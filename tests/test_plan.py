import unittest

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot.helpers import infer_snapshot_grain
from adk_agents.copilot.plan import (
    OBS_CHART_INFEASIBLE,
    OBS_NO_SNAPSHOT,
    OBS_NO_TABLES,
    OBS_QUERY_SUCCESS,
    PLAN_STEP_CHART,
    PLAN_STEP_CHITCHAT,
    PLAN_STEP_QUERY,
    PLAN_STEP_STOP,
    normalize_plan,
    plan_from_model_text,
    plan_next,
)


def _extraction(**kwargs) -> RetrievalExtractionResult:
    payload = {
        "retrieval_query": "金华地区电费未结清用户清单",
        "keywords": ["金华", "未结清"],
        "intent": "查询未结清用户",
        "task": "query",
    }
    payload.update(kwargs)
    return RetrievalExtractionResult(**payload)


class PlanNormalizeTest(unittest.TestCase):
    def test_plain_query_stays_query_even_if_model_adds_chart(self):
        plan = normalize_plan(
            steps=["query", "chart"],
            extraction=_extraction(),
            user_message="金华地区电费未结清用户清单",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertEqual(plan["steps"], [PLAN_STEP_QUERY])

    def test_combined_query_and_chart_starts_with_query(self):
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertEqual(plan["steps"], [PLAN_STEP_QUERY])
        self.assertIn("柱状图", plan["chart_request"])

    def test_query_success_then_chart_when_user_wants_chart(self):
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=False,
        )
        plan = normalize_plan(
            next_step="stop",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)
        self.assertEqual(plan["steps"], [PLAN_STEP_QUERY, PLAN_STEP_CHART])
        self.assertIn("柱状图", plan["chart_request"])

    def test_generate_analysis_chart_is_not_stripped(self):
        message = "帮我查询金华地区各县区用户占比，并生成分析图表"
        plan = normalize_plan(
            steps=["query", "chart"],
            extraction=_extraction(task="query"),
            user_message=message,
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertEqual(plan["chart_request"], message)

        plan = normalize_plan(
            steps=["query"],
            extraction=_extraction(task="query"),
            user_message=message,
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertEqual(plan["chart_request"], message)

    def test_direct_chart_without_snapshot_can_choose_chart_first(self):
        plan = normalize_plan(
            steps=["chart"],
            extraction=_extraction(
                retrieval_query="按地区统计用户数",
                keywords=["地区", "用户数"],
                intent="按地区出图",
                task="chart",
            ),
            user_message="画个各地区用户数柱状图",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)

    def test_no_snapshot_observation_replans_to_query(self):
        previous = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=False,
        )
        plan = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=False,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "blocked",
                "code": OBS_NO_SNAPSHOT,
            },
            previous_plan=previous,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)
        self.assertEqual(plan["steps"], [PLAN_STEP_CHART, PLAN_STEP_QUERY])

    def test_chart_followup_with_snapshot_skips_query(self):
        plan = normalize_plan(
            steps=["chart"],
            extraction=_extraction(
                retrieval_query="换成饼图并改橙黄色",
                keywords=["饼图", "橙黄"],
                intent="更换图表展示",
                task="chart",
            ),
            user_message="可以换一个展示形式，并且色调改成橙黄色吗",
            has_snapshot=True,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)
        self.assertEqual(plan["steps"], [PLAN_STEP_CHART])

    def test_chitchat_is_exclusive(self):
        plan = normalize_plan(
            steps=["chitchat"],
            extraction=_extraction(
                retrieval_query="",
                keywords=[],
                intent="非数据查询",
                task="chitchat",
            ),
            user_message="你好",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHITCHAT)
        self.assertEqual(plan["steps"], [PLAN_STEP_CHITCHAT])

    def test_query_failure_stops_instead_of_retrying(self):
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(),
            user_message="金华地区电费未结清用户清单",
            has_snapshot=False,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "failed",
                "code": OBS_NO_TABLES,
            },
        )
        self.assertEqual(plan["next"], PLAN_STEP_STOP)

    def test_first_turn_stop_falls_back_to_query(self):
        plan = normalize_plan(
            next_step="stop",
            extraction=_extraction(),
            user_message="全省高压用户统计",
            has_snapshot=False,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)

    def test_invalid_model_text_falls_back_to_query(self):
        plan = plan_from_model_text(
            "不是 JSON",
            extraction=_extraction(),
            user_message="全省高压用户统计",
            has_snapshot=False,
        )
        self.assertEqual(plan_next(plan), PLAN_STEP_QUERY)

    def test_chart_infeasible_does_not_stop_when_need_unmet(self):
        previous = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
        )
        plan = normalize_plan(
            next_step="stop",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
                "detail": "维度列不在当前结果中。",
            },
            previous_plan=previous,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)

    def test_first_chart_infeasible_keeps_model_chart_retry(self):
        previous = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
        )
        plan = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
            },
            previous_plan=previous,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)

    def test_repeated_chart_infeasible_switches_to_query(self):
        first = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
        )
        retry = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
            },
            previous_plan=first,
        )
        plan = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="chart"),
            user_message="画个各地区用户数柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
            },
            previous_plan=retry,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)

    def test_query_success_with_chart_request_goes_to_chart(self):
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=False,
        )
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "detail",
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)

    def test_query_and_chart_does_not_stop_after_data(self):
        message = "输出业扩工单流程数据中的有效工单数据,并生成分析图表"
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query", retrieval_query=message),
            user_message=message,
            has_snapshot=False,
        )
        self.assertEqual(first["next"], PLAN_STEP_QUERY)
        plan = normalize_plan(
            next_step="stop",
            extraction=_extraction(task="query"),
            user_message=message,
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "detail",
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)

    def test_query_success_stops_for_list_question(self):
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(),
            user_message="输出业扩工单流程数据中的有效工单数据",
            has_snapshot=False,
        )
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(),
            user_message="输出业扩工单流程数据中的有效工单数据",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "detail",
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_STOP)

    def test_query_success_aggregated_snapshot_goes_to_chart(self):
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=False,
        )
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "aggregated",
            },
            previous_plan=first,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)

    def test_chart_infeasible_aggregated_missing_columns_requeries(self):
        message = "输出业扩工单流程数据中的有效工单数据,并生成分析图表"
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query", retrieval_query=message),
            user_message=message,
            has_snapshot=False,
        )
        charted = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="query", retrieval_query=message),
            user_message=message,
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "aggregated",
            },
            previous_plan=first,
        )
        plan = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="query", retrieval_query=message),
            user_message=message,
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
                "has_snapshot": True,
                "snapshot_grain": "aggregated",
                "detail": "维度列「工单类型」不在当前结果中。可用列：有效工单数量。",
            },
            previous_plan=charted,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)

    def test_chart_infeasible_on_detail_allows_one_rewrite(self):
        first = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=False,
        )
        charted = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_QUERY,
                "status": "success",
                "code": OBS_QUERY_SUCCESS,
                "has_snapshot": True,
                "snapshot_grain": "detail",
            },
            previous_plan=first,
        )
        plan = normalize_plan(
            next_step="query",
            extraction=_extraction(task="query"),
            user_message="帮我查询各地区用户数并画柱状图",
            has_snapshot=True,
            observation={
                "last_next": PLAN_STEP_CHART,
                "status": "failed",
                "code": OBS_CHART_INFEASIBLE,
                "has_snapshot": True,
                "snapshot_grain": "detail",
            },
            previous_plan=charted,
        )
        self.assertEqual(plan["next"], PLAN_STEP_QUERY)

    def test_chart_followup_without_keyword_keeps_chart_when_snapshot_exists(self):
        plan = normalize_plan(
            next_step="chart",
            extraction=_extraction(task="query"),
            user_message="这不是还是只有一张吗",
            has_snapshot=True,
        )
        self.assertEqual(plan["next"], PLAN_STEP_CHART)


class SnapshotGrainTest(unittest.TestCase):
    def test_grouped_sql_is_aggregated(self):
        snapshot = {
            "result_sets": [{
                "columns": ["地区", "户数"],
                "rows": [{"地区": "杭州", "户数": "10"}, {"地区": "宁波", "户数": "8"}],
            }],
            "sql": "SELECT city_zj AS 地区, COUNT(*) AS 户数 FROM t GROUP BY city_zj",
            "dialect": "sqlite",
        }
        self.assertEqual(infer_snapshot_grain(snapshot), "aggregated")

    def test_work_order_list_is_detail(self):
        rows = [
            {"工单编号": f"WO{index:03d}", "工单状态": "在途" if index % 2 else "归档"}
            for index in range(12)
        ]
        snapshot = {
            "result_sets": [{
                "columns": ["工单编号", "工单状态"],
                "rows": rows,
            }],
            "sql": "SELECT wk_order_no, wk_order_stat FROM dws_cst_ykgdlcxxb_rhb_xs",
            "dialect": "sqlite",
        }
        self.assertEqual(infer_snapshot_grain(snapshot), "detail")


class WorkflowGraphTest(unittest.TestCase):
    def test_root_agent_graph_accepts_planner_loop(self):
        from adk_agents.copilot.agent import root_agent

        self.assertIsNotNone(root_agent.graph)
        names = {node.name for node in root_agent.graph.nodes}
        self.assertIn("prepare_planner_input", names)
        self.assertIn("observe_query_result", names)
        self.assertIn("observe_no_snapshot", names)
        self.assertIn("prepare_report_planner_input", names)
        self.assertIn("prepare_sql_generator_input", names)
        self.assertIn("report_planner", names)
        self.assertIn("end_plan", names)
        self.assertNotIn("route_after_execute", names)
        self.assertNotIn("inspect_query_result", names)
        self.assertNotIn("format_no_chart_snapshot_response", names)


if __name__ == "__main__":
    unittest.main()
