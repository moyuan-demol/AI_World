"""Shared FastAPI dependencies: database session, auth, rate limiting."""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import RateLimiter
from app.core.security import decode_access_token
from app.database.session import get_session
from app.models.user import User
from app.repositories.user_repository import UserRepository

bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")

SessionDep = Annotated[AsyncSession, Depends(get_session)]

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="未登录或登录状态已失效",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: SessionDep,
) -> User:
    """Resolve the authenticated user or fail with 401.

    Every downstream query is scoped by this user id, which is what keeps the
    data of different users isolated (no cross account reads).
    """
    if credentials is None or not credentials.credentials:
        raise _UNAUTHORIZED
    payload = decode_access_token(credentials.credentials)
    if not payload:
        raise _UNAUTHORIZED
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise _UNAUTHORIZED from None
    user = await UserRepository(session).get(user_id)
    if user is None:
        raise _UNAUTHORIZED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

# ---- rate limiting ---------------------------------------------------- #
_auth_limiter = RateLimiter(max_calls=20, window_seconds=60)
_ai_limiter = RateLimiter(max_calls=40, window_seconds=60)
_upload_limiter = RateLimiter(max_calls=20, window_seconds=60)
_write_limiter = RateLimiter(max_calls=120, window_seconds=60)


def _client_key(request: Request) -> str:
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


async def auth_rate_limit(request: Request) -> None:
    await _auth_limiter.check("auth:" + _client_key(request))


async def ai_rate_limit(request: Request) -> None:
    await _ai_limiter.check("ai:" + _client_key(request))


async def upload_rate_limit(request: Request) -> None:
    await _upload_limiter.check("upload:" + _client_key(request))


async def write_rate_limit(request: Request) -> None:
    await _write_limiter.check("write:" + _client_key(request))


AuthLimit = Annotated[None, Depends(auth_rate_limit)]
AiLimit = Annotated[None, Depends(ai_rate_limit)]
UploadLimit = Annotated[None, Depends(upload_rate_limit)]
WriteLimit = Annotated[None, Depends(write_rate_limit)]
