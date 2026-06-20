"""
MCP mount tests.

Gated on the `mcp` package. Exercises the real mount path (the three
transport fixes) and the missing-state guard, without wrestling the
streamable-HTTP ASGI transport through a test client.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from fastapi import FastAPI

from seren_loci.config import LociConfig, StorageConfig
from seren_loci.store import LociStore
from seren_loci.mcp.server import mount_mcp_routes, _count_tools


@pytest.fixture
def app_with_store(tmp_path):
    cfg = LociConfig(storage=StorageConfig(
        db_path=str(tmp_path / "loci.db"), embedding_model=None))
    store = LociStore(cfg)
    app = FastAPI()
    app.state.store = store
    app.state.config = cfg
    yield app
    store.close()


def test_mount_requires_store():
    """Mounting before the store/config are on app.state is a hard error - the
    guard against mounting outside the lifespan."""
    app = FastAPI()
    with pytest.raises(RuntimeError):
        mount_mcp_routes(app)


def test_mount_succeeds_and_registers_tools(app_with_store):
    mcp = mount_mcp_routes(app_with_store)
    assert mcp is not None
    # all six tools registered
    assert _count_tools(mcp) == 6


def test_mount_sets_streamable_path_to_root(app_with_store):
    """Bug-1 fix: the sub-app's own path is pushed to root so mount('/mcp')
    resolves to exactly '/mcp', not '/mcp/mcp'."""
    mcp = mount_mcp_routes(app_with_store)
    if hasattr(mcp.settings, "streamable_http_path"):
        assert mcp.settings.streamable_http_path == "/"


def test_mount_attaches_route(app_with_store):
    """The /mcp mount actually lands on the app's routes."""
    mount_mcp_routes(app_with_store)
    paths = [getattr(r, "path", "") for r in app_with_store.routes]
    assert any("/mcp" in p for p in paths)
