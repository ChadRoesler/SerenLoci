"""
seren_loci.config
════════════════════════════════════════════════════════════════════════

Loads seren-loci.yaml into a typed config object. Same pattern as
SerenMemory: defaults -> yaml -> env (later wins). Deliberately parallel so
the two services feel like siblings to anyone operating both.

Resolution order (later wins):
    1. Defaults (this file)
    2. seren-loci.yaml (path from --config or ./seren-loci.yaml)
    3. Environment variables (SEREN_LOCI_*)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    # Neighbor convention: memory 7420, margin 7421, loci 7422. No cute
    # base-36 derivation - just the next free port in the family.
    port: int = 7422
    bearer_token: str = ""   # empty = no auth (dev / trusted LAN)


class StorageConfig(BaseModel):
    # The WHOLE left brain is one sqlite file. No server, no daemon, no second
    # process. Nano-floor by construction: this runs on the 4GB laptop that
    # started all of this.
    db_path: str = "~/.seren-loci/loci.db"

    # OPTIONAL associative finder.
    #   None / "" -> the store runs EMBEDDING-FREE: exact-key + FTS5 lexical
    #                only. Zero vector deps, no torch, the cheapest floor.
    #   a model   -> a sqlite-vec finder is built over the facts so the
    #                "this smells like that CUDA thing" associative jump works.
    #
    # Additive, not load-bearing: the floor needs no embedder; naming one is
    # the ceiling. Same structural-opt-in spirit as Margin's "don't install" -
    # the capability arrives by config presence, not a feature flag you have to
    # remember to turn off. sqlite-vec / sentence-transformers are only
    # imported when this is set, so the dep-free path never touches them.
    embedding_model: Optional[str] = None
    embedding_device: str = "cpu"


class TlsConfig(BaseModel):
    # Same corp-proxy escape hatch as SerenMemory. Off by default; opt-in via
    # seren-loci[corp] + tls.trust_system_store: true.
    trust_system_store: bool = False


class LociConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tls: TlsConfig = Field(default_factory=TlsConfig)

    def resolved_db_path(self) -> Path:
        """Expand ~, ensure the parent dir exists, return an absolute Path."""
        p = Path(os.path.expanduser(self.storage.db_path)).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p


def _apply_env_overrides(cfg: LociConfig) -> LociConfig:
    env = os.environ
    if v := env.get("SEREN_LOCI_PORT"):
        cfg.server.port = int(v)
    if v := env.get("SEREN_LOCI_HOST"):
        cfg.server.host = v
    if v := env.get("SEREN_LOCI_BEARER_TOKEN"):
        cfg.server.bearer_token = v
    if v := env.get("SEREN_LOCI_DB_PATH"):
        cfg.storage.db_path = v
    if v := env.get("SEREN_LOCI_EMBEDDING_MODEL"):
        cfg.storage.embedding_model = v
    if v := env.get("SEREN_LOCI_TRUST_SYSTEM_STORE"):
        cfg.tls.trust_system_store = v.lower() in ("1", "true", "yes", "on")
    return cfg


def load_config(path: Optional[str] = None) -> LociConfig:
    """Load config from YAML (if present) + env overrides. A missing file is
    fine - defaults + env is a valid zero-config dev experience."""
    data: dict[str, Any] = {}

    candidate = path or os.environ.get("SEREN_LOCI_CONFIG") or "seren-loci.yaml"
    cfg_path = Path(os.path.expanduser(candidate))
    if cfg_path.is_file():
        with open(cfg_path) as f:
            data = yaml.safe_load(f) or {}

    cfg = LociConfig(**data)
    cfg = _apply_env_overrides(cfg)
    return cfg
