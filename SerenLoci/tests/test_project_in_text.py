"""
The project is part of what the finder reads. 19 Oct 2026.

Probe's merged twin (eleven entities' facts in one Loci) showed the project
name was invisible to the finder: FTS indexed key, value and why, the vectors
embedded the same, and so 'What is Laionan's title now?' tied every title in
the store (MRR 0.25; 0.70 with the project in the text). The Callosum
searches a Loci unscoped unless asked, so the shape of an answer depends on it.

Pinned here (the floor; the vector side is in test_why_lane.py):
- an unscoped question naming a project finds that project's fact
- the possessive form too (test_fts_possessive.py is the word-level half)
- a store whose FTS table predates the column is rebuilt on boot, history
  included, once
- a moved fact is found under its new project, not its old one
- a scoped search is unchanged
"""
from __future__ import annotations

from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactMove, FactWrite
from seren_loci.store import LociStore, _FTS_COLUMNS


def _titles(store):
    store.set_fact(FactWrite(project="Laionan", key="title", value="Warden of the East", why="promoted after the siege"))
    store.set_fact(FactWrite(project="Lurggnashmug", key="title", value="Warden of the West", why="by the axe"))
    store.set_fact(FactWrite(project="Laionan", key="race", value="elf", why="born in the bough"))


def test_an_unscoped_question_naming_a_project_finds_that_projects_fact(store):
    _titles(store)
    for q in ("Laionan title", "What is Laionan's title now?", "Lurggnashmug warden"):
        hits, kind = store.search(q)
        assert kind == "lexical" and hits, q
        want = "Lurggnashmug" if "Lurggnashmug" in q else "Laionan"
        assert hits[0].project == want and hits[0].key in ("title",), (q, [(h.project, h.key) for h in hits])


def test_a_scoped_search_is_unchanged(store):
    _titles(store)
    hits, _ = store.search("warden", project="Lurggnashmug", include_fundamentals=False)
    assert [(h.project, h.key) for h in hits] == [("Lurggnashmug", "title")]


def test_a_moved_fact_is_found_under_its_new_project(store):
    store.set_fact(FactWrite(project="Lurggnashmug", key="sword", value="Irondrake", why="taken in the raid"))
    store.move_fact(FactMove(project="Lurggnashmug", key="sword", to_project="Laionan", why_changed="it changed hands"))
    assert [(h.project, h.key) for h in store.search("Laionan sword")[0]] == [("Laionan", "sword")]
    assert store.search("Lurggnashmug sword")[0] == [], "the old address is history, and history is opt-in"
    hist, _ = store.search("Lurggnashmug sword", include_superseded=True)
    assert [(h.project, h.live) for h in hist] == [("Lurggnashmug", False)]


def test_a_store_from_before_the_column_is_rebuilt_on_boot(tmp_db):
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None))
    s = LociStore(cfg)
    _titles(s)
    s.set_fact(FactWrite(project="Laionan", key="title", value="Lord of the East", why="crowned",
                         why_changed="crowned after the war"))
    # put the FTS table back the way every store had it before 19 Oct 2026
    s._conn.execute("DROP TABLE facts_fts")
    s._conn.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(key, value, why, content='facts', "
                    "content_rowid='rowid', tokenize='unicode61')")
    s._conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
    s._conn.execute("DELETE FROM loci_meta WHERE key='fts_columns'")
    s._conn.commit()
    assert s.search("Laionan title")[0] == [], "the old index cannot see the project"
    s.close()

    s = LociStore(cfg)
    try:
        cols = [r[1] for r in s._conn.execute("PRAGMA table_info(facts_fts)")]
        assert ",".join(cols) == _FTS_COLUMNS
        stamp = s._conn.execute("SELECT value FROM loci_meta WHERE key='fts_columns'").fetchone()[0]
        assert stamp == _FTS_COLUMNS
        assert [(h.project, h.value) for h in s.search("Laionan title")[0]] == [("Laionan", "Lord of the East")]
        hist, _ = s.search("Laionan title", include_superseded=True)
        assert {h.value for h in hist} == {"Lord of the East", "Warden of the East"}, "history is in the rebuilt index"
    finally:
        s.close()


def test_a_second_boot_leaves_the_index_alone(tmp_db, monkeypatch):
    cfg = LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=None))
    LociStore(cfg).close()
    s = LociStore(cfg)
    seen = []
    s._conn.set_trace_callback(seen.append)
    s._reconcile_fts()
    s._conn.set_trace_callback(None)
    assert not any("rebuild" in q or "DROP" in q for q in seen)
    s.close()
