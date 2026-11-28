from __future__ import annotations

import threading
import time
from types import MethodType

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from novelvoice_studio.ai.base import (
    ChapterScriptResult,
    ScriptUnitDraft,
)
from novelvoice_studio.models import (
    AudioAsset,
    Chapter,
    Character,
    GenerationJob,
    ReviewItem,
    ScriptUnit,
    ScriptVersion,
)
from novelvoice_studio.services.paths import new_id
from novelvoice_studio.services.scripts import (
    ScriptService,
    _UnitPlan,
    _find_source_span,
    _is_direct_quote,
    _quote_spans,
    _source_match_index,
    _split_window,
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
    chapter = next(
        item for item in _chapters(client, book_id) if item["analysis_included"]
    )
    response = _start_script(client, chapter["id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "scenes_required"

    # 自动流程结束后，失败章节可从分场开始串联重试，不需要先回旧的分场页。
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    _wait_bible(client, book_id)
    retry = client.post(
        f"/api/v1/chapters/{chapter['id']}/script",
        json={"adaptation_level": "light", "retry_scenes": True},
    )
    assert retry.status_code == 202, retry.text
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    assert status["script_version"] == 1
    assert client.get(f"/api/v1/chapters/{chapter['id']}/script").json()["units"]


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


def test_source_span_matching_is_conservative_and_keeps_original_offsets():
    source = '第一章 雨夜\n\n“你终于来了。”老周说。\n他慢慢放下手中那封已经被门外雨水彻底浸透的信，直到指尖发白。'
    index = _source_match_index(source)
    quote_start = source.index("“")
    quote_end = source.index("老周说。") + len("老周说。")

    # 模型把弯引号改成直引号并去掉换行时，仍映射回原文字符范围。
    span = _find_source_span('"你终于来了。"老周说。', source, 0, index)
    assert span == (quote_start, quote_end)
    assert source[span[0] : span[1]] == '“你终于来了。”老周说。'

    # 标题和正文之间的空行差异不会改变原文映射。
    titled = "## 第一章 雨夜\n\n他慢慢放下手中那封已经被门外雨水彻底浸透的信，直到指尖发白。"
    span = _find_source_span(titled.replace("\n\n", "\n"), titled, 0)
    assert span == (0, len(titled))

    # 长引文漏一个字可以定位，但数据库仍保存原文的“雨水”。
    sentence_start = source.index("他慢慢")
    sentence = "他慢慢放下手中那封已经被门外雨水彻底浸透的信，直到指尖发白。"
    span = _find_source_span(sentence.replace("雨水", "水"), source, quote_end, index)
    assert span == (sentence_start, len(source))
    assert source[span[0] : span[1]] == sentence

    # 短文本的错字、多处改写以及乱序都不能被容错规则接受。
    assert _find_source_span("你终于来老周说。", source, 0, index) is None
    assert _find_source_span("他慢慢放下那封被浸透的信。", source, 0, index) is None
    assert _find_source_span("老周说。", source, quote_end, index) is None

    # 同一处长引文出现两次时不猜测位置。
    repeated = "abcdefghijklmnopqrstuvwxYzabcdefghijklmnopqrstuvwxXz"
    assert _find_source_span("abcdefghijklmnopqrstuvwxyz", repeated, 0) is None


def test_script_normalizes_model_quotes_without_rewriting_source(
    client: TestClient, upload, app
):
    custom = (
        "第一章 雨夜\n\n"
        "“你终于来了。”老周说。\n"
        "他慢慢放下手中那封已经被门外雨水彻底浸透的信，直到指尖发白。\n"
    )
    _book_id, chapter = _prepare_chapter(client, upload, custom, title="引号对齐")

    def fake_adapt(self, scene_text, known_characters, context=None):
        if "第一章" in scene_text and "你终于" not in scene_text:
            units = [ScriptUnitDraft(
                unit_type="narration", source_text=scene_text.strip(),
                text=scene_text.strip(), speaker_type="narrator", confidence=0.9,
            )]
        elif "你终于" in scene_text:
            units = [ScriptUnitDraft(
                unit_type="dialogue", source_text='"你终于来了。"老周说。',
                text="你终于来了。", speaker_name="老周", confidence=0.9,
            )]
        else:
            units = [ScriptUnitDraft(
                unit_type="narration",
                source_text="他慢慢放下手中那封已经被门外水彻底浸透的信，直到指尖发白。",
                text="他慢慢放下手中那封已经被门外水彻底浸透的信，直到指尖发白。",
                speaker_type="narrator", confidence=0.9,
            )]
        return ChapterScriptResult(units=units, meta={"mock": True})

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed", status
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    _assert_source_ordered(state["units"], custom, chapter["source_start_offset"])
    narration = next(
        unit for unit in state["units"] if unit["source_text"] == "他慢慢放下手中那封已经被门外雨水彻底浸透的信，直到指尖发白。"
    )
    assert narration["source_start_offset"] == chapter["source_start_offset"] + custom.index("他慢慢")
    assert narration["source_end_offset"] == chapter["source_start_offset"] + custom.index("他慢慢") + len(narration["source_text"])


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
    assert status["job"]["status"] == "failed"
    assert "原文对齐率不足" in status["job"]["error_message"]
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    assert state["units"] == []
    assert state["script_version"] == 0
    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.code == "script_analysis_failed",
                ReviewItem.status == "open",
            )
        )
        assert review is not None


def test_script_unresolved_speaker_review(
    client: TestClient, upload, sample_text: str, app, session_factory
) -> None:
    book_id, chapter = _prepare_chapter(
        client, upload, "第一章 未知说话人\n\n“谁在说话？”\n", title="说话人异常"
    )

    def fake_adapt(self, scene_text, known_characters, context=None):
        source = scene_text.strip()
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="dialogue",
                    source_text=source,
                    text=source,
                    speaker_name="不存在的人",
                    confidence=0.3,
                )
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["script_status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    dialogue = next(unit for unit in state["units"] if unit["unit_type"] == "dialogue")
    assert dialogue["speaker_type"] == "unknown"
    assert dialogue["review_status"] == "auto"
    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.code == "script_unresolved_speaker",
                ReviewItem.status == "open",
            )
        )
        assert review is not None
        assert review.severity == "warning"
    inbox = client.get(
        f"/api/v1/reviews?book_id={book_id}&code=script_unresolved_speaker"
    ).json()
    assert inbox["total"] == 1
    located = inbox["items"][0]
    assert located["actionable"] is True
    assert located["action_type"] == "script_unit"
    assert located["location"]["unit_id"] == dialogue["id"]
    assert located["location"]["unit_index"] == dialogue["unit_index"]
    assert located["location"]["quote"] == dialogue["source_text"]
    assert located["impact"] and located["recommended_action"]


def test_script_resolves_local_narrative_and_role_prefix_speakers(
    client: TestClient, upload, app, session_factory
) -> None:
    sample = (
        "第一章 本地说话人\n\n"
        "苏晓走过去。\n"
        "“你来了？”\n"
        "陆远：“嗯，走吧。”\n"
        "民警老周笑着说：“等等。”\n"
    )
    book_id, chapter = _prepare_chapter(client, upload, sample, title="本地说话人")
    with session_factory() as session:
        session.execute(delete(Character).where(Character.book_id == book_id))
        for name in ("苏晓", "陆远", "民警老周"):
            session.add(
                Character(
                    id=new_id("char"),
                    book_id=book_id,
                    canonical_name=name,
                    aliases_json=[],
                    gender="unknown",
                    role_tier="major",
                    source="user",
                    is_provisional=False,
                )
            )
        session.commit()

    def fake_adapt(self, scene_text, known_characters, context=None):
        source = scene_text.strip()
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="narration",
                    source_text=source,
                    text=source,
                    speaker_type="narrator",
                    confidence=0.2,
                )
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    dialogue = [unit for unit in state["units"] if unit["unit_type"] == "dialogue"]
    assert [(unit["text"], unit["speaker_name"]) for unit in dialogue] == [
        ("你来了？", "苏晓"),
        ("嗯，走吧。", "陆远"),
        ("等等。", "民警老周"),
    ]
    assert all(unit["speaker_type"] == "character" for unit in dialogue)


def test_script_resolves_gaze_targets_as_listeners_not_speakers(
    client: TestClient, upload, app, session_factory
) -> None:
    sample = (
        "第一章 看向与转向\n\n"
        "周老先生转向陆远：“谢谢你，小伙子。”\n"
        "陆远摇摇头：“不客气。”\n"
        "民警老周拿出手机，说明情况。等待的时候，他望向陆远：“你找谁？”\n"
    )
    book_id, chapter = _prepare_chapter(client, upload, sample, title="看向与转向")
    with session_factory() as session:
        session.execute(delete(Character).where(Character.book_id == book_id))
        for name in ("周老先生（已故）", "陆远", "民警老周"):
            session.add(
                Character(
                    id=new_id("char"),
                    book_id=book_id,
                    canonical_name=name,
                    aliases_json=[],
                    gender="unknown",
                    role_tier="major",
                    source="user",
                    is_provisional=False,
                )
            )
        session.commit()

    def fake_adapt(self, scene_text, known_characters, context=None):
        source = scene_text.strip()
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="narration",
                    source_text=source,
                    text=source,
                    speaker_type="narrator",
                    confidence=0.2,
                )
            ],
            meta={"mock": True},
        )

    app.state.registry.llm.adapt_scene_script = MethodType(fake_adapt, app.state.registry.llm)
    assert _start_script(client, chapter["id"]).status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    dialogue = [unit for unit in state["units"] if unit["unit_type"] == "dialogue"]
    assert [(unit["text"], unit["speaker_name"]) for unit in dialogue] == [
        ("谢谢你，小伙子。", "周老先生（已故）"),
        ("不客气。", "陆远"),
        ("你找谁？", "民警老周"),
    ]
    assert all(unit["speaker_type"] == "character" for unit in dialogue)


def test_script_resolves_post_speaker_and_context_alias(
    client: TestClient, upload, app, session_factory
) -> None:
    sample = (
        "第一章 瑶姐\n\n"
        "“在干吗呢？欺负小姑娘？”一道凌厉的女声停止了他们的动作。\n"
        "那些人的动作戛然而止，紧接着他们一个个喊那个突然出现的女生“瑶姐”。\n"
    )
    book_id, chapter = _prepare_chapter(client, upload, sample, title="后置说话人")
    with session_factory() as session:
        session.add(
            Character(
                id=new_id("char"),
                book_id=book_id,
                canonical_name="时瑶",
                aliases_json=[],
                gender="female",
                role_tier="protagonist",
                source="user",
                is_provisional=False,
            )
        )
        session.commit()
    registry = app.state.registry
    original = registry.llm.adapt_scene_script

    def fake(self, scene_text, known_characters, context=None):
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="narration",
                    source_text=scene_text.strip(),
                    text=scene_text.strip(),
                    speaker_type="narrator",
                    confidence=0.2,
                )
            ],
            meta={"mock": True},
        )

    registry.llm.adapt_scene_script = MethodType(fake, registry.llm)
    try:
        response = _start_script(client, chapter["id"])
        assert response.status_code == 202
        status = _wait_script(client, chapter["id"])
    finally:
        registry.llm.adapt_scene_script = original

    assert status["job"]["status"] == "completed"
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    units = state["units"]
    dialogue = next(unit for unit in units if unit["unit_type"] == "dialogue")
    assert dialogue["text"] == "在干吗呢？欺负小姑娘？"
    assert dialogue["speaker_name"] == "时瑶"
    assert dialogue["speaker_type"] == "character"
    assert dialogue["confidence"] >= 0.85
    assert not any(unit["text"] == "瑶姐" for unit in units)
    assert any("喊那个突然出现的女生" in unit["text"] for unit in units if unit["unit_type"] == "narration")
    _assert_source_ordered(units, sample, chapter["source_start_offset"])


def test_script_front_back_speaker_and_narrative_quotes(
    client: TestClient, upload, app, session_factory
) -> None:
    sample = (
        "第一章 放开我\n\n"
        "“放开我，”时瑶狠着心对他冷冷一道，“沈同学，我们先放放。”\n"
        "好想再听听她喊自己‘婉婉’啊。\n"
    )
    book_id, chapter = _prepare_chapter(client, upload, sample, title="说话人补全")
    with session_factory() as session:
        for name in ("时瑶", "沈和霖"):
            session.add(
                Character(
                    id=new_id("char"),
                    book_id=book_id,
                    canonical_name=name,
                    aliases_json=[],
                    role_tier="major",
                    source="user",
                    is_provisional=False,
                )
            )
        session.commit()
    registry = app.state.registry
    original = registry.llm.adapt_scene_script

    def fake(self, scene_text, known_characters, context=None):
        return ChapterScriptResult(
            units=[
                ScriptUnitDraft(
                    unit_type="dialogue",
                    source_text=scene_text.strip(),
                    text=scene_text.strip(),
                    speaker_name="不存在的人",
                    emotion="angry",
                    delivery="whispering",
                    intensity=3,
                    confidence=0.9,
                )
            ],
            meta={"mock": True},
        )

    registry.llm.adapt_scene_script = MethodType(fake, registry.llm)
    try:
        response = _start_script(client, chapter["id"])
        assert response.status_code == 202
        status = _wait_script(client, chapter["id"])
    finally:
        registry.llm.adapt_scene_script = original

    assert status["job"]["status"] == "completed"
    units = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()["units"]
    dialogues = [
        unit
        for unit in units
        if unit["unit_type"] == "dialogue" and unit["source_text"].startswith("“")
    ]
    assert [unit["text"] for unit in dialogues] == ["放开我，", "沈同学，我们先放放。"]
    assert all(unit["speaker_name"] == "时瑶" for unit in dialogues)
    assert all(unit["emotion"] == "angry" and unit["delivery"] == "whispering" for unit in dialogues)
    assert not any(unit["text"] == "婉婉" for unit in units)
    assert any("喊自己‘婉婉’" in unit["text"] for unit in units if unit["unit_type"] == "narration")
    _assert_source_ordered(units, sample, chapter["source_start_offset"])


def test_script_unquoted_dialogue_requires_explicit_speaker_action() -> None:
    service = ScriptService.__new__(ScriptService)
    character = Character(
        id=new_id("char"),
        book_id="book_1",
        canonical_name="时瑶",
        aliases_json=[],
    )
    other = Character(
        id=new_id("char"),
        book_id="book_1",
        canonical_name="阿棠",
        aliases_json=[],
    )
    canonical, aliases = service._character_index([character, other])
    assert service._unquoted_dialogue("”阿棠举着照片问。", canonical, aliases) is None
    suffix = service._unquoted_dialogue("你吃了吗，时瑶问。", canonical, aliases)
    assert suffix is not None
    assert suffix[0].id == character.id
    assert suffix[1] == "你吃了吗"
    prefix = service._unquoted_dialogue("时瑶轻声问：你吃了吗？", canonical, aliases)
    assert prefix is not None
    assert prefix[1] == "你吃了吗？"
    assert service._unquoted_dialogue("时瑶站在窗边看着雨。", canonical, aliases) is None
    first = _UnitPlan(
        unit_type="dialogue",
        source_text="放开我，",
        text="放开我，",
        display_text="放开我，",
        source_start_offset=0,
        source_end_offset=4,
        scene_id="scene_1",
        speaker_id=character.id,
        emotion="angry",
        delivery="shouting",
        intensity=3,
    )
    tag = _UnitPlan(
        unit_type="narration",
        source_text="时瑶狠着心说，",
        text="时瑶狠着心说，",
        display_text="时瑶狠着心说，",
        source_start_offset=4,
        source_end_offset=11,
        scene_id="scene_1",
        speaker_type="narrator",
    )
    second = _UnitPlan(
        unit_type="dialogue",
        source_text="我们先走。",
        text="我们先走。",
        display_text="我们先走。",
        source_start_offset=11,
        source_end_offset=16,
        scene_id="scene_1",
        speaker_id=character.id,
    )
    service._inherit_performance([first, tag, second])
    assert second.emotion == "angry"
    assert second.delivery == "shouting"
    assert second.intensity == 3


def test_speaker_fix_is_current_book_only_and_stales_audio(
    client: TestClient, upload, sample_text: str, app, session_factory
) -> None:
    book_id, chapter = _prepare_chapter(client, upload, sample_text, title="修正说话人")
    assert _start_script(client, chapter["id"]).status_code == 202
    _wait_script(client, chapter["id"])
    first_state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    old_unit_id = first_state["units"][0]["id"]
    assert _start_script(client, chapter["id"]).status_code == 202
    _wait_script(client, chapter["id"])
    state = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    unit = state["units"][0]
    bible = client.get(f"/api/v1/books/{book_id}/bible").json()["bible"]
    character = bible["characters"][0]
    other_book = _import_sample(upload, sample_text, title="另一本书")
    with session_factory() as session:
        other_character = Character(
            id=new_id("char"), book_id=other_book, canonical_name="别书角色"
        )
        session.add(other_character)
        session.flush()
        other_character_id = other_character.id
        row = session.get(ScriptUnit, unit["id"])
        assert row is not None
        row.audio_status = "ready"
        row.render_hash = "old-input-hash"
        asset = AudioAsset(
            id=new_id("audio"), script_unit_id=row.id, version=1,
            file_path="audio/test.wav", sample_rate=24000, duration_ms=100,
        )
        session.add(asset)
        session.flush()
        row.current_audio_asset_id = asset.id
        session.commit()

    endpoint = f"/api/v1/script-units/{unit['id']}/speaker"
    cross_book = client.patch(endpoint, json={
        "speaker_type": "character", "speaker_id": other_character_id,
    })
    assert cross_book.status_code == 422
    historical = client.patch(f"/api/v1/script-units/{old_unit_id}/speaker", json={
        "speaker_type": "character", "speaker_id": character["id"],
    })
    assert historical.status_code == 409
    narrator_with_id = client.patch(endpoint, json={
        "speaker_type": "narrator", "speaker_id": character["id"],
    })
    assert narrator_with_id.status_code == 422

    fixed = client.patch(endpoint, json={
        "speaker_type": "character", "speaker_id": character["id"],
    })
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["speaker_type"] == "character"
    assert fixed.json()["speaker_name"] == character["canonical_name"]
    assert fixed.json()["audio_status"] == "stale"
    assert fixed.json()["current_audio_asset_id"] is not None
    assert {"speaker_type", "speaker_id"} <= set(fixed.json()["user_locked_fields"])
    current = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    for pending in current["units"]:
        if pending["unit_type"] == "dialogue" and pending["speaker_type"] != "character":
            confirmed = client.patch(
                f"/api/v1/script-units/{pending['id']}/speaker",
                json={"speaker_type": "character", "speaker_id": character["id"]},
            )
            assert confirmed.status_code == 200, confirmed.text
    inbox = client.get(
        f"/api/v1/reviews?book_id={book_id}&code=script_unresolved_speaker"
    ).json()
    assert inbox["total"] == 0


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


def test_ship_name_quotes_are_not_treated_as_dialogue() -> None:
    text = "飞船名叫“自然选择号”，缓缓驶离港口。"
    spans = _quote_spans(text)

    assert len(spans) == 1
    _start, _end, _raw, inner = spans[0]
    assert _is_direct_quote(text, _start, _end, inner) is False


def test_multiple_quotes_do_not_reuse_one_model_speaker() -> None:
    service = ScriptService.__new__(ScriptService)
    shi = Character(
        id=new_id("char"), book_id="book_1", canonical_name="时瑶",
        aliases_json=[], role_tier="protagonist", source="user", is_provisional=False,
    )
    shen = Character(
        id=new_id("char"), book_id="book_1", canonical_name="沈和霖",
        aliases_json=[], role_tier="supporting", source="user", is_provisional=False,
    )
    canonical, aliases = service._character_index([shi, shen])
    source = "时瑶说：“你先走。”沈和霖说：“等等我。”"
    draft = ScriptUnitDraft(
        unit_type="narration", source_text=source, text=source,
        speaker_name="时瑶", confidence=0.9,
    )

    units = service._expand_source_draft(draft, source, "", canonical, aliases)
    dialogues = [unit for unit in units if unit.unit_type == "dialogue"]

    assert [(unit.text, unit.speaker_name) for unit in dialogues] == [
        ("你先走。", "时瑶"),
        ("等等我。", "沈和霖"),
    ]


def test_script_chunking_never_splits_inside_dialogue_quotes() -> None:
    text = "前文。“这是一句超过分块长度但必须完整保留的对白。”后文。"
    chunks = _split_window(text, 0, 12)

    assert len(chunks) == 2
    assert ''.join(chunk[2] for chunk in chunks) == text
    assert chunks[1][2].startswith('“')
    assert chunks[1][2].endswith('后文。')