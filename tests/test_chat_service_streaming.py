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


if __name__ == "__main__":
    unittest.main()
