"""
Figures: the exhibits a fact cites (the `figures` table, the three tools,
set_fact's `keep`, the /fact/figures routes). 15 Oct 2026.

Pinned here:
- a figure is never the fact: it hangs on a fact's row, the row says what it
  shows, and look_at_fact shows it as image content
- a superseded value keeps its own figures in history; the new value cites
  what it cites, and `figure:<digest>` cites one again without a second copy
- the store reaches only the shelves figures.sources names; what is kept is
  decided by the bytes; the fact is set whatever happens to the pictures
- forget keeps the row's figures (forget is a flag); dropping one from the
  live value sweeps the bytes once no row cites them
- the routes: cite on the live value, list, the bytes nosniff in a sandbox,
  drop
- snapshots: the folder rides along by hash, counts include figures, a
  rehearsal looks for every cited figure, a new box gets them back, and a
  snapshot from before the folder existed still restores
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_loci.app import create_app
from seren_loci.config import BackupConfig, FiguresConfig, LociConfig, StorageConfig

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100
JPG = b"\xff\xd8\xff\xe0" + b"\x01" * 100


def _gallery(tmp_path: Path) -> Path:
    g = tmp_path / "gallery"
    g.mkdir()
    (g / "wiring.png").write_bytes(PNG)
    (g / "screen.jpg").write_bytes(JPG)
    (g / "notes.txt").write_text("words go in the fact")
    return g


def _cfg(tmp_path: Path, sources=None, **backup) -> LociConfig:
    return LociConfig(storage=StorageConfig(db_path=str(tmp_path / "loci" / "loci.db"), embedding_model=None),
                      backup=BackupConfig(every_hours=0, **backup),
                      figures=FiguresConfig(sources=[str(s) for s in (sources or [])]))


@pytest.fixture
def impl(tmp_path):
    pytest.importorskip("mcp")
    from seren_loci.mcp.tools import LociToolImpl
    cfg = _cfg(tmp_path, sources=[_gallery(tmp_path)])
    with TestClient(create_app(cfg)) as c:
        yield LociToolImpl(c.app.state.store, cfg), tmp_path


# ── a figure is never the fact ──────────────────────────────────────────────

def test_a_fact_cites_an_exhibit_and_shows_it(impl):
    from mcp.server.fastmcp import Image
    tools, tmp = impl
    g = tmp / "gallery"
    r = tools.set_fact(key="xavier.fan_header", value="the second header from the left", why="the first is the pump",
                       project="node-a", keep=[str(g / "wiring.png")])
    assert r["ok"] and len(r["kept"]) == 1 and r["refused"] == [] and "superseded_figures" not in r
    fig = r["kept"][0]
    assert fig["mime"] == "image/png" and fig["file"] == f"figure-{fig['digest'][:8]}.png"
    folder = tmp / "loci" / "figures"
    assert (folder / fig["digest"][:2] / fig["digest"]).read_bytes() == PNG

    got = tools.get_fact("xavier.fan_header", project="node-a")
    assert got["found"] and [f["digest"] for f in got["figures"]] == [fig["digest"]]
    assert tools.search_loci("fan header", project="node-a")["hits"][0]["figures"] == 1, "a hit says how many, not the rows"
    look = tools.look_at_fact("xavier.fan_header", project="node-a")
    assert isinstance(look, list) and look[0]["shown"] == 1 and look[0]["value"].startswith("the second")
    assert isinstance(look[1], Image) and look[1].data == PNG
    assert tools.look_at_fact("nope") == {"found": False, "project": "*", "key": "nope"}

    r2 = tools.keep_with_fact("xavier.fan_header", str(g / "screen.jpg"), caption="the label", project="node-a")
    assert r2["ok"] and r2["kept"][0]["caption"] == "the label"
    assert tools.get_fact("xavier.fan_header", project="node-a")["figures"][1]["mime"] == "image/jpeg"
    assert tools.keep_with_fact("nope", str(g / "screen.jpg"))["ok"] is False


def test_a_superseded_value_keeps_its_figures_and_the_new_one_cites_what_it_cites(impl):
    tools, tmp = impl
    g = tmp / "gallery"
    one = tools.set_fact(key="k", value="v1", keep=[str(g / "wiring.png")])
    d = one["kept"][0]["digest"]
    two = tools.set_fact(key="k", value="v2")
    assert two["superseded"] == one["id"] and [f["digest"] for f in two["superseded_figures"]] == [d]
    assert "figures" not in tools.get_fact("k"), "a new value does not inherit"
    hist = tools.fact_history("k")["history"]
    assert [len(h["figures"]) for h in hist] == [0, 1], "history keeps its own"
    three = tools.set_fact(key="k", value="v3", keep=[f"figure:{d}", "figure:" + "0" * 64])
    assert [k["digest"] for k in three["kept"]] == [d] and three["refused"][0]["why"].startswith("no figure with that digest")
    assert tools.store.figures.count() == 1, "cited again, not copied again"
    assert tools.store.counts()["figures"] == 2
    # forget is a flag: the retired row keeps its exhibit; a drop on the live value sweeps once nothing cites it
    assert tools.forget_fact("k")["ok"]
    assert tools.store.figures.has(d) and tools.store.sweep_figures(grace=0) == []
    tools.set_fact(key="j", value="x", keep=[str(g / "screen.jpg")])
    dj = tools.get_fact("j")["figures"][0]["digest"]
    assert tools.drop_from_fact("j", dj)["ok"] and not tools.drop_from_fact("j", dj)["ok"]
    assert tools.store.sweep_figures(grace=0) == [dj]
    assert tools.drop_from_fact("k", d)["ok"] is False, "no live value: history is not touched from here"


def test_over_mcp_the_look_is_a_text_block_and_image_blocks(impl):
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent
    from seren_loci.mcp.tools import register_tools
    tools, tmp = impl
    tools.set_fact(key="k", value="v", keep=[str(tmp / "gallery" / "wiring.png")])
    mcp = FastMCP("t")
    register_tools(mcp, tools.store, tools.config)
    got = asyncio.run(mcp.call_tool("look_at_fact", {"key": "k"}))
    blocks = got[0] if isinstance(got, tuple) else got
    assert [type(b) for b in blocks] == [TextContent, ImageContent] and blocks[1].mimeType == "image/png"
    got = asyncio.run(mcp.call_tool("get_fact", {"key": "k"}))
    blocks = got[0] if isinstance(got, tuple) else got
    assert [type(b) for b in blocks] == [TextContent]


# ── where the store may reach ───────────────────────────────────────────────

def test_only_the_shelves_the_config_names(impl, tmp_path):
    tools, tmp = impl
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(PNG)
    r = tools.set_fact(key="k", value="still set", keep=[str(outside), "https://example.test/a.png",
                                                       str(tmp / "gallery" / "notes.txt"), str(tmp / "gallery" / "nope.png")])
    assert r["ok"] and tools.get_fact("k")["value"] == "still set"
    whys = {x["source"].rsplit(os.sep, 1)[-1].rsplit("/", 1)[-1]: x["why"] for x in r["refused"]}
    assert "not under a folder the sources allow" in whys["elsewhere.png"]
    assert "no URL prefixes are configured" in whys["a.png"]
    assert "words go in the fact" in whys["notes.txt"]
    assert whys["nope.png"].startswith("no file at")
    assert r["kept"] == [] and tools.store.figures.count() == 0


def test_the_store_can_keep_nothing_when_told(tmp_path):
    cfg = _cfg(tmp_path, sources=[_gallery(tmp_path)])
    cfg.figures.enabled = False
    with TestClient(create_app(cfg)) as c:
        c.post("/fact", json={"key": "k", "value": "v"})
        r = c.post("/fact/figures", json={"key": "k", "source": str(tmp_path / "gallery" / "wiring.png")})
        assert r.status_code == 400 and "keeps no figures" in r.json()["detail"]
        assert [s["name"] for s in c.get("/stores").json()["stores"]] == ["facts", "models"]


# ── over HTTP ───────────────────────────────────────────────────────────────

def test_the_routes(tmp_path):
    g = _gallery(tmp_path)
    with TestClient(create_app(_cfg(tmp_path, sources=[g]))) as c:
        c.post("/fact", json={"key": "k", "value": "v", "project": "p"})
        r = c.post("/fact/figures", json={"project": "p", "key": "k", "source": str(g / "wiring.png"), "caption": "cap"})
        assert r.status_code == 200
        fig = r.json()["figure"]
        assert fig["caption"] == "cap" and fig["file"].endswith(".png")
        assert c.post("/fact/figures", json={"key": "nope", "source": str(g / "wiring.png")}).status_code == 404
        assert c.post("/fact/figures", json={"project": "p", "key": "k", "source": str(tmp_path / "x.png")}).status_code == 400
        assert c.post("/fact/figures", json={"project": "p", "key": "k", "source": "figure:" + "0" * 64}).status_code == 404
        rows = c.get("/fact/figures", params={"project": "p", "key": "k"}).json()
        assert rows["count"] == 1 and rows["figures"][0]["digest"] == fig["digest"]
        assert c.get("/fact", params={"project": "p", "key": "k"}).json()["figures"][0]["file"] == fig["file"]
        b = c.get(f"/fact/figures/{fig['digest']}", params={"project": "p", "key": "k"})
        assert b.status_code == 200 and b.content == PNG and b.headers["content-type"] == "image/png"
        assert b.headers["x-content-type-options"] == "nosniff" and b.headers["content-security-policy"].startswith("sandbox")
        assert c.get(f"/fact/figures/{fig['digest']}", params={"key": "other"}).status_code == 404
        assert c.get(f"/fact/figures/{'0' * 64}", params={"project": "p", "key": "k"}).status_code == 404
        assert c.get("/counts").json()["figures"] == 1
        # the value is superseded; the new one cites the old exhibit again
        c.post("/fact", json={"key": "k", "value": "v2", "project": "p"})
        assert c.get("/fact/figures", params={"project": "p", "key": "k"}).json()["count"] == 0
        r = c.post("/fact/figures", json={"project": "p", "key": "k", "source": f"figure:{fig['digest']}"})
        assert r.status_code == 200 and r.json()["figure"]["digest"] == fig["digest"]
        assert c.delete(f"/fact/figures/{fig['digest']}", params={"project": "p", "key": "k"}).status_code == 200
        assert c.delete(f"/fact/figures/{fig['digest']}", params={"project": "p", "key": "k"}).status_code == 404
        assert c.get("/counts").json()["figures"] == 1, "history still cites it"


# ── snapshots ───────────────────────────────────────────────────────────────

def test_figures_ride_in_the_snapshot_and_come_back_on_a_new_box(tmp_path):
    from seren_sinew.stores import verify_snapshot
    g = _gallery(tmp_path)
    with TestClient(create_app(_cfg(tmp_path, sources=[g]))) as c:
        c.post("/fact", json={"key": "k", "value": "the wiring", "project": "p"})
        fig = c.post("/fact/figures", json={"project": "p", "key": "k", "source": str(g / "wiring.png"),
                                             "caption": "the label"}).json()["figure"]
        snap = c.post("/stores/snapshot").json()["snapshot"]
        assert snap["counts"]["figures"] == 1
        man = json.loads((Path(snap["path"]) / "manifest.json").read_text())
        assert man["stores"] == ["facts", "figures"]
        assert f"raw/figures/{fig['digest'][:2]}/{fig['digest']}" in [f["path"] for f in man["files"]]
        assert "wiring" not in json.dumps(man) and "label" not in json.dumps(man)
        snap2 = c.post("/stores/snapshot").json()["snapshot"]
        a = Path(snap["path"]) / "raw" / "figures" / fig["digest"][:2] / fig["digest"]
        b = Path(snap2["path"]) / "raw" / "figures" / fig["digest"][:2] / fig["digest"]
        assert os.path.samefile(a, b) and verify_snapshot(Path(snap2["path"])) == []
        rep = c.post(f"/stores/snapshots/{snap2['id']}/rehearse").json()
        assert rep["ok"] and rep["check"]["counts"]["figures"] == 1, rep
        # a figure missing from the copy is noticed
        man_path = Path(snap["path"]) / "manifest.json"
        man["files"] = [f for f in man["files"] if not f["path"].endswith(fig["digest"])]
        man_path.write_text(json.dumps(man))
        a.unlink()
        rep = c.post(f"/stores/snapshots/{snap['id']}/rehearse").json()
        assert not rep["ok"] and any("1 of the 1 figure(s)" in p for p in rep["problems"]), rep
    new = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "new" / "loci.db"), embedding_model=None),
                     backup=BackupConfig(every_hours=0, restore_from=snap2["path"], restore_reason="the old box died"))
    with TestClient(create_app(new)) as c:
        f = c.get("/fact", params={"project": "p", "key": "k"}).json()
        assert f["figures"][0]["caption"] == "the label"
        assert c.get(f"/fact/figures/{fig['digest']}", params={"project": "p", "key": "k"}).content == PNG


def test_a_snapshot_from_before_there_were_figures_still_restores(tmp_path):
    with TestClient(create_app(_cfg(tmp_path))) as c:
        c.post("/fact", json={"key": "k", "value": "from before"})
        snap = c.post("/stores/snapshot").json()["snapshot"]
        assert json.loads((Path(snap["path"]) / "manifest.json").read_text())["stores"] == ["facts"]
    new = LociConfig(storage=StorageConfig(db_path=str(tmp_path / "new" / "loci.db"), embedding_model=None),
                     backup=BackupConfig(every_hours=0, restore_from=snap["path"], restore_reason="moving"))
    with TestClient(create_app(new)) as c:
        assert c.get("/fact", params={"key": "k"}).json()["value"] == "from before"
        assert c.get("/counts").json()["figures"] == 0


def test_the_yaml_block_is_honoured(tmp_path):
    from seren_loci.config import load_config
    y = tmp_path / "l.yaml"
    y.write_text("storage:\n  db_path: '%s'\nbackup:\n  compress: xz\nfigures:\n  max_bytes: 5\n  sources: ['/some/gallery', 'https://']\n"
                 % str(tmp_path / "loci.db").replace("\\", "/"), encoding="utf-8")
    cfg = load_config(str(y))
    assert cfg.backup.compress == "xz" and cfg.figures.max_bytes == 5 and cfg.figures.sources == ["/some/gallery", "https://"]
    assert LociConfig().figures.sources == [] and LociConfig().figures.enabled


# ── the viewer ──────────────────────────────────────────────────────────────

def test_the_viewer_shows_figures_and_the_backup_panel_and_counts_stay_a_closed_set(tmp_path):
    with TestClient(create_app(_cfg(tmp_path))) as c:
        page = c.get("/viewer").text
        assert "figuresStrip" in page and "/fact/figures/" in page, "a fact's exhibits are shown, fetched through the token"
        assert "renderBackups" in page and 'api("/stores")' in page, "the same backup panel every Seren viewer has"
        assert set(c.get("/counts").json()) == {"live", "history", "projects", "figures"}, "the overview's numbers: a closed set"


# ── the Figures hall: figure first (20 Oct 2026) ───────────────────────────
# Chad: "figures tab and modal view, and figures viewable in a loci key.
# mimicking what we have for eidetic memory for memory."

def test_the_gallery_lists_every_figure_once_with_what_cites_it(tmp_path):
    g = _gallery(tmp_path)
    with TestClient(create_app(_cfg(tmp_path, sources=[g]))) as c:
        store = c.app.state.store
        assert store.figure_gallery() == []
        assert c.get("/figures").json() == {"ok": True, "enabled": True, "count": 0, "bytes": 0, "figures": []}

        c.post("/fact", json={"key": "k", "value": "v1", "project": "p"})
        d = c.post("/fact/figures", json={"project": "p", "key": "k", "source": str(g / "wiring.png"), "caption": "the wiring"}).json()["figure"]["digest"]
        c.post("/fact", json={"key": "j", "value": "x", "project": "p"})
        dj = c.post("/fact/figures", json={"project": "p", "key": "j", "source": str(g / "screen.jpg")}).json()["figure"]["digest"]
        # the value is superseded; the new one cites the old exhibit again: one figure, two rows
        c.post("/fact", json={"key": "k", "value": "v2", "project": "p"})
        c.post("/fact/figures", json={"project": "p", "key": "k", "source": f"figure:{d}"})

        body = c.get("/figures").json()
        assert body["ok"] and body["enabled"] and body["count"] == 2 and body["bytes"] == len(PNG) + len(JPG)
        assert [p["digest"] for p in body["figures"]] == [dj, d], "newest kept first; cited again is not kept again"
        fig = body["figures"][1]
        assert fig["caption"] == "the wiring" and fig["file"] == f"figure-{d[:8]}.png" and fig["mime"] == "image/png"
        assert fig["via"] == {"project": "p", "key": "k"}, "the bytes come through the live citing fact"
        assert fig["missing"] is False
        assert [(f["value"], f["live"]) for f in fig["facts"]] == [("v1", False), ("v2", True)]
        assert c.get(f"/fact/figures/{d}", params=fig["via"]).content == PNG

        # retire the live value: history alone cites it, so there is nothing to serve it through
        c.delete("/fact", params={"project": "p", "key": "k"})
        fig = next(p for p in c.get("/figures").json()["figures"] if p["digest"] == d)
        assert fig["via"] is None and [f["live"] for f in fig["facts"]] == [False, False]
        assert [p["digest"] for p in c.get("/figures", params={"include_history": "false"}).json()["figures"]] == [dj]
        assert c.get("/figures", params={"limit": 1, "offset": 1}).json()["figures"][0]["digest"] == d

        # the bytes gone from the folder: the gallery says so, the rows still name it
        folder = tmp_path / "loci" / "figures"
        (folder / dj[:2] / dj).unlink()
        fig = next(p for p in c.get("/figures").json()["figures"] if p["digest"] == dj)
        assert fig["missing"] is True and fig["via"] == {"project": "p", "key": "j"}


def test_the_store_that_keeps_nothing_still_answers_the_gallery(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.figures.enabled = False
    with TestClient(create_app(cfg)) as c:
        body = c.get("/figures").json()
        assert body == {"ok": True, "enabled": False, "count": 0, "bytes": 0, "figures": []}


def test_the_viewer_has_a_figures_hall(tmp_path):
    with TestClient(create_app(_cfg(tmp_path))) as c:
        page = c.get("/viewer").text
        assert 'data-tab="figures"' in page and 'id="figureGallery"' in page and "loadFigures" in page
        assert 'api("/figures?' in page and "figureTile" in page, "the hall reads the gallery route"
        assert "openFigure" in page and "cited by" in page, "open a figure, see the facts that cite it"
        assert "pic-head" in page and "pic-close" in page, "the modal's header row and square close: the family's shape"
        assert "data-open-fig" in page, "a figure on a fact's strip opens the same modal"
