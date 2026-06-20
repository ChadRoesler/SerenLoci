"""
Embedder reconcile: the vector index is a DERIVED cache over canonical fact
text, so changing the embedder is a lossless REBUILD, never a migration. These
tests prove the three ways the index can fall out of sync with the configured
embedder all heal on the next boot:

  1. dim change          (model @4 -> model @8): vec0 FLOAT[n] can't hold the
                         new width; the table must be dropped + recreated.
  2. model change, same  (model A @4 -> model B @4): old vectors are a different
     dim                  space; every live fact must be re-encoded.
  3. enable-after-seed   (floor, seed N facts, THEN turn the embedder on): the
                         pre-existing facts were never indexed -> must backfill.
                         This is the seed-on-the-floor-then-light-up-vector path.

Plus: same embedder across a reboot rebuilds NOTHING (cheap backfill path), and
an incremental supersede after a reconcile keeps the index live-only.

Real sqlite-vec, stub embedder. We monkeypatch seren_loci.store._load_embedder
(the seam that exists for exactly this - the 'make the encoder injectable'
nicety test_vector_sql.py used to file) so no torch is pulled. The stub maps a
model NAME to a dim + deterministic vectors, so 'a different model' genuinely
means 'a different vector space'.
"""
from __future__ import annotations

import hashlib
import math

import pytest

from seren_loci import store as store_mod
from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactWrite
from seren_loci.store import LociStore

pytest.importorskip("sqlite_vec")


# ── a deterministic, torch-free stub embedder ───────────────────────────────

# model name -> embedding dim. Two distinct 4-dim models let us test a same-dim
# model SWAP; the 8-dim model tests a dim change.
_MODEL_DIMS = {"stub-a-4": 4, "stub-b-4": 4, "stub-8": 8}


class _StubEmbedder:
    """Quacks like a SentenceTransformer: a dim + a deterministic encode().
    The vector is derived from (model_name, text), so different models produce
    different spaces - the whole point of the model-change test."""

    def __init__(self, name: str):
        self._name = name
        self._dim = _MODEL_DIMS[name]

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


@pytest.fixture(autouse=True)
def _stub_embedder(monkeypatch):
    """Inject the stub through the module seam for every test in this file."""
    monkeypatch.setattr(store_mod, "_load_embedder",
                        lambda name, device: _StubEmbedder(name))


# ── helpers ─────────────────────────────────────────────────────────────────

def _open(tmp_db, model):
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=model))
    return LociStore(cfg)


def _vec_count(store) -> int:
    return store._conn.execute("SELECT COUNT(*) c FROM facts_vec").fetchone()["c"]


def _vec_dim(store) -> int:
    sql = store._conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='facts_vec'"
    ).fetchone()["sql"]
    return int(sql.split("FLOAT[")[1].split("]")[0])


def _stamp(store):
    row = store._conn.execute(
        "SELECT value FROM loci_meta WHERE key='vec_embedder'"
    ).fetchone()
    return row["value"] if row else None


def _seed(store, n=3):
    for i in range(n):
        store.set_fact(FactWrite(key=f"k{i}", value=f"v{i}", why=f"because {i}"))


# ── 1. dim change ───────────────────────────────────────────────────────────

def test_dim_change_rebuilds_at_new_width(tmp_db):
    s = _open(tmp_db, "stub-a-4")
    _seed(s, 3)
    assert _vec_dim(s) == 4
    assert _vec_count(s) == 3
    assert _stamp(s) == "stub-a-4::4"
    s.close()

    # reopen with an 8-dim model: vec0 FLOAT[4] can't hold it -> drop + recreate
    s = _open(tmp_db, "stub-8")
    assert _vec_dim(s) == 8                    # table recreated at the new width
    assert _vec_count(s) == 3                  # all live facts re-encoded
    assert _stamp(s) == "stub-8::8"
    hits, kind = s.search("k1", n_results=5)   # and search doesn't dimension-crash
    assert kind == "vector"
    assert any(h.key == "k1" for h in hits)
    s.close()


# ── 2. model change, same dim ───────────────────────────────────────────────

def test_same_dim_model_change_reencodes(tmp_db):
    s = _open(tmp_db, "stub-a-4")
    _seed(s, 2)
    assert _stamp(s) == "stub-a-4::4"
    s.close()

    s = _open(tmp_db, "stub-b-4")              # same width, different space
    assert _vec_dim(s) == 4
    assert _vec_count(s) == 2
    # stamp advanced -> the rebuild path ran (every fact re-encoded with B),
    # not the backfill path that would have left stale A-vectors in place.
    assert _stamp(s) == "stub-b-4::4"
    s.close()


# ── 3. enable-after-seed (the seed-on-floor-then-light-up-vector path) ───────

def test_enable_embedder_after_floor_seed_backfills(tmp_db):
    # floor: no embedder. Seed facts that never touch facts_vec.
    s = _open(tmp_db, None)
    assert s.finder_kind == "lexical"
    _seed(s, 3)
    s.close()

    # now light up the embedder over the already-seeded store
    s = _open(tmp_db, "stub-a-4")
    assert s.finder_kind == "vector"
    assert _vec_count(s) == 3                   # the pre-seeded facts got indexed
    hits, kind = s.search("k2", n_results=5)
    assert kind == "vector"
    assert any(h.key == "k2" for h in hits)
    s.close()


# ── 4. same embedder reboot = no rebuild, index intact ──────────────────────

def test_same_embedder_reboot_does_not_rebuild(tmp_db, monkeypatch):
    s = _open(tmp_db, "stub-a-4")
    _seed(s, 3)
    s.close()

    # spy on _rebuild: a same-embedder reboot must take the cheap backfill path
    calls = {"n": 0}
    orig = store_mod._VectorFinder._rebuild

    def spy(self):
        calls["n"] += 1
        return orig(self)

    monkeypatch.setattr(store_mod._VectorFinder, "_rebuild", spy)

    s = _open(tmp_db, "stub-a-4")
    assert calls["n"] == 0                      # backfill path, no rebuild
    assert _vec_count(s) == 3                   # index still complete
    assert _stamp(s) == "stub-a-4::4"
    s.close()


# ── 5. incremental supersede after a reconcile stays consistent ─────────────

def test_supersede_after_reconcile_keeps_index_live_only(tmp_db):
    s = _open(tmp_db, "stub-a-4")
    _seed(s, 2)                                 # k0, k1
    s.close()

    s = _open(tmp_db, "stub-8")                 # force a dim-change rebuild
    s.set_fact(FactWrite(key="k0", value="v0-new", why="updated"))  # supersede k0
    # one live row per key: the superseded k0 is dropped from the index, the new
    # k0 added -> facts_vec count tracks live facts exactly.
    assert _vec_count(s) == len(s.list_facts())
    hits, _ = s.search("k0", n_results=5)
    assert hits[0].match_kind == "exact"
    assert hits[0].value == "v0-new"
    s.close()
