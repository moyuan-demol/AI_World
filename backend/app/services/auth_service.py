"""Authentication and account management."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.core.errors import ServiceError, ValidationError
from app.core.security import create_access_token, hash_password, verify_password
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import TokenOut, UserCreate, UserLogin, UserOut


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)

    async def register(self, payload: UserCreate) -> TokenOut:
        existing = await self.users.get_by_username(payload.username)
        if existing is not None:
            raise ValidationError("用户名已被注册，请更换一个")
        user = await self.users.create(
            username=payload.username,
            password_hash=hash_password(payload.password),
            email=payload.email,
            role=self._role_for(payload.username),
        )
        await self.session.commit()
        return self.issue_token(user)

    async def login(self, payload: UserLogin) -> TokenOut:
        user = await self.users.get_by_username(payload.username.strip())
        if user is None or not verify_password(payload.password, user.password_hash):
            raise ServiceError("用户名或密码错误", status_code=401, code="invalid_credentials")
        return self.issue_token(user)

    async def demo_login(self) -> tuple[TokenOut, bool]:
        """Log into the shared demo account, creating it on first use."""
        created = False
        user = await self.users.get_by_username(settings.demo_username)
        if user is None:
            user = await self.users.create(
                username=settings.demo_username,
                password_hash=hash_password(settings.demo_password),
                email="demo@ai-world.local",
                role=self._role_for(settings.demo_username),
            )
            await self.session.commit()
            created = True
        return self.issue_token(user), created

    async def get_user(self, user_id: int) -> User:
        user = await self.users.get(user_id)
        if user is None:
            raise ServiceError("登录状态无效，请重新登录", status_code=401, code="invalid_token")
        return user

    @staticmethod
    def _role_for(username: str) -> str:
        """ADMIN_USERNAMES 中列出的用户名授予 admin 角色（默认普通用户）。"""
        return "admin" if username in settings.admin_username_list else "user"

    @staticmethod
    def issue_token(user: User) -> TokenOut:
        return TokenOut(
            access_token=create_access_token(user.id),
            user=UserOut.model_validate(user),
        )
