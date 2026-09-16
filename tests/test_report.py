import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot import report as report_mod
from adk_agents.copilot.plan import (
    OBS_QUERY_SUCCESS,
    PLAN_STEP_QUERY,
    PLAN_STEP_REPORT,
    PLAN_STEP_STOP,
    normalize_plan,
)
from adk_agents.copilot.report import materialize_analysis_report, write_report_docx


def _jinhua_snapshot() -> dict:
    rows = []
    counties = [
        ("金华本部", 8, 1200),
        ("义乌", 12, 3600),
        ("兰溪", 5, 800),
        ("浦江", 7, 1500),
    ]
    user_id = 1
    for county, count, amount in counties:
        per = round(amount / count, 2)
        for _ in range(count):
            rows.append({
                "用户编号": str(user_id),
                "用户名称": f"用户{user_id}",
                "所属区县": county,
                "结清标志": "未结清",
                "电费金额": str(per),
            })
            user_id += 1
    return {
        "user_question": "金华地区未结清的用户清单",
        "selected_tables": ["dws_cst_cbjsfxb_rhb_xs"],
        "result_sets": [{
            "columns": ["用户编号", "用户名称", "所属区县", "结清标志", "电费金额"],
            "rows": rows,
            "row_count": len(rows),
            "truncated": False,
        }],
        "row_count": len(rows),
    }


def _report_plan() -> str:
    return json.dumps(
        {
            "feasible": True,
            "summary": "按区县看未结清用户数和电费金额。",
            "title": "金华地区未结清用户数据分析报告",
            "overview": "本报告基于金华地区未结清用户清单。共识别 32 户未结清用户，涉及金华本部、义乌、兰溪、浦江四个区县。",
            "analyses": [
                {
                    "heading": "按区县用户数",
                    "narrative": "义乌未结清用户最多，共 12 户；兰溪最少，为 5 户。",
                    "chart": {
                        "title": "各区县未结清用户数",
                        "type": "bar",
                        "x": {"column": "所属区县", "role": "dimension"},
                        "y": [{"column": "用户编号", "aggregation": "count", "label": "用户数"}],
                    },
                },
                {
                    "heading": "按区县金额",
                    "narrative": "义乌未结清电费金额最高，是当前需要优先清退的区域。",
                    "chart": {
                        "title": "各区县未结清电费金额",
                        "type": "bar",
                        "x": {"column": "所属区县", "role": "dimension"},
                        "y": [{"column": "电费金额", "aggregation": "sum", "label": "电费金额"}],
                    },
                },
            ],
            "conclusions": "1、未结清问题在义乌更为集中。\n2、金华本部和浦江也有一定规模，应纳入常规清退跟踪。",
        },
        ensure_ascii=False,
    )


class ReportPlanTest(unittest.TestCase):
    def test_query_success_then_report_when_user_wants_report(self):
        first = normalize_plan(
            next_step="query",
            extraction=RetrievalExtractionResult(
                retrieval_query="金华地区未结清用户清单",
                keywords=["金华", "未结清"],
                intent="查询未结清用户",
                task="query",
            ),
            user_message="生成金华地区未结清用户清单的分析报告",
            has_snapshot=False,
        )
        self.assertEqual(first["next"], PLAN_STEP_QUERY)
        plan = normalize_plan(
            next_step="stop",
            extraction=RetrievalExtractionResult(
                retrieval_query="金华地区未结清用户清单",
                keywords=["金华", "未结清"],
                intent="查询未结清用户",
                task="query",
            ),
            user_message="生成金华地区未结清用户清单的分析报告",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_REPORT)
        self.assertEqual(plan["steps"], [PLAN_STEP_QUERY, PLAN_STEP_REPORT])

    def test_short_report_followup_uses_snapshot(self):
        plan = normalize_plan(
            next_step="query",
            extraction=RetrievalExtractionResult(
                retrieval_query="金华地区未结清用户清单",
                keywords=["金华", "未结清"],
                intent="查询未结清用户",
                task="query",
            ),
            user_message="报告呢",
            has_snapshot=True,
        )
        self.assertEqual(plan["next"], PLAN_STEP_REPORT)
        self.assertNotIn(PLAN_STEP_QUERY, plan["steps"])

    def test_plain_query_does_not_auto_report(self):
        plan = normalize_plan(
            next_step="report",
            extraction=RetrievalExtractionResult(
                retrieval_query="金华地区电费未结清用户清单",
                keywords=["金华", "未结清"],
                intent="查询未结清用户",
                task="query",
            ),
            user_message="金华地区电费未结清用户清单",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertNotIn(PLAN_STEP_REPORT, plan["steps"])
        self.assertNotEqual(plan["next"], PLAN_STEP_STOP)


class ReportMaterializeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = report_mod.REPORTS_DIR
        report_mod.REPORTS_DIR = self.tmp

    def tearDown(self) -> None:
        report_mod.REPORTS_DIR = self._orig

    def test_jinhua_unpaid_report_writes_docx_with_three_parts(self):
        payload = materialize_analysis_report(
            _report_plan(),
            _jinhua_snapshot(),
            user_request="生成金华地区未结清用户清单的分析报告",
        )
        self.assertTrue(payload["feasible"])
        self.assertEqual(len(payload["analyses"]), 2)
        self.assertTrue(payload["file_id"])
        path = self.tmp / f"{payload['file_id']}.docx"
        self.assertTrue(path.is_file())
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode("utf-8")
            media_count = sum(1 for name in archive.namelist() if name.startswith("word/media/"))
        self.assertIn("一、总体情况", xml)
        self.assertIn("二、分类分析", xml)
        self.assertIn("三、分析结论", xml)
        self.assertIn("义乌", xml)
        self.assertGreaterEqual(media_count, 1)

    def test_report_fills_charts_when_model_omits_chart_objects(self):
        payload = materialize_analysis_report(
            json.dumps(
                {
                    "feasible": True,
                    "title": "金华未结清报告",
                    "overview": "共 32 户。",
                    "analyses": [
                        {"heading": "按所属区县分布", "narrative": "义乌用户最多。"},
                        {"heading": "按结清标志结构", "narrative": "全部为未结清。"},
                    ],
                    "conclusions": "义乌应作为催缴重点。",
                },
                ensure_ascii=False,
            ),
            _jinhua_snapshot(),
        )
        self.assertTrue(payload["feasible"])
        self.assertEqual(len(payload["analyses"]), 2)
        for item in payload["analyses"]:
            self.assertTrue(item.get("chart"))
            self.assertTrue(item["chart"].get("categories"))
        path = self.tmp / f"{payload['file_id']}.docx"
        with zipfile.ZipFile(path) as archive:
            media_count = sum(1 for name in archive.namelist() if name.startswith("word/media/"))
        self.assertGreaterEqual(media_count, 2)

    def test_write_report_without_charts_still_has_sections(self):
        dest = self.tmp / "plain.docx"
        write_report_docx(
            {
                "title": "测试报告",
                "overview": "总体情况一段话。",
                "analyses": [{"heading": "按区县", "narrative": "没有图也可以写分析。"}],
                "conclusions": "结论一条。",
            },
            dest,
        )
        self.assertTrue(dest.is_file())


if __name__ == "__main__":
    unittest.main()
