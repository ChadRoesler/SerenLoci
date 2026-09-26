"""
Three things /search promised and did not do, each pinned here.

1. include_superseded was accepted by the schema, passed by the route and the
   corpus callosum, and never read: the FTS row of a superseded value was
   deleted on supersede, so history was lexically invisible and the flag was
   a no-op. Now history joins the exact rung (below the live value) and the
   lexical lane when asked, and stays out of the vector lane.

2. The KNN asked sqlite-vec for the k nearest over the WHOLE index and only
   then filtered by project, so a project living in a crowded neighbourhood
   got its rows filtered away and read as empty. The filter runs before the
   limit now.

3. set_fact / forget could only delete a vector when a finder was there to do
   it. A store booted on the floor (no embedder), superseding facts, then
   booted with the embedder again, kept the dead vectors forever - the reconcile
   only ever added. It prunes now.
"""
from __future__ import annotations

import hashlib
import math

import importlib.util

import pytest

# The vector lane needs sqlite_vec. Without it Loci falls back to the lexical
# finder on purpose (a warning, not a failure) - so the tests about the vector
# lane skip, the way test_hybrid_finder.py does, instead of asserting a lane
# the box does not have. Seen on CI, 24 Sept 2026: four failures, all this.
needs_vec = pytest.mark.skipif(importlib.util.find_spec("sqlite_vec") is None,
                               reason="sqlite_vec not installed: no vector lane")

from seren_loci import store as store_mod
from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactWrite
from seren_loci.store import LociStore


class _StubEmbedder:
    """Deterministic, torch-free: (model, text) -> a fixed 4-d unit vector."""
    def __init__(self, name: str):
        self._name = name

    def get_sentence_embedding_dimension(self) -> int:
        return 4

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            seed = hashlib.sha256(f"{self._name}|{t}".encode()).digest()
            vals = [seed[i] / 255.0 for i in range(4)]
            n = math.sqrt(sum(v * v for v in vals)) or 1.0
            out.append([v / n for v in vals])
        return out


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(store_mod, "_load_embedder",
                        lambda name, device, cache_folder=None: _StubEmbedder(name))


def _open(tmp_db, model=None) -> LociStore:
    return LociStore(LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=model)))


def _vec_rowids(store) -> set[int]:
    return {r[0] for r in store._conn.execute("SELECT fact_rowid FROM facts_vec")}


# ── 1. history ───────────────────────────────────────────────────────────────

def test_history_is_invisible_by_default_and_searchable_when_asked(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="brace_style", value="tabs everywhere", why="the legacy tree"))
    s.set_fact(FactWrite(key="brace_style", value="four spaces", why="pep8 won"))

    hits, _ = s.search("tabs everywhere")
    assert hits == [], "a retired value must not surface unasked"

    hits, _ = s.search("tabs everywhere", include_superseded=True)
    assert [(h.value, h.live) for h in hits] == [("tabs everywhere", False)]
    assert hits[0].superseded_at is not None
    assert hits[0].match_kind == "lexical"
    s.close()


def test_exact_history_sits_below_the_live_exact(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="editor", value="vim", why="muscle memory"))
    s.set_fact(FactWrite(key="editor", value="neovim", why="lua config"))
    s.set_fact(FactWrite(key="editor", value="helix", why="curiosity"))

    hits, _ = s.search("editor", include_superseded=True)
    assert [h.value for h in hits] == ["helix", "neovim", "vim"]
    assert hits[0].live and hits[0].score == 1.0
    assert all((not h.live) and h.score < 1.0 and h.match_kind == "exact" for h in hits[1:])

    live_only, _ = s.search("editor")
    assert [h.value for h in live_only] == ["helix"]
    s.close()


def test_a_forgotten_fact_is_history_too(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="old_proxy", value="squid on the nuc", why="pre-corp"))
    assert s.forget("*", "old_proxy")
    assert s.search("squid")[0] == []
    hits, _ = s.search("squid", include_superseded=True)
    assert [h.key for h in hits] == ["old_proxy"] and not hits[0].live
    s.close()


def test_history_only_project_is_in_scope_when_asked(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(project="retired-proj", key="k", value="ancient wisdom", why="was true"))
    assert s.forget("retired-proj", "k")
    assert s.search("ancient wisdom")[0] == []
    hits, _ = s.search("ancient wisdom", include_superseded=True)
    assert [h.project for h in hits] == ["retired-proj"]
    s.close()


def test_legacy_store_gets_its_history_index_rebuilt_once(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="k", value="first answer", why="w"))
    s.set_fact(FactWrite(key="k", value="second answer", why="w"))
    # Fake a store written by the old code: the retired row's FTS entry is
    # gone and the stamp says the index was never widened.
    old = s._conn.execute("SELECT rowid FROM facts WHERE superseded_at IS NOT NULL").fetchone()[0]
    s._conn.execute(
        "INSERT INTO facts_fts(facts_fts, rowid, key, value, why) "
        "VALUES('delete', ?, (SELECT key FROM facts WHERE rowid=?), "
        "(SELECT value FROM facts WHERE rowid=?), (SELECT COALESCE(why,'') FROM facts WHERE rowid=?))",
        (old, old, old, old))
    s._conn.execute("DELETE FROM loci_meta WHERE key='fts_scope'")
    s._conn.commit()
    assert s.search("first answer", include_superseded=True)[0] == [], "legacy: history invisible"
    s.close()

    s = _open(tmp_db)   # boot reconciles
    hits, _ = s.search("first answer", include_superseded=True)
    assert [h.value for h in hits] == ["first answer"]
    assert s._conn.execute("SELECT value FROM loci_meta WHERE key='fts_scope'").fetchone()[0] == "all"
    s.close()


@needs_vec
def test_hybrid_search_reaches_history_lexically_but_not_by_vector(tmp_db):
    s = _open(tmp_db, model="stub-4")
    s.set_fact(FactWrite(key="k", value="first answer", why="w"))
    s.set_fact(FactWrite(key="k", value="second answer", why="w"))
    assert s.finder_kind == "hybrid"
    assert len(_vec_rowids(s)) == 1, "the vector lane holds live rows only"
    hits, _ = s.search("first answer", include_superseded=True)
    retired = [h for h in hits if not h.live]
    assert retired and retired[0].value == "first answer"
    assert retired[0].raw_distance is None, "history came through the lexical lane, not the KNN"
    s.close()


# ── 2. project-scoped KNN ────────────────────────────────────────────────────

@needs_vec
def test_knn_finds_a_small_project_behind_a_crowded_one(tmp_db):
    s = _open(tmp_db, model="stub-4")
    for i in range(40):
        s.set_fact(FactWrite(project="crowd", key=f"c{i}", value=f"crowd fact number {i}", why="noise"))
    s.set_fact(FactWrite(project="tiny", key="only", value="the lone fact", why="alone"))
    # Nothing lexical in common with the tiny fact: only the vector lane can
    # reach it, and with k=2 nearest over the whole index it never did.
    hits, finder = s.search("zzzz qqqq", project="tiny", include_fundamentals=False, n_results=1)
    assert finder == "hybrid"
    assert [h.key for h in hits] == ["only"]
    assert hits[0].raw_distance is not None, "it came through the KNN, scoped"
    s.close()


@needs_vec
def test_knn_scopes_never_leak_a_project(tmp_db):
    s = _open(tmp_db, model="stub-4")
    s.set_fact(FactWrite(project="a", key="ka", value="alpha", why="w"))
    s.set_fact(FactWrite(project="b", key="kb", value="beta", why="w"))
    hits, _ = s.search("zzzz", project="a", include_fundamentals=False, n_results=10)
    assert {h.project for h in hits} == {"a"}
    s.close()


# ── 3. dead vectors ──────────────────────────────────────────────────────────

@needs_vec
def test_vectors_superseded_on_the_floor_are_pruned_on_the_next_vector_boot(tmp_db):
    s = _open(tmp_db, model="stub-4")
    s.set_fact(FactWrite(key="k", value="v1", why="w"))
    s.set_fact(FactWrite(key="other", value="stays", why="w"))
    assert len(_vec_rowids(s)) == 2
    s.close()

    floor = _open(tmp_db)                 # no embedder: no finder to delete vectors
    floor.set_fact(FactWrite(key="k", value="v2", why="w"))
    assert floor.forget("*", "other")
    floor.close()

    s = _open(tmp_db, model="stub-4")     # same stamp -> backfill + prune, no rebuild
    live = {r[0] for r in s._conn.execute("SELECT rowid FROM facts WHERE superseded_at IS NULL")}
    assert _vec_rowids(s) == live, "the index mirrors live rows and nothing else"
    assert len(live) == 1
    s.close()


@needs_vec
def test_a_clean_vector_boot_leaves_no_transaction_open(tmp_db):
    """Seen live 26 Sept 2026: the FIRST set_fact after every restart of the
    wren Loci failed with 'cannot start a transaction within a transaction',
    and the retry worked. The boot's backfill ran _prune_dead's DELETE (which
    opens a transaction in Python's sqlite3 even when it deletes nothing) and
    committed only if something was added or pruned - so a clean boot handed
    the first write a connection already inside a transaction."""
    s = _open(tmp_db, model="m")
    s.set_fact(FactWrite(project="p", key="k", value="v"))
    s.close()
    s = _open(tmp_db, model="m")                 # same embedder: the backfill path, nothing to do
    try:
        assert not s._conn.in_transaction, "boot left a transaction open"
        s.set_fact(FactWrite(project="p", key="k2", value="v2"))   # raised before the fix
    finally:
        s.close()
