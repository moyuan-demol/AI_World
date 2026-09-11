"""Database bootstrap: create tables and seed the demo account."""

import logging

from app import models  # noqa: F401  (imports every model so metadata is complete)
from app.config.settings import settings
from app.core.security import hash_password
from app.database.base import Base
from app.database.session import SessionLocal, engine
from app.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await ensure_demo_user()


async def ensure_demo_user() -> None:
    async with SessionLocal() as session:
        repo = UserRepository(session)
        existing = await repo.get_by_username(settings.demo_username)
        if existing is None:
            await repo.create(
                username=settings.demo_username,
                password_hash=hash_password(settings.demo_password),
                email="demo@ai-world.local",
            )
            await session.commit()
            logger.info("Demo user '%s' created", settings.demo_username)
