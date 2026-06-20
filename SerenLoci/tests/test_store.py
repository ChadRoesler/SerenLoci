"""
Store-layer tests - the data layer's contract.

The load-bearing one is the strict-supersede invariant: exactly one live value
per (project, key), enforced by the partial unique index, history preserved,
never blended. If that breaks, Loci stops being a logic store.
"""
from __future__ import annotations

import sqlite3

import pytest

from seren_loci.models.schemas import FactWrite, FUNDAMENTALS


# ── exact lookup (the spine) ────────────────────────────────────────────────

def test_set_and_get_exact(store):
    store.set_fact(FactWrite(key="python.opinion", value="fuck python", why="the way"))
    f = store.get_fact(FUNDAMENTALS, "python.opinion")
    assert f is not None
    assert f.value == "fuck python"
    assert f.why == "the way"
    assert f.is_live


def test_get_missing_returns_none(store):
    assert store.get_fact(FUNDAMENTALS, "nope") is None


def test_project_scoping_is_isolated(store):
    store.set_fact(FactWrite(key="k", value="fundamental"))
    store.set_fact(FactWrite(project="proj-a", key="k", value="a-specific"))
    assert store.get_fact(FUNDAMENTALS, "k").value == "fundamental"
    assert store.get_fact("proj-a", "k").value == "a-specific"
    assert store.get_fact("proj-b", "k") is None


# ── strict supersede ────────────────────────────────────────────────────────

def test_supersede_updates_live_keeps_history(store):
    store.set_fact(FactWrite(key="k", value="v1", why="first"))
    second = store.set_fact(FactWrite(key="k", value="v2", why="second"))

    live = store.get_fact(FUNDAMENTALS, "k")
    assert live.value == "v2"

    hist = store.get_history(FUNDAMENTALS, "k")
    assert len(hist) == 2
    assert sum(1 for h in hist if h.is_live) == 1
    # the old row points forward to the new live row
    old = next(h for h in hist if not h.is_live)
    assert old.superseded_by == second.id
    assert old.value == "v1"  # never blended - the old value is intact


def test_supersede_never_blends(store):
    store.set_fact(FactWrite(key="k", value="original"))
    store.set_fact(FactWrite(key="k", value="replacement"))
    live = store.get_fact(FUNDAMENTALS, "k")
    assert live.value == "replacement"
    assert "original" not in live.value


def test_one_live_value_invariant_is_db_enforced(store, tmp_db):
    """The partial unique index must physically reject a second live row for a
    key - the invariant lives in the schema, not in app code."""
    store.set_fact(FactWrite(key="k", value="v1"))
    raw = sqlite3.connect(tmp_db)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                "INSERT INTO facts(id,project,key,value,why,source,created_at,extra) "
                "VALUES('dupe','*','k','sneaky',NULL,'user',1.0,'{}')"
            )
            raw.commit()
    finally:
        raw.close()


# ── forget (soft retire) ────────────────────────────────────────────────────

def test_forget_retires_live_keeps_history(store):
    store.set_fact(FactWrite(key="k", value="v"))
    assert store.forget(FUNDAMENTALS, "k") is True
    assert store.get_fact(FUNDAMENTALS, "k") is None
    # history survives the retire
    assert len(store.get_history(FUNDAMENTALS, "k")) == 1


def test_forget_missing_returns_false(store):
    assert store.forget(FUNDAMENTALS, "never-existed") is False


def test_can_reset_key_after_forget(store):
    """Retiring a key frees the live slot so a later set works (no invariant
    collision with the retired row)."""
    store.set_fact(FactWrite(key="k", value="v1"))
    store.forget(FUNDAMENTALS, "k")
    store.set_fact(FactWrite(key="k", value="v2"))
    assert store.get_fact(FUNDAMENTALS, "k").value == "v2"
    assert len(store.get_history(FUNDAMENTALS, "k")) == 2


# ── search: exact + lexical floor ───────────────────────────────────────────

def test_finder_kind_is_lexical_without_embedder(store):
    assert store.finder_kind == "lexical"


def test_search_exact_key_scores_one(store):
    store.set_fact(FactWrite(key="posh.brace_style", value="curlies on a new line"))
    hits, finder = store.search("posh.brace_style")
    assert finder == "lexical"
    assert hits[0].match_kind == "exact"
    assert hits[0].score == 1.0


def test_search_lexical_hits_on_why_text(store):
    # the WHY mentions cuda/runtime; the key/value don't say "runtime"
    store.set_fact(FactWrite(project="seren-memory", key="cuda.no_vmm",
                             value="GGML_CUDA_NO_VMM=ON at compile time",
                             why="env var not honored at runtime on Jetson"))
    hits, _ = store.search("cuda runtime", n_results=5)
    keys = [h.key for h in hits]
    assert "cuda.no_vmm" in keys
    hit = next(h for h in hits if h.key == "cuda.no_vmm")
    assert hit.match_kind == "lexical"
    # a real FTS match must clear zero (SCC would read 0 as noise)
    assert hit.score > 0.0
    assert hit.score < 1.0  # ...but stays below an exact hit


def test_search_scopes_to_project_plus_fundamentals(store):
    store.set_fact(FactWrite(key="global.rule", value="camelCase is life"))
    store.set_fact(FactWrite(project="proj-a", key="a.thing", value="alpha"))
    store.set_fact(FactWrite(project="proj-b", key="b.thing", value="beta"))
    # scoped to proj-a, fundamentals folded in, proj-b excluded
    hits, _ = store.search("thing", project="proj-a", n_results=10)
    projects = {h.project for h in hits}
    assert "proj-b" not in projects


def test_search_excludes_superseded(store):
    store.set_fact(FactWrite(key="k", value="old distinctive-token-alpha"))
    store.set_fact(FactWrite(key="k", value="new distinctive-token-beta"))
    hits, _ = store.search("distinctive-token-alpha", n_results=10)
    # the superseded row holding 'alpha' should not surface
    assert all(h.value != "old distinctive-token-alpha" for h in hits)


# ── housekeeping ────────────────────────────────────────────────────────────

def test_counts(store):
    store.set_fact(FactWrite(key="a", value="1"))
    store.set_fact(FactWrite(project="p", key="b", value="2"))
    store.set_fact(FactWrite(key="a", value="1b"))  # supersedes -> 1 history row
    c = store.counts()
    assert c["live"] == 2
    assert c["history"] == 1
    assert c["projects"] == 2


def test_list_facts_scoped(store):
    store.set_fact(FactWrite(key="a", value="1"))
    store.set_fact(FactWrite(project="p", key="b", value="2"))
    assert len(store.list_facts()) == 2
    assert len(store.list_facts(project="p")) == 1


def test_fts_query_tolerates_punctuation_and_empty(store):
    store.set_fact(FactWrite(key="cuda-compat", value="needs cuda-compat-12-2"))
    # punctuation-heavy query shouldn't raise
    hits, _ = store.search("cuda-compat-12-2", n_results=5)
    assert isinstance(hits, list)
    # an all-punctuation / empty query shouldn't 500 either
    assert isinstance(store.search("   ", n_results=5)[0], list)
