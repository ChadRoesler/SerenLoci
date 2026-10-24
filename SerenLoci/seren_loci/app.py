"""
seren_loci.app
════════════════════════════════════════════════════════════════════════

The FastAPI application for the left brain. Wires the store, routes, optional
bearer auth, and (when the [mcp] extra is installed) an MCP surface so a
connected model can reach facts directly.

ENDPOINTS:
    GET    /                     - service info + counts + finder kind
    GET    /health               - liveness
    GET    /viewer               - the Loci viewer (when it ships)
    POST   /fact                 - set/replace a fact (strict supersede)
    GET    /fact                 - get the live value (project, key)
    GET    /fact/history         - full history for a key
    DELETE /fact                 - retire (soft-supersede) a key
    GET    /facts                - list facts in scope
    GET    /counts               - {live, history, projects}
    POST   /search               - exact + finder discovery, ranked

Deliberately parallel to SerenMemory's app so the two services feel like
siblings: same auth posture, same conditional-MCP-mount shape, same
public-paths set. What's ABSENT is the tell of what Loci is - no consolidator,
no draft gate, no embedder safe-mode/migration. The left brain is deterministic;
its finder is a derived index it can rebuild from text, so it never needs the
'changing the embedder corrupts recall' guard that the right brain does.
"""
from __future__ import annotations

import asyncio
import time
import logging
from contextlib import asynccontextmanager, AsyncExitStack

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.responses import HTMLResponse

from .config import LociConfig, load_config
from .store import LociStore
from .routes import fact as fact_routes
from .routes import facts as facts_routes
from .routes import search as search_routes

from seren_meninges import get_version
from seren_meninges.updates import updates_payload
from seren_meninges.auth import bearer_auth_middleware
from seren_meninges.viewer import render_from_dir
from seren_sinew.request_log import RequestLoggingMiddleware

# Reported version: the installed wheel's setuptools-scm metadata, falling back
# to the package __version__ for an editable / source checkout. get_version
# never raises - a bad lookup yields the fallback, not a startup crash.
from . import __version__ as _fallback_version
APP_VERSION = get_version("seren-loci", fallback=_fallback_version)
log = logging.getLogger("seren_loci")


def create_app(config: LociConfig | None = None) -> FastAPI:
    cfg = config or load_config()
    # Resolve the bearer token ONCE at startup, not per-request: a keyring
    # lookup on every request would be slow (and could prompt the OS keychain).
    # The middleware closes over this; a restart picks up a rotated secret.
    bearer = cfg.server.resolve_bearer()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # -- Startup --
        app.state.config = cfg
        from seren_sinew.blobs import BlobStore
        from seren_sinew.stores import Store, StoreKeeper, restore_at_startup, snapshot_loop

        # The exhibits facts cite: a folder beside the database, files named
        # by their hash (seren_sinew.blobs). None = this store keeps none.
        keep = (BlobStore(cfg.resolved_figures_dir(), max_bytes=cfg.figures.max_bytes, max_total=cfg.figures.max_total)
                if cfg.figures.enabled else None)

        def declared() -> list[Store]:
            """What Loci keeps, for a snapshot and for a restore. The
            database is required; the figures folder is optional on a
            restore, since a snapshot from before it existed holds none. The
            embedding model beside them is a download: declared, never copied."""
            stores = [Store("facts", "sqlite", str(cfg.resolved_db_path()),
                            "every fact and its history (superseded values included), and the search indexes")]
            if keep is not None:
                stores.append(keep.store("figures", "the exhibits facts cite, named by their hash"))
            stores.append(Store("models", "dir", str(cfg.resolved_model_cache_path()),
                                "the embedding model's downloaded weights", backed_up=False))
            return stores

        # A restore, when the config asks for one (backup.restore_from +
        # restore_reason): into an empty store only, before the store opens.
        # Refused = the service does not start. No route does this.
        if (cfg.backup.restore_from or "").strip():
            restore_at_startup("seren-loci", declared(),
                               cfg.backup.restore_from, cfg.backup.restore_reason,
                               cfg.resolved_backup_dir() / "seren-loci",
                               log=lambda m: log.warning(f"[seren-loci] {m}"))
        # warm_in_background: listen now, on the lexical floor; the embedder
        # (tens of seconds to import) arrives behind the door. See LociStore.
        store = LociStore(cfg, warm_in_background=True, figures=keep)
        app.state.store = store
        log.info(f"[seren-loci] store ready at {cfg.resolved_db_path()}")
        log.info(f"[seren-loci] facts: {store.counts()} | finder: {store.finder_kind} ({store.finder_state})")

        # What this service keeps, and snapshots of it on its own schedule
        # (seren_sinew.stores): the database, and the figures folder beside
        # it, hardlinked from the previous snapshot. The embedding model is
        # a download: declared, never copied.

        def _facts_export() -> dict:
            rows = store.list_facts(include_superseded=True)
            return {"facts.jsonl": [f.model_dump() if hasattr(f, "model_dump") else dict(vars(f)) for f in rows]}

        def _facts_check(restored, manifest, snapshot_dir) -> dict:
            """A rehearsal's look at a restored COPY of the database: the
            same counts counts() makes, read-only, against the manifest and
            the export, and every figure the facts cite looked for in the
            copied folder. (Loci has no purge: a forgotten fact is
            superseded, and its history is in the snapshot like everything
            else.)"""
            import sqlite3
            f = restored["facts"] / cfg.resolved_db_path().name
            con = sqlite3.connect(f"file:{f.as_posix()}?mode=ro", uri=True)
            try:
                live = con.execute("SELECT COUNT(*) FROM facts WHERE superseded_at IS NULL").fetchone()[0]
                total = con.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
                projects = con.execute(
                    "SELECT COUNT(DISTINCT project) FROM facts WHERE superseded_at IS NULL").fetchone()[0]
                try:                                        # a snapshot from before there were figures has no table
                    cited = {r[0] for r in con.execute("SELECT DISTINCT digest FROM figures")}
                    figures = con.execute("SELECT COUNT(*) FROM figures").fetchone()[0]
                except sqlite3.OperationalError:
                    cited, figures = set(), 0
            finally:
                con.close()
            problems = []
            exported = (manifest.get("exports") or {}).get("facts.jsonl")
            if exported is not None and exported != total:
                problems.append(f"the export has {exported} facts, the restored database holds {total}")
            copied = BlobStore(restored["figures"]) if "figures" in restored else None
            lost = [d for d in cited if copied is None or not copied.has(d)]
            if lost:
                problems.append(f"{len(lost)} of the {len(cited)} figure(s) the facts cite are not in the copy")
            return {"counts": {"live": live, "history": total - live, "projects": projects, "figures": figures},
                    "problems": problems}

        app.state.stores = StoreKeeper(
            "seren-loci", declared,
            cfg.resolved_backup_dir(), export=_facts_export, check=_facts_check,
            extra=lambda: {"version": APP_VERSION, "embedder": cfg.storage.embedding_model,
                           "counts": store.counts(), "finder": store.finder_kind},
            keep_daily=cfg.backup.keep_daily, keep_weekly=cfg.backup.keep_weekly, compress=cfg.backup.compress,
            log=lambda m: log.info(f"[seren-loci] {m}")) if cfg.backup.enabled else None
        _snap_task = None
        if app.state.stores is not None and cfg.backup.every_hours > 0:
            import asyncio
            _snap_task = asyncio.create_task(snapshot_loop(lambda: app.state.stores, cfg.backup.every_hours))
            log.info(f"[seren-loci] snapshots every {cfg.backup.every_hours:g}h into {app.state.stores.root}")

        # -- Optional MCP server --
        # Mounted ONLY if the [mcp] extra is installed AND the mcp surface
        # module exists. Same shape as SerenMemory: a missing package (or, for
        # now, a not-yet-written module) falls back to pure-HTTP mode without
        # crashing. When seren_loci.mcp.server lands, this lights up for free.
        try:
            from .mcp.server import mount_mcp_routes
            mcp_server = mount_mcp_routes(app)
        except ImportError as exc:
            mcp_server = None
            print(f"[seren-loci] MCP surface not available; HTTP-only mode ({exc})")
        except Exception as exc:  # noqa: BLE001
            mcp_server = None
            print(f"[seren-loci] MCP mount failed: {exc!r} - continuing without MCP")

        # Enter the MCP session manager's task group if we mounted one (the
        # streamable-HTTP transport needs it; a mounted sub-app's own lifespan
        # doesn't fire under Starlette). AsyncExitStack makes HTTP-only mode a
        # clean no-op.
        async with AsyncExitStack() as _mcp_stack:
            session_manager = getattr(mcp_server, "session_manager", None)
            if session_manager is not None:
                await _mcp_stack.enter_async_context(session_manager.run())
                print("[seren-loci] MCP session manager running")
            yield

        # -- Shutdown --
        if _snap_task is not None:
            _snap_task.cancel()
        try:
            app.state.store.close()
        except Exception:  # noqa: BLE001
            pass
        print("[seren-loci] shut down")

    app = FastAPI(
        title="SerenLoci",
        description="Keyed facts/logic memory for Seren - the left brain.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # -- Bearer auth (shared) --
    # The constant-time compare + public-paths policy lives in SerenMeninges so
    # all three services enforce auth identically; a fix there lands everywhere.
    # Empty token => the middleware mounts but no-ops (trusted-LAN default).
    app.add_middleware(bearer_auth_middleware(bearer))

    # -- Request logging (shared, Sinew) --
    # Added AFTER auth so it sits OUTERMOST (Starlette mounts LIFO): every
    # request is logged including auth-rejected 401s, to stderr + the rotating
    # ~/seren-logs/loci-requests.log. env_prefix=SEREN_LOCI namespaces the knobs
    # (SEREN_LOCI_LOG_LEVEL / _LOG_QUERY).
    app.add_middleware(
        RequestLoggingMiddleware,
        service_name="seren-loci",
        env_prefix="SEREN_LOCI",
    )

    # -- Info routes --
    @app.get("/")
    async def root(request: Request):
        store = request.app.state.store
        return {
            "service": "SerenLoci",
            "version": APP_VERSION,
            "counts": store.counts(),
            "finder": store.finder_kind,
            "finder_state": store.finder_state,
            "updates": await updates_payload(
                getattr(request.app.state, "updates", None),
                distribution="seren-loci", installed=APP_VERSION),
        }

    @app.get("/figures")
    async def figure_gallery(request: Request, include_history: bool = True, limit: int = 200, offset: int = 0):
        """Every figure kept, once each, newest first, with the facts citing
        it: the viewer's Figures hall (20 Oct 2026). The words come back
        here; the bytes stay behind GET /fact/figures/{digest}?project&key,
        which each figure's `via` names - a live fact, since that route only
        serves through one. include_history=false drops what only history
        cites."""
        store = request.app.state.store
        figs = await asyncio.to_thread(store.figure_gallery)
        if not include_history:
            figs = [p for p in figs if p["via"] is not None]
        limit, offset = max(1, min(int(limit), 1000)), max(0, int(offset))
        return {"ok": True, "enabled": store.figures is not None, "count": len(figs),
                "bytes": store.figures.total_bytes() if store.figures is not None else 0,
                "figures": figs[offset:offset + limit]}

    # GET /stores, POST /stores/snapshot, GET /stores/snapshots - behind the
    # same bearer as every other route. The keeper is built at startup.
    from seren_sinew.stores import add_store_routes
    add_store_routes(app, lambda: getattr(app.state, "stores", None))

    @app.get("/health")
    async def health(request: Request):
        store = getattr(request.app.state, "store", None)
        return {"ok": True, "ts": time.time(), "version": APP_VERSION,
                "finder": store.finder_kind if store else None,
                "finder_state": store.finder_state if store else None}

    try:
        from seren_meninges.updates import UpdateChecker
        app.state.updates = UpdateChecker(
            "seren-loci",
            enabled=cfg.updates.enabled,
            index_url=cfg.updates.index_url,
            ttl_seconds=cfg.updates.check_interval_hours * 3600.0,
            allow_prerelease=cfg.updates.allow_prerelease,
            fallback_version=APP_VERSION,
        )
    # Catch EVERYTHING, not just ImportError. This whole feature is cosmetic -
    # seren_meninges/version.py states the contract: a version read must never
    # crash startup. A too-narrow catch here already bit us: cfg.updates was
    # missing, the AttributeError sailed past `except ImportError`, and five
    # services failed to boot on a feature that only draws a badge.
    except Exception as exc:
        app.state.updates = None
        log.info("update checking unavailable (%s)", exc)

    @app.get("/viewer")
    async def viewer():
        html = render_from_dir(
            Path(__file__).parent / "viewer" / "ui",
            title="SerenLoci",
            brand="Seren<b>Loci</b> · Palace of Knowledge",
            subtitle=f"v{APP_VERSION} · one address, one truth",
            accent="#5bc8e8",
        )
        return HTMLResponse(html)

    # The Skyline's 3D library (three.js r159, MIT, vendored so the viewer
    # works on a LAN with no internet; 23 Oct 2026). Under /viewer/, so public
    # like the page itself (Meninges treats /viewer/... as public): it is a
    # library, no data.
    _VENDOR = {"three.min.js"}

    @app.get("/viewer/vendor/{name}")
    async def viewer_vendor(name: str):
        from fastapi.responses import FileResponse
        if name not in _VENDOR:
            raise HTTPException(404, "no such file")
        return FileResponse(Path(__file__).parent / "viewer" / "vendor" / name,
                            media_type="text/javascript", headers={"Cache-Control": "private, max-age=86400"})

    # -- Fact + search routes --
    app.include_router(fact_routes.router)
    app.include_router(facts_routes.router)
    app.include_router(search_routes.router)

    return app
