"""书籍 REST API。"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status

from novelvoice_studio.api.deps import (
    BookServiceDep,
    ChapterServiceDep,
    ImportServiceDep,
    SettingsDep,
)
from novelvoice_studio.api.uploads import read_upload
from novelvoice_studio.schemas.books import (
    BookCreate,
    BookDetail,
    BookListResponse,
    BookUpdate,
    ImportPreviewResponse,
    ImportResult,
)
from novelvoice_studio.schemas.chapters import ChapterListResponse

router = APIRouter(prefix="/books", tags=["books"])


@router.get("", response_model=BookListResponse)
def list_books(
    service: BookServiceDep,
    include_deleted: bool = Query(default=False),
    deleted_only: bool = Query(default=False),
) -> BookListResponse:
    items, total = service.list_books(
        include_deleted=include_deleted,
        deleted_only=deleted_only,
    )
    return BookListResponse(items=items, total=total)


@router.post("", response_model=BookDetail, status_code=status.HTTP_201_CREATED)
def create_book(payload: BookCreate, service: ImportServiceDep) -> BookDetail:
    return service.create_empty_book(payload)


@router.post("/import/preview", response_model=ImportPreviewResponse)
async def preview_import(
    settings: SettingsDep,
    service: ImportServiceDep,
    file: UploadFile = File(...),
    encoding: str | None = Form(default=None),
    custom_regex: str | None = Form(default=None),
) -> ImportPreviewResponse:
    data = await read_upload(file, settings)
    return service.preview(
        file.filename or "",
        data,
        encoding=encoding,
        custom_regex=custom_regex,
    )


@router.post("/import", response_model=ImportResult, status_code=status.HTTP_201_CREATED)
async def import_book(
    settings: SettingsDep,
    service: ImportServiceDep,
    file: UploadFile = File(...),
    encoding: str | None = Form(default=None),
    title: str | None = Form(default=None),
    author: str | None = Form(default=None),
    description: str | None = Form(default=None),
    language: str = Form(default="zh-CN"),
    custom_regex: str | None = Form(default=None),
    duplicate_policy: str = Form(default="new"),
    replace_book_id: str | None = Form(default=None),
) -> ImportResult:
    data = await read_upload(file, settings)
    return service.import_book(
        file.filename or "",
        data,
        encoding=encoding,
        title=title,
        author=author,
        description=description,
        language=language,
        custom_regex=custom_regex,
        duplicate_policy=duplicate_policy,
        replace_book_id=replace_book_id,
    )


@router.get("/{book_id}", response_model=BookDetail)
def get_book(book_id: str, service: BookServiceDep) -> BookDetail:
    return service.detail(service.get_book(book_id))


@router.patch("/{book_id}", response_model=BookDetail)
def update_book(book_id: str, payload: BookUpdate, service: BookServiceDep) -> BookDetail:
    return service.update(book_id, payload)


@router.delete("/{book_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_book(book_id: str, service: BookServiceDep) -> Response:
    service.soft_delete(book_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{book_id}/restore", response_model=BookDetail)
def restore_book(book_id: str, service: BookServiceDep) -> BookDetail:
    return service.restore(book_id)


@router.delete("/{book_id}/permanent", status_code=status.HTTP_204_NO_CONTENT)
def permanently_delete_book(book_id: str, service: BookServiceDep) -> Response:
    service.permanent_delete(book_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{book_id}/chapters", response_model=ChapterListResponse)
def list_chapters(
    book_id: str,
    books: BookServiceDep,
    chapters: ChapterServiceDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    kind: Literal["story", "extra", "author_note", "unclassified"] | None = None,
) -> ChapterListResponse:
    book = books.get_book(book_id)
    items, total = chapters.page_for_book(
        book, page=page, page_size=page_size, kind=kind
    )
    return ChapterListResponse(
        items=items, total=total, page=page, page_size=page_size
    )