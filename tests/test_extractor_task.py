import unittest

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot.helpers import (
    EXTRACTOR_TASK_CHART,
    EXTRACTOR_TASK_CHITCHAT,
    EXTRACTOR_TASK_QUERY,
    EXTRACTOR_TASK_REPORT,
    looks_like_chart_request,
    resolve_extractor_task,
    user_requests_chart,
    user_requests_report,
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

    def test_generate_analysis_chart_is_chart_intent(self):
        message = "帮我查询金华地区各县区用户占比，并生成分析图表"
        self.assertTrue(user_requests_chart(message))
        self.assertFalse(looks_like_chart_request(message))
        self.assertEqual(
            resolve_extractor_task(_extraction(task="query"), message),
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

    def test_display_change_is_chart(self):
        self.assertTrue(looks_like_chart_request("可以换一个展示形式，并且色调改成橙黄色吗"))
        self.assertEqual(
            resolve_extractor_task(_extraction(task=""), "可以换一个展示形式，并且色调改成橙黄色吗"),
            EXTRACTOR_TASK_CHART,
        )

    def test_combined_query_and_report_stays_query(self):
        message = "生成金华地区未结清用户清单的分析报告"
        self.assertTrue(user_requests_report(message))
        self.assertEqual(
            resolve_extractor_task(_extraction(task="report"), message),
            EXTRACTOR_TASK_QUERY,
        )

    def test_report_followup_without_new_query(self):
        message = "基于刚才的结果生成分析报告"
        self.assertTrue(user_requests_report(message))
        self.assertEqual(
            resolve_extractor_task(_extraction(task=""), message),
            EXTRACTOR_TASK_REPORT,
        )

    def test_short_report_followup_overrides_extractor_query(self):
        self.assertTrue(user_requests_report("报告呢"))
        self.assertEqual(
            resolve_extractor_task(_extraction(task="query"), "报告呢"),
            EXTRACTOR_TASK_REPORT,
        )
        self.assertEqual(
            resolve_extractor_task(_extraction(task="query"), "帮我根据金华地区未结清的用户数据生成分析报告"),
            EXTRACTOR_TASK_QUERY,
        )


if __name__ == "__main__":
    unittest.main()
