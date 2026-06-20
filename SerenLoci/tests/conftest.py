"""Shared fixtures: a tmp-backed store and TestClients (auth + no-auth)."""
from __future__ import annotations

import pytest

from seren_loci.config import LociConfig, ServerConfig, StorageConfig
from seren_loci.store import LociStore


@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "loci.db")


@pytest.fixture
def store(tmp_db):
    """A fresh embedding-free store (the floor) on a temp db."""
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None))
    s = LociStore(cfg)
    yield s
    s.close()


@pytest.fixture
def client(tmp_db, monkeypatch):
    """TestClient over a no-auth app on a temp db (env-driven db path)."""
    monkeypatch.setenv("SEREN_LOCI_DB_PATH", tmp_db)
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def auth_client(tmp_db):
    """TestClient over an app with a bearer token set."""
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    cfg = LociConfig(
        server=ServerConfig(bearer_token="sekret"),
        storage=StorageConfig(db_path=tmp_db, embedding_model=None),
    )
    with TestClient(create_app(cfg)) as c:
        yield c
