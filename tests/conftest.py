from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from novelvoice_studio.api.app import create_app
from novelvoice_studio.config import AppConfig, ProvidersConfig, Settings
from novelvoice_studio.db.migrate import run_migrations

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def sample_text() -> str:
    return (FIXTURES / "sample_novel.txt").read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    result = Settings(
        app=AppConfig(
            data_dir=str(tmp_path / "data"),
            auto_migrate=False,
            log_level="WARNING",
            max_upload_mb=2,
            frontend_dir=str(tmp_path / "missing-frontend"),
        ),
        providers=ProvidersConfig(mode="mock"),
    )
    run_migrations(result, backup=False)
    return result


@pytest.fixture
def app(settings: Settings):  # type: ignore[no-untyped-def]
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:  # type: ignore[no-untyped-def]
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def session_factory(app) -> sessionmaker[Session]:  # type: ignore[no-untyped-def]
    return app.state.session_factory


@pytest.fixture
def db_session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def upload(client: TestClient) -> Callable[..., object]:
    def _upload(
        url: str,
        text: str = "",
        *,
        data: bytes | None = None,
        filename: str = "sample.txt",
        encoding: str = "utf-8",
        **form: str,
    ):
        payload = data if data is not None else text.encode(encoding)
        files = {"file": (filename, payload, "text/plain")}
        return client.post(url, files=files, data=form or None)

    return _upload


def import_book(
    upload: Callable[..., object],
    text: str,
    *,
    title: str = "测试小说",
    encoding: str = "utf-8",
    **form: str,
):
    return upload(
        "/api/v1/books/import",
        text,
        encoding=encoding,
        title=title,
        **form,
    )