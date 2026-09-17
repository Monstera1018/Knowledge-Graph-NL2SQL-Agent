import secrets

from server.auth.models import AuthUser, UserRecord, UserWorkspaceMembership
from server.auth.store import auth_store


def _build_auth_user(user: UserRecord, membership: UserWorkspaceMembership | None) -> AuthUser | None:
    if membership is None or not membership.workspace_ids:
        return None

    default_workspace_id = membership.default_workspace_id
    if default_workspace_id not in membership.workspace_ids:
        default_workspace_id = membership.workspace_ids[0]

    return AuthUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        workspace_ids=membership.workspace_ids,
        default_workspace_id=default_workspace_id,
    )


async def login(username: str, password: str) -> tuple[str, AuthUser] | None:
    user = await auth_store.authenticate(username, password)
    if user is None:
        return None

    membership = await auth_store.get_membership(user.id)
    auth_user = _build_auth_user(user, membership)
    if auth_user is None:
        return None

    token = secrets.token_urlsafe(32)
    await auth_store.create_session(user.id, token)
    return token, auth_user


async def logout(token: str) -> None:
    await auth_store.delete_session(token)


async def resolve_user(token: str) -> AuthUser | None:
    session = await auth_store.get_session(token)
    if session is None:
        return None

    user = await auth_store.get_user(session.user_id)
    if user is None:
        return None

    membership = await auth_store.get_membership(user.id)
    return _build_auth_user(user, membership)
