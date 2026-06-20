"""
Vector finder SQL-contract test.

Gated on sqlite-vec being installed (the [vector] extra). This validates the
vec0 KNN + delete SQL that _VectorFinder depends on, WITHOUT pulling torch -
we hand-build tiny float vectors instead of running a real embedder.

The full _VectorFinder + sentence-transformers path (encode -> store -> KNN) is
validated on hardware with a real model; torch is too heavy/flaky for CI.

The encoder is now injectable via the seren_loci.store._load_embedder seam, so
the finder's reconcile / rebuild / backfill logic IS exercised end-to-end here
in CI with a stub embedder over real sqlite-vec - see test_embedder_reconcile.py.
This file stays the narrow SQL-contract check (vec0 KNN + delete) it always was.
"""
from __future__ import annotations

import sqlite3
import struct

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")


def _blob(v):
    return struct.pack(f"{len(v)}f", *v)


def _vec_conn():
    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.execute(
        "CREATE VIRTUAL TABLE facts_vec USING "
        "vec0(fact_rowid INTEGER PRIMARY KEY, embedding FLOAT[4])"
    )
    return conn


def test_knn_orders_by_distance():
    conn = _vec_conn()
    conn.execute("INSERT INTO facts_vec(fact_rowid, embedding) VALUES(?,?)", (1, _blob([1, 0, 0, 0])))
    conn.execute("INSERT INTO facts_vec(fact_rowid, embedding) VALUES(?,?)", (2, _blob([0, 1, 0, 0])))
    conn.execute("INSERT INTO facts_vec(fact_rowid, embedding) VALUES(?,?)", (3, _blob([0.9, 0.1, 0, 0])))
    rows = conn.execute(
        "SELECT fact_rowid FROM facts_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (_blob([1, 0, 0, 0]), 2),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 3]
    conn.close()


def test_delete_removes_from_index():
    conn = _vec_conn()
    conn.execute("INSERT INTO facts_vec(fact_rowid, embedding) VALUES(?,?)", (1, _blob([1, 0, 0, 0])))
    conn.execute("INSERT INTO facts_vec(fact_rowid, embedding) VALUES(?,?)", (2, _blob([0, 1, 0, 0])))
    conn.execute("DELETE FROM facts_vec WHERE fact_rowid=?", (1,))
    rows = conn.execute(
        "SELECT fact_rowid FROM facts_vec WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (_blob([1, 0, 0, 0]), 5),
    ).fetchall()
    assert 1 not in [r[0] for r in rows]
    conn.close()
