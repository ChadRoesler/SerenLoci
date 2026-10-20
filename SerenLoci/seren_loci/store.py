"""
seren_loci.store
════════════════════════════════════════════════════════════════════════

The left brain's data layer. One sqlite file holds every fact; the access
path has three rungs, cheapest first:

    1. EXACT      get_fact(project, key) -> the live value, deterministically.
                  No embedding, no ranking. You know the address, you get the
                  thing. This is the spine and it runs free.

    2. LEXICAL    FTS5 full-text over (project, key, value, why). Live rows by default;
                  history too when a search asks for it (include_superseded).
                  The "I sort of remember the words" path. Still no vectors,
                  still floor-cheap.

    3. VECTOR     (additive) a sqlite-vec index over the LIVE facts only, built
                  when storage.embedding_model is set. The "this smells like
                  that CUDA thing" associative jump - find the door when you
                  don't know the key. The store works fully without it. History
                  never enters this lane: a retired value is not a door. Two
                  vectors a fact, the whole fact and its why (_finder_texts),
                  so a long value cannot push the why out of the window.

THE STRICT SUPERSEDE RULE - enforced by the DATABASE, not by hope:

    Exactly ONE live value per (project, key). We don't trust application code
    to remember that; a PARTIAL UNIQUE INDEX makes the invariant structural:

        CREATE UNIQUE INDEX ... ON facts(project, key) WHERE superseded_at IS NULL

    sqlite enforces it. A second live row for the same key is a hard constraint
    error, not a silent dupe. set_fact() supersedes-then-inserts inside one
    transaction so the window where two live rows could exist never opens.
    Old values are stamped superseded_at + a forward superseded_by link and
    kept as history. NEVER blended, never surfaced by default. (Facts need
    supersede HARDER than the conversational side - a vibed-together fact is
    worse than no fact.)

THE SCORE CONTRACT (so the corpus callosum's job is a merge, not a fistfight):

    Every hit carries a normalized 0..1 relevance:
        exact   -> 1.0            (it IS the thing)
        vector  -> 1/(1+distance) (mirrors SerenLoci's /search exactly)
        lexical -> bm25 squashed into 0..1, below exact
    That's the common currency. SCC compares this against SerenLoci's
    lane-weighted score directly - cosine-0.82 and exact-key-hit finally on
    one axis.

WHY THERE'S NO MIGRATION (and Memory has one):

    In SerenMemory the embeddings ARE the stored data - change the model and
    you must carefully re-embed in place or recall rots. Here the vector index
    is a DERIVED cache over the canonical text in `facts`. Nothing in facts_vec
    is the source of truth. So changing the embedder is a lossless REBUILD from
    text, never a migration: we stamp which embedder built the index and
    reconcile it on boot (see _VectorFinder). Lose the index entirely and you
    lose nothing but the time to re-encode a small table.
"""
from __future__ import annotations

import functools
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

from .config import LociConfig
from .models.schemas import Fact, FactMove, FactWrite, Figure, FUNDAMENTALS, SearchHit

if TYPE_CHECKING:
    from seren_sinew.blobs import BlobStore


_SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    rowid         INTEGER PRIMARY KEY,         -- bridges to FTS + vec
    id            TEXT    NOT NULL UNIQUE,      -- public uuid
    project       TEXT    NOT NULL,
    key           TEXT    NOT NULL,
    value         TEXT    NOT NULL,
    why           TEXT,
    source        TEXT    NOT NULL DEFAULT 'user',
    created_at    REAL    NOT NULL,
    superseded_at REAL,                          -- NULL == live
    superseded_by TEXT,
    extra         TEXT    NOT NULL DEFAULT '{}', -- JSON
    why_changed   TEXT                           -- a retired row's reason (_ensure_columns)
);

-- THE invariant: one live value per (project, key). Partial index = sqlite
-- enforces it only over live rows, so history can hold many rows per key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_live
    ON facts(project, key) WHERE superseded_at IS NULL;

-- exact-lookup index (covers the live-key get and project scans)
CREATE INDEX IF NOT EXISTS idx_facts_lookup ON facts(project, key, superseded_at);

-- FTS5 lexical index over EVERY row, live and history, via the bridging rowid.
-- Liveness is a WHERE clause on the join, not a property of the index, so a
-- search can opt into history without a second index. (Until 2026-09-23 the
-- FTS row was deleted on supersede, which made include_superseded a no-op:
-- accepted, echoed, and unable to find anything. The 'fts_scope' stamp in
-- loci_meta marks a store whose index has been rebuilt to cover history.)
-- The project is a column too (19 Oct 2026): a question naming its subject
-- ('Laionan's title') must find that project's fact in a store holding
-- many, and before this nothing a search could match said which project a
-- row belonged to. 'fts_columns' marks a store rebuilt with it.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    project, key, value, why,
    content='facts', content_rowid='rowid',
    tokenize='unicode61'
);

-- key/value scratch for store-level metadata. Today its only tenant is the
-- vector-index stamp ('vec_embedder' -> 'model::dim'), so the finder can tell
-- on boot whether the index it's looking at was built by the embedder that's
-- configured NOW. The embedding-free floor never reads this table; only the
-- vector finder does.
CREATE TABLE IF NOT EXISTS loci_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Figures: the exhibits a fact cites (seren_sinew.blobs; 15 Oct 2026). The
-- bytes sit in the figures folder under `digest`; this row is every word
-- about them. A figure belongs to ONE row of history: superseding a value
-- does not carry its figures forward (a new value cites what it cites), and
-- history keeps its own, like everything else here.
CREATE TABLE IF NOT EXISTS figures (
    fact_id   TEXT NOT NULL,
    digest    TEXT NOT NULL,
    bytes     INTEGER NOT NULL,
    mime      TEXT,
    caption   TEXT,
    source    TEXT,
    kept_at   REAL NOT NULL,
    PRIMARY KEY (fact_id, digest)
);
CREATE INDEX IF NOT EXISTS idx_figures_digest ON figures(digest);
"""

# The FTS table's columns, as _SCHEMA creates it and _reconcile_fts stamps it.
_FTS_COLUMNS = "project,key,value,why"
_FTS_TABLE_SQL = _SCHEMA[_SCHEMA.index("CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts"):].split(";")[0]

# A figure whose row was just written, or is about to be, must survive a
# sweep that runs in between: nothing younger than this is swept.
SWEEP_GRACE_SECONDS = 3600.0


def _serialized(fn):
    """Hold the store's lock for the whole call.

    ONE sqlite connection is shared by every thread that reaches the store
    (FastAPI's worker threads, the MCP tool runner). sqlite serialises
    separate CONNECTIONS; it does nothing for two threads on one. Two
    set_fact calls at once died with 'cannot start a transaction within a
    transaction' (seen live, 25 Sept 2026), and worse was possible: a read
    on the same connection inside another thread's open transaction sees its
    uncommitted rows, and one thread's rollback undoes the other's write.
    The store is small by design, so one lock around it costs nothing."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)
    return wrapper


class LociStore:
    """Owns the sqlite connection and the three access rungs."""

    def __init__(self, config: LociConfig, warm_in_background: bool = False,
                 figures: Optional["BlobStore"] = None):
        self._config = config
        self._db_path = config.resolved_db_path()
        # The exhibits facts cite (seren_sinew.blobs.BlobStore); None = this
        # store keeps none, and keep_figure says so.
        self.figures = figures
        # check_same_thread=False: FastAPI and the MCP runner reach the store
        # from worker threads. Every public method holds self._lock (see
        # _serialized) - that lock, not sqlite, is what keeps two threads off
        # the one connection at the same time.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")   # readers don't block the writer
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._ensure_columns()
        self._reconcile_fts()

        # Additive vector finder. None unless an embedder is configured AND the
        # optional deps import cleanly. Everything below degrades to FTS when
        # this is None - the floor never depends on it. Building the finder also
        # reconciles the vector index with the configured embedder (see below).
        #
        # warm_in_background (the service passes it): the store is usable at
        # once on the lexical floor and the finder arrives when the embedder
        # has loaded. Importing sentence-transformers alone was measured at
        # 34 s on the desktop (3 Oct 2026), and a service that does not listen
        # for that long outlasts an MCP client's reconnect attempts: after
        # every restart Loci's tools were gone until reconnected by hand.
        # Facts written meanwhile are picked up by the finder's backfill.
        self._finder: Optional["_HybridFinder"] = None
        self._closed = False
        self._warm_error: Optional[str] = None
        self._warming: Optional[threading.Thread] = None
        if warm_in_background and self._config.storage.embedding_model:
            self._warming = threading.Thread(target=self._warm_finder, name="loci-finder-warm", daemon=True)
            self._warming.start()
        else:
            self._finder = self._build_finder()

    def _warm_finder(self) -> None:
        """Load the embedder off the lock (the slow part), then build the
        finder on it. A failure leaves the lexical floor, as _build_finder's
        does."""
        import logging
        logger = logging.getLogger("seren_loci")
        t0 = time.time()
        try:
            st = self._config.storage
            model = _load_embedder(st.embedding_model, st.embedding_device,
                                   cache_folder=str(self._config.resolved_model_cache_path()))
            with self._lock:
                if self._closed:
                    return
                self._finder = _HybridFinder(self._conn, st.embedding_model, st.embedding_device,
                                             cache_folder=str(self._config.resolved_model_cache_path()), model=model)
            logger.info("[seren-loci] finder: hybrid, ready after %.1fs", time.time() - t0)
        except Exception as e:  # noqa: BLE001 - the floor never depends on the finder
            self._warm_error = f"{type(e).__name__}: {e}"
            logger.warning("hybrid finder disabled (%s) - staying lexical", self._warm_error)

    @property
    def finder_state(self) -> str:
        """ready | warming | failed | off - why the finder is or is not there."""
        if self._finder is not None:
            return "ready"
        if self._warming is not None and self._warming.is_alive():
            return "warming"
        if self._warm_error:
            return "failed"
        return "off"

    def wait_for_finder(self, timeout: Optional[float] = None) -> bool:
        """Block until a background warm-up has finished; True when the
        finder is there."""
        if self._warming is not None:
            self._warming.join(timeout)
        return self._finder is not None

    def _ensure_columns(self) -> None:
        """Add the columns a store written by an older build lacks. CREATE
        TABLE IF NOT EXISTS leaves an existing table as it was, so a column
        born later arrives here, once: why_changed (19 Oct 2026). It is
        nullable, so every older retired row reads as 'no reason given',
        which is what it was. It stays out of FTS: a retired row is found by
        its value and why, and its reason rides along on the hit."""
        have = {r["name"] for r in self._conn.execute("PRAGMA table_info(facts)")}
        if "why_changed" not in have:
            self._conn.execute("ALTER TABLE facts ADD COLUMN why_changed TEXT")
            self._conn.commit()

    def _reconcile_fts(self) -> None:
        """Make sure the FTS index covers history, once per store.

        A store written before 2026-09-23 dropped the FTS row of every value it
        superseded, so its history is lexically invisible. FTS5's external-
        content 'rebuild' re-reads the facts table in one statement; Loci is
        small by design, so this is a boot-time blink, and the stamp means it
        happens exactly once.

        The same goes for the index's columns: a store whose FTS table
        predates the project column (fts_columns) has it dropped and created
        again with today's columns, then rebuilt. Lossless - the FTS table is
        derived from `facts`, like the vectors."""
        def stamp(key: str) -> Optional[str]:
            row = self._conn.execute("SELECT value FROM loci_meta WHERE key=?", (key,)).fetchone()
            return row["value"] if row else None

        if stamp("fts_scope") == "all" and stamp("fts_columns") == _FTS_COLUMNS:
            return
        if stamp("fts_columns") != _FTS_COLUMNS:
            self._conn.execute("DROP TABLE IF EXISTS facts_fts")
            self._conn.execute(_FTS_TABLE_SQL)
        self._conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
        for key, value in (("fts_scope", "all"), ("fts_columns", _FTS_COLUMNS)):
            self._conn.execute(
                "INSERT INTO loci_meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        self._conn.commit()

    # ──────────────────────────────────────────────────────────────────
    #  finder construction (additive - the ceiling, not the floor)
    # ──────────────────────────────────────────────────────────────────
    def _build_finder(self) -> Optional["_HybridFinder"]:
        """Build the finder: hybrid (vector + FTS5) if an embedder is configured,
        else None (pure FTS5 lexical only).

        The hybrid finder runs BOTH semantic and lexical search in parallel and
        fuses results via reciprocal rank fusion (RRF) - see _HybridFinder.
        """
        model = self._config.storage.embedding_model
        if not model:
            return None  # embedding-free floor; FTS carries discovery
        try:
            cache_folder = str(self._config.resolved_model_cache_path())
            return _HybridFinder(
                self._conn, model, self._config.storage.embedding_device,
                cache_folder=cache_folder,
            )
        except Exception as e:  # noqa: BLE001
            # A misconfigured embedder must NOT take down the store. The left
            # brain still works exact + lexical; we just lose the associative
            # jump. Loud-ish (return None, caller can log) but never fatal.
            # Note: a half-finished rebuild that throws here leaves the stamp
            # unwritten, so the NEXT boot reconciles again from scratch - the
            # facts table is never at risk either way.
            import logging
            logging.getLogger("seren_loci").warning(
                "hybrid finder disabled (%s: %s) - falling back to lexical",
                type(e).__name__, e,
            )
            return None

    @property
    def finder_kind(self) -> str:
        if self._finder is None:
            return "lexical"
        return "hybrid"  # _HybridFinder is always used when an embedder is present

    # ──────────────────────────────────────────────────────────────────
    #  WRITE - strict supersede
    # ──────────────────────────────────────────────────────────────────
    @_serialized
    def set_fact(self, w: FactWrite) -> Fact:
        """Set (or replace) the live value for (project, key).

        If a live value already exists for that key it is superseded - stamped
        with superseded_at + a forward link to the new row, kept as history,
        with w.why_changed as the reason it stopped being the answer.
        The supersede + insert happen in ONE transaction so two live rows for a
        key never coexist (and the partial unique index would reject it anyway).
        """
        fact = Fact(project=w.project, key=w.key, value=w.value, why=w.why,
                    source=w.source, extra=w.extra)
        now = fact.created_at

        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            # Find the current live row (if any) for this key.
            old = cur.execute(
                "SELECT rowid, id FROM facts "
                "WHERE project=? AND key=? AND superseded_at IS NULL",
                (w.project, w.key),
            ).fetchone()

            if old is not None:
                # Stamp it superseded and point it forward. Its FTS row STAYS
                # (history is searchable when asked); its vector goes, because
                # the vector lane is live-only and a retired value must not
                # take a KNN seat from a live one.
                cur.execute(
                    "UPDATE facts SET superseded_at=?, superseded_by=?, why_changed=? WHERE rowid=?",
                    (now, fact.id, _clean(w.why_changed), old["rowid"]),
                )
                if self._finder is not None:
                    self._finder.delete(old["rowid"])

            self._insert_live(cur, fact)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return fact

    def _insert_live(self, cur, fact: Fact) -> int:
        """Insert `fact` as a new live row inside the caller's transaction:
        the row, its FTS row, its vectors. Returns the rowid."""
        cur.execute(
            "INSERT INTO facts(id, project, key, value, why, source, "
            "created_at, superseded_at, superseded_by, extra) "
            "VALUES(?,?,?,?,?,?,?,NULL,NULL,?)",
            (fact.id, fact.project, fact.key, fact.value, fact.why,
             fact.source.value, fact.created_at, json.dumps(fact.extra)),
        )
        new_rowid = cur.lastrowid
        self._fts_insert(cur, new_rowid, fact.project, fact.key, fact.value, fact.why)
        if self._finder is not None:
            self._finder.add(new_rowid, self._embed_texts(fact))
        return new_rowid

    @_serialized
    def forget(self, project: str, key: str, why_changed: Optional[str] = None) -> bool:
        """Retire the live value for a key (supersede with no replacement).

        'Forget is a flag, not a scalpel' - this is the soft retire: the row
        stays as history, it just stops being the live answer, and
        why_changed says why. Returns True if there was a live value to retire.
        """
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            old = cur.execute(
                "SELECT rowid FROM facts "
                "WHERE project=? AND key=? AND superseded_at IS NULL",
                (project, key),
            ).fetchone()
            if old is None:
                self._conn.rollback()
                return False
            cur.execute(
                "UPDATE facts SET superseded_at=?, why_changed=? WHERE rowid=?",
                (time.time(), _clean(why_changed), old["rowid"]),
            )
            if self._finder is not None:
                self._finder.delete(old["rowid"])
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    @_serialized
    def move_fact(self, m: FactMove) -> Optional[Fact]:
        """Move the live value at (project, key) to (to_project, to_key).

        Returns the new live row, or None when there is no live value to move.
        Raises ValueError when the move goes nowhere or the target already
        holds a live value: supersede or move that one first, never blend.

        The old address is retired the way a supersede retires it - stamped,
        pointed at the new row, with why_changed - and its history stays where
        it was written; no row of history is ever rewritten. The new row is a
        fresh row at the new address carrying the same value, why, source and
        extra, and citing the same figures (one file, two rows). get_history
        at the new address follows superseded_by back across the move, so the
        trail reads unbroken from either end. Built for merging project names
        that mean one scope."""
        to_project = m.project if m.to_project is None else m.to_project
        to_key = m.key if m.to_key is None else m.to_key
        if (to_project, to_key) == (m.project, m.key):
            raise ValueError("the move goes nowhere: that is the address it already has")
        cur = self._conn.cursor()
        try:
            cur.execute("BEGIN")
            old = cur.execute(
                "SELECT * FROM facts WHERE project=? AND key=? AND superseded_at IS NULL",
                (m.project, m.key),
            ).fetchone()
            if old is None:
                self._conn.rollback()
                return None
            if cur.execute(
                    "SELECT 1 FROM facts WHERE project=? AND key=? AND superseded_at IS NULL",
                    (to_project, to_key)).fetchone() is not None:
                raise ValueError(f"project={to_project!r} key={to_key!r} already holds a live value; "
                                 "supersede or move that one first")
            moved = Fact(project=to_project, key=to_key, value=old["value"], why=old["why"],
                         source=old["source"], extra=json.loads(old["extra"]) if old["extra"] else {})
            cur.execute(
                "UPDATE facts SET superseded_at=?, superseded_by=?, why_changed=? WHERE rowid=?",
                (moved.created_at, moved.id, _clean(m.why_changed), old["rowid"]),
            )
            if self._finder is not None:
                self._finder.delete(old["rowid"])
            self._insert_live(cur, moved)
            cur.execute(
                "INSERT INTO figures (fact_id, digest, bytes, mime, caption, source, kept_at) "
                "SELECT ?, digest, bytes, mime, caption, source, kept_at FROM figures WHERE fact_id=?",
                (moved.id, old["id"]),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return self._fact_by_id(moved.id)

    @_serialized
    def write_notes(self, w: FactWrite, replaced: bool) -> list[str]:
        """What the store sees in a write, said back to whoever made it.

        Notes, never refusals: the fact is set either way, and nothing here
        changes it. Whoever lives in the Loci decides what to do about them.
        `replaced`: whether this write superseded a live value.
        """
        notes: list[str] = []
        if not (w.why or "").strip():
            notes.append("no why given. A fact without its reason is a rule that gets broken the "
                         "first time it is inconvenient; set it again with one when there is one.")
        if _clean(w.why_changed) and not replaced:
            notes.append("why_changed was not kept: there was no live value to replace, and the "
                         "reason is kept on the value a write replaces.")
        notes += self._fold_notes(w.project)
        if self._finder is not None:
            notes += self._finder.window_notes(w.project, w.key, w.value, w.why)
        return notes

    def _fold_notes(self, project: str) -> list[str]:
        """A project name that reads as one this store already has, once case
        and separators are folded away ('seren' and 'Seren', 'SerenMemory' and
        'seren-memory'). No registry: Loci holds anything its inhabitant
        holds as fact, under names of their choosing, so the only list to
        check against is the one already here."""
        mine = _fold(project)
        rows = self._conn.execute(
            "SELECT project, COUNT(*) AS c FROM facts WHERE superseded_at IS NULL "
            "GROUP BY project ORDER BY project").fetchall()
        return [f"{project!r} reads as the same name as {r['project']!r} ({r['c']} live "
                f"fact{'' if r['c'] == 1 else 's'}). If they are one scope, move_fact brings them together."
                for r in rows if r["project"] != project and _fold(r["project"]) == mine]

    # ──────────────────────────────────────────────────────────────────
    #  READ - rung 1: exact deterministic lookup (the spine)
    # ──────────────────────────────────────────────────────────────────
    @_serialized
    def get_fact(self, project: str, key: str) -> Optional[Fact]:
        """The live value for (project, key), or None. Deterministic - no
        ranking, no embedding. This is what makes Loci a logic store: you
        either get THE value or nothing, never a stale-similar blend."""
        row = self._conn.execute(
            "SELECT * FROM facts "
            "WHERE project=? AND key=? AND superseded_at IS NULL",
            (project, key),
        ).fetchone()
        return self._attach([_row_to_fact(row)])[0] if row else None

    # ──────────────────────────────────────────────────────────────────
    #  FIGURES - the exhibits a fact cites
    # ──────────────────────────────────────────────────────────────────
    def _attach(self, facts: list[Fact]) -> list[Fact]:
        """Each fact with the figures its row cites (the rows, never the
        bytes)."""
        if not facts:
            return facts
        by_id: dict[str, list[Figure]] = {}
        ids = [f.id for f in facts]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._conn.execute(
                    f"SELECT * FROM figures WHERE fact_id IN ({marks}) ORDER BY kept_at, digest", chunk):
                by_id.setdefault(r["fact_id"], []).append(_row_to_figure(r))
        for f in facts:
            f.figures = by_id.get(f.id, [])
        return facts

    @_serialized
    def keep_figure(self, fact_id: str, src: Union[bytes, Path], caption: Optional[str] = None,
                    source: Optional[str] = None) -> Optional[Figure]:
        """Cite a new exhibit on a fact (live or history, by id): the bytes
        into the blob store, the row here. None when there is no such fact.
        Raises what the blob store raises (BlobTooLarge, BlobStoreFull) and
        ValueError for bytes that are not something Loci keeps - before
        anything is written."""
        if self.figures is None:
            raise ValueError("this store keeps no figures (figures.enabled is off)")
        from seren_sinew.blobs import head_of, kind_kept
        if self._conn.execute("SELECT 1 FROM facts WHERE id=?", (fact_id,)).fetchone() is None:
            return None
        mime = kind_kept(head_of(src))
        if mime is None:
            raise ValueError("not a picture, clip, sound or PDF by its first bytes - words go in the fact, "
                             "and nothing here trusts a filename")
        blob = self.figures.put(src)
        fig = Figure(digest=blob.digest, bytes=blob.bytes, mime=mime, caption=(caption or "").strip() or None,
                     source=(source or "").strip() or None)
        self._conn.execute(
            "INSERT OR REPLACE INTO figures (fact_id, digest, bytes, mime, caption, source, kept_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (fact_id, fig.digest, fig.bytes, fig.mime, fig.caption, fig.source, fig.kept_at))
        self._conn.commit()
        return fig

    @_serialized
    def cite_figure(self, fact_id: str, digest: str, caption: Optional[str] = None) -> Optional[Figure]:
        """Cite on `fact_id` a figure some row already holds (a new value
        keeping the old value's exhibit, by digest): the row is copied, the
        bytes are the one file. None when there is no such fact or no such
        figure."""
        if self._conn.execute("SELECT 1 FROM facts WHERE id=?", (fact_id,)).fetchone() is None:
            return None
        row = self._conn.execute("SELECT * FROM figures WHERE digest=? ORDER BY kept_at LIMIT 1", (digest,)).fetchone()
        if row is None or self.figures is None or not self.figures.has(digest):
            return None
        fig = Figure(digest=digest, bytes=row["bytes"], mime=row["mime"],
                     caption=(caption or "").strip() or row["caption"], source=row["source"])
        self._conn.execute(
            "INSERT OR REPLACE INTO figures (fact_id, digest, bytes, mime, caption, source, kept_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (fact_id, fig.digest, fig.bytes, fig.mime, fig.caption, fig.source, fig.kept_at))
        self._conn.commit()
        return fig

    @_serialized
    def figures_of(self, fact_id: str) -> list[Figure]:
        rows = self._conn.execute("SELECT * FROM figures WHERE fact_id=? ORDER BY kept_at, digest",
                                  (fact_id,)).fetchall()
        return [_row_to_figure(r) for r in rows]

    @_serialized
    def drop_figure(self, fact_id: str, digest: str) -> bool:
        """Take one figure off a fact's row. The bytes go too once no row
        cites them. True when the row was there."""
        n = self._conn.execute("DELETE FROM figures WHERE fact_id=? AND digest=?", (fact_id, digest)).rowcount
        self._conn.commit()
        if n:
            self.sweep_figures()
        return n > 0

    @_serialized
    def open_figure(self, fact_id: str, digest: str):
        """(Figure, open file) for one a fact cites, or None. The bytes are
        only ever handed out through a fact that cites them."""
        if self.figures is None:
            return None
        row = self._conn.execute("SELECT * FROM figures WHERE fact_id=? AND digest=?", (fact_id, digest)).fetchone()
        if row is None or not self.figures.has(digest):
            return None
        return _row_to_figure(row), self.figures.open(digest)

    @_serialized
    def referenced_digests(self) -> set[str]:
        return {r[0] for r in self._conn.execute("SELECT DISTINCT digest FROM figures")}

    @_serialized
    def sweep_figures(self, grace: float = SWEEP_GRACE_SECONDS) -> list[str]:
        """Remove the bytes no row cites any more (after a grace period)."""
        if self.figures is None:
            return []
        return self.figures.sweep(self.referenced_digests(), grace=grace)

    @_serialized
    def figure_totals(self) -> dict[str, int]:
        row = self._conn.execute("SELECT COUNT(*) AS c, COALESCE(SUM(bytes), 0) AS b FROM figures").fetchone()
        return {"count": int(row["c"]), "bytes": int(row["b"])}

    @_serialized
    def get_history(self, project: str, key: str) -> list[Fact]:
        """Every value this key has ever held, newest first. The audit trail
        the strict-supersede rule preserves.

        A move is followed back: when a row here came from another address
        (a row there names it in superseded_by), that address's history up to
        the move joins in, so a re-addressed fact's trail reads unbroken. Each
        row keeps the project and key it was written under."""
        rows: list[sqlite3.Row] = []
        self._history_into(project, key, None, rows, set())
        # rowid breaks ties: three values set inside one clock tick (Windows
        # time.time() steps in ~15ms) share a created_at and would otherwise
        # come back in arbitrary order, and "newest first" is the promise.
        rows.sort(key=lambda r: (r["created_at"], r["rowid"]), reverse=True)
        return self._attach([_row_to_fact(r) for r in rows])

    def _history_into(self, project: str, key: str, upto_rowid: Optional[int],
                      rows: list, seen: set) -> None:
        """Gather the rows at (project, key) - up to and including
        upto_rowid when following a move, since the old address may have been
        set fresh afterwards and that is its own story - then follow every
        move into them. `seen` makes a fact moved there and back again
        terminate."""
        q, params = "SELECT * FROM facts WHERE project=? AND key=?", [project, key]
        if upto_rowid is not None:
            q += " AND rowid <= ?"
            params.append(upto_rowid)
        here = [r for r in self._conn.execute(q, params).fetchall() if r["rowid"] not in seen]
        seen.update(r["rowid"] for r in here)
        rows.extend(here)
        ids = [r["id"] for r in here]
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            moved_in = self._conn.execute(
                f"SELECT rowid, project, key FROM facts WHERE superseded_by IN ({marks}) "
                f"AND NOT (project=? AND key=?)", (*chunk, project, key)).fetchall()
            for src in moved_in:
                if src["rowid"] not in seen:
                    self._history_into(src["project"], src["key"], src["rowid"], rows, seen)

    @_serialized
    def list_facts(self, project: Optional[str] = None,
                   include_superseded: bool = False) -> list[Fact]:
        """All live facts, optionally scoped to a project. For the viewer and
        bulk export."""
        q = "SELECT * FROM facts"
        clauses, params = [], []
        if not include_superseded:
            clauses.append("superseded_at IS NULL")
        if project is not None:
            clauses.append("project=?")
            params.append(project)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        q += " ORDER BY project, key"
        rows = self._conn.execute(q, params).fetchall()
        return self._attach([_row_to_fact(r) for r in rows])

    # ──────────────────────────────────────────────────────────────────
    #  READ - rungs 2 & 3: discovery (search)
    # ──────────────────────────────────────────────────────────────────
    @_serialized
    def search(self, query: str, project: Optional[str] = None,
               n_results: int = 10, include_fundamentals: bool = True,
               include_superseded: bool = False) -> tuple[list[SearchHit], str]:
        """Discovery search. Returns (hits, finder_kind).

        Order of operations:
          1. EXACT: if `query` names a live key verbatim, that fact leads at
             score 1.0 (you asked for the address, you get the thing).
          2. FINDER: vector if available, else FTS lexical, over the in-scope
             live facts. Normalized scores, below exact.
          3. merge (exact wins ties by id), sort, trim.

        include_superseded folds HISTORY in: every value the key has held
        joins the exact rung (below the live one, never above it), and the
        lexical lane searches retired rows too. The vector lane stays live-only
        on purpose - a retired value is not a door, and it must not take a KNN
        seat from a live one. Each hit says which it is via `live`.
        """
        scopes = self._resolve_scopes(project, include_fundamentals, include_superseded)
        hits: dict[str, SearchHit] = {}  # id -> hit, exact-wins dedupe

        # 1. exact key hit (deterministic, score 1.0); history at 0.9 when asked
        for scope in scopes:
            f = self.get_fact(scope, query)
            if f is not None:
                hits[f.id] = _hit(f, score=1.0, kind="exact")
            if include_superseded:
                # get_history follows a move back across addresses; a search
                # stays inside the scopes it was asked for.
                for h in self.get_history(scope, query):
                    if not h.is_live and h.id not in hits and h.project in scopes:
                        hits[h.id] = _hit(h, score=_EXACT_HISTORY, kind="exact")

        # 2. finder - hybrid (vector + FTS5 fused via RRF) when embedder present,
        #    pure FTS5 lexical otherwise.
        if self._finder is not None:
            # _HybridFinder.search returns (fact_id, rrf_score, vec_distance,
            # bm25_rel). The rrf_score ORDERS candidates within this store; the
            # ABSOLUTE per-lane signals are what become the cross-store-comparable
            # relevance score. We must NOT score by RRF: a doc at rank 0 in both
            # lanes always fuses to 2/RRF_K regardless of how good the match
            # really is, so every single-entity store reported an identical top
            # score and the corpus callosum's fusion collapsed into a config-order
            # tie (the All-scc HR 0.056). Scoring from distance/bm25 instead lets
            # a store whose nearest fact is genuinely far from the query report a
            # WEAK hit - which is the whole point. (raw_distance, long null on
            # this path, now carries the real vector distance too.)
            for fid, _rrf, distance, bm25_rel in self._finder.search(
                    query, n_results * 2, scopes,
                    include_superseded=include_superseded):
                f = self._fact_by_id(fid)
                if f is None or f.project not in scopes:
                    continue
                if not f.is_live and not include_superseded:
                    continue
                if f.id in hits:
                    continue  # exact already claimed it
                score = _hybrid_score(distance, bm25_rel)
                hits[f.id] = _hit(f, score=round(score, 6), kind="hybrid",
                                  raw_distance=distance)
        else:
            for fid, rel in self._fts_search(query, scopes, n_results * 2,
                                             include_superseded=include_superseded):
                f = self._fact_by_id(fid)
                if f is None or f.id in hits:
                    continue
                # bm25 'rel' is >=0, larger = better. An FTS MATCH is relevant
                # BY DEFINITION, so map strength into [0.05, 1.0): a weak-but-
                # real hit floors above 0 (so SCC never reads a genuine match as
                # noise) while staying strictly below an exact hit's 1.0 and
                # preserving relative order. Calibration-tunable, not load-bearing.
                base = rel / (1.0 + rel)
                score = 0.05 + 0.95 * base
                hits[f.id] = _hit(f, score=round(score, 6), kind="lexical")

        ordered = sorted(hits.values(), key=lambda h: h.score, reverse=True)
        return ordered[:n_results], self.finder_kind

    # ──────────────────────────────────────────────────────────────────
    #  internals
    # ──────────────────────────────────────────────────────────────────
    def _resolve_scopes(self, project: Optional[str],
                        include_fundamentals: bool,
                        include_superseded: bool = False) -> list[str]:
        if project is None:
            # search everything: fundamentals + every project that has facts
            # (a project that only has history still counts when asked)
            live = "" if include_superseded else " WHERE superseded_at IS NULL"
            rows = self._conn.execute(
                f"SELECT DISTINCT project FROM facts{live}"
            ).fetchall()
            return [r["project"] for r in rows]
        scopes = [project]
        if include_fundamentals and project != FUNDAMENTALS:
            scopes.append(FUNDAMENTALS)
        return scopes

    def _fact_by_id(self, fid: str) -> Optional[Fact]:
        row = self._conn.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
        return self._attach([_row_to_fact(row)])[0] if row else None

    def _embed_texts(self, fact: Fact) -> list[str]:
        """What we hand the embedder for a fact, one text per lane. Delegates
        to the module-level _finder_texts so the set_fact path and the index
        rebuild share ONE recipe and can't drift (a DTO-asymmetry-style silent
        killer if they ever did)."""
        return _finder_texts(fact.project, fact.key, fact.value, fact.why)

    def _fts_insert(self, cur, rowid: int, project: str, key: str, value: str,
                    why: Optional[str]) -> None:
        cur.execute(
            "INSERT INTO facts_fts(rowid, project, key, value, why) VALUES(?,?,?,?,?)",
            (rowid, project, key, value, why or ""),
        )

    def _fts_search(self, query: str, scopes: list[str], limit: int,
                    include_superseded: bool = False) -> list[tuple[str, float]]:
        """FTS5 MATCH over facts in scope - live only unless asked. Returns
        (fact_id, relevance) with relevance >= 0 (larger = better). bm25()
        returns smaller-is-better reals (negative = strong match), so we negate."""
        return _fts_search(self._conn, query, scopes, limit, include_superseded)

    @_serialized
    def counts(self) -> dict[str, int]:
        live = self._conn.execute(
            "SELECT COUNT(*) c FROM facts WHERE superseded_at IS NULL"
        ).fetchone()["c"]
        total = self._conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        projects = self._conn.execute(
            "SELECT COUNT(DISTINCT project) c FROM facts WHERE superseded_at IS NULL"
        ).fetchone()["c"]
        figures = self._conn.execute("SELECT COUNT(*) c FROM figures").fetchone()["c"]
        return {"live": live, "history": total - live, "projects": projects, "figures": figures}

    @_serialized
    def close(self) -> None:
        self._closed = True                                # a warm-up still loading must not build on a closed connection
        try:
            self._conn.commit()
        except Exception:  # noqa: BLE001
            pass
        self._conn.close()


# ── module helpers ──────────────────────────────────────────────────────────

def _row_to_fact(row: sqlite3.Row) -> Fact:
    return Fact(
        id=row["id"], project=row["project"], key=row["key"],
        value=row["value"], why=row["why"], source=row["source"],
        created_at=row["created_at"], superseded_at=row["superseded_at"],
        superseded_by=row["superseded_by"], why_changed=row["why_changed"],
        extra=json.loads(row["extra"]) if row["extra"] else {},
    )


def _clean(text: Optional[str]) -> Optional[str]:
    """A reason as given, or None for nothing / only whitespace."""
    return (text or "").strip() or None


def _fold(name: str) -> str:
    """A project name with case and separators folded away, for telling
    when two names are one: 'SerenMemory', 'seren-memory', 'seren_memory'."""
    return re.sub(r"[\s_.\-]+", "", name).casefold()


def _row_to_figure(row: sqlite3.Row) -> Figure:
    return Figure(digest=row["digest"], bytes=row["bytes"], mime=row["mime"], caption=row["caption"],
                  source=row["source"], kept_at=row["kept_at"])


def _hit(f: Fact, score: float, kind: str,
         raw_distance: Optional[float] = None) -> SearchHit:
    return SearchHit(id=f.id, project=f.project, key=f.key, value=f.value,
                     why=f.why, score=score, match_kind=kind,
                     source=f.source.value, raw_distance=raw_distance,
                     live=f.is_live, superseded_at=f.superseded_at,
                     why_changed=f.why_changed, figures=len(f.figures))


# An exact-key hit on a RETIRED value, when history was asked for. Below the
# live exact's 1.0 by a margin no lane score is likely to cross, so "what we
# used to think" never outranks "what we think now" and still leads the fuzzy
# matches for the same key.
_EXACT_HISTORY = 0.9


def _finder_texts(project: str, key: str, value: str, why: Optional[str]) -> list[str]:
    """The texts the embedder sees for a fact, one per LANE, each its own
    vector, the nearer one answering for the fact:

        lane 0  project + key + value + why   the whole fact, with where it lives
        lane 1  project + key + why           only when there is a why

    The project leads both (19 Oct 2026, measured by Probe on a store holding
    eleven entities' facts: MRR 0.25 to 0.70 with the project in the text,
    lexical and vector). Without it nothing the finder read said whose title
    a 'title' fact was, so a question naming its subject tied every title in
    the store - and the Callosum searches a Loci unscoped unless asked. It is
    also what the scoped query augmentation already puts on the query side.
    Fundamentals ('*') add nothing: they belong everywhere.

    So the associative jump can latch onto any of them ('that CUDA thing'
    should find a fact whose WHY mentions CUDA even if the key doesn't) - and
    keep latching when the fact is long. The embedder reads a window (MiniLM:
    256 tokens), and with one joined text a long value pushed the why out of
    it entirely in a store whose point
    is that the why is searchable. Lane 0 is the text a fact always had, with
    its project in front; lane 1 can only bring a fact nearer. The project and
    key ride in both so each lane knows what it is about.

    The ONE recipe - set_fact and the index rebuild both go through here so
    they can never drift. Changing it means changing _RECIPE, so every store
    rebuilds its index from text on the next boot."""
    head = [key] if project == FUNDAMENTALS else [project, key]
    texts = ["  ".join(head + [value] + ([why] if why else []))]
    if why and why.strip():
        texts.append("  ".join(head + [why]))
    return texts


# How many vectors a fact may hold, and which lane is which. A fact's vectors
# sit at vec_rowid = fact rowid * _LANES + lane, so `vec_rowid / _LANES` is
# the fact's rowid in SQL (integer division) and its lanes sit side by side.
_LANES = 2
# Names the recipe _finder_texts embeds. Stamped in loci_meta beside the
# embedder; a store whose index was built by another recipe (or before there
# was a stamp for it) rebuilds from text on boot - lossless, like a model change.
_RECIPE = "project:whole+why/2026-10-19"


# The exact rung owns 1.0. Every non-exact hit MUST stay strictly below it, or
# the corpus callosum can't distinguish "I have EXACTLY this" from "I have
# something close" - which is what let 22 stores tie at the top and made most of
# them structurally unreachable.
_MAX_NONEXACT = 0.99


def _hybrid_score(distance: Optional[float], bm25_rel: Optional[float]) -> float:
    """Absolute relevance for a hybrid hit, on the store's 0..1 contract axis.

    Deliberately NOT the RRF value. RRF is a RANK fusion: a doc at rank 0 in both
    lanes always fuses to 2/RRF_K regardless of how weak the match actually is,
    so RRF scores are not comparable across stores and collapse SCC's fusion into
    a tie. We score from the ABSOLUTE lane signals the module contract already
    prescribes:

        vector  -> 1/(1+distance)              (mirrors SerenLoci /search)
        lexical -> 0.05 + 0.95 * bm25/(1+bm25)

    and fuse the two with a noisy-OR (1 - prod(1 - s)) so a hit supported by BOTH
    lanes scores higher than either alone - more independent evidence, higher
    confidence - while a fact that is semantically far AND only a weak stopword
    match stays low even when it is its own store's rank-0. Capped strictly below
    the exact 1.0 so a lone near-perfect vector can't impersonate an exact key.
    """
    lane_scores: list[float] = []
    if distance is not None:
        lane_scores.append(1.0 / (1.0 + distance))
    if bm25_rel is not None:
        base = bm25_rel / (1.0 + bm25_rel)
        lane_scores.append(0.05 + 0.95 * base)
    if not lane_scores:
        # No lane fired (shouldn't happen for a hybrid candidate) - never emit a
        # phantom score if it somehow does.
        return 0.0
    # noisy-OR: both lanes agreeing lifts confidence above either lane alone.
    miss = 1.0
    for s in lane_scores:
        miss *= (1.0 - s)
    return min(1.0 - miss, _MAX_NONEXACT)


# Retrieval-noise words stripped from the OR (long-query) branch of _fts_query.
# ORing EVERY token of a long question - "What title does Cewellric hold?" - makes
# the scaffolding ("what", "does") match unrelated facts all over the store and drown
# the one rare term that actually identifies the answer ("Cewellric"). That is the
# stopword bleed that handed the corpus callosum a confident hit from a store
# answering about a DIFFERENT entity. Deliberately CONSERVATIVE - articles,
# prepositions, question-words, and auxiliary verbs only, never domain words -
# because an over-eager list would eat a genuinely rare content term. Postel's law:
# lenient on the query, but don't let filler outvote substance.
_FTS_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "did", "do", "does",
    "for", "from", "had", "has", "have", "how", "in", "into", "is", "it", "its",
    "of", "on", "or", "that", "the", "their", "them", "then", "there", "these",
    "this", "to", "was", "were", "what", "when", "where", "which", "who", "whom",
    "whose", "why", "will", "with", "would",
})


# A possessive on the end of a query word: 's / 's, or a bare apostrophe (see
# _fts_query). Straight and curly, either case.
_POSSESSIVE = re.compile(r"(?:['’][sS]|['’])$")


def _fts_search(conn: sqlite3.Connection, query: str, scopes: list[str],
                limit: int, include_superseded: bool = False
                ) -> list[tuple[str, float]]:
    """The ONE lexical query, shared by the store and the hybrid finder so the
    two can never drift on what 'in scope' means."""
    if not scopes:
        return []
    placeholders = ",".join("?" for _ in scopes)
    live = "" if include_superseded else "  AND f.superseded_at IS NULL "
    try:
        rows = conn.execute(
            f"SELECT f.id AS id, -bm25(facts_fts) AS rel "
            f"FROM facts_fts "
            f"JOIN facts f ON f.rowid = facts_fts.rowid "
            f"WHERE facts_fts MATCH ? "
            f"{live}"
            f"  AND f.project IN ({placeholders}) "
            f"ORDER BY rel DESC LIMIT ?",
            (_fts_query(query), *scopes, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        # malformed MATCH (e.g. user typed bare punctuation) -> no lexical
        # hits rather than a 500. The exact rung already ran.
        return []
    return [(r["id"], max(r["rel"], 0.0)) for r in rows]


def _fts_query(raw: str) -> str:
    """Make a user string safe-ish for FTS5 MATCH. We wrap each whitespace
    token in double quotes (FTS5 phrase) so punctuation in 'posh.brace_style'
    or 'cuda-compat' doesn't get read as MATCH operators. Empty -> a token that
    matches nothing rather than erroring.

    For short queries (<= 3 tokens) we use AND (default FTS5 behavior - all
    quoted terms must appear). For longer queries we switch to OR so that a
    single matching token suffices; this prevents long natural-language queries
    from returning zero lexical hits when only a few terms happen to appear in
    any fact row.

    THE OR BRANCH STOPWORD-FILTERS (the precision fix). ORing every token of a long
    question lets the scaffolding match unrelated facts and drown the discriminating
    term, so the OR is built from CONTENT tokens only. The AND/OR choice stays on the
    RAW token count on purpose: a long question must not collapse into an over-strict
    AND when filtering happens to leave <=3 content words - requiring the incidental
    'hold' in 'what title does X hold' would drop the real answer too. The short-query
    AND branch is untouched (every word of a 1-3 term query is deliberate), and an
    all-stopword query keeps its raw tokens rather than degrading to match-nothing.

    POSSESSIVES ARE STRIPPED before quoting (19 Oct 2026, found by Probe on the
    Vellmarch merged twin). unicode61 splits "Laionan's" into 'laionan' 's', so the
    quoted phrase only matched a row that itself said "Laionan's": 'What is
    Laionan's title now?' lost the one word that named its subject, and any
    question about someone's anything with it. A trailing 's or 's (straight or
    curly), or a bare trailing apostrophe, comes off; a word that was nothing else
    is dropped.

    HONEST LIMIT: this removes the pure-scaffolding bleed, not content-ish collisions
    like a question's 'title'/'hold' matching a 'ruler_title' / '...-hold' fact - those
    still surface and are left to the relevance floor + bm25's IDF to rank down. An
    IDF-weighted query builder (weight rare query terms over common ones) is the
    deeper fix if the floor ever proves insufficient."""
    tokens = [t for t in (_POSSESSIVE.sub("", t.replace('"', '')) for t in raw.split()) if t.strip()]
    if not tokens:
        return '"__seren_loci_no_match__"'
    if len(tokens) <= 3:
        return " ".join(f'"{t}"' for t in tokens)          # short query: AND on every word, unchanged
    content = [t for t in tokens if t.lower() not in _FTS_STOPWORDS] or tokens
    return " OR ".join(f'"{t}"' for t in content)          # long query: OR on content tokens only


def _load_embedder(model_name: str, device: str, cache_folder: str | None = None):
    """Build the sentence-transformers model behind the finder.

    Isolated as a module function ON PURPOSE: it's the single seam tests
    monkeypatch to inject a stub embedder, so the finder's reconcile / rebuild /
    backfill logic can be exercised against REAL sqlite-vec without dragging
    torch into CI. (This is the 'make the encoder injectable' nicety that
    test_vector_sql.py filed.)

    cache_folder redirects the HuggingFace Hub download root away from the
    global OS cache (~/.cache/huggingface/hub/) and into the Loci data dir
    (~/.seren-loci/models/ by default). The model downloads there on first boot
    and is read from there on every subsequent boot - no network, no HF cache
    involvement, no 'pytorch_model.bin not found' surprises on a fresh box.
    Copying ~/.seren-loci/ to a new machine carries the weights with it.

    FALLBACK: if HuggingFace is unreachable or the cache is empty, the model
    tar.gz is fetched from the GitHub Release that shipped the running version
    of seren-loci. The archive is extracted into cache_folder so subsequent
    boots load entirely locally. Only fires on OSError (network failure /
    missing cache) and only for proper tagged releases (dev builds surface the
    error clearly instead of attempting a download that can't exist).
    """
    from sentence_transformers import SentenceTransformer
    try:
        return SentenceTransformer(model_name, device=device, cache_folder=cache_folder)
    except OSError as primary_err:
        return _load_embedder_from_release(
            model_name, device, cache_folder, primary_err,
        )


def _load_embedder_from_release(
    model_name: str,
    device: str,
    cache_folder: str | None,
    primary_err: OSError,
):
    """Fallback: download the model tar.gz from the GitHub Release that
    shipped the running version of seren-loci and load from the extracted dir.

    URL shape: https://github.com/ChadRoesler/SerenLoci/releases/download/
               v{version}/{slug}.tar.gz
    where slug = model_name.split('/')[-1]  (e.g. 'all-MiniLM-L6-v2')

    The archive's top-level dir IS the slug, so extractall(cache_folder) lands
    at cache_folder/slug/, which is exactly what SentenceTransformer expects
    when given a local path.
    """
    import importlib.metadata
    import logging
    import re
    import tarfile
    import urllib.request
    from pathlib import Path
    from sentence_transformers import SentenceTransformer

    log = logging.getLogger("seren_loci")

    # -- version guard: dev/editable builds have no matching release asset --
    try:
        version = importlib.metadata.version("seren-loci")
    except importlib.metadata.PackageNotFoundError:
        version = "0.0.0+unknown"

    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise OSError(
            f"[seren-loci] Cannot load embedder '{model_name}' from HuggingFace "
            f"({primary_err}) and this is a dev build ({version}) with no "
            f"matching release asset. Either fix the HuggingFace error or run "
            f"from a tagged release."
        ) from primary_err

    slug = model_name.split("/")[-1]
    url = (
        f"https://github.com/ChadRoesler/SerenLoci"
        f"/releases/download/v{version}/{slug}.tar.gz"
    )

    # cache_folder is guaranteed non-None here: _build_finder always resolves
    # it before calling us. Belt-and-suspenders default just in case.
    cache_path = Path(cache_folder) if cache_folder else Path.home() / ".seren-loci" / "models"
    cache_path.mkdir(parents=True, exist_ok=True)
    local_model_dir = cache_path / slug

    # Already extracted by a previous fallback run - load directly.
    if local_model_dir.is_dir():
        log.info("[seren-loci] loading embedder from cached release copy: %s", local_model_dir)
        return SentenceTransformer(str(local_model_dir), device=device)

    archive = cache_path / f"{slug}.tar.gz"
    log.warning(
        "[seren-loci] HuggingFace unreachable (%s: %s) - downloading %s from release v%s",
        type(primary_err).__name__, primary_err, slug, version,
    )
    try:
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as tf:
            # filter='data' (Python 3.12+) strips unsafe members; fall back
            # gracefully on 3.10/3.11 where the kwarg doesn't exist yet.
            try:
                tf.extractall(cache_path, filter="data")
            except TypeError:
                tf.extractall(cache_path)  # noqa: S202 - pre-3.12 fallback
    except Exception as fallback_err:
        raise OSError(
            f"[seren-loci] Cannot load embedder '{model_name}': "
            f"HuggingFace failed ({primary_err}) and release fallback also "
            f"failed ({fallback_err}). "
            f"To fix: copy the model dir to {local_model_dir} and restart."
        ) from fallback_err
    finally:
        archive.unlink(missing_ok=True)

    log.info("[seren-loci] embedder downloaded from release, loading from %s", local_model_dir)
    return SentenceTransformer(str(local_model_dir), device=device)


# How many rows the scoped KNN asks vec0 for. Loci is a facts table (hundreds,
# low thousands), so 'all of them' is the honest answer and brute force over
# that is sub-millisecond. Past the ceiling the project filter sees only the
# nearest 10k vectors (two lanes a fact: about 5k facts) and a scoped search
# can miss - acceptable for a store this was never meant to be, and the
# ceiling exists so a runaway table can't turn every query into a full sort
# of a million rows.
_KNN_CEILING = 10_000


# ── the additive vector finder ──────────────────────────────────────────────

class _VectorFinder:
    """sqlite-vec KNN over the live facts. Constructed ONLY when an embedder is
    configured. Imports sqlite-vec lazily (and the model via _load_embedder) so
    the embedding-free floor never pulls them.

    Brute-force KNN is correct here: Loci is a facts table (hundreds, maybe low
    thousands of rows), not a corpus. sqlite-vec's brute force over that is
    sub-millisecond on a Nano. The vector finds the door; store.get_fact hands
    back the deterministic value behind it.

    LANES: a fact holds up to _LANES vectors (see _finder_texts), at
    vec_rowid = fact rowid * _LANES + lane. Every method that touches a fact's
    vectors touches all of its lanes, and the KNN answers once per fact, at
    its nearer lane.

    THE RECONCILE (why this class needs no migration):
        facts_vec is a DERIVED index over the canonical text in `facts`. On
        construction we stamp the active embedder as 'model::dim' in loci_meta,
        and the text recipe as 'vec_recipe', and reconcile the on-disk index
        against both:
          - a stamp changed -> the index is the wrong shape (different model, a
            different dim vec0's FLOAT[n] can't hold, another recipe - the
            one-lane index every store had before 19 Oct 2026 - or it's the
            FIRST vector boot over a store seeded on the floor). DROP it and
            re-encode every live fact from text. Lossless; the facts table is
            never touched.
          - stamps match -> same embedder as last boot. Backfill the live
            facts missing from the index (seeded while the finder was off, or a
            crash between the fact insert and the vec add) AND prune rows whose
            fact was superseded while the finder was off - set_fact/forget can
            only delete a vector when a finder is there to do it, so a floor
            boot leaves dead vectors that would otherwise hold KNN seats
            forever. Usually a no-op.

    SCOPED KNN: sqlite-vec returns the k nearest over the WHOLE index and only
    then can SQL filter by project. Asking for k=n from a store where another
    project dominates the neighbourhood returned n rows from the wrong project,
    filtered them away, and reported nothing - a small project became
    unsearchable. So the KNN asks for every row (Loci is small by design; see
    _KNN_CEILING) and the project filter runs before the LIMIT. Brute force
    over the whole table is what sqlite-vec does anyway, so this costs nothing.
    """

    def __init__(self, conn: sqlite3.Connection, model_name: str, device: str,
                 cache_folder: str | None = None, model: Any = None):
        import sqlite_vec  # raises if the optional dep isn't installed

        self._conn = conn
        # model: one already loaded (LociStore's background warm-up loads it
        # off the store's lock)
        self._model = model if model is not None else _load_embedder(model_name, device, cache_folder=cache_folder)
        self._dim = self._model.get_sentence_embedding_dimension()

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Reconcile the derived index with the embedder and recipe RIGHT NOW.
        current = f"{model_name}::{self._dim}"
        if self._read_stamp() == current and self._read_meta("vec_recipe") == _RECIPE:
            # Same embedder and recipe as the index on disk. Existing vectors
            # are valid; just fill any gaps (and self-heal a missing table).
            self._ensure_table()
            self._backfill_missing()
        else:
            # Different (or first-ever) embedder or recipe for this store. The
            # old index, if any, is the wrong shape - throw it away and
            # rebuild from text.
            self._rebuild()
            self._write_stamp(current)
            self._write_meta("vec_recipe", _RECIPE)

    # -- reconcile helpers ------------------------------------------------
    def _create_table_sql(self, if_not_exists: bool = False) -> str:
        # vec0 table keyed by fact rowid * _LANES + lane, so add/delete stay in
        # lockstep with live rows and a fact's lanes sit side by side.
        return (f"CREATE VIRTUAL TABLE {'IF NOT EXISTS ' if if_not_exists else ''}facts_vec "
                f"USING vec0(vec_rowid INTEGER PRIMARY KEY, embedding FLOAT[{self._dim}])")

    def _ensure_table(self) -> None:
        self._conn.execute(self._create_table_sql(if_not_exists=True))
        self._conn.commit()

    def _rebuild(self) -> int:
        """Throw the old index away and re-encode every live fact from text.
        Lossless (the facts table is never touched) and cheap (Loci is small by
        design). Returns how many facts were indexed."""
        self._conn.execute("DROP TABLE IF EXISTS facts_vec")
        self._conn.execute(self._create_table_sql())
        rows = self._conn.execute(
            "SELECT rowid, project, key, value, why FROM facts WHERE superseded_at IS NULL"
        ).fetchall()
        for r in rows:
            self.add(r["rowid"], _finder_texts(r["project"], r["key"], r["value"], r["why"]))
        self._conn.commit()
        return len(rows)

    def _backfill_missing(self) -> int:
        """Bring the index back into lockstep with the LIVE rows: add the live
        facts that aren't in facts_vec yet, drop the vectors whose fact is no
        longer live. Same embedder, so existing vectors are still valid.
        Returns how many were added."""
        rows = self._conn.execute(
            "SELECT rowid, project, key, value, why FROM facts "
            "WHERE superseded_at IS NULL "
            "  AND rowid NOT IN (SELECT vec_rowid / ? FROM facts_vec)",
            (_LANES,),
        ).fetchall()
        for r in rows:
            self.add(r["rowid"], _finder_texts(r["project"], r["key"], r["value"], r["why"]))
        self._prune_dead()
        # ALWAYS commit. The DELETE opens a transaction in Python's sqlite3
        # even when it removes nothing, and committing only on a change left a
        # clean boot holding it: the first set_fact's BEGIN then failed with
        # 'cannot start a transaction within a transaction' (26 Sept 2026).
        self._conn.commit()
        return len(rows)

    def _prune_dead(self) -> int:
        """Delete vectors for facts that are superseded or gone. Returns the
        count. The index mirrors live rows and nothing else."""
        cur = self._conn.execute(
            "DELETE FROM facts_vec WHERE vec_rowid / ? NOT IN "
            "(SELECT rowid FROM facts WHERE superseded_at IS NULL)", (_LANES,))
        return cur.rowcount

    def _read_stamp(self) -> Optional[str]:
        return self._read_meta("vec_embedder")

    def _write_stamp(self, stamp: str) -> None:
        self._write_meta("vec_embedder", stamp)

    def _read_meta(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM loci_meta WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else None

    def _write_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO loci_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    # -- encode / index maintenance --------------------------------------
    def _encode(self, text: str) -> bytes:
        return self._encode_many([text])[0]

    def _encode_many(self, texts: list[str]) -> list[bytes]:
        import struct
        vecs = self._model.encode(list(texts), normalize_embeddings=True)
        return [struct.pack(f"{len(v)}f", *v) for v in vecs]

    def add(self, rowid: int, texts: list[str]) -> None:
        """Index one fact: a vector per lane (_finder_texts), every lane it
        held before replaced, so a fact that lost its why loses that lane."""
        self.delete(rowid)
        for lane, blob in enumerate(self._encode_many(texts[:_LANES])):
            self._conn.execute(
                "INSERT INTO facts_vec(vec_rowid, embedding) VALUES(?, ?)",
                (rowid * _LANES + lane, blob),
            )

    def delete(self, rowid: int) -> None:
        """Drop every lane of one fact."""
        for lane in range(_LANES):
            self._conn.execute("DELETE FROM facts_vec WHERE vec_rowid=?", (rowid * _LANES + lane,))

    def window_notes(self, project: str, key: str, value: str, why: Optional[str]) -> list[str]:
        """Which part of a fact runs past the embedder's window, said in
        tokens of the embedder's own tokenizer. The value part (its address +
        value) past the window means words no vector holds; the why has its
        own lane, so a long why only matters when that lane itself runs past.
        Silent for an embedder that names no window or tokenizer (a stub), and
        never a reason to fail a write."""
        limit = getattr(self._model, "max_seq_length", None)
        tok = getattr(self._model, "tokenizer", None)
        if not limit or tok is None:
            return []
        head = "  ".join([key] if project == FUNDAMENTALS else [project, key])
        notes = []
        for part, text in (("value", f"{head}  {value}"), ("why", f"{head}  {why}" if why else None)):
            if not text:
                continue
            try:
                n = len(tok(text, add_special_tokens=True, truncation=False, verbose=False)["input_ids"])
            except Exception:  # noqa: BLE001 - a note is never worth a failed write
                return notes
            if n > limit:
                notes.append(
                    f"the finder reads the first {limit} tokens of the {part} part (its address + {part}); this one runs "
                    f"to {n}, so its last {n - limit} are in no vector (lexical search still sees every word). "
                    f"A fact of one claim fits; split it if it holds several.")
        return notes

    def search(self, query: str, k: int,
               scopes: list[str] | None = None) -> list[tuple[str, float]]:
        """KNN -> [(fact_id, distance)], nearest first, at most k, all of
        them live and (when scopes is given) inside those projects. A fact
        answers once, at its nearer lane.

        The vec0 MATCH returns its k nearest BEFORE SQL sees the join, so the
        project filter has to run over the whole neighbourhood: we ask vec0 for
        every row it holds (capped at _KNN_CEILING) and LIMIT after filtering.
        The LIMIT is k * _LANES vectors, which always holds k distinct facts
        when there are k to hold, and the dedupe keeps each fact's first.
        scopes=None means no project filter; scopes=[] means nothing is in scope."""
        if scopes is not None and not scopes:
            return []
        total = self._conn.execute("SELECT COUNT(*) AS c FROM facts_vec").fetchone()["c"]
        if total == 0:
            return []
        clauses, params = ["f.superseded_at IS NULL"], []
        if scopes is not None:
            clauses.append(f"f.project IN ({','.join('?' for _ in scopes)})")
            params.extend(scopes)
        rows = self._conn.execute(
            "SELECT f.id AS id, v.distance AS distance "
            "FROM facts_vec v JOIN facts f ON f.rowid = v.vec_rowid / ? "
            "WHERE v.embedding MATCH ? AND k = ? "
            "  AND " + " AND ".join(clauses) + " "
            "ORDER BY v.distance LIMIT ?",
            (_LANES, self._encode(query), min(total, _KNN_CEILING), *params, k * _LANES),
        ).fetchall()
        out: dict[str, float] = {}
        for r in rows:
            if r["id"] not in out:
                out[r["id"]] = float(r["distance"])
        return list(out.items())[:k]


# ── the hybrid finder (vector + FTS5, fused via RRF) ────────────────────

class _HybridFinder(_VectorFinder):
    """Extends _VectorFinder with hybrid FTS5 + vector search via
    reciprocal rank fusion (RRF).

    Runs BOTH lexical (FTS5) and semantic (vector) search in parallel, then
    fuses results with RRF. This gives better precision than either ranker
    alone - FTS5 catches exact/keyword matches, vector catches semantic
    matches, and RRF boosts documents that rank highly in BOTH lists.

    The RRF constant k=60 is the standard value from the literature
    (Cormack et al.).  Higher k = more weight on lower-ranked positions;
    60 is a good default that balances precision and recall.

    SCOPE-AWARE QUERY EMBEDDING:
        When a single project scope is provided (not FUNDAMENTALS), the
        vector query is augmented by prepending the project name before the
        raw query string.  This helps the embedding model disambiguate
        queries that would otherwise be close in the semantic space of
        multiple projects - e.g. "rate limiting" under "seren-memory"
        becomes "seren-memory: rate limiting", nudging the query vector
        toward the memory-domain cluster.
    """

    RRF_K: int = 60

    def search(self, query: str, k: int,
               scopes: list[str] | None = None,
               include_superseded: bool = False,
               ) -> list[tuple[str, float, Optional[float], Optional[float]]]:
        """Hybrid search: FTS5 + vector, RRF-fused for ORDERING, returning the
        absolute per-lane signals so the caller can score by relevance.
        include_superseded reaches the lexical lane only; the vector index is
        live by construction.

        Returns [(fact_id, rrf_score, vec_distance, bm25_rel)]:
            rrf_score    - reciprocal-rank-fused value. Used ONLY to order and
                           select candidates. It is NOT a relevance magnitude and
                           is NOT cross-store comparable (a doc at rank 0 in both
                           lanes always fuses to 2/RRF_K no matter how weak the
                           underlying match), so it must never be handed to SCC
                           as a score.
            vec_distance - the doc's vector distance, or None if it wasn't a
                           vector hit. Smaller = closer.
            bm25_rel     - the doc's bm25 relevance, or None if not an FTS hit.
                           Larger = stronger.

        Parameters:
            query  - raw user query string (used for both FTS5 and vector)
            k      - number of results to return
            scopes - list of project scopes; passed to FTS5 for filtering
                     and used for scope-aware vector query augmentation.
        """
        # 1. FTS5 search (scoped) - the store's own lexical query
        fts_results = _fts_search(self._conn, query, scopes or [], k * 2,
                                  include_superseded)

        # 2. Vector search (scoped via query augmentation) - scope-aware
        #    embedding: prepend primary project to the query so the
        #    embedding model has domain context.
        vec_results = self._vector_search_augmented(query, k * 2, scopes)

        # Preserve the absolute per-lane signals; the caller scores by relevance,
        # not by rank (see LociStore.search / _hybrid_score).
        bm25_by_id = {fid: rel for fid, rel in fts_results}
        dist_by_id = {fid: dist for fid, dist in vec_results}

        # 3. RRF fusion: sum 1/(RRF_K + rank) for each doc across both rankers.
        #    A doc that ranks highly in both gets a higher ORDERING score (used
        #    only to sort/select, NOT as the emitted relevance).
        rrf_scores: dict[str, float] = {}
        for rank, (fid, _) in enumerate(fts_results):
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + 1.0 / (self.RRF_K + rank)
        for rank, (fid, _) in enumerate(vec_results):
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + 1.0 / (self.RRF_K + rank)

        # 4. Sort by fused score descending, trim to k, attach absolute signals.
        ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])
        return [
            (fid, fused, dist_by_id.get(fid), bm25_by_id.get(fid))
            for fid, fused in ranked[:k]
        ]

    # ── internal helpers ──────────────────────────────────────────────

    def _vector_search_augmented(self, query: str, k: int,
                                 scopes: list[str] | None = None
                                 ) -> list[tuple[str, float]]:
        """Vector search with optional scope-aware query augmentation.

        When the query targets a SINGLE domain project, prepend it to the
        query before embedding so the vector search benefits from domain
        context.  _resolve_scopes appends FUNDAMENTALS to a project scope for
        FILTERING, so the raw scope list is usually length 2 ([project,
        FUNDAMENTALS]) even for a single-project query - keying off
        len(scopes)==1 made augmentation fire ~never in the default path (and
        never at all through SCC, which defaults include_fundamentals on). Drop
        FUNDAMENTALS first, THEN check for a lone domain scope.
        """
        domain = [s for s in (scopes or []) if s != FUNDAMENTALS]
        if len(domain) == 1:
            augmented = f"{domain[0]}: {query}"
        else:
            augmented = query
        # parent search encodes the (maybe augmented) query and runs a KNN
        # filtered to the scopes BEFORE the limit (see _VectorFinder.search)
        return super().search(augmented, k, scopes)
