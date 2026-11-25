"""启动入口：uv run novelvoice-studio。"""

from __future__ import annotations

import logging

import uvicorn

from novelvoice_studio.api.app import create_app
from novelvoice_studio.config import get_settings

logger = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.app.host,
        port=settings.app.port,
        log_level=settings.app.log_level.lower(),
        log_config=None,
        access_log=True,
    )
    server = uvicorn.Server(config)
    # 供 scripts/server_control.py 通过带令牌的本机接口触发优雅退出。
    app.state.uvicorn_server = server
    logger.info("监听 http://%s:%s", settings.app.host, settings.app.port)
    server.run()


if __name__ == "__main__":
    main()