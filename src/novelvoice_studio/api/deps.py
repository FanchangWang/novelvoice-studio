"""API 依赖：从 app.state 取配置/会话/引擎/Provider。"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.ai.registry import ProviderRegistry
from novelvoice_studio.config import Settings
from novelvoice_studio.services.analysis import AnalysisService
from novelvoice_studio.services.bible import BibleService
from novelvoice_studio.services.books import BookService
from novelvoice_studio.services.chapter_ops import ChapterService
from novelvoice_studio.services.character_ops import CharacterService
from novelvoice_studio.services.importer import ImportService
from novelvoice_studio.services.audio import AudioService
from novelvoice_studio.services.scenes import SceneService
from novelvoice_studio.services.segment_ops import SegmentService
from novelvoice_studio.services.voices import VoiceService


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_engine(request: Request) -> Engine:
    return request.app.state.engine


def get_registry(request: Request) -> ProviderRegistry:
    return request.app.state.registry


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    session: Session = factory()
    try:
        yield session
    finally:
        session.close()


def get_book_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> BookService:
    return BookService(settings, session)


def get_chapter_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> ChapterService:
    return ChapterService(settings, session)


def get_import_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> ImportService:
    return ImportService(settings, session)


def get_character_service(session: Session = Depends(get_session)) -> CharacterService:
    return CharacterService(session)


def get_segment_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
) -> SegmentService:
    return SegmentService(settings, session)


def get_analysis_service(
    settings: Settings = Depends(get_app_settings),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    registry: ProviderRegistry = Depends(get_registry),
) -> AnalysisService:
    return AnalysisService(settings, factory, registry)


def get_voice_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
    registry: ProviderRegistry = Depends(get_registry),
) -> VoiceService:
    return VoiceService(settings, session, registry)


def get_bible_service(
    settings: Settings = Depends(get_app_settings),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    registry: ProviderRegistry = Depends(get_registry),
) -> BibleService:
    return BibleService(settings, factory, registry)


def get_audio_service(
    settings: Settings = Depends(get_app_settings),
    session: Session = Depends(get_session),
    registry: ProviderRegistry = Depends(get_registry),
) -> AudioService:
    return AudioService(settings, session, registry)


def get_scene_service(
    settings: Settings = Depends(get_app_settings),
    factory: sessionmaker[Session] = Depends(get_session_factory),
    registry: ProviderRegistry = Depends(get_registry),
) -> SceneService:
    return SceneService(settings, factory, registry)


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
SessionDep = Annotated[Session, Depends(get_session)]
EngineDep = Annotated[Engine, Depends(get_engine)]
RegistryDep = Annotated[ProviderRegistry, Depends(get_registry)]
BookServiceDep = Annotated[BookService, Depends(get_book_service)]
ChapterServiceDep = Annotated[ChapterService, Depends(get_chapter_service)]
ImportServiceDep = Annotated[ImportService, Depends(get_import_service)]
CharacterServiceDep = Annotated[CharacterService, Depends(get_character_service)]
SegmentServiceDep = Annotated[SegmentService, Depends(get_segment_service)]
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
BibleServiceDep = Annotated[BibleService, Depends(get_bible_service)]
VoiceServiceDep = Annotated[VoiceService, Depends(get_voice_service)]
AudioServiceDep = Annotated[AudioService, Depends(get_audio_service)]
SceneServiceDep = Annotated[SceneService, Depends(get_scene_service)]