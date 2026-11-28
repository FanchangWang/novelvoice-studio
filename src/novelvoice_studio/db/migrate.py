"""启动时执行数据库迁移，升级前自动备份 SQLite 文件。"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from novelvoice_studio.config import PROJECT_ROOT, Settings
from novelvoice_studio.db.types import utc_now

logger = logging.getLogger(__name__)


def find_migrations_dir() -> Path:
    override = os.environ.get("NOVELVOICE_MIGRATIONS_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        if not (path / "env.py").is_file():
            raise RuntimeError(f"NOVELVOICE_MIGRATIONS_DIR 无效: {path}")
        return path
    path = PROJECT_ROOT / "migrations"
    if not (path / "env.py").is_file():
        raise RuntimeError(
            "找不到 migrations 目录，无法自动迁移。请在项目根目录运行应用，"
            "或设置 NOVELVOICE_MIGRATIONS_DIR。"
        )
    return path


def backup_sqlite(db_path: Path, backups_dir: Path) -> Path | None:
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = backups_dir / f"app-{stamp}.db"
    counter = 1
    while target.exists():
        target = backups_dir / f"app-{stamp}-{counter}.db"
        counter += 1
    with closing(sqlite3.connect(db_path)) as source, closing(
        sqlite3.connect(target)
    ) as destination:
        source.backup(destination)
        destination.commit()
    logger.info("数据库已备份: %s", target)
    return target


def build_alembic_config(settings: Settings) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(find_migrations_dir()))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    cfg.attributes["configure_logger"] = False
    return cfg


def current_revision(db_path: Path) -> str | None:
    if not db_path.is_file():
        return None
    try:
        with closing(sqlite3.connect(db_path)) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row else None


def run_migrations(settings: Settings, *, backup: bool = True) -> Path | None:
    settings.ensure_dirs()
    cfg = build_alembic_config(settings)
    head = ScriptDirectory.from_config(cfg).get_current_head()
    current = current_revision(settings.db_path)
    if current == head:
        logger.debug("数据库已是最新版本: %s", head)
        return None

    backup_path = None
    if backup:
        backup_path = backup_sqlite(settings.db_path, settings.data_dir / "backups")
    logger.info("执行数据库迁移: %s（%s -> %s）", settings.db_path, current or "空", head)
    command.upgrade(cfg, "head")
    logger.info("数据库迁移完成（%s）", utc_now().isoformat())
    return backup_path