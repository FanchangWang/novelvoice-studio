from __future__ import annotations

import threading
import time
from types import MethodType

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from novelvoice_studio.ai.base import (
    ChapterSceneResult,
    SceneCharacterStateDraft,
    SceneDraft,
)
from novelvoice_studio.models import (
    BookBible,
    Chapter,
    GenerationJob,
    ReviewItem,
    Scene,
)
from novelvoice_studio.services.analysis import recover_interrupted_analysis
from novelvoice_studio.services.paths import new_id
from novelvoice_studio.services.scenes import SceneService, _Unit, _Window


def _import_sample(upload, sample_text: str, title: str = "分场测试") -> str:
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


def _setup_bible(
    client: TestClient, upload, sample_text: str, title: str = "分场测试"
) -> str:
    book_id = _import_sample(upload, sample_text, title=title)
    response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert response.status_code == 202
    status = _wait_bible(client, book_id)
    assert status["job"]["status"] == "completed"
    assert status["bible_status"] in {"approved", "needs_review"}
    return book_id


def _chapters(client: TestClient, book_id: str) -> list[dict]:
    response = client.get(f"/api/v1/books/{book_id}/chapters")
    assert response.status_code == 200
    return response.json()["items"]


def _chapter_by_title(client: TestClient, book_id: str, fragment: str) -> dict:
    return next(item for item in _chapters(client, book_id) if fragment in item["title"])


def _wait_scene(
    client: TestClient, chapter_id: str, timeout: float = 30.0
) -> dict:
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


def _assert_contiguous(
    scenes: list[dict],
    chapter: dict,
    source_text: str,
    *,
    cover_chapter: bool = True,
) -> None:
    assert scenes
    if cover_chapter:
        assert (
            source_text[chapter["source_start_offset"] : scenes[0]["source_start_offset"]]
            .strip()
            == ""
        )
        assert (
            source_text[scenes[-1]["source_end_offset"] : chapter["source_end_offset"]]
            .strip()
            == ""
        )
    for previous, current in zip(scenes, scenes[1:]):
        gap = source_text[
            previous["source_end_offset"] : current["source_start_offset"]
        ]
        assert gap.strip() == ""


def test_scene_mock_pipeline(
    client: TestClient, upload, sample_text: str, monkeypatch
) -> None:
    book_id = _setup_bible(client, upload, sample_text)
    chapter = _chapter_by_title(client, book_id, "雨夜来客")

    # 模拟任务可能瞬间跑完，用闸门确保并发冲突断言稳定。
    gate = threading.Event()
    original_run = SceneService._run_job

    def blocked_run(self, *args, **kwargs):
        gate.wait(10)
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(SceneService, "_run_job", blocked_run)
    response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["job_type"] == "chapter_scenes"
    assert job["status"] in {"pending", "running"}

    conflict = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "scene_analysis_running"

    gate.set()
    status = _wait_scene(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    assert status["scene_status"] in {"completed", "needs_review"}
    assert status["scene_version"] == 1

    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    scenes = state["scenes"]
    assert len(scenes) >= 3
    assert all(item["analysis_version"] == 1 for item in scenes)
    _assert_contiguous(scenes, chapter, sample_text)
    assert any(item["characters"] for item in scenes)
    names = {
        character["character_name"]
        for scene in scenes
        for character in scene["characters"]
    }
    assert {"林见秋", "沈砚"} & names
    assert state["open_reviews"] >= 0

    chapters = _chapters(client, book_id)
    current = next(item for item in chapters if item["id"] == chapter["id"])
    assert current["scene_version"] == 1
    assert current["scene_status"] == status["scene_status"]
    assert current["script_status"] != "completed"


def test_scene_rerun_versions_and_cleans_old_reviews(
    client: TestClient,
    upload,
    sample_text: str,
    session_factory,
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场重跑")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    first = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert first.status_code == 202
    _wait_scene(client, chapter["id"])
    first_state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    first_ids = {item["id"] for item in first_state["scenes"]}

    second = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert second.status_code == 202
    _wait_scene(client, chapter["id"])
    second_state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    assert second_state["scene_version"] == 2
    second_ids = {item["id"] for item in second_state["scenes"]}
    assert first_ids.isdisjoint(second_ids)

    with session_factory() as session:
        versions = set(
            session.scalars(
                select(Scene.analysis_version).where(
                    Scene.chapter_id == chapter["id"]
                )
            )
        )
        assert versions == {1, 2}
        note_count = int(
            session.scalar(
                select(func.count(ReviewItem.id)).where(
                    ReviewItem.book_id == book_id,
                    ReviewItem.status == "open",
                    ReviewItem.code == "scene_note:1",
                )
            )
            or 0
        )
        assert note_count == 1


def test_scene_requires_bible_and_chapter_coverage(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text, title="分场前置条件")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    missing_bible = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert missing_bible.status_code == 422
    assert missing_bible.json()["error"]["code"] == "bible_required"

    response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert response.status_code == 202
    _wait_bible(client, book_id)

    split = client.post(
        f"/api/v1/chapters/{chapter['id']}/split",
        json={"paragraph_index": 2, "new_title": "新拆章节"},
    )
    assert split.status_code == 200
    new_chapter = split.json()["chapter"]
    missing_timeline = client.post(f"/api/v1/chapters/{new_chapter['id']}/scenes")
    assert missing_timeline.status_code == 422
    assert missing_timeline.json()["error"]["code"] == "bible_chapter_missing"


def test_scene_long_chapter_windows(
    client: TestClient, upload, sample_text: str, app
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="长章节分场")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    app.state.settings.analysis.max_chapter_chars = 200

    response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert response.status_code == 202
    job = response.json()
    assert job["total_items"] > 1
    status = _wait_scene(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    _assert_contiguous(state["scenes"], chapter, sample_text)
    assert len(state["scenes"]) >= 2
    assert status["job"]["progress"] == 1.0


def test_scene_uses_timeline_overview_story_order(
    client: TestClient,
    upload,
    sample_text: str,
    session_factory,
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场剧情顺序")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    with session_factory() as session:
        bible = session.scalar(
            select(BookBible).where(
                BookBible.book_id == book_id,
                BookBible.is_active.is_(True),
            )
        )
        assert bible is not None
        content = dict(bible.content_json or {})
        rows: list[dict] = []
        for row in content.get("chapter_timelines") or []:
            cleaned = dict(row)
            cleaned.pop("story_order", None)
            rows.append(cleaned)
        content["chapter_timelines"] = rows
        bible.content_json = content
        expected = next(
            item["story_order"]
            for item in (bible.timeline_json or {}).get("chapters") or []
            if item.get("chapter_id") == chapter["id"]
        )
        assert expected is not None
        session.commit()

    response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
    assert response.status_code == 202, response.text
    status = _wait_scene(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    assert state["scenes"]
    assert all(item["story_order"] == expected for item in state["scenes"])


def test_scene_invalid_window_falls_back_without_blocking(
    client: TestClient, upload, sample_text: str, app
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场回退")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    app.state.settings.analysis.max_chapter_chars = 200
    registry = app.state.registry
    original = registry.llm.analyze_chapter_scenes
    calls = {"count": 0}

    def fake(self, paragraphs, known_characters, context=None):
        calls["count"] += 1
        first = paragraphs[0]["index"]
        last = paragraphs[-1]["index"]
        if calls["count"] == 1:
            return ChapterSceneResult(
                scenes=[
                    SceneDraft(
                        paragraph_start=first,
                        paragraph_end=last + 1,
                        title="不完整窗口",
                        confidence=0.8,
                    )
                ],
                meta={"mock": True},
            )
        return ChapterSceneResult(
            scenes=[
                SceneDraft(
                    paragraph_start=first,
                    paragraph_end=last,
                    title="完整窗口",
                    confidence=0.8,
                )
            ],
            meta={"mock": True},
        )

    registry.llm.analyze_chapter_scenes = MethodType(fake, registry.llm)
    try:
        response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
        assert response.status_code == 202
        status = _wait_scene(client, chapter["id"])
    finally:
        registry.llm.analyze_chapter_scenes = original

    assert status["job"]["status"] == "completed"
    assert status["job"]["result"]["fallback_windows"] == 1
    assert status["scene_status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    _assert_contiguous(state["scenes"], chapter, sample_text)
    sources = {item["source"] for item in state["scenes"]}
    assert "system" in sources
    fallback = next(item for item in state["scenes"] if item["source"] == "system")
    assert fallback["analysis_status"] == "completed"
    assert fallback["confidence"] <= 0.2
    codes = {item["code"] for item in state["review_items"]}
    assert "scene_llm_fallback" not in codes
    assert status["scene_status"] == "completed"


def test_scene_all_windows_missing_tail_are_repaired(
    client: TestClient, upload, sample_text: str, app
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场漏尾恢复")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    app.state.settings.analysis.max_chapter_chars = 120
    registry = app.state.registry
    original = registry.llm.analyze_chapter_scenes

    def fake(self, paragraphs, known_characters, context=None):
        first = paragraphs[0]["index"]
        return ChapterSceneResult(
            scenes=[
                SceneDraft(
                    paragraph_start=first,
                    paragraph_end=first,
                    title="模型只覆盖首段",
                    confidence=0.9,
                )
            ],
            meta={"mock": True},
        )

    registry.llm.analyze_chapter_scenes = MethodType(fake, registry.llm)
    try:
        response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
        assert response.status_code == 202
        total_windows = response.json()["total_items"]
        assert total_windows > 1
        status = _wait_scene(client, chapter["id"])
    finally:
        registry.llm.analyze_chapter_scenes = original

    assert status["job"]["status"] == "completed"
    assert status["job"]["result"]["fallback_windows"] == 0
    assert status["job"]["result"]["repaired_windows"] == total_windows
    diagnostics = status["job"]["result"]["coverage_diagnostics"]
    assert len(diagnostics) == total_windows
    assert all(item["missing_range"] for item in diagnostics)
    assert all(item["missing_excerpt"] for item in diagnostics)
    assert all(item["raw_output_excerpt"] for item in diagnostics)
    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    _assert_contiguous(state["scenes"], chapter, sample_text)
    repaired = [
        item for item in state["scenes"] if item["title"] == "原文尾部（自动补全）"
    ]
    assert len(repaired) == total_windows
    assert all(item["source"] == "system" for item in repaired)
    assert status["scene_status"] == "completed"


def test_scene_character_resolution_and_age_override(
    client: TestClient, upload, sample_text: str, app
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场状态校正")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    bible = client.get(f"/api/v1/books/{book_id}/bible").json()
    chapters = {item["id"]: item["sequence"] for item in _chapters(client, book_id)}

    def target_age(character: dict) -> str:
        for arc in character["arcs"]:
            start = chapters.get(arc.get("chapter_start_id") or "")
            end = chapters.get(arc.get("chapter_end_id") or "")
            if (
                start is not None
                and end is not None
                and start <= chapter["sequence"] <= end
            ):
                return arc["age_stage"]
        if len(character["arcs"]) == 1:
            return character["arcs"][0]["age_stage"]
        return character["age_range"]

    candidates = [
        item
        for item in bible["bible"]["characters"]
        if target_age(item) not in {"child", "unknown"}
    ]
    assert candidates
    candidate = candidates[0]
    target = target_age(candidate)
    registry = app.state.registry
    original = registry.llm.analyze_chapter_scenes

    def fake(self, paragraphs, known_characters, context=None):
        first = paragraphs[0]["index"]
        last = paragraphs[-1]["index"]
        return ChapterSceneResult(
            scenes=[
                SceneDraft(
                    paragraph_start=first,
                    paragraph_end=last,
                    title="校正测试",
                    pov_name="不存在的视角",
                    confidence=0.9,
                    characters=[
                        SceneCharacterStateDraft(
                            name=candidate["canonical_name"],
                            presence="speaker",
                            age_stage="child",
                            confidence=0.9,
                        ),
                        SceneCharacterStateDraft(
                            name="不存在的人",
                            presence="present",
                            age_stage="adult",
                            confidence=0.9,
                        ),
                    ],
                )
            ],
            meta={"mock": True},
        )

    registry.llm.analyze_chapter_scenes = MethodType(fake, registry.llm)
    try:
        response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
        assert response.status_code == 202
        status = _wait_scene(client, chapter["id"])
    finally:
        registry.llm.analyze_chapter_scenes = original

    assert status["job"]["status"] == "completed"
    assert status["scene_status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/scenes").json()
    scene = state["scenes"][0]
    assert scene["pov_character_id"] is None
    names = [item["character_name"] for item in scene["characters"]]
    assert candidate["canonical_name"] in names
    assert "不存在的人" not in names
    resolved = next(
        item
        for item in scene["characters"]
        if item["character_name"] == candidate["canonical_name"]
    )
    assert resolved["age_stage"] == target
    codes = {item["code"] for item in state["review_items"]}
    assert {"scene_unknown_character", "scene_age_stage_conflict"} <= codes
    assert "scene_pov_unknown" not in codes


def test_scene_cancel_and_recovery(
    client: TestClient,
    upload,
    sample_text: str,
    app,
    session_factory,
) -> None:
    book_id = _setup_bible(client, upload, sample_text, title="分场取消恢复")
    chapter = _chapter_by_title(client, book_id, "雨夜来客")
    registry = app.state.registry
    original = registry.llm.analyze_chapter_scenes
    started = threading.Event()
    release = threading.Event()

    def slow(self, paragraphs, known_characters, context=None):
        started.set()
        release.wait(timeout=5)
        return ChapterSceneResult(
            scenes=[
                SceneDraft(
                    paragraph_start=paragraphs[0]["index"],
                    paragraph_end=paragraphs[-1]["index"],
                    title="取消测试",
                    confidence=0.8,
                )
            ],
            meta={"mock": True},
        )

    registry.llm.analyze_chapter_scenes = MethodType(slow, registry.llm)
    try:
        response = client.post(f"/api/v1/chapters/{chapter['id']}/scenes")
        assert response.status_code == 202
        assert started.wait(timeout=5)
        cancelled = client.post(f"/api/v1/chapters/{chapter['id']}/scenes/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        missing = client.post(f"/api/v1/chapters/{chapter['id']}/scenes/cancel")
        assert missing.status_code == 409
        assert missing.json()["error"]["code"] == "no_active_job"
    finally:
        release.set()
        registry.llm.analyze_chapter_scenes = original

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = client.get(f"/api/v1/chapters/{chapter['id']}/scenes/status").json()
        if status["scene_status"] != "running":
            break
        time.sleep(0.05)
    assert status["job"]["status"] == "cancelled"
    assert status["scene_status"] == "not_started"

    with session_factory() as session:
        row = session.get(Chapter, chapter["id"])
        assert row is not None
        row.scene_status = "running"
        job = GenerationJob(
            id=new_id("job"),
            book_id=book_id,
            target_id=chapter["id"],
            target_type="chapter",
            stage="scene",
            job_type="chapter_scenes",
            status="running",
            total_items=2,
            progress=0.3,
            attempt_count=1,
            max_attempts=1,
            params_json={"scene_version": 9},
            result_json={},
        )
        session.add(job)
        session.commit()

    recovered = recover_interrupted_analysis(session_factory)
    assert recovered["scene_jobs"] >= 1
    assert recovered["scene_chapters"] >= 1
    status = client.get(f"/api/v1/chapters/{chapter['id']}/scenes/status").json()
    assert status["job"]["status"] == "failed"
    assert status["job"]["error_code"] == "interrupted"
    assert status["scene_status"] == "failed"


def test_scene_one_based_ranges_are_normalized_without_retry() -> None:
    service = object.__new__(SceneService)
    window = _Window(
        index=0,
        total=1,
        units=tuple(_Unit(index=index, start=index * 10, end=index * 10 + 9, text="段落") for index in range(3)),
    )
    shifted = ChapterSceneResult(scenes=[SceneDraft(
        paragraph_start=1, paragraph_end=3, title="整体偏移", confidence=0.9,
    )])

    repaired, detail = service._repair_shifted_ranges(shifted, window)

    assert repaired is not None
    assert service._coverage_error(repaired, window) is None
    assert repaired.scenes[0].paragraph_start == 0
    assert repaired.scenes[0].paragraph_end == 2
    assert detail["index_shift"] == -1