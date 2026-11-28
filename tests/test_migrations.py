from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from novelvoice_studio.config import Settings
from novelvoice_studio.db.migrate import backup_sqlite, current_revision, run_migrations
from novelvoice_studio.models import Book
from novelvoice_studio.services.text_io import sha256_bytes


def test_migrations_create_all_tables(settings: Settings) -> None:
    with sqlite3.connect(settings.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    expected = {
        "books",
        "chapters",
        "characters",
        "character_relationships",
        "voice_profiles",
        "segments",
        "audio_assets",
        "generation_jobs",
        "playback_states",
        "book_bibles",
        "character_arcs",
        "scenes",
        "scene_characters",
        "script_versions",
        "script_units",
        "voice_actors",
        "voice_casts",
        "review_items",
        "book_id_sequences",
    }
    assert expected.issubset(tables)
    assert revision is not None
    assert revision[0] == current_revision(settings.db_path)


def test_migrations_are_idempotent(settings: Settings) -> None:
    assert run_migrations(settings, backup=False) is None
    assert current_revision(settings.db_path) is not None


def test_product_flow_columns_and_sequence(settings: Settings) -> None:
    with sqlite3.connect(settings.db_path) as connection:
        chapter_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('chapters')")
        }
        actor_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('voice_actors')")
        }
        review_columns = {
            row[1] for row in connection.execute("PRAGMA table_info('review_items')")
        }
        sequence = connection.execute(
            "SELECT next_value FROM book_id_sequences WHERE id = 1"
        ).fetchone()
    assert "classification_status" in chapter_columns
    assert "scope" in actor_columns
    assert "category" in review_columns
    assert sequence is not None and sequence[0] >= 1


def test_segment_evidence_column_exists(settings: Settings) -> None:
    with sqlite3.connect(settings.db_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info('segments')")
        }
    assert "evidence_json" in columns


def test_backup_sqlite(settings: Settings, tmp_path) -> None:
    target = backup_sqlite(settings.db_path, tmp_path / "backups")
    assert target is not None
    assert target.is_file()
    with sqlite3.connect(target) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()


def test_utc_timestamps_roundtrip(upload, sample_text: str, db_session) -> None:
    response = upload("/api/v1/books/import", sample_text, title="时间测试")
    assert response.status_code == 201
    book_id = response.json()["book"]["id"]
    created_at = response.json()["book"]["created_at"]
    assert created_at.endswith("Z") or created_at.endswith("+00:00")
    parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None

    row = db_session.get(Book, book_id)
    assert row is not None
    assert row.created_at.tzinfo is not None
    assert row.created_at.utcoffset() == timezone.utc.utcoffset(None)
    assert row.source_sha256 == sha256_bytes(sample_text.encode("utf-8"))