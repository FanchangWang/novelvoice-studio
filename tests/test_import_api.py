from __future__ import annotations

import codecs
import json
from pathlib import Path

from fastapi.testclient import TestClient

from novelvoice_studio.api.app import create_app
from novelvoice_studio.config import Settings
from novelvoice_studio.models import AudioAsset, Book, Chapter, Segment
from novelvoice_studio.services.paths import new_id


def _import(upload, sample_text: str, **form):  # type: ignore[no-untyped-def]
    data = {"title": "测试小说", **form}
    return upload("/api/v1/books/import", sample_text, **data)


def test_import_preview(upload, sample_text: str) -> None:
    response = upload("/api/v1/books/import/preview", sample_text)
    assert response.status_code == 200
    payload = response.json()
    assert payload["encoding"] == "utf-8"
    assert payload["needs_encoding_confirmation"] is False
    assert payload["chapter_count"] == 4
    assert payload["chapter_titles"] == ["前言", "第一章 雨夜来客", "第二章 旧照片", "第三章 灯下"]
    assert payload["total_chars"] == len(sample_text)
    assert payload["duplicate"] is None
    assert "旧城" in payload["sample_text"]


def test_import_creates_files_and_manifest(upload, sample_text: str, settings: Settings) -> None:
    response = _import(upload, sample_text, author="测试作者", description="用于测试")
    assert response.status_code == 201
    payload = response.json()
    book = payload["book"]
    assert payload["chapters_created"] == 4
    assert book["status"] == "ready"
    assert book["source_char_count"] == len(sample_text)
    assert book["author"] == "测试作者"

    project_dir = settings.data_dir / book["project_dir"]
    original = project_dir / "source" / "original.txt"
    normalized = project_dir / "source" / "normalized.txt"
    manifest_path = project_dir / "parsed" / "manifest.json"
    assert original.read_bytes() == sample_text.encode("utf-8")
    assert normalized.read_text(encoding="utf-8") == sample_text
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["book_id"] == book["id"]
    assert manifest["source"]["encoding"] == "utf-8"
    assert len(manifest["chapters"]) == 4
    assert manifest["chapters"][1]["start"] == manifest["chapters"][0]["end"]


def test_gbk_import_auto_and_manual(client, upload, sample_text: str) -> None:
    preview = upload(
        "/api/v1/books/import/preview",
        data=sample_text.encode("gb18030"),
        filename="gbk.txt",
    )
    assert preview.status_code == 200
    assert preview.json()["encoding"] in {"gb18030", "gbk", "gb2312"}
    assert preview.json()["needs_encoding_confirmation"] is False

    imported = upload(
        "/api/v1/books/import",
        data=sample_text.encode("gb18030"),
        filename="gbk.txt",
        title="GBK小说",
    )
    assert imported.status_code == 201
    assert imported.json()["encoding"] == preview.json()["encoding"]
    book_id = imported.json()["book"]["id"]
    assert imported.json()["book"]["source_encoding"] in {"gb18030", "gbk", "gb2312"}

    items = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    detail = client.get(f"/api/v1/chapters/{items[1]['id']}")
    assert detail.status_code == 200
    assert "林见秋" in detail.json()["text"]

    manual = upload(
        "/api/v1/books/import",
        data=sample_text.encode("gb18030"),
        filename="gbk-manual.txt",
        title="GBK手动编码",
        encoding="gb18030",
    )
    assert manual.status_code == 201
    assert manual.json()["book"]["source_encoding"] == "gb18030"


def test_utf16_bom_import(upload, sample_text: str) -> None:
    data = codecs.BOM_UTF16_LE + sample_text.encode("utf-16-le")
    response = upload(
        "/api/v1/books/import",
        data=data,
        filename="utf16.txt",
        title="UTF16小说",
    )
    assert response.status_code == 201
    assert response.json()["encoding"] == "utf-16"
    assert response.json()["book"]["source_encoding"] == "utf-16"


def test_duplicate_policies(upload, sample_text: str, settings: Settings) -> None:
    first = _import(upload, sample_text, title="第一本")
    assert first.status_code == 201
    book_id = first.json()["book"]["id"]

    preview = upload("/api/v1/books/import/preview", sample_text)
    assert preview.json()["duplicate"]["id"] == book_id

    rejected = upload(
        "/api/v1/books/import",
        sample_text,
        title="重复",
        duplicate_policy="reject",
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "duplicate_book"

    second = upload(
        "/api/v1/books/import",
        sample_text,
        title="第二本",
        duplicate_policy="new",
    )
    assert second.status_code == 201
    assert second.json()["book"]["id"] != book_id

    replaced = upload(
        "/api/v1/books/import",
        sample_text,
        title="覆盖后的书",
        duplicate_policy="replace",
        replace_book_id=book_id,
    )
    assert replaced.status_code == 201
    assert replaced.json()["replaced"] is True
    assert replaced.json()["book"]["title"] == "覆盖后的书"
    trashed = list((settings.data_dir / "trash").iterdir())
    assert trashed and trashed[0].is_dir()

    missing = upload(
        "/api/v1/books/import",
        sample_text,
        duplicate_policy="replace",
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "replace_book_id_required"


def test_replace_blocked_by_audio(
    upload, sample_text: str, db_session
) -> None:
    first = _import(upload, sample_text, title="有声书")
    book_id = first.json()["book"]["id"]
    second = upload(
        "/api/v1/books/import",
        sample_text,
        title="副本",
        duplicate_policy="new",
    )
    assert second.status_code == 201

    book = db_session.get(Book, book_id)
    chapter = db_session.query(Chapter).filter_by(book_id=book_id).first()
    assert book and chapter
    segment = Segment(
        id=new_id("seg"),
        book_id=book.id,
        chapter_id=chapter.id,
        segment_index=0,
        source_text="测试片段",
        speech_text="测试片段",
        speaker_type="narrator",
        review_status="auto",
    )
    db_session.add(segment)
    db_session.flush()
    asset = AudioAsset(
        id=new_id("audio"),
        segment_id=segment.id,
        version=1,
        file_path="audio/fake.wav",
        sample_rate=24000,
        duration_ms=1000,
    )
    db_session.add(asset)
    db_session.commit()

    response = upload(
        "/api/v1/books/import",
        sample_text,
        title="拒绝覆盖",
        duplicate_policy="replace",
        replace_book_id=book_id,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "replace_blocked_by_audio"


def test_invalid_uploads(upload) -> None:
    empty = upload("/api/v1/books/import", data=b"", filename="empty.txt")
    assert empty.status_code == 422

    pdf = upload("/api/v1/books/import", data=b"%PDF-1.4", filename="book.pdf")
    assert pdf.status_code == 415

    too_large = upload(
        "/api/v1/books/import",
        data=b"a" * (2 * 1024 * 1024 + 1),
        filename="big.txt",
    )
    assert too_large.status_code == 413


def test_upload_filename_traversal(upload, sample_text: str, settings: Settings) -> None:
    response = upload(
        "/api/v1/books/import",
        sample_text,
        filename="..\\..\\evil.txt",
        title="路径测试",
    )
    assert response.status_code == 201
    project_dir = settings.data_dir / response.json()["book"]["project_dir"]
    assert (project_dir / "source" / "original.txt").is_file()
    assert not (settings.data_dir.parent / "evil.txt").exists()

    preview = upload(
        "/api/v1/books/import/preview",
        sample_text,
        filename="C:\\Temp\\..\\note.txt",
    )
    assert preview.status_code == 200
    assert preview.json()["filename"] == "note.txt"


def test_persistence_across_app_restart(settings: Settings, upload, sample_text: str) -> None:
    with TestClient(create_app(settings)) as client:
        response = upload("/api/v1/books/import", sample_text, title="持久化")
        book_id = response.json()["book"]["id"]

    with TestClient(create_app(settings)) as client:
        listing = client.get("/api/v1/books").json()
        assert listing["total"] >= 1
        fetched = client.get(f"/api/v1/books/{book_id}")
        assert fetched.status_code == 200
        assert fetched.json()["title"] == "持久化"
        chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()
        assert chapters["total"] == 4


def test_book_crud(client) -> None:
    created = client.post("/api/v1/books", json={"title": "空项目"})
    assert created.status_code == 201
    book = created.json()
    assert book["status"] == "empty"
    assert book["chapter_count"] == 0

    patched = client.patch(f"/api/v1/books/{book['id']}", json={"title": "改名"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "改名"

    invalid = client.patch(f"/api/v1/books/{book['id']}", json={})
    assert invalid.status_code == 422

    deleted = client.delete(f"/api/v1/books/{book['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/books/{book['id']}").status_code == 404
    listed = client.get("/api/v1/books").json()
    assert all(item["id"] != book["id"] for item in listed["items"])
    with_deleted = client.get("/api/v1/books", params={"include_deleted": "true"}).json()
    assert any(item["id"] == book["id"] for item in with_deleted["items"])

    restored = client.post(f"/api/v1/books/{book['id']}/restore")
    assert restored.status_code == 200
    assert restored.json()["status"] == "empty"


def test_invalid_book_id(client) -> None:
    assert client.get("/api/v1/books/book_zzzzzzzzzzzzzzzz").status_code == 422
    assert client.get("/api/v1/books/not-a-book").status_code == 422
    assert client.get("/api/v1/books/..%2F..").status_code == 404