import json
import os
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from google.adk import Context

from adk_agents.common import RetrievalExtractionResult, SearchStageOutput

COPILOT_USER_MESSAGE_STATE_KEY = "copilot_user_message"
COPILOT_WORKSPACE_ID_STATE_KEY = "copilot_workspace_id"
SQL_SCHEMA_CONTEXT_STATE_KEY = "sql_schema_context"
SQL_VALIDATION_FEEDBACK_STATE_KEY = "sql_validation_feedback"
SQL_LAST_ATTEMPT_STATE_KEY = "sql_last_attempt"
SQL_VALIDATED_SQL_STATE_KEY = "sql_validated_sql"
SQL_GENERATOR_LOGIC_STATE_KEY = "sql_generator_logic"
SQL_GENERATION_ATTEMPT_STATE_KEY = "sql_generation_attempt"
SQL_SELECTED_TABLE_DETAILS_STATE_KEY = "sql_selected_table_details"
NO_USABLE_TABLE_MESSAGE = "未识别到可用数据表，请补充查询目标。"
MAX_SQL_GENERATION_ATTEMPTS = 3
SQL_LAST_ATTEMPT_MAX_CHARS = 12_000
DEFAULT_SQL_EXECUTION_MAX_ROWS = 100
EXTRACTOR_USER_HISTORY_MAX_TURNS = 5
NON_DATA_QUERY_INTENT = "非数据查询"
EXTRACTOR_TASK_QUERY = "query"
EXTRACTOR_TASK_CHART = "chart"
EXTRACTOR_TASK_CHITCHAT = "chitchat"
EXTRACTOR_TASK_REPORT = "report"
QUERY_SNAPSHOT_STATE_KEY = "query_snapshot"
QUERY_SNAPSHOT_MAX_CONTEXT_CHARS = 8_000
QUERY_SNAPSHOT_FRESH_STATE_KEY = "query_snapshot_fresh"
PLAN_STATE_KEY = "copilot_plan"
PLAN_OBSERVATION_STATE_KEY = "copilot_plan_observation"
PLAN_TURN_COUNT_STATE_KEY = "copilot_plan_turn"
MAX_PLANNER_TURNS = 5
EXTRACTOR_RESULT_STATE_KEY = "extractor_result"
LAST_CHART_SPEC_STATE_KEY = "last_chart_spec"
LAST_REPORT_SPEC_STATE_KEY = "last_report_spec"
_CHART_REQUEST_RE = re.compile(
    r"(画(个|一张|一个)?(分析)?图|生成(分析)?图表|出(个|一张|一个)?(分析)?图|"
    r"可视化|柱状图|折线图|饼图|趋势图|统计图|分析图表|图表|"
    r"换成(饼图|柱状图|折线图)|改成(饼图|柱状图|折线图)|换(一个|种|个)?(展示形式|图)|"
    r"换(个|一种)?颜色|色调|配色|看(一下)?趋势)",
)
_REPORT_REQUEST_RE = re.compile(
    r"(分析报告|生成报告|出一份报告|写(一份)?报告|下载报告|报告文件|"
    r"出报告|要报告|给我报告|报告呢|报告啊|报告吧|^报告$)",
)
_DATA_QUERY_RE = re.compile(r"(查询|清单|明细|统计|有哪些|多少|帮我查|未结清)")


def _user_event_text(event) -> str:
    if not event.content or not event.content.parts:
        return ""
    return "".join(
        part.text for part in event.content.parts if part.text and not part.thought
    ).strip()


def _is_workflow_injected_user_text(text: str) -> bool:
    """跳过工作流大模型节点注入的伪用户消息。"""
    t = text.strip()
    if t.startswith("## 用户问题"):
        return True
    if t.startswith("## 用户最新输入"):
        return True
    if t.startswith("## 用户出图要求"):
        return True
    if t.startswith("## 用户报告要求"):
        return True
    if t.startswith("## 问题解析结果"):
        return True
    if t.startswith("## 当前是否已有查询结果"):
        return True
    if t.startswith("## 历史对话"):
        return True
    if t.startswith("{") and "retrieval_query" in t:
        return True
    return False


def conversation_history_for_extractor(ctx: Context, *, current_message: str) -> str:
    """从会话事件中构造历史用户轮次，不包含当前输入。"""
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
    return resolve_extractor_task(extraction) == EXTRACTOR_TASK_CHITCHAT


def normalize_extractor_task(raw: str) -> str:
    text = (raw or "").strip().lower()
    mapping = {
        "query": EXTRACTOR_TASK_QUERY,
        "ask": EXTRACTOR_TASK_QUERY,
        "sql": EXTRACTOR_TASK_QUERY,
        "问数": EXTRACTOR_TASK_QUERY,
        "查询": EXTRACTOR_TASK_QUERY,
        "数据查询": EXTRACTOR_TASK_QUERY,
        "chart": EXTRACTOR_TASK_CHART,
        "visualize": EXTRACTOR_TASK_CHART,
        "visualization": EXTRACTOR_TASK_CHART,
        "图表": EXTRACTOR_TASK_CHART,
        "图表分析": EXTRACTOR_TASK_CHART,
        "可视化": EXTRACTOR_TASK_CHART,
        "report": EXTRACTOR_TASK_REPORT,
        "分析报告": EXTRACTOR_TASK_REPORT,
        "报告": EXTRACTOR_TASK_REPORT,
        "chitchat": EXTRACTOR_TASK_CHITCHAT,
        "chat": EXTRACTOR_TASK_CHITCHAT,
        "闲聊": EXTRACTOR_TASK_CHITCHAT,
        "非数据查询": EXTRACTOR_TASK_CHITCHAT,
    }
    return mapping.get(text, "")


def user_requests_chart(message: str) -> bool:
    """用户是否明确提到出图、换图或改配色。"""
    return bool(_CHART_REQUEST_RE.search((message or "").strip()))


def user_requests_report(message: str) -> bool:
    """用户是否明确要求生成分析报告文件。"""
    return bool(_REPORT_REQUEST_RE.search((message or "").strip()))


def user_requests_new_query(message: str) -> bool:
    """用户是否在要新的取数，而不是只改图表展示。"""
    return bool(_DATA_QUERY_RE.search((message or "").strip()))


def looks_like_chart_request(message: str) -> bool:
    text = (message or "").strip()
    if not text or not user_requests_chart(text):
        return False
    # extractor 的 task 提示：同一句既要查数又要画图时仍标 query，由规划节点决定是否续跑图表。
    if user_requests_new_query(text) and len(text) > 12:
        return False
    return True


def resolve_extractor_task(
        extraction: RetrievalExtractionResult,
        user_message: str = "",
) -> str:
    explicit = normalize_extractor_task(extraction.task)
    message = user_message or ""
    if user_requests_report(message) and not user_requests_new_query(message):
        return EXTRACTOR_TASK_REPORT
    if (
            explicit == EXTRACTOR_TASK_CHART
            and _DATA_QUERY_RE.search(message)
            and _CHART_REQUEST_RE.search(message)
            and len(message) > 12
    ):
        return EXTRACTOR_TASK_QUERY
    if (
            explicit == EXTRACTOR_TASK_REPORT
            and _DATA_QUERY_RE.search(message)
            and _REPORT_REQUEST_RE.search(message)
            and len(message) > 12
    ):
        return EXTRACTOR_TASK_QUERY
    if explicit:
        return explicit
    if looks_like_chart_request(message):
        return EXTRACTOR_TASK_CHART
    if (extraction.intent or "").strip() == NON_DATA_QUERY_INTENT:
        return EXTRACTOR_TASK_CHITCHAT
    if not (extraction.retrieval_query or "").strip() and not extraction.keywords:
        return EXTRACTOR_TASK_CHITCHAT
    return EXTRACTOR_TASK_QUERY


def format_extractor_user_message(*, history: str, current_message: str) -> str:
    """构造 extractor 可读的用户消息，包含历史对话和最新输入。"""
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
    """由于 ADK State 没有 pop/del，通过写入空字符串清理指定键。"""
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


def extract_sql_logic_from_output(text: str) -> str:
    """从 sql_generator 输出中抽出中文逻辑说明，去掉 SQL 代码块。"""
    cleaned = re.sub(
        r"```(?:sql)?\s*.*?```",
        "",
        text or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned[:2000]


def format_sql_generator_user_message(ctx: Context, node_input) -> str:
    """构造 sql_generator 的表结构上下文；重试时追加校验反馈。"""
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
    """将已执行 SQL 和结果集格式化为面向用户的 Markdown。"""
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


def _serialize_result_rows(rows: list[dict[str, object]]) -> tuple[list[str], list[dict[str, str]]]:
    if not rows:
        return [], []
    columns = [str(key) for key in rows[0].keys()]
    serialized: list[dict[str, str]] = []
    for row in rows:
        serialized.append({
            column: _format_cell_value(row.get(column))
            for column in columns
        })
    return columns, serialized


def save_query_snapshot(
        ctx: Context,
        result_sets: list[tuple[list[dict[str, object]], bool]],
) -> None:
    """问数成功后写入快照，供后续按需出图使用；reset_sql_turn_state 不会清理。"""
    sets: list[dict[str, Any]] = []
    for rows, truncated in result_sets:
        columns, serialized_rows = _serialize_result_rows(rows)
        sets.append({
            "columns": columns,
            "rows": serialized_rows,
            "truncated": bool(truncated),
            "row_count": len(serialized_rows),
        })
    schema_context = (ctx.state.get(SQL_SCHEMA_CONTEXT_STATE_KEY) or "").strip()
    if len(schema_context) > QUERY_SNAPSHOT_MAX_CONTEXT_CHARS:
        schema_context = schema_context[: QUERY_SNAPSHOT_MAX_CONTEXT_CHARS - 1].rstrip() + "…"
    payload = {
        "user_question": resolve_user_message(ctx),
        "schema_context": schema_context,
        "selected_tables": selected_table_names_from_state(ctx),
        "result_sets": sets,
        "row_count": sum(item["row_count"] for item in sets),
    }
    ctx.state[QUERY_SNAPSHOT_STATE_KEY] = json.dumps(payload, ensure_ascii=False)
    ctx.state[QUERY_SNAPSHOT_FRESH_STATE_KEY] = "1"
    # 新查询结果作废上一张图和上一份报告，避免套到新数据。
    clear_state_key(ctx, LAST_CHART_SPEC_STATE_KEY)
    clear_state_key(ctx, LAST_REPORT_SPEC_STATE_KEY)


def load_query_snapshot(ctx: Context) -> dict[str, Any] | None:
    raw = ctx.state.get(QUERY_SNAPSHOT_STATE_KEY)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    sets = data.get("result_sets")
    if not isinstance(sets, list) or not sets:
        return None
    return data


def query_snapshot_is_fresh(ctx: Context) -> bool:
    return str(ctx.state.get(QUERY_SNAPSHOT_FRESH_STATE_KEY) or "").strip() == "1"


def save_extractor_result(ctx: Context, extraction: RetrievalExtractionResult) -> None:
    ctx.state[EXTRACTOR_RESULT_STATE_KEY] = extraction.model_dump_json()


def load_extractor_result(ctx: Context) -> RetrievalExtractionResult:
    return coerce_extractor_result(ctx.state.get(EXTRACTOR_RESULT_STATE_KEY)) or RetrievalExtractionResult(
        retrieval_query="",
        keywords=[],
        intent="",
        task="",
    )


def extractor_result_has_content(extraction: RetrievalExtractionResult | None) -> bool:
    if extraction is None:
        return False
    return bool(
        (extraction.retrieval_query or "").strip()
        or extraction.keywords
        or (extraction.intent or "").strip()
        or (extraction.task or "").strip()
    )


def coerce_extractor_result(node_input: Any) -> RetrievalExtractionResult | None:
    """把 ADK 节点输入还原为解析结果。FunctionNode 会把 BaseModel 打成 dict。"""
    if node_input is None:
        return None
    if isinstance(node_input, RetrievalExtractionResult):
        return node_input
    nested = getattr(node_input, "output", None)
    if nested is not None and nested is not node_input:
        parsed = coerce_extractor_result(nested)
        if parsed is not None:
            return parsed
    data: Any = node_input
    if isinstance(node_input, str):
        text = node_input.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    if not any(
        key in data
        for key in ("retrieval_query", "keywords", "intent", "task")
    ):
        return None
    keywords = data.get("keywords")
    return RetrievalExtractionResult(
        retrieval_query=str(data.get("retrieval_query") or "").strip(),
        keywords=[str(item).strip() for item in keywords if str(item).strip()]
        if isinstance(keywords, list)
        else [],
        intent=str(data.get("intent") or "").strip(),
        task=str(data.get("task") or "").strip(),
    )


def extraction_from_node_input(ctx: Context, node_input: Any) -> RetrievalExtractionResult:
    parsed = coerce_extractor_result(node_input)
    if extractor_result_has_content(parsed):
        return parsed
    stored = load_extractor_result(ctx)
    if extractor_result_has_content(stored):
        return stored
    return parsed or stored


def save_plan(ctx: Context, plan: dict[str, Any]) -> None:
    ctx.state[PLAN_STATE_KEY] = json.dumps(plan, ensure_ascii=False)


def load_plan(ctx: Context) -> dict[str, Any] | None:
    raw = ctx.state.get(PLAN_STATE_KEY)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict) and (data.get("next") or isinstance(data.get("steps"), list)):
        return data
    return None


def save_plan_observation(ctx: Context, observation: dict[str, Any]) -> None:
    ctx.state[PLAN_OBSERVATION_STATE_KEY] = json.dumps(observation, ensure_ascii=False)


def load_plan_observation(ctx: Context) -> dict[str, Any] | None:
    raw = ctx.state.get(PLAN_OBSERVATION_STATE_KEY)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def load_plan_turn(ctx: Context) -> int:
    raw = ctx.state.get(PLAN_TURN_COUNT_STATE_KEY)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


def increment_plan_turn(ctx: Context) -> int:
    count = load_plan_turn(ctx) + 1
    ctx.state[PLAN_TURN_COUNT_STATE_KEY] = str(count)
    return count


def save_last_chart_spec(ctx: Context, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    ctx.state[LAST_CHART_SPEC_STATE_KEY] = json.dumps(payload, ensure_ascii=False)


def load_last_chart_spec(ctx: Context) -> dict[str, Any] | None:
    raw = ctx.state.get(LAST_CHART_SPEC_STATE_KEY)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        return data
    return None


def save_last_report_spec(ctx: Context, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    ctx.state[LAST_REPORT_SPEC_STATE_KEY] = json.dumps(payload, ensure_ascii=False)


def load_last_report_spec(ctx: Context) -> dict[str, Any] | None:
    raw = ctx.state.get(LAST_REPORT_SPEC_STATE_KEY)
    data: Any = raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
    if isinstance(data, dict):
        return data
    return None


def format_table_selector_user_message(stage: SearchStageOutput) -> str:
    """构造 table_selector 可读的用户消息，避免 ADK BaseModel 直接转储为 JSON。"""
    user_message = (stage.user_message or "").strip() or "（无）"
    retrieval_context = (stage.retrieval_context or "").strip() or "（无检索结果）"
    candidate_lines = [
        f"- `{table_name}`"
        for table_name in stage.table_candidates
    ]
    candidates = "\n".join(candidate_lines) if candidate_lines else "（无候选表）"
    return "\n".join([
        "## 用户问题",
        "",
        user_message,
        "",
        "## 候选数据表白名单",
        "",
        candidates,
        "",
        "注意：只能从上述白名单中选择表名，必须逐字复制完整表名，不得改写、缩写、补全或输出相似表名。",
        "",
        "## 检索上下文",
        "",
        retrieval_context,
    ])
