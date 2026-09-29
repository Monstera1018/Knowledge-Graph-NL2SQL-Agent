"""基于已有查询结果规划并物化图表规格。"""

from __future__ import annotations

import json
import re
from typing import Any

CHART_TYPES = frozenset({"bar", "line", "pie", "area", "hbar", "radar", "scatter", "funnel"})
CHART_TYPE_ALIASES = {
    "column": "bar",
    "柱状": "bar",
    "柱状图": "bar",
    "折线": "line",
    "折线图": "line",
    "趋势": "line",
    "饼": "pie",
    "饼图": "pie",
    "环形": "pie",
    "面积": "area",
    "面积图": "area",
    "条形": "hbar",
    "条形图": "hbar",
    "横向柱": "hbar",
    "barh": "hbar",
    "雷达": "radar",
    "雷达图": "radar",
    "spider": "radar",
    "散点": "scatter",
    "散点图": "scatter",
    "点图": "scatter",
    "漏斗": "funnel",
    "漏斗图": "funnel",
}
CHART_AGGREGATIONS = frozenset({"count", "sum", "avg"})
MAX_CHARTS = 3
MAX_PIE_SLICES = 12
MAX_RADAR_AXES = 8
MAX_FUNNEL_SLICES = 8
MAX_SCATTER_POINTS = 200
SAMPLE_ROWS = 8
NO_CHART_SNAPSHOT_MESSAGE = (
    "当前会话还没有可分析的查询结果。请先提出一个数据问题，等系统查出表格后再让我生成图表。"
)
_JSON_OBJECT_RE = re.compile(r"\{.*?\}", re.DOTALL)
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
PALETTE_PRESETS: dict[str, list[str]] = {
    "default": ["#1677ff", "#69b1ff", "#36cfc9", "#faad14", "#722ed1"],
    "orange": ["#ea580c", "#f59e0b", "#fbbf24", "#fb923c", "#c2410c"],
    "blue": ["#1677ff", "#4096ff", "#69b1ff", "#91caff", "#bae0ff"],
    "green": ["#047857", "#059669", "#10b981", "#34d399", "#6ee7b7"],
    "purple": ["#6d28d9", "#7c3aed", "#8b5cf6", "#a78bfa", "#c4b5fd"],
}


def _relax_json_text(text: str) -> str:
    """尽量修好模型常见的非法 JSON：中文引号、尾逗号、字符串里未转义换行。"""
    cleaned = (text or "").replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
    out: list[str] = []
    in_string = False
    escape = False
    for char in cleaned:
        if in_string:
            if escape:
                out.append(char)
                escape = False
                continue
            if char == "\\":
                out.append(char)
                escape = True
                continue
            if char == '"':
                in_string = False
                out.append(char)
                continue
            if char == "\n":
                out.append("\\n")
                continue
            if char == "\r":
                continue
            out.append(char)
            continue
        if char == '"':
            in_string = True
        out.append(char)
    return "".join(out)


def parse_json_objects(text: str) -> list[dict[str, Any]]:
    cleaned = (text or "").strip()
    if "```" in cleaned:
        cleaned = re.sub(r"```(?:json)?\s*", "", cleaned).strip().rstrip("`").strip()
    candidates = [cleaned]
    relaxed = _relax_json_text(cleaned)
    if relaxed != cleaned:
        candidates.append(relaxed)

    decoder = json.JSONDecoder()
    # 先从第一个 { 解完整对象，避免把 analyses/charts 里的小对象当成整份方案。
    for source in candidates:
        start = source.find("{")
        if start < 0:
            continue
        try:
            value, _ = decoder.raw_decode(source[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            found = [item for item in value if isinstance(item, dict)]
            if found:
                return found

    objects: list[dict[str, Any]] = []
    for source in candidates:
        cursor = 0
        while cursor < len(source):
            start = source.find("{", cursor)
            if start < 0:
                break
            try:
                value, end = decoder.raw_decode(source[start:])
            except json.JSONDecodeError:
                cursor = start + 1
                continue
            if isinstance(value, dict):
                objects.append(value)
            cursor = start + end
        if objects:
            return objects

    for source in candidates:
        for match in _JSON_OBJECT_RE.finditer(source):
            try:
                value = json.loads(match.group(0))
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                objects.append(value)
        if objects:
            break
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


def _empty_chart_spec(
        summary: str,
        *,
        source_row_count: int = 0,
        source_truncated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "chart_spec",
        "feasible": False,
        "summary": summary,
        "source_row_count": source_row_count,
        "source_truncated": source_truncated,
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


_COLUMN_KEY_RE = re.compile(r"[\s`\"'“”‘’\[\]（）()]+")
_COLUMN_HINT_SPLIT_RE = re.compile(r"[\s()（）\[\]【】/、，,|+\-_—:：]+")


def _normalize_column_key(name: str) -> str:
    return _COLUMN_KEY_RE.sub("", name or "").casefold()


def _column_lookup(columns: list[str]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    normalized_hits: dict[str, list[str]] = {}
    for column in columns:
        lookup[column] = column
        lookup[column.casefold()] = column
        key = _normalize_column_key(column)
        if key:
            normalized_hits.setdefault(key, []).append(column)
    for key, names in normalized_hits.items():
        if len(names) == 1:
            lookup[key] = names[0]
    return lookup


def format_snapshot_result_overview(snapshot: dict[str, Any] | None) -> str:
    """把查询快照整理成给规划模型看的列、类型和样例行。"""
    sets = _snapshot_sets(snapshot)
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
            counts: dict[str, int] = {}
            numbers: list[float] = []
            for row in rows:
                raw_value = row.get(column)
                value = str(raw_value or "").strip()
                if not value:
                    continue
                counts[value] = counts.get(value, 0) + 1
                parsed = parse_number(raw_value)
                if parsed is not None:
                    numbers.append(parsed)
            unique_count = len(counts)
            uniques = list(counts.keys())[:8]
            unique_note = f"，样例取值：{'、'.join(uniques)}" if uniques else ""
            result_parts.append(f"- `{column}`（{kind}，约 {unique_count} 个不同值{unique_note}）")
            if kind in {"category", "time"} and 1 <= unique_count <= 12:
                dist = "、".join(f"{name} {counts[name]} 条" for name in counts)
                result_parts.append(f"  分布：{dist}")
            elif kind == "number" and numbers:
                is_identifier = unique_count >= len(rows) and len(rows) >= 8
                if not is_identifier:
                    total = sum(numbers)
                    avg = total / len(numbers)
                    result_parts.append(
                        f"  数值：最小 {min(numbers):g}，最大 {max(numbers):g}，合计 {total:g}，平均 {avg:g}"
                    )
                    if unique_count <= 8:
                        dist = "、".join(f"{name} {counts[name]} 条" for name in counts)
                        result_parts.append(f"  取值次数：{dist}")
        if sample:
            header = "| " + " | ".join(columns) + " |"
            sep = "| " + " | ".join("---" for _ in columns) + " |"
            body = [
                "| " + " | ".join(str(row.get(col) or "") for col in columns) + " |"
                for row in sample
            ]
            result_parts.extend(["", "样例行：", header, sep, *body, ""])
    return "\n".join(result_parts) if result_parts else "（无结果行）"


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


def normalize_palette_name(raw: str) -> str:
    text = (raw or "").strip().lower()
    mapping = {
        "default": "default",
        "orange": "orange",
        "amber": "orange",
        "yellow": "orange",
        "橙": "orange",
        "橙色": "orange",
        "橙黄": "orange",
        "橙黄色": "orange",
        "黄": "orange",
        "黄色": "orange",
        "蓝": "blue",
        "蓝色": "blue",
        "blue": "blue",
        "绿": "green",
        "绿色": "green",
        "green": "green",
        "紫": "purple",
        "紫色": "purple",
        "purple": "purple",
    }
    return mapping.get(text, "")


def infer_palette_name(text: str) -> str:
    source = text or ""
    if re.search(r"橙黄|橙|琥珀|amber|orange", source, re.I):
        return "orange"
    if re.search(r"(色调|配色|颜色|色).{0,8}黄|黄.{0,8}(色调|配色|颜色)", source):
        return "orange"
    if re.search(r"蓝|blue", source, re.I) and re.search(r"色调|配色|颜色|色", source):
        return "blue"
    if re.search(r"绿|green", source, re.I) and re.search(r"色调|配色|颜色|色", source):
        return "green"
    if re.search(r"紫|purple", source, re.I) and re.search(r"色调|配色|颜色|色", source):
        return "purple"
    return "default"


def resolve_palette(
        plan: dict[str, Any] | None,
        *,
        user_request: str = "",
        previous_palette: list[str] | None = None,
) -> list[str]:
    raw = plan.get("palette") if isinstance(plan, dict) else None
    if isinstance(raw, list):
        hexes = [
            str(item).strip()
            for item in raw
            if _HEX_COLOR_RE.match(str(item).strip())
        ]
        if hexes:
            return hexes[:8]
    if isinstance(raw, str) and raw.strip():
        name = normalize_palette_name(raw)
        if name in PALETTE_PRESETS:
            return list(PALETTE_PRESETS[name])
        if _HEX_COLOR_RE.match(raw.strip()):
            return [raw.strip()]
    inferred = infer_palette_name(user_request)
    if inferred != "default":
        return list(PALETTE_PRESETS[inferred])
    if previous_palette:
        kept = [str(item).strip() for item in previous_palette if _HEX_COLOR_RE.match(str(item).strip())]
        if kept:
            return kept[:8]
    return list(PALETTE_PRESETS["default"])


def _format_available_columns(columns: list[str]) -> str:
    names = [str(item).strip() for item in columns if str(item).strip()]
    if not names:
        return "（无）"
    return "、".join(names)


def _format_previous_charts(last_chart: dict[str, Any] | None) -> str:
    if not last_chart:
        return "（无）"
    if last_chart.get("feasible") and isinstance(last_chart.get("charts"), list) and last_chart.get("charts"):
        lines: list[str] = []
        for index, chart in enumerate(last_chart.get("charts") or [], start=1):
            if not isinstance(chart, dict):
                continue
            title = str(chart.get("title") or f"图表{index}").strip()
            chart_type = str(chart.get("type") or "").strip() or "未知"
            palette = chart.get("palette")
            palette_text = ""
            if isinstance(palette, list) and palette:
                palette_text = "，配色 " + "、".join(str(item) for item in palette[:5])
            lines.append(f"- {title}（{chart_type}{palette_text}）")
        return "\n".join(lines) if lines else "（无）"
    reason = str(last_chart.get("summary") or last_chart.get("reason") or "").strip()
    if reason:
        return "\n".join([
            "上次未能生成图表，请改用结果概要中的列名重试，不要沿用失败方案。",
            f"失败原因：{reason}",
        ])
    return "上次未能生成图表。请改用结果概要中的列名重试。"


def format_chart_planner_user_message(
        *,
        user_request: str,
        snapshot: dict[str, Any],
        last_chart: dict[str, Any] | None = None,
) -> str:
    previous_question = str(snapshot.get("user_question") or "").strip() or "（无）"
    tables = snapshot.get("selected_tables")
    table_lines = (
        "\n".join(f"- `{name}`" for name in tables if str(name).strip())
        if isinstance(tables, list) and tables
        else "（无）"
    )
    schema_context = str(snapshot.get("schema_context") or "").strip() or "（无）"
    result_text = format_snapshot_result_overview(snapshot)
    allowed_columns: list[str] = []
    sets = _snapshot_sets(snapshot)
    if sets:
        allowed_columns = [str(item).strip() for item in sets[0].get("columns") or [] if str(item).strip()]
    allowed_text = "、".join(f"`{name}`" for name in allowed_columns) if allowed_columns else "（无）"
    return "\n".join([
        "## 用户出图要求",
        "",
        (user_request or "").strip() or "（无）",
        "",
        "## 本轮允许绑定的结果列",
        "",
        allowed_text,
        "",
        "只能用上面这些列名。表结构里有、但这里没有的字段不能作为图表维度或指标。",
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
        "## 上一张图表",
        "",
        _format_previous_charts(last_chart),
        "",
        "请只输出 JSON 图表规格。列名必须来自上述结果概要。",
    ])


def _guess_aggregation(column: str) -> str:
    text = column or ""
    if re.search(r"平均|均价|单价|占比", text):
        return "avg"
    if re.search(r"(用户)?数$|户数|次数", text):
        return "count"
    if re.search(r"金额|电量|电费|费用|合计|应收|实收", text):
        return "sum"
    return "sum"


def _coerce_metric(item: object) -> dict[str, str] | None:
    if isinstance(item, str) and item.strip():
        column = item.strip()
        return {"column": column, "aggregation": _guess_aggregation(column), "label": ""}
    if not isinstance(item, dict):
        return None
    column = str(item.get("column") or item.get("name") or item.get("field") or "").strip()
    if not column:
        return None
    aggregation = str(item.get("aggregation") or item.get("agg") or "").strip().lower()
    if aggregation not in CHART_AGGREGATIONS:
        aggregation = _guess_aggregation(column)
    label = str(item.get("label") or "").strip()
    return {"column": column, "aggregation": aggregation, "label": label}


def coerce_chart_plan(raw: object, *, title: str = "") -> dict[str, Any] | None:
    """把模型常见的简写图定义收成可绑定的 chart plan。"""
    if not isinstance(raw, dict) or not raw:
        return None
    chart = dict(raw)
    hint = f"{title} {chart.get('title') or ''}"
    chart_type = str(chart.get("type") or "").strip().lower()
    if chart_type in CHART_TYPE_ALIASES:
        chart_type = CHART_TYPE_ALIASES[chart_type]
    if chart_type not in CHART_TYPES:
        if re.search(r"雷达|spider", hint, re.I):
            chart_type = "radar"
        elif re.search(r"散点|点图|scatter", hint, re.I):
            chart_type = "scatter"
        elif re.search(r"漏斗|funnel", hint, re.I):
            chart_type = "funnel"
        elif re.search(r"面积|area", hint, re.I):
            chart_type = "area"
        elif re.search(r"条形|横向|hbar|barh", hint, re.I):
            chart_type = "hbar"
        elif re.search(r"折线|趋势|line", hint, re.I):
            chart_type = "line"
        elif re.search(r"饼|环形|pie", hint, re.I):
            chart_type = "pie"
        else:
            chart_type = "bar"
    chart["type"] = chart_type

    x_raw = chart.get("x")
    if isinstance(x_raw, str) and x_raw.strip():
        chart["x"] = {"column": x_raw.strip(), "role": "dimension"}
    elif isinstance(x_raw, dict):
        column = str(x_raw.get("column") or x_raw.get("name") or x_raw.get("field") or "").strip()
        if column:
            chart["x"] = {**x_raw, "column": column, "role": str(x_raw.get("role") or "dimension")}

    y_raw = chart.get("y")
    if isinstance(y_raw, str) and y_raw.strip():
        metric = _coerce_metric(y_raw)
        chart["y"] = [metric] if metric else []
    elif isinstance(y_raw, dict):
        metric = _coerce_metric(y_raw)
        chart["y"] = [metric] if metric else []
    elif isinstance(y_raw, list):
        chart["y"] = [item for item in (_coerce_metric(value) for value in y_raw) if item]
    return chart


def _resolve_column(name: object, columns: list[str]) -> str:
    text = str(name or "").strip().strip("`\"'")
    if not text:
        return ""
    lookup = _column_lookup(columns)
    exact = lookup.get(text) or lookup.get(text.casefold()) or lookup.get(_normalize_column_key(text))
    if exact:
        return exact
    # 复合名如「客户名称(记账单号)」先按片段精确命中，避免同时匹配两列后直接失败。
    for part in _COLUMN_HINT_SPLIT_RE.split(text):
        hint = part.strip()
        if len(hint) < 2:
            continue
        hit = lookup.get(hint) or lookup.get(hint.casefold()) or lookup.get(_normalize_column_key(hint))
        if hit:
            return hit
    key = _normalize_column_key(text)
    if not key or len(key) < 2:
        return ""
    hits = [
        column for column in columns
        if key in _normalize_column_key(column) or _normalize_column_key(column) in key
    ]
    unique = list(dict.fromkeys(hits))
    if len(unique) == 1:
        return unique[0]
    if unique:
        ranked: list[tuple[int, int, str]] = []
        for column in unique:
            column_key = _normalize_column_key(column)
            pos = key.find(column_key)
            if pos >= 0:
                ranked.append((pos, -len(column_key), column))
        ranked.sort()
        if ranked:
            return ranked[0][2]
    return ""


def infer_fallback_chart_plan(heading: str, snapshot: dict[str, Any] | None) -> dict[str, Any] | None:
    """分类分析没给出可绑定的图时，按标题和结果列补一张。"""
    sets = _snapshot_sets(snapshot)
    if not sets:
        return None
    columns = sets[0]["columns"]
    rows = sets[0]["rows"]
    if not columns or not rows:
        return None
    category_cols = [
        column for column in columns
        if infer_column_kind(rows, column) in {"category", "time"}
    ]
    number_cols = [
        column for column in columns
        if infer_column_kind(rows, column) == "number"
        and not (len({str(row.get(column) or "").strip() for row in rows if str(row.get(column) or "").strip()}) >= len(rows) and len(rows) >= 8)
    ]
    heading_key = _normalize_column_key(heading)
    x_column = ""
    for column in category_cols:
        key = _normalize_column_key(column)
        if key and heading_key and (key in heading_key or heading_key in key):
            x_column = column
            break
    if not x_column:
        scored: list[tuple[int, str]] = []
        for column in category_cols:
            uniques = {str(row.get(column) or "").strip() for row in rows if str(row.get(column) or "").strip()}
            if 2 <= len(uniques) <= 12:
                scored.append((len(uniques), column))
        scored.sort(reverse=True)
        x_column = scored[0][1] if scored else (category_cols[0] if category_cols else "")
    if not x_column:
        return None
    if number_cols:
        y_column = number_cols[0]
        aggregation = "sum"
        label = y_column
    else:
        y_column = columns[0]
        aggregation = "count"
        label = "数量"
    chart_type = "line" if infer_column_kind(rows, x_column) == "time" or re.search(r"月|期|趋势", heading) else "bar"
    if re.search(r"雷达", heading):
        chart_type = "radar"
    elif re.search(r"散点", heading):
        chart_type = "scatter"
    elif re.search(r"漏斗", heading):
        chart_type = "funnel"
    elif re.search(r"面积", heading):
        chart_type = "area"
    elif re.search(r"条形|横向", heading):
        chart_type = "hbar"
    elif re.search(r"占比|结构", heading):
        chart_type = "pie"
    return {
        "title": heading or x_column,
        "type": chart_type,
        "x": {"column": x_column, "role": "dimension"},
        "y": [{"column": y_column, "aggregation": aggregation, "label": label}],
    }


def _chart_source_note(note: str, *, row_count: int, truncated: bool) -> str:
    text = (note or "").strip() or f"基于当前返回的 {row_count} 行"
    if truncated and "截断" not in text:
        text = f"{text}；结果已截断，图中数字仅基于这些返回行"
    return text


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
    if chart_type in {"pie", "funnel"}:
        series = series[:1]
        limit = MAX_FUNNEL_SLICES if chart_type == "funnel" else MAX_PIE_SLICES
        pairs = list(zip(categories, series[0]["values"]))
        pairs.sort(key=lambda item: item[1], reverse=True)
        if len(pairs) > limit:
            head = pairs[: limit - 1]
            rest = sum(value for _, value in pairs[limit - 1 :])
            pairs = [*head, ("其他", rest)]
        categories = [name for name, _ in pairs]
        series[0]["values"] = [value for _, value in pairs]
    elif chart_type == "radar":
        if len(categories) < 3:
            return None
        if len(categories) > MAX_RADAR_AXES:
            pairs = list(zip(categories, series[0]["values"]))
            pairs.sort(key=lambda item: item[1], reverse=True)
            keep = {name for name, _ in pairs[:MAX_RADAR_AXES]}
            indexes = [index for index, name in enumerate(categories) if name in keep][:MAX_RADAR_AXES]
            categories = [categories[index] for index in indexes]
            for item in series:
                item["values"] = [item["values"][index] for index in indexes]

    return {
        "title": title,
        "type": chart_type,
        "note": note,
        "categories": categories,
        "series": series,
    }


def _scatter_chart(
        *,
        title: str,
        note: str,
        x_column: str,
        metrics: list[dict[str, str]],
        rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """两个数值列逐行打点；X 不是数值时退回按分类汇总后再画散点。"""
    if not metrics:
        return None
    metric = metrics[0]
    points: list[tuple[float, float]] = []
    for row in rows:
        x_value = parse_number(row.get(x_column))
        if x_value is None:
            continue
        y_value = parse_number(row.get(metric["column"]))
        if y_value is None:
            continue
        points.append((x_value, float(y_value)))
    if len(points) < 2:
        return None
    if len(points) > MAX_SCATTER_POINTS:
        points = points[:MAX_SCATTER_POINTS]
        if "截断" not in note:
            note = f"{note}；散点仅展示前 {MAX_SCATTER_POINTS} 个点"
    return {
        "title": title,
        "type": "scatter",
        "note": note,
        "categories": [f"{item[0]:g}" for item in points],
        "series": [{"name": metric["label"], "values": [item[1] for item in points]}],
    }


def materialize_chart_spec(
        raw_text: str,
        snapshot: dict[str, Any] | None,
        *,
        user_request: str = "",
        last_chart: dict[str, Any] | None = None,
) -> dict[str, Any]:
    sets = _snapshot_sets(snapshot)
    source_row_count = sum(len(item["rows"]) for item in sets)
    source_truncated = bool((snapshot or {}).get("truncated")) or any(
        isinstance(item, dict) and item.get("truncated") is True
        for item in ((snapshot or {}).get("result_sets") or [])
    )
    if not sets:
        return _empty_chart_spec(
            "当前没有可分析的查询结果，请先完成一次问数。",
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    parsed_objects = parse_json_objects(raw_text)
    plan = parsed_objects[-1] if parsed_objects else {}
    if not plan:
        return _empty_chart_spec(
            "未能理解图表方案，请再说明想按哪个维度看。",
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    result = sets[0]
    rows = result["rows"]
    if not rows or not result["columns"]:
        return _empty_chart_spec(
            "当前查询结果没有可绘图的数据行。",
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    summary = str(plan.get("summary") or "").strip()
    raw_charts = plan.get("charts")
    # 模型常因复合列名误判不可行；只要结果里有列，就继续尝试绑定或兜底出图。

    charts: list[dict[str, Any]] = []
    failures: list[str] = []
    available = _format_available_columns(result["columns"])
    if not isinstance(raw_charts, list) or not raw_charts:
        failures.append(f"图表方案没有给出可绑定的图表定义。当前可用列：{available}。")
    else:
        for item in raw_charts[:MAX_CHARTS]:
            item = coerce_chart_plan(item, title=str((item or {}).get("title") or "") if isinstance(item, dict) else "")
            if not isinstance(item, dict):
                failures.append("图表定义不是对象，已跳过。")
                continue
            chart_type = str(item.get("type") or "").strip().lower()
            if chart_type not in CHART_TYPES:
                failures.append("图表类型不受支持，仅支持柱状、折线、面积、条形、饼图、雷达、散点或漏斗。")
                continue
            title = str(item.get("title") or "").strip() or "查询结果"
            note = str(item.get("note") or "").strip()
            x_raw = item.get("x") if isinstance(item.get("x"), dict) else {}
            requested_x = str((x_raw or {}).get("column") or "").strip()
            x_column = _resolve_column(requested_x, result["columns"])
            if not x_column:
                failures.append(
                    f"维度列「{requested_x or '未指定'}」不在当前结果中。可用列：{available}。"
                )
                continue
            y_raw = item.get("y")
            metrics: list[dict[str, str]] = []
            y_items = y_raw if isinstance(y_raw, list) else [y_raw] if isinstance(y_raw, dict) else []
            requested_y: list[str] = []
            for metric in y_items:
                if not isinstance(metric, dict):
                    continue
                requested_column = str(metric.get("column") or "").strip()
                if requested_column:
                    requested_y.append(requested_column)
                column = _resolve_column(requested_column, result["columns"])
                aggregation = str(metric.get("aggregation") or "count").strip().lower()
                if aggregation not in CHART_AGGREGATIONS:
                    continue
                if not column:
                    if aggregation == "count":
                        column = x_column
                    else:
                        continue
                label = str(metric.get("label") or "").strip() or (
                    "数量" if aggregation == "count" else column
                )
                metrics.append({"column": column, "aggregation": aggregation, "label": label})
            if not metrics:
                y_text = "、".join(requested_y) if requested_y else "未指定"
                failures.append(
                    f"指标列「{y_text}」无法绑定到当前结果，或聚合方式不受支持。可用列：{available}。"
                )
                continue
            if chart_type in {"pie", "funnel", "scatter"}:
                metrics = metrics[:1]
            source_note = _chart_source_note(note, row_count=len(rows), truncated=source_truncated)
            if chart_type == "scatter":
                materialized = _scatter_chart(
                    title=title,
                    note=source_note,
                    x_column=x_column,
                    metrics=metrics,
                    rows=rows,
                )
                if materialized is None:
                    materialized = _aggregate_chart(
                        chart_type="scatter",
                        title=title,
                        note=source_note,
                        x_column=x_column,
                        metrics=metrics,
                        rows=rows,
                    )
            else:
                materialized = _aggregate_chart(
                    chart_type=chart_type,
                    title=title,
                    note=source_note,
                    x_column=x_column,
                    metrics=metrics,
                    rows=rows,
                )
            if materialized:
                charts.append(materialized)
            else:
                failures.append(f"按「{x_column}」汇总后没有可绘制的数据点。")

    if not charts:
        fallback = infer_fallback_chart_plan(summary or user_request, snapshot)
        fallback_x = str(
            ((fallback.get("x") or {}) if isinstance(fallback.get("x"), dict) else {}).get("column")
            or fallback.get("x")
            or ""
        )
        already_tried = {
            str(
                ((item.get("x") or {}) if isinstance(item.get("x"), dict) else {}).get("column")
                or item.get("x")
                or ""
            )
            for item in (raw_charts or [])
            if isinstance(item, dict)
        }
        if fallback and fallback_x and fallback_x not in already_tried:
            retry = materialize_chart_spec(
                json.dumps({"feasible": True, "summary": summary, "charts": [fallback]}, ensure_ascii=False),
                snapshot,
                user_request=user_request,
                last_chart=last_chart,
            )
            if retry.get("feasible"):
                return retry
        reason = " ".join(failures) if failures else (
            f"当前结果缺少合适的分析维度。可用列：{available}。"
        )
        return _empty_chart_spec(
            reason,
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    previous_palette = None
    if last_chart and isinstance(last_chart.get("charts"), list):
        for item in last_chart["charts"]:
            if isinstance(item, dict) and isinstance(item.get("palette"), list):
                previous_palette = [str(color) for color in item["palette"]]
                break
        if previous_palette is None and isinstance(last_chart.get("palette"), list):
            previous_palette = [str(color) for color in last_chart["palette"]]
    palette = resolve_palette(
        plan,
        user_request=user_request,
        previous_palette=previous_palette,
    )
    for chart in charts:
        chart["palette"] = list(palette)

    return {
        "kind": "chart_spec",
        "feasible": True,
        "summary": summary or "已根据当前查询结果生成图表。",
        "source_row_count": source_row_count,
        "source_truncated": source_truncated,
        "palette": list(palette),
        "charts": charts,
    }
