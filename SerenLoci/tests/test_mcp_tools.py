"""
MCP tool tests.

Gated on the `mcp` package (the [mcp] extra) - seren_loci.mcp.tools imports
FastMCP at module top. Tools are called DIRECTLY on the impl (the testability
seam), no FastMCP/client/HTTP roundtrip.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from seren_loci.config import LociConfig, StorageConfig
from seren_loci.store import LociStore
from seren_loci.mcp.tools import LociToolImpl


@pytest.fixture
def impl(tmp_path):
    cfg = LociConfig(storage=StorageConfig(
        db_path=str(tmp_path / "loci.db"), embedding_model=None))
    store = LociStore(cfg)
    yield LociToolImpl(store, cfg)
    store.close()


# ── set_fact ────────────────────────────────────────────────────────────────

def test_set_fact_fresh(impl):
    r = impl.set_fact(key="python.opinion", value="fuck python", why="the way")
    assert r["ok"] is True
    assert r["superseded"] is None
    assert r["project"] == "*"


def test_set_fact_supersede_names_old(impl):
    impl.set_fact(key="k", value="v1")
    r = impl.set_fact(key="k", value="v2")
    assert r["superseded"] is not None


def test_set_fact_into_project(impl):
    impl.set_fact(project="seren-memory", key="cuda.no_vmm",
                  value="GGML_CUDA_NO_VMM=ON at compile time",
                  why="env var not honored at runtime")
    got = impl.get_fact(key="cuda.no_vmm", project="seren-memory")
    assert got["found"] is True
    assert "compile time" in got["value"]


# ── get_fact ────────────────────────────────────────────────────────────────

def test_get_fact_found(impl):
    impl.set_fact(key="k", value="v", why="w")
    r = impl.get_fact(key="k")
    assert r["found"] is True
    assert r["value"] == "v"
    assert r["why"] == "w"


def test_get_fact_not_found_is_clean(impl):
    r = impl.get_fact(key="never-set")
    assert r["found"] is False  # a clean answer, not an error


# ── search_loci ─────────────────────────────────────────────────────────────

def test_search_exact_leads(impl):
    impl.set_fact(key="posh.brace_style", value="curlies new line")
    r = impl.search_loci(query="posh.brace_style")
    assert r["finder"] == "lexical"
    assert r["hits"][0]["match_kind"] == "exact"
    assert r["hits"][0]["score"] == 1.0


def test_search_lexical_on_why(impl):
    impl.set_fact(project="seren-memory", key="cuda.no_vmm",
                  value="GGML_CUDA_NO_VMM=ON at compile time",
                  why="env var not honored at runtime on Jetson")
    r = impl.search_loci(query="cuda runtime", n_results=5)
    keys = [h["key"] for h in r["hits"]]
    assert "cuda.no_vmm" in keys


# ── forget_fact ─────────────────────────────────────────────────────────────

def test_forget_then_gone(impl):
    impl.set_fact(key="k", value="v")
    assert impl.forget_fact(key="k")["ok"] is True
    assert impl.get_fact(key="k")["found"] is False


def test_forget_missing_is_false(impl):
    r = impl.forget_fact(key="never")
    assert r["ok"] is False


# ── fact_history ────────────────────────────────────────────────────────────

def test_fact_history(impl):
    impl.set_fact(key="k", value="v1")
    impl.set_fact(key="k", value="v2")
    r = impl.fact_history(key="k")
    assert r["count"] == 2


# ── list_facts ──────────────────────────────────────────────────────────────

def test_list_facts_scoped(impl):
    impl.set_fact(key="a", value="1")
    impl.set_fact(project="p", key="b", value="2")
    assert impl.list_facts()["count"] == 2
    assert impl.list_facts(project="p")["count"] == 1
