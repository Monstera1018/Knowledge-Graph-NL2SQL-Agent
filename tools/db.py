import asyncio
import os
import re
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tools.common import require_non_empty_env

_MYSQL_ERR_MESSAGE_RE = re.compile(r'\(\d+,\s*"([^"]+)"\)')
_SAFE_SQL_IDENT_RE = re.compile(r"^[A-Za-z0-9_]+$")
DatabaseBackend = Literal["mysql", "sqlite"]

_engine: AsyncEngine | None = None
_default_query_timeout_s = float(require_non_empty_env("DATABASE_QUERY_TIMEOUT_S"))
_metadata_lookup_timeout_s = float(require_non_empty_env("DATABASE_LOOKUP_TIMEOUT_S"))


def _get_sqlalchemy_uri() -> str:
    """Resolve an async SQLAlchemy database URL from the environment.
    """

    direct = os.environ.get("DATABASE_URL", "").strip()
    if direct:
        return direct

    user = require_non_empty_env("DATABASE_USER")
    password = require_non_empty_env("DATABASE_PASSWORD")
    host = require_non_empty_env("DATABASE_HOST")
    port_raw = os.environ.get("DATABASE_PORT", "3306").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError(f"DATABASE_PORT must be an integer, got {port_raw!r}") from exc
    database = require_non_empty_env("DATABASE_NAME")
    driver = require_non_empty_env("DATABASE_DRIVER")

    url = URL.create(
        drivername=driver,
        username=user,
        password=password or None,
        host=host,
        port=port,
        database=database,
    )
    return url.render_as_string(hide_password=False)


def _uri_implies_sqlite(uri: str) -> bool:
    lowered = (uri or "").strip().lower()
    if not lowered:
        return False
    driver = lowered.split("://", 1)[0]
    return driver.startswith("sqlite") or "+sqlite" in driver


def get_database_backend() -> DatabaseBackend:
    """Resolve physical DB backend for metadata queries and EXPLAIN.

    ``DATABASE_BACKEND`` may be ``mysql``, ``sqlite``, or empty (auto-detect
    from ``DATABASE_URL`` / ``DATABASE_DRIVER``).
    """

    override = os.environ.get("DATABASE_BACKEND", "").strip().lower()
    if override in ("mysql", "sqlite"):
        return override  # type: ignore[return-value]

    uri = _get_sqlalchemy_uri()
    if _uri_implies_sqlite(uri):
        return "sqlite"

    driver = os.environ.get("DATABASE_DRIVER", "").strip().lower()
    if "sqlite" in driver:
        return "sqlite"
    return "mysql"


def _require_safe_sql_ident(name: str, *, kind: str) -> str:
    text_name = (name or "").strip()
    if not text_name or not _SAFE_SQL_IDENT_RE.fullmatch(text_name):
        raise ValueError(f"unsafe {kind} identifier: {name!r}")
    return text_name


def get_sql_read_dialect() -> str:
    """Dialect for LLM SQL generation and sqlglot validation.

    ``SQL_READ_DIALECT`` overrides auto-detection from ``DATABASE_BACKEND``.
    """

    override = os.environ.get("SQL_READ_DIALECT", "").strip().lower()
    if override:
        return override
    if get_database_backend() == "sqlite":
        return "sqlite"
    return "mysql"


def prepare_sql_for_physical_db(
        sql: str,
        *,
        read_dialect: str | None = None,
) -> str:
    """Adapt SQL before hitting the configured physical database.

    When read dialect differs from the physical backend (e.g. legacy MySQL
    SQL against SQLite), transpile before EXPLAIN/execute.
    """

    statement = (sql or "").strip()
    if not statement:
        return statement

    backend = get_database_backend()
    source = (read_dialect or get_sql_read_dialect()).strip().lower() or "mysql"
    if source == backend:
        return statement
    if backend != "sqlite":
        return statement

    from sqlglot import transpile
    try:
        converted = transpile(statement, read=source, write="sqlite")
    except Exception as exc:
        raise ValueError(f"无法将 SQL 转换为 SQLite 方言：{exc}") from exc
    if not converted:
        raise ValueError("无法将 SQL 转换为 SQLite 方言")
    return str(converted[0]).strip()


def _engine_connect_args(uri: str) -> dict:
    """Connection pool kwargs for ``create_async_engine``.

    ``pool_pre_ping`` is disabled for aiomysql/asyncmy: SQLAlchemy 2.0 calls
    ``ping()`` without the required ``reconnect`` arg on those adapters
    (see sqlalchemy/sqlalchemy#13306). ``pool_recycle`` still retires stale
    connections before MySQL ``wait_timeout``.
    """

    kwargs = {"pool_recycle": 3600}
    if "aiomysql" in uri or "asyncmy" in uri:
        kwargs["pool_pre_ping"] = False
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        uri = _get_sqlalchemy_uri()
        _engine = create_async_engine(uri, **_engine_connect_args(uri))

    assert isinstance(_engine, AsyncEngine)

    return _engine


async def close_sql_engine() -> None:
    """Dispose the singleton engine (call once on application shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None


def get_configured_database_name() -> str:
    return require_non_empty_env("DATABASE_NAME")


async def list_physical_table_columns(
        table_name: str,
        schema: str | None = None,
        timeout_s: float = _default_query_timeout_s,
) -> list[str]:
    """Column names for a physical table (MySQL INFORMATION_SCHEMA or SQLite PRAGMA)."""

    table = (table_name or "").strip()
    if not table:
        return []

    eng = get_engine()

    if get_database_backend() == "sqlite":
        safe_table = _require_safe_sql_ident(table, kind="table")
        stmt = text(f'PRAGMA table_info("{safe_table}")')

        async def _run_sqlite() -> list[str]:
            async with eng.connect() as conn:
                result = await conn.execute(stmt)
                return [
                    str(row["name"])
                    for row in result.mappings().all()
                    if row.get("name")
                ]

        return await asyncio.wait_for(_run_sqlite(), timeout=timeout_s)

    db_schema = (schema or get_configured_database_name()).strip()
    stmt = text(
        "SELECT COLUMN_NAME AS column_name "
        "FROM INFORMATION_SCHEMA.COLUMNS "
        "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table "
        "ORDER BY ORDINAL_POSITION"
    )

    async def _run_mysql() -> list[str]:
        async with eng.connect() as conn:
            result = await conn.execute(stmt, {"schema": db_schema, "table": table})
            return [
                str(row["column_name"])
                for row in result.mappings().all()
                if row.get("column_name")
            ]

    return await asyncio.wait_for(_run_mysql(), timeout=timeout_s)


async def resolve_physical_table_name(
        table_name: str,
        schema: str | None = None,
        timeout_s: float = _metadata_lookup_timeout_s,
) -> str | None:
    """Return TABLE_NAME as stored in the database, or None if the table does not exist."""

    name = (table_name or "").strip()
    if not name:
        return None

    eng = get_engine()

    if get_database_backend() == "sqlite":
        stmt = text(
            "SELECT name AS table_name "
            "FROM sqlite_master "
            "WHERE type = 'table' AND LOWER(name) = LOWER(:table) "
            "LIMIT 1"
        )

        async def _run_sqlite() -> str | None:
            async with eng.connect() as conn:
                result = await conn.execute(stmt, {"table": name})
                row = result.mappings().first()
                if row is None:
                    return None
                return str(row["table_name"])

        return await asyncio.wait_for(_run_sqlite(), timeout=timeout_s)

    db_schema = (schema or get_configured_database_name()).strip()
    stmt = text(
        "SELECT TABLE_NAME AS table_name "
        "FROM INFORMATION_SCHEMA.TABLES "
        "WHERE TABLE_SCHEMA = :schema AND LOWER(TABLE_NAME) = LOWER(:table) "
        "LIMIT 1"
    )

    async def _run_mysql() -> str | None:
        async with eng.connect() as conn:
            result = await conn.execute(
                stmt,
                {"schema": db_schema, "table": name},
            )
            row = result.mappings().first()
            if row is None:
                return None
            return str(row["table_name"])

    return await asyncio.wait_for(_run_mysql(), timeout=timeout_s)


async def list_columns_for_physical_table(
        table_name: str,
        schema: str | None = None,
        timeout_s: float = _metadata_lookup_timeout_s,
) -> tuple[str | None, list[str]]:
    """Resolve table name in the database and return (actual_name, column_names)."""

    resolved = await resolve_physical_table_name(table_name, schema=schema, timeout_s=timeout_s)
    if not resolved:
        return None, []
    columns = await list_physical_table_columns(
        resolved,
        schema=schema,
        timeout_s=timeout_s,
    )
    return resolved, columns


def format_db_error_message(exc: Exception) -> str:
    """Extract a concise MySQL/SQLAlchemy error message for display."""

    message = str(exc).strip()
    match = _MYSQL_ERR_MESSAGE_RE.search(message)
    if match:
        return match.group(1)
    if "(Background on this error" in message:
        message = message.split("(Background on this error", 1)[0].strip()
    return message or "数据库校验失败"


async def explain_read_sql_statements(
        statements: Sequence[str],
        *,
        timeout_s: float | None = None,
) -> list[str]:
    """Run EXPLAIN on each statement; return human-readable errors (empty if all ok)."""

    normalized = [(statement or "").strip() for statement in statements]
    normalized = [statement for statement in normalized if statement]
    if not normalized:
        return []

    eng = get_engine()
    effective_timeout = _metadata_lookup_timeout_s if timeout_s is None else timeout_s
    errors: list[str] = []

    for index, statement in enumerate(normalized, start=1):
        prefix = f"第 {index} 条 SQL " if len(normalized) > 1 else ""

        try:
            physical_sql = prepare_sql_for_physical_db(statement)
        except ValueError as exc:
            errors.append(f"{prefix}{exc}")
            continue

        explain_prefix = (
            "EXPLAIN QUERY PLAN"
            if get_database_backend() == "sqlite"
            else "EXPLAIN"
        )

        async def _run(sql: str = physical_sql) -> None:
            async with eng.connect() as conn:
                await conn.execute(text(f"{explain_prefix} {sql}"))

        try:
            await asyncio.wait_for(_run(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            errors.append(f"{prefix}EXPLAIN 超时（>{effective_timeout:.0f}s）")
        except Exception as exc:
            errors.append(f"{prefix}{format_db_error_message(exc)}")

    return errors


def _quote_sql_identifier(name: str, dialect: str) -> str:
    """Return a safely quoted SQL identifier for the given dialect."""
    if dialect == "sqlite":
        return '"' + name.replace('"', '""') + '"'
    return "`" + name.replace("`", "``") + "`"


async def lookup_distinct_column_values(
        table_name: str,
        column_name: str,
        *,
        limit: int = 30,
        timeout_s: float | None = None,
        read_dialect: str | None = None,
) -> tuple[list[str], bool]:
    """Return (values, truncated) of distinct non-null values for a column.

    *values* is a list of string representations; *truncated* is True when
    there were more than *limit* distinct values.
    """
    table = (table_name or "").strip()
    col = (column_name or "").strip()
    if not table or not col:
        return [], False

    dialect = (read_dialect or get_sql_read_dialect()).strip().lower()
    qt = _quote_sql_identifier(table, dialect)
    qc = _quote_sql_identifier(col, dialect)

    # Fetch one extra row to detect truncation
    sql = (
        f"SELECT DISTINCT {qc} AS _lookup_val "
        f"FROM {qt} "
        f"WHERE {qc} IS NOT NULL "
        f"ORDER BY {qc} "
        f"LIMIT {limit + 1}"
    )
    effective_timeout = timeout_s if timeout_s is not None else _metadata_lookup_timeout_s
    try:
        rows = await execute_read_sql(sql, timeout_s=effective_timeout)
    except Exception:
        return [], False

    truncated = len(rows) > limit
    values = [
        str(row["_lookup_val"])
        for row in rows[:limit]
        if row.get("_lookup_val") is not None
    ]
    return values, truncated


async def execute_read_sql(
        sql: str,
        *,
        timeout_s: float | None = None,
) -> list[dict[str, object]]:
    """Run a read query and return rows as plain dicts."""

    statement = (sql or "").strip()
    if not statement:
        return []

    physical_sql = prepare_sql_for_physical_db(statement)
    eng = get_engine()

    async def _run() -> list[dict[str, object]]:
        async with eng.connect() as conn:
            result = await conn.execute(text(physical_sql))
            return [dict(row) for row in result.mappings().all()]

    effective_timeout = _default_query_timeout_s if timeout_s is None else timeout_s
    try:
        return await asyncio.wait_for(_run(), timeout=effective_timeout)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(
            f"SQL 查询超时（>{timeout_s:.0f}s），请缩小范围或检查语句"
        ) from exc
