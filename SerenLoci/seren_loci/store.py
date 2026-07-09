"""
seren_loci.store
════════════════════════════════════════════════════════════════════════

The left brain's data layer. One sqlite file holds every fact; the access
path has three rungs, cheapest first:

    1. EXACT      get_fact(project, key) -> the live value, deterministically.
                  No embedding, no ranking. You know the address, you get the
                  thing. This is the spine and it runs free.

    2. LEXICAL    FTS5 full-text over (key, value, why) of the LIVE rows. The
                  "I sort of remember the words" path. Still no vectors, still
                  floor-cheap.

    3. VECTOR     (additive) a sqlite-vec index over the live facts, built only
                  when storage.embedding_model is set. The "this smells like
                  that CUDA thing" associative jump - find the door when you
                  don't know the key. The store works fully without it.

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

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .config import LociConfig
from .models.schemas import Fact, FactWrite, FUNDAMENTALS, SearchHit


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
    extra         TEXT    NOT NULL DEFAULT '{}'  -- JSON
);

-- THE invariant: one live value per (project, key). Partial index = sqlite
-- enforces it only over live rows, so history can hold many rows per key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_facts_live
    ON facts(project, key) WHERE superseded_at IS NULL;

-- exact-lookup index (covers the live-key get and project scans)
CREATE INDEX IF NOT EXISTS idx_facts_lookup ON facts(project, key, superseded_at);

-- FTS5 lexical index. Regular (content-bearing) so we can read columns back.
-- Kept in lockstep with live rows in set_fact/forget via the bridging rowid.
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    key, value, why,
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
"""


class LociStore:
    """Owns the sqlite connection and the three access rungs."""

    def __init__(self, config: LociConfig):
        self._config = config
        self._db_path = config.resolved_db_path()
        # check_same_thread=False: FastAPI may touch the store from a worker
        # thread. We serialize writes ourselves via the connection's implicit
        # transaction + a single-writer discipline (sqlite handles the locking).
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")   # readers don't block the writer
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

        # Additive vector finder. None unless an embedder is configured AND the
        # optional deps import cleanly. Everything below degrades to FTS when
        # this is None - the floor never depends on it. Building the finder also
        # reconciles the vector index with the configured embedder (see below).
        self._finder = self._build_finder()

    # ──────────────────────────────────────────────────────────────────
    #  finder construction (additive - the ceiling, not the floor)
    # ──────────────────────────────────────────────────────────────────
    def _build_finder(self) -> Optional["_HybridFinder"]:
        """Build the finder: hybrid (vector + FTS5) if an embedder is configured,
        else None (pure FTS5 lexical only).

        The hybrid finder runs BOTH semantic and lexical search in parallel and
        fuses results via reciprocal rank fusion (RRF) — see _HybridFinder.
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
    def set_fact(self, w: FactWrite) -> Fact:
        """Set (or replace) the live value for (project, key).

        If a live value already exists for that key it is superseded - stamped
        with superseded_at + a forward link to the new row, kept as history.
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
                # Stamp it superseded and point it forward. Drop its FTS row so
                # lexical search only ever sees live facts.
                cur.execute(
                    "UPDATE facts SET superseded_at=?, superseded_by=? WHERE rowid=?",
                    (now, fact.id, old["rowid"]),
                )
                self._fts_delete(cur, old["rowid"])
                if self._finder is not None:
                    self._finder.delete(old["rowid"])

            # Insert the new live row.
            cur.execute(
                "INSERT INTO facts(id, project, key, value, why, source, "
                "created_at, superseded_at, superseded_by, extra) "
                "VALUES(?,?,?,?,?,?,?,NULL,NULL,?)",
                (fact.id, fact.project, fact.key, fact.value, fact.why,
                 fact.source.value, now, json.dumps(fact.extra)),
            )
            new_rowid = cur.lastrowid
            self._fts_insert(cur, new_rowid, fact.key, fact.value, fact.why)
            if self._finder is not None:
                self._finder.add(new_rowid, self._embed_text(fact))
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return fact

    def forget(self, project: str, key: str) -> bool:
        """Retire the live value for a key (supersede with no replacement).

        'Forget is a flag, not a scalpel' - this is the soft retire: the row
        stays as history, it just stops being the live answer. Returns True if
        there was a live value to retire.
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
                "UPDATE facts SET superseded_at=? WHERE rowid=?",
                (time.time(), old["rowid"]),
            )
            self._fts_delete(cur, old["rowid"])
            if self._finder is not None:
                self._finder.delete(old["rowid"])
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

    # ──────────────────────────────────────────────────────────────────
    #  READ - rung 1: exact deterministic lookup (the spine)
    # ──────────────────────────────────────────────────────────────────
    def get_fact(self, project: str, key: str) -> Optional[Fact]:
        """The live value for (project, key), or None. Deterministic - no
        ranking, no embedding. This is what makes Loci a logic store: you
        either get THE value or nothing, never a stale-similar blend."""
        row = self._conn.execute(
            "SELECT * FROM facts "
            "WHERE project=? AND key=? AND superseded_at IS NULL",
            (project, key),
        ).fetchone()
        return _row_to_fact(row) if row else None

    def get_history(self, project: str, key: str) -> list[Fact]:
        """Every value this key has ever held, newest first. The audit trail
        the strict-supersede rule preserves."""
        rows = self._conn.execute(
            "SELECT * FROM facts WHERE project=? AND key=? "
            "ORDER BY created_at DESC",
            (project, key),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

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
        return [_row_to_fact(r) for r in rows]

    # ──────────────────────────────────────────────────────────────────
    #  READ - rungs 2 & 3: discovery (search)
    # ──────────────────────────────────────────────────────────────────
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
        """
        scopes = self._resolve_scopes(project, include_fundamentals)
        hits: dict[str, SearchHit] = {}  # id -> hit, exact-wins dedupe

        # 1. exact key hit (deterministic, score 1.0)
        for scope in scopes:
            f = self.get_fact(scope, query)
            if f is not None:
                hits[f.id] = _hit(f, score=1.0, kind="exact")

        # 2. finder — hybrid (vector + FTS5 fused via RRF) when embedder present,
        #    pure FTS5 lexical otherwise.
        if self._finder is not None:
            # _HybridFinder returns (fact_id, rrf_score) where rrf_score is the
            # reciprocal-rank-fused relevance (higher = better). We use it
            # directly as the match score — no distance-to-score conversion
            # needed because RRF already gives us a calibrated 0..1-ish value.
            for fid, fused in self._finder.search(query, n_results * 2, scopes):
                f = self._fact_by_id(fid)
                if f is None or not _in_scope(f, scopes):
                    continue
                if f.id in hits:
                    continue  # exact already claimed it
                # RRF score is a sum of 1/(k + rank). Normalize into [0, 1] by
                # dividing by max possible score (2/k, since each doc can appear
                # in at most two rankers: FTS5 + vector).
                max_possible = 2.0 / _HybridFinder.RRF_K
                score = min(fused / max_possible, 1.0)
                hits[f.id] = _hit(f, score=round(score, 6), kind="hybrid")
        else:
            for fid, rel in self._fts_search(query, scopes, n_results * 2):
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
                        include_fundamentals: bool) -> list[str]:
        if project is None:
            # search everything: fundamentals + every project that has facts
            rows = self._conn.execute(
                "SELECT DISTINCT project FROM facts WHERE superseded_at IS NULL"
            ).fetchall()
            return [r["project"] for r in rows]
        scopes = [project]
        if include_fundamentals and project != FUNDAMENTALS:
            scopes.append(FUNDAMENTALS)
        return scopes

    def _fact_by_id(self, fid: str) -> Optional[Fact]:
        row = self._conn.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
        return _row_to_fact(row) if row else None

    def _embed_text(self, fact: Fact) -> str:
        """What we hand the embedder for a fact. Delegates to the module-level
        _finder_text so the set_fact path and the index rebuild share ONE recipe
        and can't drift (a DTO-asymmetry-style silent killer if they ever did)."""
        return _finder_text(fact.key, fact.value, fact.why)

    def _fts_insert(self, cur, rowid: int, key: str, value: str,
                    why: Optional[str]) -> None:
        cur.execute(
            "INSERT INTO facts_fts(rowid, key, value, why) VALUES(?,?,?,?)",
            (rowid, key, value, why or ""),
        )

    def _fts_delete(self, cur, rowid: int) -> None:
        # external-content FTS5 delete idiom: special 'delete' command row
        cur.execute(
            "INSERT INTO facts_fts(facts_fts, rowid, key, value, why) "
            "VALUES('delete', ?, "
            "(SELECT key FROM facts WHERE rowid=?), "
            "(SELECT value FROM facts WHERE rowid=?), "
            "(SELECT COALESCE(why,'') FROM facts WHERE rowid=?))",
            (rowid, rowid, rowid, rowid),
        )

    def _fts_search(self, query: str, scopes: list[str],
                    limit: int) -> list[tuple[str, float]]:
        """FTS5 MATCH over live facts in scope. Returns (fact_id, relevance)
        with relevance >= 0 (larger = better). bm25() returns smaller-is-better
        reals (negative = strong match), so we negate."""
        if not scopes:
            return []
        placeholders = ",".join("?" for _ in scopes)
        try:
            rows = self._conn.execute(
                f"SELECT f.id AS id, -bm25(facts_fts) AS rel "
                f"FROM facts_fts "
                f"JOIN facts f ON f.rowid = facts_fts.rowid "
                f"WHERE facts_fts MATCH ? "
                f"  AND f.superseded_at IS NULL "
                f"  AND f.project IN ({placeholders}) "
                f"ORDER BY rel DESC LIMIT ?",
                (_fts_query(query), *scopes, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # malformed MATCH (e.g. user typed bare punctuation) -> no lexical
            # hits rather than a 500. The exact rung already ran.
            return []
        return [(r["id"], max(r["rel"], 0.0)) for r in rows]

    def counts(self) -> dict[str, int]:
        live = self._conn.execute(
            "SELECT COUNT(*) c FROM facts WHERE superseded_at IS NULL"
        ).fetchone()["c"]
        total = self._conn.execute("SELECT COUNT(*) c FROM facts").fetchone()["c"]
        projects = self._conn.execute(
            "SELECT COUNT(DISTINCT project) c FROM facts WHERE superseded_at IS NULL"
        ).fetchone()["c"]
        return {"live": live, "history": total - live, "projects": projects}

    def close(self) -> None:
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
        superseded_by=row["superseded_by"],
        extra=json.loads(row["extra"]) if row["extra"] else {},
    )


def _hit(f: Fact, score: float, kind: str,
         raw_distance: Optional[float] = None) -> SearchHit:
    return SearchHit(id=f.id, project=f.project, key=f.key, value=f.value,
                     why=f.why, score=score, match_kind=kind,
                     source=f.source.value, raw_distance=raw_distance)


def _in_scope(f: Fact, scopes: list[str]) -> bool:
    return f.is_live and f.project in scopes


def _finder_text(key: str, value: str, why: Optional[str]) -> str:
    """The text the embedder sees for a fact: key + value + why, so the
    associative jump can latch onto any of them ('that CUDA thing' should find a
    fact whose WHY mentions CUDA even if the key doesn't). The ONE recipe -
    set_fact and the index rebuild both go through here so they can never drift."""
    parts = [key, value]
    if why:
        parts.append(why)
    return "  ".join(parts)


def _fts_query(raw: str) -> str:
    """Make a user string safe-ish for FTS5 MATCH. We wrap each whitespace
    token in double quotes (FTS5 phrase) so punctuation in 'posh.brace_style'
    or 'cuda-compat' doesn't get read as MATCH operators. Empty -> a token that
    matches nothing rather than erroring.

    For short queries (<= 3 tokens) we use AND (default FTS5 behavior — all
    quoted terms must appear). For longer queries we switch to OR so that a
    single matching token suffices; this prevents long natural-language queries
    from returning zero lexical hits when only a few terms happen to appear in
    any fact row."""
    tokens = [t.replace('"', '') for t in raw.split() if t.strip()]
    if not tokens:
        return '"__seren_loci_no_match__"'
    quoted = [f'"{t}"' for t in tokens]
    if len(tokens) <= 3:
        return " ".join(quoted)
    return " OR ".join(quoted)


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


# ── the additive vector finder ──────────────────────────────────────────────

class _VectorFinder:
    """sqlite-vec KNN over the live facts. Constructed ONLY when an embedder is
    configured. Imports sqlite-vec lazily (and the model via _load_embedder) so
    the embedding-free floor never pulls them.

    Brute-force KNN is correct here: Loci is a facts table (hundreds, maybe low
    thousands of rows), not a corpus. sqlite-vec's brute force over that is
    sub-millisecond on a Nano. The vector finds the door; store.get_fact hands
    back the deterministic value behind it.

    THE RECONCILE (why this class needs no migration):
        facts_vec is a DERIVED index over the canonical text in `facts`. On
        construction we stamp the active embedder as 'model::dim' in loci_meta
        and reconcile the on-disk index against it:
          - stamp changed -> the index is the wrong shape (different model, a
            different dim vec0's FLOAT[n] can't hold, or it's the FIRST vector
            boot over a store seeded on the floor). DROP it and re-encode every
            live fact from text. Lossless; the facts table is never touched.
          - stamp matches -> same embedder as last boot. Backfill only the live
            facts missing from the index (seeded while the finder was off, or a
            crash between the fact insert and the vec add). Usually a no-op.
    """

    def __init__(self, conn: sqlite3.Connection, model_name: str, device: str,
                 cache_folder: str | None = None):
        import sqlite_vec  # raises if the optional dep isn't installed

        self._conn = conn
        self._model = _load_embedder(model_name, device, cache_folder=cache_folder)
        self._dim = self._model.get_sentence_embedding_dimension()

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Reconcile the derived index with the embedder configured RIGHT NOW.
        current = f"{model_name}::{self._dim}"
        if self._read_stamp() == current:
            # Same embedder as the index on disk. Existing vectors are valid;
            # just fill any gaps (and self-heal a missing table).
            self._ensure_table()
            self._backfill_missing()
        else:
            # Different (or first-ever) embedder for this store. The old index,
            # if any, is the wrong shape - throw it away and rebuild from text.
            self._rebuild()
            self._write_stamp(current)

    # -- reconcile helpers ------------------------------------------------
    def _ensure_table(self) -> None:
        # vec0 table keyed by the same rowid the facts table uses, so add/delete
        # stay in lockstep with live rows.
        self._conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS facts_vec "
            f"USING vec0(fact_rowid INTEGER PRIMARY KEY, embedding FLOAT[{self._dim}])"
        )
        self._conn.commit()

    def _rebuild(self) -> int:
        """Throw the old index away and re-encode every live fact from text.
        Lossless (the facts table is never touched) and cheap (Loci is small by
        design). Returns how many facts were indexed."""
        self._conn.execute("DROP TABLE IF EXISTS facts_vec")
        self._conn.execute(
            f"CREATE VIRTUAL TABLE facts_vec "
            f"USING vec0(fact_rowid INTEGER PRIMARY KEY, embedding FLOAT[{self._dim}])"
        )
        rows = self._conn.execute(
            "SELECT rowid, key, value, why FROM facts WHERE superseded_at IS NULL"
        ).fetchall()
        for r in rows:
            self.add(r["rowid"], _finder_text(r["key"], r["value"], r["why"]))
        self._conn.commit()
        return len(rows)

    def _backfill_missing(self) -> int:
        """Index any live facts that aren't in facts_vec yet. Same embedder, so
        existing vectors are still valid - we only add the gaps."""
        rows = self._conn.execute(
            "SELECT rowid, key, value, why FROM facts "
            "WHERE superseded_at IS NULL "
            "  AND rowid NOT IN (SELECT fact_rowid FROM facts_vec)"
        ).fetchall()
        for r in rows:
            self.add(r["rowid"], _finder_text(r["key"], r["value"], r["why"]))
        if rows:
            self._conn.commit()
        return len(rows)

    def _read_stamp(self) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value FROM loci_meta WHERE key='vec_embedder'"
        ).fetchone()
        return row["value"] if row else None

    def _write_stamp(self, stamp: str) -> None:
        self._conn.execute(
            "INSERT INTO loci_meta(key, value) VALUES('vec_embedder', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (stamp,),
        )
        self._conn.commit()

    # -- encode / index maintenance --------------------------------------
    def _encode(self, text: str) -> bytes:
        import struct
        vec = self._model.encode([text], normalize_embeddings=True)[0]
        return struct.pack(f"{len(vec)}f", *vec)

    def add(self, rowid: int, text: str) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO facts_vec(fact_rowid, embedding) VALUES(?, ?)",
            (rowid, self._encode(text)),
        )

    def delete(self, rowid: int) -> None:
        self._conn.execute("DELETE FROM facts_vec WHERE fact_rowid=?", (rowid,))

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """KNN -> [(fact_id, distance)]. Joins vec rowid back to the live facts
        row to return the public id."""
        rows = self._conn.execute(
            "SELECT f.id AS id, v.distance AS distance "
            "FROM facts_vec v JOIN facts f ON f.rowid = v.fact_rowid "
            "WHERE v.embedding MATCH ? AND k = ? "
            "  AND f.superseded_at IS NULL "
            "ORDER BY v.distance",
            (self._encode(query), k),
        ).fetchall()
        return [(r["id"], float(r["distance"])) for r in rows]


# ── the hybrid finder (vector + FTS5, fused via RRF) ────────────────────

class _HybridFinder(_VectorFinder):
    """Extends _VectorFinder with hybrid FTS5 + vector search via
    reciprocal rank fusion (RRF).

    Runs BOTH lexical (FTS5) and semantic (vector) search in parallel, then
    fuses results with RRF. This gives better precision than either ranker
    alone — FTS5 catches exact/keyword matches, vector catches semantic
    matches, and RRF boosts documents that rank highly in BOTH lists.

    The RRF constant k=60 is the standard value from the literature
    (Cormack et al.).  Higher k = more weight on lower-ranked positions;
    60 is a good default that balances precision and recall.

    SCOPE-AWARE QUERY EMBEDDING:
        When a single project scope is provided (not FUNDAMENTALS), the
        vector query is augmented by prepending the project name before the
        raw query string.  This helps the embedding model disambiguate
        queries that would otherwise be close in the semantic space of
        multiple projects — e.g. "rate limiting" under "seren-memory"
        becomes "seren-memory: rate limiting", nudging the query vector
        toward the memory-domain cluster.
    """

    RRF_K: int = 60

    def search(self, query: str, k: int,
               scopes: list[str] | None = None) -> list[tuple[str, float]]:
        """Hybrid search: FTS5 + vector → RRF fusion → top-k.

        Returns [(fact_id, rrf_score)] where rrf_score is the RRF
        combination score (higher = better).  The caller (LociStore.search)
        normalises this into 0..1 for the score contract.

        Parameters:
            query  — raw user query string (used for both FTS5 and vector)
            k      — number of results to return
            scopes — list of project scopes; passed to FTS5 for filtering
                     and used for scope-aware vector query augmentation.
        """
        # 1. FTS5 search (scoped) — use the module-level _fts_query helper
        fts_results = self._fts_search(query, scopes or [], k * 2)

        # 2. Vector search (scoped via query augmentation) — scope-aware
        #    embedding: prepend primary project to the query so the
        #    embedding model has domain context.
        vec_results = self._vector_search_augmented(query, k * 2, scopes)

        # 3. RRF fusion: sum 1/(RRF_K + rank) for each doc across both
        #    rankers.  A doc that ranks highly in both gets a higher score.
        rrf_scores: dict[str, float] = {}
        for rank, (fid, _) in enumerate(fts_results):
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + 1.0 / (self.RRF_K + rank)
        for rank, (fid, _) in enumerate(vec_results):
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + 1.0 / (self.RRF_K + rank)

        # 4. Sort by fused score descending, trim to k.
        ranked = sorted(rrf_scores.items(), key=lambda x: -x[1])
        return ranked[:k]

    # ── internal helpers ──────────────────────────────────────────────

    def _fts_search(self, query: str, scopes: list[str],
                    limit: int) -> list[tuple[str, float]]:
        """FTS5 MATCH over live facts in scope. Returns [(fact_id, bm25_rel)]
        with rel >= 0 (larger = better).  bm25() returns smaller-is-better
        reals (negative = strong match), so we negate.

        Identical to LociStore._fts_search — duplicated here so the hybrid
        finder is self-contained and does not need a store reference.
        """
        if not scopes:
            return []
        placeholders = ",".join("?" for _ in scopes)
        try:
            rows = self._conn.execute(
                f"SELECT f.id AS id, -bm25(facts_fts) AS rel "
                f"FROM facts_fts "
                f"JOIN facts f ON f.rowid = facts_fts.rowid "
                f"WHERE facts_fts MATCH ? "
                f"  AND f.superseded_at IS NULL "
                f"  AND f.project IN ({placeholders}) "
                f"ORDER BY rel DESC LIMIT ?",
                (_fts_query(query), *scopes, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # malformed MATCH (e.g. user typed bare punctuation) -> no
            # lexical hits rather than a 500.
            return []
        return [(r["id"], max(r["rel"], 0.0)) for r in rows]

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
        # parent search encodes the (maybe augmented) query and runs KNN
        return super().search(augmented, k)
