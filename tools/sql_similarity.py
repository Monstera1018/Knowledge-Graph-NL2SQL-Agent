from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from typing import Sequence

from sqlglot import exp, parse

from tools.graph.model import SQLNode
from tools.sql import find_all_sources

_DEFAULT_MERGE_THRESHOLD = 0.84


def sql_merge_similarity_threshold() -> float:
    raw = (os.environ.get("SQL_MERGE_SIMILARITY_THRESHOLD") or "").strip()
    if not raw:
        return _DEFAULT_MERGE_THRESHOLD
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_MERGE_THRESHOLD
    return min(max(value, 0.0), 1.0)


def normalize_sql_for_similarity(content: str, dialect: str = "") -> str:
    text = (content or "").strip()
    if not text:
        return ""
    read_dialect = (dialect or "mysql").strip().lower() or "mysql"
    try:
        statements = [item for item in parse(text, dialect=read_dialect) if item is not None]
        if statements:
            return ";\n".join(item.sql(dialect=read_dialect).strip() for item in statements)
    except Exception:
        pass
    return re.sub(r"\s+", " ", text).strip().lower()


def extract_sql_literals(content: str, dialect: str = "") -> set[str]:
    text = (content or "").strip()
    if not text:
        return set()
    read_dialect = (dialect or "mysql").strip().lower() or "mysql"
    values: set[str] = set()
    try:
        statements = [item for item in parse(text, dialect=read_dialect) if item is not None]
    except Exception:
        return set()
    for statement in statements:
        for literal in statement.find_all(exp.Literal):
            raw = str(literal.this or "").strip().lower()
            if raw:
                values.add(raw)
    return values


def extract_sql_tables(content: str, dialect: str = "") -> set[str]:
    read_dialect = (dialect or "mysql").strip().lower() or "mysql"
    table_and_columns, _error = find_all_sources(content or "", read_dialect)
    if not table_and_columns:
        return set()
    return {str(name).strip().lower() for name in table_and_columns if str(name).strip()}


def _jaccard(left: set[str], right: set[str], *, empty_is_one: bool = False) -> float:
    if not left and not right:
        return 1.0 if empty_is_one else 0.0
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union)


def sql_sample_similarity(
        *,
        name: str,
        content: str,
        dialect: str,
        other_name: str,
        other_content: str,
        other_dialect: str,
) -> float:
    """综合规范化 SQL、表集合、字面量和名称，估算两条样例的相似度。"""
    sql_ratio = SequenceMatcher(
        None,
        normalize_sql_for_similarity(content, dialect),
        normalize_sql_for_similarity(other_content, other_dialect),
    ).ratio()
    table_score = _jaccard(
        extract_sql_tables(content, dialect),
        extract_sql_tables(other_content, other_dialect),
    )
    literal_score = _jaccard(
        extract_sql_literals(content, dialect),
        extract_sql_literals(other_content, other_dialect),
        empty_is_one=True,
    )
    name_score = SequenceMatcher(
        None,
        (name or "").strip().lower(),
        (other_name or "").strip().lower(),
    ).ratio()
    return (
        0.50 * sql_ratio
        + 0.20 * table_score
        + 0.20 * literal_score
        + 0.10 * name_score
    )


def find_best_similar_sql(
        *,
        name: str,
        content: str,
        dialect: str,
        candidates: Sequence[SQLNode],
        exclude_uuid: str | None = None,
        threshold: float | None = None,
) -> tuple[SQLNode, float] | None:
    cutoff = sql_merge_similarity_threshold() if threshold is None else threshold
    best: SQLNode | None = None
    best_score = -1.0
    skip = (exclude_uuid or "").strip()
    for item in candidates:
        if skip and item.uuid == skip:
            continue
        score = sql_sample_similarity(
            name=name,
            content=content,
            dialect=dialect,
            other_name=item.name,
            other_content=item.content,
            other_dialect=item.dialect,
        )
        if score > best_score:
            best = item
            best_score = score
    if best is None or best_score < cutoff:
        return None
    return best, best_score


def merge_sql_logic(existing: str, incoming: str) -> str:
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
