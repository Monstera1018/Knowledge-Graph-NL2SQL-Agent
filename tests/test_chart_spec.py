import unittest

from adk_agents.copilot.chart import materialize_chart_spec, parse_number


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

    def test_unknown_column_is_rejected(self):
        raw = """
        {"feasible": true, "summary": "电压", "charts": [
          {"title": "电压", "type": "pie", "x": {"column": "电压等级"}, "y": [{"column": "用户编号", "aggregation": "count"}]}
        ]}
        """
        payload = materialize_chart_spec(raw, self.snapshot)
        self.assertFalse(payload["feasible"])
        self.assertEqual(payload["charts"], [])

    def test_missing_snapshot(self):
        payload = materialize_chart_spec("{}", None)
        self.assertFalse(payload["feasible"])

    def test_parse_number(self):
        self.assertEqual(parse_number("1,000"), 1000.0)
        self.assertIsNone(parse_number("金华"))


if __name__ == "__main__":
    unittest.main()
