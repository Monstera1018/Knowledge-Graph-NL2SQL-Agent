from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PROTOCOL_VERSION = 1


class AgentDescriptor(BaseModel):
    id: str
    name: str
    description: str = ""
    capabilities: list[str] = Field(default_factory=list)


class StreamEventType(StrEnum):
    TURN_START = "turn_start"
    TURN_END = "turn_end"
    PART_START = "part_start"
    PART_DELTA = "part_delta"
    PART_END = "part_end"
    ERROR = "error"


class StreamEvent(BaseModel):
    v: int = PROTOCOL_VERSION
    type: StreamEventType
    session_id: str
    turn_id: str
    ts: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    agent_id: str | None = None
    user_message: str | None = None
    status: Literal["ok", "error", "cancelled"] | None = None
    part: dict[str, Any] | None = None
    part_id: str | None = None
    patch: dict[str, Any] | None = None
    message: str | None = None
    code: str | None = None
    recoverable: bool | None = None


def new_turn_id() -> str:
    return uuid4().hex


def sse_line(event: StreamEvent) -> str:
    return f"data: {event.model_dump_json(exclude_none=True)}\n\n"
