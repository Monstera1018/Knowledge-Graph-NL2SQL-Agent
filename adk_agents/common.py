import sys
from pathlib import Path
from typing import List, Any, Dict, Optional

from pydantic import BaseModel, Field, PrivateAttr

from google.adk.models import LiteLlm
from google.adk.models.llm_request import LlmRequest

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

load_dotenv(_PROJECT_ROOT.joinpath(".env"))

from tools.llm import generate_with_failover, litellm_agent_kwargs, load_llm_profiles


class WebSafeLiteLlm(LiteLlm):
    """避免 adk web 序列化 llm_client 崩溃；并带上套餐名供日志使用。"""

    _profile_name: str = PrivateAttr(default="primary")

    def __init__(self, model: str, **kwargs: Any) -> None:
        profile_name = kwargs.pop("profile_name", "primary")
        super().__init__(model, **kwargs)
        self._profile_name = profile_name

    @property
    def profile_name(self) -> str:
        return self._profile_name

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        return {
            "model": self.model,
            "type": "LiteLlm (Web Safe Workaround)",
        }


class FailoverLiteLlm(WebSafeLiteLlm):
    """主套餐限流或额度用尽后，改走备用套餐（通常为火山方舟 Agent Plan）。"""

    _fallback_llm: Optional[WebSafeLiteLlm] = PrivateAttr(default=None)

    def __init__(self, model: str, **kwargs: Any) -> None:
        fallback_llm = kwargs.pop("fallback_llm", None)
        super().__init__(model, **kwargs)
        self._fallback_llm = fallback_llm

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ):
        parent = super().generate_content_async

        class _Primary:
            model = self.model
            profile_name = self.profile_name

            async def generate_content_async(
                _inner, request: LlmRequest, stream: bool = False
            ):
                async for chunk in parent(request, stream=stream):
                    yield chunk

        backends: list[Any] = [_Primary()]
        if self._fallback_llm is not None:
            backends.append(self._fallback_llm)
        async for chunk in generate_with_failover(backends, llm_request, stream=stream):
            yield chunk


def get_llm_model():
    """所有 ADK Agent 共用：主套餐 + 可选 Agent Plan 备用套餐。"""

    profiles = load_llm_profiles()
    primary = litellm_agent_kwargs(profiles[0])
    primary["profile_name"] = profiles[0].name
    if len(profiles) == 1:
        return WebSafeLiteLlm(**primary)

    fallback = litellm_agent_kwargs(profiles[1])
    fallback["profile_name"] = profiles[1].name
    return FailoverLiteLlm(
        fallback_llm=WebSafeLiteLlm(**fallback),
        **primary,
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
