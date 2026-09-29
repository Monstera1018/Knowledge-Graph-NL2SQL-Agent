import json
import unittest

from adk_agents.copilot.chart import (
    format_snapshot_result_overview,
    materialize_chart_spec,
    parse_number,
)


class ChartSpecTest(unittest.TestCase):
    def setUp(self):
        self.snapshot = {
            "user_question": "金华未结清用户清单",
            "schema_context": "地区是用户归属维度",
            "selected_tables": ["dws_user"],
            "result_sets": [
                {
                    "columns": ["用户编号", "地区", "电费"],
                    "rows": [
                        {"用户编号": "1", "地区": "金华", "电费": "10"},
                        {"用户编号": "2", "地区": "金华", "电费": "20"},
                        {"用户编号": "3", "地区": "宁波", "电费": "30"},
                    ],
                }
            ],
        }

    def test_count_bar_chart_from_existing_rows(self):
        raw = """
        {
          "feasible": true,
          "summary": "按地区统计用户数",
          "charts": [
            {
              "title": "各地区用户数",
              "type": "bar",
              "x": {"column": "地区", "role": "dimension"},
              "y": [{"column": "用户编号", "aggregation": "count", "label": "用户数"}]
            }
          ]
        }
        """
        payload = materialize_chart_spec(raw, self.snapshot)
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["kind"], "chart_spec")
        chart = payload["charts"][0]
        self.assertEqual(chart["type"], "bar")
        self.assertEqual(chart["categories"], ["金华", "宁波"])
        self.assertEqual(chart["series"][0]["values"], [2.0, 1.0])
        self.assertEqual(chart["palette"][0], "#1677ff")

    def test_orange_palette_from_user_request(self):
        raw = """
        {
          "feasible": true,
          "summary": "按地区统计用户数",
          "charts": [
            {
              "title": "各地区用户数",
              "type": "pie",
              "x": {"column": "地区", "role": "dimension"},
              "y": [{"column": "用户编号", "aggregation": "count", "label": "用户数"}]
            }
          ]
        }
        """
        payload = materialize_chart_spec(
            raw,
            self.snapshot,
            user_request="换成饼图，色调改成橙黄色",
        )
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["charts"][0]["type"], "pie")
        self.assertEqual(payload["charts"][0]["palette"][0], "#ea580c")

    def test_string_axes_and_fuzzy_column_names(self):
        snapshot = {
            "result_sets": [{
                "columns": ["地市公司名称", "应收电费"],
                "rows": [
                    {"地市公司名称": "绍兴", "应收电费": "100"},
                    {"地市公司名称": "绍兴", "应收电费": "50"},
                    {"地市公司名称": "湖州", "应收电费": "20"},
                ],
            }],
        }
        payload = materialize_chart_spec(
            json.dumps({
                "feasible": True,
                "charts": [{
                    "title": "各地市电费",
                    "x": "地市公司",
                    "y": "应收电费",
                }],
            }, ensure_ascii=False),
            snapshot,
        )
        self.assertTrue(payload["feasible"])
        chart = payload["charts"][0]
        self.assertEqual(chart["categories"], ["绍兴", "湖州"])
        self.assertEqual(chart["series"][0]["values"], [150.0, 20.0])

    def test_unknown_column_falls_back_to_available_dimension(self):
        raw = """
        {"feasible": true, "summary": "电压", "charts": [
          {"title": "电压", "type": "pie", "x": {"column": "电压等级"}, "y": [{"column": "用户编号", "aggregation": "count"}]}
        ]}
        """
        payload = materialize_chart_spec(raw, self.snapshot)
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["charts"][0]["categories"], ["金华", "宁波"])

    def test_compound_dimension_binds_first_existing_column(self):
        snapshot = {
            "result_sets": [{
                "columns": ["客户名称", "记账单号", "应收电费金额"],
                "rows": [
                    {"客户名称": "甲", "记账单号": "A1", "应收电费金额": "10"},
                    {"客户名称": "乙", "记账单号": "A2", "应收电费金额": "20"},
                    {"客户名称": "甲", "记账单号": "A3", "应收电费金额": "5"},
                ],
            }],
        }
        payload = materialize_chart_spec(
            json.dumps({
                "feasible": False,
                "summary": "当前查询结果中不存在「客户名称(记账单号)」对应列，无法按指定维度绘图。",
                "charts": [{
                    "title": "客户应收",
                    "type": "bar",
                    "x": "客户名称(记账单号)",
                    "y": "应收电费金额",
                }],
            }, ensure_ascii=False),
            snapshot,
        )
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["charts"][0]["categories"], ["甲", "乙"])
        self.assertEqual(payload["charts"][0]["series"][0]["values"], [15.0, 20.0])

    def test_model_infeasible_without_charts_uses_fallback(self):
        payload = materialize_chart_spec(
            json.dumps({
                "feasible": False,
                "summary": "当前查询结果中不存在「客户名称」对应列，建议调整X轴维度。",
                "charts": [],
            }, ensure_ascii=False),
            self.snapshot,
        )
        self.assertTrue(payload["feasible"])
        self.assertTrue(payload["charts"])

    def test_missing_snapshot(self):
        payload = materialize_chart_spec("{}", None)
        self.assertFalse(payload["feasible"])

    def test_radar_requires_at_least_three_categories(self):
        payload = materialize_chart_spec(
            json.dumps({
                "feasible": True,
                "charts": [{
                    "title": "地区用户轮廓",
                    "type": "radar",
                    "x": {"column": "地区"},
                    "y": [{"column": "用户编号", "aggregation": "count", "label": "用户数"}],
                }],
            }, ensure_ascii=False),
            {
                "result_sets": [{
                    "columns": ["用户编号", "地区"],
                    "rows": [
                        {"用户编号": "1", "地区": "杭州"},
                        {"用户编号": "2", "地区": "宁波"},
                        {"用户编号": "3", "地区": "金华"},
                        {"用户编号": "4", "地区": "杭州"},
                    ],
                }],
            },
        )
        self.assertTrue(payload["feasible"])
        self.assertEqual(payload["charts"][0]["type"], "radar")
        self.assertEqual(sorted(payload["charts"][0]["categories"]), ["宁波", "杭州", "金华"])

    def test_scatter_uses_numeric_xy_points(self):
        payload = materialize_chart_spec(
            json.dumps({
                "feasible": True,
                "charts": [{
                    "title": "容量与电费",
                    "type": "scatter",
                    "x": {"column": "容量"},
                    "y": [{"column": "电费", "aggregation": "sum", "label": "电费"}],
                }],
            }, ensure_ascii=False),
            {
                "result_sets": [{
                    "columns": ["容量", "电费"],
                    "rows": [
                        {"容量": "10", "电费": "100"},
                        {"容量": "20", "电费": "80"},
                        {"容量": "30", "电费": "40"},
                    ],
                }],
            },
        )
        self.assertTrue(payload["feasible"])
        chart = payload["charts"][0]
        self.assertEqual(chart["type"], "scatter")
        self.assertEqual(chart["categories"], ["10", "20", "30"])
        self.assertEqual(chart["series"][0]["values"], [100.0, 80.0, 40.0])

    def test_parse_number(self):
        self.assertEqual(parse_number("1,000"), 1000.0)
        self.assertIsNone(parse_number("金华"))

    def test_result_overview_includes_category_counts_and_numeric_totals(self):
        text = format_snapshot_result_overview(self.snapshot)
        self.assertIn("分布：金华 2 条、宁波 1 条", text)
        self.assertIn("合计 60", text)
        self.assertNotIn("分布：1 1 条", text)


if __name__ == "__main__":
    unittest.main()
