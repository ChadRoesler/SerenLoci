"""
Citations: a fact RESTS ON another (23 Oct 2026).

A dry run over one inhabitant's 165 facts found six whose reason had changed
under them - the design they described had been replaced the day before -
with nothing to say so. A citation is dependency ("this holds because that
does"), never "see also". Pinned here:

- cite: both addresses live, never a fact on itself; citing again updates
  the why; a fact can rest on several, across projects
- citations read both ways: what a fact rests on, what rests on it
- superseding or retiring a cited fact marks what rests on it to recheck,
  with what changed and why_changed; the write's notes say how many
- the recheck clears when the fact is written again or said to still hold,
  and when the citation is taken back
- search hits and get_fact carry the recheck
- citations are between addresses: they survive a supersede on either side
  and follow a move, both ways
- the city: a tower per project, floors oldest first with their history and
  citation counts, skyways between live floors
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from seren_loci.config import LociConfig, StorageConfig
from seren_loci.mcp.tools import LociToolImpl
from seren_loci.models.schemas import FactMove, FactWrite
from seren_loci.store import LociStore

HIP, MEM = "seren-hippocampus", "seren-memory"


@pytest.fixture
def impl(tmp_path):
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci.db"), embedding_model=None))
    store = LociStore(cfg)
    yield LociToolImpl(store, cfg)
    store.close()


@pytest.fixture
def house(impl):
    impl.set_fact(project=HIP, key="sleep-touches-only-the-aging", value="A sleep decides only what is aging.",
                  why="design note, 22 Oct: only touch what is going to age out.")
    impl.set_fact(project=HIP, key="the-brief-is-optional", value="The brief is optional context.",
                  why="The aging clock picks what is drafted.")
    impl.set_fact(project="wren", key="session-end-routine", value="Write a brief when the shorts miss something.",
                  why="The brief is context now.")
    impl.set_fact(key="seren-license", value="Every Seren repo is AGPL-3.0.", why="No rug-pulls.")
    return impl


def test_cite_and_read_both_ways(house):
    r = house.cite_fact(project=HIP, key="the-brief-is-optional",
                        rests_on_project=HIP, rests_on_key="sleep-touches-only-the-aging")
    assert r["ok"] and r["note"] == "cited"
    assert house.cite_fact(project="wren", key="session-end-routine", rests_on_project=HIP,
                           rests_on_key="the-brief-is-optional", why="optional, so only when it adds")["ok"]
    assert house.cite_fact(project="wren", key="session-end-routine",
                           rests_on_key="seren-license")["ok"], "several, across projects"
    g = house.get_fact(project=HIP, key="the-brief-is-optional")
    assert [(c["project"], c["key"]) for c in g["rests_on"]] == [(HIP, "sleep-touches-only-the-aging")]
    assert g["rests_on"][0]["value"] == "A sleep decides only what is aging."
    assert [(c["project"], c["key"], c["why"]) for c in g["rested_on_by"]] == \
        [("wren", "session-end-routine", "optional, so only when it adds")]
    again = house.cite_fact(project="wren", key="session-end-routine", rests_on_project=HIP,
                            rests_on_key="the-brief-is-optional", why="reworded")
    assert again["note"].startswith("already cited")
    rests = house.get_fact(project="wren", key="session-end-routine")["rests_on"]
    assert next(c["why"] for c in rests if c["key"] == "the-brief-is-optional") == "reworded"
    assert "recheck" not in g and "rests_on" not in house.get_fact(key="seren-license")


def test_a_citation_needs_two_live_addresses_and_not_itself(house):
    assert "itself" in house.cite_fact(project=HIP, key="the-brief-is-optional", rests_on_project=HIP,
                                       rests_on_key="the-brief-is-optional")["error"]
    assert "no live fact" in house.cite_fact(project=HIP, key="the-brief-is-optional",
                                             rests_on_key="nope")["error"]
    assert "no live fact" in house.cite_fact(key="nope", rests_on_key="seren-license")["error"]
    assert not house.uncite_fact(key="seren-license", rests_on_key="nope")["ok"]


def test_a_superseded_reason_marks_what_rests_on_it(house):
    house.cite_fact(project=HIP, key="the-brief-is-optional", rests_on_project=HIP,
                    rests_on_key="sleep-touches-only-the-aging")
    w = house.set_fact(project=HIP, key="sleep-touches-only-the-aging", value="A sleep decides everything.",
                       why="changed our minds", why_changed="the aging flow was rolled back")
    assert any("1 fact rests on this one" in n for n in w["notes"])
    (row,) = house.facts_to_recheck()["facts"]
    assert (row["project"], row["key"]) == (HIP, "the-brief-is-optional")
    (b,) = row["because"]
    assert (b["key"], b["what"], b["why_changed"], b["reads_now"]) == \
        ("sleep-touches-only-the-aging", "superseded", "the aging flow was rolled back", "A sleep decides everything.")
    assert house.get_fact(project=HIP, key="the-brief-is-optional")["recheck"][0]["what"] == "superseded"
    hit = next(h for h in house.search_loci("brief optional context")["hits"] if h["key"] == "the-brief-is-optional")
    assert hit["recheck"] == [f"{HIP}/sleep-touches-only-the-aging superseded"]
    # only the ones resting on it directly: the routine rests on the brief fact, not on the sleep fact
    assert [(r["key"]) for r in house.facts_to_recheck()["facts"]] == ["the-brief-is-optional"]


def test_still_holds_or_a_rewrite_clears_it(house):
    house.cite_fact(project=HIP, key="the-brief-is-optional", rests_on_project=HIP,
                    rests_on_key="sleep-touches-only-the-aging")
    house.cite_fact(project="wren", key="session-end-routine", rests_on_project=HIP,
                    rests_on_key="sleep-touches-only-the-aging")
    house.set_fact(project=HIP, key="sleep-touches-only-the-aging", value="Reworded.", why="same point")
    assert house.facts_to_recheck()["count"] == 2
    assert house.still_holds(project=HIP, key="the-brief-is-optional")["cleared"] == 1
    assert not house.still_holds(project=HIP, key="the-brief-is-optional")["ok"], "once"
    house.set_fact(project="wren", key="session-end-routine", value="Rewritten after looking.", why="checked")
    assert house.facts_to_recheck()["count"] == 0
    assert house.get_fact(project="wren", key="session-end-routine")["rests_on"][0]["key"] == \
        "sleep-touches-only-the-aging", "a rewrite keeps what it rests on"


def test_a_retired_reason_marks_it_and_uncite_clears_it(house):
    house.cite_fact(key="seren-license", rests_on_project=HIP, rests_on_key="the-brief-is-optional")
    house.forget_fact(project=HIP, key="the-brief-is-optional", why_changed="gone")
    (row,) = house.facts_to_recheck()["facts"]
    assert row["because"][0]["what"] == "retired" and row["because"][0]["reads_now"] is None
    g = house.get_fact(key="seren-license")
    assert g["rests_on"][0]["live"] is False and g["rests_on"][0]["value"] is None
    assert house.uncite_fact(key="seren-license", rests_on_project=HIP, rests_on_key="the-brief-is-optional")["ok"]
    assert house.facts_to_recheck()["count"] == 0


def test_citations_follow_a_move_both_ways(house):
    s = house.store
    house.cite_fact(project=HIP, key="the-brief-is-optional", rests_on_project=HIP,
                    rests_on_key="sleep-touches-only-the-aging")
    house.cite_fact(project="wren", key="session-end-routine", rests_on_project=HIP,
                    rests_on_key="the-brief-is-optional")
    s.move_fact(FactMove(project=HIP, key="the-brief-is-optional", to_project=MEM, to_key="brief-is-optional",
                         why_changed="filed under memory"))
    c = s.citations(MEM, "brief-is-optional")
    assert [(x["project"], x["key"]) for x in c["rests_on"]] == [(HIP, "sleep-touches-only-the-aging")]
    assert [(x["project"], x["key"]) for x in c["rested_on_by"]] == [("wren", "session-end-routine")]
    assert house.facts_to_recheck()["count"] == 0, "a move changes nothing under anyone"
    assert s.citations(HIP, "the-brief-is-optional") == {"rests_on": [], "rested_on_by": [], "recheck": []}


def test_the_city(house):
    house.cite_fact(project="wren", key="session-end-routine", rests_on_project=HIP,
                    rests_on_key="the-brief-is-optional")
    house.set_fact(project=HIP, key="the-brief-is-optional", value="Still optional.", why="reworded")
    house.store.set_fact(FactWrite(project=HIP, key="the-brief-is-optional", value="Optional, really.", why="x"))
    c = house.store.city()
    assert [t["project"] for t in c["towers"]] == ["*", HIP, "wren"]
    hip = next(t for t in c["towers"] if t["project"] == HIP)
    assert [f["key"] for f in hip["floors"]] == ["sleep-touches-only-the-aging", "the-brief-is-optional"], \
        "oldest first: a tower is built from the ground up"
    brief = hip["floors"][1]
    assert (brief["history"], brief["rested_on_by"], brief["value"]) == (2, 1, "Optional, really.")
    routine = next(t for t in c["towers"] if t["project"] == "wren")["floors"][0]
    assert (routine["rests_on"], routine["recheck"]) == (1, 1)
    assert c["links"] == [{"project": "wren", "key": "session-end-routine", "on_project": HIP,
                           "on_key": "the-brief-is-optional", "why": None}]
    assert c["counts"] == {"towers": 3, "floors": 4, "skyways": 1, "recheck": 1}


def test_the_routes(client):
    for k in ("a", "b"):
        assert client.post("/fact", json={"project": "p", "key": k, "value": k.upper(), "why": "w"}).status_code == 200
    assert client.post("/fact/cite", json={"project": "p", "key": "a", "on_project": "p", "on_key": "b"}).json()["new"]
    assert client.post("/fact/cite", json={"project": "p", "key": "a", "on_project": "p", "on_key": "a"}).status_code == 400
    assert client.post("/fact/cite", json={"project": "p", "key": "a", "on_key": "zz"}).status_code == 404
    assert client.get("/fact/citations", params={"project": "p", "key": "b"}).json()["rested_on_by"][0]["key"] == "a"
    client.post("/fact", json={"project": "p", "key": "b", "value": "B2", "why": "w", "why_changed": "moved on"})
    assert client.get("/recheck").json()["facts"][0]["key"] == "a"
    assert client.post("/fact/still-holds", params={"project": "p", "key": "a"}).json()["cleared"] == 1
    assert client.get("/city").json()["counts"]["skyways"] == 1
    assert client.delete("/fact/cite", params={"project": "p", "key": "a", "on_project": "p", "on_key": "b"}).status_code == 200
    assert client.delete("/fact/cite", params={"project": "p", "key": "a", "on_project": "p", "on_key": "b"}).status_code == 404


def test_the_skylines_library_is_served_and_nothing_else(client):
    r = client.get("/viewer/vendor/three.min.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    assert client.get("/viewer/vendor/other.js").status_code == 404
    assert 'data-tab="skyline"' in client.get("/viewer").text
