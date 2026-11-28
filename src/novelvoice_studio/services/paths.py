"""项目文件路径与原子写入。"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from novelvoice_studio.core.errors import AppError, ValidationError

_ID_PATTERN = re.compile(r"^[a-z]{2,8}_[0-9a-f]{16}$")
_BOOK_ID_PATTERN = re.compile(r"^book_[1-9][0-9]*$")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def validate_id(value: str, prefix: str | None = None) -> str:
    if prefix == "book" and _BOOK_ID_PATTERN.fullmatch(value or ""):
        return value
    if not _ID_PATTERN.fullmatch(value or ""):
        raise ValidationError("ID 格式无效")
    if prefix and not value.startswith(f"{prefix}_"):
        raise ValidationError(f"ID 前缀无效，应为 {prefix}_")
    return value


def relative_to_data(path: Path, data_dir: Path) -> str:
    try:
        return path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError as exc:
        raise AppError("文件路径不在数据目录内") from exc


def resolve_relative(data_dir: Path, relative: str) -> Path:
    candidate = (data_dir / relative).resolve()
    root = data_dir.resolve()
    if candidate != root and root not in candidate.parents:
        raise AppError("文件路径越界")
    return candidate


def atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    atomic_write_bytes(path, text.encode(encoding))


@dataclass(frozen=True)
class BookPaths:
    data_dir: Path
    book_id: str

    @property
    def root(self) -> Path:
        return self.data_dir / "books" / self.book_id

    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def parsed_dir(self) -> Path:
        return self.root / "parsed"

    @property
    def voices_dir(self) -> Path:
        return self.root / "voices"

    @property
    def audio_dir(self) -> Path:
        return self.root / "audio"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def original_path(self) -> Path:
        return self.source_dir / "original.txt"

    @property
    def normalized_path(self) -> Path:
        return self.source_dir / "normalized.txt"

    @property
    def manifest_path(self) -> Path:
        return self.parsed_dir / "manifest.json"

    def ensure(self) -> None:
        for directory in (
            self.source_dir,
            self.parsed_dir,
            self.voices_dir,
            self.audio_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def relative_original(self) -> str:
        return relative_to_data(self.original_path, self.data_dir)

    def relative_normalized(self) -> str:
        return relative_to_data(self.normalized_path, self.data_dir)

    def relative_root(self) -> str:
        return relative_to_data(self.root, self.data_dir)


def book_paths(data_dir: Path, book_id: str) -> BookPaths:
    return BookPaths(data_dir=data_dir, book_id=validate_id(book_id, "book"))