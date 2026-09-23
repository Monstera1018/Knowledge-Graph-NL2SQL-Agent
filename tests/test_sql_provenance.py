import unittest
from unittest.mock import AsyncMock, patch

from adk_agents.common import SearchStageOutput
from adk_agents.copilot.helpers import (
    COPILOT_USER_MESSAGE_STATE_KEY,
    QUERY_SNAPSHOT_FRESH_STATE_KEY,
    RETRIEVAL_CONTEXT_STATE_KEY,
    SQL_GENERATION_ATTEMPT_STATE_KEY,
    SQL_VALIDATED_SQL_STATE_KEY,
    SQL_VALIDATION_FEEDBACK_STATE_KEY,
    TABLE_RESELECT_ATTEMPT_STATE_KEY,
    TABLE_RESELECT_FEEDBACK_STATE_KEY,
    begin_table_reselect,
    can_reselect_tables,
    format_table_selector_user_message,
    save_query_snapshot,
)
from tools.sql_provenance import (
    inspect_query_health,
    question_expects_grouped_rank,
    sql_has_grouping,
)


class _Ctx:
    def __init__(self, state=None):
        self.state = state or {}


def _event_route(event) -> str:
    actions = getattr(event, "actions", None)
    return str(getattr(actions, "route", None) or getattr(event, "route", None) or "").strip()


class QueryHealthTest(unittest.TestCase):
    def test_empty_result_asks_reselect(self) -> None:
        health = inspect_query_health(
            question="杭州用户清单",
            sql="SELECT * FROM t WHERE city = '杭州'",
            row_count=0,
            dialect="sqlite",
        )
        self.assertTrue(health.reselect_tables)
        self.assertFalse(health.retry_sql)

    def test_rank_question_without_group_retries_sql(self) -> None:
        self.assertTrue(question_expects_grouped_rank("每个供电所高压用户数量排名"))
        self.assertFalse(sql_has_grouping("SELECT * FROM t WHERE volt = '高压'", "sqlite"))
        health = inspect_query_health(
            question="每个供电所高压用户数量排名",
            sql="SELECT * FROM t WHERE volt = '高压'",
            row_count=12,
            dialect="sqlite",
        )
        self.assertTrue(health.retry_sql)
        self.assertFalse(health.reselect_tables)

    def test_grouped_rank_is_healthy(self) -> None:
        sql = (
            "SELECT org_name, COUNT(*) AS cnt FROM t "
            "GROUP BY org_name ORDER BY cnt DESC"
        )
        self.assertTrue(sql_has_grouping(sql, "sqlite"))
        health = inspect_query_health(
            question="每个供电所高压用户数量排名",
            sql=sql,
            row_count=8,
            dialect="sqlite",
        )
        self.assertFalse(health.retry_sql)
        self.assertFalse(health.reselect_tables)

    def test_detail_list_is_not_treated_as_rank(self) -> None:
        self.assertFalse(question_expects_grouped_rank("每个用户的抄表明细清单"))


class TableReselectHelpersTest(unittest.TestCase):
    def test_reselect_budget_and_feedback(self) -> None:
        ctx = _Ctx()
        self.assertTrue(can_reselect_tables(ctx))
        begin_table_reselect(ctx, ["查询结果为空"])
        self.assertEqual(ctx.state[TABLE_RESELECT_ATTEMPT_STATE_KEY], "1")
        self.assertIn("查询结果为空", ctx.state[TABLE_RESELECT_FEEDBACK_STATE_KEY])
        self.assertFalse(can_reselect_tables(ctx))

    def test_selector_prompt_includes_reselect_feedback(self) -> None:
        text = format_table_selector_user_message(
            SearchStageOutput(
                user_message="杭州用户",
                retrieval_context="**数据表**\n- **t**",
                table_candidates=["t", "s"],
            ),
            reselect_feedback="- 查询结果为空",
            previous_tables=["t"],
        )
        self.assertIn("上次选表需要调整", text)
        self.assertIn("`t`", text)
        self.assertIn("查询结果为空", text)


class ValidateSqlRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_wording_difference_does_not_fail_validation(self) -> None:
        from adk_agents.copilot.agent import validate_sql

        ctx = _Ctx({
            SQL_GENERATION_ATTEMPT_STATE_KEY: 0,
            "selected_tables": {"tables": ["t"]},
            COPILOT_USER_MESSAGE_STATE_KEY: "宁波公司用户清单",
        })
        sql = "SELECT * FROM t WHERE city_zj = '宁波地区'"
        with patch(
            "adk_agents.copilot.agent.validate_read_sql",
            AsyncMock(return_value=(True, [], sql)),
        ):
            event = await validate_sql(ctx, f"```sql\n{sql}\n```")
        self.assertEqual(_event_route(event), "")
        self.assertEqual(ctx.state[SQL_VALIDATED_SQL_STATE_KEY], sql)

    async def test_validation_exhausted_routes_reselect(self) -> None:
        from adk_agents.copilot.agent import validate_sql

        ctx = _Ctx({
            SQL_GENERATION_ATTEMPT_STATE_KEY: 2,
            "selected_tables": {"tables": ["t"]},
            COPILOT_USER_MESSAGE_STATE_KEY: "杭州用户清单",
            RETRIEVAL_CONTEXT_STATE_KEY: "**数据表**",
        })
        with patch(
            "adk_agents.copilot.agent.validate_read_sql",
            AsyncMock(return_value=(False, ["字段不存在"], "")),
        ):
            event = await validate_sql(ctx, "SELECT missing FROM t")
        self.assertEqual(_event_route(event), "reselect")
        self.assertEqual(ctx.state[TABLE_RESELECT_ATTEMPT_STATE_KEY], "1")


class ExecuteSqlHealthRouteTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_result_routes_reselect_without_snapshot(self) -> None:
        from adk_agents.copilot.agent import execute_sql

        ctx = _Ctx({
            SQL_VALIDATED_SQL_STATE_KEY: "SELECT * FROM t WHERE city = '杭州'",
            SQL_GENERATION_ATTEMPT_STATE_KEY: 0,
            COPILOT_USER_MESSAGE_STATE_KEY: "杭州用户清单",
        })
        with patch(
            "adk_agents.copilot.agent.execute_read_sql",
            AsyncMock(return_value=[]),
        ), patch(
            "adk_agents.copilot.agent.extract_sql_statements_from_text",
            return_value=(["SELECT * FROM t WHERE city = '杭州'"], ""),
        ):
            event = await execute_sql(ctx, "")
        self.assertEqual(_event_route(event), "reselect")
        self.assertNotEqual(str(ctx.state.get(QUERY_SNAPSHOT_FRESH_STATE_KEY) or ""), "1")

    async def test_rank_without_group_routes_sql_retry(self) -> None:
        from adk_agents.copilot.agent import execute_sql

        sql = "SELECT * FROM t WHERE volt = '高压'"
        ctx = _Ctx({
            SQL_VALIDATED_SQL_STATE_KEY: sql,
            SQL_GENERATION_ATTEMPT_STATE_KEY: 0,
            COPILOT_USER_MESSAGE_STATE_KEY: "每个供电所高压用户数量排名",
        })
        with patch(
            "adk_agents.copilot.agent.execute_read_sql",
            AsyncMock(return_value=[{"供电所": "A", "用户": "1"}]),
        ), patch(
            "adk_agents.copilot.agent.extract_sql_statements_from_text",
            return_value=([sql], ""),
        ):
            event = await execute_sql(ctx, "")
        self.assertEqual(_event_route(event), "retry")
        self.assertIn("GROUP BY", ctx.state[SQL_VALIDATION_FEEDBACK_STATE_KEY])

    def test_observe_requires_non_empty_fresh_snapshot(self) -> None:
        from adk_agents.copilot.agent import observe_query_result
        from adk_agents.copilot.plan import OBS_QUERY_FAILED, OBS_QUERY_SUCCESS

        ctx = _Ctx()
        save_query_snapshot(ctx, [([], False)])
        event = observe_query_result(ctx, "（无数据）")
        self.assertIsNotNone(event)
        from adk_agents.copilot.helpers import load_plan_observation
        observation = load_plan_observation(ctx)
        self.assertEqual(observation.get("code"), OBS_QUERY_FAILED)

        ctx2 = _Ctx()
        save_query_snapshot(ctx2, [([{"用户": "1"}], False)])
        observe_query_result(ctx2, "ok")
        self.assertEqual(load_plan_observation(ctx2).get("code"), OBS_QUERY_SUCCESS)


class WorkflowGraphGuardrailTest(unittest.TestCase):
    def test_validate_and_execute_can_reselect_tables(self) -> None:
        from adk_agents.copilot.agent import root_agent

        names = {node.name for node in root_agent.graph.nodes}
        self.assertIn("prepare_table_selector_input", names)
        self.assertIn("observe_query_result", names)
        self.assertNotIn("inspect_query_result", names)


if __name__ == "__main__":
    unittest.main()
