import re
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT.joinpath(".env"))

from google.adk import Agent
from google.adk import Context
from google.adk import Event
from google.adk import Workflow
from google.adk.workflow import DEFAULT_ROUTE, Edge

from adk_agents.common import (
    RetrievalExtractionResult,
    SearchStageOutput,
    TableSelectionResult,
    WebSafeLiteLlm, get_llm_model,
)
from adk_agents.copilot.helpers import (
    MAX_SQL_GENERATION_ATTEMPTS,
    SQL_GENERATION_ATTEMPT_STATE_KEY,
    SQL_SCHEMA_CONTEXT_STATE_KEY,
    SQL_SELECTED_TABLE_DETAILS_STATE_KEY,
    SQL_LAST_ATTEMPT_STATE_KEY,
    SQL_VALIDATED_SQL_STATE_KEY,
    SQL_VALIDATION_FEEDBACK_STATE_KEY,
    NO_USABLE_TABLE_MESSAGE,
    clear_state_key,
    conversation_history_for_extractor,
    format_extractor_user_message,
    is_non_data_query,
    format_sql_execution_response,
    format_sql_generator_user_message,
    save_sql_last_attempt,
    format_table_selector_user_message,
    resolve_user_message,
    resolve_workspace_id,
    selected_table_names_from_state,
    save_user_message_to_state,
    sql_execution_max_rows,
    text_from_node_input,
)
from adk_agents.copilot.prompts import (
    CHITCHAT_INSTRUCTION,
    build_sql_generator_instruction,
    TABLE_SELECTOR_INSTRUCTION,
)
from adk_agents.extractor.agent import root_agent as extractor
from tools.db import execute_read_sql, get_sql_read_dialect, lookup_distinct_column_values
from tools.retrieval import (
    build_sql_schema_context,
    load_selected_tables_bundle,
    search_and_build_llm_context_with_hits,
)
from tools.sql import extract_sql_statements_from_text, validate_read_sql
from tools.common import to_litellm_model_name, require_non_empty_env

SQL_DIALECT = get_sql_read_dialect()
TABLE_CANDIDATES_STATE_KEY = "table_candidates"
SELECTED_TABLES_STATE_KEY = "selected_tables"
RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY = "retrieved_knowledge_uuids"

_TABLE_HEADING_RE = re.compile(
    r"(?m)^-\s+\*\*([A-Za-z0-9_][A-Za-z0-9_.$]*)\*\*"
)
_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)


def _extract_table_candidates(retrieval_context: str) -> list[str]:
    """按检索上下文中的展示顺序提取候选表名。"""
    names: list[str] = []
    seen: set[str] = set()
    for match in _TABLE_HEADING_RE.finditer(retrieval_context or ""):
        name = match.group(1).strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return names


def _parse_json_objects(text: str) -> list[dict]:
    """从模型文本中解析一个或多个有效 JSON 对象。"""
    cleaned = (text or "").strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()

    decoder = json.JSONDecoder()
    objects: list[dict] = []
    cursor = 0
    while cursor < len(cleaned):
        start = cleaned.find("{", cursor)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        cursor = start + end

    if objects:
        return objects

    # 兜底处理：允许单个 JSON 对象前后夹带少量非 JSON 文本。
    for match in _JSON_OBJECT_RE.finditer(cleaned):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def reset_sql_turn_state(ctx: Context, node_input: str) -> str:
    """清理不能跨用户轮次泄露的 SQL 状态。"""
    for key in (
            SQL_SCHEMA_CONTEXT_STATE_KEY,
            SQL_SELECTED_TABLE_DETAILS_STATE_KEY,
            SQL_VALIDATION_FEEDBACK_STATE_KEY,
            SQL_LAST_ATTEMPT_STATE_KEY,
            SQL_VALIDATED_SQL_STATE_KEY,
            SQL_GENERATION_ATTEMPT_STATE_KEY,
            SELECTED_TABLES_STATE_KEY,
            TABLE_CANDIDATES_STATE_KEY,
            RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY,
    ):
        clear_state_key(ctx, key)
    return text_from_node_input(node_input)


def prepare_extractor_input(ctx: Context, node_input: str) -> str:
    current = text_from_node_input(node_input)
    save_user_message_to_state(ctx, current)
    history = conversation_history_for_extractor(ctx, current_message=current)
    return format_extractor_user_message(history=history, current_message=current)


def parse_extractor_output(ctx: Context, node_input) -> RetrievalExtractionResult:
    """将 extractor 输出的 JSON 文本解析为 RetrievalExtractionResult。

    用于替代部分模型不支持的 output_schema / output_key 结构化输出机制。
    """
    raw = text_from_node_input(node_input)
    cleaned = (raw or "").strip()

    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()

    retrieval_query = ""
    keywords: list[str] = []
    intent = ""
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            retrieval_query = str(data.get("retrieval_query", "")).strip()
            raw_kw = data.get("keywords", [])
            if isinstance(raw_kw, list):
                keywords = [str(k).strip() for k in raw_kw if str(k).strip()]
            intent = str(data.get("intent", "")).strip()
    except (json.JSONDecodeError, ValueError):
        pass

    return RetrievalExtractionResult(
        retrieval_query=retrieval_query,
        keywords=keywords,
        intent=intent,
    )


def route_after_extractor(ctx: Context, node_input: RetrievalExtractionResult) -> Event:
    """将非问数闲聊从检索和 SQL 链路中分流出去。"""
    if is_non_data_query(node_input):
        return Event(route="chitchat", output=node_input)
    return Event(output=node_input)


def prepare_chitchat_input(ctx: Context, node_input: RetrievalExtractionResult) -> str:
    current = resolve_user_message(ctx)
    history = conversation_history_for_extractor(ctx, current_message=current)
    return format_extractor_user_message(history=history, current_message=current)


chitchat = Agent(
    model=get_llm_model(),
    name="chitchat",
    description="处理与数据分析无关的闲聊，并引导用户回到问数场景。",
    instruction=CHITCHAT_INSTRUCTION,
)


async def search(
        ctx: Context,
        node_input: RetrievalExtractionResult,
) -> SearchStageOutput:
    workspace_id = resolve_workspace_id(ctx)
    queries = [node_input.retrieval_query, *node_input.keywords, node_input.intent]
    queries = [q for q in queries if (q or "").strip()]

    search_result = await search_and_build_llm_context_with_hits(
        queries,
        workspace_id=workspace_id,
        limit=10,
    )
    retrieval_context = search_result.context
    table_candidates = _extract_table_candidates(retrieval_context)
    ctx.state[TABLE_CANDIDATES_STATE_KEY] = table_candidates
    ctx.state[RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY] = search_result.unbound_knowledge_hit_uuids

    user_message = node_input.retrieval_query
    save_user_message_to_state(ctx, user_message)

    return SearchStageOutput(
        user_message=user_message,
        retrieval_context=retrieval_context,
        table_candidates=table_candidates,
    )


def prepare_table_selector_input(node_input: SearchStageOutput) -> str:
    """将结构化检索结果转换为给大模型阅读的 Markdown，而不是 JSON。"""
    return format_table_selector_user_message(node_input)


table_selector = Agent(
    model=get_llm_model(),
    name="table_selector",
    description="根据检索上下文与用户问题判断取数所需的表。",
    instruction=TABLE_SELECTOR_INSTRUCTION,
)


def parse_table_selection(ctx: Context, node_input) -> TableSelectionResult:
    """将 table_selector 输出的 JSON 文本解析为 TableSelectionResult。

    用于替代许多非 OpenAI 模型不支持的 output_schema / output_key 机制。
    """
    raw = text_from_node_input(node_input)
    cleaned = (raw or "").strip()

    tables: list[str] = []
    reason = ""
    candidate_tables = [
        str(item).strip()
        for item in (ctx.state.get(TABLE_CANDIDATES_STATE_KEY) or [])
        if str(item).strip()
    ]
    candidate_names = {name.lower(): name for name in candidate_tables}

    parsed_objects = _parse_json_objects(cleaned)
    data = parsed_objects[-1] if parsed_objects else {}
    if isinstance(data, dict):
        raw_tables = data.get("tables", [])
        if isinstance(raw_tables, list):
            for raw_table in raw_tables:
                table_name = candidate_names.get(str(raw_table).strip().lower())
                if table_name and table_name not in tables:
                    tables.append(table_name)

        reason = str(data.get("reason", "")).strip()

    result = TableSelectionResult(tables=tables, reason=reason)
    # 同步状态，保证 selected_table_names_from_state() 仍能读取本轮选表结果。
    ctx.state[SELECTED_TABLES_STATE_KEY] = {"tables": tables, "reason": reason}
    return result


def route_after_table_selection(ctx: Context, node_input: TableSelectionResult) -> Event:
    """未选出可用表时停止 SQL 链路。"""
    if not node_input.tables:
        return Event(route="no_tables", output=node_input)
    return Event(output=node_input)


def format_no_table_response(ctx: Context, node_input: TableSelectionResult) -> str:
    reason = (node_input.reason or "").strip()
    parts = [
        "未识别到可用数据表，请补充查询目标。",
    ]
    if reason:
        parts.extend(["", f"选表说明：{reason}"])
    return "\n".join(parts)


async def load_sql_schema_context(
        ctx: Context,
        node_input: TableSelectionResult,
) -> str:
    """加载候选表的完整图谱元数据，并整理为 SQL 生成上下文。"""
    workspace_id = resolve_workspace_id(ctx)
    bundle = await load_selected_tables_bundle(workspace_id, node_input.tables)
    detail_by_name = {
        table.name.lower(): {
            "name": table.name,
            "comment": table.comment,
            "description": table.description,
        }
        for table in bundle.tables
    }
    ctx.state[SQL_SELECTED_TABLE_DETAILS_STATE_KEY] = [
        detail_by_name.get(
            table_name.lower(),
            {"name": table_name, "comment": "", "description": ""},
        )
        for table_name in node_input.tables
    ]
    schema_text = await build_sql_schema_context(
        workspace_id=workspace_id,
        table_names=node_input.tables,
        user_message=resolve_user_message(ctx),
        selection_reason=node_input.reason,
        extra_knowledge_uuids=[
            str(item).strip()
            for item in (ctx.state.get(RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY) or [])
            if str(item).strip()
        ],
    )
    ctx.state[SQL_SCHEMA_CONTEXT_STATE_KEY] = schema_text
    ctx.state[SQL_GENERATION_ATTEMPT_STATE_KEY] = 0
    clear_state_key(ctx, SQL_VALIDATION_FEEDBACK_STATE_KEY)
    clear_state_key(ctx, SQL_LAST_ATTEMPT_STATE_KEY)
    clear_state_key(ctx, SQL_VALIDATED_SQL_STATE_KEY)
    return schema_text


def prepare_sql_generator_input(ctx: Context, node_input=None) -> str:
    """构造 sql_generator 输入；重试时可能带有结构化校验结果。"""
    return format_sql_generator_user_message(ctx, node_input)


async def lookup_column_values(table_name: str, column_name: str) -> str:
    """查询指定表中某一列在数据库中实际存储的去重值。

    当你需要在 WHERE 条件中使用某列的具体字符串值（如状态码、类型、名称等），
    但不确定数据库里实际存储的写法时，先调用此工具查看真实值，再写 SQL。

    Args:
        table_name: 数据表名（与库表结构中的名称完全一致）。
        column_name: 需要查看去重值的列名（与库表结构中的名称完全一致）。

    Returns:
        该列的实际去重值列表（文本格式）。若列为空或表不存在则提示说明。
    """
    values, truncated = await lookup_distinct_column_values(
        table_name, column_name, read_dialect=SQL_DIALECT
    )
    if not values:
        return f"未查到「{table_name}.{column_name}」的有效值（该列可能全为空，或表/列名有误）。"
    val_list = "、".join(f'"{v}"' for v in values)
    suffix = "（值较多，已截断，仅展示前 30 条）" if truncated else ""
    return f"「{table_name}.{column_name}」的实际去重值共 {len(values)} 个：{val_list}{suffix}"


sql_generator = Agent(
    model=get_llm_model(),
    name="sql_generator",
    description="根据库表结构与用户问题生成取数 SQL。",
    instruction=build_sql_generator_instruction(SQL_DIALECT),
    tools=[lookup_column_values],
)


def _validation_checked_sql(raw_text: str) -> str:
    statements, _ = extract_sql_statements_from_text(raw_text, SQL_DIALECT)
    if statements:
        return "\n\n".join(statements).strip()
    return (raw_text or "").strip()


def _validation_failure_payload(
        *,
        errors: list[str],
        attempt: int,
        raw_text: str,
        terminal: bool,
) -> dict[str, object]:
    return {
        "ok": False,
        "errors": list(errors),
        "attempt": attempt,
        "checked_sql": _validation_checked_sql(raw_text)[:4000],
        "recovery_action": "stop" if terminal else "repair",
    }


def _validation_output(payload: dict[str, object]) -> str:
    """在保留结构化数据的同时，让 FunctionNode 输出保持字符串兼容。"""

    return json.dumps(payload, ensure_ascii=False)


def format_validation_failure_response(ctx: Context, node_input) -> str:
    """处理 SQL 校验重试次数耗尽后的终止分支。"""
    raw_text = text_from_node_input(node_input)
    payload: dict[str, object] = {}
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            payload = parsed
    except json.JSONDecodeError:
        pass

    errors = payload.get("errors")
    error_lines = (
        [f"- {err}" for err in errors if str(err).strip()]
        if isinstance(errors, list)
        else []
    )
    attempt = payload.get("attempt")
    attempt_text = attempt if isinstance(attempt, int) else MAX_SQL_GENERATION_ATTEMPTS

    parts = [
        "## SQL 生成失败",
        "",
        f"已尝试 {attempt_text} 次，仍未通过校验，未执行查询。",
    ]
    if error_lines:
        parts.extend(["", "主要问题：", "", *error_lines])
    else:
        parts.extend(["", "请补充更明确的筛选条件或缩小问题范围后重试。"])
    return "\n".join(parts)


async def validate_sql(ctx: Context, node_input) -> Event:
    """校验生成的 SQL；失败且未达上限时路由回 sql_generator 重试。"""
    raw_text = text_from_node_input(node_input)
    attempt = int(ctx.state.get(SQL_GENERATION_ATTEMPT_STATE_KEY, 0))

    ok, errors, normalized_sql = await validate_read_sql(
        raw_text,
        dialect=SQL_DIALECT,
        workspace_id=resolve_workspace_id(ctx),
        candidate_table_names=selected_table_names_from_state(ctx),
    )

    if ok:
        clear_state_key(ctx, SQL_VALIDATION_FEEDBACK_STATE_KEY)
        clear_state_key(ctx, SQL_LAST_ATTEMPT_STATE_KEY)
        if normalized_sql.strip():
            ctx.state[SQL_VALIDATED_SQL_STATE_KEY] = normalized_sql.strip()
        return Event(output=_validation_output({"ok": True}))

    attempt += 1
    ctx.state[SQL_GENERATION_ATTEMPT_STATE_KEY] = attempt
    feedback = "\n".join(f"- {err}" for err in errors)
    ctx.state[SQL_VALIDATION_FEEDBACK_STATE_KEY] = feedback
    save_sql_last_attempt(ctx, raw_text)

    if attempt >= MAX_SQL_GENERATION_ATTEMPTS:
        return Event(
            route="failed",
            output=_validation_output(
                _validation_failure_payload(
                    errors=errors,
                    attempt=attempt,
                    raw_text=raw_text,
                    terminal=True,
                )
            ),
        )

    return Event(
        route="retry",
        output=_validation_output(
            _validation_failure_payload(
                errors=errors,
                attempt=attempt,
                raw_text=raw_text,
                terminal=False,
            )
        ),
    )


async def execute_sql(ctx: Context, node_input) -> str:
    """执行已校验 SQL，并返回 Markdown 查询结果。"""
    raw_text = text_from_node_input(node_input)
    sql_text = (ctx.state.get(SQL_VALIDATED_SQL_STATE_KEY) or "").strip()
    if not sql_text:
        ok, errors, sql_text = await validate_read_sql(
            raw_text,
            dialect=SQL_DIALECT,
            workspace_id=resolve_workspace_id(ctx),
            candidate_table_names=selected_table_names_from_state(ctx),
        )
        if not ok:
            return "SQL 校验未通过，无法执行查询：\n\n" + "\n".join(
                f"- {err}" for err in errors
            )

    statements, extract_err = extract_sql_statements_from_text(sql_text, SQL_DIALECT)
    if extract_err:
        return f"无法解析待执行的 SQL：{extract_err}"
    if not statements:
        return "未找到可执行的 SQL 语句。"

    max_rows = sql_execution_max_rows()
    result_sets: list[tuple[list[dict[str, object]], bool]] = []

    for index, statement in enumerate(statements, start=1):
        try:
            rows = await execute_read_sql(statement)
        except TimeoutError as exc:
            prefix = f"第 {index} 条 SQL " if len(statements) > 1 else ""
            return f"{prefix}查询超时：{exc}"
        except Exception as exc:
            prefix = f"第 {index} 条 SQL " if len(statements) > 1 else ""
            return f"{prefix}查询执行失败：{exc}"

        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        result_sets.append((rows, truncated))

    return format_sql_execution_response(statements, result_sets)


root_agent = Workflow(
    name="TYWS",
    edges=[
        (
            "START",
            prepare_extractor_input,
            extractor,
            parse_extractor_output,
            route_after_extractor,
            {
                "chitchat": prepare_chitchat_input,
                DEFAULT_ROUTE: search,
            },
        ),
        (
            prepare_chitchat_input,
            chitchat,
        ),
        (
            search,
            prepare_table_selector_input,
            table_selector,
            parse_table_selection,
            route_after_table_selection,
            {
                "no_tables": format_no_table_response,
                DEFAULT_ROUTE: load_sql_schema_context,
            },
            prepare_sql_generator_input,
            sql_generator,
            validate_sql,
            {
                "retry": prepare_sql_generator_input,
                "failed": format_validation_failure_response,
                DEFAULT_ROUTE: execute_sql,
            },
        ),
    ],
)
