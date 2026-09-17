import unittest

from adk_agents.copilot.helpers import extract_sql_logic_from_output
from tools.graph.model import SQLNode, SQLSource, normalize_sql_enabled, normalize_sql_source
from tools.sql_similarity import (
    find_best_similar_sql,
    merge_sql_logic,
    sql_merge_similarity_threshold,
    sql_sample_similarity,
)


class SqlPrecipitationHelpersTest(unittest.TestCase):
    def test_extract_logic_strips_sql_fence(self) -> None:
        text = "按执行电价筛选居民合表用户。\n\n```sql\nSELECT 1;\n```\n"
        self.assertEqual(extract_sql_logic_from_output(text), "按执行电价筛选居民合表用户。")

    def test_extract_logic_empty_when_only_sql(self) -> None:
        self.assertEqual(extract_sql_logic_from_output("```sql\nSELECT 1;\n```"), "")

    def test_normalize_source_and_enabled(self) -> None:
        self.assertEqual(normalize_sql_source("llm"), SQLSource.LLM)
        self.assertEqual(normalize_sql_source(None), SQLSource.IMPORT)
        self.assertTrue(normalize_sql_enabled(None))
        self.assertFalse(normalize_sql_enabled("false"))
        self.assertTrue(normalize_sql_enabled(1))

    def test_sql_node_defaults_for_legacy_graph_payload(self) -> None:
        node = SQLNode.model_validate({
            "uuid": "abc",
            "workspace_id": "default",
            "type": "SQL",
            "name": "样例",
            "logic": "说明",
            "content": "SELECT 1",
            "dialect": "sqlite",
        })
        self.assertEqual(node.source, SQLSource.IMPORT)
        self.assertTrue(node.enabled)
        self.assertEqual(node.created_at, "")
        self.assertEqual(node.updated_at, "")


class SqlSimilarityTest(unittest.TestCase):
    def test_similar_ningbo_queries_should_merge(self) -> None:
        left = (
            "SELECT cust_no, cust_name FROM dws_cst_yhjbxxb_rhb_xs "
            "WHERE city_zj = '宁波' AND prc_st_type = '单一制'"
        )
        right = (
            "SELECT cust_no FROM dws_cst_yhjbxxb_rhb_xs "
            "WHERE city_zj = '宁波' AND prc_st_type = '单一制'"
        )
        score = sql_sample_similarity(
            name="查询宁波地区执行两部制定价策略为单一制的用户",
            content=left,
            dialect="sqlite",
            other_name="查询宁波地区执行两部制定价策略为单一制的电价",
            other_content=right,
            other_dialect="sqlite",
        )
        self.assertGreaterEqual(score, sql_merge_similarity_threshold())

    def test_same_structure_different_city_should_not_merge(self) -> None:
        left = "SELECT cust_no FROM dws_cst_yhjbxxb_rhb_xs WHERE city_zj = '宁波'"
        right = "SELECT cust_no FROM dws_cst_yhjbxxb_rhb_xs WHERE city_zj = '杭州'"
        score = sql_sample_similarity(
            name="查询宁波地区用户",
            content=left,
            dialect="sqlite",
            other_name="查询杭州地区用户",
            other_content=right,
            other_dialect="sqlite",
        )
        self.assertLess(score, sql_merge_similarity_threshold())

    def test_find_best_similar_sql_returns_match(self) -> None:
        candidate = SQLNode.model_validate({
            "uuid": "sql-1",
            "workspace_id": "default",
            "type": "SQL",
            "name": "查询宁波地区执行两部制定价策略为单一制的用户",
            "logic": "筛选宁波单一制用户",
            "content": "SELECT cust_no FROM dws_cst_yhjbxxb_rhb_xs WHERE city_zj = '宁波'",
            "dialect": "sqlite",
            "source": "llm",
            "enabled": False,
        })
        matched = find_best_similar_sql(
            name="查询宁波地区执行两部制定价策略为单一制的电价",
            content="SELECT cust_no, cust_name FROM dws_cst_yhjbxxb_rhb_xs WHERE city_zj = '宁波'",
            dialect="sqlite",
            candidates=[candidate],
        )
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched[0].uuid, "sql-1")

    def test_merge_sql_logic_keeps_richer_text(self) -> None:
        self.assertEqual(merge_sql_logic("短说明", "更完整的逻辑说明，包含筛选口径。"), "更完整的逻辑说明，包含筛选口径。")
        self.assertEqual(merge_sql_logic("已有完整说明", "完整"), "已有完整说明")


if __name__ == "__main__":
    unittest.main()
