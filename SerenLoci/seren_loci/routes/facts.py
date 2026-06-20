"""
Facts routes - /facts, /counts.

Bulk read + operational counts. /facts backs the (future) viewer and any
caller that wants to scan a whole scope; /counts is the cheap liveness-of-data
signal.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request

router = APIRouter(tags=["facts"])


@router.get("/facts")
async def list_facts(request: Request, project: Optional[str] = None,
                     include_superseded: bool = False):
    """All live facts, newest-key-order, optionally scoped to one project.
    Pass include_superseded=true to fold in history rows too (for the audit
    view)."""
    store = request.app.state.store
    facts = store.list_facts(project=project, include_superseded=include_superseded)
    return {"count": len(facts), "facts": [f.model_dump() for f in facts]}


@router.get("/counts")
async def counts(request: Request):
    """{live, history, projects} - the store's at-a-glance state."""
    return request.app.state.store.counts()
