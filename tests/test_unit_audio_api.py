from __future__ import annotations

import time
from types import MethodType

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from novelvoice_studio.ai.base import ChapterScriptResult, ScriptUnitDraft
from novelvoice_studio.models import (
    ReviewItem,
    ScriptUnit,
    VoiceActor,
    VoiceCast,
)

TTS_TYPES = {"narration", "dialogue", "inner_monologue"}


def _import_sample(upload, sample_text: str, title: str = "单元音频测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201, response.text
    return response.json()["book"]["id"]


def _wait(
    client: TestClient, path: str, field: str, timeout: float = 30.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        last = response.json()
        job = last.get("job")
        if (
            job is None
            or job["status"] in {"completed", "failed", "cancelled"}
        ) and last.get(field) != "running":
            return last
        time.sleep(0.05)
    raise AssertionError(f"{path} 未结束: {last}")


def _prepare(
    client: TestClient,
    upload,
    sample_text: str,
    *,
    with_casting: bool = True,
    title: str = "单元音频测试",
) -> tuple[str, str]:
    book_id = _import_sample(upload, sample_text, title=title)
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    bible = _wait(client, f"/api/v1/books/{book_id}/bible/status", "bible_status")
    assert bible["bible_status"] in {"approved", "needs_review"}

    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    chapter = next(
        (item for item in chapters if "雨夜来客" in item["title"]), chapters[0]
    )
    assert client.post(f"/api/v1/chapters/{chapter['id']}/scenes").status_code == 202
    assert (
        _wait(
            client,
            f"/api/v1/chapters/{chapter['id']}/scenes/status",
            "scene_status",
        )["job"]["status"]
        == "completed"
    )
    assert (
        client.post(
            f"/api/v1/chapters/{chapter['id']}/script",
            json={"adaptation_level": "light"},
        ).status_code
        == 202
    )
    assert (
        _wait(
            client,
            f"/api/v1/chapters/{chapter['id']}/script/status",
            "script_status",
        )["job"]["status"]
        == "completed"
    )
    if with_casting:
        assert client.post(f"/api/v1/books/{book_id}/casts/auto").status_code == 202
        casting = _wait(
            client, f"/api/v1/books/{book_id}/casts/status", "production_status"
        )
        assert casting["job"]["status"] == "completed", casting
    return book_id, chapter["id"]


def _script(client: TestClient, chapter_id: str) -> dict:
    response = client.get(f"/api/v1/chapters/{chapter_id}/script")
    assert response.status_code == 200, response.text
    return response.json()


def _tts_units(script: dict) -> list[dict]:
    return [unit for unit in script["units"] if unit["unit_type"] in TTS_TYPES]


def _narrator_unit(script: dict) -> dict:
    return next(
        unit
        for unit in _tts_units(script)
        if unit["speaker_type"] == "narrator"
    )


def test_generate_versions_restore_and_stream(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id, chapter_id = _prepare(client, upload, sample_text, title="单元音频版本")
    script = _script(client, chapter_id)
    unit = _narrator_unit(script)
    assert unit["voice_actor_id"], "选角后剧本单元应带配音员信息"
    assert unit["voice_actor_name"]

    first = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["audio_status"] == "ready"
    assert payload["unit_id"] == unit["id"]
    assert payload["render_hash"]
    asset = payload["asset"]
    assert asset is not None
    assert asset["version"] == 1
    assert asset["is_current"] is True
    assert asset["duration_ms"] > 0
    assert asset["file_size_bytes"] > 0
    assert asset["voice_actor_id"] == unit["voice_actor_id"]
    assert asset["audio_url"] == f"/api/v1/audio-assets/{asset['id']}/audio"

    audio = client.get(asset["audio_url"])
    assert audio.status_code == 200
    assert audio.content[:4] == b"RIFF"
    assert audio.headers["cache-control"] == "no-store"
    first_bytes = audio.content

    current = client.get(f"/api/v1/script-units/{unit['id']}/audio")
    assert current.status_code == 200
    assert current.content == first_bytes

    listed = client.get(f"/api/v1/script-units/{unit['id']}/audio-versions")
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["current_asset_id"] == asset["id"]

    second = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert second.status_code == 200, second.text
    second_asset = second.json()["asset"]
    assert second_asset["version"] == 2
    assert second_asset["is_current"] is True
    assert second_asset["id"] != asset["id"]
    assert (
        client.get(f"/api/v1/script-units/{unit['id']}/audio-versions").json()[
            "total"
        ]
        == 2
    )

    restored = client.post(
        f"/api/v1/script-units/{unit['id']}/audio-versions/{asset['id']}/restore"
    )
    assert restored.status_code == 200, restored.text
    restored_payload = restored.json()
    assert restored_payload["audio_status"] == "ready"
    assert restored_payload["asset"]["id"] == asset["id"]
    assert restored_payload["asset"]["is_current"] is True
    current = client.get(f"/api/v1/script-units/{unit['id']}/audio")
    assert current.content == first_bytes

    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    assert overview["audio_ready_units"] >= 1
    assert overview["audio_stale_units"] == 0
    chapter = next(item for item in overview["chapters"] if item["id"] == chapter_id)
    assert chapter["audio_ready_units"] >= 1


def test_text_and_voice_changes_mark_audio_stale(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    _book_id, chapter_id = _prepare(client, upload, sample_text, title="音频过期")
    unit = _narrator_unit(_script(client, chapter_id))
    first = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert first.status_code == 200, first.text
    first_asset = first.json()["asset"]

    with session_factory() as session:
        row = session.get(ScriptUnit, unit["id"])
        assert row is not None
        row.emotion = "sad" if row.emotion != "sad" else "happy"
        session.commit()

    refreshed = _script(client, chapter_id)
    changed = next(item for item in refreshed["units"] if item["id"] == unit["id"])
    assert changed["audio_status"] == "stale"
    overview = client.get(
        f"/api/v1/books/{_book_id}/production"
    ).json()
    assert overview["audio_stale_units"] >= 1

    second = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert second.status_code == 200, second.text
    second_asset = second.json()["asset"]
    assert second.json()["audio_status"] == "ready"
    assert second_asset["version"] == 2

    old_restore = client.post(
        f"/api/v1/script-units/{unit['id']}/audio-versions/{first_asset['id']}/restore"
    )
    assert old_restore.status_code == 200, old_restore.text
    assert old_restore.json()["audio_status"] == "stale"
    new_restore = client.post(
        f"/api/v1/script-units/{unit['id']}/audio-versions/{second_asset['id']}/restore"
    )
    assert new_restore.json()["audio_status"] == "ready"

    with session_factory() as session:
        row = session.get(ScriptUnit, unit["id"])
        assert row is not None and row.voice_cast_id
        cast = session.get(VoiceCast, row.voice_cast_id)
        assert cast is not None
        actor = session.get(VoiceActor, cast.voice_actor_id)
        assert actor is not None
        actor.description = f"{actor.description or ''} 声线描述更新"
        session.commit()

    refreshed = _script(client, chapter_id)
    changed = next(item for item in refreshed["units"] if item["id"] == unit["id"])
    assert changed["audio_status"] == "stale"


def test_missing_voice_and_cue_are_rejected(
    client: TestClient, upload, app, monkeypatch
) -> None:
    custom = """第一章 拍案

“啪”的一声，惊堂木被县太爷重重拍下。
"""

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

    monkeypatch.setattr(
        app.state.registry.llm,
        "adapt_scene_script",
        MethodType(fake_adapt, app.state.registry.llm),
    )
    _book_id, chapter_id = _prepare(
        client, upload, custom, with_casting=False, title="缺少选角"
    )
    script = _script(client, chapter_id)
    unit = _narrator_unit(script)
    assert unit["voice_cast_id"] is None
    assert unit["voice_actor_id"] is None

    response = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "unit_voice_missing"

    cue = next(
        item
        for item in script["units"]
        if item["unit_type"] in {"sfx", "ambience", "pause"}
    )
    rejected = client.post(f"/api/v1/script-units/{cue['id']}/generate")
    assert rejected.status_code == 422, rejected.text
    assert rejected.json()["error"]["code"] == "audio_cue_not_applicable"

    missing = client.get(f"/api/v1/script-units/{unit['id']}/audio")
    assert missing.status_code == 404


def test_failure_review_and_recovery(
    client: TestClient, upload, sample_text: str, session_factory, monkeypatch
) -> None:
    _book_id, chapter_id = _prepare(client, upload, sample_text, title="生成失败")
    unit = _narrator_unit(_script(client, chapter_id))
    original = client.app.state.registry.tts.synthesize

    def broken_synthesize(*args, **kwargs):
        raise RuntimeError("语音引擎不可用（测试）")

    monkeypatch.setattr(client.app.state.registry.tts, "synthesize", broken_synthesize)
    failed = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert failed.status_code == 502, failed.text
    assert failed.json()["error"]["code"] == "unit_audio_failed"

    with session_factory() as session:
        row = session.get(ScriptUnit, unit["id"])
        assert row is not None
        assert row.audio_status == "failed"
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.entity_type == "script_unit",
                ReviewItem.entity_id == unit["id"],
                ReviewItem.code == "audio_generation_failed",
                ReviewItem.status == "open",
            )
        )
        assert review is not None

    monkeypatch.setattr(client.app.state.registry.tts, "synthesize", original)
    recovered = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["audio_status"] == "ready"

    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.entity_type == "script_unit",
                ReviewItem.entity_id == unit["id"],
                ReviewItem.code == "audio_generation_failed",
            )
        )
        assert review is not None
        assert review.status == "resolved"


def test_actor_reference_problem_enters_inbox(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    _book_id, chapter_id = _prepare(client, upload, sample_text, title="参考音异常")
    unit = _narrator_unit(_script(client, chapter_id))
    with session_factory() as session:
        row = session.get(ScriptUnit, unit["id"])
        assert row is not None and row.voice_cast_id
        cast = session.get(VoiceCast, row.voice_cast_id)
        assert cast is not None
        actor = session.get(VoiceActor, cast.voice_actor_id)
        assert actor is not None
        actor.status = "archived"
        session.commit()
        actor_id = actor.id

    rejected = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["error"]["code"] == "actor_not_ready"

    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.entity_type == "script_unit",
                ReviewItem.entity_id == unit["id"],
                ReviewItem.code == "audio_voice_missing",
                ReviewItem.status == "open",
            )
        )
        assert review is not None
        actor = session.get(VoiceActor, actor_id)
        assert actor is not None
        actor.status = "ready"
        session.commit()

    generated = client.post(f"/api/v1/script-units/{unit['id']}/generate")
    assert generated.status_code == 200, generated.text
    with session_factory() as session:
        review = session.scalar(
            select(ReviewItem).where(
                ReviewItem.entity_type == "script_unit",
                ReviewItem.entity_id == unit["id"],
                ReviewItem.code == "audio_voice_missing",
            )
        )
        assert review is not None
        assert review.status == "resolved"