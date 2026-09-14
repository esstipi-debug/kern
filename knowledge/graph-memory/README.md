# graph-memory/ — cross-session memory for the code graph

The **persistent** half of the graphify feedback loop. The SessionStart hook keeps
the code graph (`graphify-out/`) fresh, but that directory is gitignored and
rebuilt every session — so anything worth remembering across sessions lives
**here**, versioned.

Each `*.md` file is one answered structural question about this codebase, with a
signal (`useful` / `dead_end` / `corrected`) and the graph nodes it came from.
`graphify reflect` aggregates them into a deterministic lessons doc that an agent
reads at session start: **[`documentation/GRAPH_LESSONS.md`](../../documentation/GRAPH_LESSONS.md)**.

## The loop

```bash
# 1. You asked the graph something and the answer proved useful (or was a dead
#    end, or you had to correct it). Record it — this is what persists.
graphify save-result --memory-dir knowledge/graph-memory \
  --type query --outcome useful \
  --question "How does the orchestrator route a brief to a tool?" \
  --answer  "intent.classify -> registry.get(tool) -> prepare/run/qa/deliver; QA fails => no deliverable." \
  --nodes   "Orchestrator" "Tool" "build_default_registry()"

# 2. Regenerate the lessons doc from all committed memories (deterministic, no LLM).
python scripts/reflect_lessons.py

# 3. Commit the new entry + the regenerated lessons in your PR.
```

`--outcome dead_end` (the path didn't lead anywhere) and `--outcome corrected`
(`--correction "what was actually true"`) are as valuable as `useful`: they stop
the next session from repeating a wrong turn.

## Which graph `reflect` is given

**Use `scripts/reflect_lessons.py`, not `graphify reflect` directly.** `reflect` drops
any `--nodes` entry it cannot resolve in the graph you pass it — that is how it prunes
lessons about code that no longer exists, and it is correct. But since the
capacity-gaps source (#28) landed, the memory store spans *two* graphs, so neither one
alone is right:

| `--graph` | kept | dropped |
|---|---|---|
| `graphify-out/graph.json` | code lessons | all 8 capacity gaps |
| `knowledge/scm-books/graph.json` | the capacity gaps | code lessons |

The script merges the two and reflects against the union, so nothing is lost. Its
`--check` mode fails if the committed `GRAPH_LESSONS.md` has drifted from the memories
it claims to summarize.

One consequence for how you write an entry: **reference nodes by LABEL, not by id.**
`graphify merge-graphs` re-namespaces ids (`kernbrechas_x` becomes
`knowledge::kernbrechas_x`) but leaves labels untouched, so a label resolves in both
the raw graph and the merged one.

## Conventions

- **Keep entries true and verified.** A memory is an assertion the next agent will
  trust for orientation; verify against the code before saving.
- **Prune stale memories** when the code they describe changes — or mark a fresh
  entry `corrected`. `reflect` half-lifes old signals (30 days) but can't know the
  code moved.
- **Paths:** the store is `knowledge/graph-memory/` (here, committed); the graph is
  `graphify-out/graph.json` (gitignored, rebuilt by the hook). The reflect header
  prints a generic `graphify-out/memory/` string — the real store is this folder.
