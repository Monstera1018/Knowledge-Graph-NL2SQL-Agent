from typing import Annotated

from fastapi import Depends, Header, HTTPException

from server.auth.models import AuthUser
from server.auth.service import resolve_user


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


async def get_current_user(
        authorization: Annotated[str | None, Header()] = None,
) -> AuthUser:
    token = _extract_bearer_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    user = await resolve_user(token)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或会话已过期")
    return user


def _pick_workspace_id(user: AuthUser, requested: str | None) -> str:
    if not user.workspace_ids:
        raise HTTPException(status_code=403, detail="未配置 workspace 权限")

    workspace_id = (requested or user.default_workspace_id).strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="缺少 X-Workspace-Id")
    if workspace_id not in user.workspace_ids:
        raise HTTPException(status_code=403, detail=f"无权访问 workspace: {workspace_id}")
    return workspace_id


async def get_workspace_id(
        user: Annotated[AuthUser, Depends(get_current_user)],
        x_workspace_id: Annotated[str | None, Header(alias="X-Workspace-Id")] = None,
) -> str:
    return _pick_workspace_id(user, x_workspace_id)
