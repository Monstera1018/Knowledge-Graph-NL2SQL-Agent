import re
from typing import Awaitable, Callable, List, Any, Dict
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from tools.progress import (
    CliProgressRenderer,
    ImportStage,
    ProgressCallback,
    ProgressEntity,
    ProgressLevel,
    build_import_progress,
    emit_progress,
)

from tools.common import normalize_text, normalize_key, utc_now_iso
from tools.graph.internal import upsert_graph_node_tx, upsert_graph_edge_tx, build_stable_sql_uuid, \
    build_stable_table_uuid, build_stable_column_uuid, build_stable_knowledge_uuid, build_stable_enum_uuid, \
    get_graph_driver, _get_graph_database
from tools.graph.model import ColumnNode, NodeType, TableNode, HasEdge, EdgeType, EnumNode, KnowledgeNode, DescribeEdge, \
    UseEdge, \
    SQLNode, JoinEdge
from tools.sql import find_all_sources
from tools.vector import upsert_vector_records
from tools.table_description import generate_table_description

# ---------------------------------------------------------------------------
# 任务注册表：新增 init 类型时，在这里加一条配置即可
# ---------------------------------------------------------------------------

TABLE_SCHEMA_COLUMNS = ["表名", "表描述", "列名", "列类型", "列描述"]
ENUM_COLUMNS = ["表名", "列名", "枚举值"]
KNOWLEDGE_COLUMNS = ["业务知识名称", "业务知识描述", "相关表", "相关列"]
SQL_COLUMNS = ["SQL名称", "SQL逻辑", "SQL内容", "数据库类型"]
RELATION_COLUMNS = [
    "主表名称",
    "主表描述",
    "从表名称",
    "从表描述",
    "关联条件",
    "关联条件描述",
]

ImportFn = Callable[[str, pd.DataFrame, ProgressCallback | None], Awaitable[None]]


@dataclass
class ImportJob:
    stage: ImportStage
    filename: str
    label: str
    required_columns: list[str]
    import_fn: ImportFn = field(repr=False, compare=False)


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------

def str_cell(x: Any) -> str:
    if pd.isna(x):
        return ""
    return str(x).strip()


def load_excel_df(
        source: str | Path | pd.DataFrame,
        *,
        required_columns: List[str],
        label: str,
) -> pd.DataFrame:
    if isinstance(source, pd.DataFrame):
        df = source.copy()
    else:
        df = pd.read_excel(source)

    missing = [column for column in required_columns if column not in df.columns]
    if missing:
        raise ValueError(f"{label} Excel 缺少必要列: {missing}")

    for column in required_columns:
        df[column] = df[column].map(str_cell)

    return df


async def run_import(
        workspace_id: str,
        job: ImportJob,
        source: str | Path | pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    df = load_excel_df(
        source,
        required_columns=job.required_columns,
        label=job.label,
    )
    await job.import_fn(workspace_id, df, on_progress)


async def run_all_imports(
        workspace_id: str,
        data_dir: Path,
        jobs: list[ImportJob] | None = None,
) -> None:
    for job in jobs or IMPORT_JOBS:
        renderer = CliProgressRenderer(job.stage)
        try:
            await run_import(workspace_id, job, data_dir / job.filename, on_progress=renderer)
        finally:
            renderer.close()


# ---------------------------------------------------------------------------
# 1. 表结构：表 -> 列
# ---------------------------------------------------------------------------

async def import_table_schema(
        workspace_id: str,
        df: pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    grouped = df.groupby(by=["表名", "表描述"], sort=False)
    table_total = grouped.ngroups
    overall_total = len(df)
    if overall_total == 0:
        return

    overall_current = 0
    for table_current, ((table_name, table_comment), sub_df) in enumerate(grouped, start=1):
        stage = ImportStage.IMPORT_TABLE_SCHEMA
        column_total = len(sub_df)
        table_level = ProgressLevel(
            entity=ProgressEntity.TABLE,
            name=table_name,
            current=table_current,
            total=table_total,
        )

        await emit_progress(
            on_progress,
            build_import_progress(
                stage=stage,
                scope=ProgressEntity.TABLE,
                levels=[table_level],
                overall_current=overall_current,
                overall_total=overall_total,
            ),
        )

        table_uuid = build_stable_table_uuid(workspace_id, table_name)
        normalized_table_name = normalize_key(table_name)

        columns: list[tuple[str, str, str]] = []
        for _, row in sub_df.iterrows():
            columns.append((
                str_cell(row["列名"]),
                str_cell(row["列类型"]),
                str_cell(row["列描述"]),
            ))

        description = await generate_table_description(
            table_name=table_name,
            table_comment=table_comment,
            columns=columns,
        )

        table_node = TableNode(
            uuid=table_uuid,
            type=NodeType.TABLE,
            name=normalized_table_name,
            comment=table_comment,
            description=description,
            workspace_id=workspace_id
        )

        table_vector_texts = [
            text for text in (table_comment, description) if text.strip()
        ]

        first_time = True

        driver = get_graph_driver()
        async with driver.session(database=_get_graph_database()) as session:
            tx = await session.begin_transaction()
            try:
                for column_current, (column_name, column_dtype, column_comment) in enumerate(columns, start=1):
                    column_uuid = build_stable_column_uuid(workspace_id, table_name, column_name)
                    column_node = ColumnNode(
                        uuid=column_uuid,
                        type=NodeType.COLUMN,
                        name=normalize_key(column_name),
                        comment=column_comment,
                        dtype=column_dtype,
                        workspace_id=workspace_id
                    )

                    if first_time:
                        await upsert_graph_node_tx(tx, table_node)

                        if table_vector_texts:
                            await upsert_vector_records(
                                workspace_id,
                                table_vector_texts,
                                NodeType.TABLE,
                                table_uuid,
                            )
                        first_time = False
                    await upsert_graph_node_tx(tx, column_node)
                    await upsert_graph_edge_tx(tx, HasEdge(
                        type=EdgeType.HAS,
                        from_uuid=table_node.uuid,
                        to_uuid=column_node.uuid
                    ))

                    column_vector_texts = [
                        text for text in (column_comment,) if text.strip()
                    ]
                    if column_vector_texts:
                        await upsert_vector_records(
                            workspace_id,
                            column_vector_texts,
                            NodeType.COLUMN,
                            column_uuid,
                        )

                    overall_current += 1

                    await emit_progress(
                        on_progress,
                        build_import_progress(
                            stage=stage,
                            scope=ProgressEntity.COLUMN,
                            levels=[
                                table_level,
                                ProgressLevel(
                                    entity=ProgressEntity.COLUMN,
                                    name=column_name,
                                    current=column_current,
                                    total=column_total,
                                ),
                            ],
                            overall_current=overall_current,
                            overall_total=overall_total,
                        ),
                    )
                await tx.commit()
            except Exception:
                await tx.rollback()
                raise
            finally:
                await tx.close()


# ---------------------------------------------------------------------------
# 2. 枚举值：表 -> 列 -> 枚举
# ---------------------------------------------------------------------------

async def import_enum_values(
        workspace_id: str,
        df: pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    grouped = df.groupby(by=["表名", "列名"], sort=False)
    table_order: list[str] = []
    table_to_columns: dict[str, list[str]] = {}
    for (table_name, column_name), _ in grouped:
        if table_name not in table_to_columns:
            table_order.append(table_name)
            table_to_columns[table_name] = []
        table_to_columns[table_name].append(column_name)

    table_total = len(table_order)
    overall_total = len(df)
    if overall_total == 0:
        return

    overall_current = 0
    for table_current, table_name in enumerate(table_order, start=1):
        column_names = table_to_columns[table_name]
        column_total = len(column_names)

        for column_current, column_name in enumerate(column_names, start=1):
            enum_df = grouped.get_group((table_name, column_name))
            stage = ImportStage.IMPORT_ENUM_VALUE
            table_level = ProgressLevel(
                entity=ProgressEntity.TABLE,
                name=table_name,
                current=table_current,
                total=table_total,
            )
            column_level = ProgressLevel(
                entity=ProgressEntity.COLUMN,
                name=column_name,
                current=column_current,
                total=column_total,
            )

            await emit_progress(
                on_progress,
                build_import_progress(
                    stage=stage,
                    scope=ProgressEntity.COLUMN,
                    levels=[table_level, column_level],
                    overall_current=overall_current,
                    overall_total=overall_total,
                ),
            )

            enum_total = len(enum_df)
            driver = get_graph_driver()
            async with driver.session(database=_get_graph_database()) as session:
                tx = await session.begin_transaction()
                try:
                    for enum_current, (_, row) in enumerate(enum_df.iterrows(), start=1):
                        enum_value = normalize_text(str_cell(row["枚举值"]))
                        enum_uuid = build_stable_enum_uuid(workspace_id, table_name, column_name, enum_value)

                        await upsert_graph_node_tx(tx, EnumNode(
                            uuid=enum_uuid,
                            type=NodeType.ENUM,
                            value=enum_value,
                            workspace_id=workspace_id
                        ))
                        await upsert_graph_edge_tx(tx, HasEdge(
                            from_uuid=build_stable_column_uuid(workspace_id, table_name, column_name),
                            type=EdgeType.HAS,
                            to_uuid=enum_uuid,
                        ))

                        await upsert_vector_records(
                            workspace_id,
                            [enum_value],
                            NodeType.ENUM,
                            enum_uuid,
                        )

                        overall_current += 1
                        await emit_progress(
                            on_progress,
                            build_import_progress(
                                stage=stage,
                                scope=ProgressEntity.ENUM,
                                levels=[
                                    table_level,
                                    column_level,
                                    ProgressLevel(
                                        entity=ProgressEntity.ENUM,
                                        name=enum_value,
                                        current=enum_current,
                                        total=enum_total,
                                    ),
                                ],
                                overall_current=overall_current,
                                overall_total=overall_total,
                            ),
                        )

                    await tx.commit()
                except Exception:
                    await tx.rollback()
                    raise
                finally:
                    await tx.close()


# ---------------------------------------------------------------------------
# 3. 业务知识
# ---------------------------------------------------------------------------

def _parse_related_table_and_column(
        related_tables: str,
        related_columns: str,
) -> Dict[str, List[str]]:
    related_table_and_column = {
        x.strip().lower(): []
        for x in re.split("[，,、]", related_tables)
        if len(x.strip()) > 0
    }
    for txt in [x.strip().lower() for x in re.split("[，,、]", related_columns) if len(x.strip()) > 0]:
        table_name, *column_names = [
            c.strip().lower() for c in re.split("[-—]", txt) if len(c.strip()) > 0
        ]
        if related_table_and_column.get(table_name) is not None:
            related_table_and_column[table_name].extend(column_names)
        else:
            related_table_and_column[table_name] = column_names
    return related_table_and_column


async def import_knowledge(
        workspace_id: str,
        df: pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    stage = ImportStage.IMPORT_KNOWLEDGE
    entity = ProgressEntity.KNOWLEDGE

    overall_total = len(df)
    if overall_total == 0:
        return

    driver = get_graph_driver()
    async with driver.session(database=_get_graph_database()) as session:
        tx = await session.begin_transaction()
        try:
            for overall_current, (_, row) in enumerate(df.iterrows(), start=1):
                knowledge_name = str_cell(row["业务知识名称"])
                knowledge_description = str_cell(row["业务知识描述"])
                knowledge_uuid = build_stable_knowledge_uuid(workspace_id, knowledge_name, knowledge_description)

                related_tables = str_cell(row['相关表'])
                related_columns = str_cell(row['相关列'])
                related_table_and_column = _parse_related_table_and_column(related_tables, related_columns)

                await upsert_graph_node_tx(tx,
                                           KnowledgeNode(uuid=knowledge_uuid, type=NodeType.KNOWLEDGE,
                                                         name=knowledge_name,
                                                         description=knowledge_description, workspace_id=workspace_id))

                for table_name, column_names in related_table_and_column.items():
                    for column_name in column_names:
                        await upsert_graph_edge_tx(
                            tx,
                            DescribeEdge(from_uuid=knowledge_uuid,
                                         to_uuid=build_stable_column_uuid(workspace_id, table_name, column_name),
                                         type=EdgeType.DESCRIBES)
                        )
                    await upsert_graph_edge_tx(
                        tx,
                        DescribeEdge(from_uuid=knowledge_uuid,
                                     to_uuid=build_stable_table_uuid(workspace_id, table_name),
                                     type=EdgeType.DESCRIBES)
                    )

                await upsert_vector_records(
                    workspace_id,
                    [knowledge_name, knowledge_description],
                    NodeType.KNOWLEDGE,
                    knowledge_uuid
                )

                await emit_progress(
                    on_progress,
                    build_import_progress(
                        stage=stage,
                        scope=entity,
                        levels=[
                            ProgressLevel(
                                entity=entity,
                                name=knowledge_name,
                                current=overall_current,
                                total=overall_total,
                            ),
                        ],
                        overall_current=overall_current,
                        overall_total=overall_total,
                    ),
                )

            await tx.commit()
        except Exception:
            await tx.rollback()
            raise
        finally:
            await tx.close()


# ---------------------------------------------------------------------------
# 4. SQL
# ---------------------------------------------------------------------------

async def import_sql_history(
        workspace_id: str,
        df: pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    stage = ImportStage.IMPORT_SQL
    entity = ProgressEntity.SQL

    overall_total = len(df)
    if overall_total == 0:
        return

    driver = get_graph_driver()
    async with driver.session(database=_get_graph_database()) as session:
        tx = await session.begin_transaction()
        try:
            for overall_current, (_, row) in enumerate(df.iterrows(), start=1):
                sql_name = str_cell(row["SQL名称"])
                sql_logic = str_cell(row["SQL逻辑"])
                sql_content = str_cell(row["SQL内容"])
                sql_dialect = str_cell(row["数据库类型"])

                sql_uuid = build_stable_sql_uuid(workspace_id, sql_name, sql_content)
                now = utc_now_iso()

                table_and_columns, error = find_all_sources(sql_content, sql_dialect.lower())

                await upsert_graph_node_tx(tx,
                                           SQLNode(uuid=sql_uuid, type=NodeType.SQL, name=sql_name,
                                                   logic=sql_logic, content=sql_content, dialect=sql_dialect,
                                                   workspace_id=workspace_id,
                                                   source="import", enabled=True,
                                                   created_at=now, updated_at=now))

                if table_and_columns is None:
                    raise ValueError(error)
                for table_name, column_names in table_and_columns.items():
                    for column_name in column_names:
                        await upsert_graph_edge_tx(
                            tx,
                            UseEdge(from_uuid=sql_uuid,
                                    to_uuid=build_stable_column_uuid(workspace_id, table_name, column_name),
                                    type=EdgeType.USES)
                        )
                    await upsert_graph_edge_tx(
                        tx,
                        UseEdge(from_uuid=sql_uuid,
                                to_uuid=build_stable_table_uuid(workspace_id, table_name),
                                type=EdgeType.USES)
                    )

                await upsert_vector_records(
                    workspace_id,
                    [sql_name, sql_logic, sql_content],
                    NodeType.SQL,
                    sql_uuid,
                )

                await emit_progress(
                    on_progress,
                    build_import_progress(
                        stage=stage,
                        scope=entity,
                        levels=[
                            ProgressLevel(
                                entity=entity,
                                name=sql_name,
                                current=overall_current,
                                total=overall_total,
                            ),
                        ],
                        overall_current=overall_current,
                        overall_total=overall_total,
                    ),
                )

            await tx.commit()
        except Exception:
            await tx.rollback()
            raise
        finally:
            await tx.close()


# ---------------------------------------------------------------------------
# 5. 表关系
# ---------------------------------------------------------------------------


async def import_table_relations(
        workspace_id: str,
        df: pd.DataFrame,
        on_progress: ProgressCallback | None = None,
) -> None:
    stage = ImportStage.IMPORT_TABLE_RELATION
    entity = ProgressEntity.RELATION

    overall_total = len(df)
    if overall_total == 0:
        return

    driver = get_graph_driver()
    async with driver.session(database=_get_graph_database()) as session:
        tx = await session.begin_transaction()
        try:
            for overall_current, (_, row) in enumerate(df.iterrows(), start=1):
                master_table_name = str_cell(row["主表名称"])
                slave_table_name = str_cell(row["从表名称"])
                condition = str_cell(row["关联条件"])

                await upsert_graph_edge_tx(
                    tx,
                    JoinEdge(
                        from_uuid=build_stable_table_uuid(workspace_id, master_table_name),
                        to_uuid=build_stable_table_uuid(workspace_id, slave_table_name),
                        type=EdgeType.JOINS,
                        condition=condition,
                    ),
                )

                await emit_progress(
                    on_progress,
                    build_import_progress(
                        stage=stage,
                        scope=entity,
                        levels=[
                            ProgressLevel(
                                entity=entity,
                                name=f"{master_table_name.lower()} -> {slave_table_name.lower()}",
                                current=overall_current,
                                total=overall_total,
                            ),
                        ],
                        overall_current=overall_current,
                        overall_total=overall_total,
                    ),
                )

            await tx.commit()
        except Exception:
            await tx.rollback()
            raise
        finally:
            await tx.close()


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------

IMPORT_JOBS: List[ImportJob] = [
    ImportJob(
        stage=ImportStage.IMPORT_TABLE_SCHEMA,
        filename="表结构DEV.xlsx",
        label="表结构",
        required_columns=TABLE_SCHEMA_COLUMNS,
        import_fn=import_table_schema,
    ),
    ImportJob(
        stage=ImportStage.IMPORT_ENUM_VALUE,
        filename="枚举值DEV.xlsx",
        label="枚举值",
        required_columns=ENUM_COLUMNS,
        import_fn=import_enum_values,
    ),
    ImportJob(
        stage=ImportStage.IMPORT_KNOWLEDGE,
        filename="业务知识DEV.xlsx",
        label="业务知识",
        required_columns=KNOWLEDGE_COLUMNS,
        import_fn=import_knowledge,
    ),
    ImportJob(
        stage=ImportStage.IMPORT_SQL,
        filename="历史SQLDEV.xlsx",
        label="历史 SQL",
        required_columns=SQL_COLUMNS,
        import_fn=import_sql_history,
    ),
    ImportJob(
        stage=ImportStage.IMPORT_TABLE_RELATION,
        filename="表关系DEV.xlsx",
        label="表关系",
        required_columns=RELATION_COLUMNS,
        import_fn=import_table_relations,
    ),
]
