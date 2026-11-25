"""上传读取：分块读取并提前执行大小限制。"""

from __future__ import annotations

from fastapi import UploadFile

from novelvoice_studio.config import Settings
from novelvoice_studio.core.errors import ValidationError

CHUNK_SIZE = 1024 * 1024


async def read_upload(file: UploadFile, settings: Settings) -> bytes:
    limit = max(1, settings.app.max_upload_mb) * 1024 * 1024
    try:
        if file.size is not None and file.size > limit:
            raise ValidationError(
                f"文件超过大小限制（{settings.app.max_upload_mb} MB）",
                status_code=413,
                code="file_too_large",
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > limit:
                raise ValidationError(
                    f"文件超过大小限制（{settings.app.max_upload_mb} MB）",
                    status_code=413,
                    code="file_too_large",
                )
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await file.close()