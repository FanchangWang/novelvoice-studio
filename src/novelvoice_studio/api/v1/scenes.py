"""v0.5.0 P3：逐章分场与角色状态快照 API。"""

from __future__ import annotations

from fastapi import APIRouter, status

from novelvoice_studio.api.deps import SceneServiceDep
from novelvoice_studio.schemas.analysis import AnalysisJobOut
from novelvoice_studio.schemas.scenes import SceneStateResponse, SceneStatusResponse

router = APIRouter(prefix="/chapters", tags=["scenes"])


@router.post(
    "/{chapter_id}/scenes",
    response_model=AnalysisJobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def start_scene_analysis(
    chapter_id: str, service: SceneServiceDep
) -> AnalysisJobOut:
    return service.start_scene_analysis(chapter_id)


@router.get("/{chapter_id}/scenes/status", response_model=SceneStatusResponse)
def get_scene_status(
    chapter_id: str, service: SceneServiceDep
) -> SceneStatusResponse:
    return service.scene_status(chapter_id)


@router.get("/{chapter_id}/scenes", response_model=SceneStateResponse)
def get_scenes(chapter_id: str, service: SceneServiceDep) -> SceneStateResponse:
    return service.get_scenes(chapter_id)


@router.post(
    "/{chapter_id}/scenes/cancel", response_model=AnalysisJobOut
)
def cancel_scene_analysis(
    chapter_id: str, service: SceneServiceDep
) -> AnalysisJobOut:
    return service.cancel_scene_analysis(chapter_id)