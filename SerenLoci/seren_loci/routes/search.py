"""
Search route - /search.

The discovery path: exact-key first (score 1.0), then the finder (vector if an
embedder is configured, else FTS5 lexical), merged and trimmed. The response
echoes which finder served so a caller can tell whether it's getting the
associative jump or just lexical matching.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..models.schemas import SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/search")
async def search(request: Request, req: SearchRequest = Body(...)) -> SearchResponse:
    store = request.app.state.store
    hits, finder = store.search(
        req.query,
        project=req.project,
        n_results=req.n_results,
        include_fundamentals=req.include_fundamentals,
        include_superseded=req.include_superseded,
    )
    return SearchResponse(query=req.query, project=req.project,
                          hits=hits, finder=finder)
