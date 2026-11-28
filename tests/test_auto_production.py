from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Book, BookBible, Chapter, GenerationJob
from novelvoice_studio.services.auto_production import (
    AUTO_JOB_TYPE,
    AutoProductionService,
    recover_interrupted_auto,
)
from novelvoice_studio.services.paths import new_id
from novelvoice_studio.services.unit_audio import UnitAudioService


def _import(client: TestClient, upload, sample_text: str) -> str:
    response = upload("/api/v1/books/import", sample_text, title="自动制作测试")
    assert response.status_code == 201, response.text
    return response.json()["book"]["id"]


def _wait_auto(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/auto-production/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"自动制作未结束: {last}")


def _service(app, settings: Settings) -> AutoProductionService:
    return AutoProductionService(
        settings,
        app.state.session_factory,
        ProviderRegistry(settings),
    )


def test_auto_requires_included_chapters(client: TestClient, upload, sample_text: str, db_session: Session) -> None:
    book_id = _import(client, upload, sample_text)
    for chapter in db_session.scalars(select(Chapter).where(Chapter.book_id == book_id)):
        chapter.analysis_included = False
    db_session.commit()

    response = client.post(f"/api/v1/books/{book_id}/production/auto", json={})
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "no_included_chapters"


def test_auto_rejects_another_active_job(client: TestClient, upload, sample_text: str, db_session: Session) -> None:
    book_id = _import(client, upload, sample_text)
    now = utc_now()
    db_session.add(GenerationJob(
        id=new_id("job"), book_id=book_id, target_id=book_id, target_type="book",
        stage="production", job_type="chapter_analysis", status="running",
        total_items=1, completed_items=0, failed_items=0, progress=0.0,
        attempt_count=1, max_attempts=1, params_json={}, result_json={},
        created_at=now, updated_at=now,
    ))
    db_session.commit()

    response = client.post(f"/api/v1/books/{book_id}/production/auto", json={})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "book_job_running"


def test_auto_plans_incremental_bible_for_uncovered_chapters(
    client: TestClient, upload, sample_text: str, db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = _import(client, upload, sample_text)
    chapter_ids = list(db_session.scalars(
        select(Chapter.id).where(
            Chapter.book_id == book_id, Chapter.analysis_included.is_(True),
        ).order_by(Chapter.sequence)
    ))
    now = utc_now()
    db_session.add(BookBible(
        id=new_id("bible"), book_id=book_id, version=1, status="approved", source="ai",
        content_json={"chapter_timelines": []}, timeline_json={}, confidence=1.0,
        is_active=True, created_at=now, updated_at=now,
    ))
    db_session.commit()
    monkeypatch.setattr("novelvoice_studio.services.auto_production.threading.Thread.start", lambda _thread: None)

    response = client.post(f"/api/v1/books/{book_id}/production/auto", json={})

    assert response.status_code == 202, response.text
    assert response.json()["result"]["stages"]["bible"] == {"status": "pending"}
    db_session.expire_all()
    job = db_session.get(GenerationJob, response.json()["id"])
    assert job is not None
    assert job.params_json["needs_bible"] is True
    assert job.params_json["bible_chapter_ids"] == chapter_ids


def test_auto_runs_full_mock_pipeline_and_resumes(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import(client, upload, sample_text)
    started = client.post(f"/api/v1/books/{book_id}/production/auto", json={})
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    job = _wait_auto(client, job_id)
    assert job["status"] == "completed", job
    assert 0.0 <= job["progress"] <= 1.0
    assert job["result"]["stages"]["bible"]["status"] == "completed"
    assert job["result"]["stages"]["casting"]["status"] == "completed"
    assert job["result"]["stages"]["script"]["status"] == "completed"
    assert job["result"]["stages"]["audio"]["status"] == "completed"
    assert job["result"]["audio"]["ready"] > 0
    assert job["result"]["audio"]["failed"] == 0
    assert job["result"]["system_failures"] == []

    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    assert overview["bible_ready"] is True
    assert overview["chapters_with_scripts"] == overview["chapter_count"]
    assert overview["audio_ready_units"] == overview["tts_unit_count"]
    assert overview["cast_count"] > 0
    assert overview["narrator_ready"] is True
    assert overview["job"]["id"] == job_id
    assert overview["job"]["status"] == "completed"
    assert overview["job"]["result"]["stages"]["audio"]["status"] == "completed"
    cast_plan = client.get(f"/api/v1/books/{book_id}/casts").json()
    assert cast_plan["cast_count"] > 0

    resumed = client.post(f"/api/v1/books/{book_id}/production/auto", json={})
    assert resumed.status_code == 202, resumed.text
    second = _wait_auto(client, resumed.json()["id"])
    assert second["status"] == "completed", second
    assert second["result"]["stages"]["bible"]["status"] == "skipped"
    assert second["result"]["stages"]["casting"]["status"] == "skipped"
    assert second["total_items"] == 2, (second["total_items"], second["completed_items"], second["result"])
    assert second["result"]["audio"]["ready"] == 0


def test_auto_lists_all_stages_before_processing(
    client: TestClient,
    upload,
    sample_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = _import(client, upload, sample_text)
    monkeypatch.setattr("novelvoice_studio.services.auto_production.threading.Thread.start", lambda _thread: None)

    started = client.post(f"/api/v1/books/{book_id}/production/auto", json={})

    assert started.status_code == 202, started.text
    assert started.json()["result"]["stages"] == {
        "bible": {"status": "pending"},
        "casting": {"status": "pending"},
        "script": {"status": "pending"},
        "audio": {"status": "pending"},
    }


def test_auto_casting_failure_still_generates_scripts(
    client: TestClient,
    upload,
    sample_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = _import(client, upload, sample_text)
    monkeypatch.setattr(
        "novelvoice_studio.services.casting.CastingService._process_target",
        lambda *_args, **_kwargs: {"status": "failed", "error": "测试语音模型不可用"},
    )

    started = client.post(f"/api/v1/books/{book_id}/production/auto", json={})
    assert started.status_code == 202, started.text
    job = _wait_auto(client, started.json()["id"])

    assert job["status"] == "failed"
    assert job["result"]["stages"]["casting"]["status"] == "failed"
    assert job["result"]["stages"]["script"]["status"] == "completed"
    assert job["result"]["stages"]["audio"]["status"] in {"completed", "failed"}
    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    assert overview["chapters_with_scripts"] == overview["chapter_count"]
    assert overview["job"]["id"] == started.json()["id"]
    assert overview["job"]["status"] == "failed"
    assert overview["derived_status"] == "failed"
    assert any("语音模型不可用" in item["message"] for item in job["result"]["system_failures"])


def test_auto_cancel_propagates_to_current_child(
    client: TestClient,
    app,
    upload,
    sample_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = _import(client, upload, sample_text)
    monkeypatch.setattr("novelvoice_studio.services.auto_production.threading.Thread.start", lambda _thread: None)
    service = _service(app, app.state.settings)
    job = service.start(book_id)
    with app.state.session_factory() as session:
        row = session.get(GenerationJob, job.id)
        assert row is not None
        row.result_json = {**row.result_json, "current_child": {"id": "job_0000000000000001", "type": "batch"}}
        session.commit()
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(service, "_cancel_child", lambda child_id, kind: calls.append((child_id, kind)))

    cancelled = service.cancel(job.id)
    assert cancelled.status == "cancelled"
    assert calls == [("job_0000000000000001", "batch")]
    with app.state.session_factory() as session:
        book = session.get(Book, book_id)
        assert book is not None
        assert book.production_status == "not_started"


def test_auto_audio_failure_isolated_and_recorded(
    client: TestClient,
    app,
    upload,
    sample_text: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = _import(client, upload, sample_text)
    monkeypatch.setattr("novelvoice_studio.services.auto_production.threading.Thread.start", lambda _thread: None)
    service = _service(app, app.state.settings)
    job = service.start(book_id)
    monkeypatch.setattr(service, "_audio_units", lambda _session, _book_id: [
        SimpleNamespace(id="unit_0000000000000001"),
        SimpleNamespace(id="unit_0000000000000002"),
    ])
    attempted: list[str] = []

    def generate(_self, unit_id: str) -> None:
        attempted.append(unit_id)
        audio_stage = service.get_job(job.id).result["stages"]["audio"]
        assert audio_stage["status"] == "running"
        assert audio_stage["total_items"] == 2
        assert audio_stage["completed_items"] == len(attempted) - 1
        if unit_id.endswith("1"):
            raise RuntimeError("测试语音引擎不可用")

    monkeypatch.setattr(UnitAudioService, "generate", generate)
    service._generate_audio(job.id, book_id)

    final = service.get_job(job.id)
    assert final.status == "failed"
    assert final.error_code == "audio_units_failed"
    assert attempted == ["unit_0000000000000001", "unit_0000000000000002"]
    assert final.result["audio"] == {"ready": 1, "failed": 1, "skipped": 0}
    assert final.result["stages"]["audio"]["completed_items"] == 2
    assert final.result["stages"]["audio"]["total_items"] == 2
    assert final.result["stages"]["audio"]["progress"] == 1.0
    assert final.result["system_failures"][0]["unit_id"] == "unit_0000000000000001"
    assert final.progress == 1.0


def test_recover_interrupted_auto(app, db_session: Session) -> None:
    now = utc_now()
    book = Book(
        id="book_1", title="恢复测试", author=None, description=None, language="zh-CN",
        status="ready", source_encoding="utf-8", source_file_path=None,
        source_char_count=0, project_dir="books/book_1",
        production_status="analyzing", bible_status="not_started",
        created_at=now, updated_at=now,
    )
    job = GenerationJob(
        id=new_id("job"), book_id=book.id, target_id=book.id, target_type="book",
        stage="production", job_type=AUTO_JOB_TYPE, status="running",
        total_items=4, completed_items=1, failed_items=0, progress=0.25,
        attempt_count=1, max_attempts=1, params_json={}, result_json={},
        created_at=now, updated_at=now,
    )
    db_session.add(book)
    db_session.add(job)
    db_session.commit()

    assert recover_interrupted_auto(db_session) == {"auto_production_jobs": 1}
    db_session.commit()
    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "interrupted"
