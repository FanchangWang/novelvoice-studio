"""v0.5.0 P5：全局配音员库与书内自动/手动选角 API。"""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status
from fastapi.responses import FileResponse

from novelvoice_studio.api.deps import CastingServiceDep
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.casting import (
    BookCastsResponse,
    CastAssignRequest,
    CastingStatusResponse,
    GeneralVoicePresetListOut,
    VoiceActorCreateRequest,
    VoiceActorListResponse,
    VoiceActorOut,
    VoiceActorUpdateRequest,
)
from novelvoice_studio.schemas.voice import VoicePreviewRequest

router = APIRouter(tags=["casting"])


@router.get("/voice-actors", response_model=VoiceActorListResponse)
def list_voice_actors(
    service: CastingServiceDep,
    q: str | None = Query(default=None, max_length=120),
    favorite: bool | None = None,
    actor_status: str | None = Query(
        default=None, alias="status", pattern="^(draft|ready|failed|archived)$"
    ),
    scope: str | None = Query(default=None, pattern="^(general|book)$"),
    book_id: str | None = Query(default=None, max_length=32),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> VoiceActorListResponse:
    return service.list_actors(
        query=q,
        favorite=favorite,
        status=actor_status,
        scope=scope,
        book_id=book_id,
        limit=limit,
        offset=offset,
    )


@router.get("/voice-actors/general-presets", response_model=GeneralVoicePresetListOut)
def list_general_voice_presets(service: CastingServiceDep) -> GeneralVoicePresetListOut:
    return service.list_general_presets()


@router.post(
    "/voice-actors/general-presets/{preset_id}/generate",
    response_model=VoiceActorOut,
    status_code=status.HTTP_201_CREATED,
)
def generate_general_voice_preset(
    preset_id: str, service: CastingServiceDep
) -> VoiceActorOut:
    return service.generate_general_preset(preset_id)


@router.post(
    "/voice-actors",
    response_model=VoiceActorOut,
    status_code=status.HTTP_201_CREATED,
)
def create_voice_actor(
    payload: VoiceActorCreateRequest, service: CastingServiceDep
) -> VoiceActorOut:
    return service.create_actor(payload)


@router.get("/voice-actors/{actor_id}", response_model=VoiceActorOut)
def get_voice_actor(actor_id: str, service: CastingServiceDep) -> VoiceActorOut:
    return service.get_actor(actor_id)


@router.patch("/voice-actors/{actor_id}", response_model=VoiceActorOut)
def update_voice_actor(
    actor_id: str, payload: VoiceActorUpdateRequest, service: CastingServiceDep
) -> VoiceActorOut:
    return service.update_actor(actor_id, payload)


@router.get("/voice-actors/{actor_id}/audio")
def voice_actor_audio(actor_id: str, service: CastingServiceDep) -> FileResponse:
    path = service.actor_audio_path(actor_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{actor_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.post("/voice-actors/{actor_id}/preview")
def preview_voice_actor(
    actor_id: str, payload: VoicePreviewRequest, service: CastingServiceDep
) -> Response:
    audio = service.preview_actor(actor_id, payload)
    return Response(
        content=audio.audio_bytes,
        media_type="audio/wav",
        headers={
            "Cache-Control": "no-store",
            "X-NVS-Duration-Ms": str(audio.duration_ms),
            "X-NVS-Sample-Rate": str(audio.sample_rate),
            "X-NVS-Provider": audio.provider,
            "X-NVS-Is-Mock": "true" if audio.is_mock else "false",
        },
    )


@router.get("/books/{book_id}/casts", response_model=BookCastsResponse)
def get_book_casts(book_id: str, service: CastingServiceDep) -> BookCastsResponse:
    return service.book_casts(book_id)


@router.get(
    "/books/{book_id}/casts/status", response_model=CastingStatusResponse
)
def get_casting_status(
    book_id: str, service: CastingServiceDep
) -> CastingStatusResponse:
    return service.casting_status(book_id)


@router.post(
    "/books/{book_id}/casts/auto",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_casting(book_id: str, service: CastingServiceDep) -> AnalysisJobOut:
    return service.start_casting(book_id)


@router.post("/books/{book_id}/casts/cancel", response_model=AnalysisJobOut)
def cancel_casting(book_id: str, service: CastingServiceDep) -> AnalysisJobOut:
    return service.cancel_casting(book_id)


@router.post("/books/{book_id}/casts/assign", response_model=BookCastsResponse)
def assign_cast(
    book_id: str, payload: CastAssignRequest, service: CastingServiceDep
) -> BookCastsResponse:
    return service.assign_cast(book_id, payload)