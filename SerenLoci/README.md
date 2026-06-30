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
currency **SerenCorpusCallosum** uses to merge left-brain and right-brain results
on one axis instead of comparing cosines to key-hits.

---

## Config

`seren-loci.yaml` (all optional - defaults are a working zero-config dev setup).
Env vars (`SEREN_LOCI_*`) override the file.

```yaml
server:
  host: 0.0.0.0
  port: 7422
  bearer_token: ""        # empty = no auth (trusted LAN)
storage:
  db_path: ~/.seren-loci/loci.db
  embedding_model:        # null = floor (no torch). name one to light the vector finder.
  embedding_device: cpu
tls:
  trust_system_store: false   # true (+ [corp]) for TLS-intercepting corp proxies
```

---

## Where it sits in the constellation

- **SerenMemory** - the right brain. Fuzzy, consolidated, episodic. General-purpose AI memory protocol.
- **SerenLoci** - *this*. The left brain. Keyed facts, deterministic, strict-supersede.
- **SerenCorpusCallosum** - fans a query across both hemispheres and merges on the shared score currency.

Build for the floor, not the ceiling. The Nano is the floor, not the cap. GPL-3.0-or-later. Rip it and win.