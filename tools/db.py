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
    """从环境变量解析异步 SQLAlchemy 数据库连接地址。"""

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
    """解析物理数据库类型，用于元数据查询和 EXPLAIN。

    ``DATABASE_BACKEND`` 可以是 ``mysql``、``sqlite`` 或留空；留空时根据
    ``DATABASE_URL`` / ``DATABASE_DRIVER`` 自动判断。
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
    """大模型生成 SQL 和 sqlglot 校验使用的 SQL 方言。

    ``SQL_READ_DIALECT`` 会覆盖基于 ``DATABASE_BACKEND`` 的自动判断。
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
    """访问已配置的物理数据库前，按需适配 SQL。

    当读取方言与物理后端不一致时，例如使用旧 MySQL SQL 访问 SQLite，
    在 EXPLAIN/执行前先转换方言。
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
    """构造 ``create_async_engine`` 使用的连接池参数。

    对 aiomysql/asyncmy 禁用 ``pool_pre_ping``：SQLAlchemy 2.0 在这些适配器上
    调用 ``ping()`` 时不会传入必需的 ``reconnect`` 参数
    （见 sqlalchemy/sqlalchemy#13306）。``pool_recycle`` 仍会在 MySQL
    ``wait_timeout`` 前回收陈旧连接。
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
    """释放单例数据库引擎，应用关闭时调用一次。"""
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
    """查询物理表字段名，MySQL 使用 INFORMATION_SCHEMA，SQLite 使用 PRAGMA。"""

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
    """返回数据库中实际存储的表名；表不存在时返回 None。"""

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
    """解析数据库中的实际表名，并返回（实际表名，字段名列表）。"""

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
    """提取简短的 MySQL/SQLAlchemy 错误信息用于展示。"""

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
    """对每条语句执行 EXPLAIN；全部通过时返回空列表，否则返回可读错误。"""

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
    """按指定方言返回安全引用后的 SQL 标识符。"""
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
    """返回指定字段的非空去重值及是否被截断。

    返回值中的 *values* 是字符串形式的值列表；当去重值数量超过 *limit* 时，
    返回值中的 *truncated* 为 True。
    """
    table = (table_name or "").strip()
    col = (column_name or "").strip()
    if not table or not col:
        return [], False

    dialect = (read_dialect or get_sql_read_dialect()).strip().lower()
    qt = _quote_sql_identifier(table, dialect)
    qc = _quote_sql_identifier(col, dialect)

    # 多取一行用于判断结果是否被截断。
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
    """执行只读查询，并以普通 dict 列表返回结果行。"""

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
