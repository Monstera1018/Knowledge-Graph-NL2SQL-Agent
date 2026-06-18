from __future__ import annotations

import os
import asyncio
import json
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from server.chat.protocol import (
    AgentDescriptor,
    StreamEvent,
    StreamEventType,
    new_turn_id,
    sse_line,
)

_COPILOT_AGENT_ID = "copilot"
_COPILOT_APP_NAME = "TYWS-Copilot"
_COPILOT_WORKSPACE_STATE_KEY = "copilot_workspace_id"

_STAGE_ORDER = [
    "extractor",
    "chitchat",
    "search",
    "table_selector",
    "load_sql_schema_context",
    "sql_generator",
    "validate_sql",
    "format_validation_failure_response",
    "execute_sql",
]
_STAGE_LABELS = {
    "extractor": "🧭 解析问题",
    "chitchat": "💬 闲聊回复",
    "search": "🔎 检索上下文",
    "table_selector": "🗂️ 选择数据表",
    "load_sql_schema_context": "🧱 汇总表结构",
    "sql_generator": "🧠 生成 SQL",
    "validate_sql": "✅ 校验 SQL",
    "format_validation_failure_response": "⚠️ SQL 修复失败",
    "execute_sql": "🚀 执行 SQL",
}
_TEXT_STREAM_STAGES = frozenset({"sql_generator", "chitchat"})


class AdkCopilotRuntime:
    """ADK runner wrapper for the Copilot workflow."""

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
        """Persist workspace_id into ADK session state.

        ADK Workflow nodes run via ``_run_node_async`` and do not apply
        ``run_async(state_delta=...)`` today, so agents read the default
        workspace unless we write session state explicitly.
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
    parsed = _try_parse_json(stage_text)
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


def _enrich_table_selection(
        selection: dict[str, Any],
        table_candidates: list[str],
) -> dict[str, Any]:
    result = dict(selection)
    tables = result.get("tables")
    if not isinstance(tables, list):
        tables = result.get("selected_tables")
    if not isinstance(tables, list) or not tables:
        selected: list[str] = []
        raw_ids = result.get("table_ids")
        if isinstance(raw_ids, list):
            for raw_id in raw_ids:
                match = re.match(r"^T(\d+)$", str(raw_id).strip(), re.IGNORECASE)
                if not match:
                    continue
                index = int(match.group(1)) - 1
                if 0 <= index < len(table_candidates):
                    table_name = table_candidates[index]
                    if table_name and table_name not in selected:
                        selected.append(table_name)
        if selected:
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
    """Parse compact Markdown context into user-facing sections."""
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
        sections.append({
            "title": title,
            "items": items,
            "content": body,
        })
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
    """Collect user-visible text from an ADK event.

    LLM nodes stream via ``event.content.parts[].text``; workflow
    ``FunctionNode`` results (e.g. ``execute_sql``) are emitted on
    ``event.output`` instead.
    """
    chunks: list[str] = []
    content = getattr(event, "content", None)
    if content is not None:
        parts = getattr(content, "parts", None)
        if parts:
            for part in parts:
                text = getattr(part, "text", None)
                if text:
                    chunks.append(str(text))
    output = getattr(event, "output", None)
    if isinstance(output, str) and output:
        chunks.append(output)
    elif isinstance(output, dict):
        chunks.append(json.dumps(output, ensure_ascii=False))
    else:
        structured = _output_to_dict(output)
        if structured is not None:
            chunks.append(json.dumps(structured, ensure_ascii=False))
    return chunks


def _is_sse_partial_text_event(event: Any) -> bool:
    """True for ADK SSE streaming text chunks (event.partial is True)."""
    if getattr(event, "partial", None) is not True:
        return False
    return bool(_iter_event_text_chunks(event))


def _coerce_event_output_dict(event: Any) -> dict[str, Any] | None:
    return _output_to_dict(getattr(event, "output", None))


def _skip_sql_generator_aggregated_event(event: Any, *, got_partial: bool) -> bool:
    """ADK SSE yields partial chunks and a final aggregated copy of the same text."""
    if not got_partial:
        return False
    return getattr(event, "partial", None) is not True


def _should_replace_sql_stream_with_final(stage: str, event: Any, *, got_partial: bool) -> bool:
    """Prefer the final SQL response over earlier streamed draft tokens."""
    if stage != "sql_generator" or not got_partial:
        return False
    if getattr(event, "partial", None) is True:
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


def _merge_stage_chunk(prev: str, chunk: str) -> str:
    """Merge incremental or cumulative ADK chunks without duplication."""
    if not chunk:
        return prev
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
    # ADK may resend the same SQL response in a different layout after streaming
    # tokens (e.g. headings reordered or added). Keep the streamed body.
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


def _split_markdown_row(line: str) -> list[str]:
    raw = line.strip()
    if not raw.startswith("|"):
        return []
    if raw.endswith("|"):
        raw = raw[:-1]
    raw = raw[1:]
    return [cell.strip().replace("\\|", "|") for cell in raw.split("|")]


def _is_table_sep(line: str) -> bool:
    # Support single- and multi-column separators, e.g. ``| --- |`` or ``| --- | --- |``.
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
    for stage in _STAGE_ORDER:
        label = _STAGE_LABELS.get(stage, stage)
        if stage in done_stages:
            lines.append(f"✓ {label}")
        elif stage == active_stage:
            lines.append(f"→ {label}")
    return "\n".join(lines)


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
                name="TYWS",
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
        text_stream_stage = ""
        text_stream_open = False
        text_stream_got_partial = False
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

        async def emit_text_stream_stage() -> AsyncIterator[str]:
            nonlocal emitted
            if not text_stream_stage:
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
            nonlocal active_tool_part_id, active_tool_stage, sql_result_emitted
            nonlocal latest_validate_sql, sql_diagnostic_open, emitted
            nonlocal latest_table_selection, latest_table_candidates, schema_plan_emitted
            if not active_tool_part_id:
                return

            stage_text = stage_texts.get(stage, "").strip()
            parsed = _try_parse_json(stage_text)
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
                output["result"] = parsed
            elif stage_text and stage not in (*_TEXT_STREAM_STAGES, "validate_sql", "execute_sql"):
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
                        got_partial=text_stream_got_partial,
                    )
                )

                if replace_sql_stream_with_final:
                    stage_texts[stage] = ""
                    stage_sent_len[stage] = 0
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

            if not emitted and not sql_result_emitted:
                yield sse_line(StreamEvent(
                    type=StreamEventType.PART_DELTA,
                    session_id=session_id,
                    turn_id=turn_id,
                    part_id=part_id,
                    patch={"op": "text_append", "text": "本次未返回可展示文本，请重试或补充问题。"},
                ))

            if active_tool_part_id and active_tool_stage:
                async for line in close_tool_part(active_tool_stage):
                    yield line
                done_stages.add(active_tool_stage)

            if latest_table_selection is not None and not schema_plan_emitted:
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
                message=str(exc),
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
