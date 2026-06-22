import os
from datetime import date, datetime
from decimal import Decimal

from google.adk import Context

from adk_agents.common import RetrievalExtractionResult, SearchStageOutput

COPILOT_USER_MESSAGE_STATE_KEY = "copilot_user_message"
COPILOT_WORKSPACE_ID_STATE_KEY = "copilot_workspace_id"
SQL_SCHEMA_CONTEXT_STATE_KEY = "sql_schema_context"
SQL_VALIDATION_FEEDBACK_STATE_KEY = "sql_validation_feedback"
SQL_LAST_ATTEMPT_STATE_KEY = "sql_last_attempt"
SQL_VALIDATED_SQL_STATE_KEY = "sql_validated_sql"
SQL_GENERATION_ATTEMPT_STATE_KEY = "sql_generation_attempt"
SQL_SELECTED_TABLE_DETAILS_STATE_KEY = "sql_selected_table_details"
NO_USABLE_TABLE_MESSAGE = "未识别到可用数据表，请补充查询目标。"
MAX_SQL_GENERATION_ATTEMPTS = 3
SQL_LAST_ATTEMPT_MAX_CHARS = 12_000
DEFAULT_SQL_EXECUTION_MAX_ROWS = 100
EXTRACTOR_USER_HISTORY_MAX_TURNS = 5
NON_DATA_QUERY_INTENT = "非数据查询"


def _user_event_text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(
        part.text for part in event.content.parts if part.text and not part.thought
    ).strip()


def _is_workflow_injected_user_text(text: str) -> bool:
    """Skip synthetic user turns added by workflow LLM nodes."""
    t = text.strip()
    if t.startswith("## 用户问题"):
        return True
    if t.startswith("## 历史对话"):
        return True
    if t.startswith("{") and "retrieval_query" in t:
        return True
    return False


def conversation_history_for_extractor(ctx: Context, *, current_message: str) -> str:
    """Build prior user turns from session events (excludes current input)."""
    session = getattr(ctx, "session", None)
    if session is None or not session.events:
        return ""

    current = (current_message or "").strip()
    inv_order: list[str] = []
    inv_user: dict[str, str] = {}

    for event in session.events:
        invocation_id = (getattr(event, "invocation_id", None) or "").strip()
        if not invocation_id:
            continue
        if invocation_id not in inv_order:
            inv_order.append(invocation_id)

        author = (getattr(event, "author", None) or "").strip()
        if author != "user":
            continue

        text = _user_event_text(event)
        if text and not _is_workflow_injected_user_text(text):
            inv_user[invocation_id] = text

    user_turns: list[str] = []
    for invocation_id in inv_order:
        user_text = inv_user.get(invocation_id, "")
        if not user_text or user_text == current:
            continue
        user_turns.append(user_text)

    recent_turns = user_turns[-EXTRACTOR_USER_HISTORY_MAX_TURNS:]
    return "\n\n".join(f"**用户**：{text}" for text in recent_turns).strip()


def is_non_data_query(extraction: RetrievalExtractionResult) -> bool:
    return (extraction.intent or "").strip() == NON_DATA_QUERY_INTENT


def format_extractor_user_message(*, history: str, current_message: str) -> str:
    """Readable user turn for extractor (history + latest input)."""
    current = (current_message or "").strip() or "（无）"
    history_text = (history or "").strip()
    if not history_text:
        return current

    return "\n".join([
        "## 历史对话",
        "",
        history_text,
        "",
        "## 用户最新输入",
        "",
        current,
    ])


def clear_state_key(ctx: Context, key: str) -> None:
    """ADK State has no pop/del; clear a key by writing an empty string."""
    ctx.state[key] = ""


def save_user_message_to_state(ctx: Context, message: str) -> None:
    text = (message or "").strip()
    if text:
        ctx.state[COPILOT_USER_MESSAGE_STATE_KEY] = text


def user_message_from_state(ctx: Context) -> str:
    value = ctx.state.get(COPILOT_USER_MESSAGE_STATE_KEY)
    if value is None:
        return ""
    return str(value).strip()


def resolve_user_message(ctx: Context) -> str:
    return user_message_from_state(ctx)


def resolve_workspace_id(ctx: Context, default: str = "default") -> str:
    value = ctx.state.get(COPILOT_WORKSPACE_ID_STATE_KEY)
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def selected_table_names_from_state(ctx: Context) -> list[str]:
    value = ctx.state.get("selected_tables")
    tables: list[object] = []
    if isinstance(value, dict):
        raw = value.get("tables")
        if isinstance(raw, list):
            tables = raw
    elif value is not None and hasattr(value, "tables"):
        tables = list(getattr(value, "tables") or [])

    names: list[str] = []
    for item in tables:
        text = str(item).strip()
        if text:
            names.append(text)
    return names


def text_from_node_input(node_input) -> str:
    if node_input is None:
        return ""
    if isinstance(node_input, str):
        return node_input.strip()
    content = getattr(node_input, "parts", None)
    if content is not None:
        return "".join(
            part.text for part in node_input.parts if part.text and not part.thought
        ).strip()
    return str(node_input).strip()


def _truncate_sql_retry_context(text: str, *, max_chars: int) -> str:
    content = (text or "").strip()
    if not content or len(content) <= max_chars:
        return content
    return content[: max_chars - 1].rstrip() + "…"


def save_sql_last_attempt(ctx: Context, text: str) -> None:
    content = _truncate_sql_retry_context(
        text,
        max_chars=SQL_LAST_ATTEMPT_MAX_CHARS,
    )
    if content:
        ctx.state[SQL_LAST_ATTEMPT_STATE_KEY] = content


def format_sql_generator_user_message(ctx: Context, node_input) -> str:
    """Schema context for sql_generator; append validation feedback on retry."""
    base = (ctx.state.get(SQL_SCHEMA_CONTEXT_STATE_KEY) or "").strip()
    allowed_tables = selected_table_names_from_state(ctx)
    if not allowed_tables or not base:
        return NO_USABLE_TABLE_MESSAGE

    if allowed_tables:
        allowed_text = "\n".join(f"- `{name}`" for name in allowed_tables)
        base = "\n".join([
            base,
            "",
            "**本轮允许使用的物理表名（必须逐字照抄）**",
            "",
            allowed_text,
            "",
            "SQL 中的 FROM/JOIN 表名只能来自上面列表，不得改写、缩写、补全或使用相似表名。",
        ])
    feedback = (ctx.state.get(SQL_VALIDATION_FEEDBACK_STATE_KEY) or "").strip()
    if not feedback:
        return base

    last_attempt = (ctx.state.get(SQL_LAST_ATTEMPT_STATE_KEY) or "").strip()
    parts = [
        base,
        "",
        "**上次 SQL 校验未通过**",
        "",
        feedback,
    ]
    if last_attempt:
        parts.extend([
            "",
            "**上次生成的内容（请在此基础上修正）**",
            "",
            last_attempt,
        ])
    parts.extend([
        "",
        "请根据上述错误修正 SQL 后重新生成（仍只输出说明 + ```sql 代码块）。",
    ])
    return "\n".join(parts)


def sql_execution_max_rows() -> int:
    raw = os.environ.get("SQL_EXECUTION_MAX_ROWS", "").strip()
    if not raw:
        return DEFAULT_SQL_EXECUTION_MAX_ROWS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_SQL_EXECUTION_MAX_ROWS
    return max(1, value)


def _format_cell_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ", timespec="seconds")
    if isinstance(value, Decimal):
        return format(value, "f").rstrip("0").rstrip(".") or "0"
    text = str(value).replace("\n", " ").strip()
    return text.replace("|", "\\|")


def _markdown_table(rows: list[dict[str, object]]) -> str:
    if not rows:
        return "（无数据）"
    columns = list(rows[0].keys())
    header = "| " + " | ".join(_format_cell_value(c) for c in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(_format_cell_value(row.get(col)) for col in columns) + " |"
        for row in rows
    ]
    return "\n".join([header, sep, *body])


def format_sql_execution_response(
        statements: list[str],
        result_sets: list[tuple[list[dict[str, object]], bool]],
) -> str:
    """Format executed SQL and row sets as Markdown for the user."""
    parts: list[str] = ["## 查询结果", ""]
    for index, (sql, (rows, truncated)) in enumerate(
            zip(statements, result_sets),
            start=1,
    ):
        title = f"### 结果 {index}" if len(statements) > 1 else "### 数据"
        parts.extend([
            title,
            "",
            "**执行的 SQL**",
            "",
            "```sql",
            sql.strip(),
            "```",
            "",
            f"**行数**：{len(rows)}",
        ])
        if truncated:
            max_rows = sql_execution_max_rows()
            parts.append(f"（仅展示前 {max_rows} 行，结果已截断）")
        parts.extend(["", _markdown_table(rows), ""])
    return "\n".join(parts).strip()


def format_table_selector_user_message(stage: SearchStageOutput) -> str:
    """Readable user turn for table_selector (avoid ADK BaseModel → JSON dump)."""
    user_message = (stage.user_message or "").strip() or "（无）"
    retrieval_context = (stage.retrieval_context or "").strip() or "（无检索结果）"
    candidate_lines = [
        f"- T{index}: `{table_name}`"
        for index, table_name in enumerate(stage.table_candidates, start=1)
    ]
    candidates = "\n".join(candidate_lines) if candidate_lines else "（无候选表）"
    return "\n".join([
        "## 用户问题",
        "",
        user_message,
        "",
        "## 候选表编号",
        "",
        candidates,
        "",
        "## 检索上下文",
        "",
        retrieval_context,
    ])
