"""
Entry point: python -m seren_loci [--config path]   (also the `seren-loci` script)

Boots the FastAPI app with uvicorn using the resolved config.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from .app import create_app
from .config import load_config


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale.

    On Windows the console defaults to a legacy codepage (cp1252), so any
    non-Latin-1 char a service prints - an emoji, a smart quote in a fact's
    why-text, an arrow in a log line - raises UnicodeEncodeError and can take
    down whatever was mid-work. PYTHONUTF8=1 in the service env is the primary
    fix; this is the in-code backstop for the hand-run `python -m seren_loci`
    case. No-op where stdio is already UTF-8.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def _maybe_inject_truststore(cfg, log=print) -> None:
    """If tls.trust_system_store is on, route Python TLS through the OS trust
    store via `truststore`. MUST run before any SSLContext is created (before
    the optional embedder pulls a model over TLS on a corp-proxied box). Gated
    + logged so it's never silent; a missing truststore tells the operator what
    to install instead of dying with an opaque ImportError."""
    if not cfg.tls.trust_system_store:
        return
    try:
        import truststore
    except ImportError:
        log("[seren-loci] tls.trust_system_store is ON but 'truststore' isn't "
            "installed. Install the corp extra: pip install 'seren-loci[corp]' "
            "(continuing with certifi defaults).")
        return
    truststore.inject_into_ssl()
    log("[seren-loci] TLS: using OS trust store (truststore injected)")


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren_loci",
        description="SerenLoci - keyed facts/logic memory. The left brain.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-loci.yaml (default: ./seren-loci.yaml or "
             "$SEREN_LOCI_CONFIG, falling back to built-in defaults).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    # Before create_app: building the store may construct the optional embedder,
    # which can pull a model over TLS. On a corp-proxied box the trust store has
    # to be injected first or that download fails with CERTIFICATE_VERIFY_FAILED.
    _maybe_inject_truststore(cfg)
    app = create_app(cfg)

    print(f"[seren-loci] listening on {cfg.server.host}:{cfg.server.port}")
    print(f"[seren-loci] auth: "
          f"{'enabled' if cfg.server.bearer_token else 'DISABLED (no token)'}")

    uvicorn.run(app, host=cfg.server.host, port=cfg.server.port, log_level="info")


if __name__ == "__main__":
    main()
