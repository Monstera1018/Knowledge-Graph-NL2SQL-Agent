import json
import unittest
from types import SimpleNamespace

from server.chat.service import (
    ChatService,
    _iter_event_text_chunks,
    _merge_stage_chunk,
    _should_replace_sql_stream_with_final,
)


def _part(text: str, *, thought: bool = False) -> SimpleNamespace:
    return SimpleNamespace(text=text, thought=thought)


class _Event(SimpleNamespace):
    def is_final_response(self) -> bool:
        return bool(getattr(self, "final", False))


class _FakeRuntime:
    available = True
    unavailable_reason = None

    async def stream(self, **_kwargs):
        yield _Event(
            node_info=SimpleNamespace(path="sql_generator"),
            content=SimpleNamespace(parts=[_part("The lookup\n", thought=True)]),
            output=None,
            partial=True,
            turn_complete=False,
            final=False,
        )
        yield _Event(
            node_info=SimpleNamespace(path="sql_generator"),
            content=SimpleNamespace(
                parts=[
                    _part("Let\nme\nthink\n", thought=True),
                    _part("查询说明。\n\n```sql\nSELECT 1000 AS \"数量\";\n```"),
                ]
            ),
            output=None,
            partial=False,
            turn_complete=True,
            final=True,
        )


class _FakeReportRuntime:
    available = True
    unavailable_reason = None

    async def stream(self, **_kwargs):
        payload = {
            "kind": "analysis_report",
            "feasible": True,
            "summary": "按区县看未结清用户。",
            "title": "金华地区未结清用户数据分析报告",
            "overview": "共 32 户未结清用户。",
            "conclusions": "义乌更为集中。",
            "analyses": [],
            "file_id": "a" * 32,
            "filename": "金华地区未结清用户数据分析报告.docx",
        }
        yield _Event(
            node_info=SimpleNamespace(path="parse_report_spec"),
            content=SimpleNamespace(parts=[_part(json.dumps(payload, ensure_ascii=False))]),
            output=json.dumps(payload, ensure_ascii=False),
            partial=False,
            turn_complete=True,
            final=True,
        )


class _FakeChartRuntime:
    available = True
    unavailable_reason = None

    async def stream(self, **_kwargs):
        payload = {
            "kind": "chart_spec",
            "feasible": True,
            "summary": "按地区统计",
            "charts": [
                {
                    "title": "各地区用户数",
                    "type": "pie",
                    "categories": ["金华", "宁波"],
                    "series": [{"name": "用户数", "values": [2, 1]}],
                    "palette": ["#ea580c", "#f59e0b"],
                }
            ],
        }
        yield _Event(
            node_info=SimpleNamespace(path="parse_chart_spec"),
            content=SimpleNamespace(parts=[_part(json.dumps(payload, ensure_ascii=False))]),
            output=json.dumps(payload, ensure_ascii=False),
            partial=False,
            turn_complete=True,
            final=True,
        )


class ChatServiceTextTest(unittest.IsolatedAsyncioTestCase):
    def test_event_text_filters_thought_and_deduplicates_output(self):
        event = _Event(
            content=SimpleNamespace(
                parts=[_part("英文思考", thought=True), _part("中文答案")]
            ),
            output="中文答案",
        )

        self.assertEqual(_iter_event_text_chunks(event), ["中文答案"])

    def test_incremental_merge_preserves_repeated_characters_and_newlines(self):
        text = ""
        for chunk in ["1", "0", "0", "0", "\n", "\n", "结", "果"]:
            text = _merge_stage_chunk(text, chunk, incremental=True)

        self.assertEqual(text, "1000\n\n结果")

    def test_only_final_sql_event_can_replace_visible_text(self):
        partial = _Event(
            content=SimpleNamespace(parts=[_part("草稿")]),
            output=None,
            partial=True,
            final=False,
        )
        final = _Event(
            content=SimpleNamespace(parts=[_part("最终答案")]),
            output=None,
            partial=False,
            final=True,
        )

        self.assertFalse(_should_replace_sql_stream_with_final("sql_generator", partial))
        self.assertTrue(_should_replace_sql_stream_with_final("sql_generator", final))

    async def test_sql_generator_sends_only_non_thought_final_answer(self):
        service = ChatService()
        service._copilot_runtime = _FakeRuntime()

        events = []
        async for line in service.stream_sse(
            "session-test",
            "copilot",
            "测试问题",
            user_id="user-test",
            workspace_id="default",
        ):
            events.append(json.loads(line.removeprefix("data: ").strip()))

        main_part_id = next(
            event["part"]["id"]
            for event in events
            if event["type"] == "part_start"
            and event.get("part", {}).get("kind") == "text"
        )
        visible_text = ""
        for event in events:
            if event.get("part_id") != main_part_id or event["type"] != "part_delta":
                continue
            patch = event.get("patch", {})
            if patch.get("op") == "field_set" and patch.get("path") == "text":
                visible_text = str(patch.get("value") or "")
            elif patch.get("op") == "text_append":
                visible_text += str(patch.get("text") or "")

        self.assertEqual(
            visible_text,
            "查询说明。\n\n```sql\nSELECT 1000 AS \"数量\";\n```",
        )
        self.assertNotIn("The lookup", visible_text)
        self.assertNotIn("Let\nme", visible_text)

    async def test_chart_only_turn_does_not_emit_empty_text(self):
        service = ChatService()
        service._copilot_runtime = _FakeChartRuntime()

        events = []
        async for line in service.stream_sse(
            "session-chart",
            "copilot",
            "换成饼图",
            user_id="user-test",
            workspace_id="default",
        ):
            events.append(json.loads(line.removeprefix("data: ").strip()))

        visible_text = ""
        chart_payloads = []
        for event in events:
            if event["type"] == "part_start" and event.get("part", {}).get("kind") == "data":
                payload = event["part"].get("payload") or {}
                if payload.get("kind") == "chart_spec":
                    chart_payloads.append(payload)
            if event["type"] != "part_delta":
                continue
            patch = event.get("patch", {})
            if patch.get("op") == "text_append":
                visible_text += str(patch.get("text") or "")

        self.assertEqual(len(chart_payloads), 1)
        self.assertNotIn("本次未返回可展示文本", visible_text)

    async def test_report_only_turn_emits_analysis_report_payload(self):
        service = ChatService()
        service._copilot_runtime = _FakeReportRuntime()

        events = []
        async for line in service.stream_sse(
            "session-report",
            "copilot",
            "生成分析报告",
            user_id="user-test",
            workspace_id="default",
        ):
            events.append(json.loads(line.removeprefix("data: ").strip()))

        visible_text = ""
        report_payloads = []
        for event in events:
            if event["type"] == "part_start" and event.get("part", {}).get("kind") == "data":
                payload = event["part"].get("payload") or {}
                if payload.get("kind") == "analysis_report":
                    report_payloads.append(payload)
            if event["type"] != "part_delta":
                continue
            patch = event.get("patch", {})
            if patch.get("op") == "text_append":
                visible_text += str(patch.get("text") or "")

        self.assertEqual(len(report_payloads), 1)
        self.assertTrue(report_payloads[0].get("file_id"))
        self.assertNotIn("本次未返回可展示文本", visible_text)


if __name__ == "__main__":
    unittest.main()
