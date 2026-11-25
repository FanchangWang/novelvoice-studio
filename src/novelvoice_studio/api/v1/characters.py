"""角色列表、人工编辑、合并与拆分 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query

from novelvoice_studio.api.deps import CharacterServiceDep
from novelvoice_studio.schemas.analysis import (
    CharacterListResponse,
    CharacterMergeRequest,
    CharacterMergeResponse,
    CharacterOut,
    CharacterSplitRequest,
    CharacterSplitResponse,
    CharacterUpdate,
    RelationshipListResponse,
)

router = APIRouter(tags=["characters"])


@router.get("/books/{book_id}/characters", response_model=CharacterListResponse)
def list_characters(
    book_id: str,
    service: CharacterServiceDep,
    include_merged: bool = Query(default=False),
) -> CharacterListResponse:
    return service.list_characters(book_id, include_merged=include_merged)


@router.get("/books/{book_id}/relationships", response_model=RelationshipListResponse)
def list_relationships(
    book_id: str, service: CharacterServiceDep
) -> RelationshipListResponse:
    return service.list_relationships(book_id)


@router.post("/characters/merge", response_model=CharacterMergeResponse)
def merge_characters(
    payload: CharacterMergeRequest, service: CharacterServiceDep
) -> CharacterMergeResponse:
    return service.merge(payload)


@router.post("/characters/split", response_model=CharacterSplitResponse)
def split_character(
    payload: CharacterSplitRequest, service: CharacterServiceDep
) -> CharacterSplitResponse:
    return service.split(payload.character_id, payload.source_character_id)


@router.get("/characters/{character_id}", response_model=CharacterOut)
def get_character(character_id: str, service: CharacterServiceDep) -> CharacterOut:
    return service.get_character(character_id)


@router.patch("/characters/{character_id}", response_model=CharacterOut)
def update_character(
    character_id: str, payload: CharacterUpdate, service: CharacterServiceDep
) -> CharacterOut:
    return service.update_character(character_id, payload)