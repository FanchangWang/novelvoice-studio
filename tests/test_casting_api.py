from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from novelvoice_studio.ai.registry import ProviderRegistry

from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import (
    BookBible,
    Character,
    CharacterArc,
    GenerationJob,
    ReviewItem,
    Scene,
    SceneCharacter,
    VoiceActor,
    VoiceCast,
)
from novelvoice_studio.services.casting import (
    CastingService,
    _Target,
    general_voice_presets,
    recover_interrupted_casting,
)
from novelvoice_studio.services.paths import new_id


def _import_sample(upload, sample_text: str, title: str = "选角测试") -> str:
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


def _wait_scene(client: TestClient, chapter_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/chapters/{chapter_id}/scenes/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if (job is None or job["status"] in {"completed", "failed", "cancelled"}) and (
            last["scene_status"] != "running"
        ):
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
        if (job is None or job["status"] in {"completed", "failed", "cancelled"}) and (
            last["script_status"] != "running"
        ):
            return last
        time.sleep(0.05)
    raise AssertionError(f"剧本任务未在 {timeout}s 内结束: {last}")


def _wait_casting(client: TestClient, book_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/books/{book_id}/casts/status")
        assert response.status_code == 200
        last = response.json()
        job = last.get("job")
        if (
            job is None or job["status"] in {"completed", "failed", "cancelled"}
        ) and not last.get("running"):
            return last
        time.sleep(0.05)
    raise AssertionError(f"自动选角任务未在 {timeout}s 内结束: {last}")


def _prepare_book(
    client: TestClient,
    upload,
    sample_text: str,
    *,
    title: str = "选角测试",
    with_scene_script: bool = True,
) -> tuple[str, dict | None]:
    book_id = _import_sample(upload, sample_text, title=title)
    response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert response.status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {"approved", "needs_review"}
    if not with_scene_script:
        return book_id, None
    chapters = _chapters(client, book_id)
    chapter = next(
        (item for item in chapters if "雨夜来客" in item["title"]),
        chapters[0],
    )
    assert client.post(f"/api/v1/chapters/{chapter['id']}/scenes").status_code == 202
    _wait_scene(client, chapter["id"])
    script = client.post(
        f"/api/v1/chapters/{chapter['id']}/script",
        json={"adaptation_level": "light"},
    )
    assert script.status_code == 202
    status = _wait_script(client, chapter["id"])
    assert status["job"]["status"] == "completed"
    return book_id, chapter


def _start_casting(client: TestClient, book_id: str):
    return client.post(f"/api/v1/books/{book_id}/casts/auto")


def _casts(client: TestClient, book_id: str) -> dict:
    response = client.get(f"/api/v1/books/{book_id}/casts")
    assert response.status_code == 200, response.text
    return response.json()


def test_voice_actor_lifecycle(client: TestClient) -> None:
    created = client.post(
        "/api/v1/voice-actors",
        json={
            "display_name": "测试女声",
            "description": "二十多岁女性，清亮、克制，略显疲惫",
            "gender_presentation": "female",
            "age_impression": "young_adult",
            "timbre_tags": ["清亮", "克制"],
            "personality_tags": ["冷静"],
            "seed": 123,
        },
    )
    assert created.status_code == 201, created.text
    actor = created.json()
    assert actor["id"].startswith("va_")
    assert actor["status"] == "ready"
    assert actor["source"] == "user"
    assert actor["is_mock"] is True
    assert actor["audio_url"].endswith("/audio")
    assert actor["usage"]["active_cast_count"] == 0

    fetched = client.get(f"/api/v1/voice-actors/{actor['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["display_name"] == "测试女声"

    updated = client.patch(
        f"/api/v1/voice-actors/{actor['id']}",
        json={"is_favorite": True, "timbre_tags": ["清亮", "偏低"]},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_favorite"] is True
    assert updated.json()["timbre_tags"] == ["清亮", "偏低"]

    listed = client.get("/api/v1/voice-actors", params={"favorite": True})
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == actor["id"]

    audio = client.get(f"/api/v1/voice-actors/{actor['id']}/audio")
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert audio.headers["cache-control"] == "no-store"

    preview = client.post(
        f"/api/v1/voice-actors/{actor['id']}/preview",
        json={"text": "这是全局配音员试听。", "emotion": "tender"},
    )
    assert preview.status_code == 200, preview.text
    assert preview.content[:4] == b"RIFF"
    assert preview.headers["x-nvs-is-mock"] == "true"

    archived = client.patch(
        f"/api/v1/voice-actors/{actor['id']}", json={"status": "archived"}
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    hidden = client.get("/api/v1/voice-actors")
    assert hidden.json()["total"] == 0


def test_auto_casting_assigns_units_and_reuses_narrator(
    client: TestClient, upload, sample_text: str, monkeypatch
) -> None:
    general = client.post(
        "/api/v1/voice-actors",
        json={
            "display_name": "通用旁白",
            "description": "成年中性声，平稳、清楚、自然的叙述音色",
            "gender_presentation": "neutral",
            "age_impression": "adult",
            "timbre_tags": ["旁白", "叙述", "平稳"],
        },
    )
    assert general.status_code == 201, general.text
    general_actor_id = general.json()["id"]
    book_id, chapter = _prepare_book(client, upload, sample_text, title="自动选角")
    assert chapter is not None

    gate = threading.Event()
    original_run = CastingService._run_job

    def blocked_run(self, *args, **kwargs):
        gate.wait(10)
        return original_run(self, *args, **kwargs)

    monkeypatch.setattr(CastingService, "_run_job", blocked_run)
    started = _start_casting(client, book_id)
    assert started.status_code == 202, started.text
    assert started.json()["job_type"] == "book_casting"
    conflict = _start_casting(client, book_id)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "casting_running"
    book = client.get(f"/api/v1/books/{book_id}").json()
    assert book["production_status"] == "casting"

    gate.set()
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    plan = _casts(client, book_id)
    assert plan["narrator"] is not None
    assert plan["bible_ready"] is True
    assert plan["missing_roles"] == []
    assert plan["cast_count"] >= 2
    assert plan["ready"] is True
    narrator_cast = plan["narrator"]
    narrator_actor = narrator_cast["actor"]
    assert narrator_cast["source"] == "auto"
    assert narrator_actor["id"] == general_actor_id
    assert narrator_actor["scope"] == "general"
    assert "通用声线优先" in narrator_cast["reason"]
    assert narrator_actor["is_mock"] is True
    assert client.get(narrator_actor["audio_url"]).status_code == 200

    character_casts = {
        item["character_id"]: item["base_cast"]
        for item in plan["characters"]
        if item["base_cast"] is not None
    }
    script = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    units = script["units"]
    tts_units = [
        unit
        for unit in units
        if unit["unit_type"] in {"narration", "dialogue", "inner_monologue"}
    ]
    assert tts_units
    for unit in tts_units:
        assert unit["voice_cast_id"]
    narrator_units = [unit for unit in tts_units if unit["speaker_type"] == "narrator"]
    assert narrator_units
    assert {unit["voice_cast_id"] for unit in narrator_units} == {narrator_cast["id"]}
    for unit in tts_units:
        if unit["speaker_type"] == "character" and unit["speaker_id"] in character_casts:
            assert unit["voice_cast_id"] == character_casts[unit["speaker_id"]]["id"]

    book = client.get(f"/api/v1/books/{book_id}").json()
    assert book["production_status"] == "ready"

    rerun = _start_casting(client, book_id)
    assert rerun.status_code == 202
    rerun_status = _wait_casting(client, book_id)
    assert rerun_status["job"]["status"] == "completed"
    rerun_plan = _casts(client, book_id)
    assert rerun_plan["cast_count"] == plan["cast_count"]
    assert rerun_status["job"]["result"]["generated"] == 0
    assert rerun_status["job"]["result"]["reused"] == 0
    assert rerun_status["job"]["total_items"] == 0

    # 另一本书没有场景/剧本时只需旁白；旁白声线应复用全局库中的同一配音员。
    second_id, _ = _prepare_book(
        client, upload, sample_text, title="复用旁白", with_scene_script=False
    )
    assert _start_casting(client, second_id).status_code == 202
    second_status = _wait_casting(client, second_id)
    assert second_status["job"]["status"] == "completed"
    second_plan = _casts(client, second_id)
    assert second_plan["narrator"] is not None
    assert second_plan["narrator"]["voice_actor_id"] == narrator_actor["id"]
    assert second_plan["cast_count"] >= 1


def test_manual_cast_replaces_active_cast_and_syncs_units(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id, chapter = _prepare_book(client, upload, sample_text, title="手动选角")
    assert chapter is not None
    assert _start_casting(client, book_id).status_code == 202
    assert _wait_casting(client, book_id)["job"]["status"] == "completed"
    plan = _casts(client, book_id)
    script = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    speaker = next(
        unit
        for unit in script["units"]
        if unit["speaker_type"] == "character" and unit["speaker_id"]
    )
    character_id = speaker["speaker_id"]
    old_cast = next(
        item["base_cast"]
        for item in plan["characters"]
        if item["character_id"] == character_id
    )
    assert old_cast is not None

    actor_response = client.post(
        "/api/v1/voice-actors",
        json={
            "display_name": "手动替换声线",
            "description": "中性、平稳、略带沙哑的叙述声线",
            "gender_presentation": "neutral",
            "age_impression": "adult",
            "timbre_tags": ["沙哑"],
        },
    )
    assert actor_response.status_code == 201, actor_response.text
    actor_id = actor_response.json()["id"]

    assigned = client.post(
        f"/api/v1/books/{book_id}/casts/assign",
        json={
            "scope": "character",
            "character_id": character_id,
            "voice_actor_id": actor_id,
            "reason": "测试手动替换",
        },
    )
    assert assigned.status_code == 200, assigned.text
    updated = assigned.json()
    new_cast = next(
        item["base_cast"]
        for item in updated["characters"]
        if item["character_id"] == character_id
    )
    assert new_cast["id"] != old_cast["id"]
    assert new_cast["voice_actor_id"] == actor_id
    assert new_cast["source"] == "manual"
    assert new_cast["actor"]["display_name"] == "手动替换声线"

    with session_factory() as session:
        old_row = session.get(VoiceCast, old_cast["id"])
        assert old_row is not None
        assert old_row.is_active is False
        active_count = int(
            session.scalar(
                select(func.count(VoiceCast.id)).where(
                    VoiceCast.book_id == book_id,
                    VoiceCast.character_id == character_id,
                    VoiceCast.scope == "character",
                    VoiceCast.is_active.is_(True),
                )
            )
            or 0
        )
        assert active_count == 1

    synced = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    character_units = [
        unit
        for unit in synced["units"]
        if unit["speaker_type"] == "character" and unit["speaker_id"] == character_id
    ]
    assert character_units
    assert {unit["voice_cast_id"] for unit in character_units} == {new_cast["id"]}


def test_stage_cast_created_from_character_arc(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="阶段选角")
    with session_factory() as session:
        bible = session.scalar(
            select(BookBible).where(
                BookBible.book_id == book_id, BookBible.is_active.is_(True)
            )
        )
        assert bible is not None
        speaker_id = session.scalar(
            select(Character.id)
            .join(CharacterArc, CharacterArc.character_id == Character.id)
            .where(Character.book_id == book_id)
            .limit(1)
        )
        if speaker_id is None:
            speaker_id = session.scalar(
                select(Character.id).where(Character.book_id == book_id).limit(1)
            )
        assert speaker_id is not None
        state = session.scalar(
            select(SceneCharacter).where(
                SceneCharacter.book_id == book_id,
                SceneCharacter.character_id == speaker_id,
            )
        )
        if state is None:
            scene = session.scalar(
                select(Scene).where(Scene.book_id == book_id).limit(1)
            )
            assert scene is not None
            state = SceneCharacter(
                id=new_id("scch"),
                book_id=book_id,
                scene_id=scene.id,
                character_id=speaker_id,
                state_phase=0,
                presence="present",
                age_stage="adult",
                state_tags_json=[],
                confidence=1.0,
                evidence_json=[],
                source="user",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            session.add(state)
        else:
            state.age_stage = "adult"
            state.updated_at = utc_now()
        session.add(
            CharacterArc(
                id=new_id("arc"),
                book_id=book_id,
                bible_id=bible.id,
                character_id=speaker_id,
                arc_index=99,
                label="童年",
                age_stage="child",
                traits_json=[],
                appearance_json={},
                voice_traits_json={"description": "童声，音调偏高"},
                confidence=0.9,
                source="user",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.add(
            CharacterArc(
                id=new_id("arc"),
                book_id=book_id,
                bible_id=bible.id,
                character_id=speaker_id,
                arc_index=100,
                label="成年",
                age_stage="adult",
                traits_json=[],
                appearance_json={},
                voice_traits_json={"description": "成年声线"},
                confidence=0.9,
                source="user",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()

    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    plan = _casts(client, book_id)
    item = next(
        row for row in plan["characters"] if row["character_id"] == speaker_id
    )
    assert item["base_cast"] is not None
    stages = {row["age_stage"]: row for row in item["stages"]}
    assert "child" in stages
    assert "adult" in stages
    assert len(stages) >= 2
    assert all(row["cast"] is not None for row in stages.values())
    assert stages["child"]["cast"]["actor"]["age_impression"] == "child"
    assert stages["adult"]["cast"]["actor"]["age_impression"] == "adult"


def test_unconfirmed_age_drift_uses_one_character_voice(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id, chapter = _prepare_book(client, upload, sample_text, title="连续时间选角")
    assert chapter is not None
    with session_factory() as session:
        bible = session.scalar(
            select(BookBible).where(
                BookBible.book_id == book_id, BookBible.is_active.is_(True)
            )
        )
        assert bible is not None
        character = session.scalar(
            select(Character)
            .join(CharacterArc, CharacterArc.character_id == Character.id)
            .where(Character.book_id == book_id)
            .limit(1)
        )
        assert character is not None
        canonical_age = character.age_range
        arcs = list(
            session.scalars(
                select(CharacterArc).where(
                    CharacterArc.bible_id == bible.id,
                    CharacterArc.character_id == character.id,
                )
            )
        )
        for arc in arcs:
            arc.source = "ai"
            arc.voice_traits_json = {
                "age_stage": arc.age_stage,
                "age_stage_explicit": False,
                "age_stage_basis": "canonical",
            }
        state = session.scalar(
            select(SceneCharacter).where(SceneCharacter.character_id == character.id)
        )
        if state is not None:
            state.age_stage = "adult" if canonical_age != "adult" else "elderly"
        session.add(
            CharacterArc(
                id=new_id("arc"),
                book_id=book_id,
                bible_id=bible.id,
                character_id=character.id,
                arc_index=100,
                label="事件发起",
                age_stage="adult" if canonical_age != "adult" else "elderly",
                traits_json=[],
                appearance_json={},
                voice_traits_json={
                    "age_stage_explicit": False,
                    "age_stage_basis": "canonical",
                },
                confidence=0.9,
                source="ai",
                created_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        session.commit()

    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    plan = _casts(client, book_id)
    item = next(
        row for row in plan["characters"] if row["character_id"] == character.id
    )
    assert {stage["age_stage"] for stage in item["stages"]} <= {canonical_age}
    assert item["base_cast"] is not None
    synced = client.get(f"/api/v1/chapters/{chapter['id']}/script").json()
    character_units = [
        unit
        for unit in synced["units"]
        if unit["speaker_type"] == "character" and unit["speaker_id"] == character.id
    ]
    assert character_units
    assert {unit["voice_cast_id"] for unit in character_units} == {item["base_cast"]["id"]}


def test_auto_resume_detects_unconfirmed_stage_cast(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id, _chapter = _prepare_book(
        client, upload, sample_text, title="伪阶段续作检测", with_scene_script=False
    )
    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    with session_factory() as session:
        base = session.scalar(
            select(VoiceCast).where(
                VoiceCast.book_id == book_id,
                VoiceCast.scope == "character",
                VoiceCast.is_active.is_(True),
            )
        )
        assert base is not None and base.character_id is not None
        character = session.get(Character, base.character_id)
        assert character is not None
        invalid_age = "adult" if character.age_range != "adult" else "elderly"
        session.add(VoiceCast(
            id=new_id("cast"),
            book_id=book_id,
            character_id=character.id,
            voice_actor_id=base.voice_actor_id,
            scope="character_stage",
            age_stage_condition=invalid_age,
            source="auto",
            confidence=0.9,
            is_active=True,
            reason="测试：无证据的旧阶段声线",
            created_at=utc_now(),
            updated_at=utc_now(),
        ))
        session.commit()

    service = CastingService(
        client.app.state.settings, session_factory, client.app.state.registry
    )
    assert service.has_incompatible_auto_casts(book_id) is True


def test_casting_generation_failure_enters_inbox(
    client: TestClient, upload, sample_text: str, monkeypatch
) -> None:
    book_id, _ = _prepare_book(
        client, upload, sample_text, title="选角失败", with_scene_script=False
    )
    original = client.app.state.registry.voice_design.design_voice

    def broken_design(*args, **kwargs):
        raise RuntimeError("声音设计服务不可用（测试）")

    monkeypatch.setattr(client.app.state.registry.voice_design, "design_voice", broken_design)
    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    monkeypatch.setattr(client.app.state.registry.voice_design, "design_voice", original)
    assert status["job"]["status"] == "failed"
    assert status["production_status"] == "failed"
    book = client.get(f"/api/v1/books/{book_id}").json()
    assert book["production_status"] == "failed"
    with client.app.state.session_factory() as session:
        codes = set(
            session.scalars(
                select(ReviewItem.code).where(
                    ReviewItem.book_id == book_id, ReviewItem.status == "open"
                )
            )
        )
    assert "cast_job_failed" in codes
    assert "cast_narrator_unfilled" in codes


def test_recover_interrupted_casting(client: TestClient, upload, sample_text: str, session_factory) -> None:
    book_id, _ = _prepare_book(
        client, upload, sample_text, title="选角恢复", with_scene_script=False
    )
    with session_factory() as session:
        job = GenerationJob(
            id=new_id("job"),
            book_id=book_id,
            target_id=book_id,
            target_type="book",
            stage="casting",
            job_type="book_casting",
            status="running",
            total_items=1,
            completed_items=0,
            failed_items=0,
            progress=0.5,
            attempt_count=1,
            max_attempts=1,
            params_json={"targets": []},
            result_json={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        session.add(job)
        session.commit()
        job_id = job.id

    with session_factory() as session:
        recovered = recover_interrupted_casting(session)
        session.commit()
    assert recovered == {"jobs": 1, "books": 1}
    with session_factory() as session:
        job = session.get(GenerationJob, job_id)
        assert job is not None
        assert job.status == "failed"
        assert job.error_code == "interrupted"
        book = client.get(f"/api/v1/books/{book_id}").json()
        assert book["production_status"] == "failed"
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.book_id == book_id,
                ReviewItem.code == "cast_job_failed",
                ReviewItem.status == "open",
            )
        )
        assert review is not None


def test_general_voice_presets_are_complete_and_idempotent(client: TestClient) -> None:
    catalog = client.get("/api/v1/voice-actors/general-presets")
    assert catalog.status_code == 200, catalog.text
    result = catalog.json()
    assert result["total"] == 36
    assert len({item["preset_id"] for item in result["items"]}) == 36
    assert {item["age_impression"] for item in result["items"]} == {
        "child", "teenager", "young_adult", "adult", "middle_aged", "elderly"
    }
    assert sum(item["gender_presentation"] == "male" for item in result["items"]) == 18
    assert result["ready_count"] == 0

    preset_id = result["items"][0]["preset_id"]
    generated = client.post(f"/api/v1/voice-actors/general-presets/{preset_id}/generate")
    assert generated.status_code == 201, generated.text
    actor = generated.json()
    assert actor["scope"] == "general"
    assert actor["source_book_id"] is None
    assert actor["generation_params"]["general_preset_id"] == preset_id
    repeated = client.post(f"/api/v1/voice-actors/general-presets/{preset_id}/generate")
    assert repeated.status_code == 201
    assert repeated.json()["id"] == actor["id"]
    refreshed = client.get("/api/v1/voice-actors/general-presets").json()
    assert refreshed["ready_count"] == 1
    assert refreshed["items"][0]["actor_id"] == actor["id"]


def test_empty_library_auto_casting_materializes_general_presets(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="空库通用选角")
    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status

    with session_factory() as session:
        actors = list(session.scalars(select(VoiceActor)))
        assert actors
        assert all(actor.scope == "general" for actor in actors)
        assert all((actor.generation_params_json or {}).get("general_preset_id") for actor in actors)
        assert all(actor.source_book_id is None for actor in actors)
    plan = _casts(client, book_id)
    assert plan["narrator"]["actor"]["scope"] == "general"
    assert all(
        row["base_cast"]["actor"]["scope"] == "general"
        for row in plan["characters"]
        if row["base_cast"] is not None
    )


def test_ready_general_catalog_is_reused_without_voice_design(
    client: TestClient, upload, sample_text: str, monkeypatch
) -> None:
    catalog = client.get("/api/v1/voice-actors/general-presets").json()["items"]
    for preset in catalog:
        response = client.post(f"/api/v1/voice-actors/general-presets/{preset['preset_id']}/generate")
        assert response.status_code == 201, response.text

    def unexpected_design(self, *args, **kwargs):
        raise AssertionError("通用声线已就绪，自动选角不应再次调用声音设计")

    monkeypatch.setattr(CastingService, "_design_voice", unexpected_design)
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="通用声线复用")
    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    assert status["job"]["result"]["generated"] == 0
    assert status["job"]["result"]["reused"] > 0
    assert _casts(client, book_id)["narrator"]["actor"]["scope"] == "general"


def test_rerun_casting_migrates_old_auto_book_voices_to_general(
    client: TestClient, upload, sample_text: str, monkeypatch, session_factory
) -> None:
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="旧专属声线迁移")
    monkeypatch.setattr("novelvoice_studio.services.casting.general_voice_presets", lambda: [])
    assert _start_casting(client, book_id).status_code == 202
    first = _wait_casting(client, book_id)
    assert first["job"]["status"] == "completed", first
    with session_factory() as session:
        old_actor_ids = {
            row.voice_actor_id
            for row in session.scalars(
                select(VoiceCast).where(
                    VoiceCast.book_id == book_id,
                    VoiceCast.is_active.is_(True),
                )
            )
        }
        old_actors = list(session.scalars(select(VoiceActor).where(VoiceActor.id.in_(old_actor_ids))))
        assert old_actors
        assert all(actor.scope == "book" for actor in old_actors)

    monkeypatch.setattr("novelvoice_studio.services.casting.general_voice_presets", general_voice_presets)
    catalog = client.get("/api/v1/voice-actors/general-presets").json()["items"]
    for preset in catalog:
        generated = client.post(f"/api/v1/voice-actors/general-presets/{preset['preset_id']}/generate")
        assert generated.status_code == 201, generated.text
    assert _start_casting(client, book_id).status_code == 202
    second = _wait_casting(client, book_id)
    assert second["job"]["status"] == "completed", second
    assert second["job"]["result"]["generated"] == 0
    assert second["job"]["result"]["reused"] > 0
    plan = _casts(client, book_id)
    assert plan["narrator"]["actor"]["scope"] == "general"
    assert all(
        row["base_cast"]["actor"]["scope"] == "general"
        for row in plan["characters"]
        if row["base_cast"] is not None
    )
    with session_factory() as session:
        active_actor_ids = {
            row.voice_actor_id
            for row in session.scalars(
                select(VoiceCast).where(
                    VoiceCast.book_id == book_id,
                    VoiceCast.is_active.is_(True),
                )
            )
        }
        active_actors = list(session.scalars(select(VoiceActor).where(VoiceActor.id.in_(active_actor_ids))))
        assert active_actor_ids.isdisjoint(old_actor_ids)
        assert all(actor.scope == "general" for actor in active_actors)
        retained_actors = list(session.scalars(select(VoiceActor).where(VoiceActor.id.in_(old_actor_ids))))
        historical_casts = list(
            session.scalars(select(VoiceCast).where(VoiceCast.voice_actor_id.in_(old_actor_ids)))
        )
        assert len(retained_actors) == len(old_actor_ids)
        assert historical_casts
        assert all(not cast.is_active for cast in historical_casts)


def test_detects_incompatible_existing_auto_cast(app, db_session, upload, sample_text, monkeypatch) -> None:
    book_id = _import_sample(upload, sample_text)
    service = CastingService(app.state.settings, app.state.session_factory, ProviderRegistry(app.state.settings))
    target = _Target(scope="character", description="成年男性", gender="male", age="adult")
    actor = VoiceActor(
        id="va_0000000000000001", display_name="少年男", description="少年男声",
        gender_presentation="male", age_impression="teenager", language="zh-CN",
        timbre_tags_json=[], personality_tags_json=[],
    )
    cast = SimpleNamespace(source="auto", voice_actor_id=actor.id)
    db_session.add(actor)
    db_session.commit()
    monkeypatch.setattr(service, "_active_bible", lambda *_args: SimpleNamespace(status="approved"))
    monkeypatch.setattr(service, "_targets", lambda *_args: [target])
    monkeypatch.setattr(service, "_cast_for_target", lambda *_args: cast)

    assert service.has_incompatible_auto_casts(book_id) is True
    cast.source = "user"
    assert service.has_incompatible_auto_casts(book_id) is False


def test_actor_matching_rejects_teen_voice_for_adult_and_uses_timbre() -> None:
    service = object.__new__(CastingService)
    target = _Target(scope="character", description="低沉威严的成年男性", gender="male", age="adult")
    teen = VoiceActor(
        id="va_0000000000000001", display_name="少年男", description="少年男声，低柔",
        gender_presentation="male", age_impression="teenager", language="zh-CN",
        timbre_tags_json=["低沉"], personality_tags_json=[],
    )
    adult = VoiceActor(
        id="va_0000000000000002", display_name="成年男", description="成年男声，低沉威严",
        gender_presentation="male", age_impression="adult", language="zh-CN",
        timbre_tags_json=["低沉", "威严"], personality_tags_json=[],
    )
    plain = VoiceActor(
        id="va_0000000000000003", display_name="普通成年男", description="成年男声",
        gender_presentation="male", age_impression="adult", language="zh-CN",
        timbre_tags_json=[], personality_tags_json=[],
    )

    assert service._score_actor(target, teen) is None
    assert service._hard_voice_mismatch(target, teen) is True
    assert service._score_actor(target, adult) > service._score_actor(target, plain)


def test_rerun_casting_preserves_manual_book_voice(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="手动声线保留")
    created = client.post(
        "/api/v1/voice-actors",
        json={
            "display_name": "用户指定专属声线",
            "description": "用户手动指定的低沉沙哑男声",
            "gender_presentation": "male",
            "age_impression": "adult",
            "source_book_id": book_id,
        },
    )
    assert created.status_code == 201, created.text
    actor = created.json()
    character_id = _casts(client, book_id)["characters"][0]["character_id"]
    assigned = client.post(
        f"/api/v1/books/{book_id}/casts/assign",
        json={
            "scope": "character",
            "character_id": character_id,
            "voice_actor_id": actor["id"],
        },
    )
    assert assigned.status_code == 200, assigned.text

    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    plan = _casts(client, book_id)
    assert plan["narrator"]["actor"]["scope"] == "general"
    selected = next(row for row in plan["characters"] if row["character_id"] == character_id)
    assert selected["base_cast"]["voice_actor_id"] == actor["id"]
    assert selected["base_cast"]["actor"]["scope"] == "book"
    assert selected["base_cast"]["source"] == "manual"


def test_auto_casting_falls_back_to_book_voice_without_compatible_preset(
    client: TestClient, upload, sample_text: str, monkeypatch, session_factory
) -> None:
    book_id, _chapter = _prepare_book(client, upload, sample_text, title="专属声线回退")
    monkeypatch.setattr("novelvoice_studio.services.casting.general_voice_presets", lambda: [])
    assert _start_casting(client, book_id).status_code == 202
    status = _wait_casting(client, book_id)
    assert status["job"]["status"] == "completed", status
    with session_factory() as session:
        actors = list(session.scalars(select(VoiceActor)))
        assert actors
        assert all(actor.scope == "book" and actor.source_book_id == book_id for actor in actors)


def test_book_voice_cannot_be_assigned_to_another_book(
    client: TestClient, upload, sample_text: str
) -> None:
    first, _ = _prepare_book(client, upload, sample_text, title="专属声线甲", with_scene_script=False)
    second, _ = _prepare_book(client, upload, sample_text, title="专属声线乙", with_scene_script=False)
    created = client.post(
        "/api/v1/voice-actors",
        json={
            "display_name": "甲书专属声线",
            "description": "特殊角色使用的低沉沙哑男声",
            "gender_presentation": "male",
            "age_impression": "adult",
            "source_book_id": first,
        },
    )
    assert created.status_code == 201, created.text
    actor = created.json()
    assert actor["scope"] == "book"
    character_id = _casts(client, second)["characters"][0]["character_id"]
    forbidden = client.post(
        f"/api/v1/books/{second}/casts/assign",
        json={"scope": "character", "character_id": character_id, "voice_actor_id": actor["id"]},
    )
    assert forbidden.status_code == 409
    assert forbidden.json()["error"]["code"] == "actor_book_scope_violation"
    visible = client.get("/api/v1/voice-actors", params={"book_id": second, "limit": 200}).json()
    assert actor["id"] not in {item["id"] for item in visible["items"]}
    allowed = client.post(
        f"/api/v1/books/{first}/casts/assign",
        json={
            "scope": "character",
            "character_id": _casts(client, first)["characters"][0]["character_id"],
            "voice_actor_id": actor["id"],
        },
    )
    assert allowed.status_code == 200, allowed.text
