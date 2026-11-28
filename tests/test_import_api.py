from __future__ import annotations

import codecs
import json
from pathlib import Path

import pytest
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


def test_import_normalizes_markdown_titles_and_preserves_source(
    client, upload, settings: Settings
) -> None:
    text = (
        "# 书名：雾港来信\n作者：林秋\n\n"
        "## 前言\n卷首内容。\n\n"
        "### 第一章 雨夜——最新章节\n正文一。\n\n"
        "## 第二章 旧照片 https://example.com/novel\n正文二。\n"
    )
    preview = upload("/api/v1/books/import/preview", text, filename="下载器.txt")
    assert preview.status_code == 200, preview.text
    assert preview.json()["chapter_titles"] == ["前言", "第一章 雨夜", "第二章 旧照片"]

    imported = upload("/api/v1/books/import", text, filename="下载器.txt")
    assert imported.status_code == 201, imported.text
    book = imported.json()["book"]
    items = client.get(f"/api/v1/books/{book['id']}/chapters").json()["items"]
    assert [item["title"] for item in items] == ["前言", "第一章 雨夜", "第二章 旧照片"]
    assert items[1]["source_start_offset"] == text.index("### 第一章")
    assert items[-1]["source_end_offset"] == len(text)

    project_dir = settings.data_dir / book["project_dir"]
    assert (project_dir / "source" / "original.txt").read_bytes() == text.encode("utf-8")
    assert (project_dir / "source" / "normalized.txt").read_text(encoding="utf-8") == text
    manifest = json.loads((project_dir / "parsed" / "manifest.json").read_text(encoding="utf-8"))
    assert [item["title"] for item in manifest["chapters"]] == [
        "前言", "第一章 雨夜", "第二章 旧照片",
    ]


def test_import_uses_explicit_book_title_to_strip_heading_prefix(
    client, upload, settings: Settings
) -> None:
    text = (
        "# 《用户指定书名》第一章 第一章 雨夜\n正文一。\n\n"
        "## 《用户指定书名》第二章 旧照片——笔趣阁 www.biquge.com\n正文二。\n"
    )
    imported = upload(
        "/api/v1/books/import",
        text,
        filename="sample.txt",
        title="用户指定书名",
    )
    assert imported.status_code == 201, imported.text
    book = imported.json()["book"]
    assert imported.json()["chapters_created"] == 2
    items = client.get(f"/api/v1/books/{book['id']}/chapters").json()["items"]
    assert [item["title"] for item in items] == ["第一章 雨夜", "第二章 旧照片"]
    project_dir = settings.data_dir / book["project_dir"]
    assert (project_dir / "source" / "normalized.txt").read_text(encoding="utf-8") == text


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


def test_book_ids_are_numeric_and_never_reused(client, upload, sample_text: str) -> None:
    first = _import(upload, sample_text, title="第一本")
    second = _import(upload, sample_text, title="第二本", duplicate_policy="new")
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["book"]["id"] == "book_1"
    assert second.json()["book"]["id"] == "book_2"

    assert client.delete("/api/v1/books/book_1").status_code == 204
    third = _import(upload, sample_text, title="第三本", duplicate_policy="new")
    assert third.status_code == 201
    assert third.json()["book"]["id"] == "book_3"


def test_import_extracts_metadata_from_front_matter(upload) -> None:
    text = (
        "书名：雾港来信\n"
        "作者：林秋\n"
        "内容简介：一封迟到十年的信，让旧港的往事重新浮出水面。\n"
        "\n"
        "第一章 来信\n"
        "雨停在清晨。\n"
    )
    preview = upload(
        "/api/v1/books/import/preview",
        text,
        filename="www.example.com_123456.txt",
    )
    assert preview.status_code == 200
    metadata = preview.json()["metadata"]
    assert metadata["title"] == "雾港来信"
    assert metadata["author"] == "林秋"
    assert metadata["description"].startswith("一封迟到十年的信")
    assert metadata["title_source"] == "content_label"
    assert metadata["confidence"] >= 0.9

    imported = upload(
        "/api/v1/books/import",
        text,
        filename="www.example.com_123456.txt",
    )
    assert imported.status_code == 201
    book = imported.json()["book"]
    assert book["title"] == "雾港来信"
    assert book["author"] == "林秋"
    assert book["description"].startswith("一封迟到十年的信")


def test_import_description_excludes_publication_and_copyright_noise(upload) -> None:
    text = (
        "书名：最后一篇日记\n"
        "作者：随楹\n"
        "简介：\n"
        "【简体出版已签约】\n"
        "借助看花的名义，其实只不过想看你一眼罢了。\n"
        "\n"
        "========================================\n"
        "版权信息\n"
        "出版社：北京芙跃信息技术有限公司\n"
        "本书由唯阅读授权本平台进行数字发行\n"
        "版权所有·侵权必究\n"
        "第一章 日记的最后一篇\n"
        "是春天。\n"
    )
    response = upload("/api/v1/books/import", text, filename="最后一篇日记.txt")

    assert response.status_code == 201, response.text
    assert response.json()["book"]["description"] == (
        "借助看花的名义，其实只不过想看你一眼罢了。"
    )


@pytest.mark.parametrize(
    "boundary",
    [
        "\n\n正文不应被收进简介。",
        "\n第二章 后续\n正文不应被收进简介。",
        "\n================ 版权信息",
        "\n---- 出版社：示例出版社",
        "\n版权所有·侵权必究",
        "\n【简体出版已签约】",
        "\n正文开始",
    ],
)
def test_import_description_stops_at_noise_boundaries(upload, boundary: str) -> None:
    text = f"书名：边界测试\n内容简介：第一句是完整简介。{boundary}\n"
    response = upload("/api/v1/books/import/preview", text)

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["description"] == "第一句是完整简介。"


@pytest.mark.parametrize(
    "noise",
    [
        "================ 版权信息",
        "---- 出版社：示例出版社",
        "版权所有·侵权必究",
        "【简体出版已签约】",
        "第二章 后续",
        "正文开始",
    ],
)
def test_import_description_removes_inline_noise(upload, noise: str) -> None:
    text = f"书名：行内噪声\n内容简介：第一句是完整简介。{noise}\n第二句也不保留。\n"
    response = upload("/api/v1/books/import/preview", text)

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["description"] == "第一句是完整简介。"


def test_import_description_preserves_punctuation_and_line_breaks(upload) -> None:
    text = (
        "书名：原文保留\n"
        "内容简介：第一行，含“引号”和（括号）！\n"
        "第二行仍含标点；换行原样保留。\n"
        "\n"
        "第一章 正文\n"
    )
    response = upload("/api/v1/books/import/preview", text)

    assert response.status_code == 200, response.text
    assert response.json()["metadata"]["description"] == (
        "第一行，含“引号”和（括号）！\n第二行仍含标点；换行原样保留。"
    )


def test_import_description_truncates_at_complete_sentence(upload) -> None:
    sentences = [f"第{index}句是完整的内容简介。" for index in range(1, 61)]
    expected = ""
    for sentence in sentences:
        if len(expected) + len(sentence) > 180:
            break
        expected += sentence
    assert expected and len(expected) <= 180 < len("".join(sentences))
    text = f"书名：超长简介\n内容简介：{''.join(sentences)}\n"
    response = upload("/api/v1/books/import/preview", text)

    assert response.status_code == 200, response.text
    description = response.json()["metadata"]["description"]
    assert description == expected
    assert description.endswith("。")
    assert len(description) <= 180


def test_import_uses_cleaned_filename_when_content_has_no_metadata(upload) -> None:
    text = "第一章 开端\n\n故事从这里开始。\n"
    imported = upload(
        "/api/v1/books/import",
        text,
        filename="【精校】雾港来信_第1-10章.txt",
    )
    assert imported.status_code == 201
    assert imported.json()["book"]["title"] == "雾港来信"


def test_merge_import_preserves_user_metadata(client, upload) -> None:
    original = "书名：旧名\n作者：旧作者\n简介：旧简介\n\n第一章 起点\n\n甲。\n"
    first = upload("/api/v1/books/import", original, filename="旧名.txt")
    assert first.status_code == 201
    book_id = first.json()["book"]["id"]
    patched = client.patch(
        f"/api/v1/books/{book_id}",
        json={"title": "用户修改名", "author": "用户作者", "description": "用户简介"},
    )
    assert patched.status_code == 200

    updated = "书名：旧名\n作者：旧作者\n简介：旧简介\n\n第一章 起点\n\n甲。\n\n第二章 续篇\n\n乙。\n"
    merged = upload(
        "/api/v1/books/import",
        updated,
        filename="新名.txt",
        duplicate_policy="merge",
        replace_book_id=book_id,
    )
    assert merged.status_code == 201, merged.text
    book = merged.json()["book"]
    assert book["title"] == "用户修改名"
    assert book["author"] == "用户作者"
    assert book["description"] == "用户简介"


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


def test_merge_appends_new_chapters_and_preserves_ids(
    client, upload, sample_text: str, settings: Settings
) -> None:
    first = _import(upload, sample_text, title="连载书")
    assert first.status_code == 201
    book_id = first.json()["book"]["id"]
    original_ids = [item["id"] for item in client.get(
        f"/api/v1/books/{book_id}/chapters"
    ).json()["items"]]
    addition = "第四章 后续\n\n新增正文。\n"

    merged = upload(
        "/api/v1/books/import",
        addition,
        title="连载书",
        duplicate_policy="merge",
        replace_book_id=book_id,
    )
    assert merged.status_code == 201
    payload = merged.json()
    assert payload["mode"] == "merge"
    assert payload["chapters_created"] == 1
    assert payload["chapters_unchanged"] == 4
    items = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    assert [item["id"] for item in items[:4]] == original_ids
    assert items[-1]["title"] == "第四章 后续"
    project_dir = settings.data_dir / payload["book"]["project_dir"]
    normalized = (project_dir / "source" / "normalized.txt").read_text(encoding="utf-8")
    assert normalized.startswith(sample_text)
    assert normalized.endswith(addition)


def test_merge_full_revision_preserves_audio_and_invalidates_changed_chapter(
    client, upload, sample_text: str, settings: Settings, db_session
) -> None:
    from novelvoice_studio.models import ScriptUnit, ScriptVersion

    first = _import(upload, sample_text, title="修订书")
    book_id = first.json()["book"]["id"]
    items = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    changed, later = items[1], items[2]
    changed_text = client.get(f"/api/v1/chapters/{changed['id']}").json()["text"]
    insertion = "\n\n新增修订。\n"
    revised = sample_text.replace(changed_text, changed_text + insertion, 1)
    project_dir = settings.data_dir / first.json()["book"]["project_dir"]
    audio_path = project_dir / "audio" / "historical.wav"
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    audio_path.write_bytes(b"historical-audio")

    changed_segment = Segment(
        id=new_id("seg"), book_id=book_id, chapter_id=changed["id"], segment_index=0,
        source_text="旧片段", speech_text="旧片段", speaker_type="narrator",
        review_status="approved", audio_status="ready",
        source_start_offset=changed["source_start_offset"],
        source_end_offset=changed["source_end_offset"],
    )
    later_segment = Segment(
        id=new_id("seg"), book_id=book_id, chapter_id=later["id"], segment_index=0,
        source_text="后续旧片段", speech_text="后续旧片段", speaker_type="narrator",
        review_status="approved", audio_status="ready",
        source_start_offset=later["source_start_offset"] + 5,
        source_end_offset=later["source_end_offset"],
    )
    db_session.add_all([changed_segment, later_segment])
    db_session.flush()
    for segment in (changed_segment, later_segment):
        db_session.add(AudioAsset(
            id=new_id("audio"), segment_id=segment.id, version=1,
            file_path="audio/historical.wav", sample_rate=24000, duration_ms=1000,
        ))
    version = ScriptVersion(
        id=new_id("script"), book_id=book_id, chapter_id=changed["id"], version=1,
        status="approved", source_scene_version=0, is_active=True,
    )
    db_session.add(version)
    db_session.flush()
    db_session.add(ScriptUnit(
        id=new_id("unit"), book_id=book_id, chapter_id=changed["id"],
        script_version_id=version.id, unit_index=0, unit_type="narration",
        speaker_type="narrator", source_text="旧片段", text="旧片段",
        source_start_offset=changed["source_start_offset"],
        source_end_offset=changed["source_end_offset"],
        review_status="approved", audio_status="ready",
    ))
    db_session.commit()

    merged = upload(
        "/api/v1/books/import", revised, title="修订书",
        duplicate_policy="merge", replace_book_id=book_id,
    )
    assert merged.status_code == 201, merged.text
    payload = merged.json()
    assert payload["chapters_changed"] == 1
    assert payload["chapters_unchanged"] == 3
    assert payload["chapters_created"] == 0
    updated = {item["id"]: item for item in client.get(
        f"/api/v1/books/{book_id}/chapters"
    ).json()["items"]}
    assert set(updated) == {item["id"] for item in items}
    assert updated[changed["id"]]["analysis_status"] == "not_started"
    assert updated[later["id"]]["source_start_offset"] == later["source_start_offset"] + len(insertion)
    db_session.refresh(changed_segment)
    db_session.refresh(later_segment)
    db_session.refresh(version)
    assert changed_segment.audio_status == "stale"
    assert later_segment.audio_status == "ready"
    assert later_segment.source_start_offset == later["source_start_offset"] + 5 + len(insertion)
    assert version.is_active is False
    assert version.status == "superseded"
    unit = db_session.query(ScriptUnit).filter_by(script_version_id=version.id).one()
    assert unit.audio_status == "stale"
    assert db_session.query(AudioAsset).count() == 2
    assert audio_path.read_bytes() == b"historical-audio"
    normalized = (project_dir / "source" / "normalized.txt").read_text(encoding="utf-8")
    assert normalized == revised


def test_merge_full_revision_rejects_missing_old_chapters(
    client, upload, sample_text: str
) -> None:
    first = _import(upload, sample_text)
    book_id = first.json()["book"]["id"]
    partial = sample_text.split("第二章", 1)[0]
    response = upload(
        "/api/v1/books/import", partial, duplicate_policy="merge",
        replace_book_id=book_id,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "merge_missing_chapters"
    assert client.get(f"/api/v1/books/{book_id}/chapters").json()["total"] == 4


def test_replace_accepts_different_full_revision(upload, sample_text: str) -> None:
    first = _import(upload, sample_text, title="旧版")
    book_id = first.json()["book"]["id"]
    revised = sample_text + "\n\n第四章 新增\n\n覆盖导入正文。\n"
    response = upload(
        "/api/v1/books/import", revised, title="新版",
        duplicate_policy="replace", replace_book_id=book_id,
    )
    assert response.status_code == 201
    payload = response.json()
    assert payload["replaced"] is True
    assert payload["mode"] == "replace"
    assert payload["book"]["id"] == book_id
    assert payload["chapters_created"] == 5
