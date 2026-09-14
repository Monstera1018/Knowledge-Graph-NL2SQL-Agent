import unittest

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot.helpers import (
    coerce_extractor_result,
    extraction_from_node_input,
    extractor_result_has_content,
    save_extractor_result,
)


class _Ctx:
    def __init__(self) -> None:
        self.state = {}


class ExtractorResultCoerceTest(unittest.TestCase):
    def test_function_node_dict_dump_is_restored(self):
        dumped = RetrievalExtractionResult(
            retrieval_query="金华地区结清用户清单",
            keywords=["金华", "结清"],
            intent="查询结清用户",
            task="query",
        ).model_dump()
        parsed = coerce_extractor_result(dumped)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.retrieval_query, "金华地区结清用户清单")
        self.assertEqual(parsed.keywords, ["金华", "结清"])

    def test_empty_dict_does_not_wipe_stored_result(self):
        ctx = _Ctx()
        save_extractor_result(
            ctx,
            RetrievalExtractionResult(
                retrieval_query="金华地区结清用户清单",
                keywords=["金华"],
                intent="查询结清用户",
                task="query",
            ),
        )
        extracted = extraction_from_node_input(ctx, {"foo": "bar"})
        self.assertEqual(extracted.retrieval_query, "金华地区结清用户清单")

    def test_planner_markdown_falls_back_to_state(self):
        ctx = _Ctx()
        save_extractor_result(
            ctx,
            RetrievalExtractionResult(
                retrieval_query="金华地区结清用户清单",
                keywords=["金华"],
                intent="查询结清用户",
                task="query",
            ),
        )
        extracted = extraction_from_node_input(
            ctx,
            "## 用户最新输入\n\n金华地区结清的用户清单",
        )
        self.assertEqual(extracted.retrieval_query, "金华地区结清用户清单")

    def test_empty_extraction_is_detected(self):
        self.assertFalse(
            extractor_result_has_content(
                RetrievalExtractionResult(
                    retrieval_query="",
                    keywords=[],
                    intent="",
                    task="",
                )
            )
        )
        self.assertTrue(
            extractor_result_has_content(
                RetrievalExtractionResult(
                    retrieval_query="金华地区结清用户清单",
                    keywords=[],
                    intent="",
                    task="query",
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
