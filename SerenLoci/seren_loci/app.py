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

import time
import logging
from contextlib import asynccontextmanager, AsyncExitStack

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.responses import HTMLResponse

from .config import LociConfig, load_config
from .store import LociStore
from .routes import fact as fact_routes
from .routes import facts as facts_routes
from .routes import search as search_routes

from seren_meninges import get_version
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
        store = LociStore(cfg)
        app.state.store = store
        log.info(f"[seren-loci] store ready at {cfg.resolved_db_path()}")
        log.info(f"[seren-loci] facts: {store.counts()} | finder: {store.finder_kind}")

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
            "updates": await updates_payload(
                getattr(request.app.state, "updates", None),
                distribution="seren-loci", installed=APP_VERSION),
        }

    @app.get("/health")
    async def health():
        return {"ok": True, "ts": time.time()}

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

    # -- Fact + search routes --
    app.include_router(fact_routes.router)
    app.include_router(facts_routes.router)
    app.include_router(search_routes.router)

    return app
