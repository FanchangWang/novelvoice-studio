from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import Chapter, ReviewItem, Scene, ScriptUnit, ScriptVersion
from novelvoice_studio.services.paths import new_id


def _import_sample(upload, sample_text: str, title: str = "制作台测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201, response.text
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
    raise AssertionError(f"角色圣经任务未结束: {last}")


def _wait_job(
    client: TestClient, path: str, field: str, timeout: float = 30.0
) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(path)
        assert response.status_code == 200, response.text
        last = response.json()
        job = last.get("job")
        if (job is None or job["status"] in {"completed", "failed", "cancelled"}) and (
            last.get(field) != "running"
        ):
            return last
        time.sleep(0.05)
    raise AssertionError(f"{path} 未结束: {last}")


def _build_full_pipeline(client: TestClient, upload, sample_text: str) -> tuple[str, list[dict]]:
    book_id = _import_sample(upload, sample_text)
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    status = _wait_bible(client, book_id)
    assert status["bible_status"] in {"approved", "needs_review"}

    response = client.get(f"/api/v1/books/{book_id}/chapters")
    chapters = [
        item for item in response.json()["items"] if item["analysis_included"]
    ]
    assert chapters
    for chapter in chapters:
        assert (
            client.post(f"/api/v1/chapters/{chapter['id']}/scenes").status_code
            == 202
        )
        scene_status = _wait_job(
            client,
            f"/api/v1/chapters/{chapter['id']}/scenes/status",
            "scene_status",
        )
        assert scene_status["job"]["status"] == "completed"
        script = client.post(
            f"/api/v1/chapters/{chapter['id']}/script",
            json={"adaptation_level": "light"},
        )
        assert script.status_code == 202, script.text
        script_status = _wait_job(
            client,
            f"/api/v1/chapters/{chapter['id']}/script/status",
            "script_status",
        )
        assert script_status["job"]["status"] == "completed"

    assert client.post(f"/api/v1/books/{book_id}/casts/auto").status_code == 202
    casting = _wait_job(
        client, f"/api/v1/books/{book_id}/casts/status", "production_status"
    )
    assert casting["job"]["status"] == "completed"
    return book_id, chapters


def test_production_overview_tracks_full_pipeline(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id, chapters = _build_full_pipeline(client, upload, sample_text)

    response = client.get(f"/api/v1/books/{book_id}/production")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["book_id"] == book_id
    assert data["bible_ready"] is True
    assert data["character_count"] > 0
    assert data["arc_count"] > 0
    assert data["chapter_count"] == len(chapters)
    assert data["chapter_count"] < len(data["chapters"])
    assert data["chapters_with_scenes"] == len(chapters)
    assert data["chapters_with_scripts"] == len(chapters)
    assert data["scene_count"] > 0
    assert data["script_unit_count"] > 0
    assert data["tts_unit_count"] > 0
    assert data["cast_count"] > 0
    assert data["narrator_ready"] is True
    assert data["derived_status"] in {"ready", "needs_review"}
    assert data["next_action"] in {"reviews", "audio", "done"}
    assert data["job"] is None
    assert len(data["chapters"]) == len(chapters) + 1
    assert next(
        item for item in data["chapters"] if not item["analysis_included"]
    )["chapter_kind"] == "extra"
    first = next(item for item in data["chapters"] if item["analysis_included"])
    assert first["scene_count"] > 0
    assert first["unit_count"] > 0
    assert first["tts_unit_count"] > 0
    assert data["open_reviews"] == (
        data["blocking_reviews"] + data["warning_reviews"] + data["info_reviews"]
    )


def test_review_inbox_list_and_resolve(
    client: TestClient, upload, sample_text: str, db_session: Session
) -> None:
    book_id, chapters = _build_full_pipeline(client, upload, sample_text)
    chapter_id = chapters[0]["id"]
    now = utc_now()
    rows = [
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="chapter",
            entity_id=chapter_id,
            code="test_blocking",
            severity="blocking",
            title="测试阻塞项",
            message="需要人工确认",
            payload_json={"chapter_id": chapter_id, "sample": 1},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="book",
            entity_id=book_id,
            code="test_warning",
            severity="warning",
            title="测试警告项",
            payload_json={},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="book",
            entity_id=book_id,
            code="test_info",
            severity="info",
            title="测试提示项",
            payload_json={},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    ids = [row.id for row in rows]

    listing = client.get(f"/api/v1/reviews?book_id={book_id}&limit=50")
    assert listing.status_code == 200, listing.text
    data = listing.json()
    assert data["total"] >= 3
    by_id = {item["id"]: item for item in data["items"]}
    assert by_id[ids[0]]["severity"] == "blocking"
    assert by_id[ids[0]]["book_title"] == "制作台测试"
    assert data["counts"]["open"] >= 3
    assert by_id[ids[0]]["chapter_id"] == chapter_id
    assert by_id[ids[0]]["chapter_title"] == chapters[0]["title"]
    assert by_id[ids[1]]["chapter_id"] is None
    assert data["counts"]["blocking"] >= 1

    chapter_filtered = client.get(
        f"/api/v1/reviews?book_id={book_id}&chapter_id={chapter_id}&limit=50"
    )
    assert chapter_filtered.status_code == 200, chapter_filtered.text
    assert chapter_filtered.json()["counts"]["open"] == chapter_filtered.json()["total"]
    assert chapter_filtered.json()["counts"]["open"] < data["counts"]["open"]
    assert all(
        item["chapter_id"] == chapter_id
        for item in chapter_filtered.json()["items"]
    )
    assert ids[0] in {
        item["id"] for item in chapter_filtered.json()["items"]
    }
    assert ids[1] not in {
        item["id"] for item in chapter_filtered.json()["items"]
    }
    missing_chapter = client.get(
        "/api/v1/reviews?chapter_id=ch_0000000000000000"
    )
    assert missing_chapter.status_code == 404

    filtered = client.get(f"/api/v1/reviews?book_id={book_id}&severity=blocking")
    filtered_ids = {item["id"] for item in filtered.json()["items"]}
    assert ids[0] in filtered_ids
    assert ids[1] not in filtered_ids

    accepted = client.post(
        f"/api/v1/reviews/{ids[0]}/resolve", json={"action": "accept"}
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["status"] == "resolved"
    assert accepted.json()["resolution_note"] == "已接受"

    ignored_without_note = client.post(
        f"/api/v1/reviews/{ids[1]}/resolve", json={"action": "ignore"}
    )
    assert ignored_without_note.status_code == 422
    ignored = client.post(
        f"/api/v1/reviews/{ids[1]}/resolve",
        json={"action": "ignore", "note": "已人工核对"},
    )
    assert ignored.status_code == 200, ignored.text
    assert ignored.json()["status"] == "ignored"
    assert ignored.json()["resolution_note"] == "已人工核对"

    # 已关闭项重复处理保持幂等
    again = client.post(
        f"/api/v1/reviews/{ids[0]}/resolve", json={"action": "accept"}
    )
    assert again.status_code == 200
    assert again.json()["status"] == "resolved"

    open_listing = client.get(f"/api/v1/reviews?book_id={book_id}&status=open")
    open_ids = {item["id"] for item in open_listing.json()["items"]}
    assert ids[0] not in open_ids
    assert ids[1] not in open_ids
    assert ids[2] in open_ids


def test_stage_status_only_current_blocking_reviews_require_review(
    client: TestClient,
    upload,
    sample_text: str,
    db_session: Session,
) -> None:
    book_id, chapters = _build_full_pipeline(client, upload, sample_text)
    chapter_id = chapters[0]["id"]
    scene_state = client.get(f"/api/v1/chapters/{chapter_id}/scenes").json()
    script_state = client.get(f"/api/v1/chapters/{chapter_id}/script").json()
    scene = scene_state["scenes"][0]
    unit = script_state["units"][0]
    version = script_state["version"]
    now = utc_now()

    chapter = db_session.get(Chapter, chapter_id)
    assert chapter is not None
    chapter.scene_status = "needs_review"
    chapter.script_status = "needs_review"
    current_scene = db_session.get(Scene, scene["id"])
    assert current_scene is not None
    current_scene.analysis_status = "needs_review"
    current_unit = db_session.get(ScriptUnit, unit["id"])
    assert current_unit is not None
    current_unit.review_status = "needs_review"
    current_version = db_session.get(ScriptVersion, version["id"])
    assert current_version is not None
    current_version.status = "needs_review"

    old_scene = Scene(
        id=new_id("scene"),
        book_id=book_id,
        chapter_id=chapter_id,
        analysis_version=0,
        scene_index=99,
        source_start_offset=0,
        source_end_offset=1,
        time_mode="present",
        analysis_status="needs_review",
        evidence_json=[],
        meta_json={},
        created_at=now,
        updated_at=now,
    )
    old_version = ScriptVersion(
        id=new_id("script"),
        book_id=book_id,
        chapter_id=chapter_id,
        version=version["version"] + 1,
        status="needs_review",
        adaptation_level="light",
        source_scene_version=chapter.scene_version,
        is_active=False,
        summary_json={},
        created_at=now,
        updated_at=now,
    )
    warning_rows = [
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="scene",
            entity_id=scene["id"],
            code="scene_low_confidence",
            severity="warning",
            title="场景低置信提示",
            payload_json={"chapter_id": chapter_id},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="script_unit",
            entity_id=unit["id"],
            code="script_low_confidence",
            severity="warning",
            title="剧本低置信提示",
            payload_json={"chapter_id": chapter_id},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
    ]
    old_blocking_rows = [
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="scene",
            entity_id=old_scene.id,
            code="scene_old_conflict",
            severity="blocking",
            title="历史分场冲突",
            payload_json={"chapter_id": chapter_id},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="script_version",
            entity_id=old_version.id,
            code="script_old_conflict",
            severity="blocking",
            title="历史剧本冲突",
            payload_json={"chapter_id": chapter_id},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        ),
    ]
    db_session.add_all([old_scene, old_version, *warning_rows, *old_blocking_rows])
    db_session.commit()

    scenes_response = client.get(f"/api/v1/chapters/{chapter_id}/scenes")
    assert scenes_response.status_code == 200, scenes_response.text
    scenes_data = scenes_response.json()
    assert scenes_data["scene_status"] == "completed"
    assert scenes_data["scenes"][0]["analysis_status"] == "completed"
    assert {item["id"] for item in scenes_data["review_items"]}.isdisjoint(
        {old_blocking_rows[0].id}
    )

    script_response = client.get(f"/api/v1/chapters/{chapter_id}/script")
    assert script_response.status_code == 200, script_response.text
    script_data = script_response.json()
    assert script_data["script_status"] == "completed"
    assert script_data["version"]["status"] == "draft"
    assert script_data["units"][0]["review_status"] == "auto"
    assert {item["id"] for item in script_data["review_items"]}.isdisjoint(
        {old_blocking_rows[1].id}
    )

    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    overview_chapter = next(item for item in overview["chapters"] if item["id"] == chapter_id)
    assert overview_chapter["scene_status"] == "completed"
    assert overview_chapter["script_status"] == "completed"

    blocking = ReviewItem(
        id=new_id("review"),
        book_id=book_id,
        entity_type="scene",
        entity_id=scene["id"],
        code="scene_blocking_conflict",
        severity="blocking",
        title="当前分场冲突",
        payload_json={"chapter_id": chapter_id},
        status="open",
        source="system",
        created_at=now,
        updated_at=now,
    )
    db_session.add(blocking)
    db_session.commit()
    blocking_id = blocking.id
    assert client.get(f"/api/v1/chapters/{chapter_id}/scenes").json()[
        "scene_status"
    ] == "needs_review"

    resolved = client.post(
        f"/api/v1/reviews/{blocking_id}/resolve",
        json={"action": "accept"},
    )
    assert resolved.status_code == 200, resolved.text
    assert client.get(f"/api/v1/chapters/{chapter_id}/scenes").json()[
        "scene_status"
    ] == "completed"
    db_session.expire_all()
    refreshed = db_session.get(Chapter, chapter_id)
    assert refreshed is not None
    assert refreshed.scene_status == "completed"

    script_blocking = ReviewItem(
        id=new_id("review"),
        book_id=book_id,
        entity_type="script_unit",
        entity_id=unit["id"],
        code="script_blocking_conflict",
        severity="blocking",
        title="当前剧本冲突",
        payload_json={"chapter_id": chapter_id},
        status="open",
        source="system",
        created_at=now,
        updated_at=now,
    )
    db_session.add(script_blocking)
    db_session.commit()
    script_blocking_id = script_blocking.id
    assert client.get(f"/api/v1/chapters/{chapter_id}/script").json()[
        "script_status"
    ] == "needs_review"
    batch = client.post(
        "/api/v1/reviews/batch-resolve",
        json={"review_ids": [script_blocking_id], "action": "accept"},
    )
    assert batch.status_code == 200, batch.text
    assert client.get(f"/api/v1/chapters/{chapter_id}/script").json()[
        "script_status"
    ] == "completed"
    db_session.expire_all()
    refreshed = db_session.get(Chapter, chapter_id)
    assert refreshed is not None
    assert refreshed.script_status == "completed"


def test_review_batch_resolve_and_invalid_ids(
    client: TestClient, upload, sample_text: str, db_session: Session
) -> None:
    book_id, _ = _build_full_pipeline(client, upload, sample_text)
    now = utc_now()
    rows = [
        ReviewItem(
            id=new_id("review"),
            book_id=book_id,
            entity_type="book",
            entity_id=book_id,
            code=f"test_batch_{index}",
            severity="warning",
            title=f"批量测试 {index}",
            payload_json={},
            status="open",
            source="system",
            created_at=now,
            updated_at=now,
        )
        for index in range(2)
    ]
    db_session.add_all(rows)
    db_session.commit()
    ids = [row.id for row in rows]

    result = client.post(
        "/api/v1/reviews/batch-resolve",
        json={"review_ids": ids, "action": "accept", "note": "批量接受"},
    )
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["resolved"] == 2
    assert payload["ignored"] == 0
    assert payload["book_ids"] == [book_id]

    db_session.expire_all()
    statuses = list(
        db_session.scalars(select(ReviewItem).where(ReviewItem.id.in_(ids)))
    )
    assert {item.status for item in statuses} == {"resolved"}

    missing = client.post(
        "/api/v1/reviews/batch-resolve",
        json={"review_ids": [ids[0], "review_0000000000000000"], "action": "accept"},
    )
    assert missing.status_code == 404

    invalid_resolve = client.post(
        "/api/v1/reviews/not-an-id/resolve", json={"action": "accept"}
    )
    assert invalid_resolve.status_code == 422

    unknown_book = client.get("/api/v1/books/book_0000000000000000/production")
    assert unknown_book.status_code == 404


def test_content_inbox_excludes_diagnostics_and_system_failures(
    client: TestClient, upload, sample_text: str, db_session: Session
) -> None:
    book_id = _import_sample(upload, sample_text, title="异常分类")
    now = utc_now()
    rows = [
        ReviewItem(
            id=new_id("review"), book_id=book_id, entity_type="book",
            entity_id=book_id, code="real_content_conflict", severity="blocking",
            title="真实内容冲突", category="content", payload_json={},
            status="open", source="system", created_at=now, updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"), book_id=book_id, entity_type="book",
            entity_id=book_id, code="script_low_confidence", severity="warning",
            title="自动采用的诊断", category="content", payload_json={},
            status="open", source="system", created_at=now, updated_at=now,
        ),
        ReviewItem(
            id=new_id("review"), book_id=book_id, entity_type="book",
            entity_id=book_id, code="unit_audio_failed", severity="blocking",
            title="语音引擎失败", category="system", payload_json={},
            status="open", source="system", created_at=now, updated_at=now,
        ),
    ]
    db_session.add_all(rows)
    db_session.commit()
    ids = [row.id for row in rows]

    content = client.get(f"/api/v1/reviews?book_id={book_id}").json()
    assert {item["id"] for item in content["items"]} == {ids[0]}
    assert content["items"][0]["impact"]
    assert content["items"][0]["recommended_action"]
    system = client.get(f"/api/v1/reviews?book_id={book_id}&category=system").json()
    assert {item["id"] for item in system["items"]} == {ids[2]}
    assert system["items"][0]["action_type"] == "task"
    all_items = client.get(f"/api/v1/reviews?book_id={book_id}&category=all").json()
    assert {item["id"] for item in all_items["items"]} == set(ids)

    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    assert overview["open_reviews"] == 1
    assert overview["blocking_reviews"] == 1
    assert overview["warning_reviews"] == 0


def _wait_batch(client: TestClient, job_id: str, timeout: float = 60.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/production-batches/{job_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] in {"completed", "failed", "cancelled"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"批量制作任务未结束: {last}")


def _batch_source() -> str:
    return (
        "第一卷 山门\n\n"
        "第一章 入山\n\n林见秋推开山门，沈砚提着灯跟在后面。\n\n"
        "第二章 寻人\n\n阿棠站在灯下，说姐姐林见春还没有回来。\n\n"
        "第二卷 故城\n\n"
        "第一章 钥匙\n\n沈砚把旧木盒推到林见秋面前，盒里有一枚生锈的钥匙。\n\n"
        "番外 旧日书信\n\n林见春留下一封没有寄出的信。\n\n"
        "请假单\n\n作者今日休息，明日更新。\n"
    )


def test_production_batch_generates_book_and_only_missing(
    client: TestClient, upload,
) -> None:
    source = _batch_source()
    book_id = _import_sample(upload, source, title="批量制作测试")
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {
        "approved", "needs_review"
    }
    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    included = [item for item in chapters if item["analysis_included"]]
    assert len(included) == 3
    assert {item["chapter_kind"] for item in chapters if not item["analysis_included"]} == {
        "extra", "author_note"
    }

    started = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes", "script"], "only_missing": True},
    )
    assert started.status_code == 202, started.text
    job_id = started.json()["id"]
    done = _wait_batch(client, job_id)
    assert done["status"] == "completed", done
    assert done["total_items"] == 3
    assert done["completed_items"] == 3
    assert done["failed_items"] == 0
    assert len(done["result"]["child_job_ids"]) == 6
    assert all(
        item["status"] == "completed" for item in done["result"]["chapters"]
    )
    overview = client.get(f"/api/v1/books/{book_id}/production").json()
    assert overview["chapter_count"] == 3
    assert overview["chapters_with_scenes"] == 3
    assert overview["chapters_with_scripts"] == 3

    skipped = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes", "script"], "only_missing": True},
    )
    assert skipped.status_code == 202, skipped.text
    skipped_done = _wait_batch(client, skipped.json()["id"])
    assert skipped_done["status"] == "completed"
    assert skipped_done["result"]["child_job_ids"] == []
    assert all(
        stage["status"] == "skipped"
        for item in skipped_done["result"]["chapters"]
        for stage in item["stages"].values()
    )


def test_production_batch_volume_selection_and_validation(
    client: TestClient, upload,
) -> None:
    book_id = _import_sample(upload, _batch_source(), title="卷批量测试")
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {
        "approved", "needs_review"
    }
    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    first_volume = [item for item in chapters if item["volume_number"] == 1]
    second_volume = next(item for item in chapters if item["volume_number"] == 2)

    started = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes"], "volume_number": 1},
    )
    assert started.status_code == 202, started.text
    done = _wait_batch(client, started.json()["id"])
    assert done["status"] == "completed"
    assert {item["chapter_id"] for item in done["result"]["chapters"]} == {
        item["id"] for item in first_volume
    }

    selected = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={
            "stages": ["scenes", "script"],
            "volume_number": 2,
            "chapter_ids": [second_volume["id"]],
        },
    )
    assert selected.status_code == 202, selected.text
    selected_done = _wait_batch(client, selected.json()["id"])
    assert selected_done["status"] == "completed"
    assert [item["chapter_id"] for item in selected_done["result"]["chapters"]] == [
        second_volume["id"]
    ]

    excluded = next(item for item in chapters if not item["analysis_included"])
    rejected = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes"], "chapter_ids": [excluded["id"]]},
    )
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "batch_chapter_unavailable"
    wrong_volume = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={
            "stages": ["scenes"],
            "volume_number": 1,
            "chapter_ids": [second_volume["id"]],
        },
    )
    assert wrong_volume.status_code == 422


def test_production_batch_isolates_chapter_failure(
    client: TestClient, upload, monkeypatch,
) -> None:
    from novelvoice_studio.services.scenes import SceneService

    book_id = _import_sample(upload, _batch_source(), title="批量容错测试")
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {
        "approved", "needs_review"
    }
    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    failed_chapter = next(item for item in chapters if item["analysis_included"])
    original = SceneService.start_scene_analysis

    def fail_one(self, chapter_id: str, *args, **kwargs):
        if chapter_id == failed_chapter["id"]:
            raise RuntimeError("受控失败")
        return original(self, chapter_id, *args, **kwargs)

    monkeypatch.setattr(SceneService, "start_scene_analysis", fail_one)
    started = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes", "script"]},
    )
    assert started.status_code == 202, started.text
    done = _wait_batch(client, started.json()["id"])
    assert done["status"] == "failed"
    assert done["error_code"] == "production_batch_chapters_failed"
    assert done["failed_items"] == 1
    results = {item["chapter_id"]: item for item in done["result"]["chapters"]}
    assert results[failed_chapter["id"]]["status"] == "failed"
    assert results[failed_chapter["id"]]["stages"]["script"]["status"] == "skipped"
    assert sum(item["status"] == "completed" for item in results.values()) == 2
    assert len(done["result"]["child_job_ids"]) == 4


def test_production_batch_cancel_and_recovery(
    client: TestClient, upload, monkeypatch, session_factory,
) -> None:
    from threading import Event
    from novelvoice_studio.services.production_batch import ProductionBatchService

    book_id = _import_sample(upload, _batch_source(), title="批量取消测试")
    assert client.post(f"/api/v1/books/{book_id}/bible/analysis").status_code == 202
    assert _wait_bible(client, book_id)["bible_status"] in {
        "approved", "needs_review"
    }
    entered = Event()
    release = Event()

    def blocked_run(self, job_id: str) -> None:
        entered.set()
        release.wait(10)

    monkeypatch.setattr(ProductionBatchService, "_run", blocked_run)
    started = client.post(
        f"/api/v1/books/{book_id}/production/batch",
        json={"stages": ["scenes"]},
    )
    assert started.status_code == 202
    job_id = started.json()["id"]
    assert entered.wait(5)
    cancelled = client.post(f"/api/v1/production-batches/{job_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"
    release.set()
    assert client.post(f"/api/v1/production-batches/{job_id}/cancel").status_code == 409

    from novelvoice_studio.models import GenerationJob
    from novelvoice_studio.services.production_batch import recover_interrupted_batches

    with session_factory() as session:
        session.add(
            GenerationJob(
                id=new_id("job"), book_id=book_id, target_id=book_id,
                target_type="book", stage="production", job_type="production_batch",
                status="running", total_items=1, completed_items=0, failed_items=0,
                progress=0.0, attempt_count=0, max_attempts=1,
                params_json={}, result_json={"current_child": {"job_id": "job_x"}},
                created_at=utc_now(), updated_at=utc_now(),
            )
        )
        session.commit()
        recovered = recover_interrupted_batches(session)
        session.commit()
        assert recovered["production_batch_jobs"] == 1
        stale = session.scalar(
            select(GenerationJob).where(
                GenerationJob.book_id == book_id,
                GenerationJob.status == "running",
            )
        )
        assert stale is None
        interrupted = session.scalar(
            select(GenerationJob).where(GenerationJob.error_code == "interrupted")
        )
        assert interrupted is not None
        assert interrupted.result_json["current_child"] is None