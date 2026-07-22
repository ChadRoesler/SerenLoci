"""
FTS stopword-bleed regression - the bug the SerenProbe harness walked us down to.

THE FIELD SYMPTOM: All-scc (22 stores) scored HR 0.056. Every store's rank-1 hit
arrived at SCC as a perfect 1.0, so rank-only fusion tie-broke on config order and
stores 11-22 were structurally unreachable. But the ties were the DOWNSTREAM
effect. The root was one store answering a question about a DIFFERENT entity and
scoring it 1.0.

Reproduced with a single store, embedding-free (the FTS floor), because that is
where the bug lives:

  query "What title does Cewellric hold?" against a store whose entity is
  Thranaz-hold returns Thranaz-hold facts at score 1.0 -- because
  _fts_query ORs every token on a >3-token query, so the STOPWORDS ("what",
  "does", "hold") and structural terms ("title") match Thranaz-hold's
  ruler_title / "...hold" name, while the one discriminating token ("Cewellric")
  is drowned. bm25 then ranks the junk-term store top, and the hybrid->1.0
  normalization stamps it exact-match score.

Two asserts, two distinct failures, so a partial fix is legible:
  1. RANKING: the named entity must outrank the unrelated one.
  2. SCORE CONTRACT: a lexical-only near-miss must NOT reach 1.0 (that number is
     reserved for an exact-key hit; SCC trusts it as "here's EXACTLY how it's
     called").
"""
from __future__ import annotations

from seren_loci.models.schemas import FactWrite


def _seed_two_entities(store):
    # Cewellric: the entity the query NAMES.
    store.set_fact(FactWrite(project="identity", key="title",
                             value="Ironwarden", why="title of Cewellric (id:1007)"))
    store.set_fact(FactWrite(project="stats", key="race",
                             value="Human", why="race of Cewellric (id:1007)"))
    # Thranaz-hold: an unrelated entity whose facts happen to contain the
    # STOPWORDS and structural terms of the question -- "title" (ruler_title),
    # "hold" (its own name). This is the store that bled to the top in the field.
    store.set_fact(FactWrite(project="misc", key="ruler_title",
                             value="Tyrant of Mokthrak", why="ruler_title of Thranaz-hold (id:193)"))
    store.set_fact(FactWrite(project="misc", key="defenses",
                             value="murder holes", why="defenses of Thranaz-hold (id:193)"))


def test_named_entity_outranks_stopword_bleed(store):
    _seed_two_entities(store)
    hits, _ = store.search("What title does Cewellric hold?", n_results=10)
    assert hits, "expected at least one hit"
    top = hits[0]
    why = (top.why or "")
    # The winner must be the entity the query NAMED, not the one that merely
    # shares stopwords with the question.
    assert "Cewellric" in why, (
        f"stopword bleed: top hit is {why!r} (score {top.score}), "
        f"not the named entity Cewellric"
    )


def test_lexical_hit_does_not_saturate_to_exact_score(store):
    _seed_two_entities(store)
    hits, _ = store.search("What title does Cewellric hold?", n_results=10)
    # 1.0 is the EXACT-key contract ("you asked for this address"). No lexical /
    # hybrid near-miss may reach it, or SCC cannot tell a keyword brush from a
    # precise invocation -- which is exactly what produced the 22-way 1.0 tie.
    bleed = [h for h in hits if "Thranaz-hold" in (h.why or "")]
    assert bleed, "expected the unrelated entity to still appear (as a lesser hit)"
    assert all(h.score < 1.0 for h in bleed), (
        "score contract broken: an unrelated lexical hit reached 1.0, "
        f"scores={[h.score for h in bleed]}"
    )

def test_hybrid_saturates_nonexact_to_exact_score(hybrid_store):
    """ATOMIC PROOF of the offending line (max_possible = 2.0/RRF_K).
    One fact, sharing a lexical token with the query ('hold'), NOT a verbatim
    key. It's the only doc -> trivially rank-0 in BOTH lanes -> fused = 2/RRF_K
    -> normalizes to exactly 1.0. Deterministic; independent of embedder quality
    because a lone doc tops both lanes no matter what the vectors say."""
    hybrid_store.set_fact(FactWrite(project="misc", key="ruler_title",
        value="Tyrant of Mokthrak", why="ruler_title of Thranaz-hold (id:193)"))
    hits, _ = hybrid_store.search("What title does Cewellric hold?", n_results=10)
    top = hits[0]
    assert top.match_kind != "exact"
    assert top.score < 1.0, (          # PREDICT RED: 1.0, match_kind='hybrid'
        f"hybrid path stamped a non-exact hit at {top.score}")


def test_isolated_leaf_saturates_unanswerable_question(hybrid_store):
    """FIELD-FAITHFUL. Thranaz-hold ALONE — no Cewellric in this store, exactly
    like a per-entity leaf in All-scc. Nothing local outranks the template fact,
    so ruler_title is rank-0 in both lanes -> 1.0, and SCC gets a phantom perfect
    hit for a question this store literally cannot answer.
    (Needs a semantically-sane embedder: if a degenerate stub splits the two
    lanes' rank-0 across different facts, neither reaches 1.0. Use real MiniLM or
    a stub that embeds meaningfully.)"""
    hybrid_store.set_fact(FactWrite(project="misc", key="ruler_title",
        value="Tyrant of Mokthrak", why="ruler_title of Thranaz-hold (id:193)"))
    hybrid_store.set_fact(FactWrite(project="misc", key="defenses",
        value="murder holes", why="defenses of Thranaz-hold (id:193)"))
    hits, _ = hybrid_store.search("What title does Cewellric hold?", n_results=10)
    top = hits[0]
    assert top.score < 1.0, (          # PREDICT RED
        f"isolated leaf handed a phantom {top.score} for {top.why!r}")