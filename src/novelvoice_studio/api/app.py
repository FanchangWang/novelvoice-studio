"""FastAPI 应用工厂。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import PurePosixPath
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import Engine

from novelvoice_studio import __version__
from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.api.errors import register_exception_handlers
from novelvoice_studio.api.v1.router import api_router
from novelvoice_studio.config import Settings, get_settings
from novelvoice_studio.core.logging import configure_logging
from novelvoice_studio.db.migrate import run_migrations
from novelvoice_studio.db.session import check_database, create_db_engine, create_session_factory
from novelvoice_studio.services.analysis import recover_interrupted_analysis
from novelvoice_studio.services.scripts import recover_interrupted_scripts

logger = logging.getLogger(__name__)


def _cors_origins(settings: Settings) -> list[str]:
    port = settings.app.port
    return [
        f"http://127.0.0.1:{port}",
        f"http://localhost:{port}",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:4173",
        "http://localhost:4173",
    ]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if settings.app.host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeError("出于本地优先原则，服务只允许绑定回环地址")
    settings.ensure_dirs()
    configure_logging(settings)

    if settings.app.auto_migrate:
        run_migrations(settings)

    registry = ProviderRegistry(settings)
    engine: Engine = create_db_engine(settings)
    check_database(engine)
    session_factory = create_session_factory(engine)
    recovered = recover_interrupted_analysis(session_factory)
    with session_factory() as session:
        script_recovered = recover_interrupted_scripts(session)
        session.commit()
    if (
        recovered["jobs"]
        or recovered["chapters"]
        or recovered["bible_jobs"]
        or recovered["scene_jobs"]
        or script_recovered["jobs"]
        or script_recovered["chapters"]
    ):
        logger.warning(
            "已恢复中断的分析任务：jobs=%s chapters=%s bible_jobs=%s scene_jobs=%s "
            "script_jobs=%s script_chapters=%s",
            recovered["jobs"],
            recovered["chapters"],
            recovered["bible_jobs"],
            recovered["scene_jobs"],
            script_recovered["jobs"],
            script_recovered["chapters"],
        )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info("NovelVoice Studio %s 启动，数据目录: %s", __version__, settings.data_dir)
        try:
            yield
        finally:
            engine.dispose()
            logger.info("数据库连接已关闭")

    app = FastAPI(
        title="NovelVoice Studio API",
        description="本地 AI 多角色有声小说制作系统",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.registry = registry

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(settings),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "testserver"],
    )

    @app.middleware("http")
    async def add_security_headers(request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")

    dist = settings.frontend_dist
    index_path = dist / "index.html"

    @app.get("/", include_in_schema=False)
    def root():  # type: ignore[no-untyped-def]
        if index_path.is_file():
            return FileResponse(index_path)
        return {
            "name": "NovelVoice Studio",
            "version": __version__,
            "status": "backend-only",
            "docs": "/api/docs",
            "ui": "前端尚未构建，请运行 cd frontend && npm run build",
        }

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):  # type: ignore[no-untyped-def]
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API 路径不存在")
        if not index_path.is_file():
            raise HTTPException(
                status_code=404,
                detail="前端尚未构建，请运行 cd frontend && npm run build",
            )
        resolved_dist = dist.resolve()
        candidate = (resolved_dist / full_path).resolve()
        if resolved_dist in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        if PurePosixPath(full_path).suffix:
            raise HTTPException(status_code=404, detail="静态资源不存在")
        return FileResponse(index_path)

    logger.info("应用已创建（providers=%s）", registry.mode)
    return app