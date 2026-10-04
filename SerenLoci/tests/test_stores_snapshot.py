"""
Loci says what it keeps and snapshots it (seren_sinew.stores).

Pinned here:
- GET /stores declares the database, and the embedding model beside it as
  declared-but-not-copied (it is a download, and it is most of the folder)
- a snapshot is the database copied whole while it is open, plus every fact
  and its history as readable lines
- the routes sit behind the bearer like everything else
- backup.enabled: false turns it off, and says so
- no route restores a snapshot or deletes one
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from seren_loci.config import BackupConfig, LociConfig, ServerConfig, StorageConfig


@pytest.fixture
def app_client(tmp_path):
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "store" / "loci.db"), embedding_model=None),
                     backup=BackupConfig(every_hours=0))
    with TestClient(create_app(cfg)) as c:
        yield c


def test_it_says_what_it_keeps(app_client, tmp_path):
    d = app_client.get("/stores").json()
    assert d["ok"] and d["service"] == "seren-loci"
    by = {s["name"]: s for s in d["stores"]}
    assert (by["facts"]["kind"], by["facts"]["backed_up"], by["facts"]["exists"]) == ("sqlite", True, True)
    assert by["models"]["backed_up"] is False, "the model's weights are a download"
    assert Path(d["snapshots"]["dir"]) == (tmp_path / "store" / "backups" / "seren-loci").resolve()


def test_a_snapshot_is_the_database_and_every_fact_with_its_history(app_client):
    app_client.post("/fact", json={"key": "indent", "value": "tabs", "why": "makefiles need them"})
    app_client.post("/fact", json={"key": "indent", "value": "spaces", "why": "changed our mind"})
    app_client.post("/fact", json={"key": "port", "value": "7200", "project": "hippocampus"})
    r = app_client.post("/stores/snapshot", json={"reason": "by hand"})
    assert r.status_code == 200, r.text
    snap = r.json()["snapshot"]
    root = Path(snap["path"])
    man = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert man["stores"] == ["facts"] and {s["name"] for s in man["skipped"]} <= {"models"}
    assert man["exports"] == {"facts.jsonl": 3} and man["version"] and man["counts"]

    copy = root / "raw" / "facts" / "loci.db"
    con = sqlite3.connect(copy)
    assert con.execute("SELECT count(*) FROM facts").fetchone()[0] == 3
    con.close()

    rows = [json.loads(x) for x in (root / "export" / "facts.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted((x["key"], x["value"]) for x in rows) == [("indent", "spaces"), ("indent", "tabs"), ("port", "7200")]
    old = next(x for x in rows if x["value"] == "tabs")
    assert old["superseded_at"] and old["why"] == "makefiles need them", "history rides along, with the why"
    assert app_client.get("/stores/snapshots").json()["count"] == 1


def test_the_routes_are_behind_the_bearer(tmp_path):
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    cfg = LociConfig(server=ServerConfig(bearer_token="sekret"),
                     storage=StorageConfig(db_path=str(tmp_path / "loci.db"), embedding_model=None),
                     backup=BackupConfig(every_hours=0))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/stores").status_code == 401 and c.post("/stores/snapshot").status_code == 401
        ok = c.post("/stores/snapshot", headers={"Authorization": "Bearer sekret"})
        assert ok.status_code == 200 and ok.json()["ok"]


def test_switched_off_the_routes_say_so(tmp_path):
    from fastapi.testclient import TestClient
    from seren_loci.app import create_app
    cfg = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci.db"), embedding_model=None),
                     backup=BackupConfig(enabled=False))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/stores").status_code == 404 and "backup.enabled" in c.get("/stores").json()["error"]


def test_no_route_restores_or_deletes_a_snapshot(app_client):
    sid = app_client.post("/stores/snapshot").json()["snapshot"]["id"]
    for path in ("/stores/restore", f"/stores/snapshots/{sid}"):
        assert app_client.post(path).status_code in (404, 405)
        assert app_client.delete(path).status_code in (404, 405)
    assert app_client.get("/stores/snapshots").json()["count"] == 1


def test_a_rehearsal_opens_a_copy_and_counts_it(app_client):
    """A restore's dry run (seren_sinew.stores): the copy is counted against
    the manifest and the export, and the live database is not touched."""
    app_client.post("/fact", json={"key": "indent", "value": "tabs", "why": "makefiles need them"})
    app_client.post("/fact", json={"key": "indent", "value": "spaces", "why": "changed our mind"})
    sid = app_client.post("/stores/snapshot").json()["snapshot"]["id"]
    app_client.post("/fact", json={"key": "port", "value": "7200"})          # after the snapshot
    r = app_client.post(f"/stores/snapshots/{sid}/rehearse")
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["ok"] and rep["dry_run"] and rep["check"]["counts"]["live"] == 1 and rep["check"]["counts"]["history"] == 1, rep
    assert rep["sqlite"] == [{"file": "facts/loci.db", "integrity": "ok"}] and rep["live_store_touched"] is False
    assert app_client.app.state.store.counts()["live"] == 2, "the live database kept what came after"
