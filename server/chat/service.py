from __future__ import annotations

import os
import asyncio
import json
import re
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.chat.protocol import (
    AgentDescriptor,
    StreamEvent,
    StreamEventType,
    new_turn_id,
    sse_line,
)
from tools.llm import is_llm_quota_or_rate_limit_error
from tools.retrieval import parse_sql_reference_samples

_COPILOT_AGENT_ID = "copilot"
_COPILOT_APP_NAME = "TYWS-Copilot"
_COPILOT_WORKSPACE_STATE_KEY = "copilot_workspace_id"

_STAGE_ORDER = [
    "extractor",
    "planner",
    "parse_plan",
    "chitchat",
    "search",
    "table_selector",
    "route_after_table_selection",
    "format_no_table_response",
    "load_sql_schema_context",
    "sql_generator",
    "validate_sql",
    "format_validation_failure_response",
    "execute_sql",
    "prepare_chart_planner_input",
    "chart_planner",
    "parse_chart_spec",
    "format_no_chart_snapshot_response",
    "prepare_report_planner_input",
    "report_planner",
    "parse_report_spec",
]
_STAGE_LABELS = {
    "extractor": "🧭 解析问题",
    "planner": "🧩 规划步骤",
    "parse_plan": "🧩 规划步骤",
    "chitchat": "💬 闲聊回复",
    "search": "🔎 检索上下文",
    "table_selector": "🗂️ 选择数据表",
    "route_after_table_selection": "🧩 判断选表结果",
    "format_no_table_response": "⚠️ 未匹配到数据表",
    "load_sql_schema_context": "🧱 汇总表结构",
    "sql_generator": "🧠 生成 SQL",
    "validate_sql": "✅ 校验 SQL",
    "format_validation_failure_response": "⚠️ SQL 修复失败",
    "execute_sql": "🚀 执行 SQL",
    "prepare_chart_planner_input": "📊 分析图表",
    "chart_planner": "📊 分析图表",
    "parse_chart_spec": "📊 分析图表",
    "format_no_chart_snapshot_response": "📊 分析图表",
    "prepare_report_planner_input": "📄 分析报告",
    "report_planner": "📄 分析报告",
    "parse_report_spec": "📄 分析报告",
}
_TEXT_STREAM_STAGES = frozenset({
    "sql_generator",
    "chitchat",
    "format_no_table_response",
})
_BUFFERED_TEXT_STAGES = frozenset({"sql_generator"})

_WORKFLOW_DISPLAY_GROUPS = [
    ("extractor", "🧭 解析问题", ("extractor",)),
    ("planner", "🧩 规划步骤", ("planner", "parse_plan")),
    ("search", "🔎 检索上下文", ("search",)),
    (
        "table_context",
        "🧱 确定数据表与表结构",
        (
            "table_selector",
            "route_after_table_selection",
            "format_no_table_response",
            "load_sql_schema_context",
        ),
    ),
    ("sql_generator", "🧠 生成 SQL", ("sql_generator",)),
    (
        "validate_sql",
        "✅ 校验 SQL",
        ("validate_sql", "format_validation_failure_response"),
    ),
    ("execute_sql", "🚀 执行 SQL", ("execute_sql",)),
        (
            "chart",
            "📊 分析图表",
            (
                "prepare_chart_planner_input",
                "chart_planner",
                "parse_chart_spec",
                "format_no_chart_snapshot_response",
            ),
        ),
        (
            "report",
            "📄 分析报告",
            (
                "prepare_report_planner_input",
                "report_planner",
                "parse_report_spec",
            ),
        ),
]


class AdkCopilotRuntime:
    """用于 Copilot 工作流的 ADK Runner 封装。"""

    def __init__(self) -> None:
        self.available = False
        self.unavailable_reason: str | None = None
        self._runner: Any | None = None
        self._types: Any | None = None
        self._run_config: Any | None = None

        try:
            from adk_agents.copilot.agent import root_agent
            from google.adk.agents.run_config import RunConfig, StreamingMode
            from google.adk.runners import Runner
            from google.adk.sessions.database_session_service import DatabaseSessionService
            from google.adk.sessions.in_memory_session_service import InMemorySessionService
            from google.adk.sessions.sqlite_session_service import SqliteSessionService
            from google.genai import types
        except Exception as exc:  # pragma: no cover - import failure path
            self.unavailable_reason = f"ADK 导入失败：{exc}"
            return

        try:
            session_service = self._build_session_service(
                DatabaseSessionService=DatabaseSessionService,
                SqliteSessionService=SqliteSessionService,
                InMemorySessionService=InMemorySessionService,
            )
            self._runner = Runner(
                app_name=_COPILOT_APP_NAME,
                agent=root_agent,
                session_service=session_service,
                auto_create_session=True,
            )
            self._run_config = RunConfig(streaming_mode=StreamingMode.SSE)
            self._types = types
            self.available = True
        except Exception as exc:  # pragma: no cover - runtime boot failure path
            self.unavailable_reason = f"ADK Runner 初始化失败：{exc}"

    @staticmethod
    def _build_session_service(
            *,
            DatabaseSessionService,
            SqliteSessionService,
            InMemorySessionService,
    ):
        db_url = os.environ.get("ADK_SESSION_DB_URL", "").strip()
        if db_url:
            return DatabaseSessionService(db_url=db_url)

        mode = os.environ.get("ADK_SESSION_MODE", "sqlite").strip().lower()
        if mode == "in_memory":
            return InMemorySessionService()

        default_path = (
            Path(__file__).resolve().parents[2] / "data" / "chat" / "adk_sessions.sqlite3"
        )
        sqlite_path = os.environ.get("ADK_SESSION_SQLITE_PATH", "").strip()
        db_path = Path(sqlite_path).expanduser() if sqlite_path else default_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        return SqliteSessionService(db_path=str(db_path))

    async def _apply_workspace_to_session(
            self,
            *,
            user_id: str,
            session_id: str,
            workspace_id: str,
    ) -> None:
        """将 workspace_id 写入 ADK 会话状态。

        ADK Workflow 节点通过 ``_run_node_async`` 运行，目前不会应用
        ``run_async(state_delta=...)``，因此需要显式写入会话状态，否则
        agent 会读取默认 workspace。
        """
        from google.adk.agents.invocation_context import new_invocation_context_id
        from google.adk.events.event import Event
        from google.adk.events.event_actions import EventActions

        session_service = self._runner.session_service
        session = await session_service.get_session(
            app_name=_COPILOT_APP_NAME,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            session = await session_service.create_session(
                app_name=_COPILOT_APP_NAME,
                user_id=user_id,
                session_id=session_id,
            )

        await session_service.append_event(
            session=session,
            event=Event(
                invocation_id=new_invocation_context_id(),
                author="user",
                actions=EventActions(
                    state_delta={_COPILOT_WORKSPACE_STATE_KEY: workspace_id},
                ),
            ),
        )

    async def stream(
            self,
            *,
            user_id: str,
            session_id: str,
            message: str,
            workspace_id: str,
    ):
        if not self.available or self._runner is None or self._types is None:
            return

        await self._apply_workspace_to_session(
            user_id=user_id,
            session_id=session_id,
            workspace_id=workspace_id,
        )

        new_message = self._types.UserContent(parts=[self._types.Part.from_text(text=message)])
        async for event in self._runner.run_async(
            user_id=user_id,
            session_id=session_id,
            new_message=new_message,
            state_delta={_COPILOT_WORKSPACE_STATE_KEY: workspace_id},
            run_config=self._run_config,
        ):
            yield event


def _output_to_dict(output: Any) -> dict[str, Any] | None:
    if isinstance(output, dict):
        return output
    model_dump = getattr(output, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            dumped = None
        if isinstance(dumped, dict):
            return dumped
    if isinstance(output, str):
        parsed = _try_parse_json(output)
        if isinstance(parsed, dict):
            return parsed
    return None


def _is_table_selection_result(value: dict[str, Any] | None) -> bool:
    if not isinstance(value, dict):
        return False
    tables = value.get("tables")
    selected_tables = value.get("selected_tables")
    if isinstance(tables, list) and len(tables) > 0:
        return True
    return isinstance(selected_tables, list) and len(selected_tables) > 0


def _table_selection_from_event(event: Any, *, stage_text: str = "") -> dict[str, Any] | None:
    structured = _output_to_dict(getattr(event, "output", None))
    if _is_table_selection_result(structured):
        return structured
    parsed = _parse_table_selector_json(stage_text)
    if _is_table_selection_result(parsed):
        return parsed
    return None


def _schema_plan_payload(selection: dict[str, Any]) -> dict[str, Any]:
    tables = selection.get("tables")
    if not isinstance(tables, list):
        tables = selection.get("selected_tables")
    table_details = selection.get("table_details")
    reason = selection.get("reason")
    if not isinstance(reason, str):
        reason = selection.get("reasoning")
    return {
        "kind": "schema_plan",
        "selected_tables": tables if isinstance(tables, list) else [],
        "selected_table_details": table_details if isinstance(table_details, list) else [],
        "reasoning": reason if isinstance(reason, str) else "",
    }


def _normalize_table_detail_list(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        details.append({
            "name": name,
            "comment": str(item.get("comment", "")).strip(),
            "description": str(item.get("description", "")).strip(),
        })
    return details


def _collect_selected_table_names(selection: dict[str, Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()

    def add(raw: Any) -> None:
        name = str(raw).strip()
        if not name:
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        names.append(name)

    for key in ("tables", "selected_tables"):
        value = selection.get(key)
        if isinstance(value, list):
            for item in value:
                add(item)

    for key in ("table_details", "selected_table_details"):
        for detail in _normalize_table_detail_list(selection.get(key)):
            add(detail["name"])

    return names


async def _load_selected_table_details(
        *,
        workspace_id: str,
        table_names: list[str],
) -> list[dict[str, str]]:
    names = [str(name).strip() for name in table_names if str(name).strip()]
    if not names:
        return []

    from tools.retrieval import load_selected_tables_bundle

    bundle = await load_selected_tables_bundle(workspace_id, names)
    detail_by_name = {
        table.name.lower(): {
            "name": table.name,
            "comment": table.comment or "",
            "description": table.description or "",
        }
        for table in bundle.tables
    }
    return [
        detail_by_name.get(
            name.lower(),
            {"name": name, "comment": "", "description": ""},
        )
        for name in names
    ]


async def _enrich_table_selection_details(
        selection: dict[str, Any],
        *,
        workspace_id: str,
) -> dict[str, Any]:
    result = dict(selection)
    table_names = _collect_selected_table_names(result)
    if not table_names:
        return result

    existing_details = (
        _normalize_table_detail_list(result.get("table_details"))
        or _normalize_table_detail_list(result.get("selected_table_details"))
    )
    loaded_details = await _load_selected_table_details(
        workspace_id=workspace_id,
        table_names=table_names,
    )

    existing_by_name = {item["name"].lower(): item for item in existing_details}
    loaded_by_name = {item["name"].lower(): item for item in loaded_details}
    merged_details: list[dict[str, str]] = []

    for name in table_names:
        key = name.lower()
        existing = existing_by_name.get(key, {})
        loaded = loaded_by_name.get(key, {})
        merged_details.append({
            "name": name,
            "comment": existing.get("comment") or loaded.get("comment") or "",
            "description": existing.get("description") or loaded.get("description") or "",
        })

    result["tables"] = table_names
    result["selected_tables"] = table_names
    result["table_details"] = merged_details
    result["selected_table_details"] = merged_details
    return result


def _enrich_table_selection(
        selection: dict[str, Any],
        table_candidates: list[str],
) -> dict[str, Any]:
    result = dict(selection)
    tables = result.get("tables")
    if not isinstance(tables, list):
        tables = result.get("selected_tables")
    if not isinstance(tables, list):
        return result

    candidate_by_name = {
        str(name).strip().lower(): str(name).strip()
        for name in table_candidates
        if str(name).strip()
    }
    selected: list[str] = []
    for raw_table in tables:
        table_name = candidate_by_name.get(str(raw_table).strip().lower())
        if table_name and table_name not in selected:
            selected.append(table_name)

    result["tables"] = selected
    result["selected_tables"] = selected
    return result


def _extract_section(text: str, heading: str) -> str:
    pattern = re.compile(
        rf"\*\*{re.escape(heading)}\*\*\s*\n\n(.*?)(?=\n\n\*\*[^*\n]+\*\*|\Z)",
        re.DOTALL,
    )
    match = pattern.search(text or "")
    return match.group(1).strip() if match else ""


def _extract_context_sections(text: str) -> list[dict[str, Any]]:
    """将紧凑 Markdown 上下文解析为面向用户的分区。"""
    sections: list[dict[str, Any]] = []
    pattern = re.compile(
        r"\*\*([^*\n]+)\*\*\s*\n\n(.*?)(?=\n\n\*\*[^*\n]+\*\*|\Z)",
        re.DOTALL,
    )
    for match in pattern.finditer(text or ""):
        title = match.group(1).strip()
        body = match.group(2).strip()
        if not title or not body:
            continue
        items = [
            line.strip()[2:].strip()
            for line in body.splitlines()
            if line.strip().startswith("- ")
        ]
        section: dict[str, Any] = {
            "title": title,
            "items": items,
            "content": body,
        }
        if title.strip() == "SQL":
            samples = parse_sql_reference_samples(body)
            if samples:
                section["sql_samples"] = samples
                section["items"] = [
                    str(sample.get("name") or "").strip() or "未命名样例"
                    for sample in samples
                ]
        sections.append(section)
    return sections


def _schema_plan_from_schema_context(text: str) -> dict[str, Any] | None:
    table_section = _extract_section(text, "数据表")
    if not table_section:
        return None

    tables: list[str] = []
    details: list[dict[str, str]] = []
    current: dict[str, str] | None = None

    for line in table_section.splitlines():
        table_match = re.match(r"^-\s+\*\*([^*]+)\*\*\s*$", line.strip())
        if table_match:
            current = {
                "name": table_match.group(1).strip(),
                "comment": "",
                "description": "",
            }
            if current["name"]:
                tables.append(current["name"])
                details.append(current)
            continue
        if current is None:
            continue
        comment_match = re.match(r"^-\s+说明[：:]\s*(.*)$", line.strip())
        if comment_match:
            current["comment"] = comment_match.group(1).strip()
            continue
        desc_match = re.match(r"^-\s+摘要[：:]\s*(.*)$", line.strip())
        if desc_match:
            current["description"] = desc_match.group(1).strip()

    if not tables:
        return None

    return {
        "tables": tables,
        "table_details": details,
        "reason": _extract_section(text, "选表说明"),
    }


def _iter_event_text_chunks(event: Any) -> list[str]:
    """从 ADK 事件中收集用户可见文本。

    大模型节点通过 ``event.content.parts[].text`` 输出；其中 ``thought=True``
    是模型思考过程，不能发送给前端。工作流 ``FunctionNode`` 的结果（例如
    ``execute_sql``）则通过 ``event.output`` 输出。
    """
    chunks: list[str] = []
    content_text = ""
    content = getattr(event, "content", None)
    if content is not None:
        parts = getattr(content, "parts", None)
        if parts:
            content_text = "".join(
                str(text)
                for part in parts
                if not getattr(part, "thought", False)
                and (text := getattr(part, "text", None))
            )
            if content_text:
                chunks.append(content_text)

    output = getattr(event, "output", None)
    output_text = ""
    if isinstance(output, str) and output:
        output_text = output
    elif isinstance(output, dict):
        output_text = json.dumps(output, ensure_ascii=False)
    else:
        structured = _output_to_dict(output)
        if structured is not None:
            output_text = json.dumps(structured, ensure_ascii=False)

    # LLM Agent 的最终事件可能同时把相同正文放在 content 和 output 中。
    if output_text and output_text != content_text:
        chunks.append(output_text)
    return chunks


def _is_sse_partial_text_event(event: Any) -> bool:
    """判断是否为 ADK SSE 的流式文本分片（event.partial 为 True）。"""
    if getattr(event, "partial", None) is not True:
        return False
    return bool(_iter_event_text_chunks(event))


def _coerce_event_output_dict(event: Any) -> dict[str, Any] | None:
    return _output_to_dict(getattr(event, "output", None))


def _skip_sql_generator_aggregated_event(event: Any, *, got_partial: bool) -> bool:
    """判断 ADK SSE 是否在分片后又输出了一份完整聚合文本。"""
    if not got_partial:
        return False
    return getattr(event, "partial", None) is not True


def _is_final_response_event(event: Any) -> bool:
    """判断事件是否为当前 Agent 的最终答案。"""
    checker = getattr(event, "is_final_response", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            return False
    return getattr(event, "partial", None) is not True


def _should_replace_sql_stream_with_final(stage: str, event: Any) -> bool:
    """SQL 生成阶段仅在最终答案到达时发布用户可见正文。"""
    if stage != "sql_generator":
        return False
    if not _is_final_response_event(event):
        return False
    return bool(_iter_event_text_chunks(event))


def _extract_sql_blocks(text: str) -> list[str]:
    return [
        block.strip()
        for block in re.findall(
            r"```(?:sql|mysql)\s*(.*?)```",
            text or "",
            flags=re.IGNORECASE | re.DOTALL,
        )
        if block.strip()
    ]


def _merge_stage_chunk(prev: str, chunk: str, *, incremental: bool = False) -> str:
    """合并 ADK 文本；增量事件必须逐字追加，避免吞掉重复字符。"""
    if not chunk:
        return prev
    if incremental:
        return prev + chunk
    if not prev:
        return chunk
    if chunk == prev:
        return prev
    if chunk.startswith(prev):
        return chunk
    if prev.startswith(chunk):
        return prev
    if prev.endswith(chunk):
        return prev
    overlap = min(len(prev), len(chunk))
    for size in range(overlap, 0, -1):
        if prev[-size:] == chunk[:size]:
            return prev + chunk[size:]
    # 运行时可能在流式 token 后，以不同排版重发同一份 SQL 响应。
    # 例如调整或新增标题；此时保留已流式输出的正文。
    chunk_blocks = _extract_sql_blocks(chunk)
    if chunk_blocks and all(block in prev for block in chunk_blocks):
        return prev
    return prev + chunk


def _split_visual_chunks(text: str, size: int = 24) -> list[str]:
    if len(text) <= size:
        return [text]
    return [text[i: i + size] for i in range(0, len(text), size)]


def _try_parse_json(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_json_objects_from_text(text: str) -> list[dict[str, Any]]:
    """从可能夹带说明文字的模型文本中提取有效 JSON 对象。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()

    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
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
    return objects


def _parse_table_selector_json(text: str) -> dict[str, Any] | None:
    parsed = _try_parse_json(text)
    if isinstance(parsed, dict):
        return parsed
    objects = _parse_json_objects_from_text(text)
    return objects[-1] if objects else None


def _split_markdown_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    if raw.endswith("|"):
        raw = raw[:-1]
    raw = raw[1:]
    return [cell.strip().replace("\\|", "|") for cell in raw.split("|")]


def _is_table_sep(line: str) -> bool:
    # 支持单列和多列表格分隔行，例如 ``| --- |`` 或 ``| --- | --- |``。
    return bool(re.match(r"^\|(?:(?:\s*:?-{3,}:?\s*)\|)+$", line.strip()))


def _extract_markdown_tables(text: str) -> list[dict[str, Any]]:
    lines = (text or "").splitlines()
    tables: list[dict[str, Any]] = []
    i = 0
    while i + 2 < len(lines):
        header_line = lines[i].strip()
        sep_line = lines[i + 1].strip()
        if not header_line.startswith("|") or not _is_table_sep(sep_line):
            i += 1
            continue

        columns = _split_markdown_row(header_line)
        rows: list[dict[str, str]] = []
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            values = _split_markdown_row(lines[j].strip())
            if len(values) == len(columns):
                rows.append({col: values[idx] for idx, col in enumerate(columns)})
            j += 1

        tables.append({"columns": columns, "rows": rows})
        i = j

    return tables


def _extract_chart_spec_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict) or parsed.get("kind") != "chart_spec":
        return None
    charts = parsed.get("charts")
    if charts is not None and not isinstance(charts, list):
        return None
    return parsed


def _extract_analysis_report_payload(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, dict) or parsed.get("kind") != "analysis_report":
        return None
    return parsed


def _extract_sql_result_payload(text: str) -> dict[str, Any]:
    tables = _extract_markdown_tables(text)
    message = ""
    if not tables:
        if "无数据" in (text or ""):
            message = "查询已执行，但结果为空。"
        elif "查询执行失败" in (text or "") or "查询超时" in (text or ""):
            message = "查询执行失败，请查看上方错误信息。"
        else:
            message = "本次查询未返回可表格化的数据。"
    return {
        "kind": "sql_result",
        "result_sets": [
            {
                "columns": table["columns"],
                "rows": table["rows"],
                "row_count": len(table["rows"]),
            }
            for table in tables
        ],
        "message": message,
    }


def _event_stage_name(event: Any) -> str:
    path = getattr(getattr(event, "node_info", None), "path", "") or ""
    if not path:
        return ""
    last = path.split("/")[-1]
    return last.split("@")[0].strip()


def _build_workflow_detail(active_stage: str, done_stages: set[str]) -> str:
    lines: list[str] = []
    for _, label, stages in _WORKFLOW_DISPLAY_GROUPS:
        stage_set = set(stages)
        if active_stage in stage_set:
            lines.append(f"→ {label}")
        elif stage_set & done_stages:
            lines.append(f"✓ {label}")
    return "\n".join(lines)


def _user_facing_runtime_error(exc: BaseException) -> str:
    if is_llm_quota_or_rate_limit_error(exc):
        return (
            "模型接口触发了限流保护，刚才的查询结果仍然可用。"
            "请稍等片刻后再发送「生成分析报告」，不必重新取数。"
        )
    return str(exc)


class ChatService:
    def __init__(self) -> None:
        self._copilot_runtime = AdkCopilotRuntime()

    def list_agents(self) -> list[AgentDescriptor]:
        description = "自然语言问数助手"
        if not self._copilot_runtime.available and self._copilot_runtime.unavailable_reason:
            description = f"{description}；当前不可用：{self._copilot_runtime.unavailable_reason}"
        return [
            AgentDescriptor(
                id=_COPILOT_AGENT_ID,
                name="自然语言问数助手",
                description=description,
                capabilities=["retrieval", "sql", "streaming", "sessions"],
            ),
        ]

    def has_agent(self, agent_id: str) -> bool:
        return agent_id == _COPILOT_AGENT_ID

    async def stream_sse(
            self,
            session_id: str,
            agent_id: str,
            content: str,
            *,
            user_id: str,
            workspace_id: str,
    ) -> AsyncIterator[str]:
        turn_id = new_turn_id()
        yield sse_line(StreamEvent(
            type=StreamEventType.TURN_START,
            session_id=session_id,
            turn_id=turn_id,
            agent_id=agent_id,
            user_message=content,
        ))

        part_id = f"{turn_id}-text"
        yield sse_line(StreamEvent(
            type=StreamEventType.PART_START,
            session_id=session_id,
            turn_id=turn_id,
            part={
                "kind": "text",
                "id": part_id,
                "text": "",
            },
        ))

        if agent_id != _COPILOT_AGENT_ID:
            yield sse_line(StreamEvent(
                type=StreamEventType.ERROR,
                session_id=session_id,
                turn_id=turn_id,
                message=f"未知 agent: {agent_id}",
                code="agent_not_found",
                recoverable=False,
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.TURN_END,
                session_id=session_id,
                turn_id=turn_id,
                status="error",
            ))
            return

        if not self._copilot_runtime.available:
            reason = self._copilot_runtime.unavailable_reason or "ADK 运行时不可用"
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=part_id,
                patch={"op": "text_append", "text": f"ADK 当前不可用：{reason}"},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=part_id,
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.TURN_END,
                session_id=session_id,
                turn_id=turn_id,
                status="ok",
            ))
            return

        emitted = False
        stage_texts: dict[str, str] = {}
        stage_sent_len: dict[str, int] = {}
        stage_attempts: dict[str, int] = {}
        done_stages: set[str] = set()
        active_stage = ""
        sql_result_emitted = False
        chart_spec_emitted = False
        report_spec_emitted = False
        text_stream_stage = ""
        text_stream_open = False
        text_stream_got_partial = False
        buffered_text_ready = False
        latest_validate_sql: dict[str, Any] | None = None
        latest_table_selection: dict[str, Any] | None = None
        latest_table_candidates: list[str] = []
        schema_plan_emitted = False
        schema_plan_part_id = f"{turn_id}-data-schema-plan"
        sql_diagnostic_part_id = f"{turn_id}-sql-diag"
        sql_diagnostic_open = False

        workflow_part_id = f"{turn_id}-workflow"
        yield sse_line(StreamEvent(
            type=StreamEventType.PART_START,
            session_id=session_id,
            turn_id=turn_id,
            part={
                "kind": "workflow",
                "id": workflow_part_id,
                "title": "Copilot 执行流程",
                "status": "running",
                "detail": "",
            },
        ))

        active_tool_part_id = ""
        active_tool_stage = ""
        active_tool_started_at: datetime | None = None

        async def emit_text_stream_stage() -> AsyncIterator[str]:
            nonlocal emitted
            if not text_stream_stage:
                return
            if text_stream_stage in _BUFFERED_TEXT_STAGES and not buffered_text_ready:
                return
            full = stage_texts.get(text_stream_stage, "")
            sent = stage_sent_len.get(text_stream_stage, 0)
            if len(full) <= sent:
                return
            delta = full[sent:]
            stage_sent_len[text_stream_stage] = len(full)
            for piece in _split_visual_chunks(delta):
                emitted = True
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_DELTA,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=part_id,
                    patch={"op": "text_append", "text": piece},
                ))
                await asyncio.sleep(0)

        async def emit_schema_plan_part(selection: dict[str, Any]) -> AsyncIterator[str]:
            nonlocal schema_plan_emitted
            if schema_plan_emitted:
                return
            schema_plan_emitted = True
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_START,
                session_id=session_id,
                turn_id=turn_id,
                part={
                    "kind": "data",
                    "id": schema_plan_part_id,
                    "schema": "generic",
                    "title": "选表结果",
                    "payload": _schema_plan_payload(selection),
                    "truncated": False,
                },
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=schema_plan_part_id,
            ))

        async def close_tool_part(stage: str, *, status: str = "ok"):
            nonlocal active_tool_part_id, active_tool_stage, active_tool_started_at
            nonlocal sql_result_emitted, chart_spec_emitted, report_spec_emitted
            nonlocal latest_validate_sql, sql_diagnostic_open, emitted
            nonlocal latest_table_selection, latest_table_candidates, schema_plan_emitted
            if not active_tool_part_id:
                return

            stage_text = stage_texts.get(stage, "").strip()
            parsed = (
                _parse_table_selector_json(stage_text)
                if stage == "table_selector"
                else _try_parse_json(stage_text)
            )
            output: dict[str, Any] = {"stage": stage, "status": status}
            if stage == "validate_sql" and isinstance(latest_validate_sql, dict):
                output["result"] = latest_validate_sql
                if latest_validate_sql.get("ok") is False:
                    output["status"] = "error"
            elif isinstance(parsed, dict):
                if stage == "search":
                    candidates = parsed.get("table_candidates")
                    latest_table_candidates = (
                        [str(item) for item in candidates if str(item).strip()]
                        if isinstance(candidates, list)
                        else []
                    )
                    retrieval_context = parsed.get("retrieval_context")
                    if isinstance(retrieval_context, str) and retrieval_context.strip():
                        sections = _extract_context_sections(retrieval_context)
                        output["sections"] = sections
                        output["section_count"] = len(sections)
                if stage == "table_selector":
                    parsed = _enrich_table_selection(parsed, latest_table_candidates)
                    parsed = await _enrich_table_selection_details(
                        parsed,
                        workspace_id=workspace_id,
                    )
                output["result"] = parsed
            elif stage_text and stage not in (
                *_TEXT_STREAM_STAGES,
                "validate_sql",
                "execute_sql",
                "parse_chart_spec",
                "chart_planner",
                "parse_report_spec",
                "report_planner",
            ):
                output["summary"] = stage_text[:600]
                if stage in {"search", "load_sql_schema_context"}:
                    sections = _extract_context_sections(stage_text)
                    output["sections"] = sections
                    output["section_count"] = len(sections)

            if stage == "format_validation_failure_response" and stage_text:
                emitted = True
                for piece in _split_visual_chunks(stage_text):
                    yield sse_line(StreamEvent(
                        type=StreamEventType.PART_DELTA,
                        session_id=session_id,
                        turn_id=turn_id,
                        part_id=part_id,
                        patch={"op": "text_append", "text": piece},
                    ))

            ended_at = datetime.now(timezone.utc)
            duration_ms = 0
            if active_tool_started_at is not None:
                duration_ms = max(
                    0,
                    int((ended_at - active_tool_started_at).total_seconds() * 1000),
                )
            yield_line = sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=active_tool_part_id,
                patch={"op": "field_set", "path": "phase", "value": "result"},
            ))
            yield yield_line
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=active_tool_part_id,
                patch={"op": "field_set", "path": "ended_at", "value": ended_at.isoformat()},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=active_tool_part_id,
                patch={"op": "field_set", "path": "duration_ms", "value": duration_ms},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=active_tool_part_id,
                patch={"op": "field_set", "path": "output", "value": output},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=active_tool_part_id,
            ))
            active_tool_part_id = ""
            active_tool_stage = ""
            active_tool_started_at = None

            if stage == "validate_sql" and isinstance(latest_validate_sql, dict):
                if latest_validate_sql.get("ok") is False:
                    errors = latest_validate_sql.get("errors")
                    error_list = (
                        [str(item) for item in errors if str(item).strip()]
                        if isinstance(errors, list)
                        else []
                    )
                    recovery_action = str(
                        latest_validate_sql.get("recovery_action") or "repair"
                    ).strip().lower()
                    attempt = latest_validate_sql.get("attempt")
                    retry_count = attempt if isinstance(attempt, int) else 0
                    checked_sql = latest_validate_sql.get("checked_sql")
                    diag_payload: dict[str, Any] = {
                        "kind": "sql_diagnostic",
                        "recovery_action": recovery_action,
                        "retry_count": retry_count,
                        "errors": error_list,
                        "resolved": False,
                    }
                    if isinstance(checked_sql, str) and checked_sql.strip():
                        diag_payload["checked_sql"] = checked_sql.strip()
                    if not sql_diagnostic_open:
                        sql_diagnostic_open = True
                        yield sse_line(StreamEvent(
                            type=StreamEventType.PART_START,
                            session_id=session_id,
                            turn_id=turn_id,
                            part={
                                "kind": "data",
                                "id": sql_diagnostic_part_id,
                                "schema": "generic",
                                "title": "",
                                "payload": diag_payload,
                                "truncated": False,
                            },
                        ))
                        yield sse_line(StreamEvent(
                            type=StreamEventType.PART_END,
                            session_id=session_id,
                            turn_id=turn_id,
                            part_id=sql_diagnostic_part_id,
                        ))
                    else:
                        yield sse_line(StreamEvent(
                            type=StreamEventType.PART_DELTA,
                            session_id=session_id,
                            turn_id=turn_id,
                            part_id=sql_diagnostic_part_id,
                            patch={"op": "payload_merge", "payload": diag_payload},
                        ))
                elif latest_validate_sql.get("ok") is True and sql_diagnostic_open:
                    yield sse_line(StreamEvent(
                        type=StreamEventType.PART_DELTA,
                        session_id=session_id,
                        turn_id=turn_id,
                        part_id=sql_diagnostic_part_id,
                        patch={
                            "op": "payload_merge",
                            "payload": {"resolved": True},
                        },
                    ))

            if stage == "table_selector" and not schema_plan_emitted:
                payload = output.get("result")
                if not _is_table_selection_result(payload):
                    payload = latest_table_selection
                if _is_table_selection_result(payload):
                    if isinstance(payload, dict):
                        payload = await _enrich_table_selection_details(
                            payload,
                            workspace_id=workspace_id,
                        )
                    async for line in emit_schema_plan_part(payload):
                        yield line

            if stage == "load_sql_schema_context" and not schema_plan_emitted:
                payload = _schema_plan_from_schema_context(stage_text)
                if _is_table_selection_result(payload):
                    async for line in emit_schema_plan_part(payload):
                        yield line

            if stage == "execute_sql" and not sql_result_emitted:
                payload = _extract_sql_result_payload(stage_text)
                data_id = f"{turn_id}-data-sql-result"
                sql_result_emitted = True
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_START,
                    session_id=session_id,
                    turn_id=turn_id,
                    part={
                        "kind": "data",
                        "id": data_id,
                        "schema": "generic",
                        "title": "",
                        "payload": payload,
                        "truncated": False,
                    },
                ))
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_END,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=data_id,
                ))

            if stage == "parse_chart_spec" and not chart_spec_emitted:
                payload = _extract_chart_spec_payload(stage_text)
                if payload is None:
                    payload = {
                        "kind": "chart_spec",
                        "feasible": False,
                        "summary": "未能生成可展示的图表，请换个维度再试。",
                        "charts": [],
                    }
                data_id = f"{turn_id}-data-chart-spec"
                chart_spec_emitted = True
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_START,
                    session_id=session_id,
                    turn_id=turn_id,
                    part={
                        "kind": "data",
                        "id": data_id,
                        "schema": "generic",
                        "title": "",
                        "payload": payload,
                        "truncated": False,
                    },
                ))
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_END,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=data_id,
                ))

            if stage == "parse_report_spec" and not report_spec_emitted:
                payload = _extract_analysis_report_payload(stage_text)
                if payload is None:
                    payload = {
                        "kind": "analysis_report",
                        "feasible": False,
                        "summary": "未能生成分析报告，请换个分析维度再试。",
                        "title": "",
                        "overview": "",
                        "conclusions": "",
                        "analyses": [],
                        "file_id": "",
                        "filename": "",
                    }
                data_id = f"{turn_id}-data-analysis-report"
                report_spec_emitted = True
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_START,
                    session_id=session_id,
                    turn_id=turn_id,
                    part={
                        "kind": "data",
                        "id": data_id,
                        "schema": "generic",
                        "title": "",
                        "payload": payload,
                        "truncated": False,
                    },
                ))
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_END,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=data_id,
                ))

        try:
            async for event in self._copilot_runtime.stream(
                user_id=user_id,
                session_id=session_id,
                message=content,
                workspace_id=workspace_id,
            ):
                stage = _event_stage_name(event)
                if stage and stage in _STAGE_ORDER and stage != active_stage:
                    if text_stream_open and active_stage in _TEXT_STREAM_STAGES:
                        async for line in emit_text_stream_stage():
                            yield line
                        text_stream_open = False
                        text_stream_stage = ""

                    if active_tool_part_id and active_tool_stage:
                        async for line in close_tool_part(active_tool_stage):
                            yield line
                        if active_stage:
                            done_stages.add(active_stage)

                    if stage in done_stages:
                        stage_attempts[stage] = stage_attempts.get(stage, 0) + 1
                        stage_texts[stage] = ""
                        stage_sent_len[stage] = 0
                        if stage in _TEXT_STREAM_STAGES:
                            yield sse_line(StreamEvent(
                                type=StreamEventType.PART_DELTA,
                                session_id=session_id,
                                turn_id=turn_id,
                                part_id=part_id,
                                patch={"op": "field_set", "path": "text", "value": ""},
                            ))

                    active_stage = stage
                    if stage == "validate_sql":
                        latest_validate_sql = None
                    if stage in _TEXT_STREAM_STAGES:
                        text_stream_stage = stage
                        text_stream_open = True
                        text_stream_got_partial = False
                        buffered_text_ready = False
                    workflow_detail = _build_workflow_detail(active_stage, done_stages)
                    yield sse_line(StreamEvent(
                        type=StreamEventType.PART_DELTA,
                        session_id=session_id,
                        turn_id=turn_id,
                        part_id=workflow_part_id,
                        patch={"op": "field_set", "path": "detail", "value": workflow_detail},
                    ))

                    active_tool_stage = stage
                    attempt = stage_attempts.get(stage, 0)
                    tool_suffix = f"-r{attempt}" if attempt > 0 else ""
                    active_tool_part_id = f"{turn_id}-tool-{stage}{tool_suffix}"
                    active_tool_started_at = datetime.now(timezone.utc)
                    yield sse_line(StreamEvent(
                        type=StreamEventType.PART_START,
                        session_id=session_id,
                        turn_id=turn_id,
                        part={
                            "kind": "tool",
                            "id": active_tool_part_id,
                            "name": stage,
                            "phase": "call",
                            "title": _STAGE_LABELS.get(stage, stage),
                            "input": {"workspace_id": workspace_id, "session_id": session_id},
                            "started_at": active_tool_started_at.isoformat(),
                        },
                    ))

                structured_output = _coerce_event_output_dict(event)
                if stage == "validate_sql" and structured_output is not None:
                    latest_validate_sql = structured_output

                if stage == "table_selector":
                    selection = _table_selection_from_event(
                        event,
                        stage_text=stage_texts.get("table_selector", ""),
                    )
                    if selection is not None:
                        latest_table_selection = selection

                replace_sql_stream_with_final = (
                    stage in _TEXT_STREAM_STAGES
                    and text_stream_open
                    and active_stage == stage
                    and _should_replace_sql_stream_with_final(
                        stage,
                        event,
                    )
                )

                if replace_sql_stream_with_final:
                    stage_texts[stage] = ""
                    stage_sent_len[stage] = 0
                    buffered_text_ready = True
                    yield sse_line(StreamEvent(
                        type=StreamEventType.PART_DELTA,
                        session_id=session_id,
                        turn_id=turn_id,
                        part_id=part_id,
                        patch={"op": "field_set", "path": "text", "value": ""},
                    ))

                skip_text_aggregated = (
                    stage in _TEXT_STREAM_STAGES
                    and text_stream_open
                    and active_stage == stage
                    and not replace_sql_stream_with_final
                    and _skip_sql_generator_aggregated_event(
                        event,
                        got_partial=text_stream_got_partial,
                    )
                )

                if not skip_text_aggregated:
                    for chunk in _iter_event_text_chunks(event):
                        if not stage:
                            continue
                        if stage in _TEXT_STREAM_STAGES and not text_stream_open:
                            continue
                        if (
                            stage in _TEXT_STREAM_STAGES
                            and text_stream_open
                            and active_stage == stage
                            and _is_sse_partial_text_event(event)
                        ):
                            text_stream_got_partial = True
                        stage_texts[stage] = _merge_stage_chunk(
                            stage_texts.get(stage, ""),
                            chunk,
                            incremental=_is_sse_partial_text_event(event),
                        )
                        if (
                            stage in _TEXT_STREAM_STAGES
                            and text_stream_open
                            and active_stage == stage
                        ):
                            async for line in emit_text_stream_stage():
                                yield line

                if getattr(event, "turn_complete", False):
                    break

            if text_stream_open and active_stage in _TEXT_STREAM_STAGES:
                async for line in emit_text_stream_stage():
                    yield line

            if active_tool_part_id and active_tool_stage:
                async for line in close_tool_part(active_tool_stage):
                    yield line
                done_stages.add(active_tool_stage)

            if not emitted and not sql_result_emitted and not chart_spec_emitted and not report_spec_emitted:
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_DELTA,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=part_id,
                    patch={"op": "text_append", "text": "本次未返回可展示文本，请重试或补充问题。"},
                ))

            if latest_table_selection is not None and not schema_plan_emitted:
                latest_table_selection = await _enrich_table_selection_details(
                    latest_table_selection,
                    workspace_id=workspace_id,
                )
                async for line in emit_schema_plan_part(latest_table_selection):
                    yield line

            workflow_detail = _build_workflow_detail("", done_stages)
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=workflow_part_id,
                patch={"op": "field_set", "path": "detail", "value": workflow_detail},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=workflow_part_id,
                patch={"op": "field_set", "path": "status", "value": "done"},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=workflow_part_id,
            ))

            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=part_id,
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.TURN_END,
                session_id=session_id,
                turn_id=turn_id,
                status="ok",
            ))
        except Exception as exc:
            yield sse_line(StreamEvent(
                type=StreamEventType.ERROR,
                session_id=session_id,
                turn_id=turn_id,
                message=_user_facing_runtime_error(exc),
                code="copilot_runtime_error",
                recoverable=True,
            ))
            if active_tool_part_id and active_tool_stage:
                async for line in close_tool_part(active_tool_stage, status="error"):
                    yield line
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_DELTA,
                session_id=session_id,
                turn_id=turn_id,
                part_id=workflow_part_id,
                patch={"op": "field_set", "path": "status", "value": "error"},
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=workflow_part_id,
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.PART_END,
                session_id=session_id,
                turn_id=turn_id,
                part_id=part_id,
            ))
            yield sse_line(StreamEvent(
                type=StreamEventType.TURN_END,
                session_id=session_id,
                turn_id=turn_id,
                status="error",
            ))


chat_service = ChatService()
