import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from server.auth.models import SessionRecord, UserRecord, UserWorkspaceMembership

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_AUTH_DIR = _PROJECT_ROOT / "data" / "auth"
_USERS_FILE = _AUTH_DIR / "users.json"
_MEMBERSHIPS_FILE = _AUTH_DIR / "user_workspaces.json"

_SESSION_TTL_DAYS = 7


class AuthStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._sessions: dict[str, SessionRecord] = {}
        _AUTH_DIR.mkdir(parents=True, exist_ok=True)

    async def authenticate(self, username: str, password: str) -> UserRecord | None:
        username = username.strip()
        if not username or not password:
            return None
        async with self._lock:
            users = _load_users()
            for user in users:
                if user.username == username and user.password == password:
                    return user
        return None

    async def get_user(self, user_id: str) -> UserRecord | None:
        async with self._lock:
            for user in _load_users():
                if user.id == user_id:
                    return user
        return None

    async def get_membership(self, user_id: str) -> UserWorkspaceMembership | None:
        async with self._lock:
            for item in _load_memberships():
                if item.user_id == user_id:
                    return item
        return None

    async def create_session(self, user_id: str, token: str) -> SessionRecord:
        record = SessionRecord(
            user_id=user_id,
            created_at=_utc_now(),
            expires_at=(datetime.now(timezone.utc) + timedelta(days=_SESSION_TTL_DAYS)).isoformat(),
        )
        async with self._lock:
            self._sessions[token] = record
        return record

    async def delete_session(self, token: str) -> None:
        async with self._lock:
            self._sessions.pop(token, None)

    async def get_session(self, token: str) -> SessionRecord | None:
        async with self._lock:
            record = self._sessions.get(token)
            if record is None:
                return None
            expires_at = datetime.fromisoformat(record.expires_at)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= datetime.now(timezone.utc):
                del self._sessions[token]
                return None
            return record


auth_store = AuthStore()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_users() -> list[UserRecord]:
    payload = _read_json(_USERS_FILE)
    return [UserRecord.model_validate(item) for item in payload.get("users", [])]


def _load_memberships() -> list[UserWorkspaceMembership]:
    payload = _read_json(_MEMBERSHIPS_FILE)
    return [UserWorkspaceMembership.model_validate(item) for item in payload.get("memberships", [])]
