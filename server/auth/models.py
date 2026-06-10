from pydantic import BaseModel, Field


class UserRecord(BaseModel):
    id: str
    username: str
    display_name: str = ""
    password: str


class UserWorkspaceMembership(BaseModel):
    user_id: str
    workspace_ids: list[str] = Field(min_length=1)
    default_workspace_id: str


class SessionRecord(BaseModel):
    user_id: str
    created_at: str
    expires_at: str


class AuthUser(BaseModel):
    id: str
    username: str
    display_name: str = ""
    workspace_ids: list[str] = Field(min_length=1)
    default_workspace_id: str
