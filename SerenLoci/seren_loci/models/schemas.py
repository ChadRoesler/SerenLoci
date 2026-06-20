"""
seren_loci.models.schemas
════════════════════════════════════════════════════════════════════════

The left brain's records.

Where SerenLoci holds fuzzy, episodic memories ("we ground on the embedder
migration for weeks, it was a slog"), Loci holds FACTS - addressable, keyed,
exactly one live value each:

        { project, key, value, why }

The shape that separates this hemisphere from the other: a locus has an
ADDRESS. You go *to* it and get THE thing - you don't grope around for
something that rhymes. Method-of-loci, memory palace. That's why the value is
deterministic and the supersede rule is strict: one live value per key,
old ones kept as history, NEVER blended.

`why` is load-bearing, not decoration. The whole point of a logic store is the
hard-won reason a fact is shaped the way it is ("GGML_CUDA_NO_VMM must be set
at COMPILE time - the env var isn't honored at runtime"). The value tells you
what; the why is what stops you re-learning it the painful way.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# Reserved project scope meaning "true everywhere, not tied to one project".
# The fundamentals tier: "camelCase is life", "braces on a new line in posh",
# "write code like the next reader is a psychopath who knows where you live".
# A concrete project name ("seren-memory") is the per-project tier - the
# targeted variant. Same split as nano/xavier/dgx prebuilts: cross-platform
# truths vs the per-board override.
FUNDAMENTALS = "*"


class Source(str, Enum):
    USER = "user"
    MODEL = "model"
    IMPORT = "import"
    SYSTEM = "system"


def _new_id() -> str:
    return uuid.uuid4().hex


class Fact(BaseModel):
    """One addressable locus: a (project, key) -> value, plus the WHY."""
    id: str = Field(default_factory=_new_id)
    project: str = FUNDAMENTALS
    key: str
    value: str
    why: Optional[str] = None
    source: Source = Source.USER
    created_at: float = Field(default_factory=time.time)

    # Supersede bookkeeping. A LIVE fact has superseded_at = None. When a new
    # value lands for the same (project, key), the old row gets stamped here
    # and a forward link in superseded_by - kept as history, never surfaced by
    # default, never blended into the new value.
    superseded_at: Optional[float] = None
    superseded_by: Optional[str] = None

    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return self.superseded_at is None


class FactWrite(BaseModel):
    """Inbound payload to set/replace a fact. No id/timestamps - the store
    assigns those. project defaults to fundamentals if omitted."""
    project: str = FUNDAMENTALS
    key: str
    value: str
    why: Optional[str] = None
    source: Source = Source.USER
    extra: dict[str, Any] = Field(default_factory=dict)


class SearchRequest(BaseModel):
    query: str
    # None = search every scope (fundamentals + all projects). A concrete
    # project narrows to that project, optionally folding in fundamentals.
    project: Optional[str] = None
    n_results: int = 10
    include_fundamentals: bool = True   # when project set, also fold cross-project truths
    include_superseded: bool = False    # history is off by default


class SearchHit(BaseModel):
    id: str
    project: str
    key: str
    value: str
    why: Optional[str] = None

    # Normalized 0..1 relevance - the SCC common currency. An exact-key hit is
    # 1.0 (it IS the thing). Lexical/vector hits land below it. This is the
    # score the corpus callosum compares against SerenLoci's lane-weighted
    # score, so the merge is apples-to-apples instead of cosine-vs-keyhit.
    score: float
    match_kind: str                     # "exact" | "lexical" | "vector"
    source: Optional[str] = None
    raw_distance: Optional[float] = None  # vector hits only; None otherwise


class SearchResponse(BaseModel):
    query: str
    project: Optional[str]
    hits: list[SearchHit]
    finder: str                         # "vector" | "lexical" - which discovery path served
