from __future__ import annotations

import time
from types import MethodType

from fastapi.testclient import TestClient

from novelvoice_studio.ai.base import (
    BibleArcDraft,
    BibleChapterResult,
    BibleCharacterDraft,
    BibleTimelineDraft,
    AliasDraft,
    EvidenceDraft,
    RelationshipDraft,
)
from novelvoice_studio.models import BookBible, Character, GenerationJob
from novelvoice_studio.services.analysis import recover_interrupted_analysis
from novelvoice_studio.services.bible import BibleService


def _import_sample(upload, sample_text: str, title: str = "角色圣经测试") -> str:
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


def test_book_bible_mock_pipeline(client: TestClient, upload, sample_text: str) -> None:
    book_id = _import_sample(upload, sample_text)
    response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert response.status_code == 202
    job = response.json()
    assert job["job_type"] == "book_bible"
    assert job["status"] in {"pending", "running"}

    conflict = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert conflict.status_code == 409

    status = _wait_bible(client, book_id)
    assert status["job"]["status"] == "completed"
    assert status["bible_status"] in {"approved", "needs_review"}

    state = client.get(f"/api/v1/books/{book_id}/bible").json()
    bible = state["bible"]
    assert bible is not None
    assert bible["is_active"] is True
    assert bible["version"] == 1
    assert bible["status"] == status["bible_status"]
    assert bible["stats"]["chapters"] == 4
    assert bible["stats"]["windows"] >= 4
    assert "全书共 4 章" in bible["summary"]
    names = {item["canonical_name"] for item in bible["characters"]}
    assert {"林见秋", "沈砚", "阿棠"} <= names
    for character in bible["characters"]:
        assert character["arcs"]
        assert character["arcs"][0]["age_stage"] in {
            "child",
            "teenager",
            "young_adult",
            "adult",
            "middle_aged",
            "elderly",
            "unknown",
        }
    assert bible["timeline"]["narrative_mode"] == "nonlinear"
    assert len(bible["timeline"]["chapters"]) == 4
    orders = [item["story_order"] for item in bible["timeline"]["chapters"]]
    assert orders == list(range(1, 5))

    books = client.get("/api/v1/books").json()["items"]
    current = next(item for item in books if item["id"] == book_id)
    assert current["bible_status"] == status["bible_status"]


def test_book_bible_rerun_versions_and_lock(
    client: TestClient,
    upload,
    sample_text: str,
    session_factory,
) -> None:
    book_id = _import_sample(upload, sample_text)
    first = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert first.status_code == 202
    _wait_bible(client, book_id)

    characters = client.get(f"/api/v1/books/{book_id}/characters").json()["items"]
    target = next(item for item in characters if item["canonical_name"] == "沈砚")
    patched = client.patch(
        f"/api/v1/characters/{target['id']}",
        json={
            "identity_description": "人工确认的身份：旧书店老板",
            "user_locked": True,
        },
    )
    assert patched.status_code == 200

    second = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert second.status_code == 202
    status = _wait_bible(client, book_id)
    assert status["job"]["status"] == "completed"
    assert status["version"] == 2

    state = client.get(f"/api/v1/books/{book_id}/bible").json()
    assert state["bible"]["version"] == 2
    assert state["bible"]["is_active"] is True
    assert status["open_reviews"] == len(state["bible"]["review_items"])
    locked = next(
        item for item in state["bible"]["characters"] if item["id"] == target["id"]
    )
    assert locked["user_locked"] is True
    assert locked["identity_description"] == "人工确认的身份：旧书店老板"

    with session_factory() as session:
        rows = list(
            session.query(BookBible)
            .filter(BookBible.book_id == book_id)
            .order_by(BookBible.version)
        )
        assert [row.version for row in rows] == [1, 2]
        assert rows[0].is_active is False
        assert rows[0].status == "superseded"
        assert rows[1].is_active is True
        first_chars = {
            row.canonical_name
            for row in session.query(Character).filter(Character.book_id == book_id)
        }
        assert "林见秋" in first_chars
        assert len(first_chars) == len(
            [item for item in characters if item["canonical_name"] in first_chars]
        )


def test_book_bible_selected_chapter_rerun(
    client: TestClient,
    upload,
    sample_text: str,
    app,
    session_factory,
) -> None:
    book_id = _import_sample(upload, sample_text)
    first = client.post(f"/api/v1/books/{book_id}/bible/analysis")
    assert first.status_code == 202
    _wait_bible(client, book_id)

    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    target = chapters[1]
    registry = app.state.registry
    extracted: list[int] = []
    original = registry.llm.extract_bible_characters

    def tracking(self, text, known_characters, context=None):
        extracted.append(int((context or {}).get("chapter_sequence") or 0))
        return original(text, known_characters, context)

    registry.llm.extract_bible_characters = MethodType(tracking, registry.llm)
    try:
        response = client.post(
            f"/api/v1/books/{book_id}/bible/analysis",
            json={"chapter_ids": [target["id"]]},
        )
        assert response.status_code == 202
        job = response.json()
        assert job["total_items"] == 1
        status = _wait_bible(client, book_id)
        assert status["job"]["status"] == "completed"
        assert status["version"] == 2
    finally:
        registry.llm.extract_bible_characters = original

    assert set(extracted) == {target["sequence"]}
    state = client.get(f"/api/v1/books/{book_id}/bible").json()
    bible = state["bible"]
    assert bible["version"] == 2
    assert bible["stats"]["chapters"] == 4
    assert bible["stats"]["analyzed_chapters"] == 1
    assert bible["stats"]["reused_chapters"] == 3
    assert len(bible["timeline"]["chapters"]) == 4
    names = {item["canonical_name"] for item in bible["characters"]}
    assert {"林见秋", "沈砚", "阿棠"} <= names

    with session_factory() as session:
        rows = list(
            session.query(BookBible)
            .filter(BookBible.book_id == book_id)
            .order_by(BookBible.version)
        )
        assert rows[0].status == "superseded"
        assert rows[1].is_active is True
        content = rows[1].content_json
        assert len(content["chapter_timelines"]) == 4
        assert content["base_bible_id"] == rows[0].id
        assert {
            row["chapter_id"] for row in content["chapter_timelines"]
        } == {chapter["id"] for chapter in chapters}


def test_book_bible_selected_rerun_requires_base(
    client: TestClient, upload, sample_text: str
) -> None:
    book_id = _import_sample(upload, sample_text)
    chapters = client.get(f"/api/v1/books/{book_id}/chapters").json()["items"]
    response = client.post(
        f"/api/v1/books/{book_id}/bible/analysis",
        json={"chapter_ids": [chapters[0]["id"]]},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "bible_base_missing"


def test_book_bible_multiple_arcs_by_age_stage(
    client: TestClient,
    upload,
    sample_text: str,
    app,
) -> None:
    book_id = _import_sample(upload, sample_text)
    registry = app.state.registry
    original_extract = registry.llm.extract_bible_characters
    original_merge = registry.llm.merge_bible_candidates

    def fake_extract(self, text, known_characters, context=None):
        sequence = int((context or {}).get("chapter_sequence") or 0)
        stage = "child" if sequence <= 2 else "elderly"
        return BibleChapterResult(
            characters=[
                BibleCharacterDraft(
                    name="林见秋",
                    age_range=stage,
                    arc_note="幼年时期" if stage == "child" else "晚年时期",
                    confidence=0.8,
                    role_tier="protagonist",
                )
            ],
            timeline=BibleTimelineDraft(
                story_time_label=f"第{sequence}章时间",
                time_mode="present",
                confidence=0.7,
            ),
            meta={"mock": True, "prompt_version": "test"},
        )

    def fake_merge(self, candidates, context=None):
        from novelvoice_studio.ai.base import BibleMergeResult

        return BibleMergeResult(
            characters=[],
            summary="不给出归并阶段，走确定性分组",
            meta={"mock": True},
        )

    registry.llm.extract_bible_characters = MethodType(fake_extract, registry.llm)
    registry.llm.merge_bible_candidates = MethodType(fake_merge, registry.llm)
    try:
        response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
        assert response.status_code == 202
        status = _wait_bible(client, book_id)
        assert status["job"]["status"] == "completed"
        state = client.get(f"/api/v1/books/{book_id}/bible").json()
        character = next(
            item
            for item in state["bible"]["characters"]
            if item["canonical_name"] == "林见秋"
        )
        assert len(character["arcs"]) == 2
        assert {arc["age_stage"] for arc in character["arcs"]} == {
            "child",
            "elderly",
        }
        first, second = character["arcs"]
        assert first["chapter_start_id"] is not None
        assert second["chapter_start_id"] is not None
        assert first["id"] != second["id"]
    finally:
        registry.llm.extract_bible_characters = original_extract
        registry.llm.merge_bible_candidates = original_merge


def test_book_bible_recovery_marks_interrupted(
    client: TestClient, upload, sample_text: str, session_factory
) -> None:
    book_id = _import_sample(upload, sample_text)
    with session_factory() as session:
        from novelvoice_studio.models import Book
        from novelvoice_studio.services.paths import new_id
        from novelvoice_studio.db.types import utc_now

        book = session.get(Book, book_id)
        assert book is not None
        book.bible_status = "running"
        bible = BookBible(
            id=new_id("bible"),
            book_id=book_id,
            version=1,
            status="running",
            source="ai",
            content_json={"status": "running"},
            timeline_json={},
            confidence=0.0,
            is_active=False,
        )
        session.add(bible)
        job = GenerationJob(
            id=new_id("job"),
            book_id=book_id,
            target_id=bible.id,
            target_type="book",
            stage="bible",
            job_type="book_bible",
            status="running",
            total_items=4,
            progress=0.25,
            params_json={"bible_id": bible.id},
            result_json={},
        )
        session.add(job)
        session.commit()

    recovered = recover_interrupted_analysis(session_factory)
    assert recovered["bible_jobs"] == 1
    assert recovered["bibles"] == 1
    status = client.get(f"/api/v1/books/{book_id}/bible/status").json()
    assert status["job"]["status"] == "failed"
    assert status["job"]["error_code"] == "interrupted"
    assert status["bible_id"] is None
    assert status["bible_status"] == "failed"


def test_book_bible_alias_and_relationship_cleanup(
    client: TestClient,
    upload,
    sample_text: str,
    app,
) -> None:
    book_id = _import_sample(upload, sample_text)
    registry = app.state.registry
    original_extract = registry.llm.extract_bible_characters
    original_merge = registry.llm.merge_bible_candidates

    def fake_extract(self, text, known_characters, context=None):
        sequence = int((context or {}).get("chapter_sequence") or 0)
        return BibleChapterResult(
            characters=[
                BibleCharacterDraft(
                    name="阿棠",
                    aliases=[
                        AliasDraft(name="小棠", confidence=0.9, basis="explicit"),
                        AliasDraft(name="沈砚的姐姐", confidence=0.9, basis="explicit"),
                        AliasDraft(name="林见秋的妹妹", confidence=0.9, basis="explicit"),
                        AliasDraft(name="林见秋姐姐", confidence=0.9, basis="explicit"),
                        AliasDraft(name="林见秋妹妹", confidence=0.9, basis="explicit"),
                        AliasDraft(name="林见秋", confidence=0.9, basis="explicit"),
                    ],
                    age_range="teenager",
                    confidence=0.8,
                    role_tier="minor",
                ),
                BibleCharacterDraft(
                    name="林见秋",
                    aliases=[
                        AliasDraft(name="姐姐", confidence=0.9, basis="explicit"),
                        AliasDraft(name="旧书店老板", confidence=0.9, basis="explicit"),
                    ],
                    age_range="adult",
                    confidence=0.8,
                    role_tier="protagonist",
                ),
            ],
            relationships=[
                RelationshipDraft(
                    source_name="阿棠",
                    target_name="林见秋",
                    relationship_type="sibling",
                    description="姐妹关系",
                    confidence=0.9,
                )
            ],
            timeline=BibleTimelineDraft(
                story_time_label=f"第{sequence}章时间",
                time_mode="present",
                confidence=0.7,
            ),
            meta={"mock": True, "prompt_version": "test"},
        )

    def fake_merge(self, candidates, context=None):
        from novelvoice_studio.ai.base import BibleMergeResult

        return BibleMergeResult(characters=[], meta={"mock": True})

    registry.llm.extract_bible_characters = MethodType(fake_extract, registry.llm)
    registry.llm.merge_bible_candidates = MethodType(fake_merge, registry.llm)
    try:
        response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
        assert response.status_code == 202
        status = _wait_bible(client, book_id)
        assert status["job"]["status"] == "completed"
        state = client.get(f"/api/v1/books/{book_id}/bible").json()
        bible = state["bible"]
        aliases = {
            item["canonical_name"]: [alias["name"] for alias in item["aliases"]]
            for item in bible["characters"]
        }
        assert aliases["阿棠"] == ["小棠"]
        assert aliases["林见秋"] == []
        for names in aliases.values():
            assert all("的" not in name for name in names)
        assert bible["relationships"]
        assert bible["relationships"][0]["relationship_type"] == "亲属"

        characters = client.get(f"/api/v1/books/{book_id}/characters").json()["items"]
        for item in characters:
            for alias in item["aliases"]:
                assert "的" not in alias["name"]
            for candidate in item["alias_candidates"]:
                assert "的" not in candidate["name"]
    finally:
        registry.llm.extract_bible_characters = original_extract
        registry.llm.merge_bible_candidates = original_merge


def test_book_bible_windows_and_alias_conflict(
    client: TestClient,
    upload,
    sample_text: str,
    settings,
    app,
) -> None:
    settings.analysis.max_chapter_chars = 180
    book_id = _import_sample(upload, sample_text)
    registry = app.state.registry

    original = registry.llm.extract_bible_characters
    original_merge = registry.llm.merge_bible_candidates

    def fake_extract(self, text, known_characters, context=None):
        sequence = int((context or {}).get("chapter_sequence") or 0)
        if sequence == 1:
            characters = [
                BibleCharacterDraft(
                    name="沈砚",
                    aliases=[
                        AliasDraft(
                            name="老沈",
                            confidence=0.9,
                            basis="explicit",
                            evidence=[EvidenceDraft(quote="人称老沈")],
                        )
                    ],
                    age_range="young_adult",
                    confidence=0.8,
                    role_tier="protagonist",
                ),
                BibleCharacterDraft(
                    name="林见秋",
                    aliases=[
                        AliasDraft(
                            name="老沈",
                            confidence=0.9,
                            basis="explicit",
                            evidence=[EvidenceDraft(quote="人称老沈")],
                        )
                    ],
                    age_range="young_adult",
                    confidence=0.8,
                    role_tier="protagonist",
                ),
            ]
        else:
            characters = []
        relationships = []
        if sequence == 1:
            relationships = [
                RelationshipDraft(
                    source_name="沈砚",
                    target_name="林见秋",
                    relationship_type="旧识",
                    description="沈砚带着钥匙来找林见秋。",
                    confidence=0.8,
                )
            ]
        return BibleChapterResult(
            characters=characters,
            relationships=relationships,
            timeline=BibleTimelineDraft(
                story_time_label=f"第{sequence}章时间",
                time_mode="present",
                confidence=0.7,
            ),
            meta={"mock": True, "prompt_version": "test"},
        )

    def fake_merge(self, candidates, context=None):
        from novelvoice_studio.ai.base import (
            BibleMergeCharacterDraft,
            BibleMergeResult,
        )

        return BibleMergeResult(
            characters=[
                BibleMergeCharacterDraft(
                    name=item["name"],
                    role_tier="protagonist",
                    arcs=[
                        BibleArcDraft(
                            label="青年",
                            age_stage="young_adult",
                            chapter_start=1,
                            chapter_end=4,
                            summary=f"{item['name']}的青年阶段",
                            confidence=0.7,
                        )
                    ],
                    confidence=0.7,
                )
                for item in candidates
            ],
            summary="测试归并摘要",
            notes=["阿尔法别名疑似指向两个人"],
            meta={"mock": True},
        )

    registry.llm.extract_bible_characters = MethodType(fake_extract, registry.llm)
    registry.llm.merge_bible_candidates = MethodType(fake_merge, registry.llm)
    try:
        response = client.post(f"/api/v1/books/{book_id}/bible/analysis")
        assert response.status_code == 202
        status = _wait_bible(client, book_id)
        assert status["job"]["status"] == "completed"
        state = client.get(f"/api/v1/books/{book_id}/bible").json()
        bible = state["bible"]
        assert bible["stats"]["windows"] > bible["stats"]["chapters"]
        assert status["bible_status"] == "needs_review"
        codes = {item["code"] for item in bible["review_items"]}
        assert any(code.startswith("alias_conflict:") for code in codes)
        assert bible["review_items"]
        assert bible["relationships"]
        assert bible["relationships"][0]["source_name"] == "沈砚"
        assert bible["relationships"][0]["target_name"] == "林见秋"
    finally:
        registry.llm.extract_bible_characters = original
        registry.llm.merge_bible_candidates = original_merge