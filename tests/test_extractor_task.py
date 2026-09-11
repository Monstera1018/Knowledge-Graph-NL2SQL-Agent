import unittest

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot.helpers import (
    EXTRACTOR_TASK_CHART,
    EXTRACTOR_TASK_CHITCHAT,
    EXTRACTOR_TASK_QUERY,
    looks_like_chart_request,
    resolve_extractor_task,
)


def _extraction(**kwargs) -> RetrievalExtractionResult:
    payload = {
        "retrieval_query": "金华地区电费未结清用户清单",
        "keywords": ["金华", "未结清"],
        "intent": "查询未结清用户",
        "task": "",
    }
    payload.update(kwargs)
    return RetrievalExtractionResult(**payload)


class ExtractorTaskTest(unittest.TestCase):
    def test_plain_query_stays_query(self):
        self.assertEqual(
            resolve_extractor_task(_extraction(), "金华地区电费未结清用户清单"),
            EXTRACTOR_TASK_QUERY,
        )

    def test_explicit_query_is_not_chart(self):
        self.assertEqual(
            resolve_extractor_task(_extraction(task="query"), "全省高压用户统计"),
            EXTRACTOR_TASK_QUERY,
        )

    def test_trend_followup_is_chart(self):
        self.assertEqual(
            resolve_extractor_task(_extraction(task=""), "看一下趋势"),
            EXTRACTOR_TASK_CHART,
        )

    def test_combined_query_and_chart_stays_query(self):
        message = "帮我查询各地区用户数并画柱状图"
        self.assertFalse(looks_like_chart_request(message))
        self.assertEqual(
            resolve_extractor_task(_extraction(task="chart"), message),
            EXTRACTOR_TASK_QUERY,
        )

    def test_chitchat_from_intent(self):
        self.assertEqual(
            resolve_extractor_task(
                _extraction(
                    retrieval_query="",
                    keywords=[],
                    intent="非数据查询",
                    task="",
                ),
                "你好",
            ),
            EXTRACTOR_TASK_CHITCHAT,
        )


if __name__ == "__main__":
    unittest.main()
