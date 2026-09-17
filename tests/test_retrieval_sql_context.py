import unittest

from tools.retrieval import (
    RetrievalGraphBundle,
    SqlEntry,
    format_graph_context_for_llm,
    format_sql_generation_context,
    merge_sql_entries,
    parse_sql_reference_samples,
)
from server.chat.service import _extract_context_sections


class SqlReferenceContextTest(unittest.TestCase):
    def test_format_includes_logic_and_sql_content(self) -> None:
        bundle = RetrievalGraphBundle(
            sql=[
                SqlEntry(
                    uuid="s1",
                    name="居民合表用电清单",
                    logic="按执行电价筛选居民合表用户",
                    content="SELECT cust_no FROM dws_cst_jldxxb_rhb_gw\nWHERE prc_code LIKE '%居民%合表%'",
                    related_tables=["dws_cst_jldxxb_rhb_gw.prc_code"],
                    from_hit=True,
                )
            ]
        )
        text = format_graph_context_for_llm(bundle)
        self.assertIn("**SQL**", text)
        self.assertIn("按执行电价筛选居民合表用户", text)
        self.assertIn("```sql", text)
        self.assertIn("WHERE prc_code LIKE '%居民%合表%'", text)
        self.assertIn("`dws_cst_jldxxb_rhb_gw.prc_code`", text)

    def test_parse_roundtrip_samples(self) -> None:
        bundle = RetrievalGraphBundle(
            sql=[
                SqlEntry(
                    uuid="s1",
                    name="电费未结清",
                    logic="筛选未结清用户",
                    content="SELECT * FROM dws_fee WHERE status = '未结清'",
                    related_tables=["dws_fee.status"],
                ),
                SqlEntry(
                    uuid="s2",
                    name="仅有逻辑",
                    logic="没有语句的旧样例",
                ),
            ]
        )
        samples = parse_sql_reference_samples(
            format_graph_context_for_llm(bundle).split("**SQL**", 1)[1].strip()
        )
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0]["name"], "电费未结清")
        self.assertEqual(samples[0]["logic"], "筛选未结清用户")
        self.assertIn("未结清", str(samples[0]["content"]))
        self.assertEqual(samples[0]["related_tables"], ["dws_fee.status"])
        self.assertEqual(samples[1]["name"], "仅有逻辑")
        self.assertEqual(samples[1]["content"], "")

    def test_extract_context_sections_keeps_sql_samples(self) -> None:
        text = format_graph_context_for_llm(
            RetrievalGraphBundle(
                sql=[
                    SqlEntry(
                        name="高压专变",
                        logic="筛选专变用户",
                        content="SELECT 1",
                        related_tables=["dws_cst_yhgddyxxb_rhb_gw"],
                    )
                ]
            )
        )
        sections = _extract_context_sections(text)
        sql_section = next(section for section in sections if section["title"] == "SQL")
        self.assertEqual(sql_section["items"], ["高压专变"])
        samples = sql_section["sql_samples"]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["name"], "高压专变")
        self.assertEqual(samples[0]["content"], "SELECT 1")

    def test_merge_prefers_hits_and_respects_limit(self) -> None:
        hits = [
            SqlEntry(uuid="h1", name="命中1", logic="a", content="SELECT 1", from_hit=True),
            SqlEntry(uuid="h2", name="命中2", logic="b", content="SELECT 2", from_hit=True),
        ]
        graph = [
            SqlEntry(uuid="h1", name="命中1", logic="重复", content="SELECT 1"),
            SqlEntry(uuid="g1", name="补全1", logic="c", content="SELECT 3"),
            SqlEntry(uuid="g2", name="补全2", logic="d", content="SELECT 4"),
        ]
        merged = merge_sql_entries(hits, graph, limit=3)
        self.assertEqual([item.uuid for item in merged], ["h1", "h2", "g1"])
        self.assertTrue(merged[0].from_hit)

    def test_sql_generation_context_warns_not_to_copy_sample_filters(self) -> None:
        text = format_sql_generation_context(
            RetrievalGraphBundle(
                sql=[
                    SqlEntry(
                        name="宗教合表稽核",
                        logic="剔除定量定比、分表计量和分户计量用户",
                        content="SELECT 1 WHERE fqr_val <> 0",
                    )
                ]
            ),
            user_message="输出台州地区宗教执行居民合表的用电量最少的50条数据",
        )
        self.assertIn("不要把样例中用户未提到的剔除规则", text)
        self.assertIn("**用户问题**", text)
        self.assertIn("剔除定量定比、分表计量和分户计量用户", text)


if __name__ == "__main__":
    unittest.main()
