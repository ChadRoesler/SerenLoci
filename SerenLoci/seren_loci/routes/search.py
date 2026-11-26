"""
Search route - /search.

The discovery path: exact-key first (score 1.0), then the finder (vector if an
embedder is configured, else FTS5 lexical), merged and trimmed. The response
echoes which finder served so a caller can tell whether it's getting the
associative jump or just lexical matching.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Request

from ..models.schemas import NameSearchRequest, NameSearchResponse, SearchRequest, SearchResponse

router = APIRouter(tags=["search"])


@router.post("/by_name")
async def by_name(request: Request, req: NameSearchRequest = Body(...)) -> NameSearchResponse:
    """The association door: the live facts filed under any of `names` (a
    project, a key, or words of either), by exact name and never by meaning.
    The twin of an episodic store's lookup by topic tag, so an episode tagged
    with a thing can bring the facts about that thing. See LociStore.by_name."""
    store = request.app.state.store
    hits = store.by_name(req.names, n_results=req.n_results, exclude_ids=req.exclude_ids, query=req.query)
    return NameSearchResponse(names=req.names, hits=hits, finder=store.finder_kind)


@router.post("/search")
async def search(request: Request, req: SearchRequest = Body(...)) -> SearchResponse:
    store = request.app.state.store
    hits, finder = store.search(
        req.query,
        project=req.project,
        n_results=req.n_results,
        include_fundamentals=req.include_fundamentals,
        include_superseded=req.include_superseded,
        with_citations=req.with_citations,
    )
    return SearchResponse(query=req.query, project=req.project,
                          hits=hits, finder=finder)
