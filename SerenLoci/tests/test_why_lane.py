"""
The why gets a vector of its own. 19 Oct 2026.

The finder embedded one text per fact, key + value + why, and the embedder
reads a window (MiniLM: the first 256 tokens). A long value pushed the why
out of it entirely in a store whose point is that the why is searchable. 
Now a fact holds a vector per lane - the whole fact as before, and 
key + why - and answers at the nearer.

The stub here is a bag of words that, like the real thing, reads only its
first WINDOW words, so 'the why fell out of the window' is reproducible
without torch. Pinned:

- a word only the why holds, behind a long value, finds the fact by vector
- a short fact's whole-fact lane is the text it always had
- every lane moves together: set, supersede, forget, move, prune, backfill
- one hit per fact, however many of its lanes are near
- a store indexed by the one-lane recipe rebuilds on boot, losslessly
- window notes, counted by the embedder's own tokenizer; silent on the floor
"""
from __future__ import annotations

import importlib.util
import math

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("sqlite_vec") is None,
                                reason="sqlite_vec not installed: no vector lane")

from seren_loci import store as store_mod
from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactMove, FactWrite
from seren_loci.store import LociStore

WINDOW = 12
DIM = 256


class _WindowTokenizer:
    """Counts words plus the two special tokens, like a BERT tokenizer would."""
    def __call__(self, text, add_special_tokens=True, truncation=False, verbose=True):
        ids = list(range(len(text.split())))
        return {"input_ids": ids + ([0, 0] if add_special_tokens else [])}


class _WindowEmbedder:
    """A bag of words over the first WINDOW words only: two texts are near
    when they share words inside the window, and a word past it is in no
    vector. Each word gets its own dimension from a vocabulary shared by 
    every instance (a reopened store must agree with the vectors the last 
    one wrote), so no two words collide the way hashed buckets did."""
    max_seq_length = WINDOW + 2
    tokenizer = _WindowTokenizer()
    vocab: dict[str, int] = {}

    def __init__(self, name: str):
        self._name = name

    def get_sentence_embedding_dimension(self) -> int:
        return DIM

    def encode(self, texts, normalize_embeddings: bool = True):
        out = []
        for t in texts:
            v = [0.0] * DIM
            for w in t.lower().split()[:WINDOW]:
                i = self.vocab.setdefault(w, len(self.vocab))
                assert i < DIM, "the stub's vocabulary is full: raise DIM"
                v[i] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    monkeypatch.setattr(store_mod, "_load_embedder", lambda name, device, cache_folder=None: _WindowEmbedder(name))


def _open(tmp_db, model="window") -> LociStore:
    return LociStore(LociConfig(storage=StorageConfig(db_path=tmp_db, embedding_model=model)))


def _lanes(store) -> dict[int, list[int]]:
    """fact rowid -> the lanes it holds a vector on."""
    out: dict[int, list[int]] = {}
    for (vr,) in store._conn.execute("SELECT vec_rowid FROM facts_vec ORDER BY vec_rowid"):
        out.setdefault(vr // store_mod._LANES, []).append(vr % store_mod._LANES)
    return out


def _rowid(store, project, key) -> int:
    return store._conn.execute("SELECT rowid FROM facts WHERE project=? AND key=? AND superseded_at IS NULL",
                               (project, key)).fetchone()[0]


LONG_VALUE = " ".join(f"filler{i}" for i in range(40))


# ── the why reaches a vector ────────────────────────────────────────────────

def test_a_word_only_the_why_holds_finds_the_fact_behind_a_long_value(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="eyes", value=LONG_VALUE, why="pinkeye"))
    s.set_fact(FactWrite(key="brace", value="new line", why="readability"))
    s.set_fact(FactWrite(key="cuda", value="compile time flag", why="runtime env ignored"))
    # the whole-fact text puts the why past the window: that lane cannot see it
    whole, why_lane = store_mod._finder_texts("*", "eyes", LONG_VALUE, "pinkeye")
    assert "pinkeye" not in " ".join(whole.split()[:WINDOW]) and "pinkeye" in why_lane
    near = s._finder._vector_search_augmented("pinkeye", 3)
    assert near[0][0] == s.get_fact("*", "eyes").id
    assert near[0][1] < near[1][1]
    s.close()


def test_a_short_fact_keeps_the_whole_fact_text_it_always_had():
    # fundamentals: exactly the old joined text, plus the why lane
    assert store_mod._finder_texts("*", "k", "v", "w") == ["k  v  w", "k  w"]
    assert store_mod._finder_texts("*", "k", "v", None) == ["k  v"]
    assert store_mod._finder_texts("*", "k", "v", "  ") == ["k  v    "], "an empty why gets no lane of its own"
    # a project leads both lanes (test_project_in_text.py)
    assert store_mod._finder_texts("Laionan", "title", "Warden", "the siege") == [
        "Laionan  title  Warden  the siege", "Laionan  title  the siege"]


# ── every lane moves together ───────────────────────────────────────────────

def test_lanes_follow_the_fact_through_set_supersede_forget_and_move(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="bare", value="no reason"))
    s.set_fact(FactWrite(key="reasoned", value="v", why="w"))
    bare, reasoned = _rowid(s, "*", "bare"), _rowid(s, "*", "reasoned")
    assert _lanes(s) == {bare: [0], reasoned: [0, 1]}

    s.set_fact(FactWrite(key="reasoned", value="v2"))                # supersede, and the why is gone
    reasoned2 = _rowid(s, "*", "reasoned")
    assert _lanes(s) == {bare: [0], reasoned2: [0]}

    s.set_fact(FactWrite(key="bare", value="now with one", why="found it"))
    bare2 = _rowid(s, "*", "bare")
    assert _lanes(s) == {reasoned2: [0], bare2: [0, 1]}

    s.move_fact(FactMove(key="bare", to_project="elsewhere", why_changed="tidy"))
    moved = _rowid(s, "elsewhere", "bare")
    assert _lanes(s) == {reasoned2: [0], moved: [0, 1]}

    assert s.forget("elsewhere", "bare", "done")
    assert _lanes(s) == {reasoned2: [0]}
    s.close()


def test_one_hit_per_fact_however_many_lanes_are_near(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="alpha", value="alpha beta", why="alpha beta"))
    s.set_fact(FactWrite(key="gamma", value="gamma", why="delta"))
    got = s._finder._vector_search_augmented("alpha beta", 5)
    ids = [fid for fid, _ in got]
    assert len(ids) == len(set(ids)) == 2
    hits, kind = s.search("alpha beta")
    assert kind == "hybrid" and len({h.id for h in hits}) == len(hits)
    s.close()


def test_a_floor_boot_then_a_vector_boot_prunes_and_backfills_every_lane(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="k", value="v", why="w"))
    s.close()
    floor = _open(tmp_db, model=None)                                 # no finder: vectors untouched
    floor.set_fact(FactWrite(key="k", value="v2", why="w2"))
    floor.set_fact(FactWrite(key="late", value="written on the floor", why="no finder then"))
    floor.close()
    s = _open(tmp_db)
    assert _lanes(s) == {_rowid(s, "*", "k"): [0, 1], _rowid(s, "*", "late"): [0, 1]}
    s.close()


# ── the one-lane index rebuilds ─────────────────────────────────────────────

def test_a_store_indexed_by_the_one_lane_recipe_rebuilds_on_boot(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(key="eyes", value=LONG_VALUE, why="pinkeye"))
    s.set_fact(FactWrite(key="brace", value="new line", why="readability"))
    # put the index back the way every store had it before 19 Oct 2026
    s._conn.execute("DROP TABLE facts_vec")
    s._conn.execute(f"CREATE VIRTUAL TABLE facts_vec USING vec0(fact_rowid INTEGER PRIMARY KEY, embedding FLOAT[{DIM}])")
    s._conn.execute("DELETE FROM loci_meta WHERE key='vec_recipe'")
    s._conn.commit()
    s.close()

    s = _open(tmp_db)
    assert s._conn.execute("SELECT value FROM loci_meta WHERE key='vec_recipe'").fetchone()[0] == store_mod._RECIPE
    assert "vec_rowid" in s._conn.execute("SELECT sql FROM sqlite_master WHERE name='facts_vec'").fetchone()[0]
    assert _lanes(s) == {_rowid(s, "*", "eyes"): [0, 1], _rowid(s, "*", "brace"): [0, 1]}
    assert s._finder._vector_search_augmented("pinkeye", 1)[0][0] == s.get_fact("*", "eyes").id
    s.close()


def test_the_two_lane_index_without_projects_rebuilds_with_them(tmp_db):
    s = _open(tmp_db)
    s.set_fact(FactWrite(project="Laionan", key="title", value="warden east", why="siege"))
    s.set_fact(FactWrite(project="Urgdurg", key="title", value="warden east", why="siege"))
    s._conn.execute("UPDATE loci_meta SET value='whole+why/2026-10-19' WHERE key='vec_recipe'")
    s._conn.commit()
    s.close()
    s = _open(tmp_db)
    assert s._conn.execute("SELECT value FROM loci_meta WHERE key='vec_recipe'").fetchone()[0] == store_mod._RECIPE
    # the same claim in two projects: only the project tells them apart, and now the vector can
    near = s._finder._vector_search_augmented("Laionan", 2)
    assert near[0][0] == s.get_fact("Laionan", "title").id and near[0][1] < near[1][1]
    s.close()


def test_a_same_recipe_reboot_does_not_rebuild(tmp_db, monkeypatch):
    _open(tmp_db).close()
    calls = []
    orig = store_mod._VectorFinder._rebuild
    monkeypatch.setattr(store_mod._VectorFinder, "_rebuild", lambda self: calls.append(1) or orig(self))
    _open(tmp_db).close()
    assert calls == []


# ── window notes ────────────────────────────────────────────────────────────

def test_a_value_past_the_window_is_noted_in_the_embedders_own_tokens(tmp_db):
    s = _open(tmp_db)
    w = FactWrite(key="eyes", value=LONG_VALUE, why="pinkeye")
    s.set_fact(w)
    (note,) = s.write_notes(w, replaced=False)
    # key + 40 words + 2 special tokens, against a window of WINDOW + 2
    assert "value part" in note and f"first {WINDOW + 2} tokens" in note and "runs to 43" in note
    assert "lexical search still sees every word" in note
    s.close()


def test_a_why_past_the_window_is_noted_and_a_short_fact_is_not(tmp_db):
    s = _open(tmp_db)
    w = FactWrite(key="k", value="short", why=LONG_VALUE)
    (note,) = s.write_notes(w, replaced=False)
    assert "why part" in note
    assert s.write_notes(FactWrite(key="k", value="short", why="also short"), replaced=False) == []
    s.close()


def test_the_floor_says_nothing_about_windows(store):
    assert store.write_notes(FactWrite(key="k", value=LONG_VALUE, why="w"), replaced=False) == []
