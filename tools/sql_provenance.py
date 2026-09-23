"""执行结果轻量体检：空结果换表，排名题缺少分组则改写 SQL。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from sqlglot import exp, parse

_RANK_GROUP_RE = re.compile(
    r"("
    r"每个.{0,16}(供电所|单位|地区|县|市|所|班组|台区|线路|分类).{0,16}"
    r"(排名|排行|前\s*\d+|最多|最少|top|数量|户数|电量)"
    r"|按.{0,16}(供电所|单位|地区|县|市|所|分类).{0,12}"
    r"(排名|排行|统计|汇总|排序)"
    r"|(排名|排行榜|前\s*\d+\s*名)"
    r"|各(供电所|单位|地区|县市|分类).{0,12}(数量|户数|电量|排名|统计)"
    r")",
    re.I,
)
_DETAIL_HINT_RE = re.compile(r"(清单|明细|详情|列出)")


@dataclass(frozen=True)
class QueryHealth:
    retry_sql: bool = False
    reselect_tables: bool = False
    errors: tuple[str, ...] = ()


def inspect_query_health(
        *,
        question: str,
        sql: str,
        row_count: int,
        dialect: str = "mysql",
) -> QueryHealth:
    """执行后轻量体检：空结果考虑换表；排名题缺少分组则改写 SQL。"""
    if int(row_count or 0) <= 0:
        return QueryHealth(
            reselect_tables=True,
            errors=("查询结果为空，可能选表不完整或筛选过严。请补充或更换数据表后重试。",),
        )
    if question_expects_grouped_rank(question) and not sql_has_grouping(sql, dialect):
        return QueryHealth(
            retry_sql=True,
            errors=("问题要求按维度排名或汇总，但 SQL 未见 GROUP BY / 窗口函数，请按维度分组后排序。",),
        )
    return QueryHealth()


def question_expects_grouped_rank(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    if _DETAIL_HINT_RE.search(text) and not re.search(r"(排名|前\s*\d+|最多|最少|汇总|统计各)", text):
        return False
    return bool(_RANK_GROUP_RE.search(text))


def sql_has_grouping(sql: str, dialect: str = "mysql") -> bool:
    text = (sql or "").strip()
    if not text:
        return False
    read_dialect = (dialect or "mysql").strip().lower() or "mysql"
    try:
        statements = [item for item in parse(text, dialect=read_dialect) if item is not None]
    except Exception:
        return False
    for statement in statements:
        if statement.find(exp.Group) is not None:
            return True
        if statement.find(exp.Window) is not None:
            return True
    return False


def format_health_feedback(errors: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in errors if str(item).strip())
