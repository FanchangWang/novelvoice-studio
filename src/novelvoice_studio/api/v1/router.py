"""v1 API 路由汇总。"""

from fastapi import APIRouter

from novelvoice_studio.api.v1 import (
    analysis,
    audio,
    bible,
    books,
    chapters,
    characters,
    scenes,
    scripts,
    segments,
    system,
    voices,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(books.router)
api_router.include_router(chapters.router)
api_router.include_router(analysis.router)
api_router.include_router(bible.router)
api_router.include_router(scenes.router)
api_router.include_router(scripts.router)
api_router.include_router(characters.router)
api_router.include_router(segments.router)
api_router.include_router(voices.router)
api_router.include_router(audio.router)

__all__ = ["api_router"]