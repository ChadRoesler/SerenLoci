"""
A store past sqlite-vec's 4,096. 20 Oct 2026.

sqlite-vec answers a KNN for at most 4,096 rows and refuses a query that asks
for more ("k value in knn query too large"). The vector search asked for every
row the index held, capped at a ceiling of 10,000, so a store past 4,096
vectors - two lanes a fact: about 2,048 facts with whys - failed every search,
and the Callosum dropped Loci as a failed store. Probe found it on a store of
2,822 facts (5,644 vectors): Loci 0.00 where BM25 over the same facts scored
1.00.

A ceiling of 4,096 would stop the crash and bring back the miss that asking for
every row was there to prevent: the project filter runs after the KNN, so a
scoped search would see only the nearest 4,096 and lose a project whose facts
all sit past them. And every search is scoped - one with no project searches
every project that has facts. So past 4,096 the search measures the in-scope
vectors itself. Pinned:

- a store past 4,096 vectors answers a search
- a scoped search past 4,096 finds a project whose every vector is farther
  than 4,096 of another project's
- the measured path answers exactly what the KNN does, where both can answer
"""
from __future__ import annotations

import hashlib
import math

import pytest

pytest.importorskip("sqlite_vec")

from seren_loci import store as store_mod
from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactWrite, FUNDAMENTALS
from seren_loci.store import LociStore

DIM = 8
CROWD = 2049  # two lanes each: 4,098 vectors, plus the lone fact's two


class _PoleEmbedder:
    """'beacon' and 'lighthouse' sit at opposite poles, so a test can put
    every vector of one project farther from the query than all of another's.
    Any other text gets a direction of its own from its hash, so distances
    don't tie and two paths' answers can be compared in order."""

    def __init__(self, name: str):
        self._name = name

    def get_sentence_embedding_dimension(self) -> int:
        return DIM

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            if "beacon" in t:
                v = [1.0] + [0.0] * (DIM - 1)
            elif "lighthouse" in t:
                v = [-1.0] + [0.0] * (DIM - 1)
            else:
                v = [b / 255.0 - 0.5 for b in hashlib.sha256(t.encode()).digest()[:DIM]]
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def _open(path) -> LociStore:
    return LociStore(LociConfig(storage=StorageConfig(db_path=str(path), embedding_model="poles")))


@pytest.fixture(scope="module")
def big(tmp_path_factory):
    """One store past the 4,096, built once (2,050 writes take a few seconds):
    a crowd of facts at the 'beacon' pole, and one fact of another project at
    the far pole, behind every crowd vector."""
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store_mod, "_load_embedder", lambda name, device, cache_folder=None: _PoleEmbedder(name))
        s = _open(tmp_path_factory.mktemp("big") / "loci.db")
        for i in range(CROWD):
            s.set_fact(FactWrite(project="crowd", key=f"signal-{i}", value="beacon", why=f"watch {i}"))
        s.set_fact(FactWrite(project="lone", key="keeper", value="the lighthouse keeper", why="lighthouse"))
        assert s._conn.execute("SELECT COUNT(*) FROM facts_vec").fetchone()[0] > store_mod._VEC0_K_MAX
        yield s
        s.close()


@pytest.fixture
def small(tmp_db, monkeypatch):
    monkeypatch.setattr(store_mod, "_load_embedder", lambda name, device, cache_folder=None: _PoleEmbedder(name))
    s = _open(tmp_db)
    for project in ("tide", "harbour", FUNDAMENTALS):
        for i in range(6):
            s.set_fact(FactWrite(project=project, key=f"note-{i}", value=f"{project} value {i}",
                                 why=f"{project} reason {i}" if i % 2 else None))
    yield s
    s.close()


# ── past the 4,096 ──────────────────────────────────────────────────────────

def test_a_store_past_4096_vectors_answers_a_search(big):
    hits, kind = big.search("beacon", n_results=5)
    assert kind == "hybrid"
    assert len(hits) == 5 and {h.project for h in hits} == {"crowd"}


def test_a_scoped_search_past_4096_finds_a_project_behind_the_crowd(big):
    lone = big.get_fact("lone", "keeper")
    # 'lone: beacon' is the 'beacon' pole; both of lone's vectors are at the
    # other one, farther than all 4,098 of the crowd's
    near = big._finder._vector_search_augmented("beacon", 3, ["lone", FUNDAMENTALS])
    assert [fid for fid, _ in near] == [lone.id]
    assert near[0][1] == pytest.approx(2.0)


# ── the two paths agree ─────────────────────────────────────────────────────

@pytest.mark.parametrize("scopes", [None, ["tide", FUNDAMENTALS], ["tide", "harbour"], ["harbour"]])
def test_measuring_answers_what_the_knn_answers(small, monkeypatch, scopes):
    knn = small._finder._vector_search_augmented("value 3", 5, scopes)
    # an index 'too big for vec0' at any size: the search measures instead
    monkeypatch.setattr(store_mod, "_VEC0_K_MAX", 1)
    measured = small._finder._vector_search_augmented("value 3", 5, scopes)
    assert [fid for fid, _ in measured] == [fid for fid, _ in knn]
    assert [d for _, d in measured] == pytest.approx([d for _, d in knn])
    assert len(knn) == 5
