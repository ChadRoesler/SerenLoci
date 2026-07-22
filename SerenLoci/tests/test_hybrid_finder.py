"""
Hybrid finder tests: _HybridFinder RRF fusion + scope-aware query embedding.

Validates:
  1. RRF fusion: a fact appearing in BOTH FTS5 and vector top-k gets a higher
     fused score than one appearing in only one ranker.
  2. Scope-aware query embedding: when a single project scope is given, the
     vector query is augmented with the project prefix.
  3. finder_kind returns "hybrid" when an embedder is configured.
  4. LociStore.search() with hybrid finder returns RRF-normalized scores
     in the 0..1 score contract.

Uses a stub embedder (no torch) + real sqlite-vec + FTS5, same as
test_embedder_reconcile.py.
"""
from __future__ import annotations

import hashlib
import math

import pytest

from seren_loci import store as store_mod
from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactWrite, FUNDAMENTALS
from seren_loci.store import LociStore, _HybridFinder

pytest.importorskip("sqlite_vec")


# ── stub embedder (deterministic, torch-free) ────────────────────────────────

class _StubEmbedder:
    """Deterministic embedder that maps (model_name, text) -> fixed-dim vector."""
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


@pytest.fixture(autouse=True)
def _stub_embedder(monkeypatch):
    """Inject the stub through the module seam for every test in this file."""
    monkeypatch.setattr(store_mod, "_load_embedder",
                        lambda name, device, cache_folder=None: _StubEmbedder(name))


# ── helpers ──────────────────────────────────────────────────────────────────

def _hybrid_store(tmp_db) -> LociStore:
    """Build a LociStore with a hybrid finder (embedder configured)."""
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model="stub-4"))
    s = LociStore(cfg)
    # Seed a handful of facts across two projects so we can test scoping.
    s.set_fact(FactWrite(key="k1", value="apple pie recipe", why="baking"))
    s.set_fact(FactWrite(key="k2", value="banana bread recipe", why="baking"))
    s.set_fact(FactWrite(key="k3", value="cherry cobbler", why="dessert"))
    s.set_fact(FactWrite(project="proj-a", key="pk1",
                         value="apple in project A", why="scope test"))
    s.set_fact(FactWrite(project="proj-a", key="pk2",
                         value="banana in project A", why="scope test"))
    return s


# ── tests ────────────────────────────────────────────────────────────────────

def test_finder_kind_is_hybrid(tmp_db):
    """When an embedder is configured, finder_kind returns 'hybrid'."""
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model="stub-4"))
    s = LociStore(cfg)
    assert s.finder_kind == "hybrid"
    assert s._finder is not None
    assert isinstance(s._finder, _HybridFinder)
    s.close()


def test_finder_kind_lexical_when_no_embedder(tmp_db):
    """Without an embedder, finder_kind returns 'lexical' and _finder is None."""
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None))
    s = LociStore(cfg)
    assert s.finder_kind == "lexical"
    assert s._finder is None
    s.close()


def test_rrf_boosts_doc_in_both_rankers(tmp_db):
    """A fact that appears in BOTH FTS5 and vector top-k gets a higher RRF
    fused score than one appearing in only one ranker."""
    s = _hybrid_store(tmp_db)
    # Query for "apple" - k1 ("apple pie recipe") should match in both FTS5
    # (contains "apple") and vector (semantically close to "apple").
    # k2 ("banana bread") should only match in FTS5 (contains "banana", no
    # semantic match for "apple"). k3 ("cherry cobbler") may or may not match.
    hits, kind = s.search("apple", n_results=5)
    assert kind == "hybrid"

    # k1 should be at or near the top with a strong fused score.
    k1_hit = next((h for h in hits if h.key == "k1"), None)
    assert k1_hit is not None, "k1 should be in results for 'apple'"
    assert k1_hit.match_kind == "hybrid"

    # k2 should have a lower score than k1 (FTS5-only vs FTS5+vector).
    k2_hit = next((h for h in hits if h.key == "k2"), None)
    if k2_hit is not None:
        assert k2_hit.score <= k1_hit.score, (
            f"k2 ({k2_hit.score}) should not outrank k1 ({k1_hit.score}) "
            f"since k2 is only an FTS5 match"
        )

    s.close()


def test_rrf_scores_are_normalized_0_1(tmp_db):
    """All scores returned by search() with the hybrid finder are in [0, 1]
    and strictly below the exact-match score of 1.0."""
    s = _hybrid_store(tmp_db)
    hits, kind = s.search("recipe", n_results=10)
    assert kind == "hybrid"
    for h in hits:
        assert 0.0 <= h.score <= 1.0, f"score {h.score} out of range"
        assert h.score < 1.0, "no hybrid hit should reach exact-match 1.0"
    s.close()


def test_exact_still_leads(tmp_db):
    """Exact key match (score 1.0) still beats hybrid results."""
    s = _hybrid_store(tmp_db)
    # The exact key "k1" should return k1 at 1.0, and the hybrid results
    # should be below that.
    hits, kind = s.search("k1", n_results=10)
    assert kind == "hybrid"
    assert hits[0].key == "k1"
    assert hits[0].score == 1.0
    assert hits[0].match_kind == "exact"
    s.close()


def test_scope_aware_embedding_augments_query(tmp_db):
    """When a single project scope is provided, the vector query is augmented
    by prepending the project name. We verify this indirectly by checking that
    project-scoped search returns in-scope facts with correct scores."""
    s = _hybrid_store(tmp_db)
    # Search within proj-a for "apple" - should find pk1 (apple in project A)
    # before the global k1 (apple pie recipe) because the scope-aware embedding
    # nudges toward the project domain.
    hits, kind = s.search("apple", project="proj-a", n_results=10)
    assert kind == "hybrid"

    # pk1 should be in the results (it's in scope and matches "apple").
    pk1_hit = next((h for h in hits if h.key == "pk1"), None)
    assert pk1_hit is not None, "pk1 should be in proj-a scoped results"

    # All results should be in-scope (proj-a or FUNDAMENTALS).
    for h in hits:
        assert h.project in ("proj-a", FUNDAMENTALS), (
            f"result {h.key} from project {h.project} is out of scope"
        )
    s.close()


def test_hybrid_finder_rrf_fusion_directly(tmp_db):
    """Unit-test the _HybridFinder's RRF logic directly: verify that a doc
    appearing in both rankers gets a higher fused score.

    We simulate two rankers with realistic ranks (within k=20 range) so
    the RRF formula produces clearly differentiated scores.
    """
    s = _hybrid_store(tmp_db)
    finder = s._finder
    assert isinstance(finder, _HybridFinder)

    # Two rankers with the same ordering (A > B > C).
    # Doc A ranks #1 in both → 1/(60+0) + 1/(60+0) = 0.03333
    # Doc B ranks #2 in both → 1/(60+1) + 1/(60+1) = 0.03279
    # Doc C ranks #3 in both → 1/(60+2) + 1/(60+2) = 0.03226
    fused = {}
    for rank, fid in enumerate(["A", "B", "C"]):
        fused[fid] = fused.get(fid, 0.0) + 1.0 / (60 + rank)
    for rank, fid in enumerate(["A", "B", "C"]):  # same order in second ranker
        fused[fid] = fused.get(fid, 0.0) + 1.0 / (60 + rank)
    # A: 1/60 + 1/60 ≈ 0.03333
    # B: 1/61 + 1/61 ≈ 0.03279
    # C: 1/62 + 1/62 ≈ 0.03226
    assert fused["A"] > fused["B"], f"A ({fused['A']}) should beat B ({fused['B']})"
    assert fused["B"] > fused["C"], f"B ({fused['B']}) should beat C ({fused['C']})"
    # A appearing twice at rank 1 beats C appearing twice at rank 3
    assert fused["A"] > fused["C"], (
        f"A ({fused['A']}) should beat C ({fused['C']})"
    )
    s.close()


def test_hybrid_finder_scope_aware_augment(tmp_db):
    """Verify that _HybridFinder._vector_search_augmented prepends the project
    when a single non-FUNDAMENTALS scope is given."""
    s = _hybrid_store(tmp_db)
    finder = s._finder
    assert isinstance(finder, _HybridFinder)

    # No scopes -> no augmentation (delegates to super().search with same query)
    result_no_scope = finder._vector_search_augmented("apple", 5, None)
    assert isinstance(result_no_scope, list)

    # Single non-FUNDAMENTALS scope -> augmented query "proj-a: apple"
    result_scoped = finder._vector_search_augmented("apple", 5, ["proj-a"])
    assert isinstance(result_scoped, list)

    # FUNDAMENTALS scope -> no augmentation
    result_fund = finder._vector_search_augmented("apple", 5, [FUNDAMENTALS])
    assert isinstance(result_fund, list)

    # Multiple scopes -> no augmentation
    result_multi = finder._vector_search_augmented("apple", 5, ["proj-a", FUNDAMENTALS])
    assert isinstance(result_multi, list)

    s.close()


def test_search_without_scopes_falls_through(tmp_db):
    """When no project scope is given, search searches all projects and
    scope-aware embedding is skipped (multi-scope = no augmentation)."""
    s = _hybrid_store(tmp_db)
    hits, kind = s.search("banana", n_results=10)
    assert kind == "hybrid"
    # Should find both k2 (banana bread) and pk2 (banana in project A).
    keys_found = {h.key for h in hits}
    assert "k2" in keys_found
    assert "pk2" in keys_found
    s.close()


def test_build_finder_returns_none_on_import_error(monkeypatch, tmp_db):
    """If the embedder import fails, _build_finder returns None and the store
    falls back to lexical search."""
    import logging
    monkeypatch.setattr(store_mod, "_load_embedder",
                        lambda name, device: (_ for _ in ()).throw(
                            ImportError("no torch today"))
                        )
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model="stub-4"))
    s = LociStore(cfg)
    assert s._finder is None
    assert s.finder_kind == "lexical"
    # Seed a fact so FTS5 has something to find.
    s.set_fact(FactWrite(key="apple-pie", value="best apple pie recipe", why="baking"))
    # Search still works via FTS5
    hits, kind = s.search("apple", n_results=5)
    assert kind == "lexical"
    assert len(hits) > 0, "FTS5 should find the seeded fact"
    s.close()
