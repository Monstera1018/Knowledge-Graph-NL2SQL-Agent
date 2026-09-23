from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Sequence

from tools.graph.model import KnowledgeNode, KnowledgeRelation

_DEFAULT_MERGE_THRESHOLD = 0.82
_REGION_RE = re.compile(
    r"(杭州|宁波|温州|嘉兴|湖州|绍兴|金华|衢州|舟山|台州|丽水|浙江|全省)(公司|地区|市|县)?"
)
_WHITESPACE_RE = re.compile(r"\s+")


def knowledge_merge_similarity_threshold() -> float:
    raw = (os.environ.get("KNOWLEDGE_MERGE_SIMILARITY_THRESHOLD") or "").strip()
    if not raw:
        return _DEFAULT_MERGE_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MERGE_THRESHOLD
    return min(max(value, 0.0), 1.0)


def normalize_knowledge_text(text: str) -> str:
    """去掉空白并把地市说法收成同一占位，便于比较口径而不是具体地区。"""
    cleaned = _WHITESPACE_RE.sub("", (text or "").strip())
    if not cleaned:
        return ""
    return _REGION_RE.sub("{地区}", cleaned)


def _relation_tables(relations: Sequence[KnowledgeRelation] | None) -> set[str]:
    tables: set[str] = set()
    for relation in relations or []:
        table_name = str(relation.table_name or "").strip().lower()
        if table_name:
            tables.add(table_name)
    return tables


def _relation_columns(relations: Sequence[KnowledgeRelation] | None) -> set[str]:
    columns: set[str] = set()
    for relation in relations or []:
        table_name = str(relation.table_name or "").strip().lower()
        if not table_name:
            continue
        column_names = [str(name).strip().lower() for name in (relation.column_names or []) if str(name).strip()]
        if not column_names:
            columns.add(table_name)
            continue
        for column_name in column_names:
            columns.add(f"{table_name}.{column_name}")
    return columns


def _jaccard(left: set[str], right: set[str], *, empty_is_one: bool = False) -> float:
    if not left and not right:
        return 1.0 if empty_is_one else 0.0
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def knowledge_item_similarity(
        *,
        name: str,
        description: str,
        relations: Sequence[KnowledgeRelation] | None = None,
        other_name: str,
        other_description: str,
        other_relations: Sequence[KnowledgeRelation] | None = None,
) -> float:
    """综合名称、口径说明和绑定表字段，估算两条业务知识的相似度。"""
    name_score = SequenceMatcher(
        None,
        normalize_knowledge_text(name),
        normalize_knowledge_text(other_name),
    ).ratio()
    desc_score = SequenceMatcher(
        None,
        normalize_knowledge_text(description),
        normalize_knowledge_text(other_description),
    ).ratio()
    left_tables = _relation_tables(relations)
    right_tables = _relation_tables(other_relations)
    left_columns = _relation_columns(relations)
    right_columns = _relation_columns(other_relations)
    if not left_tables and not right_tables:
        relation_score = 1.0
    elif not left_tables or not right_tables:
        relation_score = 1.0 if name_score >= 0.9 else 0.5
    else:
        relation_score = (
            0.6 * _jaccard(left_tables, right_tables)
            + 0.4 * _jaccard(left_columns, right_columns, empty_is_one=True)
        )
    return 0.45 * name_score + 0.35 * desc_score + 0.20 * relation_score


def is_similar_knowledge(
        *,
        name: str,
        description: str,
        relations: Sequence[KnowledgeRelation] | None = None,
        other_name: str,
        other_description: str,
        other_relations: Sequence[KnowledgeRelation] | None = None,
        threshold: float | None = None,
) -> tuple[bool, float]:
    cutoff = knowledge_merge_similarity_threshold() if threshold is None else threshold
    name_score = SequenceMatcher(
        None,
        normalize_knowledge_text(name),
        normalize_knowledge_text(other_name),
    ).ratio()
    score = knowledge_item_similarity(
        name=name,
        description=description,
        relations=relations,
        other_name=other_name,
        other_description=other_description,
        other_relations=other_relations,
    )
    left_tables = _relation_tables(relations)
    right_tables = _relation_tables(other_relations)
    overlapping_tables = bool(left_tables & right_tables) or (not left_tables or not right_tables)
    # 名称几乎相同且绑表不冲突时，即使说明措辞不同也视为同一口径。
    if name_score >= 0.88 and overlapping_tables and score >= max(0.62, cutoff - 0.18):
        return True, score
    return score >= cutoff, score


def find_best_similar_knowledge(
        *,
        name: str,
        description: str,
        relations: Sequence[KnowledgeRelation] | None,
        candidates: Sequence[tuple[KnowledgeNode, Sequence[KnowledgeRelation]]],
        exclude_uuid: str | None = None,
        threshold: float | None = None,
) -> tuple[KnowledgeNode, Sequence[KnowledgeRelation], float] | None:
    best_node: KnowledgeNode | None = None
    best_relations: Sequence[KnowledgeRelation] = []
    best_score = -1.0
    skip = (exclude_uuid or "").strip()
    for node, other_relations in candidates:
        if skip and node.uuid == skip:
            continue
        matched, score = is_similar_knowledge(
            name=name,
            description=description,
            relations=relations,
            other_name=node.name,
            other_description=node.description,
            other_relations=other_relations,
            threshold=threshold,
        )
        if not matched or score <= best_score:
            continue
        best_node = node
        best_relations = other_relations
        best_score = score
    if best_node is None:
        return None
    return best_node, best_relations, best_score


def merge_knowledge_description(existing: str, incoming: str) -> str:
    current = (existing or "").strip()
    extra = (incoming or "").strip()
    if not extra:
        return current
    if not current:
        return extra
    if extra in current:
        return current
    if current in extra:
        return extra
    return extra if len(extra) >= len(current) else current


def merge_knowledge_relations(
        existing: Sequence[KnowledgeRelation] | None,
        incoming: Sequence[KnowledgeRelation] | None,
) -> list[KnowledgeRelation]:
    columns_by_table: dict[str, set[str]] = {}
    for relation in list(existing or []) + list(incoming or []):
        table_name = str(relation.table_name or "").strip()
        if not table_name:
            continue
        columns_by_table.setdefault(table_name, set())
        for column_name in relation.column_names or []:
            cleaned = str(column_name).strip()
            if cleaned:
                columns_by_table[table_name].add(cleaned)
    return [
        KnowledgeRelation(table_name=table_name, column_names=sorted(column_names))
        for table_name, column_names in sorted(columns_by_table.items())
    ]
