"""
A possessive in a question must not lose the name it is attached to.
19 Oct 2026, found by Probe on the Vellmarch merged twin.

_fts_query quotes every word as an FTS5 phrase, and unicode61 splits
"Laionan's" into 'laionan' 's'. The phrase only matched a row that itself said
"Laionan's", so 'What is Laionan's title now?' lost its subject lexically and
the right fact never surfaced. The possessive comes off before quoting.
"""
from __future__ import annotations

import pytest

from seren_loci.models.schemas import FactWrite
from seren_loci.store import _fts_query


@pytest.mark.parametrize("q,want", [
    ("Laionan's title", '"Laionan" "title"'),
    ("Laionan’s title", '"Laionan" "title"'),
    ("LAIONAN'S title", '"LAIONAN" "title"'),
    ("the Joneses' house", '"the" "Joneses" "house"'),
    ("'s title", '"title"'),
    ("posh.brace_style", '"posh.brace_style"'),
])
def test_the_possessive_comes_off_before_quoting(q, want):
    assert _fts_query(q) == want


def test_a_question_about_someones_anything_finds_them(store):
    store.set_fact(FactWrite(project="Laionan", key="laionan.title", value="Warden of the East",
                             why="promoted after the siege"))
    store.set_fact(FactWrite(project="Urgdurg", key="urgdurg.title", value="Chief", why="by the axe"))
    for q in ("Laionan's title", "Laionan’s title", "What is Laionan's title now?"):
        hits, kind = store.search(q)
        assert kind == "lexical" and hits and hits[0].key == "laionan.title", q


def test_a_row_that_says_the_possessive_is_still_found_both_ways(store):
    store.set_fact(FactWrite(key="bedtime", value="20:09", why="User's sleep matters"))
    for q in ("User's sleep", "User sleep"):
        assert [h.key for h in store.search(q)[0]] == ["bedtime"], q
