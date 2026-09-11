"""Auth endpoints: register / login / demo login / current user."""

from fastapi import APIRouter

from app.api.deps import AuthLimit, CurrentUser, SessionDep
from app.schemas.user import TokenOut, UserCreate, UserLogin, UserOut
from app.services.auth_service import AuthService
from app.services.character_service import CharacterService

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, summary="注册并登录")
async def register(payload: UserCreate, session: SessionDep, _: AuthLimit) -> TokenOut:
    service = AuthService(session)
    token = await service.register(payload)
    await CharacterService(session).ensure_defaults(token.user.id)
    return token


@router.post("/login", response_model=TokenOut, summary="用户名密码登录")
async def login(payload: UserLogin, session: SessionDep, _: AuthLimit) -> TokenOut:
    return await AuthService(session).login(payload)


@router.post("/demo-login", response_model=TokenOut, summary="一键体验 Demo 账号")
async def demo_login(session: SessionDep, _: AuthLimit) -> TokenOut:
    token, _created = await AuthService(session).demo_login()
    await CharacterService(session).ensure_defaults(token.user.id)
    return token


@router.get("/me", response_model=UserOut, summary="当前登录用户")
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
