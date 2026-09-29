"""从已审核 SQL 样例抽取业务理解类知识。"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from tools.graph.crud.knowledge import (
    collapse_similar_knowledge,
    list_knowledge,
    upsert_extracted_knowledge,
)
from tools.graph.crud.sql import get_sql, list_sql_relations, update_sql
from tools.graph.model import (
    ConflictError,
    KnowledgeRelation,
    SQLNode,
    SQLReviewStatus,
)
from tools.llm import chat

logger = logging.getLogger(__name__)

MAX_EXTRACTED_KNOWLEDGE = 5
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*", re.IGNORECASE)

KNOWLEDGE_EXTRACT_SYSTEM_PROMPT = """你是电力营销数据域的业务知识审核助手。
你的任务是从一条已经人工审核通过的 SQL 样例中，抽取“业务理解”类知识，供后续问数对照。

只允许抽取这些内容：
- 用户说法到库内存储值的术语映射，例如「宁波公司」对应地区字段存「宁波地区」。
- 业务口径落地：某个业务词对应哪张表的哪个字段、以及闭集取值。
- 枚举/分类的中文名称与库内取值对应关系。

禁止抽取：
- JOIN 路径、表关联写法、SQL 语句本身。
- 样例里出现、但用户问题未体现的额外过滤、账期、定量定比剔除规则。
- 一次性查询结果、排名数字、具体清单行。
- 把整段逻辑说明复述成知识。

如果样例里没有稳定的业务映射，返回空数组。
不要重复已有知识名称对应的同一口径；同一术语映射只输出一条。
最多输出 5 条。只输出 JSON 数组，不要解释。每项格式：
{"name":"短名称","description":"把业务词如何落到字段和取值写清楚","relations":[{"table_name":"物理表名","column_names":["字段名"]}]}
表名必须来自给定候选表；没有把握绑表时 relations 可以为空。"""


def parse_extracted_knowledge_items(text: str) -> list[dict[str, Any]]:
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if "```" in cleaned:
        cleaned = _JSON_FENCE_RE.sub("", cleaned).strip().rstrip("`").strip()

    parsed: Any = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("[")
        end = cleaned.rfind("]")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
        if parsed is None:
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    parsed = json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    return []

    if isinstance(parsed, dict):
        parsed = parsed.get("items") or parsed.get("knowledge") or []
    if not isinstance(parsed, list):
        return []

    items: list[dict[str, Any]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        description = str(raw.get("description") or "").strip()
        if not name or not description:
            continue
        relations: list[dict[str, Any]] = []
        for rel in raw.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            table_name = str(rel.get("table_name") or "").strip()
            if not table_name:
                continue
            column_names: list[str] = []
            for column in rel.get("column_names") or []:
                column_name = str(column or "").strip()
                if column_name:
                    column_names.append(column_name)
            relations.append({"table_name": table_name, "column_names": column_names})
        items.append({"name": name, "description": description, "relations": relations})
        if len(items) >= MAX_EXTRACTED_KNOWLEDGE:
            break
    return items


def _filter_relations(
        relations: list[dict[str, Any]],
        allowed_tables: set[str],
) -> list[KnowledgeRelation]:
    if not allowed_tables:
        return []
    allowed = {name.lower(): name for name in allowed_tables}
    filtered: list[KnowledgeRelation] = []
    for relation in relations:
        table_name = str(relation.get("table_name") or "").strip()
        canonical = allowed.get(table_name.lower())
        if not canonical:
            continue
        column_names = [
            str(name).strip()
            for name in (relation.get("column_names") or [])
            if str(name).strip()
        ]
        filtered.append(KnowledgeRelation(table_name=canonical, column_names=column_names))
    return filtered


def should_extract_knowledge_from_sql(before: SQLNode, after: SQLNode) -> bool:
    if after.review_status != SQLReviewStatus.APPROVED or after.knowledge_extracted:
        return False
    became_approved = (
        before.review_status == SQLReviewStatus.PENDING
        and after.review_status == SQLReviewStatus.APPROVED
    )
    turned_on = (not before.enabled) and after.enabled
    return became_approved or turned_on


async def _existing_knowledge_names(workspace_id: str) -> str:
    page = await list_knowledge(workspace_id, page=1, page_size=10000, sort="name")
    names: list[str] = []
    seen: set[str] = set()
    for item in page.items:
        name = (item.name or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
        if len(names) >= 80:
            break
    return "、".join(names)


async def extract_knowledge_from_approved_sql(workspace_id: str, sql: SQLNode) -> int:
    """从已审核 SQL 抽取业务知识，默认停用等待二次审核。成功后打上已抽取标记。"""
    try:
        collapsed = await collapse_similar_knowledge(workspace_id)
        if collapsed:
            logger.info("抽取前合并相似业务知识 %s 条", collapsed)
    except Exception:
        logger.exception("抽取前合并相似业务知识失败")

    relations = await list_sql_relations(sql.uuid)
    allowed_tables = {item.table_name for item in relations}
    table_hint = "、".join(sorted(allowed_tables)) if allowed_tables else "无"
    existing_names = await _existing_knowledge_names(workspace_id)

    user_prompt = (
        f"样例名称：{sql.name}\n"
        f"逻辑说明：{sql.logic or '无'}\n"
        f"候选表：{table_hint}\n"
        f"已有知识名称（同一口径不要再输出）：{existing_names or '无'}\n"
        f"SQL：\n{sql.content}"
    )
    raw = await chat(user_prompt, system_prompt=KNOWLEDGE_EXTRACT_SYSTEM_PROMPT)
    created = 0
    for item in parse_extracted_knowledge_items(raw):
        knowledge_relations = _filter_relations(item["relations"], allowed_tables)
        try:
            _node, action = await upsert_extracted_knowledge(
                workspace_id,
                name=item["name"],
                description=item["description"],
                relations=knowledge_relations,
                source_sql_uuid=sql.uuid,
            )
            if action == "created":
                created += 1
            else:
                logger.info(
                    "抽取知识已%s：%s",
                    "合并" if action == "merged" else "跳过",
                    item["name"],
                )
        except ConflictError:
            logger.info("跳过已存在的抽取知识：%s", item["name"])
        except Exception:
            logger.exception("写入抽取知识失败：%s", item["name"])

    await update_sql(
        workspace_id,
        sql.uuid,
        knowledge_extracted=True,
        preserve_uuid=True,
    )
    return created


async def maybe_extract_knowledge_from_sql(
        workspace_id: str,
        before: SQLNode,
        after: SQLNode,
) -> SQLNode:
    if not should_extract_knowledge_from_sql(before, after):
        return after
    try:
        created = await extract_knowledge_from_approved_sql(workspace_id, after)
        logger.info(
            "SQL %s 审核通过后抽取业务知识 %s 条",
            after.uuid,
            created,
        )
    except Exception:
        logger.exception("SQL %s 审核通过后抽取业务知识失败", after.uuid)
        return after
    return await get_sql(after.uuid)


_EXTRACTING_SQL_UUIDS: set[str] = set()


def schedule_knowledge_extract_from_sql(
        add_background_task,
        workspace_id: str,
        before: SQLNode,
        after: SQLNode,
) -> bool:
    """审核接口先返回；知识抽取放到响应后的后台任务，避免页面卡住。"""
    if not should_extract_knowledge_from_sql(before, after):
        return False
    sql_uuid = (after.uuid or "").strip()
    if not sql_uuid or sql_uuid in _EXTRACTING_SQL_UUIDS:
        return False
    _EXTRACTING_SQL_UUIDS.add(sql_uuid)

    async def _run() -> None:
        try:
            await maybe_extract_knowledge_from_sql(workspace_id, before, after)
        finally:
            _EXTRACTING_SQL_UUIDS.discard(sql_uuid)

    add_background_task(_run)
    return True
