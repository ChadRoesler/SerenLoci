"""Shared fixtures: a tmp-backed store and TestClients (auth + no-auth)."""
from __future__ import annotations

import hashlib
import math

import pytest

from seren_loci import store as store_mod
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


# ── hybrid (vector + FTS5) fixtures ──────────────────────────────────────────
#
# The `store` fixture above is the embedding-free FLOOR. The score-contract
# saturation bug (a lexical near-miss reaching the exact-key 1.0) lives in the
# HYBRID normalization, so the stopword-bleed reproduction needs a store WITH a
# finder. We inject a deterministic, torch-free stub through the same module
# seam test_hybrid_finder.py uses, so CI never drags torch in.


class _StubEmbedder:
    """Deterministic embedder: (model_name, text) -> fixed 4-dim vector.

    Mirrors the stub in test_hybrid_finder.py. HASH-based, so NOT semantically
    meaningful: a lone doc still tops both lanes (the atomic proof holds), but a
    multi-fact leaf test that needs the vector lane to AGREE with FTS on the same
    rank-0 may need a semantically-sane embedder or real MiniLM to go red.
    """

    def __init__(self, name: str):
        self._name = name
        self._dim = 4

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            seed = hashlib.sha256(f"{self._name}|{t}".encode()).digest()
            vals = [seed[i % len(seed)] / 255.0 for i in range(self._dim)]
            if normalize_embeddings:
                n = math.sqrt(sum(v * v for v in vals)) or 1.0
                vals = [v / n for v in vals]
            out.append(vals)
        return out


@pytest.fixture
def hybrid_store(tmp_db, monkeypatch):
    """A store with a hybrid finder (vector + FTS5 via RRF), using the torch-free
    stub embedder. This is the path where the RRF->1.0 normalization can violate
    the exact-key 1.0 contract; the stopword-bleed reproduction tests run against
    this, not the embedding-free `store`."""
    monkeypatch.setattr(
        store_mod, "_load_embedder",
        lambda name, device, cache_folder=None: _StubEmbedder(name),
    )
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model="stub-4"))
    s = LociStore(cfg)
    yield s
    s.close()
