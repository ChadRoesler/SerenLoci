"""
One sqlite connection, many threads: the store serialises them itself.

Seen live on 25 Sept 2026: two MCP set_fact calls at once, and one died with
"cannot start a transaction within a transaction" - the shared connection had
no lock, only a comment promising one. These drive the store from a pool of
threads the way FastAPI's workers and the MCP runner do.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from seren_loci.config import LociConfig, StorageConfig
from seren_loci.models.schemas import FactWrite
from seren_loci.store import LociStore


def _store(tmp_path) -> LociStore:
    return LociStore(LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci.db"))))


def test_concurrent_writes_all_land(tmp_path):
    s = _store(tmp_path)
    try:
        def put(i: int) -> str:
            return s.set_fact(FactWrite(project="p", key=f"k{i}", value=f"v{i}", why="w")).id
        with ThreadPoolExecutor(max_workers=16) as pool:
            ids = list(pool.map(put, range(200)))
        assert len(set(ids)) == 200
        assert len(s.list_facts(project="p")) == 200
    finally:
        s.close()


def test_concurrent_supersedes_of_one_key_leave_exactly_one_live_value(tmp_path):
    s = _store(tmp_path)
    try:
        def put(i: int) -> None:
            s.set_fact(FactWrite(project="p", key="same", value=f"v{i}", why="w"))
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(put, range(100)))
        live = [f for f in s.list_facts(project="p") if f.key == "same"]
        assert len(live) == 1
        assert len(s.get_history("p", "same")) == 100
    finally:
        s.close()


def test_reads_during_writes_never_fail(tmp_path):
    s = _store(tmp_path)
    try:
        s.set_fact(FactWrite(project="p", key="seed", value="v", why="w"))

        def work(i: int) -> None:
            if i % 2:
                s.set_fact(FactWrite(project="p", key=f"k{i}", value="v", why="w"))
            else:
                assert s.get_fact("p", "seed") is not None
                s.search("seed", project="p", n_results=5)
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(work, range(200)))
    finally:
        s.close()
