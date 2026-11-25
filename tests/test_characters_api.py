from __future__ import annotations

from novelvoice_studio.models import Character, CharacterRelationship, Segment
from novelvoice_studio.services.paths import new_id


def _import_book(client, upload, sample_text: str, title: str = "角色测试") -> str:
    response = upload("/api/v1/books/import", sample_text, title=title)
    assert response.status_code == 201
    return response.json()["book"]["id"]


def _add_character(
    session,
    book_id: str,
    name: str,
    *,
    aliases: list[dict] | None = None,
    candidates: list[dict] | None = None,
    locked: bool = False,
) -> Character:
    character = Character(
        id=new_id("char"),
        book_id=book_id,
        canonical_name=name,
        aliases_json=aliases or [],
        alias_candidates_json=candidates or [],
        gender="unknown",
        gender_basis="unknown",
        age_range="unknown",
        age_basis="unknown",
        character_type="unknown",
        personality_json=[],
        evidence_json=[],
        confidence=0.8,
        is_provisional=False,
        user_locked=locked,
        merge_log_json=[],
    )
    session.add(character)
    return character


def _add_relation(session, book_id: str, source_id: str, target_id: str, kind: str) -> None:
    session.add(
        CharacterRelationship(
            id=new_id("rel"),
            book_id=book_id,
            source_character_id=source_id,
            target_character_id=target_id,
            relationship_type=kind,
            description=f"{kind}关系",
            evidence_json=[],
            confidence=0.9,
            is_provisional=False,
        )
    )


def _add_segment(session, book_id: str, chapter_id: str, index: int, speaker_id: str) -> Segment:
    segment = Segment(
        id=new_id("seg"),
        book_id=book_id,
        chapter_id=chapter_id,
        segment_index=index,
        source_text=f"第{index}段",
        speech_text=f"第{index}段",
        segment_type="dialogue",
        speaker_type="character",
        speaker_id=speaker_id,
        speaker_name_snapshot="测试角色",
        speaker_confidence=0.9,
        emotion="neutral",
        emotion_confidence=0.9,
        intensity=1,
        delivery="normal",
        review_status="auto",
        audio_status="none",
        user_locked_fields_json=[],
        text_version=1,
    )
    session.add(segment)
    return segment


def test_update_character_aliases_and_lock(
    client, upload, sample_text: str, db_session
) -> None:
    book_id = _import_book(client, upload, sample_text)
    character = _add_character(
        db_session,
        book_id,
        "林见秋",
        aliases=[
            {
                "name": "小秋",
                "confidence": 0.8,
                "basis": "explicit",
                "chapter_id": None,
                "evidence": [],
                "source": "ai",
            }
        ],
        candidates=[
            {
                "name": "秋姐",
                "confidence": 0.4,
                "basis": "inferred",
                "chapter_id": None,
                "evidence": [],
                "source": "ai",
            }
        ],
    )
    other = _add_character(db_session, book_id, "沈砚")
    db_session.commit()

    response = client.patch(
        f"/api/v1/characters/{character.id}",
        json={
            "canonical_name": "林见秋修订",
            "aliases": ["小秋", "阿秋"],
            "gender": "female",
            "gender_basis": "explicit",
            "personality": ["冷静", "固执"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["canonical_name"] == "林见秋修订"
    assert [item["name"] for item in payload["aliases"]] == ["小秋", "阿秋", "林见秋"]
    assert payload["user_locked"] is True
    assert payload["gender"] == "female"
    assert payload["personality"] == ["冷静", "固执"]
    assert [item["name"] for item in payload["alias_candidates"]] == ["秋姐"]

    unused = client.patch(f"/api/v1/characters/{character.id}", json={"user_locked": False})
    assert unused.status_code == 200
    assert unused.json()["user_locked"] is False

    duplicate = client.patch(
        f"/api/v1/characters/{other.id}", json={"canonical_name": "林见秋修订"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "conflict"

    missing = client.patch(
        f"/api/v1/characters/{new_id('char')}", json={"gender": "male"}
    )
    assert missing.status_code == 404


def test_merge_and_split_restore_segments_and_relationships(
    client, upload, sample_text: str, db_session
) -> None:
    book_id = _import_book(client, upload, sample_text)
    chapter_id = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"][0]["id"]

    first = _add_character(
        db_session,
        book_id,
        "林见秋",
        aliases=[
            {
                "name": "小秋",
                "confidence": 1.0,
                "basis": "explicit",
                "chapter_id": None,
                "evidence": [],
                "source": "manual",
            }
        ],
    )
    second = _add_character(
        db_session,
        book_id,
        "沈砚",
        aliases=[
            {
                "name": "老沈",
                "confidence": 1.0,
                "basis": "explicit",
                "chapter_id": None,
                "evidence": [],
                "source": "manual",
            }
        ],
    )
    third = _add_character(db_session, book_id, "阿棠")
    _add_relation(db_session, book_id, first.id, second.id, "朋友")
    _add_relation(db_session, book_id, second.id, third.id, "邻居")
    _add_relation(db_session, book_id, first.id, third.id, "姐妹")
    _add_segment(db_session, book_id, chapter_id, 0, first.id)
    _add_segment(db_session, book_id, chapter_id, 1, second.id)
    db_session.commit()

    merged = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": first.id, "source_character_ids": [second.id]},
    )
    assert merged.status_code == 200
    body = merged.json()
    assert body["updated_segments"] == 1
    assert body["merged_source_ids"] == [second.id]
    assert body["target"]["merged_source_ids"] == [second.id]
    assert {item["name"] for item in body["target"]["aliases"]} == {"小秋", "沈砚", "老沈"}

    relationships = client.get(f"/api/v1/books/{book_id}/relationships").json()["items"]
    assert [(item["source_name"], item["target_name"], item["relationship_type"]) for item in relationships] == [
        ("林见秋", "阿棠", "姐妹")
    ]
    segments = client.get(f"/api/v1/chapters/{chapter_id}/segments").json()["items"]
    assert segments[1]["speaker_id"] == first.id
    assert segments[1]["speaker_name_snapshot"] == "林见秋"

    listed = client.get(
        f"/api/v1/books/{book_id}/characters", params={"include_merged": "true"}
    ).json()["items"]
    merged_source = next(item for item in listed if item["id"] == second.id)
    assert merged_source["merged_into_id"] == first.id

    split = client.post(
        "/api/v1/characters/split",
        json={"character_id": first.id, "source_character_id": second.id},
    )
    assert split.status_code == 200
    restored = split.json()
    assert restored["updated_segments"] == 1
    assert restored["restored"]["merged_into_id"] is None
    assert {item["name"] for item in restored["character"]["aliases"]} == {"小秋"}

    relationships = client.get(f"/api/v1/books/{book_id}/relationships").json()["items"]
    triples = {
        (item["source_name"], item["target_name"], item["relationship_type"])
        for item in relationships
    }
    assert triples == {
        ("林见秋", "沈砚", "朋友"),
        ("沈砚", "阿棠", "邻居"),
        ("林见秋", "阿棠", "姐妹"),
    }
    segments = client.get(f"/api/v1/chapters/{chapter_id}/segments").json()["items"]
    assert segments[1]["speaker_id"] == second.id
    assert segments[1]["speaker_name_snapshot"] == "沈砚"

    again = client.post(
        "/api/v1/characters/split",
        json={"character_id": first.id, "source_character_id": second.id},
    )
    assert again.status_code == 409


def test_merge_validation(client, upload, sample_text: str, db_session) -> None:
    book_id = _import_book(client, upload, sample_text, title="合并校验")
    other_book = _import_book(client, upload, sample_text, title="另一本书")
    first = _add_character(db_session, book_id, "甲")
    second = _add_character(db_session, book_id, "乙")
    child = _add_character(db_session, book_id, "丙")
    foreign = _add_character(db_session, other_book, "丁")
    db_session.commit()

    same = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": first.id, "source_character_ids": [first.id]},
    )
    assert same.status_code == 422

    duplicate = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": first.id, "source_character_ids": [second.id, second.id]},
    )
    assert duplicate.status_code == 422

    cross_book = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": first.id, "source_character_ids": [foreign.id]},
    )
    assert cross_book.status_code == 422
    assert cross_book.json()["error"]["code"] == "validation_error"

    nested = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": second.id, "source_character_ids": [child.id]},
    )
    assert nested.status_code == 200
    blocked = client.post(
        "/api/v1/characters/merge",
        json={"target_character_id": first.id, "source_character_ids": [second.id]},
    )
    assert blocked.status_code == 409
    assert "已合并" in blocked.json()["error"]["message"] or "包含已合并角色" in blocked.json()["error"]["message"]


def test_character_requires_valid_ids(client) -> None:
    assert client.get(f"/api/v1/characters/{new_id('char')}").status_code == 404
    assert client.get("/api/v1/characters/not-valid").status_code == 422
    assert client.get("/api/v1/books/not-valid/characters").status_code == 422