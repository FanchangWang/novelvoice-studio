"""v0.5.0 P4：有声剧本 API。"""

from __future__ import annotations

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from novelvoice_studio.api.deps import ScriptServiceDep
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.scripts import (
    ScriptStateResponse,
    ScriptStatusResponse,
    ScriptUnitOut,
    ScriptUnitSpeakerUpdate,
    ScriptVersionListResponse,
    ScriptVersionOut,
)

router = APIRouter(tags=["scripts"])


class ScriptStartIn(BaseModel):
    adaptation_level: str = Field(default="light", pattern="^(light|standard|strong)$")
    retry_scenes: bool = False


@router.post(
    "/chapters/{chapter_id}/script",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_script(
    chapter_id: str, service: ScriptServiceDep, payload: ScriptStartIn | None = None
) -> AnalysisJobOut:
    adaptation = payload.adaptation_level if payload is not None else "light"
    retry_scenes = payload.retry_scenes if payload is not None else False
    return service.start_script(chapter_id, adaptation, retry_scenes=retry_scenes)


@router.get("/chapters/{chapter_id}/script/status", response_model=ScriptStatusResponse)
def get_script_status(
    chapter_id: str, service: ScriptServiceDep
) -> ScriptStatusResponse:
    return service.script_status(chapter_id)


@router.get("/chapters/{chapter_id}/script", response_model=ScriptStateResponse)
def get_script(chapter_id: str, service: ScriptServiceDep) -> ScriptStateResponse:
    return service.get_script(chapter_id)


@router.get(
    "/chapters/{chapter_id}/script/versions", response_model=ScriptVersionListResponse
)
def get_script_versions(
    chapter_id: str, service: ScriptServiceDep
) -> ScriptVersionListResponse:
    return service.list_versions(chapter_id)


@router.post("/chapters/{chapter_id}/script/cancel", response_model=AnalysisJobOut)
def cancel_script(chapter_id: str, service: ScriptServiceDep) -> AnalysisJobOut:
    return service.cancel_script(chapter_id)


@router.patch("/script-units/{unit_id}/speaker", response_model=ScriptUnitOut)
def update_script_unit_speaker(
    unit_id: str, payload: ScriptUnitSpeakerUpdate, service: ScriptServiceDep
) -> ScriptUnitOut:
    return service.update_unit_speaker(unit_id, payload)


@router.post("/script-versions/{version_id}/approve", response_model=ScriptVersionOut)
def approve_script_version(
    version_id: str, service: ScriptServiceDep
) -> ScriptVersionOut:
    return service.activate_version(version_id, approve=True)


@router.post("/script-versions/{version_id}/rollback", response_model=ScriptVersionOut)
def rollback_script_version(
    version_id: str, service: ScriptServiceDep
) -> ScriptVersionOut:
    return service.activate_version(version_id, approve=False)