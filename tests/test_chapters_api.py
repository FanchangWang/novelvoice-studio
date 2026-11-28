from __future__ import annotations

from novelvoice_studio.models import Chapter, Segment
from novelvoice_studio.services.paths import new_id


def _import_sample(upload, sample_text: str, title: str = "章节测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _chapters(client, book_id: str) -> list[dict]:
    response = client.get(f"/api/v1/books/{book_id}/chapters")
    assert response.status_code == 200
    return response.json()["items"]


def _detail(client, chapter_id: str) -> dict:
    response = client.get(f"/api/v1/chapters/{chapter_id}")
    assert response.status_code == 200
    return response.json()


def test_chapter_offsets_cover_source(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    items = _chapters(client, book_id)
    assert items[0]["source_start_offset"] == 0
    assert items[-1]["source_end_offset"] == len(sample_text)
    assert sum(item["char_count"] for item in items) == len(sample_text)
    combined = "".join(_detail(client, item["id"])["text"] for item in items)
    assert combined == sample_text


def test_split_chapter(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    items = _chapters(client, book_id)
    target = items[1]
    detail = _detail(client, target["id"])
    assert len(detail["paragraphs"]) >= 3
    old_end = target["source_end_offset"]

    response = client.post(
        f"/api/v1/chapters/{target['id']}/split",
        json={"paragraph_index": 2, "new_title": "第一章 雨夜来客（续）"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"]["title"] == "第一章 雨夜来客（续）"
    updated = {item["id"]: item for item in payload["items"]}
    assert updated[target["id"]]["source_end_offset"] < old_end
    assert payload["chapter"]["source_start_offset"] == updated[target["id"]]["source_end_offset"]
    assert payload["chapter"]["source_end_offset"] == old_end
    assert [item["sequence"] for item in payload["items"]] == list(
        range(1, len(payload["items"]) + 1)
    )


def test_split_then_merge_with_later_chapters(client, upload, sample_text: str) -> None:
    """拆分后再合并，后续章节需要下移两位，回归唯一序号冲突问题。"""
    book_id = _import_sample(upload, sample_text)
    items = _chapters(client, book_id)
    target = items[1]
    split_response = client.post(
        f"/api/v1/chapters/{target['id']}/split",
        json={"paragraph_index": 2, "new_title": "拆分章"},
    )
    assert split_response.status_code == 200
    created = split_response.json()["chapter"]

    merge_response = client.post(
        "/api/v1/chapters/merge",
        json={"chapter_ids": [target["id"], created["id"]], "new_title": "合并回来的章"},
    )
    assert merge_response.status_code == 200
    payload = merge_response.json()
    assert payload["chapter"]["title"] == "合并回来的章"
    assert payload["chapter"]["source_start_offset"] == target["source_start_offset"]
    assert payload["chapter"]["source_end_offset"] == created["source_end_offset"]
    assert [item["sequence"] for item in payload["items"]] == list(
        range(1, len(payload["items"]) + 1)
    )
    assert len(payload["items"]) == len(items)


def test_split_invalid_index(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[0]
    for index in (0, 999):
        response = client.post(
            f"/api/v1/chapters/{chapter['id']}/split",
            json={"paragraph_index": index},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] in {"request_validation_error", "invalid_split_index"}


def test_merge_chapters(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    items = _chapters(client, book_id)
    first, second = items[1], items[2]
    response = client.post(
        "/api/v1/chapters/merge",
        json={"chapter_ids": [first["id"], second["id"]], "new_title": "合并章"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["chapter"]["title"] == "合并章"
    assert payload["chapter"]["source_start_offset"] == first["source_start_offset"]
    assert payload["chapter"]["source_end_offset"] == second["source_end_offset"]
    assert [item["sequence"] for item in payload["items"]] == [1, 2, 3]
    assert payload["affected_chapter_ids"][0] == payload["chapter"]["id"]
    assert len(payload["affected_chapter_ids"]) == 2


def test_merge_non_adjacent_rejected(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    items = _chapters(client, book_id)
    response = client.post(
        "/api/v1/chapters/merge",
        json={"chapter_ids": [items[0]["id"], items[2]["id"]]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_merge_duplicate_ids_rejected(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[0]
    response = client.post(
        "/api/v1/chapters/merge",
        json={"chapter_ids": [chapter["id"], chapter["id"]]},
    )
    assert response.status_code == 422


def test_rename_chapter(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[0]
    response = client.patch(
        f"/api/v1/chapters/{chapter['id']}",
        json={"title": "新的标题"},
    )
    assert response.status_code == 200
    assert response.json()["title"] == "新的标题"
    assert _detail(client, chapter["id"])["title"] == "新的标题"


def test_split_blocked_when_segments_exist(
    client, upload, sample_text: str, db_session
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[1]
    segment = Segment(
        id=new_id("seg"),
        book_id=book_id,
        chapter_id=chapter["id"],
        segment_index=0,
        source_text="已生成片段",
        speech_text="已生成片段",
        speaker_type="narrator",
        review_status="auto",
    )
    db_session.add(segment)
    db_session.commit()

    response = client.post(
        f"/api/v1/chapters/{chapter['id']}/split",
        json={"paragraph_index": 1},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "chapter_has_segments"


def test_chapter_not_found(client) -> None:
    assert client.get(f"/api/v1/chapters/{new_id('ch')}").status_code == 404
    assert client.get("/api/v1/chapters/not-valid").status_code == 422


def test_chapter_pagination_and_kind_filter(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    all_items = _chapters(client, book_id)
    assert len(all_items) >= 4

    first = client.get(
        f"/api/v1/books/{book_id}/chapters",
        params={"page": 2, "page_size": 2},
    )
    assert first.status_code == 200
    assert first.json()["total"] == len(all_items)
    assert first.json()["page"] == 2
    assert first.json()["page_size"] == 2
    assert [item["sequence"] for item in first.json()["items"]] == [3, 4]

    preface = client.get(
        f"/api/v1/books/{book_id}/chapters",
        params={"kind": "extra", "page_size": 20},
    )
    assert preface.status_code == 200
    assert preface.json()["items"]
    assert all(item["chapter_kind"] == "extra" for item in preface.json()["items"])


def test_chapter_filter_rejects_invalid_kind_and_page_size(client, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    assert client.get(
        f"/api/v1/books/{book_id}/chapters", params={"kind": "unknown"}
    ).status_code == 422
    assert client.get(
        f"/api/v1/books/{book_id}/chapters", params={"page_size": 101}
    ).status_code == 422


def test_unclassified_chapter_is_excluded_until_confirmed(
    client, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[1]

    unclassified = client.patch(
        f"/api/v1/chapters/{chapter['id']}",
        json={"chapter_kind": "unclassified"},
    )
    assert unclassified.status_code == 200
    assert unclassified.json()["chapter_kind"] == "unclassified"
    assert unclassified.json()["classification_status"] == "confirmed"
    assert unclassified.json()["analysis_included"] is False

    blocked = client.patch(
        f"/api/v1/chapters/{chapter['id']}",
        json={"analysis_included": True},
    )
    assert blocked.status_code == 422

    confirmed = client.patch(
        f"/api/v1/chapters/{chapter['id']}",
        json={"chapter_kind": "story", "analysis_included": True},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["classification_status"] == "confirmed"
    assert confirmed.json()["analysis_included"] is True


def test_chapter_offset_mismatch_detected(
    client, upload, sample_text: str, db_session
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapter = _chapters(client, book_id)[0]
    row = db_session.get(Chapter, chapter["id"])
    assert row is not None
    row.source_end_offset = len(sample_text) + 100
    db_session.commit()

    response = client.get(f"/api/v1/chapters/{chapter['id']}")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "source_offset_mismatch"


def test_special_chapter_classification_and_manual_include(
    client, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text)
    preface = _chapters(client, book_id)[0]
    assert preface["chapter_kind"] == "extra"
    assert preface["analysis_included"] is False

    excluded = client.post(f"/api/v1/chapters/{preface['id']}/analysis")
    assert excluded.status_code == 422
    assert excluded.json()["error"]["code"] == "chapter_excluded"

    included = client.patch(
        f"/api/v1/chapters/{preface['id']}",
        json={"chapter_kind": "story", "analysis_included": True},
    )
    assert included.status_code == 200
    assert included.json()["chapter_kind"] == "story"
    assert included.json()["analysis_included"] is True
