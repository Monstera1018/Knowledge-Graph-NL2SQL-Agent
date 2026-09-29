"""基于查询快照生成三部分分析报告，并导出 Word。"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from adk_agents.copilot.chart import (
    _format_previous_charts,
    _snapshot_sets,
    coerce_chart_plan,
    format_snapshot_result_overview,
    infer_column_kind,
    infer_fallback_chart_plan,
    materialize_chart_spec,
    parse_json_objects,
    parse_number,
)

MAX_REPORT_ANALYSES = 3
REPORTS_DIR = Path(__file__).resolve().parents[2] / "data" / "reports"

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\s]+')


def reports_dir() -> Path:
    path = REPORTS_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _empty_report(
        summary: str,
        *,
        source_row_count: int = 0,
        source_truncated: bool = False,
) -> dict[str, Any]:
    return {
        "kind": "analysis_report",
        "feasible": False,
        "summary": summary,
        "title": "",
        "overview": "",
        "conclusions": "",
        "analyses": [],
        "file_id": "",
        "filename": "",
        "source_row_count": source_row_count,
        "source_truncated": source_truncated,
    }


def format_report_planner_user_message(
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
    allowed_columns: list[str] = []
    sets = _snapshot_sets(snapshot)
    if sets:
        allowed_columns = [str(item).strip() for item in sets[0].get("columns") or [] if str(item).strip()]
    allowed_text = "、".join(f"`{name}`" for name in allowed_columns) if allowed_columns else "（无）"
    return "\n".join([
        "## 用户报告要求",
        "",
        (user_request or "").strip() or "请基于当前查询结果生成分析报告。",
        "",
        "## 本轮允许绑定的结果列",
        "",
        allowed_text,
        "",
        "只能用上面这些列名。表结构里有、但这里没有的字段不能作为分析维度或指标。",
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
        format_snapshot_result_overview(snapshot),
        "",
        "## 上一张图表",
        "",
        _format_previous_charts(last_chart),
        "",
        "请只输出完整可解析的 JSON。列名必须来自上述结果列。正文用中文，结论要能被当前数字支撑。",
    ])


def _is_materialized_chart(chart: dict[str, Any] | None) -> bool:
    return bool(isinstance(chart, dict) and chart.get("categories"))


def _pick_report_plan(objects: list[dict[str, Any]]) -> dict[str, Any]:
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in objects:
        if not isinstance(item, dict):
            continue
        score = 0
        if item.get("overview"):
            score += 2
        if isinstance(item.get("analyses"), list) and item.get("analyses"):
            score += 3
        if item.get("conclusions"):
            score += 2
        if item.get("title"):
            score += 1
        if item.get("feasible") is True:
            score += 1
        if score:
            scored.append((score, item))
    if scored:
        scored.sort(key=lambda pair: pair[0])
        return scored[-1][1]
    return {}


def _report_has_body(plan: dict[str, Any] | None) -> bool:
    if not isinstance(plan, dict):
        return False
    if str(plan.get("overview") or "").strip() or str(plan.get("conclusions") or "").strip():
        return True
    analyses = plan.get("analyses")
    return isinstance(analyses, list) and any(isinstance(item, dict) for item in analyses)


def _chart_narrative(chart: dict[str, Any]) -> str:
    categories = [str(item).strip() for item in (chart.get("categories") or []) if str(item).strip()]
    series = chart.get("series") if isinstance(chart.get("series"), list) else []
    values: list[float] = []
    if series and isinstance(series[0], dict):
        raw_values = series[0].get("values") or []
        if isinstance(raw_values, list):
            values = [float(item) for item in raw_values]
    pairs = [
        (name, values[index] if index < len(values) else 0.0)
        for index, name in enumerate(categories)
    ]
    if not pairs:
        return "该维度在当前返回行中没有可汇总的分类。"
    pairs.sort(key=lambda item: item[1], reverse=True)
    total = sum(item[1] for item in pairs)
    parts = [f"当前返回行覆盖 {len(pairs)} 个分类"]
    if total:
        parts.append(f"合计 {total:g}")
    parts.append(f"最高的是{pairs[0][0]}（{pairs[0][1]:g}）")
    if len(pairs) > 1:
        parts.append(f"其次是{pairs[1][0]}（{pairs[1][1]:g}）")
    return "，".join(parts) + "。数字仅基于当前返回行。"


def _overview_from_snapshot(snapshot: dict[str, Any] | None, user_request: str) -> str:
    if not snapshot:
        return ""
    question = str(snapshot.get("user_question") or user_request or "当前查询结果").strip()
    row_count = int(snapshot.get("row_count") or 0)
    truncated = bool(snapshot.get("truncated"))
    lines = [f"本报告基于「{question}」的查询结果，共返回 {row_count} 行。"]
    if truncated:
        lines.append("结果已截断，下文数字仅基于这些返回行，不能当作全量统计。")
    sets = _snapshot_sets(snapshot)
    if not sets:
        return "".join(f"{item}\n" for item in lines).strip()
    columns = [str(item).strip() for item in sets[0].get("columns") or [] if str(item).strip()]
    rows = sets[0].get("rows") or []
    if columns:
        lines.append(f"结果列包括：{'、'.join(columns)}。")
    for column in columns:
        if infer_column_kind(rows, column) not in {"category", "time"}:
            continue
        counts: dict[str, int] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = str(row.get(column) or "").strip()
            if value:
                counts[value] = counts.get(value, 0) + 1
        if 2 <= len(counts) <= 12:
            dist = "、".join(f"{name} {count} 条" for name, count in sorted(counts.items(), key=lambda item: item[1], reverse=True))
            lines.append(f"按{column}看，分布为：{dist}。")
            break
    return "\n".join(lines)


def _analyses_from_last_charts(last_chart: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not last_chart or last_chart.get("feasible") is False:
        return []
    charts = last_chart.get("charts")
    if not isinstance(charts, list):
        return []
    analyses: list[dict[str, Any]] = []
    for chart in charts[:MAX_REPORT_ANALYSES]:
        if not _is_materialized_chart(chart):
            continue
        title = str(chart.get("title") or "分析维度").strip()
        analyses.append({
            "heading": title,
            "narrative": _chart_narrative(chart),
            "chart": chart,
        })
    return analyses


def _analyses_from_snapshot(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    sets = _snapshot_sets(snapshot)
    if not sets:
        return []
    columns = [str(item).strip() for item in sets[0].get("columns") or [] if str(item).strip()]
    rows = sets[0].get("rows") or []
    analyses: list[dict[str, Any]] = []
    for column in columns:
        if infer_column_kind(rows, column) not in {"category", "time"}:
            continue
        plan = infer_fallback_chart_plan(f"按{column}分布", snapshot)
        if not plan:
            continue
        analyses.append({
            "heading": f"按{column}分布",
            "narrative": f"按「{column}」对当前返回行做分类汇总。",
            "chart": plan,
        })
        if len(analyses) >= MAX_REPORT_ANALYSES:
            break
    return analyses


def _conclusions_from_analyses(analyses: list[dict[str, Any]], *, truncated: bool) -> str:
    items: list[str] = []
    for item in analyses:
        chart = item.get("chart") if isinstance(item, dict) else None
        if not _is_materialized_chart(chart):
            heading = str((item or {}).get("heading") or "").strip()
            if heading:
                items.append(f"{heading}已纳入分类分析，需结合图表查看构成。")
            continue
        narrative = _chart_narrative(chart)
        heading = str(item.get("heading") or chart.get("title") or "该维度").strip()
        items.append(f"{heading}：{narrative}")
    if truncated:
        items.append("因结果截断，以上结论只反映已返回行，扩大查询范围后可能变化。")
    if not items:
        return "当前结果可以先看总量和已有分类，再按需要补充分析维度。"
    return "\n".join(f"{index}、{text}" for index, text in enumerate(items, start=1))


def synthesize_report_plan(
        snapshot: dict[str, Any] | None,
        *,
        user_request: str = "",
        last_chart: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """模型方案解析失败时，用快照和已有图表拼一份可落地的报告。"""
    if not snapshot:
        return None
    sets = _snapshot_sets(snapshot)
    if not sets or not sets[0].get("rows"):
        return None
    analyses = _analyses_from_last_charts(last_chart) or _analyses_from_snapshot(snapshot)
    title = str(snapshot.get("user_question") or user_request or "数据分析报告").strip()
    if not title.endswith("分析报告"):
        title = f"{title}分析报告"
    return {
        "feasible": True,
        "summary": "已根据当前查询结果自动整理分析报告。",
        "title": title,
        "overview": _overview_from_snapshot(snapshot, user_request),
        "analyses": analyses,
        "conclusions": _conclusions_from_analyses(
            analyses,
            truncated=bool(snapshot.get("truncated")),
        ),
    }


def _paragraphs(text: str) -> list[str]:
    chunks = [item.strip() for item in re.split(r"\n{2,}", (text or "").strip()) if item.strip()]
    if chunks:
        return chunks
    lines = [item.strip() for item in (text or "").splitlines() if item.strip()]
    return lines


def _conclusions_text(raw: object) -> str:
    if isinstance(raw, list):
        items = [str(item).strip() for item in raw if str(item).strip()]
        if not items:
            return ""
        numbered = []
        for index, item in enumerate(items, start=1):
            if re.match(r"^\d+[\.、]", item):
                numbered.append(item)
            else:
                numbered.append(f"{index}、{item}")
        return "\n".join(numbered)
    return str(raw or "").strip()


def _sanitize_filename(title: str) -> str:
    stem = _UNSAFE_FILENAME_RE.sub("_", (title or "").strip())[:40].strip("._")
    if not stem:
        stem = "分析报告"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stem}_{stamp}.docx"


def _configure_matplotlib() -> Any:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams["axes.unicode_minus"] = False
    candidates = [
        "Microsoft YaHei",
        "SimHei",
        "PingFang SC",
        "Noto Sans CJK SC",
        "Source Han Sans SC",
    ]
    try:
        from matplotlib import font_manager

        available = {item.name for item in font_manager.fontManager.ttflist}
        chosen = next((name for name in candidates if name in available), None)
        if chosen:
            plt.rcParams["font.sans-serif"] = [chosen, "DejaVu Sans"]
        else:
            plt.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans"]
    except Exception:
        plt.rcParams["font.sans-serif"] = candidates + ["DejaVu Sans"]
    return plt


_ANTD_PRIMARY = "#1677ff"
_DEFAULT_REPORT_PALETTE = ["#1677ff", "#69b1ff", "#36cfc9", "#faad14", "#722ed1"]


def _resolve_report_palette(palette: list[str]) -> list[str]:
    return palette or list(_DEFAULT_REPORT_PALETTE)


def _hex_to_rgba(hex_color: str, alpha: float) -> tuple[float, float, float, float]:
    raw = (hex_color or "").lstrip("#")
    if len(raw) == 3:
        raw = "".join(part * 2 for part in raw)
    if len(raw) != 6:
        return (22 / 255, 119 / 255, 255 / 255, alpha)
    value = int(raw, 16)
    return ((value >> 16) & 255) / 255, ((value >> 8) & 255) / 255, (value & 255) / 255, alpha


def _format_chart_number(value: float) -> str:
    if not math.isfinite(value):
        return "—"
    absolute = abs(value)
    if absolute >= 10000:
        wan = value / 10000
        return f"{wan:.0f}万" if absolute >= 100000 else f"{wan:.1f}万"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}"
    return f"{value:,.2f}"


def _peak_index(values: list[float]) -> int:
    if not values:
        return 0
    return max(range(len(values)), key=lambda index: values[index])


def _style_report_axes(ax, *, horizontal: bool = False) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_visible(not horizontal)
    ax.spines["left"].set_color("#f0f0f0")
    ax.spines["bottom"].set_color("#f0f0f0")
    ax.tick_params(colors="#8c8c8c", labelsize=9, length=0)
    grid_axis = "x" if horizontal else "y"
    ax.grid(True, axis=grid_axis, linestyle="--", linewidth=0.8, color="#f0f0f0")
    ax.set_axisbelow(True)


def _add_average_line(ax, values: list[float], *, horizontal: bool = False) -> None:
    finite = [item for item in values if math.isfinite(item)]
    if not finite:
        return
    average = sum(finite) / len(finite)
    if horizontal:
        ax.axvline(average, color="#91caff", linestyle="--", linewidth=1, zorder=1)
    else:
        ax.axhline(average, color="#91caff", linestyle="--", linewidth=1, zorder=1)
        ax.text(
            0.99,
            average,
            f"均值 {_format_chart_number(average)}",
            transform=ax.get_yaxis_transform(),
            ha="right",
            va="bottom",
            fontsize=8,
            color="#8c8c8c",
        )


def render_chart_png(chart: dict[str, Any]) -> bytes:
    plt = _configure_matplotlib()
    categories = [str(item) for item in (chart.get("categories") or [])]
    series = chart.get("series") if isinstance(chart.get("series"), list) else []
    palette = _resolve_report_palette(
        [str(item) for item in (chart.get("palette") or []) if str(item).strip()]
    )
    chart_type = str(chart.get("type") or "bar").strip().lower()
    title = str(chart.get("title") or "").strip()
    primary = palette[0] if palette else _ANTD_PRIMARY

    polar = chart_type == "radar"
    fig, ax = plt.subplots(
        figsize=(8.2, 4.4),
        dpi=140,
        subplot_kw={"projection": "polar"} if polar else None,
    )
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#ffffff")
    if title:
        ax.set_title(title, fontsize=13, pad=12, color="#262626", loc="left", fontweight="bold")

    if chart_type == "pie" and series:
        values = [float(item) for item in (series[0].get("values") or [])][: len(categories)]
        if not values or not any(value > 0 for value in values):
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
        else:
            colors = [palette[index % len(palette)] for index in range(len(values))]
            total = sum(values)
            peak = _peak_index(values)
            labels = [
                f"{name}\n{value / total * 100:.2f}%" if total else name
                for name, value in zip(categories[: len(values)], values)
            ]
            explode = [0.03 if index == peak else 0 for index in range(len(values))]
            wedges, texts = ax.pie(
                values,
                labels=labels,
                colors=colors,
                startangle=90,
                explode=explode,
                wedgeprops={"width": 0.32, "edgecolor": "#ffffff", "linewidth": 2},
                textprops={"color": "#595959", "fontsize": 9},
                labeldistance=1.18,
            )
            wedges[peak].set_linewidth(3)
            ax.text(0, 0.06, _format_chart_number(total), ha="center", va="center", color=primary, fontsize=16, fontweight="bold")
            ax.text(0, -0.16, "总计", ha="center", va="center", color="#8c8c8c", fontsize=9)
            ax.legend(
                wedges,
                categories[: len(values)],
                loc="center left",
                bbox_to_anchor=(1.08, 0.5),
                frameon=False,
                fontsize=9,
                handlelength=0.8,
            )
            ax.axis("equal")
    elif chart_type == "funnel" and series:
        values = [float(item) for item in (series[0].get("values") or [])][: len(categories)]
        if not values:
            ax.text(0.5, 0.5, "暂无数据", ha="center", va="center")
        else:
            count = len(values)
            ax.set_xlim(0, 1)
            ax.invert_yaxis()
            for index, (name, value) in enumerate(zip(categories[:count], values)):
                width = max(0.28, 0.92 - index * 0.14)
                left = (1 - width) / 2
                color = palette[index % len(palette)]
                ax.barh(
                    index,
                    width,
                    left=left,
                    height=0.7,
                    color=_hex_to_rgba(color, 0.92),
                    edgecolor="#ffffff",
                    linewidth=2,
                )
                ax.text(
                    0.5,
                    index,
                    f"{name}  {_format_chart_number(value)}",
                    ha="center",
                    va="center",
                    color="#ffffff",
                    fontsize=9,
                )
            ax.set_yticks([])
            ax.set_xticks([])
            for spine in ax.spines.values():
                spine.set_visible(False)
    elif chart_type == "radar" and categories:
        axes = len(categories)
        angles = [index / axes * 2 * math.pi for index in range(axes)]
        angles.append(angles[0])
        ax.set_facecolor("#f7faff")
        for index, item in enumerate(series):
            if not isinstance(item, dict):
                continue
            values = [float(value) for value in (item.get("values") or [])][:axes]
            values.extend([0.0] * (axes - len(values)))
            values.append(values[0])
            color = palette[index] if index < len(palette) else _ANTD_PRIMARY
            ax.plot(angles, values, marker="o", markersize=5, linewidth=2, label=str(item.get("name") or f"系列{index + 1}"), color=color)
            ax.fill(angles, values, alpha=0.28, color=color)
        ax.set_xticks(angles[:-1])
        ax.set_xticklabels(categories, color="#595959", fontsize=9)
        ax.grid(color="#d6e4ff", linestyle="-", linewidth=0.8)
        ax.spines["polar"].set_color("#d6e4ff")
        if len(series) > 1:
            ax.legend(loc="upper right", bbox_to_anchor=(1.25, 1.1), frameon=False)
    elif chart_type == "scatter" and series:
        y_values = [float(value) for value in (series[0].get("values") or [])]
        x_values: list[float] = []
        for index, name in enumerate(categories):
            parsed = parse_number(name)
            x_values.append(parsed if parsed is not None else float(index))
        count = min(len(x_values), len(y_values))
        ax.scatter(
            x_values[:count],
            y_values[:count],
            color=_hex_to_rgba(primary, 0.82),
            edgecolors="#ffffff",
            linewidths=1,
            s=48,
            zorder=3,
        )
        _style_report_axes(ax)
    else:
        x_pos = list(range(len(categories)))
        rotate = 25 if len(categories) > 6 else 0
        if chart_type in {"line", "area"}:
            for index, item in enumerate(series):
                if not isinstance(item, dict):
                    continue
                values = [float(value) for value in (item.get("values") or [])]
                color = palette[index] if index < len(palette) else _ANTD_PRIMARY
                xs = x_pos[: len(values)]
                ax.plot(xs, values, linewidth=3, color=color, zorder=3, label=str(item.get("name") or f"系列{index + 1}"))
                ax.fill_between(xs, values, color=_hex_to_rgba(color, 0.28), linewidth=0)
                peak = _peak_index(values)
                for point_index, (x, value) in enumerate(zip(xs, values)):
                    is_peak = point_index == peak
                    ax.scatter(
                        [x],
                        [value],
                        s=90 if is_peak else 48,
                        facecolors=color if is_peak else "#ffffff",
                        edgecolors=color,
                        linewidths=2,
                        zorder=4,
                    )
                if values:
                    ax.annotate(
                        f"最大 {_format_chart_number(values[peak])}",
                        (xs[peak], values[peak]),
                        textcoords="offset points",
                        xytext=(0, 10),
                        ha="center",
                        fontsize=8,
                        color=color,
                    )
                _add_average_line(ax, values)
        elif chart_type == "hbar":
            height = 0.56 / max(1, len(series))
            for index, item in enumerate(series):
                if not isinstance(item, dict):
                    continue
                values = [float(value) for value in (item.get("values") or [])]
                offset = (index - (len(series) - 1) / 2) * height
                color = palette[index] if index < len(palette) else _ANTD_PRIMARY
                peak = _peak_index(values)
                bar_colors = [
                    color if data_index == peak else _hex_to_rgba(color, 0.82)
                    for data_index in range(len(values))
                ]
                bars = ax.barh(
                    [pos + offset for pos in x_pos[: len(values)]],
                    values,
                    height=height,
                    label=str(item.get("name") or f"系列{index + 1}"),
                    color=bar_colors,
                )
                for data_index, (bar, value) in enumerate(zip(bars, values)):
                    ax.text(
                        bar.get_width(),
                        bar.get_y() + bar.get_height() / 2,
                        _format_chart_number(value),
                        va="center",
                        ha="left",
                        fontsize=9,
                        fontweight="bold" if data_index == peak else "normal",
                        color=primary if data_index == peak else "#595959",
                    )
                _add_average_line(ax, values, horizontal=True)
            if categories:
                ax.set_yticks(x_pos)
                ax.set_yticklabels(categories, color="#8c8c8c")
        else:
            width = 0.56 / max(1, len(series))
            for index, item in enumerate(series):
                if not isinstance(item, dict):
                    continue
                values = [float(value) for value in (item.get("values") or [])]
                offset = (index - (len(series) - 1) / 2) * width
                color = palette[index] if index < len(palette) else _ANTD_PRIMARY
                peak = _peak_index(values)
                bar_colors = [
                    color if data_index == peak else _hex_to_rgba(color, 0.82)
                    for data_index in range(len(values))
                ]
                bars = ax.bar(
                    [pos + offset for pos in x_pos[: len(values)]],
                    values,
                    width=width,
                    label=str(item.get("name") or f"系列{index + 1}"),
                    color=bar_colors,
                    zorder=3,
                )
                for data_index, (bar, value) in enumerate(zip(bars, values)):
                    ax.text(
                        bar.get_x() + bar.get_width() / 2,
                        bar.get_height(),
                        _format_chart_number(value),
                        va="bottom",
                        ha="center",
                        fontsize=9,
                        fontweight="bold" if data_index == peak else "normal",
                        color=primary if data_index == peak else "#595959",
                    )
                _add_average_line(ax, values)
            if categories:
                ax.set_xticks(x_pos)
                ax.set_xticklabels(categories, rotation=rotate, ha="right" if rotate else "center", color="#8c8c8c")
        if chart_type in {"line", "area"} and categories:
            ax.set_xticks(x_pos)
            ax.set_xticklabels(categories, rotation=rotate, ha="right" if rotate else "center", color="#8c8c8c")
        if len(series) > 1:
            ax.legend(frameon=False, fontsize=9, labelcolor="#595959")
        _style_report_axes(ax, horizontal=chart_type == "hbar")

    fig.tight_layout()
    buffer = BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor="#ffffff")
    plt.close(fig)
    return buffer.getvalue()


def _set_run_font(run, *, size_pt: float = 11, bold: bool = False) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.bold = bold
    run.font.size = Pt(size_pt)
    run.font.name = "Microsoft YaHei"
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def write_report_docx(payload: dict[str, Any], destination: Path) -> None:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Inches, Pt
    from docx.oxml.ns import qn

    document = Document()
    section = document.sections[0]
    section.top_margin = Inches(0.9)
    section.bottom_margin = Inches(0.9)
    section.left_margin = Inches(1.0)
    section.right_margin = Inches(1.0)
    style = document.styles["Normal"]
    style.font.name = "Microsoft YaHei"
    style.font.size = Pt(11)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "Microsoft YaHei")

    title = str(payload.get("title") or "数据分析报告").strip()
    heading = document.add_paragraph()
    heading.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = heading.add_run(title)
    _set_run_font(run, size_pt=18, bold=True)

    part_one = document.add_paragraph()
    run = part_one.add_run("一、总体情况")
    _set_run_font(run, size_pt=14, bold=True)
    for block in _paragraphs(str(payload.get("overview") or "")):
        para = document.add_paragraph()
        run = para.add_run(block)
        _set_run_font(run)

    part_two = document.add_paragraph()
    run = part_two.add_run("二、分类分析")
    _set_run_font(run, size_pt=14, bold=True)
    analyses = payload.get("analyses")
    if isinstance(analyses, list):
        for index, item in enumerate(analyses, start=1):
            if not isinstance(item, dict):
                continue
            heading_text = str(item.get("heading") or f"分析维度 {index}").strip()
            sub = document.add_paragraph()
            run = sub.add_run(f"{index}、{heading_text}")
            _set_run_font(run, size_pt=12, bold=True)
            for block in _paragraphs(str(item.get("narrative") or "")):
                para = document.add_paragraph()
                run = para.add_run(block)
                _set_run_font(run)
            chart = item.get("chart")
            if isinstance(chart, dict) and chart.get("categories"):
                image_bytes = render_chart_png(chart)
                image_para = document.add_paragraph()
                image_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = image_para.add_run()
                run.add_picture(BytesIO(image_bytes), width=Inches(6.1))
                caption = str(chart.get("title") or heading_text).strip()
                cap = document.add_paragraph()
                cap.alignment = WD_ALIGN_PARAGRAPH.CENTER
                run = cap.add_run(caption)
                _set_run_font(run, size_pt=9)
            elif str(item.get("chart_error") or "").strip():
                para = document.add_paragraph()
                run = para.add_run(str(item.get("chart_error")).strip())
                _set_run_font(run, size_pt=9)

    part_three = document.add_paragraph()
    run = part_three.add_run("三、分析结论")
    _set_run_font(run, size_pt=14, bold=True)
    for block in _paragraphs(_conclusions_text(payload.get("conclusions"))):
        para = document.add_paragraph()
        run = para.add_run(block)
        _set_run_font(run)

    destination.parent.mkdir(parents=True, exist_ok=True)
    document.save(str(destination))


def _materialize_one_chart(
        chart_plan: dict[str, Any] | None,
        snapshot: dict[str, Any] | None,
        *,
        heading: str = "",
        user_request: str = "",
        palette: object = None,
) -> tuple[dict[str, Any] | None, str]:
    """按单个分析维度绑图；失败时按标题和结果列补一张。"""
    planned = coerce_chart_plan(chart_plan, title=heading) if chart_plan else None
    if planned and heading and not str(planned.get("title") or "").strip():
        planned = {**planned, "title": heading}

    def _try(plan: dict[str, Any] | None) -> tuple[dict[str, Any] | None, str]:
        if not plan:
            return None, ""
        spec = materialize_chart_spec(
            json.dumps(
                {"feasible": True, "palette": palette, "charts": [plan]},
                ensure_ascii=False,
            ),
            snapshot,
            user_request=user_request,
        )
        charts = spec.get("charts") if isinstance(spec.get("charts"), list) else []
        chart = charts[0] if charts and isinstance(charts[0], dict) else None
        if chart and chart.get("categories"):
            return chart, ""
        return None, str(spec.get("summary") or "").strip()

    chart, error = _try(planned)
    if chart:
        return chart, ""
    fallback = infer_fallback_chart_plan(heading, snapshot)
    if fallback:
        chart, fallback_error = _try(fallback)
        if chart:
            return chart, ""
        error = error or fallback_error
    return None, error or "当前结果无法按该维度绘图。"


def materialize_analysis_report(
        raw_text: str,
        snapshot: dict[str, Any] | None,
        *,
        user_request: str = "",
        last_chart: dict[str, Any] | None = None,
        write_file: bool = True,
) -> dict[str, Any]:
    source_row_count = 0
    source_truncated = False
    if snapshot:
        source_row_count = int(snapshot.get("row_count") or 0)
        source_truncated = bool(snapshot.get("truncated")) or any(
            isinstance(item, dict) and item.get("truncated") is True
            for item in (snapshot.get("result_sets") or [])
        )
    parsed_objects = parse_json_objects(raw_text)
    plan = _pick_report_plan(parsed_objects)
    if plan.get("feasible") is False:
        return _empty_report(
            str(plan.get("summary") or "当前结果不足以生成分析报告。").strip(),
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )
    if not _report_has_body(plan):
        plan = synthesize_report_plan(
            snapshot,
            user_request=user_request,
            last_chart=last_chart,
        ) or {}
    if not _report_has_body(plan):
        return _empty_report(
            "当前结果缺少可分析的分类维度，请补充希望按哪些字段汇总。",
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    title = str(plan.get("title") or "").strip() or str(
        (snapshot or {}).get("user_question") or "数据分析报告"
    ).strip()
    overview = str(plan.get("overview") or "").strip()
    conclusions = _conclusions_text(plan.get("conclusions"))
    raw_analyses = plan.get("analyses")
    if not isinstance(raw_analyses, list):
        raw_analyses = []

    analyses: list[dict[str, Any]] = []
    for item in raw_analyses[:MAX_REPORT_ANALYSES]:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or item.get("dimension") or "").strip() or "分析维度"
        narrative = str(item.get("narrative") or item.get("analysis") or "").strip()
        chart_plan = item.get("chart") if isinstance(item.get("chart"), dict) else None
        if _is_materialized_chart(chart_plan):
            chart, chart_error = chart_plan, ""
        else:
            chart, chart_error = _materialize_one_chart(
                chart_plan,
                snapshot,
                heading=heading,
                user_request=user_request,
                palette=plan.get("palette"),
            )
        analyses.append({
            "heading": heading,
            "narrative": narrative,
            "chart": chart,
            "chart_error": "" if chart else chart_error,
        })

    if not overview and not conclusions and not analyses:
        return _empty_report(
            "报告正文为空，请再试一次。",
            source_row_count=source_row_count,
            source_truncated=source_truncated,
        )

    payload: dict[str, Any] = {
        "kind": "analysis_report",
        "feasible": True,
        "summary": str(plan.get("summary") or "已根据当前查询结果生成分析报告。").strip(),
        "title": title,
        "overview": overview,
        "conclusions": conclusions,
        "analyses": analyses,
        "file_id": "",
        "filename": "",
        "source_row_count": source_row_count,
        "source_truncated": source_truncated,
    }
    if write_file:
        file_id = uuid4().hex
        filename = _sanitize_filename(title)
        path = reports_dir() / f"{file_id}.docx"
        write_report_docx(payload, path)
        payload["file_id"] = file_id
        payload["filename"] = filename
    return payload


def resolve_report_file(file_id: str) -> Path | None:
    token = re.sub(r"[^a-fA-F0-9]", "", file_id or "")
    if len(token) != 32:
        return None
    path = reports_dir() / f"{token}.docx"
    if not path.is_file():
        return None
    return path
