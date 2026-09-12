from fastapi import APIRouter

from app.api.routes import admin, auth, characters, chat, knowledge, memory, recycle, roundtable

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(characters.router)
api_router.include_router(knowledge.router)
api_router.include_router(chat.router)
api_router.include_router(roundtable.router)
api_router.include_router(memory.router)
api_router.include_router(recycle.router)
api_router.include_router(admin.router)
