"""基于已有查询结果规划并物化图表规格。"""

from __future__ import annotations

import json
import re
from typing import Any

CHART_TYPES = frozenset({"bar", "line", "pie"})
CHART_AGGREGATIONS = frozenset({"count", "sum", "avg"})
MAX_CHARTS = 3
MAX_PIE_SLICES = 12
SAMPLE_ROWS = 8
NO_CHART_SNAPSHOT_MESSAGE = (
    "当前会话还没有可分析的查询结果。请先提出一个数据问题，等系统查出表格后再让我生成图表。"
)
_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")


def parse_json_objects(text: str) -> list[dict[str, Any]]:
    cleaned = (text or "").strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()

    decoder = json.JSONDecoder()
    objects: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(cleaned):
        start = cleaned.find("{", cursor)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(cleaned[start:])
        except json.JSONDecodeError:
            cursor = start + 1
            continue
        if isinstance(value, dict):
            objects.append(value)
        cursor = start + end

    if objects:
        return objects

    for match in _JSON_OBJECT_RE.finditer(cleaned):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
    return objects


def parse_number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("%", "").replace("，", "")
    if not text:
        return None
    if not _NUMBER_RE.match(text):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _empty_chart_spec(summary: str, *, source_row_count: int = 0) -> dict[str, Any]:
    return {
        "kind": "chart_spec",
        "feasible": False,
        "summary": summary,
        "source_row_count": source_row_count,
        "charts": [],
    }


def _snapshot_sets(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not snapshot:
        return []
    raw = snapshot.get("result_sets")
    if not isinstance(raw, list):
        return []
    sets: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        columns = item.get("columns")
        rows = item.get("rows")
        if not isinstance(columns, list) or not isinstance(rows, list):
            continue
        col_names = [str(col) for col in columns if str(col).strip()]
        normalized_rows = [
            row for row in rows if isinstance(row, dict)
        ]
        sets.append({"columns": col_names, "rows": normalized_rows})
    return sets


def _column_lookup(columns: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for column in columns:
        lookup[column] = column
        lookup[column.casefold()] = column
    return lookup


def infer_column_kind(rows: list[dict[str, Any]], column: str) -> str:
    values = [str(row.get(column) or "").strip() for row in rows if str(row.get(column) or "").strip()]
    if not values:
        return "empty"
    numeric = sum(1 for value in values if parse_number(value) is not None)
    if numeric >= max(1, int(len(values) * 0.7)):
        return "number"
    dated = sum(1 for value in values if re.match(r"^\d{4}[-/年]", value))
    if dated >= max(1, int(len(values) * 0.6)):
        return "time"
    return "category"


def format_chart_planner_user_message(*, user_request: str, snapshot: dict[str, Any]) -> str:
    sets = _snapshot_sets(snapshot)
    previous_question = str(snapshot.get("user_question") or "").strip() or "（无）"
    tables = snapshot.get("selected_tables")
    table_lines = (
        "\n".join(f"- `{name}`" for name in tables if str(name).strip())
        if isinstance(tables, list) and tables
        else "（无）"
    )
    schema_context = str(snapshot.get("schema_context") or "").strip() or "（无）"
    result_parts: list[str] = []
    for index, result in enumerate(sets, start=1):
        columns = result["columns"]
        rows = result["rows"]
        result_parts.append(f"### 结果集 {index}")
        result_parts.append(f"行数：{len(rows)}")
        if not columns:
            result_parts.append("列：无")
            continue
        result_parts.append("列：")
        sample = rows[:SAMPLE_ROWS]
        for column in columns:
            kind = infer_column_kind(rows, column)
            unique_count = 0
            uniques: list[str] = []
            seen: set[str] = set()
            for row in rows:
                value = str(row.get(column) or "").strip()
                if not value or value in seen:
                    continue
                seen.add(value)
                unique_count += 1
                if len(uniques) < 8:
                    uniques.append(value)
            unique_note = f"，样例取值：{'、'.join(uniques)}" if uniques else ""
            result_parts.append(f"- `{column}`（{kind}，约 {unique_count} 个不同值{unique_note}）")
        if sample:
            header = "| " + " | ".join(columns) + " |"
            sep = "| " + " | ".join("---" for _ in columns) + " |"
            body = [
                "| " + " | ".join(str(row.get(col) or "") for col in columns) + " |"
                for row in sample
            ]
            result_parts.extend(["", "样例行：", header, sep, *body, ""])
    result_text = "\n".join(result_parts) if result_parts else "（无结果行）"
    return "\n".join([
        "## 用户出图要求",
        "",
        (user_request or "").strip() or "（无）",
        "",
        "## 上一轮用户问题",
        "",
        previous_question,
        "",
        "## 已选数据表",
        "",
        table_lines,
        "",
        "## 检索与表结构上下文",
        "",
        schema_context,
        "",
        "## 当前查询结果概要",
        "",
        result_text,
        "",
        "请只输出 JSON 图表规格。列名必须来自上述结果概要。",
    ])


def _resolve_column(name: object, lookup: dict[str, str]) -> str:
    text = str(name or "").strip()
    if not text:
        return ""
    return lookup.get(text) or lookup.get(text.casefold()) or ""


def _cell(row: dict[str, Any], column: str) -> str:
    return str(row.get(column) or "").strip()


def _aggregate_chart(
        *,
        chart_type: str,
        title: str,
        note: str,
        x_column: str,
        metrics: list[dict[str, str]],
        rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    grouped: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for row in rows:
        key = _cell(row, x_column)
        if not key:
            continue
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(row)
    if not order:
        return None

    series: list[dict[str, Any]] = []
    for metric in metrics:
        column = metric["column"]
        aggregation = metric["aggregation"]
        label = metric["label"]
        values: list[float] = []
        for key in order:
            bucket = grouped[key]
            if aggregation == "count":
                values.append(float(len(bucket)))
                continue
            numbers = [num for num in (parse_number(item.get(column)) for item in bucket) if num is not None]
            if not numbers:
                values.append(0.0)
                continue
            if aggregation == "sum":
                values.append(float(sum(numbers)))
            else:
                values.append(float(sum(numbers) / len(numbers)))
        if aggregation != "count" and all(value == 0 for value in values):
            continue
        series.append({"name": label, "values": values})

    if not series:
        return None

    categories = order
    if chart_type == "pie":
        series = series[:1]
        pairs = list(zip(categories, series[0]["values"]))
        pairs.sort(key=lambda item: item[1], reverse=True)
        if len(pairs) > MAX_PIE_SLICES:
            head = pairs[: MAX_PIE_SLICES - 1]
            rest = sum(value for _, value in pairs[MAX_PIE_SLICES - 1 :])
            pairs = [*head, ("其他", rest)]
        categories = [name for name, _ in pairs]
        series[0]["values"] = [value for _, value in pairs]

    return {
        "title": title,
        "type": chart_type,
        "note": note,
        "categories": categories,
        "series": series,
    }


def materialize_chart_spec(raw_text: str, snapshot: dict[str, Any] | None) -> dict[str, Any]:
    sets = _snapshot_sets(snapshot)
    source_row_count = sum(len(item["rows"]) for item in sets)
    if not sets:
        return _empty_chart_spec(
            "当前没有可分析的查询结果，请先完成一次问数。",
            source_row_count=source_row_count,
        )

    parsed_objects = parse_json_objects(raw_text)
    plan = parsed_objects[-1] if parsed_objects else {}
    if not plan:
        return _empty_chart_spec(
            "未能理解图表方案，请再说明想按哪个维度看。",
            source_row_count=source_row_count,
        )

    result = sets[0]
    lookup = _column_lookup(result["columns"])
    rows = result["rows"]
    if not rows or not lookup:
        return _empty_chart_spec(
            "当前查询结果没有可绘图的数据行。",
            source_row_count=source_row_count,
        )

    feasible = plan.get("feasible") is not False
    summary = str(plan.get("summary") or "").strip()
    raw_charts = plan.get("charts")
    charts: list[dict[str, Any]] = []
    if feasible and isinstance(raw_charts, list):
        for item in raw_charts[:MAX_CHARTS]:
            if not isinstance(item, dict):
                continue
            chart_type = str(item.get("type") or "").strip().lower()
            if chart_type not in CHART_TYPES:
                continue
            title = str(item.get("title") or "").strip() or "查询结果"
            note = str(item.get("note") or "").strip()
            x_raw = item.get("x") if isinstance(item.get("x"), dict) else {}
            x_column = _resolve_column((x_raw or {}).get("column"), lookup)
            if not x_column:
                continue
            y_raw = item.get("y")
            metrics: list[dict[str, str]] = []
            y_items = y_raw if isinstance(y_raw, list) else [y_raw] if isinstance(y_raw, dict) else []
            for metric in y_items:
                if not isinstance(metric, dict):
                    continue
                column = _resolve_column(metric.get("column"), lookup)
                aggregation = str(metric.get("aggregation") or "count").strip().lower()
                if not column or aggregation not in CHART_AGGREGATIONS:
                    continue
                label = str(metric.get("label") or "").strip() or (
                    "数量" if aggregation == "count" else column
                )
                metrics.append({"column": column, "aggregation": aggregation, "label": label})
            if not metrics:
                continue
            if chart_type == "pie":
                metrics = metrics[:1]
            materialized = _aggregate_chart(
                chart_type=chart_type,
                title=title,
                note=note or f"基于当前返回的 {len(rows)} 行",
                x_column=x_column,
                metrics=metrics,
                rows=rows,
            )
            if materialized:
                charts.append(materialized)

    if not charts:
        return _empty_chart_spec(
            summary or "当前结果缺少合适的分析维度，请先查询带有分类或时间列的汇总/明细数据。",
            source_row_count=source_row_count,
        )

    return {
        "kind": "chart_spec",
        "feasible": True,
        "summary": summary or "已根据当前查询结果生成图表。",
        "source_row_count": source_row_count,
        "charts": charts,
    }
