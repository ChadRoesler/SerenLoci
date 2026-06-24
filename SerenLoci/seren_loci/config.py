"""
seren_loci.config
════════════════════════════════════════════════════════════════════════

Loads seren-loci.yaml into a typed config object. Same pattern as
SerenMemory: defaults -> yaml -> env (later wins). Deliberately parallel so
the two services feel like siblings to anyone operating both.

Loci keeps its OWN pydantic config classes on purpose: operator-edited yaml
benefits from pydantic's validation, and the config *shape* isn't a shared
contract. What IS shared with SerenMeninges is the security-critical bit -
token resolution (ServerConfig.resolve_bearer -> seren_meninges.resolve_token)
- so "where does the secret come from" is identical across every service.

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
    # Token POINTERS - config holds a pointer to the secret, not (ideally) the
    # secret itself. Pick one; precedence is inline > keyring > env (see
    # resolve_bearer). Empty across all three = no auth (dev / trusted LAN).
    bearer_token: str = ""           # inline literal (escape hatch / tests)
    bearer_token_env: str = ""       # NAME of an env var holding the token
    bearer_token_keyring: str = ""   # "service/username" into the OS keychain

    def resolve_bearer(self) -> str:
        """The token this service requires of callers ("" == open). Resolved
        through SerenMeninges so every Seren service does it identically:
        inline literal, OS keychain, or an env var - first one present wins."""
        from seren_meninges import resolve_token
        return resolve_token(
            inline=self.bearer_token or None,
            keyring_ref=self.bearer_token_keyring or None,
            env_var=self.bearer_token_env or None,
        )


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
    if v := env.get("SEREN_LOCI_BEARER_TOKEN_ENV"):
        cfg.server.bearer_token_env = v
    if v := env.get("SEREN_LOCI_BEARER_TOKEN_KEYRING"):
        cfg.server.bearer_token_keyring = v
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
