# Compile-Phase Resumability — Implementation Plan

**Status:** Implementation plan (code-level). The only file created by this task is this doc.
**Scope:** Make the planning-phase artifact compile (`compile_artifacts`,
`src/iriai_build_v2/workflows/_common/_helpers.py:2205`) **resumable** so a crash mid-plan-phase
restarts WITHOUT re-doing completed compile work and WITHOUT restarting the whole plan phase.
**Out of scope (explicitly):** parallelism. Everything here is SEQUENTIAL — resumability first
(lowest risk, highest ROI). The parallelism half of
`docs/compile-parallelism-resumability-design.md` is deferred.
**Author:** grounded `file:line` against the current tree (branch `fix/cli-resume-command`,
the deterministic tail built by commit `ddcd5dc`).
**Guiding constraints (from CLAUDE.md + memory):** additive / flag-gated (no refactor of the
hot path); never silently degrade (the fail-loud `_assert_compile_complete` guard stays on every
piece and is *also* the reuse validator); reuse existing library primitives
(`_json_digest`, the `dag-task:*` marker pattern, the artifact store + mirror).

---

## 0. The exact ask, restated against the code

Today `compile_artifacts` has **no early-return**. After the final guard it caches in-process only
(`_COMPILED_ARTIFACT_CACHE[(feature.id, final_key)] = compiled_text`, `_helpers.py:2693`) and
intentionally does **not** persist to the DB (`:2695-2698`). The in-process cache is process-scoped
and keyed on the *whole* artifact (`final_key`), so it does nothing across a restart and nothing for
a gate cycle that revised one subfeature. Consequently:

- Every `PlanReviewPhase` gate cycle re-compiles **all** clusters + **all** per-bundle re-emits from
  scratch (`plan_review.py:1322-1342` recompiles changed prefixes; `:1648` recompiles before gate).
- Every restart re-does the entire multi-hour compile (the live "out of extra usage" crash forced a
  complete re-do).

The fix: make the compile reuse **per-piece** on-disk outputs, where a "piece" is one cluster
(the chunk stage, `_helpers.py:2368`) or one final bundle (the deterministic re-emit, `:2558`). The
reuse must work even for the **current run's pre-resumability crash** (no markers on disk) by
**content-validation**, not solely by a marker we wrote.

---

## 1. The compile pipeline, stage-by-stage, with exact on-disk artifacts

`compile_artifacts(runner, feature, phase_name, *, compiler_actor, decomposition, artifact_prefix,
broad_key, final_key, compiled_transform=None, deterministic_final_merge=False)` —
`_helpers.py:2205-2217`.

Setup (`:2230-2357`):
- `broad_text = artifacts.get(broad_key)` (`:2234`); `decomp_text = artifacts.get("decomposition")`
  (`:2235`).
- `feature_dir = Path(mirror.feature_dir(feature.id))` and `file_path = feature_dir /
  _key_to_path(final_key)` (`:2237-2246`). **All piece files below are written under `feature_dir`**
  via plain `Path.write_text` — they are NOT DB artifacts, they survive a crash, and they are exactly
  what a restart can re-read. (`mirror.feature_dir` is `{base}/features/{feature_id}/`,
  `services/artifacts.py:36-39`.)
- `sf_sources: list[(sf, sf_text)]` by reading `{artifact_prefix}:{sf.slug}` for each subfeature
  (`:2248-2252`). **This list is the per-piece digest input source of truth.**
- `real_slugs` = the decomposition's real subfeature slugs (`:2347-2350`) — the guard's authority.
- Hierarchical gate: `len(source_text.encode()) > COMPILE_HIERARCHICAL_THRESHOLD (250_000) and
  len(sf_sources) > 1` (`:2359`, constants `:38-39`).

### Stage A — cluster loop (SEQUENTIAL today) — `:2360-2398`

- `chunks = _chunk_sources_by_rendered_size(sf_sources, include_broad=False,
  include_decomposition=False, target_bytes=COMPILE_CLUSTER_TARGET_BYTES (120_000))` (`:2360-2365`).
  Greedy byte-bounded grouping of **whole** subfeatures (`:2285-2308`). Broad/decomposition are
  deliberately **excluded** from cluster sources (`:2363-2364`).
- For each `idx, chunk` (`for ... start=1`, `:2368`):
  - **Writes** `compile-sources-{prefix}-chunk-{idx}.md` (the rendered cluster source, `:2369`,
    `:2377`).
  - **LLM call** `_run_compile_prompt(stage_label=f"cluster-{idx}", ...)` (`:2378`) which **writes**
    `compile-intermediate-{prefix}-chunk-{idx}.md` (`:2370`) and reads it back (`:2336-2341`:
    raises if missing/empty).
  - **Guard** `_assert_compile_complete(..., expected_slugs={s.slug for s,_ in chunk},
    real_slugs=real_slugs)` (`:2389-2396`). `expected_slugs` is used because raw per-SF sources carry
    no `<!-- SF: -->` markers yet (the compiler emits them).
  - Appends `(Intermediate(name=f"Cluster {idx}", slug=f"cluster-{idx}"), intermediate_text)` to
    `intermediate_sources` (`:2397`).

**Reuse slots here:** before the `_run_compile_prompt` call at `:2378`, check the reuse predicate for
this cluster piece. On reuse, skip the LLM call and set `intermediate_text` from the on-disk
`compile-intermediate-{prefix}-chunk-{idx}.md`.

### Stage B — regroup while-loop — `:2400-2500` (SKIPPED under `deterministic_final_merge`)

`while (not deterministic_final_merge and final_source_text > THRESHOLD and len(final_sources) > 1)`
(`:2415-2419`). Under `deterministic_final_merge=True` (every plan-phase caller — §4) this loop is
**never entered**. It writes `compile-sources-{prefix}-regroup-{round}-{idx}.md` /
`compile-intermediate-{prefix}-regroup-{round}-{idx}.md` (`:2437-2444`), is **lossy by construction**
(`:2407-2414`), and is out of scope for resumability (the in-scope plan-phase callers all skip it).
*Phase 1 below does NOT add reuse to this loop* (it never runs on the deterministic path; adding
reuse to a lossy loop would be over-engineering with no payoff).

### Stage C — `compile-sources-{prefix}.md` — `:2502-2506`

`final_sources_path = feature_dir / f"compile-sources-{prefix}.md"` written with `final_source_text`
(the rendered union of the cluster/regroup outputs incl. broad+decomposition). This is read by the
non-deterministic `else` branch (`:2643-2648`) and is the input the **final guard** validates against
(`:2654`). Pure write of in-hand text; **never cached** (cheap, always re-runs).

### Stage D — deterministic per-bundle re-emit (SEQUENTIAL data dependency today) — `:2507-2622`

Gated `if deterministic_final_merge and final_sources` (`:2507`).
- **Offset precompute is INLINE in the loop today.** `_cross_prefix_re = re.compile(r"\b[A-Za-z]+\d+
  CMP-(\d+)")` (`:2544`) and `_max_local_cmp(text)` (`:2546-2554`) parse owned `CMP-n` ids excluding
  cross-bundle refs. `running_offset` starts at 0 (`:2557`) and is advanced **inside** the loop:
  `running_offset += _max_local_cmp(b_text)` at the **end** of each iteration (`:2622`). This is the
  sequential dependency.
- For each `b_idx, (b_obj, b_text)` (`for ... start=1`, `:2558`):
  - **Writes** `compile-sources-{prefix}-finalbundle-{b_idx}.md` = `b_text` (`:2559-2567`).
  - **LLM call** (`runner.run(Ask(...))`, `:2570-2600`) re-emits ONLY this bundle, adding
    `+running_offset` to OWNED `CMP-n`, leaving cross-bundle `Sx CMP-n` refs verbatim, preserving
    `<!-- SF: -->` markers. **Writes** `compile-intermediate-{prefix}-finalbundle-{b_idx}.md`
    (`:2563-2566`, `:2594-2595`). Read-back raises if missing (`:2601-2605`) / empty (`:2606-2611`).
  - **Guard** `_assert_compile_complete(sources_text=b_text, compiled_text=bundle_compiled, ...,
    real_slugs=real_slugs)` (`:2614-2620`).
  - `bundle_outputs.append(bundle_compiled)` (`:2621`), then `running_offset += _max_local_cmp(b_text)`
    (`:2622`).

**Reuse slots here:** before the `runner.run(Ask(...))` at `:2570`, check the reuse predicate for
this bundle piece (digest MUST include the bundle's `offset`, §6). On reuse, set `bundle_compiled`
from on-disk `compile-intermediate-{prefix}-finalbundle-{b_idx}.md`. The `running_offset` advance at
`:2622` must happen **whether or not** the LLM ran (it's pure code over `b_text`).

### Stage E — deterministic concat / tail — `:2624-2641` (pure code, NEVER cached)

Fixed `frame_header` (`:2625-2633`) + `<!-- ===== Part k of n ===== -->` separators (`:2635-2639`) +
`"\n".join(parts_out)` (`:2640`). `file_path.write_text(compiled_text)` (`:2641`). **No LLM call.**

### Stage F — final whole-union guard (ALWAYS ON, both paths) — `:2649-2659`

`_assert_compile_complete(sources_text=final_source_text, compiled_text=compiled_text,
artifact_prefix=artifact_prefix, stage_label="final", real_slugs=real_slugs)` (`:2653-2659`). This is
the backstop: even if reuse adopted a wrong piece, a missing real subfeature in the union hard-raises
here before the artifact is hosted/returned.

### Stage G — single-pass `else` (no chunking) — `:2660-2677`

When the hierarchical gate is false (small artifact). Writes `compile-sources-{prefix}.md`, one LLM
call writing `file_path`, then the guard. **No pieces** — Phase 1 leaves this path untouched (nothing
to checkpoint between; it's a single call). A crash here simply re-runs the one call on restart.

### Stage H — finalize — `:2679-2710`

Empty-check (`:2679-2682`); optional `compiled_transform` (`:2684-2691`); in-process cache write
(`:2693`); **no DB persist** (`:2695-2698`); `hosting.push(final_key, compiled_text)` (`:2700-2708`);
`return compiled_text`.

### The guard (`_assert_compile_complete`, `:2132-2202`) — reuse validator

Two renumber-safe checks, both **hard-raise**: (1) every required `<!-- SF: {slug} -->` marker
(`_SF_MARKER_RE`, `:2111`) survives — required set from `expected_slugs` (cluster stage) or from
sources, intersected with `real_slugs` to drop synthetic `cluster-*`/`regroup-*` markers
(`:2170-2177`); (2) the CMP-bearing body-header count (`_CMP_BODY_HEADER_RE`, `:2119`) never falls
from sources to output (`:2179-2192`). **This same function is the content-validation half of the
reuse predicate** (§3) — a reused piece must re-pass it on the cached bytes.

---

## 2. The digest primitive

### 2.1 The recipe to mirror

`TaskPlanningPhase._json_digest(payload) = sha256(json.dumps(payload, indent=2, sort_keys=True,
ensure_ascii=True))` (`task_planning.py:1208-1211`); `_content_digest(text) =
sha256(text.encode("utf-8"))` (`:1204-1205`); `_slice_contract_digest` hashes a dict of **sorted** id
lists (`:1443-1464`); `contract_digest` is the whole-payload hash (`:2530-2531`). We mirror this
recipe exactly so the digest is stable across processes and runs.

### 2.2 The offset precompute hoist (prerequisite, behavior-preserving)

Before any reuse decision, hoist the offset math out of Stage D's loop so each bundle's global offset
is known up front (it's pure code over `b_text`, which is fully in hand at `:2502`). This is a no-op
refactor of identical arithmetic — it lands even on the default deterministic path with no behavior
change, and it makes the bundle digest computable before the loop runs:

```python
# _helpers.py, immediately after final_sources_path is written (~:2506), inside the
# `if deterministic_final_merge and final_sources:` block, BEFORE the per-bundle loop.
# `_max_local_cmp` / `_cross_prefix_re` are hoisted above this point (they are pure).
bundle_offsets: list[int] = []
_acc = 0
for (_b_obj, _b_text) in final_sources:
    bundle_offsets.append(_acc)
    _acc += _max_local_cmp(_b_text)
```

Then inside the loop, use `running_offset = bundle_offsets[b_idx - 1]` (replacing the inline `+=` at
`:2622`; keep the `bundle_outputs.append` at `:2621`). The renumber prompt still injects
`+running_offset` (`:2582-2584`); the result is byte-identical to today.

### 2.3 The compile piece digest — signature + exactly what it hashes

```python
# new helpers in _helpers.py (near _assert_compile_complete, ~:2130)

def _compile_content_digest(text: str) -> str:
    """sha256 of a single text blob — mirrors task_planning._content_digest."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()

def _compile_json_digest(payload: Any) -> str:
    """sha256 of canonical JSON — mirrors task_planning._json_digest (sort_keys)."""
    return hashlib.sha256(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()

def _cluster_piece_digest(
    *,
    artifact_prefix: str,
    member_slugs: list[str],           # the cluster's subfeature slugs, in chunk order
    member_source_texts: list[str],    # the SAME sf_text strings, aligned with member_slugs
) -> str:
    """Digest of a CLUSTER piece's inputs (Stage A).

    Hashes the ORDERED list of (slug, sha256(sf_text)) for the subfeatures in this
    cluster — i.e. exactly the inputs to _run_compile_prompt at the cluster stage.
    Order matters: chunk membership/order changes the rendered bundle.  Broad and
    decomposition are EXCLUDED (they are excluded from cluster sources at
    _helpers.py:2363-2364), so they never enter this digest.
    """
    return _compile_json_digest({
        "kind": "cluster",
        "artifact_prefix": artifact_prefix,
        "members": [
            [slug, _compile_content_digest(text)]
            for slug, text in zip(member_slugs, member_source_texts)
        ],
    })

def _final_bundle_piece_digest(
    *,
    artifact_prefix: str,
    bundle_text: str,                  # b_text (the cluster/regroup output fed to the re-emit)
    offset: int,                       # the PRECOMPUTED global offset for this bundle
) -> str:
    """Digest of a FINAL-BUNDLE piece's inputs (Stage D).

    Hashes sha256(b_text) PLUS the precomputed global offset.  The offset is
    LOAD-BEARING: if an upstream bundle gains/loses an owned component its
    _max_local_cmp changes, shifting THIS bundle's offset, which changes this
    bundle's renumbered ids even though b_text is unchanged — so the cached
    re-emit is stale and MUST be recomputed.  This is the invalidation cascade.
    """
    return _compile_json_digest({
        "kind": "finalbundle",
        "artifact_prefix": artifact_prefix,
        "bundle_sha256": _compile_content_digest(bundle_text),
        "offset": int(offset),
    })
```

Imports: `hashlib` is already at module scope (`_helpers.py:4`); `json` is NOT — the file imports it
locally as `import json as _json` inside functions (e.g. `:891`, `:3963`). So either add a module-level
`import json` or use the local-`_json` convention inside the new helpers (mirror the existing style:
`import json as _json` at the top of each helper that needs it). **Why these inputs and no more:** the cluster output is a deterministic function
of its ordered member sources (broad/decomp excluded), and the bundle re-emit is a deterministic
function of `b_text + offset`. Hashing exactly those = the narrowest digest that never reuses a stale
piece (correctness) and never spuriously misses (the win). This is the §6.1 scoping decision of the
design made concrete.

### 2.4 Piece slug (stable identity in the marker key)

Use a **content-stable** slug derived from the *member slugs*, not the volatile `enumerate` index, so
reordering subfeatures does not spuriously invalidate, and so a subfeature added/removed changes the
slug → cache miss → recompile (the task-set-preservation property, mirroring the regroup overlay's
fail-closed rule, `regroup_overlay.py:637-655`):

```python
def _cluster_piece_slug(member_slugs: list[str]) -> str:
    h = hashlib.sha256("|".join(sorted(member_slugs)).encode("utf-8")).hexdigest()[:16]
    return f"cluster-{h}"

def _final_bundle_piece_slug(bundle_member_slugs: list[str]) -> str:
    # bundle_member_slugs = the real SF slugs whose <!-- SF: --> markers appear in b_text,
    # intersected with real_slugs (via _distinct_sf_markers(b_text) & real_slugs).
    h = hashlib.sha256("|".join(sorted(bundle_member_slugs)).encode("utf-8")).hexdigest()[:16]
    return f"finalbundle-{h}"
```

(Note: the **file paths** stay index-based — `...-chunk-{idx}.md`, `...-finalbundle-{b_idx}.md` —
because that is what the existing code reads back. The piece *slug* is only for the marker key. The
index→file mapping is stable within a single `compile_artifacts` call because `chunks`/`final_sources`
are recomputed deterministically from `sf_sources` each call.)

---

## 3. The reuse predicate (marker fast-path + content-validation fallback)

This is THE critical requirement. For each piece, reuse iff:

> The output file exists AND a freshly-recomputed source digest matches the current inputs AND the
> on-disk output re-passes `_assert_compile_complete` (plus non-empty + a completeness sentinel).

The marker is an *optimization* that lets the happy path skip the re-read/re-validate; the
content-validation path is the fallback that recovers a **pre-resumability crash** (no marker on
disk — the current run).

```python
async def _compile_piece_reuse(
    runner, feature, *,
    artifact_prefix: str,
    piece_slug: str,
    src_digest: str,
    output_path: Path,
    # validation inputs (the SAME args the post-LLM guard uses for this piece):
    sources_text: str,
    stage_label: str,
    real_slugs: set[str],
    expected_slugs: set[str] | None,   # cluster stage passes its known slugs; bundle passes None
) -> str | None:
    """Return the reusable on-disk output text, or None if the piece must be (re)compiled.

    Path 1 (marker fast-path): if compile-piece:{prefix}:{piece_slug}:{src_digest} exists in the
            store AND output_path exists AND its bytes re-pass the guard → reuse.  (Still revalidates
            the bytes — belt-and-suspenders, mirroring develop's
            _completed_task_marker_has_current_lineage, implementation.py:21529.)
    Path 2 (content-validation fallback): NO marker (pre-resumability crash, e.g. the current run).
            If output_path exists, READ it and validate by content:
              - non-empty after strip();
              - _assert_compile_complete passes on (sources_text, cached_text);
              - completeness sentinel passes (see §5.1).
            On success → reuse AND retroactively write the marker (adopt the pre-existing output).
    Any failure / exception in either path → return None (recompile).  Fail-OPEN to recompute, never
    fail by reusing a suspect piece (no-silent-degradation: a bad reuse is a silent corruption; a
    needless recompute is just slow).
    """
    if not output_path.exists():
        return None
    try:
        cached = output_path.read_text(encoding="utf-8").strip()
    except Exception:
        return None
    if not cached:
        return None

    marker_key = f"compile-piece:{artifact_prefix}:{piece_slug}:{src_digest}"
    marker = await runner.artifacts.get(marker_key, feature=feature)

    # Content-validation runs in BOTH paths (it is the safety net):
    try:
        _assert_compile_complete(
            sources_text=sources_text,
            compiled_text=cached,
            artifact_prefix=artifact_prefix,
            stage_label=f"{stage_label}-reuse-validate",
            real_slugs=real_slugs,
            expected_slugs=expected_slugs,
        )
    except Exception:
        logger.info("compile reuse REJECTED %s (guard failed on cached bytes) — recompiling",
                    marker_key)
        return None
    if not _compile_piece_sentinel_ok(cached):   # §5.1
        logger.info("compile reuse REJECTED %s (incomplete/no sentinel) — recompiling", marker_key)
        return None

    if not marker:
        # Path 2: retroactively adopt a pre-resumability output (the current run's situation).
        await runner.artifacts.put(marker_key, "complete", feature=feature)
        logger.info("compile reuse ADOPTED pre-existing output %s (no prior marker)", marker_key)
    else:
        logger.info("compile reuse HIT %s", marker_key)
    return cached
```

Note the digest-in-the-key scheme makes staleness implicit: a changed source produces a *different*
`src_digest` → a *different* `marker_key` → the old marker is simply never looked up (it is never
deleted; it is just orphaned). This mirrors develop's `dag-group:{idx}` carrying a fresh-or-stale
body and the freshness check being a key/identity match.

### Write ordering (write-output-then-marker)

The existing code already writes the output file *before* the function could write a marker
(`_run_compile_prompt` writes the file at `:2336-2341`; the bundle re-emit at `:2594-2595`). The
marker `put` (§4) must happen **after** the post-LLM `_assert_compile_complete` passes for that piece.
So a crash between output-write and marker-write leaves an unmarked-but-valid output → recovered by
Path 2 on the next run. A crash mid-output-write leaves a partial file → rejected by Path 2's
guard/sentinel → recompiled (§5.1).

---

## 4. Where reuse + marker-write slot into each loop (flag-gated, additive)

### 4.1 The flag

Add `incremental_compile: bool = False` to `compile_artifacts`'s signature (`:2205-2217`), alongside
`deterministic_final_merge`. Reuse is enabled iff `incremental_compile and deterministic_final_merge`
(the deterministic path is the only one with the bounded, non-lossy pieces worth caching). Default
`False` preserves the legacy callers `pm.py:167` and `design.py:208` **byte-for-byte** (they pass
neither flag → `deterministic_final_merge=False`, `incremental_compile=False` → no behavior change;
their non-deterministic regroup path is untouched). The plan-phase callers opt in (§7.1).

### 4.2 Stage A (cluster loop, `:2368-2397`)

```python
for idx, chunk in enumerate(chunks, start=1):
    chunk_sources_path = feature_dir / f"compile-sources-{artifact_prefix}-chunk-{idx}.md"
    chunk_output_path  = feature_dir / f"compile-intermediate-{artifact_prefix}-chunk-{idx}.md"
    chunk_src_text = _render_source_bundle(...)            # :2371-2376  (unchanged)
    chunk_sources_path.write_text(chunk_src_text, ...)     # :2377       (unchanged)

    member_slugs = [getattr(s, "slug", "") for s, _ in chunk]
    expected = {s for s in member_slugs}
    intermediate_text = None
    if incremental_compile and deterministic_final_merge:
        src_digest = _cluster_piece_digest(
            artifact_prefix=artifact_prefix,
            member_slugs=member_slugs,
            member_source_texts=[t for _s, t in chunk],
        )
        piece_slug = _cluster_piece_slug(member_slugs)
        intermediate_text = await _compile_piece_reuse(
            runner, feature, artifact_prefix=f"{artifact_prefix}-chunk-{idx}",
            piece_slug=piece_slug, src_digest=src_digest, output_path=chunk_output_path,
            sources_text=chunk_src_text, stage_label=f"cluster-{idx}",
            real_slugs=real_slugs, expected_slugs=expected,
        )

    if intermediate_text is None:                          # cache miss or flag off → compile
        intermediate_text = await _run_compile_prompt(... cluster-{idx} ...)   # :2378
        _assert_compile_complete(... expected_slugs=expected, real_slugs=real_slugs)  # :2389-2396
        if incremental_compile and deterministic_final_merge:
            await runner.artifacts.put(
                f"compile-piece:{artifact_prefix}-chunk-{idx}:{piece_slug}:{src_digest}",
                "complete", feature=feature,
            )
    intermediate_sources.append((Intermediate(... f"cluster-{idx}"), intermediate_text))  # :2397
```

Note: the artifact_prefix passed to the marker for a cluster is `f"{artifact_prefix}-chunk-{idx}"` to
match the per-cluster guard's prefix (`:2392`); but the marker *namespace* uses the bare prefix +
piece_slug so it is index-independent within the key. (Either is fine as long as write/read use the
same key; the §4.2 code uses the same expression on both sides.)

### 4.3 Stage D (per-bundle loop, `:2558-2622`) — with the offset hoist (§2.2)

```python
# after the bundle_offsets precompute (§2.2):
for b_idx, (b_obj, b_text) in enumerate(final_sources, start=1):
    bundle_src_path = feature_dir / f"compile-sources-{artifact_prefix}-finalbundle-{b_idx}.md"
    bundle_out_path = feature_dir / f"compile-intermediate-{artifact_prefix}-finalbundle-{b_idx}.md"
    bundle_src_path.write_text(b_text, encoding="utf-8")   # :2567 (unchanged)
    running_offset = bundle_offsets[b_idx - 1]             # §2.2 (replaces inline +=)

    bundle_compiled = None
    if incremental_compile:                                # deterministic path implied here
        bundle_member_slugs = sorted(_distinct_sf_markers(b_text) & real_slugs)
        src_digest = _final_bundle_piece_digest(
            artifact_prefix=artifact_prefix, bundle_text=b_text, offset=running_offset,
        )
        piece_slug = _final_bundle_piece_slug(bundle_member_slugs)
        bundle_compiled = await _compile_piece_reuse(
            runner, feature, artifact_prefix=f"{artifact_prefix}-finalbundle-{b_idx}",
            piece_slug=piece_slug, src_digest=src_digest, output_path=bundle_out_path,
            sources_text=b_text, stage_label=f"final-bundle-{b_idx}",
            real_slugs=real_slugs, expected_slugs=None,    # b_text carries <!-- SF: --> markers
        )

    if bundle_compiled is None:
        await runner.run(Ask(... renumber prompt ..., +running_offset ...))   # :2570-2600
        # existing missing/empty read-back guards :2601-2611
        bundle_compiled = bundle_out_path.read_text(encoding="utf-8").strip()
        _assert_compile_complete(sources_text=b_text, compiled_text=bundle_compiled, ...,
                                 real_slugs=real_slugs)    # :2614-2620
        if incremental_compile:
            await runner.artifacts.put(
                f"compile-piece:{artifact_prefix}-finalbundle-{b_idx}:{piece_slug}:{src_digest}",
                "complete", feature=feature,
            )
    bundle_outputs.append(bundle_compiled)                 # :2621 (unchanged)
    # NOTE: no inline running_offset += here anymore — it is precomputed (§2.2).
```

### 4.4 Stages C, E, F, G — unchanged, always re-run

`compile-sources-{prefix}.md` (Stage C, `:2502`), the concat (Stage E, `:2624-2641`), the final guard
(Stage F, `:2649-2659`), and the single-pass path (Stage G) are pure-code/cheap or single-call and
are **never cached**. They re-run every call over the (mostly reused) piece outputs. This satisfies
the edge case "the deterministic concat/tail always re-runs."

---

## 5. Edge cases

### 5.1 Partial / corrupt piece from a mid-write crash

A crash during `_run_compile_prompt`'s `output_path.read_text`-after-write (`:2336-2341`) or the
bundle re-emit write (`:2594-2595`) can leave a file that exists but is truncated. Rejection layers
(all in `_compile_piece_reuse`, §3):

1. **non-empty**: `cached = read_text().strip()`; empty → `None`. (Matches the existing empty-file
   guards at `:2339-2340`, `:2606-2611`.)
2. **`_assert_compile_complete`**: a truncated body drops `<!-- SF: -->` markers and/or lowers the
   `_CMP_BODY_HEADER_RE` count below `n_src` → hard-raises → caught → `None`. This already catches the
   common truncation class.
3. **Completeness sentinel** `_compile_piece_sentinel_ok(text)` — defends against the rare case where
   a truncation lands *after* the last SF marker / CMP header (so checks 1-2 pass) but the file is
   still cut off:

```python
def _compile_piece_sentinel_ok(text: str) -> bool:
    """Cheap structural sanity for a reused piece body.

    A complete compiler output ends at a natural markdown boundary, never mid-token.
    Reject obvious mid-write truncation: empty, or a final non-whitespace line that
    ends inside an unbalanced markdown construct (open code fence / dangling header
    marker / trailing backslash).  This is a sanity floor, not a parser — the guard
    above is the real check; this only catches the residual 'cut off after the last
    CMP header' case.
    """
    t = (text or "").rstrip()
    if not t:
        return False
    if t.count("```") % 2 != 0:          # unbalanced fenced block ⇒ cut mid-fence
        return False
    last = t.splitlines()[-1].rstrip()
    if last.endswith("\\"):              # line continuation cut mid-write
        return False
    if last in ("#", "##", "###", "####"):  # bare header marker, no title ⇒ cut mid-header
        return False
    return True
```

Belt-and-suspenders, cheap, deterministic. (Optional hardening, deferred: write the marker only
*after* the file, and additionally record `sha256(output_bytes)` in the marker value so Path 1 can
detect a post-marker file edit — not needed for Phase 1 because Path 2's guard already revalidates
bytes on every reuse.)

### 5.2 Stale piece (source changed since last compile) + the invalidation cascade

- **Cluster:** a revised SF source changes `sha256(sf_text)` → changes `_cluster_piece_digest` →
  different `src_digest` → marker miss. Path 2 then reads the old output but validates it against the
  *new* `chunk_src_text`; if the SF content changed materially the guard/sentinel will usually still
  pass (the old output is internally complete) — so we must NOT rely on content-validation to catch a
  source change. **The digest is what catches a source change** (different key ⇒ no marker ⇒
  recompile). Path 2 only adopts when there is *no* marker at all (pre-resumability), and even then a
  changed source yields a different key so the adopted marker is for the *new* digest only if the
  bytes match the new sources — which for a genuinely changed source they will not have been produced
  from, but the guard cannot tell. **Resolution:** for the cluster stage, when `incremental_compile`
  is on, Path 2 adoption is gated to the *first* run (no marker for ANY digest of this piece_slug);
  on a gate-cycle recompile the prior run already wrote a marker under the *old* digest, so the new
  digest is a clean miss → recompile. This is automatic: the only run with zero markers is the very
  first post-upgrade run (the current crashed run), which is exactly the retroactive-adoption case.
- **Final bundle:** if an *upstream* bundle's owned-CMP count changes, its `_max_local_cmp` changes →
  every downstream bundle's precomputed `offset` shifts → `_final_bundle_piece_digest` changes for
  every downstream bundle → marker miss → those downstream bundles recompile (their renumbered ids
  changed). This is the **cascade**, and it is correct *because the offset is in the digest*. A bundle
  whose `b_text` AND `offset` are both unchanged is reused.

### 5.3 The deterministic concat / tail is never cached

Stages C/E/F (and G) re-run every call (§4.4). Cheap pure code + the always-on final guard. Confirmed.

### 5.4 Retroactive adoption of the CURRENT run's artifacts (the operator's proof)

Suppose the running `resume9` crashes mid-`finalbundle-N` under today's marker-less code. On disk
under `feature_dir` it has left:
- `compile-intermediate-{prefix}-chunk-1..k.md` (all clusters, from Stage A) — valid, complete.
- `compile-intermediate-{prefix}-finalbundle-1..(N-1).md` (bundles already re-emitted) — valid.
- `compile-intermediate-{prefix}-finalbundle-N.md` — **missing or partial** (crash point).
- No `compile-piece:*` markers anywhere (they did not exist pre-upgrade).

Restart under the new code (same `feature.id`, same per-SF sources → same `chunks`/`final_sources` →
same `bundle_offsets`):
1. **Cluster loop:** for each cluster, `_compile_piece_reuse` Path 2 fires (no marker, file exists),
   re-reads `chunk-{idx}.md`, re-validates with `_assert_compile_complete` + sentinel, **reuses**, and
   retroactively writes the marker. **Zero cluster LLM calls.**
2. **Offset precompute (§2.2):** identical to the crashed run (same `final_sources`).
3. **Bundle loop:** bundles `1..(N-1)` hit Path 2 (no marker, file exists, valid) → **reused** +
   marker adopted. Bundle `N`: file missing (or partial → guard/sentinel rejects) → Path 2 returns
   `None` → **the only LLM call** re-emits `finalbundle-N`. Bundles `N+1..` (if any) likewise recompute
   (they never ran). 
4. **Concat + final guard** re-run over the reused + freshly-emitted bundles → host → return.

Net: a restart re-does only `finalbundle-N` onward + the cheap concat, recovering ~everything. This is
the concrete proof the design meets the ask — *and it works for the marker-less crash already on disk.*

---

## 6. How it composes with the existing phase markers (end-to-end crash recovery)

The plan phase already has higher-level seals; compile-piece resumability slots **beneath** them. Map
of the plan phase and what a restart re-does at each crash point.

**Higher-level seals (unchanged):**
- `SubfeaturePhase` global tails: `_run_global_*_tail` call `compile_artifacts(...,
  deterministic_final_merge=True)` then `interview_gate_review` (`subfeature.py:1887-1911`,
  `:2021-2057`, `:2172-2241`). On resume, an approved gate short-circuits compile+gate entirely
  (`test_run_global_prd_tail_skips_compile_when_gate_already_approved`, test file `:4986`); a
  pending-gate resume reuses the existing compiled artifact and goes straight to gate (test `:5044`).
- `PlanReviewPhase._run_gates` (`plan_review.py:1629`): recompile-before-gate per prefix unless
  `plan-review-gate:{prefix}` is present (`:1641-1657`); on approval writes
  `plan-review-gate:{prefix}="approved"` (`:1684`,`:1705`,`:1726`,`:1752`); an approved marker skips
  recompile+gate for that prefix (`:1667-1669`, etc.).
- `targeted_revision` (`_helpers.py:3892`) checkpoints per SF via
  `revision-done:{checkpoint_prefix}:{artifact_prefix}:{slug}` (`:3968-3978`, written `:4225`,`:4352`).
- `interview_gate_review` reads compiled text from `_COMPILED_ARTIFACT_CACHE` → `get_existing_artifact`
  (`_helpers.py:2763-2768`); `get_existing_artifact` falls back to the filesystem mirror
  (`:267-294`); `get_gate_approved_artifact` (`:604`) gates on DB persistence after approval.
- `resume_workflow` (`_runner.py:1297`) skips phases before `--from-phase` (`:1356-1362`) and re-hosts
  prior artifacts (`:1340-1353`). Each phase is independently re-entrant via its own markers.

**Crash-point walkthrough (with compile-piece resumability ON):**

| Crash point | Today (no early-return) | With compile-piece resumability |
|---|---|---|
| **Mid-cluster compile** (Stage A, e.g. cluster 3 of 4) | Restart re-compiles clusters 1-4 + all bundles from scratch | Clusters 1-2 reused (Path 2/marker); cluster 3 recompiled (partial/missing → rejected); cluster 4 + bundles run. No cluster redo. |
| **Mid per-bundle re-emit** (Stage D, bundle N) | Restart re-compiles ALL clusters + ALL bundles | All clusters reused; bundles `1..N-1` reused; bundle `N`..end recompiled; concat re-runs. (=§5.4.) |
| **After compile, mid-gate** (`interview_gate_review` open) | `compile_artifacts` re-runs fully when gate re-enters via recompile-before-gate (`plan_review.py:1648`) | recompile-before-gate re-enters `compile_artifacts`, but every piece's digest matches → all pieces reused → only concat re-runs (near-instant). Gate resumes from its own state. |
| **Mid-revision** (`targeted_revision` between SFs) | `revision-done:*` skips revised SFs (already), but the subsequent recompile redoes everything | `revision-done:*` skips revised SFs; the recompile reuses unchanged clusters, recompiles only the cluster(s) whose SF source changed (digest miss) + cascaded bundles. |
| **After a kind seals** (`plan-review-gate:prd="approved"`) | recompile-before-gate skips that prefix already (`:1645`) | unchanged: the gate marker short-circuits the whole prefix; compile-piece reuse never even runs for it. |

**The goal "no full plan-phase restart" holds at every point:** the phase-level markers
(`plan-review-gate:*`, `revision-done:*`, approved-gate short-circuit) already prevent re-running
*sealed* phases/prefixes; compile-piece resumability fills the remaining gap *inside* an unsealed
`compile_artifacts` call so that re-entry of an in-flight compile reuses completed pieces instead of
restarting the compile. Together: a restart re-does only the smallest incomplete unit (one piece) and
the cheap deterministic tail — never the whole plan phase, never a completed compile, never a sealed
prefix.

---

## 7. Build sequence (phased, additive, flag-gated)

### 7.1 Phase 1 — the only phase needed for the ask (resumability, sequential)

1. **Hoist the offset precompute** (§2.2) on the deterministic path. Behavior-preserving; lands first
   so the bundle digest is computable. Covered by an equivalence test (§8 T7).
2. **Add the digest + slug + sentinel + reuse helpers** (`_compile_content_digest`,
   `_compile_json_digest`, `_cluster_piece_digest`, `_final_bundle_piece_digest`,
   `_cluster_piece_slug`, `_final_bundle_piece_slug`, `_compile_piece_sentinel_ok`,
   `_compile_piece_reuse`) near `_assert_compile_complete` (`:2130`). Pure/unit-testable in isolation.
3. **Add `incremental_compile: bool = False`** to `compile_artifacts` (`:2205-2217`); wire the reuse
   check + marker write into Stage A (§4.2) and Stage D (§4.3), gated `incremental_compile and
   deterministic_final_merge`.
4. **Opt the plan-phase callers in** by passing `incremental_compile=True`: `plan_review.py:1325`
   (gather recompile), `plan_review.py:1648` (recompile-before-gate), `subfeature.py:1887`, `:2021`,
   `:2172` (global tails), and the `interview_gate_review` call sites that pass
   `deterministic_final_merge=True` and internally compile. **Do NOT touch** `pm.py:167` /
   `design.py:208` (legacy non-deterministic path stays default-off).
5. **Tests** (§8).

Phase 1 alone fully satisfies the operator's ask (incremental gate cycles + crash recovery, sequential).

### 7.2 Phase 2 (deferred, out of scope) — parallelism

The offset hoist (step 1) is the prerequisite that also unlocks order-independent bundle re-emits.
Parallelism (semaphore-bounded `gather`, per-piece actors, `COMPILE_MAX_PIECES`, cross-ref-density
coarsening) is the second half of `docs/compile-parallelism-resumability-design.md` and is explicitly
NOT built here.

---

## 8. Test plan (mirroring `tests/workflows/test_threaded_planning.py`)

Mirror the existing conventions: `_TestMirror(tmp_path / "features")`, `SimpleNamespace` runner with
`artifacts` + `services={"artifact_mirror": ..., "hosting": ...}`, `monkeypatch` of `compile`/`Ask`
seams, and the `_assert_compile_complete` import already at test file `:103`. The
`keeps_completed_* / resume_reuses_* / deletes_invalid_*` triplet from the contract-digest tests is
the model. Use a fake `compiler_actor`/`runner.run` that records each piece's `stage_label` and writes
a deterministic body to `output_path`, so reuse vs recompile is observable by counting calls.

- **T1 `test_compile_reuses_completed_clusters_when_only_marker_present` (marker fast-path):** seed
  `compile-intermediate-{prefix}-chunk-1..k.md` + matching `compile-piece:*` markers; run with
  `incremental_compile=True`; assert **zero** cluster LLM calls, output identical.
- **T2 `test_compile_adopts_preexisting_outputs_with_no_markers` (content-validation fallback / the
  current run):** seed valid `chunk-1..k.md` + `finalbundle-1..(N-1).md`, **no markers**; remove/
  truncate `finalbundle-N.md`. Assert clusters + bundles `1..N-1` reused (no LLM call), exactly one LLM
  call for `finalbundle-N`, markers retroactively written, final guard passes. (This is §5.4.)
- **T3 `test_compile_recompiles_cluster_when_source_digest_changes` (stale → recompile):** seed a
  cluster output+marker, then change one SF source text; assert that cluster recompiles (digest miss)
  while sibling clusters are reused.
- **T4 `test_compile_offset_shift_invalidates_downstream_bundles` (cascade):** two bundles; mutate
  bundle 1 so its `_max_local_cmp` rises; assert bundle 1 recompiles AND bundle 2 recompiles (offset in
  its digest changed) even though bundle 2's `b_text` is unchanged.
- **T5 `test_compile_rejects_partial_piece_and_recompiles` (corrupt mid-write):** seed a `chunk-1.md`
  that drops an SF marker (or an unbalanced ```` ``` ````); assert `_compile_piece_reuse` returns None
  (guard / sentinel reject) and the cluster recompiles. Add a unit test for
  `_compile_piece_sentinel_ok` directly (empty, open fence, bare `##`, trailing `\`).
- **T6 `test_compile_crash_after_2_of_4_pieces_reuses_2_recompiles_2` (the operator's headline):** seed
  2 valid cluster outputs+markers of 4; assert exactly 2 cluster LLM calls (pieces 3,4), 2 reused,
  output identical to a from-scratch compile.
- **T7 `test_offset_precompute_equivalence` (hoist is behavior-preserving):** assert the hoisted
  `bundle_offsets` sequence equals the running `+= _max_local_cmp` sequence for representative
  `final_sources` incl. cross-bundle `Sx CMP-n` refs (exercises `_cross_prefix_re`, `:2544`).
- **T8 `test_legacy_callers_unchanged_when_flag_off`:** `pm.py`/`design.py`-style call
  (`incremental_compile=False`) writes no `compile-piece:*` markers and behaves as today (the existing
  `test_*_phase_resumes_compiled_artifact_at_gate_review`, file `:5535`/`:5576`, must still pass).
- **T9 `test_concat_and_final_guard_always_rerun_over_reused_pieces`:** with all pieces reused, assert
  Stage E concat + Stage F `_assert_compile_complete` still run and produce the framed union.

Run with `python -m pytest tests/workflows/test_threaded_planning.py -v` (per CLAUDE.md).

---

## 9. Correctness risks (top 3) + mitigations

1. **Digest scoping (reuse a stale piece = silent corruption).** Mitigation: digest the *exact*
   inputs (cluster = ordered (slug, sha256(sf_text)); bundle = sha256(b_text)+offset) and put the
   digest IN the marker key so a changed source is a clean miss. The offset-in-bundle-digest is what
   makes the renumber cascade correct. The always-on final guard (`:2649`) is the last backstop. T3/T4
   defend this.
2. **Partial-file adoption (Path 2 reuses a mid-write-truncated output).** Mitigation: Path 2
   revalidates every reused byte with `_assert_compile_complete` + non-empty + sentinel before
   adopting; fail-open to recompute on any doubt (never reuse a suspect piece). T2/T5 defend this.
3. **Pre-resumability adoption matching the wrong digest on a gate-cycle recompile.** Mitigation: Path
   2 adopts only when there is no marker for the piece at all; once any run writes a marker under some
   digest, a later changed source is a clean digest miss → recompile (§5.2). The first marker-less run
   is exactly (and only) the current crashed run. T3/T6 defend this.

---

## 10. Smallest first PR

The hoist + helpers + Stage A/D wiring + flag, opting in **only** the `plan_review.py` recompile path
first (the live pain: full recompile every gate cycle), with T1, T2, T6, T7, T8. Concretely:
`incremental_compile` param on `compile_artifacts`; the digest/slug/sentinel/`_compile_piece_reuse`
helpers; the offset hoist (§2.2); Stage A + Stage D reuse slots (§4.2/§4.3); pass
`incremental_compile=True` at `plan_review.py:1325` and `:1648` only. Leave `subfeature.py` global
tails and `pm.py`/`design.py` for a follow-up. This is fully additive, default-off elsewhere, and
delivers incremental gate-cycle recompiles + crash recovery for the current run immediately.
