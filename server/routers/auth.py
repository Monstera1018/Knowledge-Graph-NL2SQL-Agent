from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException

from server.auth.deps import get_current_user
from server.auth.models import AuthUser
from server.auth.service import login as auth_login
from server.auth.service import logout as auth_logout
from server.schemas.auth import LoginRequest, LoginResponse, MeResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    parts = authorization.strip().split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1].strip()
    return token or None


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    result = await auth_login(body.username, body.password)
    if result is None:
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token, user = result
    return LoginResponse(token=token, user=user)


@router.post("/logout", status_code=204)
async def logout(authorization: Annotated[str | None, Header()] = None) -> None:
    token = _extract_bearer_token(authorization)
    if token:
        await auth_logout(token)


@router.get("/me", response_model=MeResponse)
async def me(user: Annotated[AuthUser, Depends(get_current_user)]) -> MeResponse:
    return MeResponse(user=user)
