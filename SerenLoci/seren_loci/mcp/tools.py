"""
seren_loci.mcp.tools
════════════════════

The tools the MCP server exposes - the left brain's nerves. Each is a thin
wrapper over LociStore (in-process; we're mounted INTO the same FastAPI app
that owns the store, so there's no point HTTP-round-tripping ourselves).

STRUCTURE

`LociToolImpl` holds every tool as a method. `register_tools` wires each method
onto a FastMCP instance via `@mcp.tool()`. The split exists for testability -
`LociToolImpl(...).set_fact(...)` is directly callable in unit tests without
FastMCP, an MCP client, or an HTTP roundtrip. See `tests/test_mcp_tools.py`.

TOOL ROSTER:

    set_fact        set/replace the live value for a (project, key) - strict
                    supersede, old value kept as history, never blended
    get_fact        THE live value for a (project, key), deterministically
    search_loci     discovery: exact-key first, then the finder (vector if an
                    embedder is configured, else FTS5 lexical)
    forget_fact     retire a key's live value (soft supersede - 'a flag, not a
                    scalpel'); kept as history
    fact_history    every value a key has ever held, newest first
    list_facts      scan a whole scope (a project, or everything)

NAMING: search_loci (not 'search') and fact_* prefixes keep these distinct from
SerenMemory's tools (recall/remember/forget_memory) when a model is connected to
BOTH hemispheres at once - no collisions.
"""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..config import LociConfig
from ..models.schemas import FactWrite, FUNDAMENTALS, Source
from ..store import LociStore


class LociToolImpl:
    """The actual tool implementations, callable both via FastMCP decoration
    (in production) and directly (in unit tests).

    Every return shape is JSON-serialisable - the FastMCP layer serialises it
    on the way out to the MCP client.
    """

    def __init__(self, store: LociStore, config: LociConfig) -> None:
        self.store = store
        self.config = config

    # -- write ------------------------------------------------------------
    def set_fact(self, key: str, value: str, why: Optional[str] = None,
                 project: str = FUNDAMENTALS) -> dict:
        """Set (or replace) a fact in the left brain - an addressable, durable
        piece of coding/logic knowledge.

        project defaults to '*' (fundamentals: cross-project truths like
        'camelCase is life'); pass a concrete project name for a per-project
        convention. If a live value already exists for this (project, key) it
        is SUPERSEDED - kept as history, never blended into the new value.

        `why` is not decoration: it's the hard-won reason the fact is shaped
        this way, and it's searchable. Always include it when there is one.
        """
        prior = self.store.get_fact(project, key)
        fact = self.store.set_fact(FactWrite(
            project=project, key=key, value=value, why=why, source=Source.MODEL))
        return {
            "ok": True,
            "id": fact.id,
            "project": project,
            "key": key,
            "superseded": prior.id if prior else None,
        }

    # -- read (exact) -----------------------------------------------------
    def get_fact(self, key: str, project: str = FUNDAMENTALS) -> dict:
        """Get THE live value for a (project, key), deterministically. This is
        the spine: exact address in, the thing out, no ranking, no guessing.

        project defaults to '*' (fundamentals). Returns {found: false} if there
        is no live value (a never-set or retired key) - that's a clean answer,
        not an error.
        """
        f = self.store.get_fact(project, key)
        if f is None:
            return {"found": False, "project": project, "key": key}
        return {
            "found": True,
            "project": f.project,
            "key": f.key,
            "value": f.value,
            "why": f.why,
        }

    # -- read (discovery) -------------------------------------------------
    def search_loci(self, query: str, n_results: int = 10,
                    project: Optional[str] = None,
                    include_fundamentals: bool = True) -> dict:
        """Search the left brain when you DON'T know the exact key - the
        associative jump ('that CUDA thing', 'the brace rule').

        An exact key match leads at score 1.0; otherwise the finder runs
        (semantic vector search if an embedder is configured, else FTS5 lexical
        over key/value/why). project=None searches every scope; a concrete
        project narrows to it (and folds in fundamentals unless you turn that
        off). Every hit carries a normalized 0..1 score and a match_kind.
        """
        hits, finder = self.store.search(
            query, project=project, n_results=n_results,
            include_fundamentals=include_fundamentals)
        return {
            "query": query,
            "project": project,
            "finder": finder,
            "hits": [h.model_dump() for h in hits],
        }

    # -- retire -----------------------------------------------------------
    def forget_fact(self, key: str, project: str = FUNDAMENTALS) -> dict:
        """Retire the live value for a (project, key) - a soft supersede, 'a
        flag, not a scalpel'. The row stays as history; it just stops being the
        live answer, and the key is free to be set fresh later. Returns ok:false
        if there was no live value to retire."""
        ok = self.store.forget(project, key)
        return {
            "ok": ok,
            "project": project,
            "key": key,
            "note": "retired (kept as history)" if ok
                    else "no live value to retire",
        }

    # -- history ----------------------------------------------------------
    def fact_history(self, key: str, project: str = FUNDAMENTALS) -> dict:
        """Every value a (project, key) has ever held, newest first - the audit
        trail the strict-supersede rule preserves. Useful for 'what did we used
        to think the right answer was, and why did it change'."""
        rows = self.store.get_history(project, key)
        return {
            "project": project,
            "key": key,
            "count": len(rows),
            "history": [f.model_dump() for f in rows],
        }

    # -- bulk -------------------------------------------------------------
    def list_facts(self, project: Optional[str] = None,
                   include_superseded: bool = False) -> dict:
        """List facts in scope - a whole project, or everything (project=None).
        For surveying what the left brain knows. Pass include_superseded=true to
        fold in history rows too."""
        facts = self.store.list_facts(
            project=project, include_superseded=include_superseded)
        return {"count": len(facts), "facts": [f.model_dump() for f in facts]}


# ═══════════════════════════════════════════════════════════════════════
#  Registration entry point
# ═══════════════════════════════════════════════════════════════════════
def register_tools(mcp: FastMCP, store: LociStore,
                   config: LociConfig) -> LociToolImpl:
    """Attach every LociToolImpl method to the given FastMCP instance via the
    @mcp.tool() decorator. Returns the impl object so callers that need a handle
    (e.g. direct invocation in tests) can keep one."""
    impl = LociToolImpl(store, config)

    mcp.tool()(impl.set_fact)
    mcp.tool()(impl.get_fact)
    mcp.tool()(impl.search_loci)
    mcp.tool()(impl.forget_fact)
    mcp.tool()(impl.fact_history)
    mcp.tool()(impl.list_facts)

    return impl
