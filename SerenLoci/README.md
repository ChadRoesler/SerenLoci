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

A fact is anything its inhabitant holds as fact - a project's convention, a
decision, a contract between two people, a thought of their own they mean to
keep - not only code. Whoever lives in a Loci keeps it: the store says what it
sees (write notes), and never rewrites a fact on its own.

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

**And so is `why_changed`.** When a value is superseded, retired or moved, the
reason it stopped being the answer is kept on the *old* row
(`set_fact(..., why_changed=...)`, `DELETE /fact?why_changed=`, `move_fact`).
The new value's why stays about the fact, and `fact_history` reads like a log.

---

## Keeping it

The store tells; whoever lives in it decides.

- **Write notes.** `POST /fact` and `set_fact` answer with `notes`: no why, a
  `why_changed` with nothing to replace, a part of the fact running past the
  embedder's window (counted by its own tokenizer), a project name that reads
  as one the store already has (`seren` and `Seren`, `SerenMemory` and
  `seren-memory`). Notes, never refusals: the fact is set either way. There is
  no registry of names - a Loci holds anything, under names of its
  inhabitant's choosing, so the only list to check against is the one already
  there.
- **One claim per key.** A fact holding several claims runs past the window
  and has to be rewritten whole when one of them changes. Keep the *what* and
  the *why* here; leave the *how* (file names, function names, versions) to the
  code and its docs, with the fact pointing there.
- **Move.** `POST /fact/move` / `move_fact` re-addresses a fact - another
  project, another key, or both. The old address is retired with the reason,
  the new one carries the value, why and figures, and history at the new
  address follows the move back (each row keeps the address it was written
  under). Refused onto a live value: nothing is ever blended.

---

## The floor is free

Three access rungs, cheapest first:

1. **Exact** - `get_fact(project, key)` returns the live value, deterministically.
   No embedding, no ranking. You know the address, you get the thing.
2. **Lexical** - FTS5 full-text over `(project, key, value, why)` of the live
   rows. The "I sort of remember the words" path. The project is a column so a
   question naming its subject ("Laionan's title") finds that project's fact
   in a store holding many, even unscoped.
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
| POST   | `/fact`          | Set/replace a fact (strict supersede, `why_changed` kept on the old value). Names the superseded id, or null, and carries `notes`. |
| GET    | `/fact`          | The live value for `?project=&key=` (project defaults to `*`). 404 if none. |
| GET    | `/fact/history`  | Every value a key has held, newest first, following moves back. |
| DELETE | `/fact`          | Retire (soft-supersede) the live value for a key (`?why_changed=`). |
| POST   | `/fact/move`     | Move the live value to `{to_project, to_key}` with `why_changed`. 404 nothing live, 409 target taken. |
| POST   | `/fact/figures`  | Cite an exhibit on the live value (`{project, key, source, caption}`). |
| GET    | `/fact/figures`  | The exhibits the live value cites (`?project=&key=`); `/{digest}` is the bytes. |
| DELETE | `/fact/figures/{digest}` | Take one off the live value.                   |
| POST   | `/fact/cite`     | `{project, key, on_project, on_key, why}`: the fact rests on another. 400 itself, 404 not live. |
| DELETE | `/fact/cite`     | Take a citation back (`?project=&key=&on_project=&on_key=`). |
| GET    | `/fact/citations`| What a fact rests on, what rests on it, what changed under it. |
| POST   | `/fact/still-holds` | Looked at, still holds: clear its recheck (`?project=&key=`). |
| GET    | `/recheck`       | Every fact something it rests on changed under.        |
| GET    | `/city`          | The skyline: a tower per project, floors oldest first, skyways. |
| GET    | `/facts`         | List facts in scope (`?project=&include_superseded=`). |
| GET    | `/counts`        | `{live, history, projects, figures}`.                  |
| POST   | `/search`        | Exact + finder discovery, ranked.                      |
| GET    | `/` `/health`    | Service info / liveness.                               |

Every search hit carries a normalized **0-1 score** (`exact`->1.0,
`hybrid`->RRF fused into 0..1, `lexical`->bm25 mapped above 0). That's the common
currency **SerenCorpusCallosum** uses to merge left-brain and
right-brain results on one axis instead of comparing cosines to key-hits.

---

## Figures: the exhibits a fact cites

A fact can cite a picture - the wiring photo behind "the fan header is the
second one", the screenshot behind "the 409 looks like this" - but **a figure
is never the fact.** The fact stays a sentence with a key, searchable,
supersedable, with a why; the figure is what the sentence points at: "see
figure". So there is no way to put a picture in Loci without saying in words
what it proves. `set_fact(..., keep=[...])` cites exhibits as the value is
set, `keep_with_fact` adds one to the live value, `look_at_fact` hands them
back as image content, `drop_from_fact` takes one off. A superseded value
keeps its own figures in history; the new value cites what it cites
(`figure:<digest>` cites one the store already holds, without a second copy).

The bytes go in a `figures/` folder beside the database under their sha256
and nothing else (`seren_sinew.blobs`); the row (caption, source, type) is
in `loci.db`. Both ride in the same snapshot, the folder hardlinked from the
previous one. Where a figure may be taken **from** is the `figures:` block's
to say: folders on this box and URL prefixes; nothing listed means nothing
can be kept, and the refusal says so.

```yaml
figures:
  sources:
    - ~/seren/stores/workbench/gallery
    - https://
```

---

## What a fact rests on

A fact can **rest on** other facts: it holds *because* they do. "The brief is
optional" rests on "a sleep decides only what is aging"; "the voice corpus
takes any clips" rests on "Seren provides and does not police". `cite_fact`
says so, `uncite_fact` takes it back. It is dependency, never "related to" or
"see also" - a pointer for finding your way belongs in the why's words, and
nothing is ever cited automatically or by similarity, because in a store
about one life everything sits near everything.

Citations are between **addresses**, not rows: a fact stays at one
`(project, key)`, its citations can cross projects, they survive a supersede
on either side, and they follow a move. What they buy: when a fact something
rests on is **superseded or retired**, everything resting on it directly is
marked to recheck. `facts_to_recheck` (`GET /recheck`) lists them with what
changed; `get_fact` and every search hit carry `recheck`; the write's `notes`
say how many it marked. Rewriting the fact, or `still_holds`, clears it. A
new key that replaces an old design without retiring the old one fires
nothing, so a design that changes supersedes or retires its old facts.

The viewer's **City** draws it: a tower per project (`*` at the centre),
a floor per fact stacked in the order its address was first written, a floor
as wide as the times it changed and the facts tied to it, a skyway where one
fact rests on another (lights walk toward the reason), and amber where
something under a fact changed. Its 3D library (three.js r159, MIT) is
vendored in `viewer/vendor/` and served under `/viewer/` like the page, so it
works on a LAN with no internet.

---

## The hybrid finder (additive - the ceiling, not the floor)

When an embedding model *is* configured, rung 3 is no longer a pure vector KNN.
Instead a `_HybridFinder` runs **both** lexical (FTS5) and semantic (vector) search
in parallel, then fuses results with **Reciprocal Rank Fusion (RRF)**:

```
rrf_score(fact) = Σ 1/(k + rank_in_ranker)      # k = 60
                 ranker ∈ {FTS5, vector}
```

This gives better precision than either ranker alone - FTS5 catches exact/keyword
matches with high precision, vector catches semantic matches with high recall,
and RRF boosts documents that rank highly in **both** lists. The old pure-vector
path (`_VectorFinder`) is replaced by `_HybridFinder` whenever an embedder is
present; the floor (no embedder) still uses FTS5 only.

**Two lanes a fact.** The embedder reads a window (MiniLM: the first 256
tokens), and with one joined text a long value pushed the why out of it - on
one real store, 41 facts of 121 had a why no vector held. So a fact holds up to
two vectors: the whole fact (key + value + why, the text it always had) and
key + why, each led by the fact's project (none for `*`), so the vector can
tell one entity's `title` from another's. The KNN answers once per fact, at its
nearer lane. The recipe is
stamped beside the embedder (`vec_recipe`), so a store indexed the old way
rebuilds from text on its next boot - lossless, like a model change. The floor
is untouched: FTS already holds the why as its own column.

**Scope-aware query embedding.** When a single project scope is provided (and
it isn't the `*` fundamentals tier), the vector query is augmented by prepending
the project name before the raw query string. For example, searching `"rate limiting"`
under project `seren-memory` embeds `"seren-memory: rate limiting"` instead -
nudging the query vector toward the memory-domain cluster so it disambiguates
facts that share wording across projects.

---

## Tests

| Test file | What it covers |
|-----------|----------------|
| `tests/test_store.py` | 18 tests - strict-supersede invariant, exact lookup, project isolation, FTS5 lexical search, scoping, counts, `finder_kind` returns `"lexical"` when no embedder configured |
| `tests/test_routes.py` | 16 tests - HTTP endpoints, bearer auth, search route returns provenance, `GET /` reports service + finder kind |
| `tests/test_mcp_tools.py` | 10 tests - MCP tool surface for set/get/search/forget/list/history |
| `tests/test_mcp_mount.py` | 4 tests - MCP mount attaches tools and route |
| `tests/test_vector_sql.py` | 2 tests - sqlite-vec KNN ordering + delete SQL contract (gated on `sqlite_vec` install) |
| `tests/test_embedder_reconcile.py` | 8 tests - vector index reconcile/rebuild/backfill logic with a stub embedder (gated on `sqlite_vec`) |
| `tests/test_hybrid_finder.py` | 10 tests - RRF fusion boosts docs in both rankers, RRF scores normalised to 0..1, exact still leads, scope-aware query augmentation keeps results in-scope, RRF formula correctness (direct unit test), `finder_kind` returns `"hybrid"` when embedder configured, graceful degradation to lexical if embedder import fails, unscoped search finds all projects |
| `tests/test_fts_possessive.py` | 8 tests - a possessive (`Laionan's`, straight or curly) comes off a query word before it is quoted, so a question about someone's anything still finds them |
| `tests/test_keeping.py` | 28 tests - `why_changed` on supersede, forget and search hits; the column arriving on an old store; write notes (no why, a reason with nothing to replace, folding project names); `move_fact` (history follows, refusals, scopes stay strict, figures travel); the routes and MCP tools |
| `tests/test_project_in_text.py` | 5 tests - an unscoped question naming a project finds that project's fact; scoped search unchanged; a moved fact is found under its new project; an FTS table from before the project column is rebuilt once on boot, history included |
| `tests/test_why_lane.py` | 11 tests - a word only the why holds finds its fact behind a long value; every lane moves with the fact; one hit per fact; a one-lane index rebuilds on boot; window notes (gated on `sqlite_vec`) |

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

Build for the floor, not the ceiling. The Nano is the floor, not the cap. AGPL-3.0-or-later. Rip it and win.