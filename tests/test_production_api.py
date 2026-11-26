from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from novelvoice_studio.db.types import utc_now
from novelvoice_studio.models import ReviewItem
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
    chapters = response.json()["items"]
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
    assert len(data["chapters"]) == len(chapters)
    first = data["chapters"][0]
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
    assert data["counts"]["blocking"] >= 1

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