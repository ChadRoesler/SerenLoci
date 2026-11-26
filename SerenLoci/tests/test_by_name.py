"""
/by_name: the association door (26 Nov 2026).

An episodic store can be asked "what carries this topic tag"; a fact has no tag,
but it has a NAME, its project and key. Chad: "MEMORY HAS TOPIC A PARALLEL TO
LOCIS KEYS". This is the twin lookup, so an episode tagged with a thing can
bring the facts about that thing. Pinned here:

- a fact answers to a name that IS its project or its key, or whose every word
  is a word of its project or key; '*' answers to nothing; exact words only
- ranked by how many of the asked names it answers to, then newest; with a
  question and a finder, a tie goes to the fact nearest the question first
- each hit says which names matched, is marked "name", and scores below an
  exact hit's 1.0
- exclude_ids leaves out what the caller holds; only live facts answer
- over HTTP, and an empty ask is an empty answer, not an error
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from seren_loci.models.schemas import FactWrite  # noqa: E402


def _set(store, key, value="v", project="*", why=None):
    f = store.set_fact(FactWrite(project=project, key=key, value=value, why=why))
    time.sleep(0.002)          # created_at breaks ties; give each fact its own instant
    return f.id


def _keys(hits):
    return [h.key for h in hits]


def test_a_fact_answers_to_its_project_its_key_and_the_words_of_either(store):
    _set(store, "own-mcp-door", project="seren-kitchen")
    _set(store, "where-the-kitchen-runs", project="seren-kitchen")
    _set(store, "nuc-access", project="wren")
    _set(store, "seren-license")
    _set(store, "kitchen-builtins", project="seren-workbench")
    filed = _keys(store.by_name(["seren-kitchen"]))
    assert set(filed[:2]) == {"own-mcp-door", "where-the-kitchen-runs"}, "a project by its name: the facts filed under it lead"
    assert filed[2:] == ["kitchen-builtins"], \
        "then a fact that only has the name's words between its project and key (seren-workbench / kitchen-builtins)"
    assert _keys(store.by_name(["nuc"])) == ["nuc-access"], "a word of a key"
    assert _keys(store.by_name(["nuc-access"])) == ["nuc-access"], "a key whole"
    assert set(_keys(store.by_name(["kitchen"]))) == {"own-mcp-door", "where-the-kitchen-runs", "kitchen-builtins"}, \
        "a word of a project or of a key"
    assert _keys(store.by_name(["license"])) == ["seren-license"]
    assert store.by_name(["*"]) == [] and store.by_name(["licence"]) == [], "'*' is no name, and a spelling is not forgiven"
    assert store.by_name(["nu"]) == [] and store.by_name(["acces"]) == [], "whole words only"
    assert store.by_name([]) == [] and store.by_name(["", "  "]) == []


def test_more_names_answered_first_then_the_newest(store):
    old = _set(store, "nuc-access", project="wren")
    _set(store, "kitchen-builtins", project="seren-workbench")
    new = _set(store, "nuc-stays-on-focal")
    both = _set(store, "where-the-kitchen-runs", value="on the nuc", project="seren-kitchen")
    hits = store.by_name(["nuc", "kitchen", "runs"])
    assert hits[0].id == both and hits[0].matched_names == ["kitchen", "runs"], "two of the three names"
    assert [h.id for h in hits[1:]].index(new) < [h.id for h in hits[1:]].index(old), "then the newer of two that answer one"
    assert all(h.match_kind == "name" and 0 < h.score < 1.0 for h in hits)
    assert hits[0].score > hits[-1].score and hits[-1].matched_names in (["nuc"], ["kitchen"])
    assert [h.id for h in store.by_name(["nuc", "kitchen", "runs"], n_results=1)] == [both]
    assert both not in [h.id for h in store.by_name(["nuc", "kitchen", "runs"], exclude_ids=[both])]


def test_only_live_facts_answer(store):
    _set(store, "nuc-access", value="old", project="wren")
    _set(store, "nuc-access", value="new", project="wren")
    hits = store.by_name(["nuc"])
    assert [h.value for h in hits] == ["new"] and hits[0].live is True


def test_with_a_question_a_tie_goes_to_the_nearest_fact(hybrid_store):
    s = hybrid_store
    _set(s, "nuc-access", value="reached as caesar")            # no why: its whole text is "nuc-access  reached as caesar"
    _set(s, "nuc-stays-on-focal", value="stays on Ubuntu 20.04")
    assert _keys(s.by_name(["nuc"])) == ["nuc-stays-on-focal", "nuc-access"], "no question: the newest first"
    asked = s.by_name(["nuc"], query="nuc-access  reached as caesar")
    assert _keys(asked) == ["nuc-access", "nuc-stays-on-focal"], "a question: the fact it sits nearest leads"
    assert asked[0].raw_distance is not None and asked[0].raw_distance < asked[1].raw_distance
    assert all(h.raw_distance is None for h in s.by_name(["nuc"])), "no question, no distance"


def test_a_question_without_a_finder_orders_by_date(store):
    _set(store, "nuc-access")
    _set(store, "nuc-stays-on-focal")
    assert _keys(store.by_name(["nuc"], query="how do I reach the nuc")) == ["nuc-stays-on-focal", "nuc-access"]


def test_over_http(client):
    for key, project in (("own-mcp-door", "seren-kitchen"), ("nuc-access", "wren")):
        assert client.post("/fact", json={"project": project, "key": key, "value": "v"}).status_code == 200
    r = client.post("/by_name", json={"names": ["seren-kitchen", "nuc"]})
    assert r.status_code == 200
    body = r.json()
    assert {h["key"]: h["matched_names"] for h in body["hits"]} == {"own-mcp-door": ["seren-kitchen"], "nuc-access": ["nuc"]}
    assert body["names"] == ["seren-kitchen", "nuc"] and body["finder"] in ("lexical", "hybrid")
    held = body["hits"][0]["id"]
    again = client.post("/by_name", json={"names": ["seren-kitchen", "nuc"], "exclude_ids": [held], "query": "anything"}).json()
    assert held not in [h["id"] for h in again["hits"]] and len(again["hits"]) == 1
    assert client.post("/by_name", json={"names": []}).json()["hits"] == []
    plain = client.post("/search", json={"query": "nuc-access"}).json()["hits"][0]
    assert plain["matched_names"] is None, "an ordinary search hit carries no such list"
