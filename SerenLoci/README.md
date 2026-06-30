# SerenLoci

**The left brain.** A keyed facts-and-logic store for the Seren constellation -
addressable, deterministic, exactly one live value per key.

Where [SerenMemory](https://github.com/ChadRoesler/SerenMemory) (the right
brain) holds fuzzy, episodic memory - *"we ground on the embedder migration for
a week, it was a slog"* - Loci holds **facts**:

```
{ project, key, value, why }
```

`camelCase is life`. `braces on a new line in posh`. `GGML_CUDA_NO_VMM=ON must
be set at compile time` - *because the env var isn't honored at runtime on
Jetson*. A locus has an **address**. You go *to* it and get *the* thing - you
don't grope around for something that rhymes.

---

## Why it's shaped like this

**One live value per key, enforced by the database.** Set a new value for a key
and the old one is *superseded* - kept as history, pointed at by the new row,
**never blended**. A vibed-together fact is worse than no fact, so the rule is
strict, and it's not enforced by hope: a `PARTIAL UNIQUE INDEX` makes sqlite
physically refuse a second live row per `(project, key)`.

**Two tiers.** A reserved project of `*` is the *fundamentals* tier -
cross-project truths. A concrete project name (`seren-memory`) is the
per-project tier - the targeted override. Same split as nano/xavier/dgx
prebuilts: the platform-wide truth and the per-board variant.

**The `why` is the point.** A logic store without rationale is just a
dictionary. The value tells you *what*; the why is what stops you re-learning it
the painful way - and it's searchable, so "that CUDA thing" finds a fact whose
*reason* mentions CUDA even when the key doesn't.

---

## The floor is free

Three access rungs, cheapest first:

1. **Exact** - `get_fact(project, key)` returns the live value, deterministically.
   No embedding, no ranking. You know the address, you get the thing.
2. **Lexical** - FTS5 full-text over `(key, value, why)` of the live rows. The
   "I sort of remember the words" path.
3. **Vector** *(additive)* - a [sqlite-vec](https://github.com/asg017/sqlite-vec)
   index for the "this smells like that CUDA thing" associative jump. Built
   **only** when you name an embedder.

Rungs 1 and 2 need nothing but sqlite (stdlib) and the web stack - **no torch,
no GPU, the 4GB-laptop floor**. The vector finder is the ceiling, opted into by
install, never required. *Where does it sit? How much does it need?* - the floor
needs almost nothing.

---

## Install

```bash
pip install seren-loci                 # the floor: exact + FTS5 lexical
pip install seren-loci[vector]         # + the sqlite-vec associative finder (pulls torch)
pip install seren-loci[mcp]            # + the MCP surface (model reaches facts directly)
pip install seren-loci[corp]           # + OS-trust-store TLS for corp-proxied boxes
```

Extras stack: `pip install seren-loci[vector,mcp]`.

## Run

```bash
seren-loci                             # or: python -m seren_loci
seren-loci --config ./seren-loci.yaml
```

Listens on **7422** by default (neighbor convention: memory 7420, margin 7421,
loci 7422).

```bash
# set a fundamental truth
curl -X POST localhost:7422/fact -H 'content-type: application/json' \
  -d '{"key":"posh.brace_style","value":"curly brackets on a new line","why":"readability"}'

# get it back, deterministically
curl 'localhost:7422/fact?key=posh.brace_style'

# discovery search (exact-key first, then the finder)
curl -X POST localhost:7422/search -H 'content-type: application/json' \
  -d '{"query":"cuda runtime"}'
```

---

## API

| Method | Path             | What                                                   |
|--------|------------------|--------------------------------------------------------|
| POST   | `/fact`          | Set/replace a fact (strict supersede). Names the superseded id, or null. |
| GET    | `/fact`          | The live value for `?project=&key=` (project defaults to `*`). 404 if none. |
| GET    | `/fact/history`  | Every value a key has held, newest first.              |
| DELETE | `/fact`          | Retire (soft-supersede) the live value for a key.      |
| GET    | `/facts`         | List facts in scope (`?project=&include_superseded=`). |
| GET    | `/counts`        | `{live, history, projects}`.                           |
| POST   | `/search`        | Exact + finder discovery, ranked.                      |
| GET    | `/` `/health`    | Service info / liveness.                               |

Every search hit carries a normalized **0-1 score** (`exact`->1.0,
`hybrid`->RRF fused into 0..1, `lexical`->bm25 mapped above 0). That's the common
currency **SerenCorpusCallosum** uses to merge left-brain and
right-brain results on one axis instead of comparing cosines to key-hits.

---

## The hybrid finder (additive — the ceiling, not the floor)

When an embedding model *is* configured, rung 3 is no longer a pure vector KNN.
Instead a `_HybridFinder` runs **both** lexical (FTS5) and semantic (vector) search
in parallel, then fuses results with **Reciprocal Rank Fusion (RRF)**:

```
rrf_score(fact) = Σ 1/(k + rank_in_ranker)      # k = 60
                 ranker ∈ {FTS5, vector}
```

This gives better precision than either ranker alone — FTS5 catches exact/keyword
matches with high precision, vector catches semantic matches with high recall,
and RRF boosts documents that rank highly in **both** lists. The old pure-vector
path (`_VectorFinder`) is replaced by `_HybridFinder` whenever an embedder is
present; the floor (no embedder) still uses FTS5 only.

**Scope-aware query embedding.** When a single project scope is provided (and
it isn't the `*` fundamentals tier), the vector query is augmented by prepending
the project name before the raw query string. For example, searching `"rate limiting"`
under project `seren-memory` embeds `"seren-memory: rate limiting"` instead —
nudging the query vector toward the memory-domain cluster so it disambiguates
facts that share wording across projects.

---

## Tests

| Test file | What it covers |
|-----------|----------------|
| `tests/test_store.py` | 18 tests — strict-supersede invariant, exact lookup, project isolation, FTS5 lexical search, scoping, counts, `finder_kind` returns `"lexical"` when no embedder configured |
| `tests/test_routes.py` | 16 tests — HTTP endpoints, bearer auth, search route returns provenance, `GET /` reports service + finder kind |
| `tests/test_mcp_tools.py` | 10 tests — MCP tool surface for set/get/search/forget/list/history |
| `tests/test_mcp_mount.py` | 4 tests — MCP mount attaches tools and route |
| `tests/test_vector_sql.py` | 2 tests — sqlite-vec KNN ordering + delete SQL contract (gated on `sqlite_vec` install) |
| `tests/test_embedder_reconcile.py` | 8 tests — vector index reconcile/rebuild/backfill logic with a stub embedder (gated on `sqlite_vec`) |
| `tests/test_hybrid_finder.py` | 10 tests — RRF fusion boosts docs in both rankers, RRF scores normalised to 0..1, exact still leads, scope-aware query augmentation keeps results in-scope, RRF formula correctness (direct unit test), `finder_kind` returns `"hybrid"` when embedder configured, graceful degradation to lexical if embedder import fails, unscoped search finds all projects |

Run with pytest (sqlite-vec-gated tests need the `[vector]` install or a
PYTHONPATH into the vector venv's site-packages):

```bash
pytest tests/                                    # floor tests (no vector needed)
PYTHONPATH=/path/to/vector-venv/lib/python3.12/site-packages pytest tests/  # all tests
```

---

## Where it sits in the constellation

- **SerenMemory** - the right brain. Fuzzy, consolidated, episodic. General-purpose AI memory protocol.
- **SerenLoci** - *this*. The left brain. Keyed facts, deterministic, strict-supersede.
- **SerenCorpusCallosum** - fans a query across both hemispheres and merges on the shared score currency.

Build for the floor, not the ceiling. The Nano is the floor, not the cap. GPL-3.0-or-later. Rip it and win.