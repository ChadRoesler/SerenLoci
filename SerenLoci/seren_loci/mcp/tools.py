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
                    scalpel'); kept as history, with why it was retired
    move_fact       re-address a fact (another project or key); the old
                    address retired with the reason, the history following
    fact_history    every value a key has ever held, newest first, and why
                    each one changed
    list_facts      scan a whole scope (a project, or everything)

    The descriptions below are how the keeping travels: whoever lives in a
    Loci reads them, so they say what Loci is for (anything held as fact,
    not only code), one claim per key, and the what and why here with the
    how left to the code (19 Oct 2026: set_fact called itself "coding/logic
    knowledge", and the store filled with changelogs).

    keep_with_fact  cite an exhibit on the live value (a wiring photo, a
                    screenshot of the error) - set_fact takes `keep` too
    look_at_fact    the live value and its figures, SHOWN as images
    drop_from_fact  take one figure off the live value

    A figure is never the fact (15 Oct 2026). The fact is a sentence with a
    key, searchable and supersedable, with a why; the figure is what the
    sentence points at - "see figure". So there is no way to put a picture
    in Loci without saying in words what it proves, and a superseded value
    keeps its own figures in history while the new value cites what it
    cites (`figure:<digest>` cites one again).

NAMING: search_loci (not 'search') and fact_* prefixes keep these distinct from
SerenMemory's tools (recall/remember/forget_memory) when a model is connected to
BOTH hemispheres at once - no collisions.
"""
from __future__ import annotations

from typing import Any, Optional

from mcp.server.fastmcp import FastMCP, Image
from seren_sinew.blobs import choose_shown, keep_each

from ..config import LociConfig
from ..models.schemas import FactMove, FactWrite, FUNDAMENTALS, Source
from ..store import LociStore

CITE = "figure:"            # keep=["figure:<digest>"]: cite a figure the store already holds


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
                 project: str = FUNDAMENTALS, keep: Optional[list[str]] = None,
                 why_changed: Optional[str] = None) -> dict:
        """Set (or replace) a fact in the left brain: something you hold as
        true and want to find again by its address. Anything you deal with
        as fact belongs here - a convention, a decision, a contract between
        you and someone, a thought of your own you mean to keep - not only
        code.

        ONE CLAIM PER KEY. A fact holding several claims runs past the
        finder's window (the answer's `notes` say when), and has to be
        rewritten whole when one of them changes; small facts are found and
        superseded one at a time. Keep the WHAT and the WHY here and leave
        the HOW (file names, function names, versions) to the code and its
        docs, with the fact pointing there: the how goes stale the moment the
        code moves, and nothing tells the fact.

        project defaults to '*' (fundamentals: true everywhere). Any other
        name is a scope of your choosing - a project, a person, a place. If a
        live value already exists for this (project, key) it is SUPERSEDED -
        kept as history, never blended into the new value.

        `why` is not decoration: it's the hard-won reason the fact is shaped
        this way, and it's searchable. Always include it when there is one,
        and keep it about the fact.

        `why_changed`: when this replaces a live value, why it changed ("moved
        to the NUC", "the first reading was wrong"). It is kept on the old
        value in history, so the new why stays about the fact; fact_history
        shows it.

        `keep`: exhibits this value cites - a wiring photo, a screenshot of
        the error, a diagram. Paths on this box or URLs under the shelves
        figures.sources names, or `figure:<digest>` to cite one the store
        already holds (the superseded value's, listed in the answer as
        `superseded_figures`: a new value does not inherit them, it cites
        what it cites). The answer lists what was kept and what was refused
        and why; the fact is set either way.

        `notes` in the answer is what the store saw in the write: no why, a
        part past the finder's window, a project name that reads as one you
        already have. Never a refusal; yours to act on or not.
        """
        prior = self.store.get_fact(project, key)
        w = FactWrite(project=project, key=key, value=value, why=why,
                      why_changed=why_changed, source=Source.MODEL)
        fact = self.store.set_fact(w)
        out: dict[str, Any] = {
            "ok": True,
            "id": fact.id,
            "project": project,
            "key": key,
            "superseded": prior.id if prior else None,
            "why_changed": ((why_changed or "").strip() or None) if prior else None,
            "notes": self.store.write_notes(w, replaced=prior is not None),
        }
        if prior is not None and prior.figures:
            out["superseded_figures"] = [f.model_dump() for f in prior.figures]
        if keep:
            out["kept"], out["refused"] = self._keep_many(fact.id, keep)
        return out

    def _keep_many(self, fact_id: str, refs: list[str], caption: Optional[str] = None
                   ) -> tuple[list[dict], list[dict]]:
        """Each reference cited: a `figure:<digest>` by copying its row, the
        rest fetched and kept. What cannot be is listed with why, and the
        rest still land."""
        cites = [r for r in refs if r.startswith(CITE)]
        fetches = [r for r in refs if not r.startswith(CITE)]
        kept, refused = [], []
        for ref in cites:
            fig = self.store.cite_figure(fact_id, ref[len(CITE):].strip(), caption)
            if fig is None:
                refused.append({"source": ref, "why": "no figure with that digest is held here"})
            else:
                kept.append(fig.model_dump())
        if fetches:
            fg = self.config.figures
            got, bad = keep_each(
                fetches, lambda data, ref: (f := self.store.keep_figure(fact_id, data, caption=caption, source=ref))
                and f.model_dump(), fg.sources, fg.max_bytes)
            kept += got
            refused += bad
        return kept, refused

    # -- figures: the exhibits a fact cites ----------------------------------
    def keep_with_fact(self, key: str, source: str, caption: Optional[str] = None,
                       project: str = FUNDAMENTALS) -> dict:
        """Cite an exhibit on the live value of a (project, key): the wiring
        photo behind "the fan header is the second one", the screenshot
        behind "the 409 looks like this", the diagram the sentence describes.

        The figure is never the fact. The fact stays a sentence with a key,
        searchable, supersedable; the figure is what it points at, kept in
        Loci's own folder under its hash and in the same snapshots. When the
        value is superseded, its figures stay with it in history; the new
        value cites what it cites (set_fact's `keep`, `figure:<digest>` to
        cite one again).

        `source`: a path on this box or a URL, under the shelves
        figures.sources names, or `figure:<digest>`. What is kept is decided
        by the bytes, never the filename. look_at_fact shows it back.
        """
        f = self.store.get_fact(project, key)
        if f is None:
            return {"ok": False, "project": project, "key": key, "error": "no live value for that key"}
        kept, refused = self._keep_many(f.id, [source], caption)
        return {"ok": bool(kept), "project": project, "key": key, "id": f.id, "kept": kept, "refused": refused}

    # No return annotation, on purpose: with one, FastMCP tries to serialise
    # the answer as structured output and cannot serialise an Image.
    def look_at_fact(self, key: str, project: str = FUNDAMENTALS):
        """The live value of a (project, key) and the exhibits it cites -
        SHOWN, as images, not described. get_fact and search tell you a fact
        has figures (`figures`); this is how you see them.

        Pictures come back as image content beside the fact. Up to about 8
        MB per look; the rest, and anything that is not a still picture (a
        clip, a sound, a PDF), is listed under `not_shown` with the reason.
        """
        f = self.store.get_fact(project, key)
        if f is None:
            return {"found": False, "project": project, "key": key}

        def read(row: dict) -> Optional[bytes]:
            got = self.store.open_figure(f.id, row["digest"])
            if got is None:
                return None
            with got[1] as fh:
                return fh.read()
        rows = [{"digest": g.digest, "file": g.file, "mime": g.mime, "bytes": g.bytes} for g in f.figures]
        shown, not_shown = choose_shown(rows, read)
        text = {"found": True, **f.model_dump(), "shown": len(shown),
                "not_shown": [{k: v for k, v in r.items() if k != "digest"} for r in not_shown]}
        return [text, *(Image(data=data, format=fmt) for data, fmt in shown)] if shown else text

    def drop_from_fact(self, key: str, digest: str, project: str = FUNDAMENTALS) -> dict:
        """Take one figure off the live value of a (project, key). The fact
        stays; the figure's row goes, and its bytes go with it once no row
        cites them. Needs the figure's `digest` (from the fact's `figures`).
        History's figures are history: they are not touched from here.
        """
        f = self.store.get_fact(project, key)
        if f is None:
            return {"ok": False, "project": project, "key": key, "error": "no live value for that key"}
        ok = self.store.drop_figure(f.id, digest)
        return {"ok": ok, "project": project, "key": key, "digest": digest,
                "note": "dropped" if ok else "the live value cites no such figure"}

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
        out = {
            "found": True,
            "project": f.project,
            "key": f.key,
            "value": f.value,
            "why": f.why,
        }
        if f.figures:
            out["figures"] = [g.model_dump() for g in f.figures]       # the rows; look_at_fact shows them
        return out

    # -- read (discovery) -------------------------------------------------
    def search_loci(self, query: str, n_results: int = 10,
                    project: Optional[str] = None,
                    include_fundamentals: bool = True,
                    include_superseded: bool = False) -> dict:
        """Search the left brain alone - your facts, contracts and
        conventions - when you DON'T know the exact key: the associative jump
        ('that CUDA thing', 'the brace rule', 'what did we agree about bed').

        WHEN A CORPUS CALLOSUM IS CONNECTED, ITS `search` COMES FIRST: one
        call reaches these facts AND the right brain's episodic memory,
        merged and ranked - a fuller picture for fewer tokens than calling
        here and `recall` both. Reach for `search_loci` when you specifically
        want only facts: a project's conventions, a key you half remember,
        the history of a value. With no callosum installed, this is the
        left brain's retrieval path.

        An exact key match leads at score 1.0; otherwise the finder runs
        (semantic vector search if an embedder is configured, else FTS5 lexical
        over project/key/value/why; the vectors hold a fact's why on a lane of
        its own, so a long value cannot push it out). The project is part of
        what the finder reads, so a question naming its subject ('Laionan's
        title') finds that project's fact even unscoped. project=None searches every
        scope; a concrete project narrows to it (and folds in fundamentals
        unless you turn that off). Every hit carries a normalized 0..1 score
        and a match_kind. include_superseded=true folds history in - every
        retired value of a matching key, and lexical hits on retired rows -
        each marked live=false with its superseded_at and why_changed, and
        never ranked above the live value.
        """
        hits, finder = self.store.search(
            query, project=project, n_results=n_results,
            include_fundamentals=include_fundamentals,
            include_superseded=include_superseded)
        return {
            "query": query,
            "project": project,
            "finder": finder,
            "hits": [h.model_dump() for h in hits],
        }

    # -- retire -----------------------------------------------------------
    def forget_fact(self, key: str, project: str = FUNDAMENTALS,
                    why_changed: Optional[str] = None) -> dict:
        """Retire the live value for a (project, key) - a soft supersede, 'a
        flag, not a scalpel'. The row stays as history; it just stops being the
        live answer, and the key is free to be set fresh later. `why_changed`
        says why it no longer holds, and stays with it in history. Returns
        ok:false if there was no live value to retire."""
        ok = self.store.forget(project, key, why_changed)
        return {
            "ok": ok,
            "project": project,
            "key": key,
            "note": "retired (kept as history)" if ok
                    else "no live value to retire",
        }

    def move_fact(self, key: str, to_project: Optional[str] = None, to_key: Optional[str] = None,
                  project: str = FUNDAMENTALS, why_changed: Optional[str] = None) -> dict:
        """Move the live value of a (project, key) to another address: a
        different project, a different key, or both (either defaults to where
        it is now). For tidying - two project names that mean one scope
        ('seren' and 'Seren'), a fact filed under the wrong one, a key that
        says the wrong thing.

        The old address is retired with `why_changed` ("one scope, one name")
        and its history stays as it was written; the new address carries the
        same value, why and figures, and fact_history there follows the move
        back. Refused when the target already holds a live value: supersede
        or move that one first, nothing is ever blended. A whole scope moves
        a key at a time (list_facts(project=...) gives the keys).
        """
        try:
            moved = self.store.move_fact(FactMove(project=project, key=key, to_project=to_project,
                                                  to_key=to_key, why_changed=why_changed))
        except ValueError as e:
            return {"ok": False, "project": project, "key": key, "error": str(e)}
        if moved is None:
            return {"ok": False, "project": project, "key": key, "error": "no live value to move"}
        return {
            "ok": True,
            "id": moved.id,
            "project": moved.project,
            "key": moved.key,
            "moved_from": {"project": project, "key": key},
            "notes": self.store.write_notes(
                FactWrite(project=moved.project, key=moved.key, value=moved.value, why=moved.why),
                replaced=False),
        }

    # -- history ----------------------------------------------------------
    def fact_history(self, key: str, project: str = FUNDAMENTALS) -> dict:
        """Every value a (project, key) has ever held, newest first - the audit
        trail the strict-supersede rule preserves. Useful for 'what did we used
        to think the right answer was, and why did it change': a retired row
        carries `why_changed` when whoever retired it said. A fact that was
        moved here brings the history from its old address along, each row
        keeping the project and key it was written under."""
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
        For surveying what you hold as fact, and for tidying it: facts that
        hold several claims, project names that mean one scope, a why gone
        stale. Pass include_superseded=true to fold in history rows too."""
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
    mcp.tool()(impl.move_fact)
    mcp.tool()(impl.fact_history)
    mcp.tool()(impl.list_facts)
    mcp.tool()(impl.keep_with_fact)
    mcp.tool()(impl.look_at_fact)
    mcp.tool()(impl.drop_from_fact)

    return impl
