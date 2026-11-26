"""v0.5.0 P7：剧本单元音频生成、试听与版本 API。"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import FileResponse

from novelvoice_studio.api.deps import UnitAudioServiceDep
from novelvoice_studio.schemas.unit_audio import (
    ScriptUnitAudioOut,
    UnitAudioVersionListResponse,
)

router = APIRouter(tags=["unit-audio"])


@router.post("/script-units/{unit_id}/generate", response_model=ScriptUnitAudioOut)
def generate_unit_audio(
    unit_id: str, service: UnitAudioServiceDep
) -> ScriptUnitAudioOut:
    return service.generate(unit_id)


@router.get(
    "/script-units/{unit_id}/audio-versions",
    response_model=UnitAudioVersionListResponse,
)
def list_unit_audio_versions(
    unit_id: str, service: UnitAudioServiceDep
) -> UnitAudioVersionListResponse:
    return service.versions(unit_id)


@router.get("/script-units/{unit_id}/audio")
def current_unit_audio(
    unit_id: str, service: UnitAudioServiceDep
) -> FileResponse:
    path = service.current_audio_path(unit_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=f"{unit_id}.wav",
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@router.post(
    "/script-units/{unit_id}/audio-versions/{asset_id}/restore",
    response_model=ScriptUnitAudioOut,
)
def restore_unit_audio_version(
    unit_id: str, asset_id: str, service: UnitAudioServiceDep
) -> ScriptUnitAudioOut:
    return service.restore(unit_id, asset_id)