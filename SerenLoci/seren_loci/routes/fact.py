"""
Fact routes - /fact/*.

The left brain's read/write surface. Query params (not path segments) carry
project + key on purpose: a key like 'posh.brace_style' or a project of '*'
(fundamentals) is awkward and ambiguous to URL-encode as a path segment, and
keys are free-form strings. Query params dodge all of that.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from ..models.schemas import FactWrite, FUNDAMENTALS

router = APIRouter(prefix="/fact", tags=["facts"])


@router.post("")
async def set_fact(request: Request, w: FactWrite = Body(...)):
    """Set (or replace) the live value for (project, key).

    If a live value already existed it is superseded - kept as history, never
    blended. The response names the superseded id (or null) so the caller can
    see whether this was a fresh write or a replacement.
    """
    store = request.app.state.store
    prior = store.get_fact(w.project, w.key)   # was there a live value?
    fact = store.set_fact(w)
    return {
        "ok": True,
        "fact": fact.model_dump(),
        "superseded": prior.id if prior else None,
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


@router.delete("")
async def forget_fact(request: Request, key: str, project: str = FUNDAMENTALS):
    """Retire the live value for a key (soft supersede - 'forget is a flag, not
    a scalpel'). The row stays as history; it just stops being the live answer.
    404 if there was no live value to retire."""
    store = request.app.state.store
    ok = store.forget(project, key)
    if not ok:
        raise HTTPException(404, f"no live fact to forget for project={project!r} key={key!r}")
    return {"ok": True, "forgotten": {"project": project, "key": key}}
