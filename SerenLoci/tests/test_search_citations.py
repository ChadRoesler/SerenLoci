"""
A search hit can carry what it rests on (24 Nov 2026).

Citations were kept, flagged for recheck and drawn as skyways, but no reader
was ever handed them: a fan that found a fact could not bring along the facts
it holds because of. With `with_citations`, each live hit carries `rests_on`:
the cited facts as they read now, with their ids (so a fan can dedupe them
against its packet) and the citation's own because. Pinned here:

- off by default: a hit carries no rests_on unless the search asked
- asked: the live cited facts, ids and values and whys, plus the because
- a cited address with no live value is left out; a retired hit carries none
- the HTTP route takes the field and an older caller that never sends it is unchanged
"""
from __future__ import annotations

from seren_loci.models.schemas import FactWrite

PRJ = "vellmarch"


def _world(store):
    store.set_fact(FactWrite(project=PRJ, key="lurggnashmug.title", value="Sovereign of Thoknakhkrug",
                             why="raised to ruler in year 245 for deeds of diplomacy"))
    store.set_fact(FactWrite(project=PRJ, key="lurggnashmug.civilization", value="Thoknakhkrug",
                             why="born into it"))
    store.set_fact(FactWrite(project=PRJ, key="thoknakhkrug.capital", value="Mturenfort", why="the throne sits there"))
    store.cite(PRJ, "lurggnashmug.title", PRJ, "lurggnashmug.civilization", why="a sovereign rules their own people")
    store.cite(PRJ, "lurggnashmug.title", PRJ, "thoknakhkrug.capital", why="the title is held from the capital")


def _hit(hits, key):
    return next(h for h in hits if h.key == key)


def test_off_by_default(store):
    _world(store)
    hits, _ = store.search("lurggnashmug.title", project=PRJ)
    assert _hit(hits, "lurggnashmug.title").rests_on is None


def test_asked_a_hit_brings_the_live_facts_it_rests_on(store):
    _world(store)
    hits, _ = store.search("lurggnashmug.title", project=PRJ, with_citations=True)
    title = _hit(hits, "lurggnashmug.title")
    got = {r["key"]: r for r in title.rests_on}
    assert set(got) == {"lurggnashmug.civilization", "thoknakhkrug.capital"}
    civ = got["lurggnashmug.civilization"]
    assert civ["value"] == "Thoknakhkrug" and civ["why"] == "born into it"
    assert civ["because"] == "a sovereign rules their own people" and civ["project"] == PRJ
    assert civ["id"] == store.get_fact(PRJ, "lurggnashmug.civilization").id
    # asked, a live fact that rests on nothing says so with an empty list, not None
    others, _ = store.search("thoknakhkrug.capital", project=PRJ, with_citations=True)
    assert _hit(others, "thoknakhkrug.capital").rests_on == []


def test_a_retired_cited_fact_is_left_out(store):
    _world(store)
    store.forget(PRJ, "thoknakhkrug.capital", why_changed="the capital burned")
    hits, _ = store.search("lurggnashmug.title", project=PRJ, with_citations=True)
    assert [r["key"] for r in _hit(hits, "lurggnashmug.title").rests_on] == ["lurggnashmug.civilization"]


def test_a_superseded_cited_fact_brings_its_new_value(store):
    _world(store)
    store.set_fact(FactWrite(project=PRJ, key="thoknakhkrug.capital", value="Urgdurg-lair", why="moved after the war"))
    hits, _ = store.search("lurggnashmug.title", project=PRJ, with_citations=True)
    cap = {r["key"]: r for r in _hit(hits, "lurggnashmug.title").rests_on}["thoknakhkrug.capital"]
    assert cap["value"] == "Urgdurg-lair", "the citation is between addresses; the reader gets what holds now"


def test_the_route_takes_the_field(client):
    for key, value in (("a.title", "Elder"), ("a.civ", "Rakbazbor")):
        assert client.post("/fact", json={"project": PRJ, "key": key, "value": value}).status_code == 200
    assert client.post("/fact/cite", json={"project": PRJ, "key": "a.title", "on_project": PRJ, "on_key": "a.civ",
                                      "why": "an elder of their own people"}).status_code == 200
    plain = client.post("/search", json={"query": "a.title", "project": PRJ}).json()
    assert all(h.get("rests_on") is None for h in plain["hits"]), "an older caller sees no change"
    asked = client.post("/search", json={"query": "a.title", "project": PRJ, "with_citations": True}).json()
    title = next(h for h in asked["hits"] if h["key"] == "a.title")
    assert [r["value"] for r in title["rests_on"]] == ["Rakbazbor"]
