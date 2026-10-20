"""
Keeping a Loci: what whoever lives in one needs to tend it. 19 Oct 2026.

A look at a Loci, a month into heavy use, found the facts long (68 of 121
past the finder's window), the reasons facts had changed stuffed into the new
values' whys (there was nowhere else for them), 23 project names standing for
about 15, and two fundamentals gone stale - and nothing had ever said so. The
store tells; the inhabitant decides. Pinned here:

- why_changed: supersede and forget keep the reason on the RETIRED row, so
  the new why stays about the fact; a store from before the column gets it on
  boot, its old rows reading 'no reason given'
- write notes: no why, a why_changed with nothing to replace, a project name
  that folds onto one the store has - notes, never refusals
- move_fact: the old address retired with the reason, the new one carrying
  value, why and figures, history following the move back; refused onto a
  live value or onto itself; search scopes stay strict
- the same through the routes and the MCP tools
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from seren_loci.config import BackupConfig, FiguresConfig, LociConfig, StorageConfig
from seren_loci.models.schemas import FactMove, FactWrite
from seren_loci.store import LociStore


# ── why_changed ─────────────────────────────────────────────────────────────

def test_a_supersede_keeps_why_it_changed_on_the_old_value(store):
    store.set_fact(FactWrite(key="alice.stores", value="the LocalSystem profile", why="where the services run"))
    store.set_fact(FactWrite(key="alice.stores", value="the NUC", why="where the services run",
                             why_changed="moved to the NUC in October"))
    new, old = store.get_history("*", "alice.stores")
    assert new.is_live and new.why_changed is None
    assert new.why == "where the services run", "the new why stays about the fact"
    assert not old.is_live and old.why_changed == "moved to the NUC in October"


def test_a_supersede_without_a_reason_keeps_none(store):
    store.set_fact(FactWrite(key="k", value="v1", why="w"))
    store.set_fact(FactWrite(key="k", value="v2", why="w", why_changed="   "))
    assert [h.why_changed for h in store.get_history("*", "k")] == [None, None]


def test_forget_keeps_why_it_was_retired(store):
    store.set_fact(FactWrite(key="k", value="v", why="w"))
    assert store.forget("*", "k", "it stopped being true")
    (row,) = store.get_history("*", "k")
    assert not row.is_live and row.why_changed == "it stopped being true"


def test_a_retired_hit_carries_its_reason(store):
    store.set_fact(FactWrite(key="brace_style", value="tabs everywhere", why="the legacy tree"))
    store.set_fact(FactWrite(key="brace_style", value="four spaces", why="pep8",
                             why_changed="the legacy tree was rewritten"))
    hits, _ = store.search("tabs everywhere", include_superseded=True)
    old = [h for h in hits if not h.live]
    assert old and old[0].why_changed == "the legacy tree was rewritten"


def test_a_store_from_before_the_column_gets_it_on_boot(tmp_db):
    # The facts table as every store had it until 19 Oct 2026.
    conn = sqlite3.connect(tmp_db)
    conn.executescript("""
        CREATE TABLE facts (
            rowid INTEGER PRIMARY KEY, id TEXT NOT NULL UNIQUE, project TEXT NOT NULL,
            key TEXT NOT NULL, value TEXT NOT NULL, why TEXT, source TEXT NOT NULL DEFAULT 'user',
            created_at REAL NOT NULL, superseded_at REAL, superseded_by TEXT,
            extra TEXT NOT NULL DEFAULT '{}');
        INSERT INTO facts VALUES (1, 'a1', '*', 'k', 'old', 'w', 'user', 1.0, 2.0, 'a2', '{}');
        INSERT INTO facts VALUES (2, 'a2', '*', 'k', 'new', 'w', 'user', 2.0, NULL, NULL, '{}');
    """)
    conn.commit()
    conn.close()
    s = LociStore(LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None)))
    try:
        new, old = s.get_history("*", "k")
        assert (new.value, old.value) == ("new", "old") and old.why_changed is None
        s.set_fact(FactWrite(key="k", value="newer", why="w", why_changed="a third reading"))
        assert s.get_history("*", "k")[1].why_changed == "a third reading"
        hits, _ = s.search("old", include_superseded=True)
        assert any(h.value == "old" for h in hits), "the old rows are still searchable"
    finally:
        s.close()
    # and a second boot finds the column there and leaves it alone
    LociStore(LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None))).close()


# ── write notes ─────────────────────────────────────────────────────────────

def test_a_fact_with_a_why_and_a_clean_name_gets_no_notes(store):
    w = FactWrite(key="k", value="v", why="w", project="Seren")
    store.set_fact(w)
    assert store.write_notes(w, replaced=False) == []


def test_no_why_is_noted_and_the_fact_is_set_anyway(store):
    w = FactWrite(key="k", value="v")
    store.set_fact(w)
    (note,) = store.write_notes(w, replaced=False)
    assert "no why" in note
    assert store.get_fact("*", "k").value == "v"


def test_a_reason_with_nothing_to_replace_is_noted_as_not_kept(store):
    w = FactWrite(key="k", value="v", why="w", why_changed="it moved")
    store.set_fact(w)
    (note,) = store.write_notes(w, replaced=False)
    assert "why_changed was not kept" in note
    assert store.write_notes(w, replaced=True) == []


@pytest.mark.parametrize("have,wrote", [
    ("Seren", "seren"),
    ("SerenMemory", "seren-memory"),
    ("seren_hippocampus", "SerenHippocampus"),
    ("my notes", "MyNotes"),
])
def test_a_project_name_that_folds_onto_one_the_store_has_is_noted(store, have, wrote):
    for i in range(3):
        store.set_fact(FactWrite(project=have, key=f"k{i}", value="v", why="w"))
    w = FactWrite(project=wrote, key="k", value="v", why="w")
    store.set_fact(w)
    (note,) = store.write_notes(w, replaced=False)
    assert repr(have) in note and "3 live facts" in note and "move_fact" in note


def test_names_that_merely_share_a_prefix_are_left_alone(store):
    store.set_fact(FactWrite(project="SerenMemory", key="k", value="v", why="w"))
    w = FactWrite(project="SerenMargin", key="k", value="v", why="w")
    store.set_fact(w)
    assert store.write_notes(w, replaced=False) == []


# ── move ────────────────────────────────────────────────────────────────────

def _move(store, **kw) -> object:
    return store.move_fact(FactMove(**kw))


def test_a_move_retires_the_old_address_and_carries_the_fact(store):
    store.set_fact(FactWrite(project="seren", key="bedtime", value="20:09", why="User's sleep"))
    moved = _move(store, project="seren", key="bedtime", to_project="Seren", why_changed="one scope, one name")
    assert (moved.project, moved.key, moved.value, moved.why) == ("Seren", "bedtime", "20:09", "User's sleep")
    assert store.get_fact("seren", "bedtime") is None
    assert store.get_fact("Seren", "bedtime").id == moved.id
    (old,) = store.get_history("seren", "bedtime")
    assert old.superseded_by == moved.id and old.why_changed == "one scope, one name"


def test_history_follows_a_move_back_and_keeps_where_each_row_was_written(store):
    store.set_fact(FactWrite(project="seren", key="ports", value="7265", why="desktop"))
    store.set_fact(FactWrite(project="seren", key="ports", value="7250", why="the NUC", why_changed="moved"))
    _move(store, project="seren", key="ports", to_project="Seren", to_key="alice-ports", why_changed="merge")
    store.set_fact(FactWrite(project="Seren", key="alice-ports", value="7250-7255", why="the NUC",
                             why_changed="the whole band"))
    trail = store.get_history("Seren", "alice-ports")
    assert [f.value for f in trail] == ["7250-7255", "7250", "7250", "7265"]
    assert [(f.project, f.key) for f in trail] == [
        ("Seren", "alice-ports"), ("Seren", "alice-ports"), ("seren", "ports"), ("seren", "ports")]
    assert [f.why_changed for f in trail] == [None, "the whole band", "merge", "moved"]
    # the old address's own history is untouched: it ends at the move
    assert [f.value for f in store.get_history("seren", "ports")] == ["7250", "7265"]


def test_a_key_set_fresh_after_moving_away_keeps_its_own_story(store):
    store.set_fact(FactWrite(key="a", value="first", why="w"))
    _move(store, key="a", to_key="b")
    store.set_fact(FactWrite(key="a", value="a new fact at the old key", why="w"))
    assert [f.value for f in store.get_history("*", "b")] == ["first", "first"]
    assert [f.value for f in store.get_history("*", "a")] == ["a new fact at the old key", "first"]


def test_a_fact_moved_there_and_back_again_has_a_finite_history(store):
    store.set_fact(FactWrite(key="a", value="v", why="w"))
    _move(store, key="a", to_key="b")
    _move(store, key="b", to_key="a")
    trail = store.get_history("*", "a")
    assert len(trail) == 3 and trail[0].is_live and trail[0].key == "a"


def test_a_move_onto_a_live_value_is_refused_and_changes_nothing(store):
    store.set_fact(FactWrite(project="seren", key="k", value="mine", why="w"))
    store.set_fact(FactWrite(project="Seren", key="k", value="theirs", why="w"))
    with pytest.raises(ValueError, match="already holds a live value"):
        _move(store, project="seren", key="k", to_project="Seren")
    assert store.get_fact("seren", "k").value == "mine"
    assert store.get_fact("Seren", "k").value == "theirs"
    assert len(store.get_history("seren", "k")) == 1


def test_a_move_that_goes_nowhere_is_refused(store):
    store.set_fact(FactWrite(key="k", value="v", why="w"))
    with pytest.raises(ValueError, match="goes nowhere"):
        _move(store, key="k", to_project="*", to_key="k")


def test_a_move_with_nothing_live_to_move_answers_none(store):
    assert _move(store, key="never-set", to_key="elsewhere") is None
    store.set_fact(FactWrite(key="k", value="v", why="w"))
    store.forget("*", "k")
    assert _move(store, key="k", to_key="elsewhere") is None


def test_a_moved_fact_is_found_at_its_new_address_only(store):
    store.set_fact(FactWrite(project="seren", key="pinkeye", value="no eyeball after", why="hygiene"))
    _move(store, project="seren", key="pinkeye", to_project="Seren")
    hits, _ = store.search("eyeball", project="Seren", include_fundamentals=False)
    assert [h.project for h in hits] == ["Seren"]
    hits, _ = store.search("pinkeye", project="Seren", include_fundamentals=False, include_superseded=True)
    assert {h.project for h in hits} == {"Seren"}, "history from the old scope stays out of a scoped search"


def test_a_moved_fact_keeps_its_figures(tmp_path):
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    g = tmp_path / "gallery"
    g.mkdir()
    (g / "wiring.png").write_bytes(bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100)
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci" / "loci.db"), embedding_model=None),
                     backup=BackupConfig(every_hours=0), figures=FiguresConfig(sources=[str(g)]))
    with TestClient(create_app(cfg)) as c:
        s = c.app.state.store
        s.set_fact(FactWrite(project="node-a", key="fan", value="second header", why="the first is the pump"))
        fig = s.keep_figure(s.get_fact("node-a", "fan").id, g / "wiring.png")
        moved = _move(s, project="node-a", key="fan", to_project="xavier")
        assert [f.digest for f in moved.figures] == [fig.digest]
        assert [f.digest for f in s.get_history("node-a", "fan")[0].figures] == [fig.digest], "history keeps its own"
        r = c.get("/fact/figures", params={"project": "xavier", "key": "fan"})
        assert r.status_code == 200 and r.json()["count"] == 1
        assert c.get(f"/fact/figures/{fig.digest}", params={"project": "xavier", "key": "fan"}).status_code == 200


# ── routes ──────────────────────────────────────────────────────────────────

def test_the_set_route_answers_with_notes(client):
    r = client.post("/fact", json={"key": "k", "value": "v"}).json()
    assert r["ok"] and any("no why" in n for n in r["notes"])
    assert r["why_changed"] is None, "the field is there (this store keeps reasons) and nothing was replaced"
    r = client.post("/fact", json={"key": "k", "value": "v2", "why": "w", "why_changed": "it was wrong"}).json()
    assert r["superseded"] and r["why_changed"] == "it was wrong" and r["notes"] == []
    hist = client.get("/fact/history", params={"key": "k"}).json()["history"]
    assert [h["why_changed"] for h in hist] == [None, "it was wrong"]


def test_the_forget_route_takes_a_reason(client):
    client.post("/fact", json={"key": "k", "value": "v", "why": "w"})
    assert client.delete("/fact", params={"key": "k", "why_changed": "no longer true"}).status_code == 200
    (row,) = client.get("/fact/history", params={"key": "k"}).json()["history"]
    assert row["why_changed"] == "no longer true"


def test_the_move_route(client):
    client.post("/fact", json={"project": "seren", "key": "k", "value": "v", "why": "w"})
    client.post("/fact", json={"project": "Seren", "key": "taken", "value": "v", "why": "w"})
    r = client.post("/fact/move", json={"project": "seren", "key": "k", "to_project": "Seren", "why_changed": "merge"})
    assert r.status_code == 200
    body = r.json()
    assert body["fact"]["project"] == "Seren" and body["moved_from"] == {"project": "seren", "key": "k"}
    assert client.get("/fact", params={"project": "Seren", "key": "k"}).status_code == 200
    assert client.post("/fact/move", json={"project": "seren", "key": "k", "to_project": "Seren"}).status_code == 404
    client.post("/fact", json={"project": "seren", "key": "taken", "value": "v", "why": "w"})
    assert client.post("/fact/move", json={"project": "seren", "key": "taken", "to_project": "Seren"}).status_code == 409


# ── MCP tools ───────────────────────────────────────────────────────────────

@pytest.fixture
def tools(tmp_path):
    pytest.importorskip("mcp")
    from seren_loci.mcp.tools import LociToolImpl
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci.db"), embedding_model=None))
    s = LociStore(cfg)
    yield LociToolImpl(s, cfg)
    s.close()


def test_the_set_tool_keeps_why_changed_and_says_its_notes(tools):
    assert tools.set_fact(key="k", value="v", why="w")["notes"] == []
    r = tools.set_fact(key="k", value="v2", why="w", why_changed="it moved")
    assert r["superseded"] and r["why_changed"] == "it moved" and r["notes"] == []
    hist = tools.fact_history("k")["history"]
    assert hist[1]["why_changed"] == "it moved"
    assert "no why" in tools.set_fact(key="bare", value="v")["notes"][0]


def test_the_forget_tool_takes_a_reason(tools):
    tools.set_fact(key="k", value="v", why="w")
    assert tools.forget_fact("k", why_changed="retired on purpose")["ok"]
    assert tools.fact_history("k")["history"][0]["why_changed"] == "retired on purpose"


def test_the_move_tool(tools):
    tools.set_fact(key="k", value="v", why="w", project="seren")
    tools.set_fact(key="other", value="v", why="w", project="Seren")
    r = tools.move_fact("k", to_project="Seren", project="seren", why_changed="one scope")
    assert r["ok"] and (r["project"], r["key"]) == ("Seren", "k") and r["notes"] == []
    assert tools.get_fact("k", project="Seren")["found"]
    assert not tools.move_fact("k", to_project="Seren", project="seren")["ok"]
    tools.set_fact(key="other", value="v", why="w", project="seren")
    r = tools.move_fact("other", to_project="Seren", project="seren")
    assert not r["ok"] and "already holds a live value" in r["error"]
