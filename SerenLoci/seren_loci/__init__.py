"""
SerenLoci - the left brain. A keyed facts/logic store for the Seren
constellation: addressable, deterministic, one live value per key.

The deterministic spine (exact + FTS5 lexical) runs embedding-free on the
floor. An optional sqlite-vec finder adds the associative jump when an
embedder is configured. Pairs with SerenMemory (the right brain),
both federated by the now-live SerenCorpusCallosum.
"""
from __future__ import annotations

try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 - source checkout without a build
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
