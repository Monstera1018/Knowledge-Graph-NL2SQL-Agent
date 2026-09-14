import sys
from pathlib import Path
from typing import List, Any, Dict

from pydantic import BaseModel, Field

from google.adk.models import LiteLlm

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT.joinpath(".env"))

from tools.common import to_litellm_model_name, require_non_empty_env


class WebSafeLiteLlm(LiteLlm):
    """
    Dev-Server-safe wrapper around LiteLlm.
    Overrides serialization to prevent `adk web` from crashing on llm_client.
    """

    def __init__(self, model: str, **kwargs: Any) -> None:
        super().__init__(model, **kwargs)

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "model": self.model,
            "type": "LiteLlm (Web Safe Workaround)"
        }


def get_llm_model():
    return WebSafeLiteLlm(
        model=to_litellm_model_name(require_non_empty_env("LLM_MODEL_NAME")),
        temperature=0.0,
        enable_thinking=False,
        extra_headers={"X-AI-AccessToken": require_non_empty_env("OPENAI_API_KEY")},
        allowed_openai_params=["enable_thinking"],
    )


class RetrievalExtractionResult(BaseModel):
    retrieval_query: str = Field(
        description="用于语义检索的中文查询文本，50～200 字，自洽完整",
    )
    keywords: List[str] = Field(
        default_factory=list,
        description="3～8 个短词，覆盖核心业务概念、指标、维度、时间等",
    )
    intent: str = Field(
        description="一句话概括用户分析意图",
    )
    task: str = Field(
        default="",
        description="规划提示：query 问数、chart 出图、report 分析报告、chitchat 闲聊；最终路由由 planner 决定",
    )


class SearchStageOutput(BaseModel):
    user_message: str = Field(description="用户原始问题")
    retrieval_context: str = Field(description="语义检索上下文 Markdown")
    table_candidates: List[str] = Field(
        default_factory=list,
        description="从检索上下文中确定性提取出的候选表名，按出现顺序排列",
    )


class TableSelectionResult(BaseModel):
    tables: List[str] = Field(
        default_factory=list,
        description="回答用户取数问题需要使用的数据表名，按重要性从高到低",
    )
    reason: str = Field(description="选表理由")
