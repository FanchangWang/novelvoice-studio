"""启动入口：uv run novelvoice-studio。

`worker` 子命令交给 engine_services 控制面；其余参数保持原有行为，启动 Web 服务。
"""

from __future__ import annotations

import logging
import sys

import uvicorn

from novelvoice_studio.api.app import create_app
from novelvoice_studio.config import get_settings

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "worker":
        from novelvoice_studio.engine_services import main as engine_main

        raise SystemExit(engine_main(args[1:]))

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