"""ORM 模型集中导出，供 Alembic 收集 metadata。"""

from novelvoice_studio.db.base import Base
from novelvoice_studio.models.book import Book, Chapter
from novelvoice_studio.models.character import Character, CharacterRelationship
from novelvoice_studio.models.job import GenerationJob
from novelvoice_studio.models.playback import PlaybackState
from novelvoice_studio.models.segment import AudioAsset, Segment
from novelvoice_studio.models.voice import VoiceProfile

__all__ = [
    "AudioAsset",
    "Base",
    "Book",
    "Chapter",
    "Character",
    "CharacterRelationship",
    "GenerationJob",
    "PlaybackState",
    "Segment",
    "VoiceProfile",
]