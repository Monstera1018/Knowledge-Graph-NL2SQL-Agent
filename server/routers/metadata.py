from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from tools.graph import (
    MAX_LIST_PAGE_SIZE,
    KnowledgeRelation,
    create_column,
    create_enum,
    create_knowledge,
    create_sql,
    create_table,
    delete_column,
    delete_enum,
    delete_knowledge,
    delete_sql,
    delete_table,
    get_column,
    get_enum,
    get_knowledge,
    get_sql,
    get_table,
    list_all_table_join_relations,
    list_columns,
    list_enum_items as list_enum_items_with_context,
    list_enums,
    list_knowledge,
    list_knowledge_relations,
    list_sql_relations,
    list_sqls,
    list_tables,
    list_table_join_relations,
    NotFoundError,
    replace_table_join_relations,
    TableJoinSpec,
    update_column,
    update_enum,
    update_knowledge,
    update_sql,
    update_table,
)
from server.schemas.common import Page
from server.auth.deps import get_workspace_id
from tools.graph.model import ColumnNode, EnumListItem, EnumNode, KnowledgeNode, SQLNode, TableNode

# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class TableJoinRelationBody(BaseModel):
    from_table_name: str = Field(min_length=1)
    to_table_name: str = Field(min_length=1)
    condition: str = ""


class TableJoinRelationView(BaseModel):
    from_table_uuid: str
    from_table_name: str
    to_table_uuid: str
    to_table_name: str
    condition: str = ""


class TableCreate(BaseModel):
    name: str = Field(min_length=1)
    comment: str = ""
    description: str = ""
    join_relations: list[TableJoinRelationBody] = Field(default_factory=list)


class TableUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None
    description: str | None = None
    join_relations: list[TableJoinRelationBody] | None = None


class ColumnCreate(BaseModel):
    table_uuid: str = Field(min_length=1)
    name: str = Field(min_length=1)
    comment: str = ""
    dtype: str = ""


class ColumnUpdate(BaseModel):
    name: str | None = None
    comment: str | None = None
    dtype: str | None = None


class EnumCreate(BaseModel):
    column_uuid: str = Field(min_length=1)
    value: str = Field(min_length=1)


class EnumUpdate(BaseModel):
    value: str = Field(min_length=1)


class KnowledgeRelationBody(BaseModel):
    table_name: str = Field(min_length=1)
    column_names: list[str] = Field(default_factory=list)


class KnowledgeCreate(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    relations: list[KnowledgeRelationBody] = Field(default_factory=list)


class KnowledgeUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    relations: list[KnowledgeRelationBody] | None = None


class KnowledgeRelationView(BaseModel):
    table_name: str
    column_names: list[str] = Field(default_factory=list)


class KnowledgeDetail(BaseModel):
    uuid: str
    type: str
    name: str
    description: str = ""
    relations: list[KnowledgeRelationView] = Field(default_factory=list)


class SQLCreate(BaseModel):
    name: str = Field(min_length=1)
    logic: str = ""
    content: str = ""
    dialect: str = ""


class SQLUpdate(BaseModel):
    name: str | None = None
    logic: str | None = None
    content: str | None = None
    dialect: str | None = None
    enabled: bool | None = None


class SQLDetail(BaseModel):
    uuid: str
    type: str
    name: str
    logic: str = ""
    content: str = ""
    dialect: str = ""
    source: str = "import"
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""
    relations: list[KnowledgeRelationView] = Field(default_factory=list)


def _to_knowledge_relations(
        relations: list[KnowledgeRelationBody],
) -> list[KnowledgeRelation]:
    return [
        KnowledgeRelation(
            table_name=item.table_name,
            column_names=item.column_names,
        )
        for item in relations
    ]


def _to_table_join_specs(
        relations: list[TableJoinRelationBody],
) -> list[TableJoinSpec]:
    return [
        TableJoinSpec(
            from_table_name=item.from_table_name,
            to_table_name=item.to_table_name,
            condition=item.condition,
        )
        for item in relations
    ]


def _to_table_join_views(relations) -> list[TableJoinRelationView]:
    return [
        TableJoinRelationView(
            from_table_uuid=item.from_table_uuid,
            from_table_name=item.from_table_name,
            to_table_uuid=item.to_table_uuid,
            to_table_name=item.to_table_name,
            condition=item.condition,
        )
        for item in relations
    ]


def _assert_workspace(node, workspace_id: str):
    if node.workspace_id != workspace_id:
        raise NotFoundError(f"节点不存在: {node.uuid}")
    return node


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

tables_router = APIRouter(prefix="/api/tables", tags=["tables"])
columns_router = APIRouter(prefix="/api/columns", tags=["columns"])
enums_router = APIRouter(prefix="/api/enums", tags=["enums"])
join_relations_router = APIRouter(prefix="/api/join-relations", tags=["join-relations"])
knowledge_router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
sqls_router = APIRouter(prefix="/api/sqls", tags=["sqls"])


@tables_router.get("", response_model=Page[TableNode])
async def list_table_items(
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按表名/描述搜索"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[TableNode]:
    result = await list_tables(workspace_id=workspace_id, page=page, page_size=page_size, q=q)
    return Page(
        items=result.items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@tables_router.post("", response_model=TableNode, status_code=201)
async def create_table_item(
        body: TableCreate,
        workspace_id: str = Depends(get_workspace_id),
) -> TableNode:
    node = await create_table(
        workspace_id=workspace_id,
        name=body.name,
        comment=body.comment,
        description=body.description,
    )
    if body.join_relations:
        await replace_table_join_relations(
            workspace_id,
            node.uuid,
            _to_table_join_specs(body.join_relations),
        )
    return node


@tables_router.get("/{uuid}", response_model=TableNode)
async def get_table_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> TableNode:
    return _assert_workspace(await get_table(uuid), workspace_id)


@tables_router.get("/{uuid}/join-relations", response_model=list[TableJoinRelationView])
async def list_table_join_relations_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> list[TableJoinRelationView]:
    _assert_workspace(await get_table(uuid), workspace_id)
    relations = await list_table_join_relations(workspace_id, uuid)
    return _to_table_join_views(relations)


@tables_router.put("/{uuid}/join-relations", response_model=list[TableJoinRelationView])
async def replace_table_join_relations_item(
        uuid: str,
        body: list[TableJoinRelationBody],
        workspace_id: str = Depends(get_workspace_id),
) -> list[TableJoinRelationView]:
    _assert_workspace(await get_table(uuid), workspace_id)
    relations = await replace_table_join_relations(workspace_id, uuid, _to_table_join_specs(body))
    return _to_table_join_views(relations)


@join_relations_router.get("", response_model=Page[TableJoinRelationView])
async def list_join_relation_items(
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按表名、表说明或关联条件搜索"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[TableJoinRelationView]:
    result = await list_all_table_join_relations(
        workspace_id=workspace_id,
        page=page,
        page_size=page_size,
        q=q,
    )
    return Page(
        items=_to_table_join_views(result.items),
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@tables_router.put("/{uuid}", response_model=TableNode)
async def update_table_item(
        uuid: str,
        body: TableUpdate,
        workspace_id: str = Depends(get_workspace_id),
) -> TableNode:
    _assert_workspace(await get_table(uuid), workspace_id)
    node = await update_table(
        workspace_id,
        uuid,
        name=body.name,
        comment=body.comment,
        description=body.description,
    )
    if body.join_relations is not None:
        await replace_table_join_relations(
            workspace_id,
            node.uuid,
            _to_table_join_specs(body.join_relations),
        )
    return node


@tables_router.delete("/{uuid}", status_code=204)
async def delete_table_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> None:
    _assert_workspace(await get_table(uuid), workspace_id)
    await delete_table(uuid)


@columns_router.get("", response_model=Page[ColumnNode])
async def list_column_items(
        table_uuid: str | None = Query(None, description="按表 uuid 过滤"),
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按列名/描述/类型搜索"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[ColumnNode]:
    result = await list_columns(
        workspace_id=workspace_id,
        table_uuid=table_uuid,
        page=page,
        page_size=page_size,
        q=q,
    )
    return Page(
        items=result.items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@columns_router.post("", response_model=ColumnNode, status_code=201)
async def create_column_item(
        body: ColumnCreate,
        workspace_id: str = Depends(get_workspace_id),
) -> ColumnNode:
    _assert_workspace(await get_table(body.table_uuid), workspace_id)
    return await create_column(
        workspace_id=workspace_id,
        table_uuid=body.table_uuid,
        name=body.name,
        comment=body.comment,
        dtype=body.dtype,
    )


@columns_router.get("/{uuid}", response_model=ColumnNode)
async def get_column_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> ColumnNode:
    return _assert_workspace(await get_column(uuid), workspace_id)


@columns_router.put("/{uuid}", response_model=ColumnNode)
async def update_column_item(
        uuid: str,
        body: ColumnUpdate,
        workspace_id: str = Depends(get_workspace_id),
) -> ColumnNode:
    _assert_workspace(await get_column(uuid), workspace_id)
    return await update_column(
        workspace_id,
        uuid,
        name=body.name,
        comment=body.comment,
        dtype=body.dtype,
    )


@columns_router.delete("/{uuid}", status_code=204)
async def delete_column_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> None:
    _assert_workspace(await get_column(uuid), workspace_id)
    await delete_column(uuid)


@enums_router.get("", response_model=Page[EnumListItem])
async def list_enum_items(
        column_uuid: str | None = Query(None, description="按列 uuid 过滤"),
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按枚举值搜索"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[EnumListItem]:
    result = await list_enum_items_with_context(
        workspace_id=workspace_id,
        column_uuid=column_uuid,
        page=page,
        page_size=page_size,
        q=q,
    )
    return Page(
        items=result.items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@enums_router.post("", response_model=EnumNode, status_code=201)
async def create_enum_item(
        body: EnumCreate,
        workspace_id: str = Depends(get_workspace_id),
) -> EnumNode:
    _assert_workspace(await get_column(body.column_uuid), workspace_id)
    return await create_enum(
        workspace_id=workspace_id,
        column_uuid=body.column_uuid,
        value=body.value,
    )


@enums_router.get("/{uuid}", response_model=EnumNode)
async def get_enum_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> EnumNode:
    return _assert_workspace(await get_enum(uuid), workspace_id)


@enums_router.put("/{uuid}", response_model=EnumNode)
async def update_enum_item(
        uuid: str,
        body: EnumUpdate,
        workspace_id: str = Depends(get_workspace_id),
) -> EnumNode:
    _assert_workspace(await get_enum(uuid), workspace_id)
    return await update_enum(workspace_id, uuid, value=body.value)


@enums_router.delete("/{uuid}", status_code=204)
async def delete_enum_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> None:
    _assert_workspace(await get_enum(uuid), workspace_id)
    await delete_enum(uuid)


@knowledge_router.get("", response_model=Page[KnowledgeNode])
async def list_knowledge_items(
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按名称/描述搜索"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[KnowledgeNode]:
    result = await list_knowledge(workspace_id=workspace_id, page=page, page_size=page_size, q=q)
    return Page(
        items=result.items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@knowledge_router.post("", response_model=KnowledgeNode, status_code=201)
async def create_knowledge_item(
        body: KnowledgeCreate,
        workspace_id: str = Depends(get_workspace_id),
) -> KnowledgeNode:
    return await create_knowledge(
        workspace_id=workspace_id,
        name=body.name,
        description=body.description,
        relations=_to_knowledge_relations(body.relations),
    )


@knowledge_router.get("/{uuid}", response_model=KnowledgeDetail)
async def get_knowledge_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> KnowledgeDetail:
    node = _assert_workspace(await get_knowledge(uuid), workspace_id)
    relations = await list_knowledge_relations(uuid)
    return KnowledgeDetail(
        uuid=node.uuid,
        type=node.type,
        name=node.name,
        description=node.description,
        relations=[
            KnowledgeRelationView(
                table_name=relation.table_name,
                column_names=relation.column_names,
            )
            for relation in relations
        ],
    )


@knowledge_router.put("/{uuid}", response_model=KnowledgeNode)
async def update_knowledge_item(
        uuid: str,
        body: KnowledgeUpdate,
        workspace_id: str = Depends(get_workspace_id),
) -> KnowledgeNode:
    _assert_workspace(await get_knowledge(uuid), workspace_id)
    relations = (
        _to_knowledge_relations(body.relations)
        if body.relations is not None
        else None
    )
    return await update_knowledge(
        workspace_id,
        uuid,
        name=body.name,
        description=body.description,
        relations=relations,
    )


@knowledge_router.delete("/{uuid}", status_code=204)
async def delete_knowledge_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> None:
    _assert_workspace(await get_knowledge(uuid), workspace_id)
    await delete_knowledge(uuid)


@sqls_router.get("", response_model=Page[SQLNode])
async def list_sql_items(
        page: int = Query(1, ge=1),
        page_size: int = Query(MAX_LIST_PAGE_SIZE, ge=1, le=MAX_LIST_PAGE_SIZE),
        q: str | None = Query(None, description="按名称/逻辑/内容/方言搜索"),
        source: str | None = Query(None, description="import 或 llm"),
        enabled: bool | None = Query(None, description="是否用于 SQL 生成"),
        sort: str | None = Query(None, description="time_desc、time_asc 或 name"),
        workspace_id: str = Depends(get_workspace_id),
) -> Page[SQLNode]:
    result = await list_sqls(
        workspace_id=workspace_id,
        page=page,
        page_size=page_size,
        q=q,
        source=source,
        enabled=enabled,
        sort=sort,
    )
    return Page(
        items=result.items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


@sqls_router.post("", response_model=SQLNode, status_code=201)
async def create_sql_item(
        body: SQLCreate,
        workspace_id: str = Depends(get_workspace_id),
) -> SQLNode:
    return await create_sql(
        workspace_id=workspace_id,
        name=body.name,
        logic=body.logic,
        content=body.content,
        dialect=body.dialect,
    )


@sqls_router.get("/{uuid}", response_model=SQLDetail)
async def get_sql_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> SQLDetail:
    node = _assert_workspace(await get_sql(uuid), workspace_id)
    relations = await list_sql_relations(uuid)
    return SQLDetail(
        uuid=node.uuid,
        type=node.type,
        name=node.name,
        logic=node.logic,
        content=node.content,
        dialect=node.dialect,
        source=str(node.source),
        enabled=bool(node.enabled),
        created_at=node.created_at or "",
        updated_at=node.updated_at or "",
        relations=[
            KnowledgeRelationView(
                table_name=relation.table_name,
                column_names=relation.column_names,
            )
            for relation in relations
        ],
    )


@sqls_router.put("/{uuid}", response_model=SQLNode)
async def update_sql_item(
        uuid: str,
        body: SQLUpdate,
        workspace_id: str = Depends(get_workspace_id),
) -> SQLNode:
    _assert_workspace(await get_sql(uuid), workspace_id)
    return await update_sql(
        workspace_id,
        uuid,
        name=body.name,
        logic=body.logic,
        content=body.content,
        dialect=body.dialect,
        enabled=body.enabled,
    )


@sqls_router.delete("/{uuid}", status_code=204)
async def delete_sql_item(
        uuid: str,
        workspace_id: str = Depends(get_workspace_id),
) -> None:
    _assert_workspace(await get_sql(uuid), workspace_id)
    await delete_sql(uuid)
