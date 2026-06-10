import os
from typing import Sequence, List

from tools.llm import chat

ColumnInfo = tuple[str, str, str]

_TABLE_DESCRIPTION_SYSTEM = """\
你是数据库元数据分析助手，擅长把表结构整理成面向分析问答的中文语义摘要，供语义检索与数据分析助手使用。
"""


def _format_columns_for_prompt(columns: Sequence[ColumnInfo]) -> str:
    lines: list[str] = []
    for name, dtype, comment in columns:
        if comment:
            line = f"- {comment} ({dtype})"
        else:
            line = f"- {name} ({dtype})"
        lines.append(line)
    return "\n".join(lines)


def _build_table_description_prompt(
        table_name: str,
        table_comment: str,
        columns: List[ColumnInfo],
) -> str:
    parts = [
        "请根据下列数据表的字段信息，用中文写一段「表级语义摘要」（150～350 字）。",
        "",
        "要求：",
        "1. 说明该表大致包含哪些业务数据、常见用途、适合回答哪类分析问题。",
        "2. 可概括重要维度/指标/状态类字段的含义，但不要逐字段罗列。",
        "3. 只依据提供的表数据库名、原始表说明和字段清单信息推断，禁止编造未出现的业务含义。",
        "   仅在关键业务含义无法从字段描述推断时使用「待确认」，不要对每个字段都标注。",
        "4. 不要使用英文表名、字段名；不要写 SQL。",
        "5. 用一段连贯中文正文输出，不要标题、编号、markdown。",
        "6. 不要写「综上所述」「根据上述字段」等套话，直接输出摘要正文。",
        "",
        "## 表数据库名（仅供理解，输出中不要出现）",
        "",
        table_name,
    ]

    if table_comment:
        parts.extend([
            "",
            "## 原始表说明",
            "",
            table_comment,
        ])

    parts.extend([
        "",
        "## 字段清单",
        "",
        _format_columns_for_prompt(columns),
    ])
    return "\n".join(parts)


def generate_table_description_enabled() -> bool:
    raw = os.environ.get("LLM_GENERATE_TABLE_DESCRIPTION", "false").strip().lower()
    return raw in ("1", "true", "yes", "on")


async def generate_table_description(
        table_name: str,
        table_comment: str,
        columns: List[ColumnInfo],
) -> str:
    if not generate_table_description_enabled():
        return ""

    if not columns:
        return ""

    user_prompt = _build_table_description_prompt(table_name, table_comment, columns)
    return await chat(user_prompt, system_prompt=_TABLE_DESCRIPTION_SYSTEM)
