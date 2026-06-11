# Compile-Phase Parallelism + Resumability — Design Proposal

**Status:** Proposal (no implementation). Design-only doc; the only file created is this one.
**Scope:** Speed up the planning-phase artifact compile (`compile_artifacts`,
`src/iriai_build_v2/workflows/_common/_helpers.py:2205`) via (a) **parallelism modeled on
DEVELOP mode** and (b) **per-piece resumability**, while keeping the final "tail
integration" a near-deterministic concat rather than a fragile LLM re-merge.
**Author:** grounded `file:line` against the current tree (branch `fix/cli-resume-command`,
the tail design built by commit `ddcd5dc`).
**Guiding constraints:** additive / flag-gated (no refactor of the hot path);
bounded peak concurrency (the system recently hard-crashed on Claude "out of extra
usage"); fail-loud completeness guard stays on every piece.

---

## 0. Executive summary

- **Recommended parallelization LEVEL: per-CLUSTER (the existing
  `_chunk_sources_by_rendered_size` bundles), NOT per-subfeature.** This is the
  level at which `ddcd5dc`'s deterministic tail already operates: a cluster is a
  byte-bounded group of whole subfeatures, so cross-piece references are mostly
  *internal* to a cluster, the tail reconciles only ~2–3 pieces (Kaya: 2), and the
  global renumber stays a pure-code `running_offset` precompute + concat. Going
  finer (per-subfeature, N=7 pieces for Kaya) multiplies cross-piece `Sx CMP-n`
  references and makes the tail's offset/cross-ref reconciliation the dominant cost
  and risk — exactly the operator's "tail integration gets out of control."
- **Resumability mechanism (3–4 sentences):** Add a per-piece compile checkpoint
  keyed by a **source digest** of that piece's inputs (mirroring
  `contract_digest` / `slice_contract_digest` in `task_planning.py`). Each
  cluster/bundle re-emit writes a `compile-piece:{prefix}:{slug}:{src_digest}`
  marker alongside its output file; on re-entry a piece whose source digest is
  unchanged AND whose cached output passes `_assert_compile_complete` is **reused
  (skipped)**, and only pieces whose source changed are re-compiled. The
  deterministic top-level concat is cheap, so it always re-runs over the (mostly
  cached) piece outputs. This makes gate-cycle recompiles incremental (only the
  revised subfeatures' clusters recompile), makes restarts resume from the last
  completed piece, and lets idle workers pick up the remaining pending pieces when
  concurrency is dialed up on restart.
- **Expected wall-clock win:** The big win is **resumability**, not raw fan-out.
  Today every gate cycle and every restart re-does the *entire* multi-hour compile
  (`compile_artifacts` has no early-return — §1.4). A gate cycle that revises 1 of
  7 subfeatures currently recompiles all clusters; with digest-gated reuse it
  recompiles only the cluster(s) containing that subfeature (~1/k of the LLM
  work). Bounded cluster-level parallelism then collapses the remaining k cluster
  compiles + p per-bundle re-emits from sequential to ~`ceil(k/C)` waves at
  concurrency `C=2–4`, roughly a `min(k, C)`× speedup on the LLM-bound stages
  while the tail stays O(1) LLM calls per bundle. For Kaya (k≈4 clusters, p≈2
  bundles) the practical end-to-end win is dominated by *not recompiling unchanged
  clusters every cycle*.
- **Top 3 risks / decisions:**
  1. **Over-parallelization breaks the tail (operator's explicit worry).** Guardrail:
     keep the integration boundary at the cluster, cap pieces, and add a
     **cross-ref-density check** that *coarsens* clustering (raises
     `COMPILE_CLUSTER_TARGET_BYTES` / merges clusters) when cross-piece `Sx CMP-n`
     density exceeds a threshold — never finer than a whole subfeature per piece.
  2. **Digest scoping correctness.** The piece source digest must cover *exactly*
     the inputs that change the piece's output (the per-SF source texts in the
     cluster + the renumber offset that depends on prior pieces). A too-narrow
     digest reuses a stale piece (silent corruption); a too-broad digest never
     reuses (no win). Decision: digest = ordered per-SF source contents in the
     cluster **plus the precomputed global offset**, so an upstream re-number shift
     correctly invalidates downstream pieces.
  3. **Peak concurrency vs usage caps.** A per-invoke ephemeral CLI client is spun
     up per concurrent `Ask`; the live run already crashed on "out of extra usage."
     Decision: bound with a single `asyncio.Semaphore` defaulting to **2–4** (env-
     overridable), well below develop's `policy_cap` of 4–14, because the compiler
     emits very large outputs and shares the operator's usage pool.

---

## 1. Current-state map (with `file:line`)

### 1.1 The compile entry point and its KIND fan-out

`compile_artifacts(runner, feature, phase_name, *, compiler_actor, decomposition,
artifact_prefix, broad_key, final_key, compiled_transform=None,
deterministic_final_merge=False)` — `_helpers.py:2205-2217`.

- Loads `broad_text` (`broad_key`) and `decomp_text` (`:2234-2235`).
- Resolves the output path via the artifact mirror + `_key_to_path(final_key)`
  (`:2237-2246`).
- Collects per-subfeature sources `sf_sources` by reading
  `{artifact_prefix}:{sf.slug}` for each subfeature (`:2248-2252`).
- Helpers: `_render_source_bundle` (`:2254-2268`, joins broad + decomposition +
  per-SF sections with `\n\n---\n\n`), `_rendered_source_bytes` (`:2270-2283`),
  `_chunk_sources_by_rendered_size` (`:2285-2308`, greedy byte-bounded grouping of
  *whole* subfeatures into clusters), and `_run_compile_prompt` (`:2310-2341`, one
  LLM `Ask` writing a file the function reads back).
- `real_slugs` = the decomposition's real subfeature slugs (`:2347-2350`) — the
  authority for the completeness guard.

**Callers** (the hot paths): `plan_review.py:1325,1648,1681,1702,1723,1748` (all
`deterministic_final_merge=True`), `subfeature.py:1887,2021,2172` (the global tails,
all `deterministic_final_merge=True`, wired by `ddcd5dc`), and the *broad-phase*
`pm.py:167` + `design.py:208` (which still call with the **default**
`deterministic_final_merge=False` — legacy single-LLM final merge). Any flag added
must preserve those default-path callers byte-for-byte.

### 1.2 The chunk/cluster stage (SEQUENTIAL today)

`_helpers.py:2359` gates on
`len(source_text.encode()) > COMPILE_HIERARCHICAL_THRESHOLD and len(sf_sources) > 1`
(`COMPILE_HIERARCHICAL_THRESHOLD = 250_000`, `COMPILE_CLUSTER_TARGET_BYTES = 120_000`
— `:38-39`). When hit, `_chunk_sources_by_rendered_size` groups whole subfeatures
into clusters of ≤120 KB (`:2360-2365`), then:

```
for idx, chunk in enumerate(chunks, start=1):          # _helpers.py:2368  ← SEQUENTIAL
    ... write chunk sources ...
    intermediate_text = await _run_compile_prompt(...)  # :2378  one LLM call per cluster
    _assert_compile_complete(..., expected_slugs={...}, real_slugs=real_slugs)  # :2389
    intermediate_sources.append((Intermediate(name=f"Cluster {idx}", slug=...), ...))  # :2397
```

This `for` loop is the first parallelism target: each cluster compile is
independent (its inputs are a disjoint set of subfeatures; the only shared inputs
are `broad`/`decomposition`, deliberately **excluded** from cluster sources at
`:2363-2364`). The per-cluster guard at `:2389-2396` keyed on `expected_slugs`
(the known input slugs, because raw sources carry no `<!-- SF: -->` markers yet) is
the property that must run on every parallel piece.

### 1.3 The regroup while-loop (skipped under `deterministic_final_merge`)

`_helpers.py:2415-2500`: `while (not deterministic_final_merge and
final_source_text > THRESHOLD and len(final_sources) > 1)` re-chunks the cluster
outputs and LLM-merges each multi-source regroup chunk (`:2436-2492`), passing a
single-source chunk through verbatim (`:2452-2461`) and guarding each multi-source
regroup at `:2473-2479`. Under `deterministic_final_merge=True` this loop is
**skipped entirely** (`ddcd5dc` fix C) — the bounded cluster outputs feed straight
into the deterministic tail. The default path keeps regrouping (now per-round
guarded). This loop is **lossy by construction** (an over-budget LLM merge silently
drops a whole subfeature — observed S3a/S6 drop), which is why the deterministic
path exists and is the future.

### 1.4 The deterministic per-bundle re-emit + concat tail (`ddcd5dc`, the model)

`_helpers.py:2507-2641` (gated `if deterministic_final_merge and final_sources`):

- **Pure-code global offset precompute.** `_max_local_cmp(text)` (`:2546-2554`)
  parses `CMP-n` ids, *excluding* cross-bundle refs matched by
  `_cross_prefix_re = re.compile(r"\b[A-Za-z]+\d+ CMP-(\d+)")` (`:2544`, matches
  `S2 CMP-17`, `S4 CMP-8`). The driver loop maintains
  `running_offset += _max_local_cmp(b_text)` (`:2622`) — the **SEQUENTIAL data
  dependency** that currently forces the bundles to be processed in order.
- **Per-bundle bounded re-emit.** For each final bundle (`for b_idx, (b_obj,
  b_text) in enumerate(final_sources, start=1)` — `:2558`), one LLM `Ask`
  re-emits *only that bundle*, adding `+running_offset` to its OWNED `CMP-n` ids,
  leaving cross-bundle `Sx CMP-n` refs untouched, preserving `<!-- SF: -->`
  markers (prompt `:2570-2600`). Each call is bounded well within the output budget
  — this is what kills the "prompt too long → silent truncation" class.
- **Per-bundle backstop guard** at `:2614-2620`.
- **Deterministic frame + concat** at `:2624-2641`: a fixed header
  (`:2625-2633`) + `<!-- ===== Part k of n ===== -->` separators + the per-bundle
  bodies joined with `"\n".join(...)`. **No LLM call assembles the union.**
- **Final whole-union guard** (always on, both paths) at `:2649-2659`.
- The non-deterministic branch (`else`, `:2642-2648`) is the legacy single LLM
  final merge; the single-pass (no-chunking) branch is `:2660-2677`.

**No resumability today.** After the guard, `compile_artifacts` caches in-process
(`_COMPILED_ARTIFACT_CACHE[(feature.id, final_key)] = compiled_text`, `:2693`,
declared `:40`), intentionally does **not** persist to DB (`:2695-2698`), and hosts
the artifact (`:2700-2708`). There is **no early-return**: every call re-derives
every cluster and every bundle from scratch — confirmed by grep (no
`compile-piece` / `compile-chunk-done` / per-piece markers exist anywhere in
`src/`). The in-process cache is process-scoped and `compiled_key`-keyed (the whole
artifact), so it does nothing for a restart or for a gate cycle that revised one SF.

### 1.5 The gate's recompile-before-review (where resumability pays off most)

`interview_gate_review` (`_helpers.py:2713`) reads the compiled text from cache /
existing artifact (`:2763-2768`). On the plan-review revision path,
`plan_review.py:1322-1342` recompiles **all** changed prefixes in parallel via
`asyncio.gather([compile_artifacts(...) for ...])` with
`deterministic_final_merge=True` — i.e. KIND-level parallelism already exists, but
each `compile_artifacts` call is still an all-from-scratch internal recompile. A
size guard rejects a recompile that shrank >50% (`:1370-1378`). Every gate cycle
pays the full compile again; this is the slowness the resumability design targets.

### 1.6 The completeness guard (must stay on every piece)

`_assert_compile_complete` (`_helpers.py:2132-2202`) hard-raises (never warns) on
truncation, with two renumber-safe checks: (1) every required `<!-- SF: {slug} -->`
provenance marker (`_SF_MARKER_RE`, `:2111`) survives — required set from
`expected_slugs` at the chunk stage or from sources elsewhere, intersected with
`real_slugs` to drop synthetic `cluster-*`/`regroup-*` markers; (2) the CMP-bearing
body-header count (`_CMP_BODY_HEADER_RE = ^#{2,4}.*\bCMP-\d+`, `:2119`) never falls
from sources to output. This is the invariant that makes parallel + cached pieces
*safe*: a reused or independently-compiled piece that lost content fails loud
before it can reach the concat or the store.

---

## 2. DEVELOP-mode parallelism — the model we copy

`_implement_dag` (`implementation.py:20614`) is the template. Its docstring
(`:20620-20623`) states the exact two-level checkpoint model we mirror:

- **Wave/group dispatch.** The DAG is a list of groups (`dag.execution_order`);
  the loop `for g_idx in range(start_group, len(dag.execution_order))`
  (`:20735`) runs one group at a time, and *within* a group dispatches all pending
  tasks **in parallel**: `gathered = await _asyncio.gather(*[_run_task(...) for ...
  in pending_tasks])` (`:22084-22089`). **Concurrency is bounded by the wave size,
  not a semaphore** — the wave size is the conservative `cap` (see below).
- **Two-level checkpoints.** `dag-task:{task_id}` per-task result (written at
  `:10534`, survives mid-group crash) and `dag-group:{group_idx}` group-completion
  marker with commit hash. On resume, completed groups skip
  (`_first_existing_dag_group_idx`, `:2574-2583`; the skip loop `:20735-20807`) and
  completed tasks skip (the per-task short-circuit `:21475-21552`: a `completed`
  `dag-task:*` marker is reused, anything else is appended to `pending_tasks` and
  re-run, `:21552`).
- **Freshness guard.** `_dag_group_checkpoint_is_fresh` (`:24688`) refuses to skip a
  group unless its checkpoint's `task_ids` **exactly match** the current group's
  task ids (`:24704`), its verdict is `approved` (`:24702`), and durable commit /
  gate proof backs it (`:24741-24772`). The whole-DAG identity is a
  `dag_sha256 = sha256(dag.model_dump_json())` (`:20693-20695`, base at
  `:20634-20636`); a stale-or-foreign checkpoint forces a re-run (`:20768-20773`).
- **Adaptive wave sizing — the granularity knob.** `scheduler_sizing.py` computes a
  `conservative_cap`: `policy_cap` from task risk (unknown writes / high-risk
  barriers → 4; backend/multi-repo → 6; isolated UI/doc → 10; test-only/perf → 14
  — `:20-22`), clamped, then `build_candidate_waves` (`:751`) packs tasks into
  waves ≤ cap, *shrinking* a wave on hard barriers / unknown writes /
  shared-path conflicts (`_can_join_wave`, `:926`). **Metrics may only shrink or
  reject a wave, never widen past the cap** (`:31`, `:770-771`). This is the develop
  analog of "how finely do I parallelize" — and the answer there is *bounded by a
  conservative cap, shrunk by integration-risk signals*.
- **Quiesce between waves.** `_maybe_quiesce_before_group_dispatch` (`:19936`)
  optionally checkpoints + pauses at a group boundary, keyed by the prior
  `dag-group:*` marker + `dag_sha256` + next-group task ids (`:19947-19965`). This
  is develop's literal "checkpoint between waves."
- **The regroup overlay — re-wave but NEVER change the task set.**
  `regroup_overlay.py:_validate_regroup_against_base_dag` (`:591`) lets an overlay
  re-order/re-group, but fails closed on any base/overlay mismatch: offset out of
  range (`:602`), base-dag hash mismatch (`:620-628`), original-order mismatch
  (`:630-635`), and crucially **task-set preservation** (`:637-655`,
  `dag_regroup_task_preservation_mismatch` on any missing/extra/duplicate task).
  This is the precise property the compile must inherit: **parallelism may re-wave
  the pieces, but the SET of subfeatures compiled must be exactly the
  decomposition's set** — the same set `real_slugs` already enforces in the guard.

**The "tail integration" analogy.** Develop's tail is the **merge queue**: each
parallel task produces a sandbox patch that must be integrated into trunk in a
deterministic order with global reconciliation (rebase, conflict, contract
validation). Develop bounds that cost by (a) capping wave size and (b) only
enqueuing fully-completed, contract-validated lanes
(`implementation.py:5503-5522`). The compile's tail is the
**`running_offset` + concat** of `ddcd5dc`: each parallel piece produces a
renumbered body that must be integrated into one document with global ID
continuity + cross-ref resolution. **Finer compile parallelism = more merge-queue
lanes = harder tail**, exactly as more develop tasks-per-wave = more lanes to
integrate. The lesson transfers directly: *pick the coarsest parallel unit that
still gives a speedup, and keep the integration deterministic.*

---

## 3. The resumability model we copy — contract-digest re-plan

`TaskPlanningPhase` (`task_planning.py`) is "re-derive only what changed, preserve
the rest, gated by a source digest" — the exact property the compile lacks:

- **Digest primitives.** `_json_digest` = `sha256(json.dumps(payload,
  sort_keys=True))` (`:1208-1211`); `contract_digest` over the whole SF planning
  contract (`:2531`, `:3578`); `slice_contract_digest` over a slice's step /
  requirement / journey / AC / source ids (`_slice_contract_digest`, `:1443-1464`).
- **Per-SF skip on digest match.** `_decompose_workstream` (`:6008-6034`): if
  `dag:{slug}` exists AND the manifest is `complete`, recompute the contract; if
  only `contract_digest` drifted, update the manifest digest and **`continue`**
  (skip the re-derive) — "keep the completed DAG when only the digest changes."
- **Re-plan only what changed.** `_normalize_pending_slice_manifest` (`:1576`)
  computes `reopen_required` per slice from step-id / ownership changes
  (`:1662-1671`); a `completed` slice with a valid fragment that is **not**
  reopen-required is **preserved** (`preserve_completed`, `:1673-1682`); a
  reopen-required slice has its fragment **deleted and re-planned** (`:1684-1692`);
  semantic changes flip `manifest.complete = False` (`:1719-1722`).
- **Deterministic recomposition.** `_build_approved_root_implementation_dag`
  (`:5326`) recomposes the root DAG from the per-SF `dag:{slug}` fragments — only
  touched fragments are re-derived, the root is recomposed deterministically. This
  is the analog of "the tail concat always re-runs cheaply over the cached pieces."

The compile resumability design is this pattern transposed from *DAG slices* to
*compile pieces*: a piece = a cluster (or final bundle); its `src_digest` mirrors
`slice_contract_digest`; "preserve completed" = skip the re-emit and reuse the
cached output file; "delete and re-plan" = re-compile the piece whose source
changed; "recompose deterministically" = the `running_offset` + concat tail.

---

## 4. The granularity decision (the heart of it)

### 4.1 Candidate levels and their tail cost

| Level | Pieces the tail reconciles (Kaya, 7 SF / ~126 CMP) | Cross-piece refs | Tail nature |
|---|---|---|---|
| **Per-subfeature** (max fan-out) | **7** (S1, S2, S3a, S3b, S4, S5, S6) | Every cross-SF `Sx CMP-n` (e.g. S4/S5/S6 → shared `ExternalShell`, S3b→S2 pointer) becomes a *cross-piece* ref the tail must resolve; 7 independent local `CMP-1..M` sequences to renumber into one global sequence | 7-way offset chain + cross-ref reconciliation across all 7 → fragile |
| **Per-cluster** (current chunking) | **~2–4** (Kaya bundles: Bundle-1 = S1/S2/S3a/S3b CMP-1..79; Bundle-2 = S4/S5/S6 CMP-80..126) | Cross-SF refs *within* a cluster are already resolved by the cluster's own LLM merge (they become local); only *between-cluster* refs (e.g. Bundle-2 citing Bundle-1's `S2 CMP-17`) cross a piece boundary | 2–4-way `running_offset` precompute + concat (exactly `ddcd5dc`) → near-deterministic |
| **Per-KIND / whole** (coarsest) | **1** | none | already done sequentially; no intra-KIND parallelism |

**The key observation:** a cluster is a *byte-bounded group of whole subfeatures*
(`_chunk_sources_by_rendered_size`, `:2285`). Because clustering keeps whole
subfeatures together, **most cross-subfeature references are absorbed inside a
cluster's own merge** and never reach the tail. Only references that cross a
*cluster* boundary (a handful — Kaya's Bundle-2→Bundle-1 `Sx CMP-n` citations)
remain as cross-piece refs, and `ddcd5dc` already handles those by *leaving them
untouched as written* (`:2587-2591`) and resolving ID continuity with a pure-code
offset (`:2622`). The tail at this level is already the property we want: a
deterministic concat, not an LLM re-merge.

Per-subfeature is tempting for raw fan-out (7 concurrent compiles), but it
**explodes the tail**: every one of the dozens of cross-SF references that
clustering currently hides becomes a cross-piece reference the tail must keep
consistent, and the offset chain grows from 2–4 links to 7. The Kaya cross-ref
notation is *LLM-authored and inconsistent* (`S2 CMP-17`, `S4 CMP-8`,
`S2 CMP-7/9/17` — documented at `_helpers.py:2521-2526`), and the exclusion regex
already has a known cosmetic gap (it matches `S2`/`S4` but **not** `S3a`/`S3b` —
memory `project_kaya_design_cycle5_bundle2_dropped`). At per-subfeature granularity
that gap multiplies across 7 pieces; at per-cluster granularity S3a/S3b are
*inside* Bundle-1, so the gap is harmless (they're never cross-piece). This is the
operator's "parallelize too granularly and the tail gets out of control," made
concrete.

### 4.2 Recommendation: parallelize at the CLUSTER level

**Recommended level: per-cluster.** Reasons:

1. **The tail stays a near-deterministic concat** — the exact property
   `ddcd5dc`'s per-bundle re-emit already achieves (`:2507-2641`). We add *no new
   tail risk*; we just run the existing per-cluster and per-bundle LLM calls
   concurrently instead of sequentially.
2. **Cross-piece references are minimized.** Whole subfeatures stay together, so
   intra-cluster refs are resolved by the cluster's own merge; only the small set
   of between-cluster refs survive, and those are handled deterministically today.
3. **The integration boundary is natural and contractful.** A cluster boundary is a
   byte budget over whole subfeatures; the cross-cluster contract is exactly the
   `Sx CMP-n` cross-ref convention the deterministic tail already preserves.
4. **It mirrors develop's wave-size choice.** Develop does not parallelize at the
   finest unit (one file); it parallelizes at the *task/wave* level bounded by a
   conservative cap and shrunk by integration risk. The cluster is the compile's
   "task," and `COMPILE_CLUSTER_TARGET_BYTES` is its "cap."

**Cross-boundary refs stay resolvable deterministically** via (a) the pure-code
`running_offset` precompute (now lifted *out* of the per-bundle loop — §5.2 — so
re-emits are order-independent) and (b) the existing bounded per-bundle re-emit
that leaves `Sx CMP-n` refs verbatim. We do **NOT** introduce a full LLM re-merge
at any point on the deterministic path.

**The compile's equivalent of "checkpoint between waves"** is the per-piece
`compile-piece:*` marker (§6): after each cluster/bundle piece completes and passes
its guard, its marker + output file are durable, so a crash or re-gate resumes from
the last completed piece — exactly as `dag-group:*` checkpoints let develop resume
between waves.

### 4.3 The guardrail against over-parallelization (operator's worry)

Two guardrails keep the tail bounded as parallelism rises:

- **Max-pieces cap.** Never split below a whole subfeature per piece (a piece is
  always a non-empty set of whole subfeatures — already true of clusters). Cap the
  total piece count at a small constant (e.g. `COMPILE_MAX_PIECES`, default ~8) so
  the offset chain and cross-ref set can never blow up; if the byte budget would
  produce more pieces, *coarsen* (merge clusters / raise the target bytes) rather
  than shrink below a subfeature.
- **Cross-ref-density coarsening.** Before finalizing the cluster split, measure
  cross-cluster `Sx CMP-n` reference density (count refs whose owning prefix maps
  to a *different* cluster, using the decomposition slug→cluster map). If density
  exceeds a threshold, **merge the offending clusters** (coarsen) so heavily
  cross-referencing subfeatures land in the same piece and their refs become
  intra-cluster (resolved by that cluster's merge). This is the compile analog of
  `_can_join_wave` *shrinking* a develop wave on shared-path conflicts
  (`scheduler_sizing.py:926`) — except here a conflict makes us *coarsen* (fewer,
  bigger pieces) rather than *shrink* (smaller waves), because for the compile the
  integration cost rises with piece *count*, not piece *size*.

---

## 5. The parallelism design

### 5.1 Actor-per-unit sharing the compiler `Role`

Per CLAUDE.md, the same `AgentActor` must not appear in multiple parallel tasks
(validated at runtime), because `session_key = f"{actor.name}:{feature.id}"`
(`_runner.py:829`) would collide. The codebase already solves this by defining
**distinct-named actors sharing one `Role`**: `pm_compiler`, `design_compiler`,
`plan_arch_compiler`, `sysdesign_compiler` all use `compiler_role`
(`roles/__init__.py:299,322,...`), and `_helpers.py:1095` builds
`AgentActor(name=f"summarizer-{sf_slug}", role=summarizer_role)` per unit.

Design: for parallel compile, derive a per-piece actor from the passed
`compiler_actor`:

```python
piece_actor = AgentActor(
    name=f"{compiler_actor.name}-piece-{idx}",   # distinct session_key per piece
    role=compiler_actor.role,                     # SAME compiler Role/prompt/tools
    context_keys=getattr(compiler_actor, "context_keys", []),
)
```

This is purely additive (the sequential path keeps using the single
`compiler_actor`) and respects the parallel-safety invariant.

### 5.2 Make the per-bundle re-emit ORDER-INDEPENDENT (precompute the offset)

Today the tail loop carries the `running_offset += _max_local_cmp(b_text)`
dependency inside the loop (`:2622`), forcing sequential bundles. But
`_max_local_cmp(b_text)` depends only on the **bundle's own source text** (`b_text`,
already in hand before any LLM call). So the offsets can be precomputed in pure code
*up front*:

```python
# pure-code, no LLM, deterministic
offsets, acc = [], 0
for (_obj, b_text) in final_sources:
    offsets.append(acc)
    acc += _max_local_cmp(b_text)
# now every bundle's global offset is known independently of the others
```

With offsets precomputed, the per-bundle re-emits (`:2570-2600`) become fully
independent and can run under `asyncio.gather` in any order; the deterministic
concat (`:2624-2641`) re-imposes the canonical Part-1..n order. This is the single
change that unlocks tail parallelism without touching tail *correctness* (the
offset math is identical, just hoisted).

The same independence already holds for the **cluster** stage (`:2368`): each
cluster's inputs are disjoint whole-subfeature sets with no shared offset, so those
LLM calls are independent today and only need fan-out.

### 5.3 Bounded concurrency

Reuse the codebase's existing fan-out primitive (`asyncio.gather`, as at
`_helpers.py:4362` and `plan_review.py:1323`) but bound it with a single
`asyncio.Semaphore`:

```python
sem = asyncio.Semaphore(_compile_concurrency())   # default 2–4, env-overridable

async def _bounded(coro):
    async with sem:
        return await coro

cluster_outs = await asyncio.gather(*[_bounded(_compile_cluster(i, chunk)) for i, chunk in enumerate(chunks, 1)])
# ... precompute offsets ...
bundle_outs  = await asyncio.gather(*[_bounded(_reemit_bundle(i, b_text, offsets[i-1])) for ...])
```

**Default concurrency 2–4** (not develop's 4–14) because: (a) the compiler emits
very large outputs (per-bundle ~108 KB for Kaya) — each concurrent call is an
expensive ephemeral CLI client; (b) the workflow auths via the operator's Claude
subscription OAuth (same usage pool — memory
`project_kaya_design_cycle5_bundle2_dropped` documents the live "out of extra
usage" crash); (c) bounding is the no-silent-degradation-friendly mitigation —
peak concurrency is capped, never unbounded. The cap is the *upper bound* only;
fewer pieces just run fewer concurrent calls.

### 5.4 The guard stays on every piece, results gathered with `return_exceptions`

`_assert_compile_complete` (`:2132`) must run on **every** parallel piece exactly as
it does sequentially today: per-cluster with `expected_slugs` (`:2389-2396`),
per-bundle backstop (`:2614-2620`), and the always-on final whole-union guard
(`:2649-2659`) after the concat. Use `return_exceptions=True` (as
`plan_review.py:1339` and `_helpers.py:4364` already do) so one piece's guard raise
is surfaced per-piece (which piece, which missing slug) rather than crashing the
whole gather opaquely; then re-raise a composed error naming the failed pieces.
Because each piece is independently guarded, a parallel piece can never silently
poison the concat — the fail-loud invariant is preserved verbatim.

---

## 6. The resumability design (the bigger win)

### 6.1 Per-piece source digest (mirror `slice_contract_digest`)

Define a piece's **source digest** over *exactly* the inputs that determine its
output:

- **Cluster piece** `(prefix, cluster_idx)`: digest of the ordered list of
  `(sf.slug, sha256(sf_text))` pairs in that cluster (the inputs to
  `_run_compile_prompt` at the cluster stage) — mirrors `_slice_contract_digest`
  hashing the sorted id sets. The broad/decomposition are *excluded* from cluster
  sources (`:2363-2364`), so they don't enter the cluster digest.
- **Final-bundle piece** `(prefix, bundle_idx)`: digest of `sha256(b_text)` **plus
  the precomputed global `offset`** for that bundle. Including the offset is
  load-bearing: if an *upstream* bundle gains/loses a component its
  `_max_local_cmp` changes, shifting downstream offsets, which MUST invalidate the
  downstream bundles' cached outputs (their renumbered ids changed) even though
  their `b_text` is unchanged. This is the per-piece analog of `reopen_required`
  catching a step-id/ownership change (`:1662-1671`).

Compute with `sha256(json.dumps(payload, sort_keys=True))` — the established
`_json_digest` recipe (`task_planning.py:1208-1211`). Use a `slug`/`idx` slug in
the marker that is stable across runs (the cluster's sorted member slugs, not the
volatile enumerate index, so reordering subfeatures doesn't spuriously invalidate).

### 6.2 Marker keys + where written / checked

- **Marker key:** `compile-piece:{artifact_prefix}:{piece_slug}:{src_digest}`
  where `piece_slug` is the deterministic slug for the piece (e.g.
  `cluster-<sorted-member-slugs-hash>` or `finalbundle-<member-slugs-hash>`). The
  digest is *in the key* (like develop's `dag-group:{idx}` carrying a fresh-or-stale
  body) so a changed source produces a *different* key — a stale piece is simply
  never looked up, and the guard freshness check is implicit in the key match.
- **Output cache:** the piece's compiled text already lands on the filesystem
  mirror as `compile-intermediate-{prefix}-chunk-{idx}.md` /
  `compile-intermediate-{prefix}-finalbundle-{idx}.md` (`:2370`, `:2565`). Key the
  *cache filename* by the piece slug + digest too (or keep an index marker mapping
  `piece_slug:src_digest → output_path`), so the reused bytes are unambiguous.
- **Write:** after a piece's `_assert_compile_complete` passes, write the
  `compile-piece:*` marker via `runner.artifacts.put(...)` (the same store develop
  uses for `dag-task:*` at `:10534`) AND ensure the output file is on the mirror.
  Both must be durable before the marker is written (write-output-then-marker
  ordering, so a crash between them just re-compiles — never reuses a missing file).
- **Check (re-entry / re-gate / dial-up):** before dispatching a piece, compute its
  `src_digest`, look up `compile-piece:{prefix}:{piece_slug}:{src_digest}`; if
  present AND the cached output file exists AND re-running
  `_assert_compile_complete` on the cached bytes passes (cheap, deterministic —
  the same revalidate-before-skip belt-and-suspenders as develop's
  `_completed_task_marker_has_current_lineage`, `:21529`), **reuse** (skip the LLM
  call). Otherwise compile the piece. This is `preserve_completed` (`:1673`) for the
  compile.

### 6.3 How it composes with the three drivers

1. **Gate-cycle recompiles become incremental.** On the plan-review revision path
   (`plan_review.py:1322-1342`), `targeted_revision` rewrote only the changed
   subfeatures' `{prefix}:{slug}` sources, so only the cluster(s) containing those
   slugs get a new `src_digest`; every other cluster's marker hits and is reused.
   Only the changed cluster(s) re-emit via LLM; the final-bundle pieces re-emit only
   if their bundle's `b_text` or offset changed. The tail concat re-runs cheaply.
   **This directly fixes the "full recompile every gate cycle" slowness** — a
   1-of-7-SF revision recompiles ~1 cluster instead of all 4.
2. **Restarts resume from the last completed piece.** A crash mid-compile (the Kaya
   "out of extra usage" crash at `_helpers.py:2417` is exactly this) leaves the
   completed clusters' `compile-piece:*` markers + output files durable; the restart
   reuses them and continues with the pending pieces — mirroring
   `_first_existing_dag_group_idx` / the per-task short-circuit
   (`implementation.py:2574,21475`).
3. **Parallelism dialable up on restart.** Because reuse is per-piece and the
   pending set is just "pieces with no fresh marker," raising the semaphore on
   restart simply lets more idle workers pick up the remaining pending pieces — no
   coordination needed, identical to develop letting a wider wave cap dispatch more
   pending tasks concurrently.

### 6.4 Task-set preservation (mirror the regroup overlay's fail-closed rule)

The reuse must be **fail-closed against the subfeature set changing**, exactly like
`_validate_regroup_against_base_dag` rejecting a task-set mismatch
(`regroup_overlay.py:637-655`). The compile already has the authority: `real_slugs`
(`:2347-2350`) is the decomposition's exact subfeature set, and the final guard
(`:2649-2659`) requires every real slug to survive. So even if a cluster's piece
slug accidentally matched a stale digest, the **final whole-union guard would catch
a missing subfeature** — the reuse layer can never drop a subfeature without the
always-on guard hard-raising. Additionally, the piece slug being derived from its
*member slugs* means a subfeature added to / removed from the decomposition changes
the cluster membership → changes the piece slug → cache miss → recompile. Reuse is
strictly an optimization under an unchanged-and-guarded invariant.

---

## 7. Tail-integration safety as parallelism rises

- **The tail is bounded by piece COUNT, and piece count is capped.** With the cluster
  level + `COMPILE_MAX_PIECES` cap + cross-ref-density coarsening (§4.3), the tail
  always reconciles a small, bounded number of pieces (Kaya: 2) via the pure-code
  offset + concat — never an LLM union. The offset precompute (§5.2) is exact and
  deterministic regardless of compute order.
- **What breaks if you over-parallelize** (the operator's worry): at per-subfeature
  granularity (N pieces), (a) the offset chain has N links and any drift compounds;
  (b) every cross-SF ref becomes a cross-piece ref the tail must keep consistent,
  and the LLM-authored `Sx CMP-n` notation is inconsistent (and the exclusion regex
  has a known S3a/S3b gap) so the cosmetic-gap surface multiplies; (c) the
  per-piece guard still passes (each tiny piece is internally complete) but the
  *cross-piece* consistency degrades — the failure mode is subtle ID/ref corruption,
  not a loud truncation. The guardrails prevent this by keeping the unit at the
  cluster and coarsening when cross-ref density is high, so heavily-cross-referenced
  subfeatures stay in one piece and their refs are resolved by that piece's own
  merge (intra-cluster, never cross-piece).
- **The guard backstops everything.** `_assert_compile_complete` runs per-cluster
  (`:2389`), per-bundle (`:2614`), and on the final union (`:2649`). A reused or
  parallel-compiled piece that lost content fails loud before the concat or the
  store. This is the same "fail fast, never silently degrade" posture documented in
  the recent fix and in memory (`feedback_no_silent_degradation`).

---

## 8. Honest tradeoffs + phased build sketch

### 8.1 What is reused vs genuinely new

| Component | Reused from | New? |
|---|---|---|
| Per-piece source digest | `slice_contract_digest` / `_json_digest` (`task_planning.py:1443,1208`) | Recipe reused; the *piece-input* digest payload is new |
| Skip-on-digest-match reuse | `preserve_completed` / per-SF skip (`task_planning.py:1673,6008`) | Pattern reused; `compile-piece:*` marker is new |
| Checkpoint markers in the store | `dag-task:*` / `dag-group:*` (`implementation.py:10534,20622`) | Pattern reused; compile marker key is new |
| Bounded parallel dispatch | `asyncio.gather` (`_helpers.py:4362`, `plan_review.py:1323`) + wave cap (`scheduler_sizing.py`) | gather reused; the `asyncio.Semaphore` cap on the compile is new |
| Actor-per-unit sharing a Role | `AgentActor(name=f"...-{slug}", role=...)` (`_helpers.py:1095`; `roles/__init__.py:299`) | Pattern reused; per-piece actor derivation is new |
| Deterministic offset + concat tail | `ddcd5dc` per-bundle re-emit (`_helpers.py:2507-2641`) | Reused as-is; only change is hoisting the offset precompute out of the loop (§5.2) |
| Task-set fail-closed | regroup overlay (`regroup_overlay.py:637`) + final guard (`_helpers.py:2649`) | Property reused; piece-slug-from-members is new |
| Cross-ref-density coarsening | analog of `_can_join_wave` shrink (`scheduler_sizing.py:926`) | New heuristic |

### 8.2 Changes to the hot `compile_artifacts` path (additive / flag-gated)

All behavior is gated behind a new flag (e.g. `incremental_compile: bool = False`,
and reuse the existing `deterministic_final_merge` as the precondition for parallel
tail — parallel tail only makes sense on the deterministic path). Default-False
preserves the `pm.py:167` / `design.py:208` legacy callers byte-for-byte and the
sequential path. The hoisted offset precompute (§5.2) is a no-op refactor of the
existing math (identical results), so it can land even on the default deterministic
path without behavior change. No existing code path is rewritten — new branches are
added alongside, per the no-refactor rule.

### 8.3 Phasing (resumability first — biggest win, lowest risk)

- **Phase 1 — Per-piece resumability (incremental recompile), SEQUENTIAL.** Add the
  `src_digest` + `compile-piece:*` marker + reuse-on-match to the cluster stage and
  the per-bundle tail, *without* changing concurrency. Lowest risk (no new
  parallelism, no actor changes), biggest practical win (gate cycles + restarts stop
  re-doing unchanged pieces). Validates the digest scoping (risk #2) in isolation.
  Tests mirror `test_threaded_planning.py`'s
  `keeps_completed_*_when_only_digest_changes` / `resume_reuses_completed_*` /
  `deletes_invalid_*_and_replans`.
- **Phase 2 — Bounded parallelism.** Hoist the offset precompute (§5.2), derive
  per-piece actors (§5.1), wrap the cluster and per-bundle stages in a
  semaphore-bounded `gather` (§5.3) with `return_exceptions` + composed raise (§5.4).
  Add `COMPILE_MAX_PIECES` + cross-ref-density coarsening (§4.3). Default concurrency
  2–4. Risk: peak usage (#3) — mitigated by the cap.
- **Phase 3 — Dynamic scaling on restart.** Surface the concurrency cap as an env /
  resume flag so a restart can dial it up; idle workers pick up pending pieces with
  no extra coordination (falls out of Phase 1's per-piece pending set + Phase 2's
  bounded gather). Optionally feed observed per-piece wall-clock back into the cap,
  mirroring develop's `SchedulerFeedback` (`scheduler_sizing.py`), but
  *recommend-only, never auto-widen past the configured cap* (the develop rule).

### 8.4 Tradeoffs to accept

- **Digest false-misses** (a benign whitespace change to a source re-compiles its
  cluster) — acceptable; the loss is one cluster's recompile, never correctness.
- **Disk footprint** of cached piece outputs in the mirror — small relative to the
  artifacts already mirrored; can be GC'd on final seal.
- **The win is uneven across KINDs:** `pm.py`/`design.py` broad-phase compiles use
  the legacy non-deterministic path and are out of scope for the parallel tail until
  they adopt `deterministic_final_merge` — they still get Phase-1 cluster-level
  resumability if the flag is wired, but that is a separate decision (keep them
  default-off initially to respect no-refactor).
- **Concurrency is deliberately conservative** (2–4) given the shared usage pool;
  this caps the raw fan-out speedup. The design's primary value is *not redoing
  unchanged work*, which is independent of concurrency.

---

## 9. Pointers (file:line index)

- Compile entry + chunking: `_helpers.py:2205, 2285, 2359-2397`
- Regroup loop (skipped on deterministic): `_helpers.py:2415-2500`
- Deterministic tail (offset + per-bundle re-emit + concat): `_helpers.py:2507-2641`, offset dep at `:2622`
- Completeness guard: `_helpers.py:2132-2202`, markers `:2111,2119`
- No early-return / in-process cache only: `_helpers.py:2693-2698`
- Gate recompile (KIND-parallel gather): `plan_review.py:1322-1342`
- Develop wave dispatch + parallel gather: `implementation.py:20735, 22084`
- Develop checkpoints + resume short-circuit: `implementation.py:10534, 20622, 21475-21552`, freshness `:24688-24704`
- Develop wave sizing (caps): `scheduler_sizing.py:20-31, 751, 926`
- Quiesce between waves: `implementation.py:19936`
- Regroup overlay task-set fail-closed: `regroup_overlay.py:591, 637-655`
- Contract-digest re-plan: `task_planning.py:1208, 1443, 1576, 1662-1692, 1673, 5326, 6008-6034`
- Actor-per-unit / shared Role / session_key: `_helpers.py:1095`, `roles/__init__.py:299`, `_runner.py:829`
- Existing parallel fanout precedents: `subfeature.py:2615`, `_helpers.py:4362`
- Written current-state map: `docs/update-requirements-design.md` §1.6 (`:67-92`)
