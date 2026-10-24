"""
Fact routes - /fact/*.

The left brain's read/write surface. Query params (not path segments) carry
project + key on purpose: a key like 'posh.brace_style' or a project of '*'
(fundamentals) is awkward and ambiguous to URL-encode as a path segment, and
keys are free-form strings. Query params dodge all of that.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from typing import Optional

from ..models.schemas import FactMove, FactWrite, FigureWrite, FUNDAMENTALS

router = APIRouter(prefix="/fact", tags=["facts"])


@router.post("")
async def set_fact(request: Request, w: FactWrite = Body(...)):
    """Set (or replace) the live value for (project, key).

    If a live value already existed it is superseded - kept as history, never
    blended, with `why_changed` kept on it. The response names the superseded
    id (or null) so the caller can see whether this was a fresh write or a
    replacement, echoes the `why_changed` it kept on that retired row (null
    when nothing was kept: the field's presence says this store keeps
    reasons), and carries the store's `notes` on the write (never a refusal:
    the fact is set either way).
    """
    store = request.app.state.store
    prior = store.get_fact(w.project, w.key)   # was there a live value?
    fact = store.set_fact(w)
    return {
        "ok": True,
        "fact": fact.model_dump(),
        "superseded": prior.id if prior else None,
        "why_changed": ((w.why_changed or "").strip() or None) if prior else None,
        "notes": store.write_notes(w, replaced=prior is not None),
    }


@router.post("/move")
async def move_fact(request: Request, m: FactMove = Body(...)):
    """Move the live value of (project, key) to (to_project, to_key). The old
    address is retired with `why_changed`; the history follows. 404 with no
    live value to move, 409 when the target already holds one or the move
    goes nowhere."""
    store = request.app.state.store
    try:
        moved = store.move_fact(m)
    except ValueError as e:
        raise HTTPException(409, str(e))
    if moved is None:
        raise HTTPException(404, f"no live fact to move for project={m.project!r} key={m.key!r}")
    return {
        "ok": True,
        "fact": moved.model_dump(),
        "moved_from": {"project": m.project, "key": m.key},
        "notes": store.write_notes(
            FactWrite(project=moved.project, key=moved.key, value=moved.value, why=moved.why), replaced=False),
    }


@router.get("")
async def get_fact(request: Request, key: str, project: str = FUNDAMENTALS):
    """The live value for (project, key), deterministically. 404 if there's no
    live value (a retired/never-set key). This is the spine - exact address in,
    THE thing out, no ranking."""
    store = request.app.state.store
    f = store.get_fact(project, key)
    if f is None:
        raise HTTPException(404, f"no live fact for project={project!r} key={key!r}")
    return f.model_dump()


@router.get("/history")
async def get_history(request: Request, key: str, project: str = FUNDAMENTALS):
    """Every value (project, key) has ever held, newest first - the audit trail
    the strict-supersede rule preserves. Empty list if the key is unknown."""
    store = request.app.state.store
    rows = store.get_history(project, key)
    return {
        "project": project,
        "key": key,
        "history": [f.model_dump() for f in rows],
        "count": len(rows),
    }


# ── figures: the exhibits a fact cites (seren_sinew.blobs) ──────────────────
# On the LIVE value of (project, key), by query params like the rest. A
# figure is never the fact: the row says what it shows, the bytes sit in the
# figures folder under their hash, and both ride in the same snapshot.

@router.post("/figures")
async def keep_with_fact(request: Request, w: FigureWrite = Body(...)):
    """Cite an exhibit on the live value: `source` is a path or URL under
    figures.sources, or figure:<digest> to cite one the store holds. 404
    with no live value, 400 when it may not be taken from there or is not
    something Loci keeps."""
    from seren_sinew.blobs import BlobStoreFull, BlobTooLarge, SourceRefused, fetch
    store = request.app.state.store
    f = store.get_fact(w.project, w.key)
    if f is None:
        raise HTTPException(404, f"no live fact for project={w.project!r} key={w.key!r}")
    cfg = request.app.state.config.figures
    try:
        if w.source.startswith("figure:"):
            fig = store.cite_figure(f.id, w.source[len("figure:"):].strip(), w.caption)
            if fig is None:
                raise HTTPException(404, "no figure with that digest is held here")
        else:
            data = await asyncio.to_thread(fetch, w.source, cfg.sources, cfg.max_bytes)
            fig = await asyncio.to_thread(store.keep_figure, f.id, data, w.caption, w.source)
    except (SourceRefused, BlobTooLarge, BlobStoreFull, ValueError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "id": f.id, "project": w.project, "key": w.key, "figure": fig.model_dump()}


@router.get("/figures")
async def list_figures(request: Request, key: str, project: str = FUNDAMENTALS):
    """The exhibits the live value cites: the rows (what, how big, its
    `file` name); the bytes are at /fact/figures/{digest}."""
    store = request.app.state.store
    f = store.get_fact(project, key)
    if f is None:
        raise HTTPException(404, f"no live fact for project={project!r} key={key!r}")
    return {"project": project, "key": key, "id": f.id, "count": len(f.figures),
            "figures": [g.model_dump() for g in f.figures]}


@router.get("/figures/{digest}")
async def get_figure(request: Request, digest: str, key: str, project: str = FUNDAMENTALS):
    """One figure's bytes, served as what they are and nothing more: nosniff,
    a sandbox CSP, inline under the figure's own name. Only through the live
    fact that cites it."""
    store = request.app.state.store
    f = store.get_fact(project, key)
    got = store.open_figure(f.id, digest) if f is not None else None
    if got is None:
        raise HTTPException(404, f"no figure '{digest}' on the live value of project={project!r} key={key!r}")
    fig, fh = got

    def body():
        with fh:
            while chunk := fh.read(1 << 20):
                yield chunk
    return StreamingResponse(body(), media_type=fig.mime or "application/octet-stream", headers={
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; media-src data:",
        "Content-Disposition": f'inline; filename="{fig.file}"',
        "Content-Length": str(fig.bytes), "Cache-Control": "private, max-age=86400"})


@router.delete("/figures/{digest}")
async def drop_from_fact(request: Request, digest: str, key: str, project: str = FUNDAMENTALS):
    """Take one figure off the live value. History's figures are history."""
    store = request.app.state.store
    f = store.get_fact(project, key)
    if f is None or not store.drop_figure(f.id, digest):
        raise HTTPException(404, f"no figure '{digest}' on the live value of project={project!r} key={key!r}")
    return {"ok": True, "id": f.id, "project": project, "key": key, "digest": digest, "dropped": True}


@router.delete("")
async def forget_fact(request: Request, key: str, project: str = FUNDAMENTALS,
                      why_changed: Optional[str] = None):
    """Retire the live value for a key (soft supersede - 'forget is a flag, not
    a scalpel'). The row stays as history, with `why_changed` if given; it
    just stops being the live answer. 404 if there was no live value to
    retire."""
    store = request.app.state.store
    ok = store.forget(project, key, why_changed)
    if not ok:
        raise HTTPException(404, f"no live fact to forget for project={project!r} key={key!r}")
    return {"ok": True, "forgotten": {"project": project, "key": key}}


# ── citations: what a fact rests on (23 Oct 2026) ───────────────────────────

@router.post("/cite")
async def cite_fact(request: Request, body: dict = Body(...)):
    """(project, key) rests on (on_project, on_key): it holds because that one
    does. Both live. 400 for a fact resting on itself, 404 for an address
    with no live value."""
    store = request.app.state.store
    p, k = body.get("project", FUNDAMENTALS), body.get("key")
    op, ok_ = body.get("on_project", FUNDAMENTALS), body.get("on_key")
    if not k or not ok_:
        raise HTTPException(400, "key and on_key are required")
    try:
        new = store.cite(p, k, op, ok_, body.get("why"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    except LookupError as e:
        raise HTTPException(404, str(e).strip("'\""))
    return {"ok": True, "new": new, "project": p, "key": k, "on_project": op, "on_key": ok_}


@router.delete("/cite")
async def uncite_fact(request: Request, key: str, on_key: str, project: str = FUNDAMENTALS,
                      on_project: str = FUNDAMENTALS):
    """Take a citation back. 404 when the fact did not rest on that one."""
    if not request.app.state.store.uncite(project, key, on_project, on_key):
        raise HTTPException(404, "it did not rest on that fact")
    return {"ok": True}


@router.get("/citations")
async def citations(request: Request, key: str, project: str = FUNDAMENTALS):
    """What the fact rests on, what rests on it, and what changed under it."""
    store = request.app.state.store
    if store.get_fact(project, key) is None:
        raise HTTPException(404, f"no live fact for project={project!r} key={key!r}")
    return {"project": project, "key": key, **store.citations(project, key)}


@router.post("/still-holds")
async def still_holds(request: Request, key: str, project: str = FUNDAMENTALS):
    """Looked at, and it still holds: clear what waited to be rechecked."""
    return {"ok": True, "cleared": request.app.state.store.still_holds(project, key)}
