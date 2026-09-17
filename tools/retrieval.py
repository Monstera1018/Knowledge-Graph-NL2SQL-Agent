from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Set

from pydantic import BaseModel, Field

from tools.graph.internal import _run_read, build_stable_column_uuid, build_stable_table_uuid
from tools.graph.model import EdgeType, NodeType
from tools.graph.crud.knowledge import get_knowledge, list_knowledge_relations
from tools.vector import VectorSearchHit, semantic_search


@dataclass
class _ColumnFocus:
    name: str
    comment: str = ""
    dtype: str = ""
    enums: List[str] = field(default_factory=list)


@dataclass
class _TableFocus:
    uuid: str
    name: str
    comment: str = ""
    description: str = ""
    columns: Dict[str, _ColumnFocus] = field(default_factory=dict)


class TableJoinContext(BaseModel):
    from_table_name: str
    to_table_name: str
    condition: str = ""


class KnowledgeEntry(BaseModel):
    name: str
    description: str
    related_tables: List[str] = Field(default_factory=list)


class SqlEntry(BaseModel):
    uuid: str = ""
    name: str
    logic: str
    content: str = ""
    dialect: str = ""
    related_tables: List[str] = Field(default_factory=list)
    from_hit: bool = False


MAX_SQL_REFERENCE_SAMPLES = 8


class RetrievalGraphBundle(BaseModel):
    """按表 → 字段 → 枚举组织的图谱上下文，并包含表关系、业务知识和 SQL。"""

    model_config = {"arbitrary_types_allowed": True}

    tables: List[_TableFocus] = Field(default_factory=list)
    joins: List[TableJoinContext] = Field(default_factory=list)
    knowledge: List[KnowledgeEntry] = Field(default_factory=list)
    sql: List[SqlEntry] = Field(default_factory=list)


class RetrievalSearchResult(BaseModel):
    context: str
    knowledge_hit_uuids: List[str] = Field(default_factory=list)
    unbound_knowledge_hit_uuids: List[str] = Field(default_factory=list)
    sql_hit_uuids: List[str] = Field(default_factory=list)


def dedupe_hits(hits: Sequence[VectorSearchHit]) -> List[VectorSearchHit]:
    best: Dict[str, VectorSearchHit] = {}
    for hit in hits:
        prev = best.get(hit.node_uuid)
        if prev is None or hit.distance > prev.distance:
            best[hit.node_uuid] = hit
    return sorted(best.values(), key=lambda h: h.distance, reverse=True)


def _ensure_table(
        tables: Dict[str, _TableFocus],
        *,
        uuid: str,
        name: str,
) -> _TableFocus:
    existing = tables.get(uuid)
    if existing is None:
        existing = _TableFocus(uuid=uuid, name=name)
        tables[uuid] = existing
    elif name and not existing.name:
        existing.name = name
    return existing


async def _hydrate_table_meta(tables: Dict[str, _TableFocus]) -> None:
    """为上下文中的每张表从图谱加载表说明和摘要。"""
    loaded = await _load_table_contexts(list(tables.keys()))
    for table_uuid, meta in loaded.items():
        table = tables[table_uuid]
        if meta.name:
            table.name = meta.name
        if meta.comment:
            table.comment = meta.comment
        if meta.description:
            table.description = meta.description


def _ensure_column(table: _TableFocus, column: _ColumnFocus) -> _ColumnFocus:
    existing = table.columns.get(column.name)
    if existing is None:
        table.columns[column.name] = column
        return column
    if column.comment and not existing.comment:
        existing.comment = column.comment
    if column.dtype and not existing.dtype:
        existing.dtype = column.dtype
    for enum_value in column.enums:
        if enum_value not in existing.enums:
            existing.enums.append(enum_value)
    return existing


async def _load_table_contexts(table_uuids: List[str]) -> Dict[str, _TableFocus]:
    unique = list(dict.fromkeys(table_uuids))
    if not unique:
        return {}

    rows = await _run_read(
        f"""
        UNWIND $uuids AS table_uuid
        MATCH (t:{NodeType.TABLE} {{uuid: table_uuid}})
        RETURN table_uuid, t
        """,
        uuids=unique,
    )

    tables: Dict[str, _TableFocus] = {}
    for row in rows:
        table = dict(row["t"])
        uuid = row["table_uuid"]
        tables[uuid] = _TableFocus(
            uuid=uuid,
            name=table.get("name", ""),
            comment=table.get("comment", ""),
            description=table.get("description", ""),
        )
    return tables


async def _load_full_table_subgraphs(table_uuids: List[str]) -> Dict[str, _TableFocus]:
    """从图谱加载表及其全部字段和枚举值。"""
    unique = list(dict.fromkeys(table_uuids))
    if not unique:
        return {}

    rows = await _run_read(
        f"""
        UNWIND $uuids AS table_uuid
        MATCH (t:{NodeType.TABLE} {{uuid: table_uuid}})
        OPTIONAL MATCH (t)-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        RETURN table_uuid, t, c, collect(e.value) AS enum_values
        ORDER BY c.name
        """,
        uuids=unique,
    )

    tables: Dict[str, _TableFocus] = {}
    column_seen: Dict[str, set[str]] = {}

    for row in rows:
        table_uuid = row["table_uuid"]
        table = dict(row["t"])
        if table_uuid not in tables:
            tables[table_uuid] = _TableFocus(
                uuid=table_uuid,
                name=table.get("name", ""),
                comment=table.get("comment", ""),
                description=table.get("description", ""),
            )
            column_seen[table_uuid] = set()

        column = row["c"]
        if column is None:
            continue

        column = dict(column)
        column_name = column.get("name", "")
        if column_name in column_seen[table_uuid]:
            continue
        column_seen[table_uuid].add(column_name)
        enum_values = sorted(v for v in row["enum_values"] if v)
        tables[table_uuid].columns[column_name] = _ColumnFocus(
            name=column_name,
            comment=column.get("comment", ""),
            dtype=column.get("dtype", ""),
            enums=enum_values,
        )

    return tables


async def _resolve_column_parents(
        column_uuids: List[str],
) -> Dict[str, tuple[_TableFocus, _ColumnFocus]]:
    unique = list(dict.fromkeys(column_uuids))
    if not unique:
        return {}

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        WHERE c.uuid IN $uuids
        RETURN c.uuid AS column_uuid, t, c
        """,
        uuids=unique,
    )

    result: Dict[str, tuple[_TableFocus, _ColumnFocus]] = {}
    for row in rows:
        table = dict(row["t"])
        column = dict(row["c"])
        table_ctx = _TableFocus(
            uuid=table["uuid"],
            name=table.get("name", ""),
            comment=table.get("comment", ""),
            description=table.get("description", ""),
        )
        column_ctx = _ColumnFocus(
            name=column.get("name", ""),
            comment=column.get("comment", ""),
            dtype=column.get("dtype", ""),
        )
        result[row["column_uuid"]] = (table_ctx, column_ctx)
    return result


async def _resolve_enum_parents(
        enum_uuids: List[str],
) -> Dict[str, tuple[_TableFocus, _ColumnFocus, str]]:
    unique = list(dict.fromkeys(enum_uuids))
    if not unique:
        return {}

    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WHERE e.uuid IN $uuids
        RETURN e.uuid AS enum_uuid, e.value AS enum_value, t, c
        """,
        uuids=unique,
    )

    result: Dict[str, tuple[_TableFocus, _ColumnFocus, str]] = {}
    for row in rows:
        table = dict(row["t"])
        column = dict(row["c"])
        table_ctx = _TableFocus(
            uuid=table["uuid"],
            name=table.get("name", ""),
            comment=table.get("comment", ""),
            description=table.get("description", ""),
        )
        column_ctx = _ColumnFocus(
            name=column.get("name", ""),
            comment=column.get("comment", ""),
            dtype=column.get("dtype", ""),
            enums=[row["enum_value"]],
        )
        result[row["enum_uuid"]] = (table_ctx, column_ctx, row["enum_value"])
    return result


async def _resolve_target_table_refs(target_uuids: List[str]) -> Dict[str, List[str]]:
    """将节点 uuid 映射为可读的表引用（表名或 表名.列名）。"""
    unique = list(dict.fromkeys(target_uuids))
    if not unique:
        return {}

    rows = await _run_read(
        f"""
        UNWIND $uuids AS target_uuid
        MATCH (target {{uuid: target_uuid}})
        OPTIONAL MATCH (t_table:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN} {{uuid: target_uuid}})
        OPTIONAL MATCH (t_enum:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c_enum:{NodeType.COLUMN})
              -[:{EdgeType.HAS}]->(e:{NodeType.ENUM} {{uuid: target_uuid}})
        RETURN target_uuid, labels(target) AS target_labels, target,
               t_table.name AS table_name, c.name AS column_name,
               t_enum.name AS enum_table_name, c_enum.name AS enum_column_name
        """,
        uuids=unique,
    )

    refs: Dict[str, List[str]] = {}
    for row in rows:
        target_uuid = row["target_uuid"]
        labels = list(row["target_labels"])
        target = dict(row["target"])

        if NodeType.TABLE.value in labels:
            refs[target_uuid] = [target.get("name", "")]
        elif NodeType.COLUMN.value in labels and row.get("table_name"):
            refs[target_uuid] = [
                f"{row['table_name']}.{row.get('column_name') or target.get('name', '')}"
            ]
        elif NodeType.ENUM.value in labels and row.get("enum_table_name"):
            refs[target_uuid] = [
                f"{row['enum_table_name']}.{row.get('enum_column_name', '')}"
            ]
        else:
            refs[target_uuid] = [target.get("name", "")] if target.get("name") else []
    return refs


async def _fetch_joins(table_uuids: List[str]) -> List[TableJoinContext]:
    unique = list(dict.fromkeys(table_uuids))
    if not unique:
        return []

    rows = await _run_read(
        f"""
        MATCH (a:{NodeType.TABLE})-[r:{EdgeType.JOINS}]->(b:{NodeType.TABLE})
        WHERE a.uuid IN $uuids OR b.uuid IN $uuids
        RETURN a.name AS from_name, b.name AS to_name, coalesce(r.condition, '') AS condition
        """,
        uuids=unique,
    )

    joins: List[TableJoinContext] = []
    seen: Set[tuple[str, str, str]] = set()
    for row in rows:
        join = TableJoinContext(
            from_table_name=row["from_name"],
            to_table_name=row["to_name"],
            condition=row["condition"],
        )
        key = (join.from_table_name, join.to_table_name, join.condition)
        if key in seen:
            continue
        seen.add(key)
        joins.append(join)
    return joins


async def _fetch_knowledge_entries(
        knowledge_uuids: List[str],
        target_uuids: List[str],
) -> List[KnowledgeEntry]:
    unique_k = list(dict.fromkeys(knowledge_uuids))
    unique_t = list(dict.fromkeys(target_uuids))
    if not unique_k and not unique_t:
        return []

    rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        WHERE k.uuid IN $knowledge_uuids
        OPTIONAL MATCH (k)-[:{EdgeType.DESCRIBES}]->(target)
        RETURN DISTINCT k.uuid AS knowledge_uuid,
               k.name AS name,
               k.description AS description,
               target.uuid AS target_uuid
        UNION
        MATCH (k:{NodeType.KNOWLEDGE})-[:{EdgeType.DESCRIBES}]->(target)
        WHERE target.uuid IN $target_uuids
        RETURN DISTINCT k.uuid AS knowledge_uuid,
               k.name AS name,
               k.description AS description,
               target.uuid AS target_uuid
        """,
        knowledge_uuids=unique_k,
        target_uuids=unique_t,
    )

    target_refs = await _resolve_target_table_refs(
        [row["target_uuid"] for row in rows if row.get("target_uuid")]
    )

    grouped: Dict[str, KnowledgeEntry] = {}
    for row in rows:
        kid = row["knowledge_uuid"]
        entry = grouped.get(kid)
        if entry is None:
            entry = KnowledgeEntry(name=row["name"], description=row["description"])
            grouped[kid] = entry
        for ref in target_refs.get(row["target_uuid"], []):
            if ref and ref not in entry.related_tables:
                entry.related_tables.append(ref)

    return sorted(grouped.values(), key=lambda x: x.name)


async def _filter_unbound_knowledge_uuids(knowledge_uuids: Sequence[str]) -> List[str]:
    unique = list(dict.fromkeys(uuid for uuid in knowledge_uuids if uuid))
    if not unique:
        return []

    rows = await _run_read(
        f"""
        MATCH (k:{NodeType.KNOWLEDGE})
        WHERE k.uuid IN $knowledge_uuids
          AND NOT (k)-[:{EdgeType.DESCRIBES}]->()
        RETURN k.uuid AS knowledge_uuid
        """,
        knowledge_uuids=unique,
    )
    found = {row["knowledge_uuid"] for row in rows}
    return [uuid for uuid in unique if uuid in found]


def _sql_entry_key(entry: SqlEntry) -> str:
    uuid = (entry.uuid or "").strip()
    if uuid:
        return uuid
    return f"{entry.name}\0{entry.content}"


def merge_sql_entries(
        hit_entries: Sequence[SqlEntry],
        graph_entries: Sequence[SqlEntry],
        *,
        limit: int = MAX_SQL_REFERENCE_SAMPLES,
) -> List[SqlEntry]:
    """向量命中优先，再用图谱补全去重合并；限制带给模型的样例条数。"""
    merged: List[SqlEntry] = []
    seen: Set[str] = set()
    for entry in [*hit_entries, *graph_entries]:
        key = _sql_entry_key(entry)
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(entry)
        if len(merged) >= limit:
            break
    return merged


def _table_name_from_ref(ref: str) -> str:
    text = (ref or "").strip()
    if not text:
        return ""
    return text.split(".", 1)[0].strip()


async def _sql_entries_from_rows(
        rows: Sequence[dict],
        *,
        ordered_uuids: Sequence[str] | None = None,
        from_hit: bool = False,
) -> List[SqlEntry]:
    target_refs = await _resolve_target_table_refs(
        [row["target_uuid"] for row in rows if row.get("target_uuid")]
    )
    grouped: Dict[str, SqlEntry] = {}
    for row in rows:
        sid = str(row.get("sql_uuid") or "").strip()
        if not sid:
            continue
        entry = grouped.get(sid)
        if entry is None:
            entry = SqlEntry(
                uuid=sid,
                name=str(row.get("name") or "").strip(),
                logic=str(row.get("logic") or ""),
                content=str(row.get("content") or ""),
                dialect=str(row.get("dialect") or ""),
                from_hit=from_hit,
            )
            grouped[sid] = entry
        for ref in target_refs.get(row.get("target_uuid"), []):
            if ref and ref not in entry.related_tables:
                entry.related_tables.append(ref)

    if ordered_uuids is not None:
        ordered: List[SqlEntry] = []
        seen: Set[str] = set()
        for sql_uuid in ordered_uuids:
            entry = grouped.get(sql_uuid)
            if entry is None or sql_uuid in seen:
                continue
            seen.add(sql_uuid)
            ordered.append(entry)
        return ordered
    return sorted(grouped.values(), key=lambda item: item.name)


async def _load_sql_entries_by_uuids(
        sql_uuids: Sequence[str],
        *,
        from_hit: bool = False,
) -> List[SqlEntry]:
    unique = list(dict.fromkeys((item or "").strip() for item in sql_uuids if (item or "").strip()))
    if not unique:
        return []

    rows = await _run_read(
        f"""
        UNWIND $uuids AS sql_uuid
        MATCH (s:{NodeType.SQL} {{uuid: sql_uuid}})
        WHERE coalesce(s.enabled, true) = true
        OPTIONAL MATCH (s)-[:{EdgeType.USES}]->(target)
        RETURN s.uuid AS sql_uuid, s.name AS name, s.logic AS logic,
               s.content AS content, s.dialect AS dialect,
               target.uuid AS target_uuid
        """,
        uuids=unique,
    )
    return await _sql_entries_from_rows(rows, ordered_uuids=unique, from_hit=from_hit)


async def _fetch_sql_entries(target_uuids: List[str]) -> List[SqlEntry]:
    unique = list(dict.fromkeys(target_uuids))
    if not unique:
        return []

    rows = await _run_read(
        f"""
        MATCH (s:{NodeType.SQL})-[:{EdgeType.USES}]->(target)
        WHERE target.uuid IN $uuids
          AND coalesce(s.enabled, true) = true
        OPTIONAL MATCH (s)-[:{EdgeType.USES}]->(used)
        RETURN DISTINCT s.uuid AS sql_uuid, s.name AS name, s.logic AS logic,
               s.content AS content, s.dialect AS dialect,
               used.uuid AS target_uuid
        """,
        uuids=unique,
    )
    return await _sql_entries_from_rows(rows)


def _relation_table_refs(relations: Sequence) -> List[str]:
    refs: List[str] = []
    for relation in relations:
        if relation.column_names:
            for column_name in relation.column_names:
                ref = f"{relation.table_name}.{column_name}"
                if ref not in refs:
                    refs.append(ref)
        elif relation.table_name not in refs:
            refs.append(relation.table_name)
    return refs


async def organize_graph_from_hits(hits: Sequence[VectorSearchHit]) -> RetrievalGraphBundle:
    """将召回结果合并为表 → 字段 → 枚举树，并补充表关系、业务知识和 SQL。"""
    unique_hits = dedupe_hits(hits)
    if not unique_hits:
        return RetrievalGraphBundle()

    workspace_id = unique_hits[0].workspace_id
    tables: Dict[str, _TableFocus] = {}
    knowledge_hit_uuids: List[str] = []
    sql_hit_uuids: List[str] = []
    anchor_uuids: List[str] = []

    column_hit_uuids = [h.node_uuid for h in unique_hits if h.node_type == NodeType.COLUMN]
    enum_hit_uuids = [h.node_uuid for h in unique_hits if h.node_type == NodeType.ENUM]

    column_parents = await _resolve_column_parents(column_hit_uuids)
    enum_parents = await _resolve_enum_parents(enum_hit_uuids)

    for hit in unique_hits:
        if hit.node_type == NodeType.KNOWLEDGE:
            knowledge_hit_uuids.append(hit.node_uuid)
            anchor_uuids.append(hit.node_uuid)
        if hit.node_type == NodeType.SQL:
            sql_hit_uuids.append(hit.node_uuid)
        if hit.node_type == NodeType.TABLE:
            _ensure_table(tables, uuid=hit.node_uuid, name="")
            anchor_uuids.append(hit.node_uuid)

    sql_hit_uuids = list(dict.fromkeys(sql_hit_uuids))
    hit_sql_entries = await _load_sql_entries_by_uuids(sql_hit_uuids, from_hit=True)
    for entry in hit_sql_entries:
        for ref in entry.related_tables:
            table_name = _table_name_from_ref(ref)
            if not table_name:
                continue
            stub_uuid = build_stable_table_uuid(workspace_id, table_name)
            if stub_uuid not in tables:
                _ensure_table(tables, uuid=stub_uuid, name=table_name)
            anchor_uuids.append(stub_uuid)

    for column_uuid, (parent_table, column) in column_parents.items():
        table = _ensure_table(tables, uuid=parent_table.uuid, name=parent_table.name)
        _ensure_column(table, column)
        anchor_uuids.extend([table.uuid, column_uuid])

    for enum_uuid, (parent_table, column, enum_value) in enum_parents.items():
        table = _ensure_table(tables, uuid=parent_table.uuid, name=parent_table.name)
        col = _ensure_column(table, column)
        if enum_value not in col.enums:
            col.enums.append(enum_value)
        anchor_uuids.extend([table.uuid, enum_uuid])
        anchor_uuids.append(
            build_stable_column_uuid(workspace_id, table.name, column.name)
        )

    for table in tables.values():
        for column in table.columns.values():
            anchor_uuids.append(
                build_stable_column_uuid(workspace_id, table.name, column.name)
            )

    extra_table_uuids: List[str] = []
    for knowledge_uuid in knowledge_hit_uuids:
        node = await get_knowledge(knowledge_uuid)
        relations = await list_knowledge_relations(knowledge_uuid)
        for relation in relations:
            extra_table_uuids.append(
                build_stable_table_uuid(workspace_id, relation.table_name)
            )
        for table_name in _relation_table_refs(relations):
            stub_uuid = build_stable_table_uuid(workspace_id, table_name.split(".", 1)[0])
            if stub_uuid not in tables:
                _ensure_table(tables, uuid=stub_uuid, name=table_name.split(".", 1)[0])

    for table_uuid in extra_table_uuids:
        if table_uuid not in tables:
            _ensure_table(tables, uuid=table_uuid, name="")

    await _hydrate_table_meta(tables)

    table_uuid_set = list(tables.keys())
    anchor_uuids = list(dict.fromkeys(u for u in anchor_uuids if u))

    joins = await _fetch_joins(table_uuid_set)
    knowledge = await _fetch_knowledge_entries(knowledge_hit_uuids, anchor_uuids)
    graph_sql_entries = await _fetch_sql_entries(anchor_uuids)
    sql_entries = merge_sql_entries(hit_sql_entries, graph_sql_entries)

    sorted_tables = sorted(tables.values(), key=lambda t: t.name)
    for table in sorted_tables:
        table.columns = dict(sorted(table.columns.items(), key=lambda item: item[0]))

    return RetrievalGraphBundle(
        tables=sorted_tables,
        joins=joins,
        knowledge=knowledge,
        sql=sql_entries,
    )


async def load_selected_tables_bundle(
        workspace_id: str,
        table_names: Sequence[str],
        extra_knowledge_uuids: Sequence[str] | None = None,
        extra_sql_uuids: Sequence[str] | None = None,
) -> RetrievalGraphBundle:
    """为已选表加载完整的表 → 字段 → 枚举子图及相关业务知识、历史 SQL。"""
    names = list(dict.fromkeys((n or "").strip().lower() for n in table_names if (n or "").strip()))
    if not names:
        return RetrievalGraphBundle()

    table_uuids = [build_stable_table_uuid(workspace_id, name) for name in names]
    tables = await _load_full_table_subgraphs(table_uuids)

    anchor_uuids: List[str] = []
    for table in tables.values():
        anchor_uuids.append(table.uuid)
        for column_name in table.columns:
            anchor_uuids.append(
                build_stable_column_uuid(workspace_id, table.name, column_name)
            )
    anchor_uuids = list(dict.fromkeys(anchor_uuids))

    joins = await _fetch_joins(list(tables.keys()))
    knowledge = await _fetch_knowledge_entries(list(extra_knowledge_uuids or []), anchor_uuids)
    hit_sql_entries = await _load_sql_entries_by_uuids(
        list(extra_sql_uuids or []),
        from_hit=True,
    )
    graph_sql_entries = await _fetch_sql_entries(anchor_uuids)
    sql_entries = merge_sql_entries(hit_sql_entries, graph_sql_entries)

    sorted_tables = sorted(tables.values(), key=lambda t: t.name)
    for table in sorted_tables:
        table.columns = dict(sorted(table.columns.items(), key=lambda item: item[0]))

    return RetrievalGraphBundle(
        tables=sorted_tables,
        joins=joins,
        knowledge=knowledge,
        sql=sql_entries,
    )


def _md_inline(text: str) -> str:
    return text.replace("\n", " ").strip()


def _format_column_line(column: _ColumnFocus) -> str:
    label = f"**{column.name}**"
    if column.dtype:
        label += f"（`{column.dtype}`）"
    details: List[str] = []
    if column.comment:
        details.append(_md_inline(column.comment))
    if column.enums:
        details.append("枚举 " + "、".join(f"`{v}`" for v in column.enums))
    if details:
        label += "：" + "；".join(details)
    return f"  - {label}"


def _format_table_block(table: _TableFocus) -> List[str]:
    lines = [f"- **{table.name}**"]
    if table.comment:
        lines.append(f"  - 说明：{_md_inline(table.comment)}")
    if table.description:
        lines.append(f"  - 摘要：{_md_inline(table.description)}")
    for column in table.columns.values():
        lines.append(_format_column_line(column))
    return lines


def _section_block(label: str, body_lines: List[str]) -> str:
    if not body_lines:
        return ""
    return "\n".join([f"**{label}**", ""] + body_lines)


def _format_sql_entry(item: SqlEntry) -> List[str]:
    """将一条历史 SQL 格式化为名称、逻辑、关联表和语句。"""
    name = _md_inline(item.name) or "未命名样例"
    logic = _md_inline(item.logic) if (item.logic or "").strip() else "（无说明）"
    line = f"- **{name}**：{logic}"
    if item.related_tables:
        refs = "、".join(f"`{r}`" for r in item.related_tables)
        line += f"（关联 {refs}）"
    lines = [line]
    sql_text = (item.content or "").strip()
    if sql_text:
        lines.extend(["", "```sql", sql_text, "```"])
    return lines


_SQL_SAMPLE_RE = re.compile(
    r"^- \*\*(?P<name>.+?)\*\*：(?P<body>.*?)(?=^- \*\*|\Z)",
    re.DOTALL | re.MULTILINE,
)


def parse_sql_reference_samples(body: str) -> List[dict[str, object]]:
    """从 SQL 分区 Markdown 解析出名称、逻辑、关联表和语句。"""
    samples: List[dict[str, object]] = []
    for match in _SQL_SAMPLE_RE.finditer((body or "").strip()):
        name = (match.group("name") or "").strip()
        rest = (match.group("body") or "").strip()
        sql_match = re.search(r"```sql\s*\n(.*?)```", rest, re.DOTALL)
        content = sql_match.group(1).strip() if sql_match else ""
        logic = rest[: sql_match.start()].strip() if sql_match else rest
        related: List[str] = []
        rel_match = re.search(r"（关联\s*(.+?)）\s*$", logic)
        if rel_match:
            related = [
                part.strip().strip("`")
                for part in rel_match.group(1).split("、")
                if part.strip()
            ]
            logic = logic[: rel_match.start()].strip()
        samples.append({
            "name": name,
            "logic": logic or "（无说明）",
            "content": content,
            "related_tables": related,
        })
    return samples


def format_graph_context_for_llm(
        bundle: RetrievalGraphBundle,
        *,
        sql_section_note: str = "",
) -> str:
    """将检索包格式化为紧凑 Markdown（列表 + 加粗，不堆叠多级标题）。"""
    if not (
            bundle.tables
            or bundle.joins
            or bundle.knowledge
            or bundle.sql
    ):
        return ""

    parts: List[str] = []

    if bundle.tables:
        table_lines: List[str] = []
        for table in bundle.tables:
            table_lines.extend(_format_table_block(table))
        parts.append(_section_block("数据表", table_lines))

    if bundle.joins:
        join_lines = []
        for join in bundle.joins:
            line = f"- {join.from_table_name} → {join.to_table_name}"
            if join.condition:
                line += f"：`{_md_inline(join.condition)}`"
            join_lines.append(line)
        parts.append(_section_block("表关系", join_lines))

    if bundle.knowledge:
        knowledge_lines = []
        for item in bundle.knowledge:
            line = f"- **{item.name}**：{_md_inline(item.description)}"
            if item.related_tables:
                refs = "、".join(f"`{r}`" for r in item.related_tables)
                line += f"（关联 {refs}）"
            else:
                line += "（未绑定表字段）"
            knowledge_lines.append(line)
        parts.append(_section_block("业务知识", knowledge_lines))

    if bundle.sql:
        sql_lines: List[str] = []
        note = (sql_section_note or "").strip()
        if note:
            sql_lines.extend([note, ""])
        for item in bundle.sql:
            if sql_lines and sql_lines[-1] != "":
                sql_lines.append("")
            sql_lines.extend(_format_sql_entry(item))
        parts.append(_section_block("SQL", sql_lines))

    return "\n\n".join(p for p in parts if p)


def format_sql_generation_context(
        bundle: RetrievalGraphBundle,
        *,
        user_message: str = "",
        selection_reason: str = "",
        selected_tables: Sequence[str] | None = None,
) -> str:
    """根据候选表构造 SQL 生成使用的 Markdown 表结构上下文。"""
    header_parts: List[str] = []

    if user_message.strip():
        header_parts.append("\n".join(["**用户问题**", "", user_message.strip()]))
    if selection_reason.strip():
        header_parts.append("\n".join(["**选表说明**", "", selection_reason.strip()]))
    if selected_tables:
        refs = "、".join(f"`{name}`" for name in selected_tables)
        header_parts.append("\n".join(["**候选表**", "", refs]))

    schema_body = format_graph_context_for_llm(
        bundle,
        sql_section_note=(
            "下列历史 SQL 只用于学习表关联和字段写法；"
            "筛选条件以用户问题为准，不要把样例中用户未提到的剔除规则、账期或额外过滤抄进本轮 SQL。"
        ),
    )
    if not schema_body:
        schema_body = "（未在图谱中找到候选表的元数据）"

    parts = header_parts + [
        "\n".join(["**库表结构（供生成 SQL）**", "", schema_body]),
    ]
    return "\n\n".join(p for p in parts if p)


async def build_sql_schema_context(
        workspace_id: str,
        table_names: Sequence[str],
        *,
        user_message: str = "",
        selection_reason: str = "",
        extra_knowledge_uuids: Sequence[str] | None = None,
        extra_sql_uuids: Sequence[str] | None = None,
) -> str:
    names = list(table_names)
    bundle = await load_selected_tables_bundle(
        workspace_id,
        names,
        extra_knowledge_uuids=extra_knowledge_uuids,
        extra_sql_uuids=extra_sql_uuids,
    )
    return format_sql_generation_context(
        bundle,
        user_message=user_message,
        selection_reason=selection_reason,
        selected_tables=names,
    )


async def build_llm_context_from_hits(hits: Sequence[VectorSearchHit]) -> str:
    bundle = await organize_graph_from_hits(hits)
    return format_graph_context_for_llm(bundle)


async def search_and_build_llm_context(
        queries: str | Sequence[str],
        workspace_id: str,
        limit: int = 10,
        node_types: Sequence[NodeType] | None = None,
) -> str:
    result = await search_and_build_llm_context_with_hits(
        queries,
        workspace_id=workspace_id,
        limit=limit,
        node_types=node_types,
    )
    return result.context


async def search_and_build_llm_context_with_hits(
        queries: str | Sequence[str],
        workspace_id: str,
        limit: int = 10,
        node_types: Sequence[NodeType] | None = None,
) -> RetrievalSearchResult:
    results = await semantic_search(
        queries,
        workspace_id,
        limit=limit,
        node_types=node_types,
    )
    if isinstance(results, list) and results and isinstance(results[0], list):
        hits = [hit for batch in results for hit in batch]
    else:
        hits = list(results)
    unique_hits = dedupe_hits(hits)
    knowledge_hit_uuids = [
        hit.node_uuid
        for hit in unique_hits
        if hit.node_type == NodeType.KNOWLEDGE
    ]
    sql_hit_uuids = list(dict.fromkeys(
        hit.node_uuid
        for hit in unique_hits
        if hit.node_type == NodeType.SQL
    ))
    return RetrievalSearchResult(
        context=await build_llm_context_from_hits(unique_hits),
        knowledge_hit_uuids=knowledge_hit_uuids,
        unbound_knowledge_hit_uuids=await _filter_unbound_knowledge_uuids(knowledge_hit_uuids),
        sql_hit_uuids=sql_hit_uuids,
    )
