"""日志初始化：控制台 + 数据目录滚动日志文件。"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from novelvoice_studio.config import Settings

_LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def configure_logging(settings: Settings, *, log_dir: Path | None = None) -> None:
    level = getattr(logging, settings.app.log_level.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    if any(getattr(handler, "_novelvoice", False) for handler in root.handlers):
        return

    formatter = logging.Formatter(_LOG_FORMAT)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    console._novelvoice = True  # type: ignore[attr-defined]
    root.addHandler(console)

    directory = log_dir or settings.data_dir / "logs"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / "app.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler._novelvoice = True  # type: ignore[attr-defined]
        root.addHandler(file_handler)
    except OSError:
        root.warning("无法创建日志文件，仅输出到控制台", exc_info=True)