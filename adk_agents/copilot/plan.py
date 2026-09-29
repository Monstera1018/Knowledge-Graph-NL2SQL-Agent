"""规划节点：每次只决定下一步，管道结束后根据观察再规划。"""

from __future__ import annotations

import json
from typing import Any

from adk_agents.common import RetrievalExtractionResult
from adk_agents.copilot.chart import parse_json_objects
from adk_agents.copilot.helpers import (
    EXTRACTOR_TASK_CHART,
    EXTRACTOR_TASK_CHITCHAT,
    EXTRACTOR_TASK_QUERY,
    EXTRACTOR_TASK_REPORT,
    SNAPSHOT_GRAIN_AGGREGATED,
    SNAPSHOT_GRAIN_NONE,
    infer_snapshot_grain,
    normalize_extractor_task,
    resolve_extractor_task,
    snapshot_is_truncated,
    user_requests_chart,
    user_requests_new_query,
    user_requests_report,
)

PLAN_STEP_QUERY = "query"
PLAN_STEP_CHART = "chart"
PLAN_STEP_REPORT = "report"
PLAN_STEP_CHITCHAT = "chitchat"
PLAN_STEP_STOP = "stop"

MAX_CHART_PLAN_ATTEMPTS = 2
MAX_REPORT_PLAN_ATTEMPTS = 2
MAX_QUERY_STEPS_BEFORE_ANALYSIS = 2

_STEP_MAPPING = {
    "query": PLAN_STEP_QUERY,
    "ask": PLAN_STEP_QUERY,
    "sql": PLAN_STEP_QUERY,
    "问数": PLAN_STEP_QUERY,
    "查询": PLAN_STEP_QUERY,
    "chart": PLAN_STEP_CHART,
    "visualize": PLAN_STEP_CHART,
    "图表": PLAN_STEP_CHART,
    "可视化": PLAN_STEP_CHART,
    "report": PLAN_STEP_REPORT,
    "分析报告": PLAN_STEP_REPORT,
    "报告": PLAN_STEP_REPORT,
    "chitchat": PLAN_STEP_CHITCHAT,
    "chat": PLAN_STEP_CHITCHAT,
    "闲聊": PLAN_STEP_CHITCHAT,
    "stop": PLAN_STEP_STOP,
    "end": PLAN_STEP_STOP,
    "done": PLAN_STEP_STOP,
    "结束": PLAN_STEP_STOP,
    "停止": PLAN_STEP_STOP,
}

OBS_QUERY_SUCCESS = "query_success"
OBS_QUERY_FAILED = "query_failed"
OBS_NO_TABLES = "no_tables"
OBS_VALIDATION_FAILED = "validation_failed"
OBS_NO_SNAPSHOT = "no_snapshot"
OBS_CHART_DONE = "chart_done"
OBS_CHART_INFEASIBLE = "chart_infeasible"
OBS_REPORT_DONE = "report_done"
OBS_REPORT_INFEASIBLE = "report_infeasible"


def _normalize_step(raw: object) -> str:
    return _STEP_MAPPING.get(str(raw or "").strip().lower(), "")


def _unique_steps(raw_steps: object) -> list[str]:
    if not isinstance(raw_steps, list):
        return []
    seen: set[str] = set()
    steps: list[str] = []
    for item in raw_steps:
        key = _normalize_step(item)
        if not key or key in seen:
            continue
        seen.add(key)
        steps.append(key)
    return steps


def _count_step(plan: dict[str, Any] | None, step: str) -> int:
    if not isinstance(plan, dict):
        return 0
    raw_decisions = plan.get("decisions")
    if not isinstance(raw_decisions, list):
        return 0
    count = 0
    for item in raw_decisions:
        if isinstance(item, dict) and _normalize_step(item.get("next")) == step:
            count += 1
    return count


def plan_requests_chart(
        user_message: str,
        extraction: RetrievalExtractionResult,
        previous_plan: dict[str, Any] | None = None,
) -> bool:
    """用户这轮是否要求出图：看原话、解析结果和上一轮规划留下的出图要求。"""
    if user_requests_chart(user_message):
        return True
    if user_requests_chart(extraction.retrieval_query or ""):
        return True
    if normalize_extractor_task(extraction.task) == EXTRACTOR_TASK_CHART:
        return True
    for item in extraction.keywords or []:
        if user_requests_chart(str(item)) or str(item).strip() in {"图表", "出图", "可视化"}:
            return True
    if isinstance(previous_plan, dict) and str(previous_plan.get("chart_request") or "").strip():
        return True
    return False


def plan_requests_report(
        user_message: str,
        extraction: RetrievalExtractionResult,
) -> bool:
    if user_requests_report(user_message):
        return True
    if user_requests_report(extraction.retrieval_query or ""):
        return True
    return normalize_extractor_task(extraction.task) == EXTRACTOR_TASK_REPORT


def snapshot_missing_chart_columns(observation: dict[str, Any] | None) -> bool:
    """出图失败是否因为规划列不在当前结果里，需要补一次带维度的查询。"""
    detail = str((observation or {}).get("detail") or "")
    return any(
        token in detail
        for token in (
            "不在当前结果中",
            "缺少合适的分析维度",
            "没有可绑定的图表定义",
            "不存在",
            "对应列",
            "无法按指定维度",
        )
    )


def can_rewrite_snapshot_grain(
        *,
        user_wants_chart: bool,
        user_wants_report: bool,
        observation: dict[str, Any] | None,
        previous_plan: dict[str, Any] | None,
) -> bool:
    """出图/报告失败后，最多再查一次补维度。合计快照缺分类列时也要重查。"""
    if not (user_wants_chart or user_wants_report):
        return False
    code = str((observation or {}).get("code") or "").strip()
    if code not in {OBS_CHART_INFEASIBLE, OBS_REPORT_INFEASIBLE}:
        return False
    grain = str((observation or {}).get("snapshot_grain") or "").strip()
    if grain == SNAPSHOT_GRAIN_NONE:
        return False
    return _count_step(previous_plan, PLAN_STEP_QUERY) < MAX_QUERY_STEPS_BEFORE_ANALYSIS


def _parse_next(next_step: object, steps: object) -> str:
    parsed = _normalize_step(next_step)
    if parsed:
        return parsed
    unique = _unique_steps(steps)
    return unique[0] if unique else ""


def _fallback_next(
        extraction: RetrievalExtractionResult,
        user_message: str,
        *,
        has_snapshot: bool,
        observation: dict[str, Any] | None,
) -> str:
    user_wants_chart = plan_requests_chart(user_message, extraction)
    user_wants_report = plan_requests_report(user_message, extraction)
    wants_query = user_requests_new_query(user_message) or (
        resolve_extractor_task(extraction, user_message) == EXTRACTOR_TASK_QUERY
    )
    code = str((observation or {}).get("code") or "").strip()
    if code == OBS_NO_SNAPSHOT:
        return PLAN_STEP_QUERY
    if code == OBS_QUERY_SUCCESS:
        if user_wants_report:
            return PLAN_STEP_REPORT
        return PLAN_STEP_CHART if user_wants_chart else PLAN_STEP_STOP
    if code == OBS_CHART_INFEASIBLE:
        last_next = str((observation or {}).get("last_next") or "").strip()
        if last_next == PLAN_STEP_CHART:
            return PLAN_STEP_QUERY
        return PLAN_STEP_CHART if has_snapshot else PLAN_STEP_QUERY
    if code == OBS_REPORT_INFEASIBLE:
        last_next = str((observation or {}).get("last_next") or "").strip()
        if last_next == PLAN_STEP_REPORT:
            return PLAN_STEP_QUERY
        return PLAN_STEP_REPORT if has_snapshot else PLAN_STEP_QUERY
    if code == OBS_CHART_DONE:
        return PLAN_STEP_REPORT if user_wants_report else PLAN_STEP_STOP
    if code in {
        OBS_NO_TABLES,
        OBS_VALIDATION_FAILED,
        OBS_QUERY_FAILED,
        OBS_REPORT_DONE,
    }:
        return PLAN_STEP_STOP

    task = resolve_extractor_task(extraction, user_message)
    if task == EXTRACTOR_TASK_CHITCHAT and not user_wants_chart and not user_wants_report and not wants_query:
        return PLAN_STEP_CHITCHAT
    if user_wants_report and has_snapshot and not wants_query:
        return PLAN_STEP_REPORT
    if user_wants_chart and has_snapshot and not wants_query:
        return PLAN_STEP_CHART
    if (user_wants_report or user_wants_chart) and not has_snapshot:
        return PLAN_STEP_QUERY
    if wants_query or task == EXTRACTOR_TASK_QUERY:
        return PLAN_STEP_QUERY
    if user_wants_report:
        return PLAN_STEP_REPORT
    if user_wants_chart:
        return PLAN_STEP_CHART
    return PLAN_STEP_QUERY


def normalize_plan(
        *,
        steps: object = None,
        next_step: object = None,
        reason: str = "",
        chart_request: str = "",
        extraction: RetrievalExtractionResult,
        user_message: str,
        has_snapshot: bool,
        observation: dict[str, Any] | None = None,
        previous_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """只拦空转和未完成就停；具体走问数、出图还是出报告由模型根据观察判断。"""
    task = normalize_extractor_task(extraction.task)
    user_wants_chart = plan_requests_chart(user_message, extraction, previous_plan)
    user_wants_report = plan_requests_report(user_message, extraction)
    parsed_next = _parse_next(next_step, steps)
    code = str((observation or {}).get("code") or "").strip()
    last_next = str((observation or {}).get("last_next") or "").strip()
    chart_attempts = _count_step(previous_plan, PLAN_STEP_CHART)
    report_attempts = _count_step(previous_plan, PLAN_STEP_REPORT)
    unfulfilled = code in {OBS_CHART_INFEASIBLE, OBS_NO_SNAPSHOT, OBS_REPORT_INFEASIBLE} or (
        code == OBS_QUERY_SUCCESS and (user_wants_chart or user_wants_report)
    ) or (
        code == OBS_CHART_DONE and user_wants_report
    )

    # 没有快照、也没有管道观察时，清单题不要被模型顺手加上 chart/report。
    if (
        parsed_next in {PLAN_STEP_CHART, PLAN_STEP_REPORT}
        and not user_wants_chart
        and not user_wants_report
        and not has_snapshot
        and not observation
    ):
        parsed_next = ""

    if not parsed_next or (not observation and parsed_next == PLAN_STEP_STOP):
        parsed_next = _fallback_next(
            extraction,
            user_message,
            has_snapshot=has_snapshot,
            observation=observation,
        )

    if parsed_next in {PLAN_STEP_CHART, PLAN_STEP_REPORT} and code == OBS_NO_SNAPSHOT:
        parsed_next = PLAN_STEP_QUERY
    allow_grain_rewrite = can_rewrite_snapshot_grain(
        user_wants_chart=user_wants_chart,
        user_wants_report=user_wants_report,
        observation=observation,
        previous_plan=previous_plan,
    )
    if parsed_next == PLAN_STEP_QUERY and code == OBS_QUERY_SUCCESS and not allow_grain_rewrite:
        if user_wants_report:
            parsed_next = PLAN_STEP_REPORT
        elif user_wants_chart:
            parsed_next = PLAN_STEP_CHART
        else:
            parsed_next = PLAN_STEP_STOP
    if (
        parsed_next == PLAN_STEP_QUERY
        and code in {OBS_CHART_INFEASIBLE, OBS_REPORT_INFEASIBLE}
        and not allow_grain_rewrite
    ):
        if has_snapshot and user_wants_report:
            parsed_next = PLAN_STEP_REPORT
        elif has_snapshot and user_wants_chart:
            parsed_next = PLAN_STEP_CHART
        elif has_snapshot:
            parsed_next = PLAN_STEP_STOP
    if (
        code in {OBS_CHART_INFEASIBLE, OBS_REPORT_INFEASIBLE}
        and allow_grain_rewrite
        and (
            snapshot_missing_chart_columns(observation)
            or parsed_next in {PLAN_STEP_STOP, PLAN_STEP_CHITCHAT, PLAN_STEP_QUERY}
        )
    ):
        parsed_next = PLAN_STEP_QUERY
    if code in {OBS_NO_TABLES, OBS_VALIDATION_FAILED, OBS_QUERY_FAILED}:
        parsed_next = PLAN_STEP_STOP
    if code in {OBS_CHART_DONE, OBS_REPORT_DONE} and parsed_next == PLAN_STEP_CHITCHAT:
        parsed_next = PLAN_STEP_STOP
    if (
        parsed_next == last_next
        and code in {
            OBS_NO_TABLES,
            OBS_VALIDATION_FAILED,
            OBS_QUERY_FAILED,
            OBS_NO_SNAPSHOT,
        }
    ):
        if parsed_next in {PLAN_STEP_CHART, PLAN_STEP_REPORT} and code == OBS_NO_SNAPSHOT:
            parsed_next = PLAN_STEP_QUERY
        else:
            parsed_next = PLAN_STEP_STOP
    if (
        code == OBS_CHART_INFEASIBLE
        and parsed_next == PLAN_STEP_CHART
        and chart_attempts >= MAX_CHART_PLAN_ATTEMPTS
    ):
        parsed_next = PLAN_STEP_QUERY
    if (
        code == OBS_REPORT_INFEASIBLE
        and parsed_next == PLAN_STEP_REPORT
        and report_attempts >= MAX_REPORT_PLAN_ATTEMPTS
    ):
        parsed_next = PLAN_STEP_QUERY
    if parsed_next in {PLAN_STEP_STOP, PLAN_STEP_CHITCHAT} and unfulfilled:
        parsed_next = _fallback_next(
            extraction,
            user_message,
            has_snapshot=has_snapshot,
            observation=observation,
        )
    if parsed_next == PLAN_STEP_CHITCHAT and last_next in {
        PLAN_STEP_QUERY,
        PLAN_STEP_CHART,
        PLAN_STEP_REPORT,
    }:
        parsed_next = PLAN_STEP_STOP if not unfulfilled else PLAN_STEP_QUERY

    if parsed_next not in {
        PLAN_STEP_QUERY,
        PLAN_STEP_CHART,
        PLAN_STEP_REPORT,
        PLAN_STEP_CHITCHAT,
        PLAN_STEP_STOP,
    }:
        parsed_next = PLAN_STEP_QUERY
    if (
        user_wants_report
        and has_snapshot
        and not user_requests_new_query(user_message)
        and parsed_next == PLAN_STEP_QUERY
        and code not in {
            OBS_NO_TABLES,
            OBS_VALIDATION_FAILED,
            OBS_QUERY_FAILED,
            OBS_NO_SNAPSHOT,
        }
    ):
        parsed_next = PLAN_STEP_REPORT
    if code == OBS_QUERY_SUCCESS and parsed_next in {PLAN_STEP_STOP, PLAN_STEP_CHITCHAT}:
        if user_wants_report:
            parsed_next = PLAN_STEP_REPORT
        elif user_wants_chart:
            parsed_next = PLAN_STEP_CHART
    if (
        code == OBS_QUERY_SUCCESS
        and parsed_next == PLAN_STEP_QUERY
        and user_wants_chart
        and not user_wants_report
        and not allow_grain_rewrite
    ):
        parsed_next = PLAN_STEP_CHART

    previous_request = ""
    previous_decisions: list[dict[str, str]] = []
    if isinstance(previous_plan, dict):
        previous_request = str(previous_plan.get("chart_request") or "").strip()
        raw_decisions = previous_plan.get("decisions")
        if isinstance(raw_decisions, list):
            for item in raw_decisions:
                if not isinstance(item, dict):
                    continue
                action = _normalize_step(item.get("next"))
                if not action:
                    continue
                previous_decisions.append({
                    "next": action,
                    "reason": str(item.get("reason") or "").strip(),
                })

    request = (chart_request or "").strip() or previous_request
    if not request and user_requests_chart(user_message):
        request = user_message.strip()
    if parsed_next != PLAN_STEP_CHART and parsed_next != PLAN_STEP_QUERY:
        # 闲聊/结束不必清掉出图要求，后续若再规划出图仍可沿用。
        pass

    reason_text = (reason or "").strip() or "已根据当前观察选择下一步。"
    decisions = [
        *previous_decisions,
        {"next": parsed_next, "reason": reason_text},
    ]
    return {
        "next": parsed_next,
        "steps": [item["next"] for item in decisions],
        "reason": reason_text,
        "chart_request": request if user_wants_chart or parsed_next == PLAN_STEP_CHART else "",
        "decisions": decisions,
    }


def plan_from_model_text(
        text: str,
        *,
        extraction: RetrievalExtractionResult,
        user_message: str,
        has_snapshot: bool,
        observation: dict[str, Any] | None = None,
        previous_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    objects = parse_json_objects(text)
    payload = objects[-1] if objects else {}
    return normalize_plan(
        steps=payload.get("steps"),
        next_step=payload.get("next") or payload.get("action") or payload.get("step"),
        reason=str(payload.get("reason") or ""),
        chart_request=str(payload.get("chart_request") or ""),
        extraction=extraction,
        user_message=user_message,
        has_snapshot=has_snapshot,
        observation=observation,
        previous_plan=previous_plan,
    )


def plan_next(plan: dict[str, Any] | None) -> str:
    if not isinstance(plan, dict):
        return ""
    parsed = _normalize_step(plan.get("next"))
    if parsed:
        return parsed
    steps = plan.get("steps")
    if isinstance(steps, list) and steps:
        return _normalize_step(steps[-1])
    return ""


def plan_includes_chart(plan: dict[str, Any] | None) -> bool:
    return plan_next(plan) == PLAN_STEP_CHART


def plan_includes_report(plan: dict[str, Any] | None) -> bool:
    return plan_next(plan) == PLAN_STEP_REPORT


def plan_is_chitchat(plan: dict[str, Any] | None) -> bool:
    return plan_next(plan) == PLAN_STEP_CHITCHAT


def plan_is_stop(plan: dict[str, Any] | None) -> bool:
    return plan_next(plan) == PLAN_STEP_STOP


def format_planner_user_message(
        *,
        user_message: str,
        extraction: RetrievalExtractionResult,
        has_snapshot: bool,
        snapshot: dict[str, Any] | None,
        last_chart: dict[str, Any] | None,
        observation: dict[str, Any] | None = None,
        previous_plan: dict[str, Any] | None = None,
        remaining_turns: int = 0,
) -> str:
    snapshot_lines = ["无。若用户要出图或分析报告，下一步应先 query。"]
    if has_snapshot and snapshot:
        question = str(snapshot.get("user_question") or "").strip() or "（无）"
        tables = snapshot.get("selected_tables")
        table_text = "、".join(
            str(name).strip() for name in tables if str(name).strip()
        ) if isinstance(tables, list) else "（无）"
        columns: list[str] = []
        result_sets = snapshot.get("result_sets")
        if isinstance(result_sets, list) and result_sets and isinstance(result_sets[0], dict):
            raw_columns = result_sets[0].get("columns")
            if isinstance(raw_columns, list):
                columns = [str(col).strip() for col in raw_columns if str(col).strip()]
        grain = str(snapshot.get("grain") or infer_snapshot_grain(snapshot) or "").strip()
        grain_text = "已按维度汇总" if grain == SNAPSHOT_GRAIN_AGGREGATED else (
            "无可用行" if grain == SNAPSHOT_GRAIN_NONE else "明细清单"
        )
        truncated_text = "是" if snapshot_is_truncated(snapshot) else "否"
        snapshot_lines = [
            "有查询快照。清单/统计题有结果后应 stop。要图或报告时：列够就 chart/report；缺维度或只有合计对不上图时，再 query 一次写成带分类列的 GROUP BY。",
            f"- 上次问题：{question}",
            f"- 已选表：{table_text or '（无）'}",
            f"- 结果列：{'、'.join(columns) if columns else '（无）'}",
            f"- 行数：{snapshot.get('row_count') or 0}",
            f"- 粒度：{grain_text}",
            f"- 结果是否截断：{truncated_text}",
        ]

    chart_lines = ["无"]
    if last_chart:
        if last_chart.get("feasible") and isinstance(last_chart.get("charts"), list):
            chart_lines = []
            for index, chart in enumerate(last_chart.get("charts") or [], start=1):
                if not isinstance(chart, dict):
                    continue
                title = str(chart.get("title") or f"图表{index}").strip()
                chart_type = str(chart.get("type") or "").strip() or "未知"
                palette = chart.get("palette")
                palette_text = ""
                if isinstance(palette, list) and palette:
                    palette_text = f"，配色 {', '.join(str(item) for item in palette[:5])}"
                chart_lines.append(f"- {title}（{chart_type}{palette_text}）")
            if not chart_lines:
                chart_lines = ["无"]
        else:
            reason = str(last_chart.get("summary") or last_chart.get("reason") or "").strip()
            chart_lines = [
                "上次出图未成功。若失败是因为缺列或粒度不对，应 query；若只是列名/图表类型写错，可再 chart。",
            ]
            if reason:
                chart_lines.append(f"- 失败原因：{reason}")

    observation_lines = ["无。这是本轮第一次规划，只决定下一步。"]
    if observation:
        observation_lines = [
            f"- 上一管道：{observation.get('last_next') or '（空）'}",
            f"- 结果：{observation.get('status') or '（空）'}",
            f"- 代码：{observation.get('code') or '（空）'}",
            f"- 说明：{observation.get('detail') or '（无）'}",
            f"- 当前有快照：{'是' if observation.get('has_snapshot') else '否'}",
        ]
        grain = str(observation.get("snapshot_grain") or "").strip()
        if grain:
            grain_text = "已按维度汇总" if grain == SNAPSHOT_GRAIN_AGGREGATED else (
                "无可用行" if grain == SNAPSHOT_GRAIN_NONE else "明细清单"
            )
            observation_lines.append(f"- 快照粒度：{grain_text}")
        if observation.get("snapshot_truncated") is True:
            observation_lines.append("- 快照已截断：是，图和报告中的数字仅基于返回行")
        columns = observation.get("snapshot_columns")
        if isinstance(columns, list) and columns:
            observation_lines.append(
                "- 快照列：" + "、".join(str(item) for item in columns if str(item).strip())
            )

    decision_lines = ["无"]
    if isinstance(previous_plan, dict):
        raw_decisions = previous_plan.get("decisions")
        if isinstance(raw_decisions, list) and raw_decisions:
            decision_lines = []
            for index, item in enumerate(raw_decisions, start=1):
                if not isinstance(item, dict):
                    continue
                action = str(item.get("next") or "").strip() or "（空）"
                item_reason = str(item.get("reason") or "").strip()
                suffix = f"：{item_reason}" if item_reason else ""
                decision_lines.append(f"{index}. {action}{suffix}")
            if not decision_lines:
                decision_lines = ["无"]

    keywords = "、".join(extraction.keywords) if extraction.keywords else "（无）"
    return "\n".join([
        "## 用户最新输入",
        "",
        (user_message or "").strip() or "（无）",
        "",
        "## 问题解析结果",
        "",
        f"- task：{extraction.task or '（空）'}",
        f"- intent：{extraction.intent or '（空）'}",
        f"- retrieval_query：{extraction.retrieval_query or '（空）'}",
        f"- keywords：{keywords}",
        "",
        "## 当前是否已有查询结果",
        "",
        *snapshot_lines,
        "",
        "## 上一张图表",
        "",
        *chart_lines,
        "",
        "## 上一管道观察",
        "",
        *observation_lines,
        "",
        "## 本轮已决策",
        "",
        *decision_lines,
        "",
        f"## 剩余规划次数：{remaining_turns}",
        "",
        "请只输出 JSON，且只决定**下一步**一个动作。",
    ])


def plan_dump(plan: dict[str, Any]) -> str:
    return json.dumps(plan, ensure_ascii=False)
