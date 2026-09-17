import asyncio
import logging
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
    SQL_SELECTED_TABLE_DETAILS_STATE_KEY,
    SQL_LAST_ATTEMPT_STATE_KEY,
    SQL_VALIDATED_SQL_STATE_KEY,
    SQL_GENERATOR_LOGIC_STATE_KEY,
    SQL_VALIDATION_FEEDBACK_STATE_KEY,
    EXTRACTOR_RESULT_STATE_KEY,
    MAX_PLANNER_TURNS,
    PLAN_OBSERVATION_STATE_KEY,
    PLAN_STATE_KEY,
    PLAN_TURN_COUNT_STATE_KEY,
    QUERY_SNAPSHOT_FRESH_STATE_KEY,
    NO_USABLE_TABLE_MESSAGE,
    clear_state_key,
    conversation_history_for_extractor,
    extract_sql_logic_from_output,
    format_extractor_user_message,
    format_sql_execution_response,
    format_sql_generator_user_message,
    save_sql_last_attempt,
    format_table_selector_user_message,
    increment_plan_turn,
    load_extractor_result,
    load_last_chart_spec,
    save_last_report_spec,
    load_plan,
    load_plan_observation,
    load_plan_turn,
    load_query_snapshot,
    query_snapshot_is_fresh,
    extraction_from_node_input,
    extractor_result_has_content,
    resolve_user_message,
    resolve_workspace_id,
    selected_table_names_from_state,
    save_extractor_result,
    save_last_chart_spec,
    save_plan,
    save_plan_observation,
    save_query_snapshot,
    save_user_message_to_state,
    sql_execution_max_rows,
    text_from_node_input,
)
from adk_agents.copilot.plan import (
    OBS_CHART_DONE,
    OBS_CHART_INFEASIBLE,
    OBS_NO_SNAPSHOT,
    OBS_NO_TABLES,
    OBS_QUERY_FAILED,
    OBS_QUERY_SUCCESS,
    OBS_VALIDATION_FAILED,
    OBS_REPORT_DONE,
    OBS_REPORT_INFEASIBLE,
    PLAN_STEP_CHART,
    PLAN_STEP_QUERY,
    PLAN_STEP_REPORT,
    format_planner_user_message,
    plan_dump,
    plan_from_model_text,
    plan_is_chitchat,
    plan_is_stop,
    plan_next,
)
from adk_agents.copilot.prompts import (
    CHITCHAT_INSTRUCTION,
    CHART_PLANNER_INSTRUCTION,
    PLANNER_INSTRUCTION,
    REPORT_PLANNER_INSTRUCTION,
    build_sql_generator_instruction,
    TABLE_SELECTOR_INSTRUCTION,
)
from adk_agents.copilot.chart import (
    format_chart_planner_user_message,
    materialize_chart_spec,
)
from adk_agents.copilot.report import (
    format_report_planner_user_message,
    materialize_analysis_report,
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

logger = logging.getLogger(__name__)

SQL_DIALECT = get_sql_read_dialect()
TABLE_CANDIDATES_STATE_KEY = "table_candidates"
SELECTED_TABLES_STATE_KEY = "selected_tables"
RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY = "retrieved_knowledge_uuids"
RETRIEVED_SQL_UUIDS_STATE_KEY = "retrieved_sql_uuids"

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
            SQL_GENERATOR_LOGIC_STATE_KEY,
            SQL_GENERATION_ATTEMPT_STATE_KEY,
            SELECTED_TABLES_STATE_KEY,
            TABLE_CANDIDATES_STATE_KEY,
            RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY,
            RETRIEVED_SQL_UUIDS_STATE_KEY,
            PLAN_STATE_KEY,
            PLAN_OBSERVATION_STATE_KEY,
            PLAN_TURN_COUNT_STATE_KEY,
            EXTRACTOR_RESULT_STATE_KEY,
            QUERY_SNAPSHOT_FRESH_STATE_KEY,
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
    task = ""
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict):
            retrieval_query = str(data.get("retrieval_query", "")).strip()
            raw_kw = data.get("keywords", [])
            if isinstance(raw_kw, list):
                keywords = [str(k).strip() for k in raw_kw if str(k).strip()]
            intent = str(data.get("intent", "")).strip()
            task = str(data.get("task", "")).strip()
    except (json.JSONDecodeError, ValueError):
        pass

    result = RetrievalExtractionResult(
        retrieval_query=retrieval_query,
        keywords=keywords,
        intent=intent,
        task=task,
    )
    save_extractor_result(ctx, result)
    return result


def _extraction_from_node_input(ctx: Context, node_input) -> RetrievalExtractionResult:
    return extraction_from_node_input(ctx, node_input)


def _build_plan_observation(
        ctx: Context,
        *,
        last_next: str,
        status: str,
        code: str,
        detail: str = "",
) -> dict:
    snapshot = load_query_snapshot(ctx)
    columns: list[str] = []
    row_count = 0
    if snapshot:
        row_count = int(snapshot.get("row_count") or 0)
        result_sets = snapshot.get("result_sets")
        if isinstance(result_sets, list) and result_sets and isinstance(result_sets[0], dict):
            raw_columns = result_sets[0].get("columns")
            if isinstance(raw_columns, list):
                columns = [str(item).strip() for item in raw_columns if str(item).strip()]
    return {
        "last_next": last_next,
        "status": status,
        "code": code,
        "detail": (detail or "").strip()[:800],
        "has_snapshot": bool(snapshot),
        "snapshot_columns": columns,
        "snapshot_row_count": row_count,
    }


def prepare_planner_input(ctx: Context, node_input=None) -> Event:
    """把解析结果、快照和上一管道观察交给规划节点。"""
    extraction = _extraction_from_node_input(ctx, node_input)
    if extractor_result_has_content(extraction):
        save_extractor_result(ctx, extraction)
    if load_plan_turn(ctx) >= MAX_PLANNER_TURNS:
        return Event(route="stop", output=extraction)
    snapshot = load_query_snapshot(ctx)
    message = format_planner_user_message(
        user_message=resolve_user_message(ctx),
        extraction=extraction,
        has_snapshot=bool(snapshot),
        snapshot=snapshot,
        last_chart=load_last_chart_spec(ctx),
        observation=load_plan_observation(ctx),
        previous_plan=load_plan(ctx),
        remaining_turns=max(0, MAX_PLANNER_TURNS - load_plan_turn(ctx)),
    )
    return Event(output=message)


planner = Agent(
    model=get_llm_model(),
    name="planner",
    description="根据用户问题、会话状态和上一管道观察决定下一步执行查询、出图、闲聊还是结束。",
    instruction=PLANNER_INSTRUCTION,
)


def parse_plan(ctx: Context, node_input) -> str:
    """解析规划 JSON，并结合观察约束下一步。"""
    increment_plan_turn(ctx)
    extraction = load_extractor_result(ctx)
    snapshot = load_query_snapshot(ctx)
    plan = plan_from_model_text(
        text_from_node_input(node_input),
        extraction=extraction,
        user_message=resolve_user_message(ctx),
        has_snapshot=bool(snapshot),
        observation=load_plan_observation(ctx),
        previous_plan=load_plan(ctx),
    )
    save_plan(ctx, plan)
    return plan_dump(plan)


def route_after_plan(ctx: Context, node_input) -> Event:
    """按本轮下一步分流；管道结束后再回到规划。"""
    extraction = load_extractor_result(ctx)
    plan = load_plan(ctx)
    if plan_is_stop(plan):
        return Event(route="stop", output=extraction)
    if plan_is_chitchat(plan):
        return Event(route="chitchat", output=extraction)
    if plan_next(plan) == PLAN_STEP_CHART:
        return Event(route="chart", output=extraction)
    if plan_next(plan) == PLAN_STEP_REPORT:
        return Event(route="report", output=extraction)
    return Event(output=extraction)


def end_plan(ctx: Context, node_input=None) -> str:
    """规划结束；用户可见内容已由问数/图表/闲聊节点产出。"""
    return ""


def observe_query_result(ctx: Context, node_input) -> Event:
    """问数管道结束后记录观察，供规划决定是否出图或结束。"""
    detail = text_from_node_input(node_input)
    if query_snapshot_is_fresh(ctx) and load_query_snapshot(ctx):
        observation = _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_QUERY,
            status="success",
            code=OBS_QUERY_SUCCESS,
            detail="查询成功，已写入结果快照。",
        )
    else:
        observation = _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_QUERY,
            status="failed",
            code=OBS_QUERY_FAILED,
            detail=detail or "查询未产生可用结果。",
        )
    save_plan_observation(ctx, observation)
    return Event(output=load_extractor_result(ctx))


def observe_no_tables(ctx: Context, node_input=None) -> Event:
    save_plan_observation(
        ctx,
        _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_QUERY,
            status="failed",
            code=OBS_NO_TABLES,
            detail="未匹配到可用数据表。",
        ),
    )
    return Event(output=load_extractor_result(ctx))


def observe_validation_failure(ctx: Context, node_input=None) -> Event:
    save_plan_observation(
        ctx,
        _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_QUERY,
            status="failed",
            code=OBS_VALIDATION_FAILED,
            detail="SQL 校验多次失败，问数管道已终止。",
        ),
    )
    return Event(output=load_extractor_result(ctx))


def observe_no_snapshot(ctx: Context, node_input=None) -> Event:
    last_next = plan_next(load_plan(ctx)) or PLAN_STEP_CHART
    save_plan_observation(
        ctx,
        _build_plan_observation(
            ctx,
            last_next=last_next,
            status="blocked",
            code=OBS_NO_SNAPSHOT,
            detail="当前没有可分析的查询快照。",
        ),
    )
    return Event(output=load_extractor_result(ctx))


def observe_chart_result(ctx: Context, node_input) -> Event:
    raw = text_from_node_input(node_input)
    feasible = False
    summary = ""
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            feasible = payload.get("feasible") is True
            summary = str(payload.get("summary") or "").strip()
    except json.JSONDecodeError:
        pass
    save_plan_observation(
        ctx,
        _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_CHART,
            status="success" if feasible else "failed",
            code=OBS_CHART_DONE if feasible else OBS_CHART_INFEASIBLE,
            detail=summary or ("图表已生成。" if feasible else "当前结果无法生成图表。"),
        ),
    )
    return Event(output=load_extractor_result(ctx))


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


def prepare_chart_planner_input(ctx: Context, node_input=None) -> Event:
    """没有查询快照时回到规划；否则带上出图要求和上一张图规格。"""
    snapshot = load_query_snapshot(ctx)
    if not snapshot:
        return Event(route="no_snapshot", output=node_input)
    plan = load_plan(ctx) or {}
    user_request = str(plan.get("chart_request") or "").strip() or resolve_user_message(ctx)
    message = format_chart_planner_user_message(
        user_request=user_request,
        snapshot=snapshot,
        last_chart=load_last_chart_spec(ctx),
    )
    return Event(output=message)


chart_planner = Agent(
    model=get_llm_model(),
    name="chart_planner",
    description="根据检索上下文和已有查询结果规划图表维度与类型。",
    instruction=CHART_PLANNER_INSTRUCTION,
)


def parse_chart_spec(ctx: Context, node_input) -> str:
    """校验模型输出的图表 JSON，并基于快照结果集聚合出可绑定数据。"""
    snapshot = load_query_snapshot(ctx)
    plan = load_plan(ctx) or {}
    user_request = str(plan.get("chart_request") or "").strip() or resolve_user_message(ctx)
    payload = materialize_chart_spec(
        text_from_node_input(node_input),
        snapshot,
        user_request=user_request,
        last_chart=load_last_chart_spec(ctx),
    )
    save_last_chart_spec(ctx, payload)
    return json.dumps(payload, ensure_ascii=False)


async def prepare_report_planner_input(ctx: Context, node_input=None) -> Event:
    """没有查询快照时回到规划；否则带上报告要求和结果概要。"""
    snapshot = load_query_snapshot(ctx)
    if not snapshot:
        return Event(route="no_snapshot", output=node_input)
    # 问数链路刚连续调用过多次模型，稍候再写报告，降低网关突发限流。
    await asyncio.sleep(2)
    message = format_report_planner_user_message(
        user_request=resolve_user_message(ctx),
        snapshot=snapshot,
    )
    return Event(output=message)


report_planner = Agent(
    model=get_llm_model(),
    name="report_planner",
    description="根据已有查询结果规划分析报告的总体情况、分类分析和结论。",
    instruction=REPORT_PLANNER_INSTRUCTION,
)


def parse_report_spec(ctx: Context, node_input) -> str:
    """校验报告 JSON，基于快照聚合图表并写出 Word 文件。"""
    snapshot = load_query_snapshot(ctx)
    payload = materialize_analysis_report(
        text_from_node_input(node_input),
        snapshot,
        user_request=resolve_user_message(ctx),
    )
    save_last_report_spec(ctx, payload)
    if payload.get("feasible") and payload.get("analyses"):
        charts = [
            item.get("chart")
            for item in payload.get("analyses") or []
            if isinstance(item, dict) and isinstance(item.get("chart"), dict)
        ]
        if charts:
            save_last_chart_spec(ctx, {
                "kind": "chart_spec",
                "feasible": True,
                "summary": str(payload.get("summary") or "").strip(),
                "charts": charts,
            })
    return json.dumps(payload, ensure_ascii=False)


def observe_report_result(ctx: Context, node_input) -> Event:
    raw = text_from_node_input(node_input)
    feasible = False
    summary = ""
    try:
        payload = json.loads(raw) if raw else {}
        if isinstance(payload, dict):
            feasible = payload.get("feasible") is True
            summary = str(payload.get("summary") or "").strip()
    except json.JSONDecodeError:
        pass
    save_plan_observation(
        ctx,
        _build_plan_observation(
            ctx,
            last_next=PLAN_STEP_REPORT,
            status="success" if feasible else "failed",
            code=OBS_REPORT_DONE if feasible else OBS_REPORT_INFEASIBLE,
            detail=summary or ("分析报告已生成。" if feasible else "当前结果无法生成分析报告。"),
        ),
    )
    return Event(output=load_extractor_result(ctx))


async def search(
        ctx: Context,
        node_input,
) -> SearchStageOutput:
    extraction = _extraction_from_node_input(ctx, node_input)
    if extractor_result_has_content(extraction):
        save_extractor_result(ctx, extraction)
    workspace_id = resolve_workspace_id(ctx)
    queries = [extraction.retrieval_query, *extraction.keywords, extraction.intent]
    queries = [q for q in queries if (q or "").strip()]
    user_message = (extraction.retrieval_query or "").strip() or resolve_user_message(ctx)
    if not queries and user_message:
        queries = [user_message]

    search_result = await search_and_build_llm_context_with_hits(
        queries,
        workspace_id=workspace_id,
        limit=10,
    )
    retrieval_context = search_result.context
    table_candidates = _extract_table_candidates(retrieval_context)
    ctx.state[TABLE_CANDIDATES_STATE_KEY] = table_candidates
    ctx.state[RETRIEVED_KNOWLEDGE_UUIDS_STATE_KEY] = search_result.unbound_knowledge_hit_uuids
    ctx.state[RETRIEVED_SQL_UUIDS_STATE_KEY] = [
        str(item).strip()
        for item in (search_result.sql_hit_uuids or [])
        if str(item).strip()
    ]

    if user_message:
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
        extra_sql_uuids=[
            str(item).strip()
            for item in (ctx.state.get(RETRIEVED_SQL_UUIDS_STATE_KEY) or [])
            if str(item).strip()
        ],
    )
    ctx.state[SQL_SCHEMA_CONTEXT_STATE_KEY] = schema_text
    ctx.state[SQL_GENERATION_ATTEMPT_STATE_KEY] = 0
    clear_state_key(ctx, SQL_VALIDATION_FEEDBACK_STATE_KEY)
    clear_state_key(ctx, SQL_LAST_ATTEMPT_STATE_KEY)
    clear_state_key(ctx, SQL_VALIDATED_SQL_STATE_KEY)
    clear_state_key(ctx, SQL_GENERATOR_LOGIC_STATE_KEY)
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
        logic = extract_sql_logic_from_output(raw_text)
        if logic:
            ctx.state[SQL_GENERATOR_LOGIC_STATE_KEY] = logic
        else:
            clear_state_key(ctx, SQL_GENERATOR_LOGIC_STATE_KEY)
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

    save_query_snapshot(ctx, result_sets)
    await _precipitate_executed_sql(ctx, statements, result_sets)
    return format_sql_execution_response(statements, result_sets)


async def _precipitate_executed_sql(
        ctx: Context,
        statements: list[str],
        result_sets: list[tuple[list[dict[str, object]], bool]],
) -> None:
    """问数成功且查出数据后，把 Query-SQL 沉淀为 LLM 生成样例。"""
    has_rows = any(bool(rows) for rows, _ in result_sets)
    if not has_rows:
        return
    extraction = load_extractor_result(ctx)
    question = (
        (extraction.retrieval_query or "").strip()
        or resolve_user_message(ctx).strip()
    )
    content = "\n\n".join(item.strip() for item in statements if item.strip())
    logic = str(ctx.state.get(SQL_GENERATOR_LOGIC_STATE_KEY) or "").strip()
    if not question or not content:
        return
    try:
        from tools.graph.crud.sql import upsert_llm_generated_sql

        await upsert_llm_generated_sql(
            resolve_workspace_id(ctx),
            question=question,
            logic=logic or "由问数助手根据用户问题生成。",
            content=content,
            dialect=SQL_DIALECT,
        )
    except Exception:
        logger.exception("沉淀 LLM 生成 SQL 失败")


root_agent = Workflow(
    name="TYWS",
    edges=[
        (
            "START",
            reset_sql_turn_state,
            prepare_extractor_input,
            extractor,
            parse_extractor_output,
            prepare_planner_input,
        ),
        (
            prepare_planner_input,
            {
                "stop": end_plan,
                DEFAULT_ROUTE: planner,
            },
        ),
        (
            planner,
            parse_plan,
            route_after_plan,
            {
                "stop": end_plan,
                "chitchat": prepare_chitchat_input,
                "chart": prepare_chart_planner_input,
                "report": prepare_report_planner_input,
                DEFAULT_ROUTE: search,
            },
        ),
        (
            prepare_chitchat_input,
            chitchat,
        ),
        (
            prepare_chart_planner_input,
            {
                "no_snapshot": observe_no_snapshot,
                DEFAULT_ROUTE: chart_planner,
            },
        ),
        (
            prepare_report_planner_input,
            {
                "no_snapshot": observe_no_snapshot,
                DEFAULT_ROUTE: report_planner,
            },
        ),
        (
            observe_no_snapshot,
            prepare_planner_input,
        ),
        (
            chart_planner,
            parse_chart_spec,
            observe_chart_result,
            prepare_planner_input,
        ),
        (
            report_planner,
            parse_report_spec,
            observe_report_result,
            prepare_planner_input,
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
        ),
        (
            format_no_table_response,
            observe_no_tables,
            prepare_planner_input,
        ),
        (
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
        (
            format_validation_failure_response,
            observe_validation_failure,
            prepare_planner_input,
        ),
        (
            execute_sql,
            observe_query_result,
            prepare_planner_input,
        ),
    ],
)
