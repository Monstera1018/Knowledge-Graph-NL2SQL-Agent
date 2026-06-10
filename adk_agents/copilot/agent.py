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
from google.adk.workflow import DEFAULT_ROUTE

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
    SQL_LAST_ATTEMPT_STATE_KEY,
    SQL_VALIDATED_SQL_STATE_KEY,
    SQL_VALIDATION_FEEDBACK_STATE_KEY,
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
from tools.retrieval import build_sql_schema_context, search_and_build_llm_context
from tools.sql import extract_sql_statements_from_text, validate_read_sql
from tools.common import to_litellm_model_name, require_non_empty_env

SQL_DIALECT = get_sql_read_dialect()


def prepare_extractor_input(ctx: Context, node_input: str) -> str:
    current = text_from_node_input(node_input)
    save_user_message_to_state(ctx, current)
    history = conversation_history_for_extractor(ctx, current_message=current)
    return format_extractor_user_message(history=history, current_message=current)


def parse_extractor_output(ctx: Context, node_input) -> RetrievalExtractionResult:
    """Parse the JSON text emitted by the extractor agent into a RetrievalExtractionResult.

    Replaces output_schema / output_key for models that don't support structured outputs.
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
    """Route non-data chitchat away from the retrieval/SQL pipeline."""
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

    retrieval_context = await search_and_build_llm_context(
        queries,
        workspace_id=workspace_id,
        limit=10,
    )

    user_message = node_input.retrieval_query
    save_user_message_to_state(ctx, user_message)

    return SearchStageOutput(
        user_message=user_message,
        retrieval_context=retrieval_context,
    )


def prepare_table_selector_input(node_input: SearchStageOutput) -> str:
    """Turn structured search output into Markdown for the LLM (not JSON)."""
    return format_table_selector_user_message(node_input)


table_selector = Agent(
    model=get_llm_model(),
    name="table_selector",
    description="根据检索上下文与用户问题判断取数所需的表。",
    instruction=TABLE_SELECTOR_INSTRUCTION,
)


def parse_table_selection(ctx: Context, node_input) -> TableSelectionResult:
    """Parse the JSON text emitted by table_selector into a TableSelectionResult.

    Replaces the output_schema / output_key mechanism, which many non-OpenAI
    models (e.g. DeepSeek Flash) do not support.
    """
    raw = text_from_node_input(node_input)
    cleaned = (raw or "").strip()

    # Strip optional Markdown fences (``` or ```json)
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()

    tables: list[str] = []
    reason = ""
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            raw_tables = data.get("tables", [])
            if isinstance(raw_tables, list):
                tables = [str(t).strip() for t in raw_tables if str(t).strip()]
            reason = str(data.get("reason", "")).strip()
    except (json.JSONDecodeError, ValueError):
        pass

    result = TableSelectionResult(tables=tables, reason=reason)
    # Keep state in sync so selected_table_names_from_state() still works
    ctx.state["selected_tables"] = {"tables": tables, "reason": reason}
    return result


async def load_sql_schema_context(
        ctx: Context,
        node_input: TableSelectionResult,
) -> str:
    """Load full graph metadata for candidate tables and format for SQL generation."""
    workspace_id = resolve_workspace_id(ctx)
    schema_text = await build_sql_schema_context(
        workspace_id=workspace_id,
        table_names=node_input.tables,
        user_message=resolve_user_message(ctx),
        selection_reason=node_input.reason,
    )
    ctx.state[SQL_SCHEMA_CONTEXT_STATE_KEY] = schema_text
    ctx.state[SQL_GENERATION_ATTEMPT_STATE_KEY] = 0
    clear_state_key(ctx, SQL_VALIDATION_FEEDBACK_STATE_KEY)
    clear_state_key(ctx, SQL_LAST_ATTEMPT_STATE_KEY)
    clear_state_key(ctx, SQL_VALIDATED_SQL_STATE_KEY)
    return schema_text


def prepare_sql_generator_input(ctx: Context, node_input=None) -> str:
    """Build sql_generator user message; retry may pass structured validation output."""
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
    """Keep FunctionNode output string-compatible while preserving structured data."""

    return json.dumps(payload, ensure_ascii=False)


def format_validation_failure_response(ctx: Context, node_input) -> str:
    """Terminal branch when SQL validation exhausts retry attempts."""
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
    """Validate generated SQL; route retry back to sql_generator on failure."""
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
    """Run validated SQL against the database and return Markdown results."""
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
            load_sql_schema_context,
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
