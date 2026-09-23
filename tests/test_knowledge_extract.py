import unittest

from tools.graph.model import (
    KnowledgeNode,
    KnowledgeRelation,
    KnowledgeReviewStatus,
    KnowledgeSource,
    SQLNode,
    SQLReviewStatus,
)
from tools.knowledge_extract import parse_extracted_knowledge_items, should_extract_knowledge_from_sql
from tools.knowledge_similarity import (
    find_best_similar_knowledge,
    is_similar_knowledge,
    knowledge_merge_similarity_threshold,
    merge_knowledge_description,
    merge_knowledge_relations,
    normalize_knowledge_text,
)


def _sql(**overrides: object) -> SQLNode:
    payload = {
        "uuid": "sql-1",
        "workspace_id": "default",
        "type": "SQL",
        "name": "宁波公司高压用户",
        "logic": "地区映射为宁波地区",
        "content": "SELECT 1",
        "dialect": "sqlite",
        "source": "llm",
        "enabled": False,
        "review_status": "pending",
        "knowledge_extracted": False,
    }
    payload.update(overrides)
    return SQLNode.model_validate(payload)


class KnowledgeExtractParseTest(unittest.TestCase):
    def test_parse_array_and_fence(self) -> None:
        text = """```json
        [
          {
            "name": "地区映射",
            "description": "用户说宁波公司时，地区字段存宁波地区。",
            "relations": [{"table_name": "dws_cst_yhjbxxb_rhb_xs", "column_names": ["city_zj"]}]
          }
        ]
        ```"""
        items = parse_extracted_knowledge_items(text)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["name"], "地区映射")
        self.assertEqual(items[0]["relations"][0]["column_names"], ["city_zj"])

    def test_parse_rejects_empty_name(self) -> None:
        items = parse_extracted_knowledge_items('[{"name":"","description":"x"}]')
        self.assertEqual(items, [])

    def test_should_extract_only_on_first_approve_or_enable_retry(self) -> None:
        pending = _sql()
        approved = _sql(enabled=True, review_status="approved", knowledge_extracted=False)
        extracted = _sql(enabled=True, review_status="approved", knowledge_extracted=True)
        self.assertTrue(should_extract_knowledge_from_sql(pending, approved))
        self.assertFalse(should_extract_knowledge_from_sql(approved, extracted))
        self.assertFalse(should_extract_knowledge_from_sql(extracted, extracted))
        disabled = _sql(enabled=False, review_status="approved", knowledge_extracted=True)
        reenabled = _sql(enabled=True, review_status="approved", knowledge_extracted=True)
        self.assertFalse(should_extract_knowledge_from_sql(disabled, reenabled))
        failed = _sql(enabled=False, review_status="approved", knowledge_extracted=False)
        retry = _sql(enabled=True, review_status="approved", knowledge_extracted=False)
        self.assertTrue(should_extract_knowledge_from_sql(failed, retry))


class KnowledgeReviewHelpersTest(unittest.TestCase):
    def test_legacy_llm_disabled_knowledge_is_pending(self) -> None:
        node = KnowledgeNode.model_validate({
            "uuid": "k-1",
            "workspace_id": "default",
            "type": "KNOWLEDGE",
            "name": "地区映射",
            "description": "宁波公司对应宁波地区",
            "source": "llm",
            "enabled": False,
        })
        self.assertEqual(node.review_status, KnowledgeReviewStatus.PENDING)

    def test_import_knowledge_defaults_approved(self) -> None:
        node = KnowledgeNode.model_validate({
            "uuid": "k-2",
            "workspace_id": "default",
            "type": "KNOWLEDGE",
            "name": "高压用户",
            "description": "电压等级为高压",
        })
        self.assertEqual(node.source, KnowledgeSource.IMPORT)
        self.assertTrue(node.enabled)
        self.assertEqual(node.review_status, KnowledgeReviewStatus.APPROVED)


def _knowledge(**overrides: object) -> KnowledgeNode:
    payload = {
        "uuid": "k-region",
        "workspace_id": "default",
        "type": "KNOWLEDGE",
        "name": "地区字段映射",
        "description": "用户问题中的「地区」对应库内用户基本信息表的地区字段 city_zj，取值如各市地区名称。",
        "source": "llm",
        "enabled": False,
        "review_status": "pending",
    }
    payload.update(overrides)
    return KnowledgeNode.model_validate(payload)


_CITY_RELATIONS = [KnowledgeRelation(table_name="dws_cst_yhjbxxb_rhb_xs", column_names=["city_zj"])]


class KnowledgeSimilarityTest(unittest.TestCase):
    def test_normalize_masks_city_variants(self) -> None:
        self.assertEqual(
            normalize_knowledge_text("宁波公司对应宁波地区"),
            "{地区}对应{地区}",
        )

    def test_same_region_mapping_with_different_cities_should_merge(self) -> None:
        matched, score = is_similar_knowledge(
            name="地区字段映射",
            description="用户说宁波公司、宁波市时，地区字段 city_zj 应写成宁波地区。",
            relations=_CITY_RELATIONS,
            other_name="地区字段映射",
            other_description="用户问题中的「地区」对应库内用户基本信息表的地区字段 city_zj，取值如各市地区名称。",
            other_relations=_CITY_RELATIONS,
        )
        self.assertTrue(matched, score)
        self.assertGreaterEqual(score, 0.62)

    def test_settle_enum_should_not_merge_with_unsettled_flag(self) -> None:
        matched, score = is_similar_knowledge(
            name="结清状态枚举",
            description="结清标志 settle 取值包括未结清、部分结清、全部结清。",
            relations=[KnowledgeRelation(table_name="dws_cst_yqwhzmxb_rhb_xs", column_names=["settle"])],
            other_name="未结清",
            other_description="筛选结清标识等于“未结清”",
            other_relations=[KnowledgeRelation(table_name="dws_cst_yqwhzmxb_rhb_xs", column_names=["settle"])],
        )
        self.assertFalse(matched, score)
        self.assertLess(score, knowledge_merge_similarity_threshold())

    def test_find_best_similar_knowledge_returns_match(self) -> None:
        candidate = _knowledge()
        matched = find_best_similar_knowledge(
            name="地区字段映射",
            description="「杭州公司」映射到地区字段 city_zj 的杭州地区。",
            relations=_CITY_RELATIONS,
            candidates=[(candidate, _CITY_RELATIONS)],
        )
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(matched[0].uuid, "k-region")

    def test_merge_description_keeps_richer_text(self) -> None:
        self.assertEqual(
            merge_knowledge_description("短说明", "更完整的地区字段映射口径，包含公司到地区的写法。"),
            "更完整的地区字段映射口径，包含公司到地区的写法。",
        )
        self.assertEqual(merge_knowledge_description("已有完整说明", "完整"), "已有完整说明")

    def test_merge_relations_unions_columns(self) -> None:
        merged = merge_knowledge_relations(
            [KnowledgeRelation(table_name="t1", column_names=["city_zj"])],
            [KnowledgeRelation(table_name="t1", column_names=["city_zj", "org_no"])],
        )
        self.assertEqual(merged[0].table_name, "t1")
        self.assertEqual(merged[0].column_names, ["city_zj", "org_no"])


if __name__ == "__main__":
    unittest.main()
