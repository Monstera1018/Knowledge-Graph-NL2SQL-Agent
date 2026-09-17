"""根据 Neo4j 元数据向本地 SQLite 灌入合成业务数据，供问数/分析联调。"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from tqdm import tqdm

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT / ".env")

# 不宜做成图谱枚举的高基数列，可把取值池放在本地（不提交）：
# resources/data/seed_values/{表名}.{列名}.txt，每行一个实际值。
_SEED_VALUE_DIR = _PROJECT_ROOT / "resources" / "data" / "seed_values"
_VALUE_POOLS: dict[tuple[str, str], list[str]] | None = None

from tools.graph.internal import _run_read, close_graph_driver
from tools.graph.model import EdgeType, NodeType

_CUSTOMER_COLS = {
    "cust_no",
    "gcust_no",
    "cons_no",
    "usercode",
    "cust_no_zj",
    "tfr_in_cust_no",
    "tfr_out_cust_no",
}
_INST_COLS = {"inst_no", "inst_no_zj"}
_ASSET_COLS = {"asset_no"}
_ORDER_COLS = {"app_no", "wk_order_no", "wk_order_no_zj"}
_CONN_COLS = {"conn_no"}
_YM_COLS = {
    "qty_charg_ym",
    "rcvbl_ym",
    "rcvbl_ym_zj",
    "charg_ym_zj",
    "rcvd_adv_ym_zj",
}
_MONTHS = [
    "202410",
    "202411",
    "202412",
    "202501",
    "202502",
    "202503",
    "202504",
    "202505",
    "202506",
]
@dataclass(frozen=True)
class CityProfile:
    city_zj: str
    city_code: str
    city_name: str
    counties: tuple[str, ...]


# 与枚举「地市简称 / 区县简称」对齐：区县只挂在所属地市下，不用跨市随机抽。
CITY_PROFILES: tuple[CityProfile, ...] = (
    CityProfile("杭州地区", "3301", "国网杭州供电公司", ("杭州本部", "余杭", "萧山", "富阳", "桐庐", "建德", "钱塘新区")),
    CityProfile("宁波地区", "3302", "国网宁波供电公司", ("宁波本部", "鄞州", "慈溪", "奉化", "宁海", "象山")),
    CityProfile("温州地区", "3303", "国网温州供电公司", ("温州本部", "瑞安", "平阳", "苍南", "文成", "龙港")),
    CityProfile("嘉兴地区", "3304", "国网嘉兴供电公司", ("嘉兴本部", "嘉善", "海宁", "海盐", "平湖", "桐乡")),
    CityProfile("湖州地区", "3305", "国网湖州供电公司", ("湖州本部", "德清", "长兴", "安吉")),
    CityProfile("绍兴地区", "3306", "国网绍兴供电公司", ("绍兴本部", "上虞", "诸暨", "嵊州")),
    CityProfile("金华地区", "3307", "国网金华供电公司", ("金华本部", "义乌", "兰溪", "浦江")),
    CityProfile("衢州地区", "3308", "国网衢州供电公司", ("衢州本部", "江山", "常山", "开化", "龙游")),
    CityProfile("舟山地区", "3309", "国网舟山供电公司", ("舟山本部", "岱山", "嵊泗")),
    CityProfile("台州地区", "3310", "国网台州供电公司", ("台州本部", "椒江", "黄岩", "路桥", "临海", "温岭", "玉环", "三门")),
    CityProfile("丽水地区", "3311", "国网丽水供电公司", ("丽水本部", "莲都", "青田", "缙云", "遂昌", "松阳", "云和", "庆元", "景宁", "龙泉")),
)
_ZHOU_SHAN_CITY = "舟山地区"


def county_name_for(city_name: str, county_zj: str) -> str:
    if county_zj.endswith("本部"):
        return city_name
    return f"国网{county_zj}供电公司"


def profile_by_city(city_zj: str) -> CityProfile:
    for profile in CITY_PROFILES:
        if profile.city_zj == city_zj:
            return profile
    raise KeyError(city_zj)


@dataclass
class ColumnSpec:
    name: str
    dtype: str
    comment: str
    enums: list[str] = field(default_factory=list)


@dataclass
class TableSpec:
    name: str
    comment: str
    columns: list[ColumnSpec]


@dataclass
class Entity:
    idx: int
    cust_no: str
    inst_no: str
    asset_no: str
    conn_no: str
    app_no: str
    cust_name: str
    city_zj: str
    city_code: str
    city_name: str
    county_zj: str
    county_name: str
    county_code: str
    addr: str
    is_gen: bool
    zhou_shan: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向本地 SQLite 灌入图谱对齐的合成数据")
    parser.add_argument("--min-rows", type=int, default=500)
    parser.add_argument("--max-rows", type=int, default=800)
    parser.add_argument("--entities", type=int, default=600, help="共享用户池大小")
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--workspace-id", default="default")
    return parser.parse_args()


def sqlite_path_from_url(url: str) -> Path:
    raw = (url or "").strip()
    if ":///" in raw:
        raw = raw.split(":///", 1)[1]
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def sqlite_type(dtype: str) -> str:
    text = (dtype or "").lower()
    if any(token in text for token in ("int", "bigint", "tinyint", "smallint")):
        return "INTEGER"
    if any(token in text for token in ("double", "decimal", "float", "numeric", "real")):
        return "REAL"
    return "TEXT"


async def load_schema(workspace_id: str) -> list[TableSpec]:
    rows = await _run_read(
        f"""
        MATCH (t:{NodeType.TABLE})-[:{EdgeType.HAS}]->(c:{NodeType.COLUMN})
        WHERE t.workspace_id = $workspace_id
        OPTIONAL MATCH (c)-[:{EdgeType.HAS}]->(e:{NodeType.ENUM})
        WITH t, c, collect(e.value) AS enum_values
        RETURN t.name AS table_name, t.comment AS table_comment,
               c.name AS col_name, c.dtype AS dtype, c.comment AS col_comment,
               enum_values
        ORDER BY t.name, c.name
        """,
        workspace_id=workspace_id,
    )
    tables: dict[str, TableSpec] = {}
    for row in rows:
        table_name = str(row["table_name"])
        table = tables.get(table_name)
        if table is None:
            table = TableSpec(name=table_name, comment=row["table_comment"] or "", columns=[])
            tables[table_name] = table
        enums = [str(value).strip() for value in (row["enum_values"] or []) if str(value).strip()]
        table.columns.append(
            ColumnSpec(
                name=str(row["col_name"]),
                dtype=str(row["dtype"] or ""),
                comment=str(row["col_comment"] or ""),
                enums=enums,
            )
        )
    return list(tables.values())


def build_entities(count: int, rng: random.Random) -> list[Entity]:
    entities: list[Entity] = []
    gen_limit = max(40, count // 8)
    zhou_shan_limit = max(30, count // 12)
    zhou_shan_profile = profile_by_city(_ZHOU_SHAN_CITY)
    for idx in range(count):
        profile = CITY_PROFILES[idx % len(CITY_PROFILES)]
        zhou_shan = idx < zhou_shan_limit
        if zhou_shan:
            profile = zhou_shan_profile
        county = profile.counties[idx % len(profile.counties)]
        county_code = f"{profile.city_code}{profile.counties.index(county) + 1:02d}"
        entities.append(
            Entity(
                idx=idx,
                cust_no=f"33{idx:08d}",
                inst_no=f"JL{idx:08d}",
                asset_no=f"AS{idx:08d}",
                conn_no=f"CN{idx:08d}",
                app_no=f"GD{idx:08d}",
                cust_name=f"合成用户{idx:04d}",
                city_zj=profile.city_zj,
                city_code=profile.city_code,
                city_name=profile.city_name,
                county_zj=county,
                county_name=county_name_for(profile.city_name, county),
                county_code=county_code,
                addr=f"{profile.city_zj}{county}合成路{idx % 200 + 1}号",
                is_gen=idx < gen_limit,
                zhou_shan=zhou_shan,
            )
        )
    rng.shuffle(entities)
    return entities


def _constrained(value: str, enums: list[str]) -> str | None:
    if not enums:
        return value
    if value in enums:
        return value
    return None


def _geo_field_value(column: ColumnSpec, entity: Entity) -> str | None:
    """地市/区县必须成对，且优先落在该列枚举闭集内。"""
    name = column.name.lower()
    comment = column.comment or ""
    enums = column.enums
    if name in {"city_zj", "city"} or "地市简称" in comment:
        return _constrained(entity.city_zj, enums)
    if name in {"county_zj", "county"} or "区县简称" in comment:
        return _constrained(entity.county_zj, enums)
    if "city_name" in name or "市公司名称" in comment or comment.strip() == "市名称":
        return _constrained(entity.city_name, enums) or entity.city_name
    if "county_name" in name or "县公司名称" in comment or "区县名称" in comment:
        return _constrained(entity.county_name, enums) or entity.county_name
    if "city_code" in name or "市码" in comment or "市公司代码" in comment or "地市编码" in comment:
        return _constrained(entity.city_code, enums) or entity.city_code
    if "county_code" in name or "区县码" in comment or "县公司代码" in comment or "区县编码" in comment:
        return _constrained(entity.county_code, enums) or entity.county_code
    return None


def _pick_enum(column: ColumnSpec, rng: random.Random, entity: Entity) -> str:
    values = column.enums
    comment = column.comment or ""
    name = column.name.lower()
    if any(token in comment or token in name for token in ("结清", "sett", "setl")):
        unpaid = [item for item in values if "未结清" in item or "未结" in item]
        if unpaid and rng.random() < 0.35:
            return rng.choice(unpaid)
    if "电压" in comment or "volt" in name:
        high = [item for item in values if any(tag in item for tag in ("10kV", "20kV", "35kV", "110kV"))]
        if high and rng.random() < 0.4:
            return rng.choice(high)
    if entity.zhou_shan:
        zhou = [item for item in values if "舟山" in item]
        if zhou:
            return rng.choice(zhou)
    return rng.choice(values)


def _numeric_value(column: ColumnSpec, rng: random.Random) -> float | int:
    kind = sqlite_type(column.dtype)
    comment = column.comment or ""
    name = column.name.lower()
    energy = any(token in comment or token in name for token in ("电量", "pq", "pap", "kwh", "qty"))
    money = any(token in comment or token in name for token in ("电费", "金额", "amt", "fee", "bal"))
    if energy:
        value = rng.lognormvariate(6.2, 0.7)
        if rng.random() < 0.25:
            value = max(value, 1000 + rng.random() * 4000)
        return round(value, 4) if kind == "REAL" else int(value)
    if money:
        value = rng.lognormvariate(5.5, 0.8)
        if rng.random() < 0.08:
            value = max(value, 10000 + rng.random() * 20000)
        return round(value, 2) if kind == "REAL" else int(value)
    if kind == "INTEGER":
        if column.name == "__adb_auto_id__":
            return 0
        return rng.randint(1, 9_000_000)
    return round(rng.uniform(1, 500), 4)


def _datetime_value(rng: random.Random, ym: str, as_date: bool = False) -> str:
    year, month = int(ym[:4]), int(ym[4:6])
    day = rng.randint(1, 28)
    hour = rng.randint(0, 23)
    minute = rng.randint(0, 59)
    if as_date:
        return f"{year:04d}-{month:02d}-{day:02d}"
    return f"{year:04d}-{month:02d}-{day:02d} {hour:02d}:{minute:02d}:00"


def load_seed_value_pools() -> dict[tuple[str, str], list[str]]:
    """读取不宜枚举的列取值池；文件缺失时该列仍走原有造数规则。"""
    global _VALUE_POOLS
    if _VALUE_POOLS is not None:
        return _VALUE_POOLS
    pools: dict[tuple[str, str], list[str]] = {}
    if _SEED_VALUE_DIR.is_dir():
        for path in sorted(_SEED_VALUE_DIR.glob("*.txt")):
            stem = path.stem.strip()
            if "." not in stem:
                continue
            table_name, column_name = stem.rsplit(".", 1)
            values = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if values:
                pools[(table_name.strip().lower(), column_name.strip().lower())] = values
    _VALUE_POOLS = pools
    return pools


def value_for(
        column: ColumnSpec,
        *,
        table_name: str,
        entity: Entity,
        peer: Entity,
        ym: str,
        row_id: int,
        rng: random.Random,
) -> object:
    name = column.name
    lower = name.lower()
    dtype = (column.dtype or "").lower()
    comment = column.comment or ""

    if lower == "__adb_auto_id__":
        return row_id + 1
    geo_value = _geo_field_value(column, entity)
    if geo_value is not None:
        return geo_value
    pool = load_seed_value_pools().get((table_name.lower(), lower))
    if pool:
        return rng.choice(pool)
    if column.enums:
        return _pick_enum(column, rng, entity)
    if lower in _CUSTOMER_COLS:
        if lower == "tfr_out_cust_no":
            return peer.cust_no
        if lower in {"gcust_no", "cons_no"} and not entity.is_gen:
            return entity.cust_no
        return entity.cust_no
    if lower in _INST_COLS:
        return entity.inst_no
    if lower in _ASSET_COLS:
        return entity.asset_no
    if lower in _ORDER_COLS:
        return entity.app_no
    if lower in _CONN_COLS:
        return entity.conn_no
    if lower in _YM_COLS or (lower.endswith("_ym") or lower.endswith("_ym_zj")):
        return ym
    if lower in {"cust_name", "cust_name_zj", "bp_name_in", "cust_name_in"}:
        return entity.cust_name
    if lower in {"bp_name_out", "cust_name_out"}:
        return peer.cust_name
    if lower in {"city_zj", "city"} or "地市简称" in comment:
        return entity.city_zj
    if "city_name" in lower:
        return entity.city_name
    if "city_code" in lower:
        return entity.city_code
    if lower in {"county_zj", "county"} or "区县简称" in comment:
        return entity.county_zj
    if "county_name" in lower:
        return entity.county_name
    if "county_code" in lower:
        return entity.county_code
    if lower in {"ec_addr", "elec_addr", "addr"} or "地址" in comment:
        return entity.addr
    if "datetime" in dtype or lower.endswith("_time") or "日期" in comment or "时间" in comment:
        return _datetime_value(rng, ym, as_date="日期" in comment and "时间" not in comment)
    if sqlite_type(column.dtype) in {"INTEGER", "REAL"}:
        return _numeric_value(column, rng)
    if lower.endswith("_code") or lower.endswith("_code_zj"):
        return f"{entity.city_code}{entity.idx:04d}"
    if "名称" in comment or lower.endswith("_name"):
        label = re.sub(r"[（(].*$", "", comment).strip() or name
        return f"{label}{entity.idx % 50 + 1}"
    if "标志" in comment or lower.endswith("_flag"):
        return rng.choice(["0", "1"])
    return f"SYN-{name}-{entity.idx:04d}"


def create_table(conn: sqlite3.Connection, table: TableSpec) -> None:
    cols_sql = []
    seen: set[str] = set()
    for column in table.columns:
        if column.name in seen:
            continue
        seen.add(column.name)
        cols_sql.append(f"{quote_ident(column.name)} {sqlite_type(column.dtype)}")
    conn.execute(f"DROP TABLE IF EXISTS {quote_ident(table.name)}")
    conn.execute(
        f"CREATE TABLE {quote_ident(table.name)} ({', '.join(cols_sql)})"
    )


def insert_rows(
        conn: sqlite3.Connection,
        table: TableSpec,
        *,
        entities: list[Entity],
        n_rows: int,
        rng: random.Random,
) -> None:
    colnames = list(dict.fromkeys(column.name for column in table.columns))
    colmap = {column.name: column for column in table.columns}
    placeholders = ",".join("?" for _ in colnames)
    sql = (
        f"INSERT INTO {quote_ident(table.name)} "
        f"({', '.join(quote_ident(name) for name in colnames)}) "
        f"VALUES ({placeholders})"
    )
    gen_entities = [item for item in entities if item.is_gen] or entities
    batch: list[tuple[object, ...]] = []
    for row_id in range(n_rows):
        if table.name.startswith("dws_cst_fdh") or "fdh" in table.name:
            entity = gen_entities[row_id % len(gen_entities)]
        else:
            entity = entities[row_id % len(entities)]
        peer = entities[(row_id + 17) % len(entities)]
        ym = _MONTHS[row_id % len(_MONTHS)]
        values = [
            value_for(
                colmap[name],
                table_name=table.name,
                entity=entity,
                peer=peer,
                ym=ym,
                row_id=row_id,
                rng=rng,
            )
            for name in colnames
        ]
        batch.append(tuple(values))
        if len(batch) >= 200:
            conn.executemany(sql, batch)
            batch.clear()
    if batch:
        conn.executemany(sql, batch)


def seed_sqlite(tables: list[TableSpec], args: argparse.Namespace) -> Path:
    db_path = sqlite_path_from_url(os.environ.get("DATABASE_URL", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)
    entities = build_entities(args.entities, rng)
    conn = sqlite3.connect(str(db_path), timeout=120)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=120000")
        for table in tqdm(tables, desc="灌入合成数据", unit="表"):
            n_rows = rng.randint(args.min_rows, args.max_rows)
            create_table(conn, table)
            insert_rows(conn, table, entities=entities, n_rows=n_rows, rng=rng)
            conn.commit()
    finally:
        conn.close()
    return db_path


async def async_main() -> int:
    args = parse_args()
    if args.min_rows <= 0 or args.max_rows < args.min_rows:
        raise ValueError("行数范围不合法")
    print("正在从图谱加载表结构…")
    tables = await load_schema(args.workspace_id)
    try:
        await close_graph_driver()
    except Exception:
        pass
    if not tables:
        raise RuntimeError("图谱中没有表结构，请先完成初始化")
    print(f"共 {len(tables)} 张表，每表 {args.min_rows}–{args.max_rows} 行")
    db_path = seed_sqlite(tables, args)
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        names = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1"
            )
        ]
        print(f"已写入 {db_path}")
        for name in names:
            count = conn.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0]
            print(f"  {name}: {count} 行")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(async_main()))
    except Exception as exc:
        print(f"合成数据灌入失败：{exc}", file=sys.stderr)
        raise
