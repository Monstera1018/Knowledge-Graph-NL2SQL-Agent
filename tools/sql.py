import re
from collections.abc import Sequence
from typing import Tuple, Optional, List, Dict, Set

from sqlglot import exp, parse, parse_one
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer import optimize, traverse_scope
from sqlglot.optimizer.qualify import qualify
from sqlglot.optimizer.scope import Scope
from sqlglot.expressions import Select, Column, Table, Alias, Union


def is_valid_select_stmt(stmt: str, dialect: str) -> Tuple[bool, str]:
    try:
        statements = [item for item in parse(stmt, dialect=dialect) if item is not None]
        if len(statements) != 1:
            return False, "SQL必须是单条语句"
        sql = statements[0]
        if isinstance(sql, Select) or isinstance(sql, Union):
            if _contains_disallowed_read_construct(sql):
                return False, "SQL包含非只读或高风险语法"
            return True, ""
        else:
            return False, "SQL不是Select或Union语句"
    except ParseError as e:
        return False, str(e)


def split_read_sql_statements(text: str, dialect: str) -> Tuple[List[str], str]:
    """将分号分隔的 SQL 拆分为独立的只读语句。"""

    try:
        statements = [item for item in parse(text, dialect=dialect) if item is not None]
    except ParseError as exc:
        return [], str(exc)

    if not statements:
        return [], "未解析到 SQL 语句"

    normalized: List[str] = []
    for index, statement in enumerate(statements, start=1):
        if not isinstance(statement, (Select, Union)):
            return [], f"第 {index} 条语句不是 SELECT 或 UNION 语句"
        if _contains_disallowed_read_construct(statement):
            return [], f"第 {index} 条语句包含非只读或高风险语法"
        normalized.append(statement.sql(dialect=dialect))

    return normalized, ""


def _contains_disallowed_read_construct(sql) -> bool:
    disallowed = tuple(
        expression
        for name in ("Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Into")
        if (expression := getattr(exp, name, None)) is not None
    )
    if any(sql.find_all(*disallowed)):
        return True
    return any(table.args.get("pivots") for table in sql.find_all(Table))


def _collect_cte_aliases(parsed_sql) -> Set[str]:
    """收集 WITH ... AS (...) 中定义的名称，这些不是物理表。"""

    aliases: Set[str] = set()
    for cte in parsed_sql.find_all(exp.CTE):
        name = (cte.alias_or_name or "").strip()
        if name:
            aliases.add(name.lower())
    return aliases


def _collect_cte_table_reference_aliases(parsed_sql, cte_aliases: Set[str]) -> Set[str]:
    """收集 CTE 引用上的表别名，例如 ``FROM filtered_days f``。"""

    ref_aliases: Set[str] = set()
    if not cte_aliases:
        return ref_aliases
    for table in parsed_sql.find_all(exp.Table):
        name = (table.name or "").strip().lower()
        if name not in cte_aliases:
            continue
        alias = table.args.get("alias")
        if alias is None:
            continue
        alias_name = (getattr(alias, "name", None) or alias.alias_or_name or "").strip().lower()
        if alias_name:
            ref_aliases.add(alias_name)
    return ref_aliases


def _ephemeral_table_names(cte_aliases: Set[str], cte_ref_aliases: Set[str]) -> Set[str]:
    return {name.lower() for name in cte_aliases} | {name.lower() for name in cte_ref_aliases}


def _drop_ephemeral_tables(
        source_info: Dict[str, List[str]],
        ephemeral_names: Set[str],
) -> Dict[str, List[str]]:
    if not ephemeral_names:
        return source_info
    return {
        table: columns
        for table, columns in source_info.items()
        if table.lower() not in ephemeral_names
    }


def _is_physical_table_ref(scope: Scope, table: exp.Table) -> bool:
    for key in (table.alias_or_name, table.name):
        if not key:
            continue
        source = _lookup_source(scope, key)
        if source is None:
            continue
        if isinstance(source, Table):
            return True
        if isinstance(source, Scope):
            return False
    return True


def _lookup_source(start_scope: Scope, source_name: str) -> Optional[Table | Scope]:
    scope = start_scope
    while scope is not None:
        # 1) 当前作用域
        if hasattr(scope, "sources") and source_name in scope.sources:
            return scope.sources[source_name]
        # 2) 继续往外层走 当出现`子`用`父`的情况时
        scope = getattr(scope, "parent", None)
    return None


def _duplicate_table_aliases(parsed_sql) -> List[str]:
    """查找同一作用域内重复使用的表别名，例如 orders o JOIN detail o。"""

    duplicates: List[str] = []
    seen: Set[str] = set()
    for scope in traverse_scope(parsed_sql):
        freq: Dict[str, int] = {}
        display: Dict[str, str] = {}
        for table in scope.tables:
            alias = (table.alias_or_name or "").strip()
            if not alias:
                continue
            key = alias.lower()
            freq[key] = freq.get(key, 0) + 1
            display.setdefault(key, alias)
        for key, count in freq.items():
            if count > 1 and key not in seen:
                duplicates.append(display[key])
                seen.add(key)
    return duplicates


def _format_source_analysis_error(message: str) -> str:
    if message.startswith("Alias already used:"):
        alias = message.split(":", 1)[-1].strip()
        return f"表别名重复：{alias}（同一查询中每个表须使用不同别名）"
    return message


def _register_physical_tables_from_scope(
        scope: Scope,
        source_info: Dict[str, List[str]],
        ephemeral_names: Set[str],
) -> None:
    for table in scope.tables:
        name_key = (table.name or "").strip().lower()
        if not name_key or name_key in ephemeral_names:
            continue
        if not _is_physical_table_ref(scope, table):
            continue
        physical_name = (table.name or "").strip()
        if physical_name and physical_name not in source_info:
            source_info[physical_name] = []


def _column_reference_label(column: Column) -> str:
    table = (column.table or "").strip()
    name = (column.name or "").strip()
    if not name or name == "*":
        return ""
    if table:
        return f"{table}.{name}"
    return name


def _is_timestampdiff_unit_arg(column: Column) -> bool:
    """跳过被解析成字段的 TIMESTAMPDIFF 单位标记，例如 day/hour。"""
    parent = getattr(column, "parent", None)
    if not isinstance(parent, exp.TimestampDiff):
        return False
    unit_arg = parent.args.get("this")
    if unit_arg is not column:
        return False
    return (column.name or "").strip().lower() in {
        "microsecond",
        "second",
        "minute",
        "hour",
        "day",
        "week",
        "month",
        "quarter",
        "year",
    }


def _cte_scope_select_expr(scope: Scope):
    """返回 CTE/子查询作用域中的第一组 SELECT 层表达式。

    对 UNION / INTERSECT / EXCEPT 沿左侧（``this``）分支逐层展开，
    直到拿到普通 SELECT。
    """
    expr = scope.expression
    while isinstance(expr, (exp.Union, exp.Intersect, exp.Except)):
        expr = expr.this
    return expr.args.get("expressions", [])


def _column_in_scope_sources(col_name: str, scope: Scope) -> bool:
    """当 *col_name* 出现在任一 CTE/子查询作用域来源中时返回 True。

    用于避免误报未限定字段引用：当字段明显来自同一 FROM 子句中的 CTE
    或派生表来源时，尤其是 ``qualify()`` 失败导致字段从未被限定时，
    不应将其判定为无法解析。
    """
    col_lower = col_name.lower()
    for src in (getattr(scope, "sources", None) or {}).values():
        if not isinstance(src, Scope):
            continue
        for out_expr in _cte_scope_select_expr(src):
            # 遇到 SELECT * 时，保守视为潜在命中。
            if isinstance(out_expr, exp.Star):
                return True
            if (out_expr.alias_or_name or "").lower() == col_lower:
                return True
    return False


def _is_output_alias(col_name: str, scope: Scope) -> bool:
    """当 col_name 命中当前作用域的 SELECT 输出别名时返回 True。

    覆盖 ORDER BY 或 HAVING 合法引用 SELECT 别名的情况；
    也包括 ORDER BY 位于各 SELECT 分支之外、且 qualify() 未展开别名引用的
    UNION 查询。
    """
    col_lower = col_name.lower()
    for out_expr in _cte_scope_select_expr(scope):
        if (out_expr.alias_or_name or "").lower() == col_lower:
            return True
    return False


def _resolve_unqualified_column(
        column: Column,
        scope: Scope,
        ephemeral_names: Set[str],
) -> Optional[List[str]]:
    """在当前作用域内将未限定字段名解析到物理表。"""

    name = (column.name or "").strip()
    if not name or name == "*":
        return None

    matches: List[str] = []
    for table in scope.tables:
        physical = (table.name or "").strip()
        if not physical or physical.lower() in ephemeral_names:
            continue
        if not _is_physical_table_ref(scope, table):
            continue
        matches.append(f"{physical}.{name}")

    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique

    # 没有明确的物理表匹配时，如果名称属于以下情况，则跳过“无法解析”报告：
    #   一、来自公共表表达式（CTE）/子查询输出字段（qualify() 未能限定它）；
    #   二、来自排序或过滤语句引用的 SELECT 输出别名，这是合法 SQL，
    #       其中 qualify() 可能不会展开它，尤其是在联合查询中。
    if not unique and (
        _column_in_scope_sources(name, scope) or _is_output_alias(name, scope)
    ):
        return []

    return None


def _collect_physical_table_names(
        parsed_sql,
        ephemeral_names: Set[str],
) -> Set[str]:
    names: Set[str] = set()
    for scope in traverse_scope(parsed_sql):
        for table in scope.tables:
            physical = (table.name or "").strip().lower()
            if not physical or physical in ephemeral_names:
                continue
            if _is_physical_table_ref(scope, table):
                names.add(physical)
    return names


def _is_qualify_table_name_mislabel(
        column: Column,
        physical_table_names: Set[str],
        scope: Scope,
) -> bool:
    """处理 qualify 可能把子查询中的表名误标为 ``alias.table_name`` 的情况。"""

    column_name = (column.name or "").strip().lower()
    qualifier = (column.table or "").strip().lower()
    if not column_name:
        return False
    if qualifier == column_name:
        return False

    name_source = _lookup_source(scope, column.name)
    if isinstance(name_source, Scope):
        return qualifier in physical_table_names
    if isinstance(name_source, Table):
        physical = (name_source.name or "").strip().lower()
        alias = (name_source.alias_or_name or "").strip().lower()
        return qualifier not in {physical, alias}

    if column_name not in physical_table_names:
        return False

    return qualifier != column_name


def _fallback_resolve_column(
        column: Column,
        scope: Scope,
        ephemeral_names: Set[str],
) -> Optional[List[str]]:
    """当 qualify/作用域查找失败时解析 ``alias.column``。"""
    name = (column.name or "").strip()
    prefix = (column.table or "").strip()
    if not name or name == "*" or not prefix:
        return None
    if prefix.lower() in ephemeral_names:
        return None
    if isinstance(_lookup_source(scope, name), (Scope, Table)):
        return None

    prefix_lower = prefix.lower()
    for table in scope.tables:
        alias = (table.alias_or_name or "").strip().lower()
        physical = (table.name or "").strip()
        if prefix_lower not in {alias, physical.lower()}:
            continue
        if not physical or physical.lower() in ephemeral_names:
            continue
        if not _is_physical_table_ref(scope, table):
            continue
        return [f"{physical}.{name}"]
    return None


def _gather_source_info(
        analysis_sql,
        raw_sql,
        cte_aliases: Set[str],
) -> Tuple[Dict[str, List[str]], List[str]]:
    cte_ref_aliases = _collect_cte_table_reference_aliases(raw_sql, cte_aliases)
    cte_ref_aliases |= _collect_cte_table_reference_aliases(analysis_sql, cte_aliases)
    ephemeral_names = _ephemeral_table_names(cte_aliases, cte_ref_aliases)

    column_source_set: Set[str] = set()
    source_info: Dict[str, List[str]] = {}
    unresolved: List[str] = []
    seen_refs: Set[str] = set()
    physical_table_names = _collect_physical_table_names(analysis_sql, ephemeral_names)
    physical_table_names |= _collect_physical_table_names(raw_sql, ephemeral_names)

    for scope in traverse_scope(analysis_sql):
        _register_physical_tables_from_scope(scope, source_info, ephemeral_names)
        for column in scope.columns:
            if _is_timestampdiff_unit_arg(column):
                continue
            label = _column_reference_label(column)
            if not label or label in seen_refs:
                continue
            seen_refs.add(label)

            if _is_qualify_table_name_mislabel(column, physical_table_names, scope):
                continue

            column_sources = _resolve_column_sources(column, scope)
            if column_sources is None and column.table:
                column_sources = _fallback_resolve_column(column, scope, ephemeral_names)
            if column_sources is None and not column.table:
                column_sources = _resolve_unqualified_column(column, scope, ephemeral_names)

            if column_sources is None:
                if column.table and column.table.strip().lower() in ephemeral_names:
                    continue
                if label:
                    unresolved.append(label)
                continue
            if not column_sources:
                continue
            column_source_set.update(column_sources)

    for column_source in column_source_set:
        table_name, column_name = column_source.split(".", 1)
        if table_name.lower() in ephemeral_names:
            continue
        if source_info.get(table_name, None) is None:
            source_info[table_name] = []
        if column_name not in source_info[table_name]:
            source_info[table_name].append(column_name)

    return _drop_ephemeral_tables(source_info, ephemeral_names), unresolved


def _resolve_column_sources(
        column: Column,
        scope: Scope,
        _visited: Optional[frozenset] = None,
) -> Optional[List[str]]:
    source_name = (column.table or "").strip()
    if not source_name:
        return None

    source = _lookup_source(scope, source_name)

    if source is None:
        return None

    if isinstance(source, Table):
        # 这里处理 qualify 可能把子查询中的表名错标成其它表字段的情况，如 ded.dwd_cst_meter_run。
        if column.name:
            alias_source = _lookup_source(scope, column.name)
            if isinstance(alias_source, (Scope, Table)):
                return None
        physical = (source.name or "").strip()
        column_name = (column.name or "").strip()
        if not physical or not column_name or column_name == "*":
            return None
        return [f"{physical}.{column_name}"]

    if isinstance(source, Scope):
        # 递归 CTE 防循环：CTE 主体可能引用自身。
        visited = _visited or frozenset()
        scope_id = id(source)
        if scope_id in visited:
            return []
        visited = visited | {scope_id}

        # 展开 UNION / INTERSECT / EXCEPT：使用第一个 SELECT 分支发现输出列名。
        # 所有分支仍会被 traverse_scope 单独遍历，因此物理表字段仍会被追踪。
        expressions = _cte_scope_select_expr(source)

        # 找出与外层列名匹配的输出项
        target = None
        for expression in expressions:
            if expression.alias_or_name and expression.alias_or_name.lower() == column.name.lower():
                target = expression.this if isinstance(expression, Alias) else expression
                break
        if target is None:
            # 对 CTE/子查询投影的解析在复杂表达式下可能不完整；真实缺列交给 EXPLAIN 兜底。
            return []

        sub_scope = source

        if isinstance(target, Column):
            return _resolve_column_sources(target, sub_scope, visited) or []
        else:
            # 由列混合计算而来
            inner_cols = list(target.find_all(Column))
            if inner_cols:
                leafs: List[str] = []
                for c in inner_cols:
                    resolved = _resolve_column_sources(c, sub_scope, visited)
                    if resolved:
                        leafs.extend(resolved)
                return leafs
            else:
                return []  # 不是由列组成的
    else:
        return None


def find_all_sources(stmt: str, dialect: str) -> Tuple[Optional[Dict[str, List[str]]], str]:
    flag, message = is_valid_select_stmt(stmt, dialect)
    if not flag:
        return None, message

    sql = parse_one(stmt, dialect=dialect)
    cte_aliases = _collect_cte_aliases(sql)

    dup_aliases = _duplicate_table_aliases(sql)
    if dup_aliases:
        return None, f"表别名重复：{', '.join(dup_aliases)}（同一查询中每个表须使用不同别名）"

    analysis_sql = sql
    try:
        analysis_sql = optimize(sql, dialect=dialect, rules=(qualify,))
    except OptimizeError:
        analysis_sql = sql

    try:
        source_info, unresolved = _gather_source_info(analysis_sql, sql, cte_aliases)
    except OptimizeError as exc:
        return None, _format_source_analysis_error(str(exc))

    if unresolved:
        labels = ", ".join(sorted(set(unresolved)))
        return None, f"无法解析列引用：{labels}"

    return source_info, "OK"


_SQL_CODE_BLOCK_RE = re.compile(
    r"```(?:sql|mysql)?\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def extract_sql_statements_from_text(text: str, dialect: str = "mysql") -> Tuple[List[str], str]:
    """从模型输出中提取一条或多条只读 SQL 语句。

    支持单个 ```sql 代码块中用分号分隔的多条语句，也支持同一回复中的多个
    fenced 代码块。
    """
    raw = (text or "").strip()
    if not raw:
        return [], ""

    chunks: List[str] = _SQL_CODE_BLOCK_RE.findall(raw)
    if not chunks:
        chunks = [raw]

    statements: List[str] = []
    for index, chunk in enumerate(chunks, start=1):
        chunk = chunk.strip()
        if not chunk:
            continue
        split, err = split_read_sql_statements(chunk, dialect)
        if err:
            prefix = f"第 {index} 个 SQL 代码块" if len(chunks) > 1 else "SQL"
            return [], f"{prefix}：{err}"
        statements.extend(split)

    unique: List[str] = []
    seen: set[str] = set()
    for stmt in statements:
        key = stmt.strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(key)

    return unique, ""


def extract_sql_from_text(text: str, dialect: str = "mysql") -> str:
    """返回提取到的第一条 SQL 语句，用于向后兼容。"""
    statements, _ = extract_sql_statements_from_text(text, dialect)
    return statements[0] if statements else ""


def _merge_table_sources(target: Dict[str, List[str]], source: Dict[str, List[str]]) -> None:
    for table_name, column_names in source.items():
        bucket = target.setdefault(table_name, [])
        for column_name in column_names:
            if column_name not in bucket:
                bucket.append(column_name)


def _collect_sources_from_statements(
        statements: List[str],
        dialect: str,
) -> Tuple[Dict[str, List[str]] | None, List[str]]:
    """解析并合并所有语句中的表/字段引用。"""
    errors: List[str] = []
    merged: Dict[str, List[str]] = {}

    for index, sql in enumerate(statements, start=1):
        ok, message = is_valid_select_stmt(sql, dialect)
        if not ok:
            prefix = f"第 {index} 条 SQL" if len(statements) > 1 else "SQL"
            errors.append(f"{prefix}：{message}")
            continue

        sources, message = find_all_sources(sql, dialect)
        if sources is None:
            prefix = f"第 {index} 条 SQL" if len(statements) > 1 else "SQL"
            errors.append(f"{prefix}：{message or '无法解析表与列'}")
            continue

        _merge_table_sources(merged, sources)

    if errors:
        return None, errors

    if not merged:
        return None, ["SQL 未引用任何物理表"]

    return merged, []


async def _verify_sources_in_database(sources: Dict[str, List[str]]) -> List[str]:
    from tools.db import list_columns_for_physical_table

    errors: List[str] = []
    for table_name, column_names in sources.items():
        resolved_name, db_columns = await list_columns_for_physical_table(table_name)
        if not resolved_name:
            errors.append(f"表不存在：{table_name}")
            continue

        if not column_names:
            continue

        known = {name.lower() for name in db_columns}
        for column_name in column_names:
            if column_name.lower() not in known:
                errors.append(f"列不存在：{resolved_name}.{column_name}")

    return errors


async def _verify_sources_in_workspace_graph(
        workspace_id: str,
        sources: Dict[str, List[str]],
        *,
        candidate_table_names: Sequence[str] | None = None,
) -> List[str]:
    from tools.retrieval import load_selected_tables_bundle

    names = list(
        dict.fromkeys(
            name.strip()
            for name in (
                list(candidate_table_names or [])
                + list(sources.keys())
            )
            if (name or "").strip()
        )
    )
    if not names:
        return []

    bundle = await load_selected_tables_bundle(workspace_id, names)
    known_tables = {table.name.lower(): table for table in bundle.tables}
    errors: List[str] = []

    for table_name, column_names in sources.items():
        table = known_tables.get(table_name.lower())
        if table is None:
            continue
        if not column_names:
            continue

        known_columns = {name.lower() for name in table.columns}
        for column_name in column_names:
            if column_name.lower() not in known_columns:
                errors.append(f"列不存在（工作区元数据）：{table.name}.{column_name}")

    return errors


def _verify_sources_in_candidate_tables(
        sources: Dict[str, List[str]],
        candidate_table_names: Sequence[str] | None = None,
) -> List[str]:
    if not candidate_table_names:
        return []

    allowed = {
        name.strip().lower()
        for name in candidate_table_names
        if (name or "").strip()
    }
    if not allowed:
        return []

    errors: List[str] = []
    for table_name in sources:
        if table_name.strip().lower() not in allowed:
            allowed_text = "、".join(candidate_table_names)
            errors.append(
                f"SQL 引用了未选中的表：{table_name}；本轮只能使用已选表：{allowed_text}"
            )
    return errors


async def validate_read_sql(
        stmt: str,
        dialect: str = "mysql",
        *,
        workspace_id: str | None = None,
        candidate_table_names: Sequence[str] | None = None,
) -> Tuple[bool, List[str], str]:
    """校验 SELECT SQL 的语法、来源，以及表/字段是否存在于数据库中。

    返回 (ok, errors, normalized_sql)。
    """
    errors: List[str] = []
    statements, extract_err = extract_sql_statements_from_text(stmt, dialect)
    if extract_err:
        return False, [extract_err], ""
    if not statements:
        return False, ["未找到 SQL 语句"], ""

    sources, source_errors = _collect_sources_from_statements(statements, dialect)
    if source_errors:
        return False, source_errors, ";\n\n".join(statements)

    assert sources is not None
    errors.extend(_verify_sources_in_candidate_tables(sources, candidate_table_names))
    errors.extend(await _verify_sources_in_database(sources))

    workspace = (workspace_id or "").strip()
    if workspace:
        errors.extend(
            await _verify_sources_in_workspace_graph(
                workspace,
                sources,
                candidate_table_names=candidate_table_names,
            )
        )

    normalized = ";\n\n".join(statements)
    if not errors:
        from tools.db import explain_read_sql_statements

        errors.extend(await explain_read_sql_statements(statements))

    if errors:
        return False, errors, normalized

    return True, [], normalized
