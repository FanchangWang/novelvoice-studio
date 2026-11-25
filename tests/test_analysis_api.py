from __future__ import annotations

import time

from fastapi.testclient import TestClient

from novelvoice_studio.config import Settings
from novelvoice_studio.models import Chapter, GenerationJob
from novelvoice_studio.services.analysis import recover_interrupted_analysis
from novelvoice_studio.services.paths import new_id


def _import_sample(upload, sample_text: str, title: str = "分析测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _chapters(client: TestClient, book_id: str) -> list[dict]:
    response = client.get(f"/api/v1/books/{book_id}/chapters")
    assert response.status_code == 200
    return response.json()["items"]


def _segments(client: TestClient, chapter_id: str, **params) -> list[dict]:
    response = client.get(f"/api/v1/chapters/{chapter_id}/segments", params=params)
    assert response.status_code == 200
    return response.json()["items"]


def _wait_job(client: TestClient, book_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/books/{book_id}/analysis/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if job is None or job["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"分析任务未在 {timeout}s 内结束: {last}")


def test_analyze_chapter_mock_pipeline(client: TestClient, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    chapters = _chapters(client, book_id)
    target = chapters[1]

    response = client.post(f"/api/v1/chapters/{target['id']}/analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"]["analysis_status"] in {"completed", "needs_review"}
    assert payload["chapter"]["has_summary"] is True
    assert payload["segments_written"] > 0
    assert payload["meta"]["is_mock"] is True
    assert payload["meta"]["prompt_versions"]["characters"].startswith("stage2.v1")

    detail = client.get(f"/api/v1/chapters/{target['id']}").json()
    assert detail["summary"]
    assert detail["summary_json"]["summary"] == detail["summary"]
    assert detail["analysis_meta"]["status"] == payload["chapter"]["analysis_status"]
    assert detail["analysis_meta"]["calls"]["characters"]["mock"] is True

    items = _segments(client, target["id"])
    assert len(items) == payload["segments_written"]
    assert [item["segment_index"] for item in items] == list(range(len(items)))
    chapter_start = target["source_start_offset"]
    chapter_end = target["source_end_offset"]
    for item in items:
        start = item["source_start_offset"]
        end = item["source_end_offset"]
        assert chapter_start <= start < end <= chapter_end
        assert detail["text"][start - chapter_start : end - chapter_start] == item["source_text"]
        assert item["source_text"].strip() == item["source_text"]
    offsets = [
        (item["source_start_offset"], item["source_end_offset"]) for item in items
    ]
    for (_, previous_end), (next_start, _) in zip(offsets, offsets[1:]):
        assert previous_end <= next_start
    assert any(item["segment_type"] == "dialogue" for item in items)
    assert any(item["segment_type"] == "narration" for item in items)
    dialogue = [item for item in items if item["segment_type"] == "dialogue"]
    assert all(item["review_status"] == "needs_review" for item in dialogue)
    assert all(item["speaker_type"] == "unknown" for item in dialogue)
    assert payload["chapter"]["review_count"] == sum(
        1 for item in items if item["review_status"] == "needs_review"
    )

    characters = client.get(f"/api/v1/books/{book_id}/characters").json()
    assert characters["total"] > 0


def test_rerun_preserves_locked_segment(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[1]
    first = client.post(f"/api/v1/chapters/{chapter['id']}/analysis")
    assert first.status_code == 200

    characters = client.get(f"/api/v1/books/{book_id}/characters").json()["items"]
    character_id = characters[0]["id"]
    items = _segments(client, chapter["id"])
    target = next(item for item in items if item["segment_type"] == "dialogue")

    patched = client.patch(
        f"/api/v1/segments/{target['id']}",
        json={
            "speech_text": "人工修改后的台词",
            "speaker_id": character_id,
            "review_status": "approved",
        },
    )
    assert patched.status_code == 200
    edited = patched.json()
    assert edited["speaker_type"] == "character"
    assert edited["speaker_id"] == character_id
    assert edited["review_status"] == "approved"
    assert {"speech_text", "speaker_id", "speaker_type", "emotion"} <= set(
        edited["user_locked_fields"]
    )

    rerun = client.post(f"/api/v1/chapters/{chapter['id']}/analysis")
    assert rerun.status_code == 200
    after = {item["id"]: item for item in _segments(client, chapter["id"])}
    assert target["id"] in after
    preserved = after[target["id"]]
    assert preserved["speech_text"] == "人工修改后的台词"
    assert preserved["speaker_id"] == character_id
    assert preserved["review_status"] == "approved"
    assert preserved["user_locked_fields"] == edited["user_locked_fields"]
    assert [item["segment_index"] for item in after.values()] == list(range(len(after)))
    ordered = sorted(after.values(), key=lambda item: item["segment_index"])
    for previous, current in zip(ordered, ordered[1:]):
        assert previous["source_end_offset"] <= current["source_start_offset"]


def test_full_book_analysis_job(client: TestClient, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    response = client.post(f"/api/v1/books/{book_id}/analysis", json={})
    assert response.status_code == 202
    job = response.json()
    assert job["job_type"] == "chapter_analysis"
    assert job["total_items"] == 4
    assert job["status"] in {"pending", "running", "completed"}

    status = _wait_job(client, book_id)
    completed = status["job"]
    assert completed is not None
    assert completed["status"] == "completed"
    assert completed["progress"] == 1.0
    assert completed["completed_items"] == 4
    assert status["completed_chapters"] == 4
    assert status["review_chapters"] >= 1
    assert status["total_segments"] > 0
    assert status["review_segments"] > 0
    assert status["total_characters"] > 0
    assert all(
        item["analysis_status"] in {"completed", "needs_review"}
        for item in status["chapters"]
    )
    assert all(item["has_summary"] for item in status["chapters"])


def test_chapter_too_long_marks_failed(
    client: TestClient, upload, settings: Settings
) -> None:
    content = "第一章 长章\n\n" + ("字" * (settings.analysis.max_chapter_chars + 200))
    book_id = _import_sample(upload, content, title="超长章节")
    chapter = _chapters(client, book_id)[0]

    response = client.post(f"/api/v1/chapters/{chapter['id']}/analysis")
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "chapter_too_long"
    assert error["details"]["limit"] == settings.analysis.max_chapter_chars

    detail = client.get(f"/api/v1/chapters/{chapter['id']}").json()
    assert detail["analysis_status"] == "failed"
    assert detail["analysis_meta"]["error"]["code"] == "chapter_too_long"
    status = client.get(f"/api/v1/books/{book_id}/analysis/status").json()
    assert status["failed_chapters"] == 1


def test_book_analysis_selected_chapters(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text, title="选章分析")
    chapters = _chapters(client, book_id)
    response = client.post(
        f"/api/v1/books/{book_id}/analysis",
        json={"chapter_ids": [chapters[2]["id"]]},
    )
    assert response.status_code == 202
    assert response.json()["total_items"] == 1
    status = _wait_job(client, book_id)
    assert status["job"]["completed_items"] == 1
    by_id = {item["id"]: item for item in status["chapters"]}
    assert by_id[chapters[2]["id"]]["analysis_status"] in {"completed", "needs_review"}
    assert by_id[chapters[0]["id"]]["analysis_status"] == "not_started"
    assert by_id[chapters[1]["id"]]["analysis_status"] == "not_started"


def test_book_analysis_conflict_and_cancel(
    client: TestClient, upload, sample_text: str, db_session
) -> None:
    book_id = _import_sample(upload, sample_text, title="任务冲突")
    chapter = _chapters(client, book_id)[0]
    active = GenerationJob(
        id=new_id("job"),
        book_id=book_id,
        target_id=None,
        job_type="chapter_analysis",
        status="running",
        total_items=4,
        completed_items=0,
        failed_items=0,
        progress=0.0,
        attempt_count=1,
        max_attempts=1,
        params_json={},
        result_json={},
    )
    db_session.add(active)
    db_session.commit()

    conflict = client.post(f"/api/v1/books/{book_id}/analysis", json={})
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "analysis_already_running"
    chapter_conflict = client.post(f"/api/v1/chapters/{chapter['id']}/analysis")
    assert chapter_conflict.status_code == 409

    cancelled = client.post(f"/api/v1/books/{book_id}/analysis/cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    missing = client.post(f"/api/v1/books/{book_id}/analysis/cancel")
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "no_active_job"


def test_analysis_recovery_marks_interrupted(
    client: TestClient,
    upload,
    sample_text: str,
    session_factory,
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[0]

    with session_factory() as session:
        row = session.get(Chapter, chapter["id"])
        assert row is not None
        row.analysis_status = "running"
        row.analysis_meta_json = {"status": "running"}
        job = GenerationJob(
            id=new_id("job"),
            book_id=book_id,
            target_id=None,
            job_type="chapter_analysis",
            status="running",
            total_items=4,
            completed_items=1,
            failed_items=0,
            progress=0.25,
            attempt_count=1,
            max_attempts=1,
            params_json={"chapter_ids": [chapter["id"]]},
            result_json={},
        )
        session.add(job)
        session.commit()

    recovered = recover_interrupted_analysis(session_factory)
    assert recovered == {
        "jobs": 1,
        "chapters": 1,
        "bible_jobs": 0,
        "bibles": 0,
        "scene_jobs": 0,
        "scene_chapters": 0,
    }
    status = client.get(f"/api/v1/books/{book_id}/analysis/status").json()
    assert status["job"]["status"] == "failed"
    assert status["job"]["error_code"] == "interrupted"
    assert status["failed_chapters"] == 1