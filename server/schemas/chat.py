"""聊天路由的 HTTP 请求/响应模型（非流式协议）。"""

from pydantic import BaseModel, Field

from server.chat.protocol import AgentDescriptor, PROTOCOL_VERSION


class ChatProtocolInfo(BaseModel):
    version: int = PROTOCOL_VERSION


class AgentListResponse(BaseModel):
    agents: list[AgentDescriptor]


class SendMessageRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    content: str = Field(min_length=1)
