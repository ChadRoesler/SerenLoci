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

import logging
import os
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


class ServerConfig(BaseModel):
    # Loopback, like every Seren service since seren-meninges 2.3.0. The yaml
    # path goes through the shared `ServerConfig.from_dict` in load_config, so
    # the fallback rule is the family's, not a second copy of it. This default
    # is for a LociConfig built in code with no yaml at all.
    host: str = "127.0.0.1"
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
    # Where sentence-transformers caches downloaded model weights.
    # None / "" -> defaults to <db_path parent>/models/ at runtime (see
    # LociConfig.resolved_model_cache_path). Set explicitly to share weights
    # across services (e.g. point both SerenLoci and SerenMemory at the same
    # dir) or to put them on a larger volume. Overridable via
    # SEREN_LOCI_EMBEDDING_CACHE_PATH. Only created when an embedder is
    # configured; the embedding-free floor never touches this path.
    embedding_cache_path: Optional[str] = None


class TlsConfig(BaseModel):
    # Same corp-proxy escape hatch as SerenMemory. Off by default; opt-in via
    # seren-loci[corp] + tls.trust_system_store: true.
    trust_system_store: bool = False


class UpdatesConfig(BaseModel):
    """\"Is there a newer seren-loci\" checking. Cosmetic, opt-outable.

    Needs seren-meninges[updates]. Without it the check reports
    status="unavailable" rather than silently reading as "you're current" -
    see seren_meninges/updates.py for why that distinction is load-bearing.
    """
    enabled: bool = True
    check_interval_hours: float = 6.0
    index_url: str = "https://pypi.org/pypi/{distribution}/json"
    allow_prerelease: bool = False


class LociConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    tls: TlsConfig = Field(default_factory=TlsConfig)
    updates: UpdatesConfig = Field(default_factory=UpdatesConfig)

    def resolved_db_path(self) -> Path:
        """Expand ~, ensure the parent dir exists, return an absolute Path."""
        p = Path(os.path.expanduser(self.storage.db_path)).resolve()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def resolved_model_cache_path(self) -> Path:
        """Absolute path for sentence-transformers model weights.

        If storage.embedding_cache_path is set, use that (expanded + resolved).
        Otherwise derive from the DB parent so the full data layer lives together:
            ~/.seren-loci/loci.db  ->  ~/.seren-loci/models/

        The directory is created here so _load_embedder never has to care.
        Only called from _build_finder, which already short-circuits when no
        embedder is configured, so the models/ dir is never created on the floor.
        """
        if self.storage.embedding_cache_path:
            p = Path(os.path.expanduser(self.storage.embedding_cache_path)).resolve()
        else:
            p = Path(os.path.expanduser(self.storage.db_path)).resolve().parent / "models"
        p.mkdir(parents=True, exist_ok=True)
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
    if v := env.get("SEREN_LOCI_EMBEDDING_CACHE_PATH"):
        cfg.storage.embedding_cache_path = v
    if v := env.get("SEREN_LOCI_EMBEDDING_MODEL"):
        cfg.storage.embedding_model = v
    if v := env.get("SEREN_LOCI_TRUST_SYSTEM_STORE"):
        cfg.tls.trust_system_store = v.lower() in ("1", "true", "yes", "on")
    # Update checking is cosmetic, so it gets a deploy-time off switch that
    # needs no config file - handy for a systemd unit or a locked-down box
    # that must not make outbound calls.
    if (v := os.getenv("SEREN_LOCI_UPDATES_ENABLED")) is not None:
        cfg.updates.enabled = v.strip().lower() in ("1", "true", "yes", "on")
    return cfg


def load_config(path: Optional[str] = None) -> LociConfig:
    """Load config from YAML (if present) + env overrides. A missing file is
    fine - defaults + env is a valid zero-config dev experience."""
    data: dict[str, Any] = {}

    candidate = path or os.environ.get("SEREN_LOCI_CONFIG") or "seren-loci.yaml"
    cfg_path = Path(os.path.expanduser(candidate))
    if cfg_path.is_file():
        try:
            # encoding= IS NOT OPTIONAL. Without it Python uses the LOCALE
            # codec - cp1252 on Windows - and seren-loci.yaml.sample opens
            # with a `# ═══` banner (U+2550 -> E2 95 90), so byte 0x90 raises
            # UnicodeDecodeError at position 4. Unguarded, that took the
            # service down at STARTUP: copy the sample, run on Windows, and
            # SerenLoci refuses to boot.
            with open(cfg_path, encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as ex:  # noqa: BLE001
            # Lenient parse, matching the rest of the family. LOUD, though -
            # a config that isn't being read is the expensive kind of silence.
            log.warning("could not read %s: %s — using defaults + env", cfg_path, ex)
            data = {}

    data["server"] = _shared_server_block(data.get("server"))
    cfg = LociConfig(**data)
    cfg = _apply_env_overrides(cfg)
    return cfg


def _shared_server_block(raw: Any) -> dict[str, Any]:
    """Normalise the yaml `server:` block through seren-meninges.

    ONE RULE FOR THE WHOLE FAMILY - see SerenMemory's twin of this function.
    Loci keeps its own pydantic ServerConfig for the shape the rest of this
    module is built on, and feeds it the shared library's answer.
    """
    from dataclasses import asdict

    from seren_meninges.config import ServerConfig as SharedServer

    shared = SharedServer.from_dict(raw if isinstance(raw, dict) else {},
                                    default_port=7422)
    return asdict(shared)
