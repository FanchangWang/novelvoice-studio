from __future__ import annotations

from sqlalchemy import select

from novelvoice_studio.models import Book, GenerationJob, VoiceActor
from novelvoice_studio.services.paths import new_id


def test_trash_listing_restore_and_permanent_delete(
    client, upload, sample_text: str, settings
) -> None:
    imported = upload("/api/v1/books/import", sample_text, title="回收站测试")
    assert imported.status_code == 201
    book_id = imported.json()["book"]["id"]
    project_root = settings.data_dir / "books" / book_id
    assert project_root.is_dir()

    assert client.delete(f"/api/v1/books/{book_id}").status_code == 204

    active = client.get("/api/v1/books").json()
    assert all(item["id"] != book_id for item in active["items"])
    trash = client.get("/api/v1/books", params={"deleted_only": "true"}).json()
    assert [item["id"] for item in trash["items"]] == [book_id]

    restored = client.post(f"/api/v1/books/{book_id}/restore")
    assert restored.status_code == 200
    assert restored.json()["deleted_at"] is None

    assert client.delete(f"/api/v1/books/{book_id}").status_code == 204
    deleted = client.delete(f"/api/v1/books/{book_id}/permanent")
    assert deleted.status_code == 204
    assert not project_root.exists()
    assert client.get("/api/v1/books", params={"deleted_only": "true"}).json()["total"] == 0


def test_permanent_delete_requires_trash_and_blocks_running_jobs(
    client, upload, sample_text: str, db_session
) -> None:
    imported = upload("/api/v1/books/import", sample_text, title="删除保护测试")
    book_id = imported.json()["book"]["id"]

    active = client.delete(f"/api/v1/books/{book_id}/permanent")
    assert active.status_code == 409
    assert active.json()["error"]["code"] == "book_not_in_trash"

    assert client.delete(f"/api/v1/books/{book_id}").status_code == 204
    db_session.add(
        GenerationJob(
            id=new_id("job"),
            book_id=book_id,
            job_type="analysis",
            status="running",
        )
    )
    db_session.commit()

    blocked = client.delete(f"/api/v1/books/{book_id}/permanent")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "book_has_running_jobs"


def test_permanent_delete_removes_book_scoped_voice_actor(
    client, upload, sample_text: str, db_session
) -> None:
    imported = upload("/api/v1/books/import", sample_text, title="专属声线清理")
    book_id = imported.json()["book"]["id"]
    actor = VoiceActor(
        id=new_id("actor"),
        display_name="本书专属声线",
        scope="book",
        source_book_id=book_id,
    )
    db_session.add(actor)
    db_session.commit()
    actor_id = actor.id

    assert client.delete(f"/api/v1/books/{book_id}").status_code == 204
    assert client.delete(f"/api/v1/books/{book_id}/permanent").status_code == 204
    assert db_session.scalar(select(Book).where(Book.id == book_id)) is None
    assert db_session.scalar(
        select(VoiceActor).where(VoiceActor.id == actor_id)
    ) is None
