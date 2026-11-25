from __future__ import annotations

import threading
import time
from types import MethodType

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from novelvoice_studio.ai.base import (
    ChapterScriptResult,
    ScriptUnitDraft,
)
from novelvoice_studio.models import (
    Chapter,
    GenerationJob,
    ReviewItem,
    ScriptUnit,
    ScriptVersion,
)
from novelvoice_studio.services.scripts import (
    ScriptService,
    recover_interrupted_scripts,
)


def _import_sample(upload, sample_text: str, title: str = "剧本测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _wait_bible(client: TestClient, book_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/books/{book_id}/bible/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if (
            job is None or job["status"] in {"completed", "failed", "cancelled"}
        ) and last.get("bible_status") != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"角色圣经任务未在 {timeout}s 内结束: {last}")


def _chapters(client: TestClient, book_id: str) -> list[dict]:
    response = client.get(f"/api/v1/books/{book_id}/chapters")
    assert response.status_code == 200
    return response.json()["items"]


def _chapter_by_title(client: TestClient, book_id: str, fragment: str) -> dict:
    return next(item for item in _chapters(client, book_id) if fragment in item["title"])


def _wait_scene(client: TestClient, chapter_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/chapters/{chapter_id}/scenes/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if job is None or job["status"] in {"completed", "failed", "cancelled"}:
            if last["scene_status"] != "running":
                return last
        time.sleep(0.05)
    raise AssertionError(f"分场任务未在 {timeout}s 内结束: {last}")


def _wait_script(client: TestClient, chapter_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/chapters/{chapter_id}/script/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if job is None or job["status"] in {"completed", "failed", "cancelled"}:
            if last["script_status"] != "running":
                return last
        time.sleep(0.05)
    raise AssertionError(f"剧本任务未在 {timeout}s 内结束: {last}")


def _prepare_chapter(
    client: TestClient, upload, sample_text: str, title: str = "剧本测试"
) -> tuple[str, dict]:
    book_id = _import_sample(upload, sample_text, title=title)
    response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert response.status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {"approved", "needs_review"}
    chapters = _chapters(client, book_id)
    chapter = next(
        (item for item in chapters if "雨夜来客" in item["title"]),
        chapters[0],
    )
    scene = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert scene.status_code == 202
    _wait_scene(client, chapter["id"])
    return book_id, chapter


def _start_script(client: TestClient, chapter_id: str, adaptation: str = "light"):
    return client.post(
        f"/api/v1/chapters/{chapter_id}/script",
        json={"adaptation_level": adaptation},
    )


def _assert_source_ordered(
    units: list[dict], source_text: str, cursor: int = 0
) -> None:
    assert units
    for unit in units:
        start = unit["source_start_offset"]
        end = unit["source_end_offset"]
        assert end > start
        assert start >= cursor
        gap = source_text[cursor:start]
        assert gap.strip() == "", f"非空白原文缺口: {gap!r}"
        assert source_text[start:end] == unit["source_text"]
        cursor = end


def test_script_mock_pipeline(
    client: TestClient, upload, sample_text: str, monkeypatch
) -> None:
    book_id, chapter = _prepare_chapter(client, upload, sample_text)

    gate = threading.Event()
    original_run = ScriptService._run_job

    def blocked_run(self, *args, **kwargs):
        gate.wait(10)
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(ScriptService, "_run_job", blocked_run)
    response = _start_script(client, chapter["id"])
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["job_type"] == "chapter_script"
    assert job["status"] in {"pending", "running"}

    conflict = _start_script(client, chapter["id"])
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "script_analysis_running"

    gate.set()
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    assert status["script_status"] in {"completed", "needs_review"}
    assert status["script_version"] == 1
    assert status["adaptation_level"] == "light"

    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    units = state["units"]
    assert len(units) >= 3
    assert state["version"]["is_active"] is True
    assert any(unit["unit_type"] == "dialogue" for unit in units)
    assert any(unit["unit_type"] == "narration" for unit in units)
    _assert_source_ordered(units, sample_text, chapter["source_start_offset"])
    for unit in units:
        if unit["unit_type"] in {"sfx", "ambience", "pause"}:
            assert unit["audio_status"] == "not_applicable"
            assert unit["text"] == ""
        else:
            assert unit["text"].strip()

    current = next(
        item for item in _chapters(client, book_id) if item["id"] == chapter["id"]
    )
    assert current["script_status"] == status["script_status"]


def test_script_versions_and_rollback(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    _book_id, chapter = _prepare_chapter(client, upload, sample_text, title="剧本版本")
    assert _start_script(client, chapter["id"]).status_code == 202
    _wait_script(client, chapter["id"])
    first = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    first_ids = {unit["id"] for unit in first["units"]}

    assert _start_script(client, chapter["id"]).status_code == 202
    _wait_script(client, chapter["id"])
    second = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    assert second["script_version"] == 2
    assert second["version"]["is_active"] is True
    second_ids = {unit["id"] for unit in second["units"]}
    assert first_ids.isdisjoint(second_ids)

    versions = client.get(
        f"/api/v1/chapters/{chapter['id']}/script/versions"
    ).json()
    assert versions["total"] == 2
    first_version = next(item for item in versions["items"] if item["version"] == 1)
    rollback = client.post(
        f"/api/v1/script-versions/{first_version['id']}/rollback"
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["is_active"] is True

    current = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    assert current["script_version"] == 1
    assert {unit["id"] for unit in current["units"]} == first_ids

    approve = client.post(f"/api/v1/script-versions/{second['version']['id']}/approve")
    assert approve.status_code == 200
    assert approve.json()["status"] == "approved"
    approved = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    assert approved["script_version"] == 2
    assert approved["version"]["status"] == "approved"

    with session_factory() as session:
        count = int(
            session.scalar(
                select(func.count(ScriptUnit.id)).where(
                    ScriptUnit.chapter_id == chapter["id"]
                )
            )
            or 0
        )
        assert count == len(first_ids) + len(second_ids)
        active = session.scalar(
            select(func.count(ScriptVersion.id)).where(
                ScriptVersion.chapter_id == chapter["id"],
                ScriptVersion.is_active.is_(True),
            )
        )
        assert active == 1


def test_script_requires_scenes(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text, title="剧本前置")
    chapter = _chapters(client, book_id)[0]
    response = _start_script(client, chapter["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "scenes_required"


def test_script_sfx_cue_and_source_coverage(
    client: TestClient, upload, app
) -> None:
    custom = "第一章 拍案\n\n“啪”的一声，惊堂木被县太爷重重拍下。\n"
    _book_id, chapter = _prepare_chapter(client, upload, custom, title="音效剧本")

    def fake_adapt(self, scene_text, known_characters, context=None):
        sfx = "“啪”的一声，"
        narration = "惊堂木被县太爷重重拍下。"
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="sfx",
                    source_text=sfx,
                    text="",
                    display_text="音效：惊堂木重拍（啪）",
                    speaker_type="none",
                    sfx={"label": "惊堂木", "description": "重拍声"},
                    confidence=0.9,
                ),
                ScriptUnitDraft(
                    unit_type="narration",
                    source_text=narration,
                    text=narration,
                    speaker_type="narrator",
                    confidence=0.9,
                ),
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    _wait_script(client, chapter["id"])
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    types = [unit["unit_type"] for unit in state["units"]]
    sfx_index = types.index("sfx")
    assert types[sfx_index : sfx_index + 2] == ["sfx", "narration"]
    assert state["units"][sfx_index]["audio_status"] == "not_applicable"
    assert state["units"][sfx_index]["text"] == ""
    assert state["units"][sfx_index + 1]["audio_status"] == "none"
    _assert_source_ordered(state["units"], custom, chapter["source_start_offset"])


def test_script_invalid_quote_is_filled(
    client: TestClient, upload, sample_text: str, app, session_factory
) -> None:
    book_id, chapter = _prepare_chapter(client, upload, sample_text, title="对齐补齐")

    def fake_adapt(self, scene_text, known_characters, context=None):
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="narration",
                    source_text="这段文字不在原文中",
                    text="这段文字不在原文中",
                    speaker_type="narrator",
                    confidence=0.8,
                )
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    assert state["units"]
    _assert_source_ordered(state["units"], sample_text, chapter["source_start_offset"])
    assert all(unit["source"] == "system" for unit in state["units"])
    assert all(unit["audio_status"] == "none" for unit in state["units"])
    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.code == "script_source_fill",
                ReviewItem.status == "open",
            )
        )
        assert review is not None


def test_script_unresolved_speaker_review(
    client: TestClient, upload, sample_text: str, app, session_factory
) -> None:
    book_id, chapter = _prepare_chapter(client, upload, sample_text, title="说话人异常")

    def fake_adapt(self, scene_text, known_characters, context=None):
        first = next(line for line in scene_text.splitlines() if line.strip())
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="dialogue",
                    source_text=first.strip(),
                    text="谁在说话？",
                    speaker_name="不存在的人",
                    confidence=0.3,
                )
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["script_status"] == "needs_review"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    dialogue = next(unit for unit in state["units"] if unit["unit_type"] == "dialogue")
    assert dialogue["speaker_type"] == "unknown"
    assert dialogue["review_status"] == "needs_review"
    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.code == "script_unresolved_speaker",
                ReviewItem.status == "open",
            )
        )
        assert review is not None


def test_script_cancel_and_recovery(
    client: TestClient, upload, sample_text: str, monkeypatch, session_factory
) -> None:
    _book_id, chapter = _prepare_chapter(client, upload, sample_text, title="剧本取消")
    gate = threading.Event()

    def blocked_run(self, *args, **kwargs):
        gate.wait(10)

    monkeypatch.setattr(ScriptService, "_run_job", blocked_run)
    assert _start_script(client, chapter["id"]).status_code == 202
    cancel = client.post(f"/api/v1/chapters/{chapter['id']}/script/cancel")
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "cancelled"
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "cancelled"
    assert status["script_status"] == "not_started"
    gate.set()

    with session_factory() as session:
        job = GenerationJob(
            id="job_interrupted_script",
            book_id=chapter["book_id"],
            target_id=chapter["id"],
            target_type="chapter",
            stage="script",
            job_type="chapter_script",
            status="running",
            total_items=1,
            completed_items=0,
            failed_items=0,
            progress=0.0,
            attempt_count=1,
            max_attempts=1,
            params_json={},
            result_json={},
        )
        session.add(job)
        row = session.get(Chapter, chapter["id"])
        assert row is not None
        row.script_status = "running"
        session.commit()
        result = recover_interrupted_scripts(session)
        assert result["jobs"] >= 1
        session.commit()
        session.refresh(job)
        session.refresh(row)
        assert job.status == "failed"
        assert row.script_status == "failed"


def test_script_rejects_invalid_adaptation(
    client: TestClient, upload, sample_text: str
) -> None:
    _book_id, chapter = _prepare_chapter(client, upload, sample_text, title="改编档位")
    response = _start_script(client, chapter["id"], "rewrite-everything")
    assert response.status_code == 422


def test_script_unknown_chapter(client: TestClient) -> None:
    response = _start_script(client, "ch_0000000000000000")
    assert response.status_code == 404