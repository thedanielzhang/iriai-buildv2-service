# 13A. Lossless Context And Evidence Completeness — Acceptance Artifact

Authority documents:

- `docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md`
  (the doc-13a specification; lines cited inline below as `doc-13a:NN-MM`).
- `docs/execution-control-plane/IMPLEMENTATION_PROMPT_GOVERNANCE.md`
  § "Slice 13A — Lossless Context And Evidence Completeness (Precondition)"
  + the four acceptance bullets at lines 248-259.
- `docs/execution-control-plane/STATUS.md` — the cheap O(1) restart pointer.
- `docs/execution-control-plane/implementation-journal.md` — full history;
  read the tail for the most recent sub-slice acceptance entries.
- `docs/execution-control-plane/implementation-decisions.jsonl` — typed
  decision rows for every implementer / reviewer / finalizer transition.

This artifact pins the in-progress status of the **post-landing
change-control remediation** named **Slice 13A — Lossless Context And
Evidence Completeness (Precondition)**. It is the documentation-only
deliverable required by **doc-13a § Refactoring Steps step 8** (lines
283-285) — *"Add a 13A acceptance artifact and README index entry
instead of rewriting accepted Slice 00-12 plan docs."* It is APPEND-ONLY
and does not rewrite any accepted Slice 00-12 plan doc.

## Scope

Slice 13A enforces the cross-cutting invariant pinned at
doc-13a:18-23:

> If a component can influence dispatch, verification, merge,
> checkpoint, routing, scheduler feedback, or policy recommendation,
> it must consume exact cited evidence or an exact paged manifest.
> Lossy summaries and previews are display-only.

It is a **post-landing change-control remediation** (doc-13a:11-16) —
it runs after Slices 00-12 have landed and **must not rewrite accepted
slice plans or destabilize an active slice review cycle**.

## Doc-13a § Refactoring Steps — status (as of this artifact)

| Step | Doc-13a citation | Status | Implementing sub-slice |
|------|------------------|--------|------------------------|
| 1 | doc-13a:261-262 — "Re-check implementation status and record a 13A start decision" | **SATISFIED** (the foundational doc-13a itself records the start decision; the first 13A sub-slice journal entry confirms inventory) | Slice 13A 1st sub-slice |
| 2 | doc-13a:263-265 — "Add `completeness.py` under the execution-control package with the shared models above plus digest helpers" | **SATISFIED** | Slice 13A 2nd sub-slice (`src/iriai_build_v2/execution_control/completeness.py`; 575 lines; 7 `__all__` surfaces) |
| 3 | doc-13a:266-268 — "Add compatibility adapters that derive `EvidenceCompleteness` and `AuthoritativeContextRef` from existing Slice 05 prompt-context records without changing accepted Slice 05 interfaces in-place" | **SATISFIED** | Slice 13A 3rd sub-slice (`src/iriai_build_v2/execution_control/prompt_context_adapter.py`; 557 lines; 3 `__all__` surfaces) |
| 4 | doc-13a:269-272 — "Update the prompt/context builder through the 13A adapter so a large prompt emits a compact preview plus exact page refs. If `required_complete_for` cannot be satisfied, dispatch records `runtime_context/context_incomplete` and does not invoke a runtime" | **SATISFIED** | Slice 13A 4th sub-slice (`src/iriai_build_v2/execution_control/dispatcher_prompt_context.py`; 630 lines; 6 `__all__` surfaces) + opt-in port wiring through `src/iriai_build_v2/workflows/develop/execution/dispatcher.py` |
| 5 | doc-13a:273-275 — "Add a 13A gate companion record so model verifier input is either complete for the gate scope or exactly paged. A gate may not approve from `preview_only` evidence after 13A is enabled" | **SATISFIED** | Slice 13A 5th sub-slice (`src/iriai_build_v2/execution_control/gate_companion.py`; 926 lines; 9 `__all__` surfaces) |
| 6 | doc-13a:276-279 — "Replace any deterministic-summary escape hatch in post-13A gates with explicit typed proof rows. A summary can satisfy a required gate only if the proof row states the exact source digest, page refs, proof algorithm, and verification time" | **SATISFIED** | Slice 13A 5th sub-slice (CO-BUNDLED with step 5 via the `AuthoritativeGateProofRow` shape + `derive_proof_row` helper in `gate_companion.py`) |
| 7 | doc-13a:280-282 — "Add a 13A snapshot companion so every list field carries field-level completeness. Partial snapshots are allowed for display but classifier rules fail closed unless their required fields are complete" | **SATISFIED** | Slice 13A 6th sub-slice (`src/iriai_build_v2/execution_control/snapshot_companion.py`; 1176 lines; 9 `__all__` surfaces) |
| 8 | doc-13a:283-285 — "Add a 13A acceptance artifact and README index entry instead of rewriting accepted Slice 00-12 plan docs" | **SATISFIED** (this artifact + the appended README index entry) | Slice 13A 7th sub-slice |
| 9 | doc-13a:285-287 — "Update governance Slices 13-20 and context Slice 21 to depend on this shared completeness model instead of redefining authority semantics locally" | **SATISFIED** (13An-1 appended uniform `## Slice 13A Shared Completeness Model Dependency` sub-section to 9 plan docs; 13An-2 finalizer landed the P3-13A-6-3 binding closure via production-callsite swap at `dashboard.py:1568`) | Slice 13A 8th sub-slice 13An-1 (step 9 reconciliation) + 13An-2 (P3-13A-6-3 binding closure wiring) + 13An-3 (slice-end SIX-VECTOR review) |

## Invariants pinned by Slice 13A

### Doc-13a:18-23 — the cross-cutting invariant

> If a component can influence dispatch, verification, merge,
> checkpoint, routing, scheduler feedback, or policy recommendation,
> it must consume exact cited evidence or an exact paged manifest.
> Lossy summaries and previews are display-only.

**Status**: ENFORCED by the typed shapes in
`src/iriai_build_v2/execution_control/completeness.py` (Slice 13A 2nd
sub-slice) + the adapter / wiring chain through the 3rd-6th sub-slices.
The shared `EvidenceCompleteness` + `AuthoritativeContextRef` types are
the source-of-truth for completeness semantics across dispatcher,
gates, snapshots, and (per step 9 — deferred) the governance/context
slices.

### Doc-13a:111-115 — blocking deviations

Per doc-13a:111-115:

> Blocking deviations:
> - `PromptContextBundle.truncation_notes` is the only indication that
>   task context is incomplete.
> - A verifier, gate, router, merge queue, scheduler, supervisor
>   classifier, or governance recommender acts on a truncated list
>   without fetching exact pages or marking the decision
>   degraded/unknown.
> - A deterministic summary is treated as satisfying required evidence
>   unless it is a typed proof row with a digest and exact page refs
>   back to the source.
> - Provider/runtime output or a compatibility artifact projection
>   becomes execution authority without typed evidence reconciliation.

**Status**: STRUCTURALLY ENFORCED by:

- The Slice 13A 3rd sub-slice `AuthoritativePromptContextBundle` +
  `MissingPromptContextFieldError` adapter shape, which projects
  `EvidenceCompleteness` + `AuthoritativeContextRef` onto the legacy
  `PromptContextBundle` rather than relying on `truncation_notes`.
- The Slice 13A 4th sub-slice dispatcher port that records
  `runtime_context/context_incomplete` and does not invoke a runtime
  when `required_complete_for` cannot be satisfied.
- The Slice 13A 5th sub-slice `AuthoritativeGateProofRow` typed shape
  + `MissingProofRowFieldError`, which is the **only** path by which a
  deterministic summary can satisfy a required gate.
- The Slice 13A 6th sub-slice `AuthoritativeSnapshotListFieldCompleteness`
  + `AuthoritativeSnapshotClassifierRouting` shapes, which carry
  per-list-field completeness and force classifier rules to fail closed
  when their required snapshot fields are incomplete.

**Binding statement (carry)**: the full deviation closure depends on
**P3-13A-6-3** — the composite `LegacyGateConsumerSnapshotAdapter`
chain must be wired into a real gate / verifier / classifier production
consumer site before any Slice 14-19 governance slice can claim gate
execution authority. See § "Carried-P3 ledger" below.

### Doc-13a:280-282 — snapshot classifier fail-closed

> Add a 13A snapshot companion so every list field carries field-level
> completeness. Partial snapshots are allowed for display but
> classifier rules fail closed unless their required fields are
> complete.

**Status**: ENFORCED by the Slice 13A 6th sub-slice
`snapshot_companion.py` module + the 2 NEW typed failure ids
`evidence_corruption/list_field_incomplete` +
`evidence_corruption/classifier_rule_blocked` registered under the
EXISTING `evidence_corruption` failure_class in
`src/iriai_build_v2/workflows/develop/execution/failure_router.py`.
Both route to `quiesce` per doc-13a:280-282.

**Binding statement (carry)**: the runtime fail-closed behavior
depends on **P3-13A-6-3** — the composite adapter chain must be wired
into a real consumer (likely supervisor `classifier.py` or
`public_dashboard.py`) via an external opt-in wrapper. See § "Carried-P3
ledger" below.

## Per-sub-slice module `__all__` projections

| Sub-slice | Module | `__all__` count | Surfaces |
|-----------|--------|-----------------|----------|
| 1st | `src/iriai_build_v2/workflows/develop/governance/completeness_scanner.py` | (re-exports in `governance` package `__all__` count 26) | `scan_governance_completeness` + `CompletenessScanReport` (re-exported through `governance/__init__.py`) |
| 2nd | `src/iriai_build_v2/execution_control/completeness.py` | **7** | `CompletenessState` + `EvidenceAuthority` + `EvidencePageRef` + `EvidenceCompleteness` + `ExactEvidenceManifest` + `AuthoritativeContextRef` + `compute_completeness_digest` |
| 3rd | `src/iriai_build_v2/execution_control/prompt_context_adapter.py` | **3** | `AuthoritativePromptContextBundle` + `MissingPromptContextFieldError` + `derive_authoritative_prompt_context_bundle` |
| 4th | `src/iriai_build_v2/execution_control/dispatcher_prompt_context.py` | **6** | `AuthoritativePromptBuildResult` + `AuthoritativePromptContextRouting` + `AuthoritativePromptBuilderPort` + `derive_dispatch_routing` + `LegacyPromptBuilderAuthoritativeAdapter` + `AuthoritativePromptContextIncompleteSignal` |
| 5th | `src/iriai_build_v2/execution_control/gate_companion.py` (926 lines) | **9** | `AuthoritativeGateCompanionRecord` + `AuthoritativeGateApprovalRouting` + `AuthoritativeGateCompanionPort` + `LegacyGateCompanionAdapter` + `derive_gate_companion` + `AuthoritativeGateProofRow` + `derive_proof_row` + `MissingGateCompanionFieldError` + `MissingProofRowFieldError` |
| 6th | `src/iriai_build_v2/execution_control/snapshot_companion.py` (1176 lines) | **9** | `AuthoritativeSnapshotListFieldCompleteness` + `AuthoritativeSnapshotCompanionRecord` + `AuthoritativeSnapshotClassifierRouting` + `AuthoritativeSnapshotCompanionPort` + `LegacySnapshotCompanionAdapter` + `derive_snapshot_companion` + `LegacyGateConsumerSnapshotAdapter` + `derive_gate_companion_with_snapshot` + `MissingSnapshotCompanionFieldError` |
| 7th | (THIS sub-slice — documentation-only) | n/a | acceptance artifact + README index entry; no module surfaces |

**Re-export discipline**: per doc-13a:42-46 + 124-126 +
`feedback_no_refactor`, **none** of the 5 execution-control 13A
modules (2nd-6th sub-slice) are re-exported from
`src/iriai_build_v2/execution_control/__init__.py` or
`src/iriai_build_v2/workflows/develop/governance/__init__.py`. Only
the Slice 13A 1st sub-slice's `scan_governance_completeness` +
`CompletenessScanReport` are re-exported from the governance package
(per the 1st-sub-slice `__all__` extension 24→26).

## Typed failure ids registered by Slice 13A

All registered in
`src/iriai_build_v2/workflows/develop/execution/failure_router.py` as
NEW pure-data enumerators (no behavior change to existing rows). All
route to `quiesce` per the doc-13a:273-282 fail-closed rule.

| Failure id | Failure_class | Routing | Registered by | Doc citation |
|------------|---------------|---------|---------------|--------------|
| `runtime_context/context_incomplete` | `runtime_context` (existing failure_class; new failure_id) | `quiesce` | Slice 13A 4th sub-slice | doc-13a:269-272 |
| `verifier_context/companion_record_unavailable` | `verifier_context` (existing failure_class; new failure_id) | `quiesce` | Slice 13A 5th sub-slice | doc-13a:273-275 |
| `verifier_context/proof_row_required` | `verifier_context` (existing failure_class; new failure_id) | `quiesce` | Slice 13A 5th sub-slice | doc-13a:276-278 |
| `evidence_corruption/list_field_incomplete` | `evidence_corruption` (existing failure_class) | `quiesce` | Slice 13A 6th sub-slice | doc-13a:280-282 |
| `evidence_corruption/classifier_rule_blocked` | `evidence_corruption` (existing failure_class) | `quiesce` | Slice 13A 6th sub-slice | doc-13a:280-282 |

**Note on the snapshot failure_class choice (P3-13A-6-1 carry)**: the
two snapshot-derived typed failure ids register under the EXISTING
`evidence_corruption` failure_class rather than a dedicated `snapshot`
failure_class. Rationale: a dedicated `snapshot` failure_class would
have required a coverage row in `supervisor/classifier_mapping.py`
(READ-ONLY per the doc-13a:42-46 change-control rule). The
`evidence_corruption` class is semantically close (both signal
structurally incomplete snapshot evidence; both route to `quiesce`).
See P3-13A-6-1 in the carry ledger.

## Carried-P3 ledger (Slice 13A scope; running total)

This ledger tracks the carried-P3 items introduced by Slice 13A
sub-slices. Items downgraded or closed in subsequent sub-slices are
noted inline. The full ledger across the implementation lives in
`STATUS.md` § "Carried-P3 ledger".

| ID | Introduced by | Status | Summary |
|----|---------------|--------|---------|
| **P3-13A-1** | Slice 13A 1st sub-slice finalizer | **CARRIED** | Structural false positives in the governance completeness scanner. 7 descriptive-text mentions of canonical `P[12]-<slice>-<digit>` IDs in narrative prose without same-line status markers. **Binding statement**: future Slice 13A sub-slices MUST design downstream consumers to EITHER (a) ignore `is_complete` and consume `unresolved_findings` / `evidence_gaps` lists directly, OR (b) add journal-section-aware filtering. |
| **P3-13A-5-1** | Slice 13A 5th sub-slice | **CARRIED** | `LegacyGateCompanionAdapter` is a stateless wrapper that simply delegates to `derive_gate_companion`. Kept for symmetry with the 4th sub-slice's `LegacyPromptBuilderAuthoritativeAdapter` pattern (stable opt-in port shape for future wiring). |
| **P3-13A-5-2** | Slice 13A 5th sub-slice | **CARRIED** | `AuthoritativeGateProofRow.proof_metadata: dict[str, Any]` is free-form. Intentionally permissive; future Slice 13A sub-slices (or Slice 17 policy interface) may tighten the shape once algorithm-specific metadata is known. |
| **P3-13A-5-4** | Slice 13A 5th sub-slice; **DOWNGRADED** by Slice 13A 6th sub-slice finalizer | **DOWNGRADED → restated as P3-13A-6-3** | Dead-until-wired binding closure for the fifth-sub-slice `LegacyGateCompanionAdapter` + `derive_gate_companion`. The 6th-sub-slice implementer's CLOSURE claim was OVERSTATED — the composed `LegacyGateConsumerSnapshotAdapter` chain remains dead-until-wired because NEITHER underlying adapter has external production callers. The previous CLOSED claim is hereby DOWNGRADED; P3-13A-5-4 remains OPEN and is superseded / restated as P3-13A-6-3 NEW binding statement (see below). |
| **P3-13A-6-1** | Slice 13A 6th sub-slice | **CARRIED** | The snapshot companion record's 2 NEW typed failure ids (`list_field_incomplete` + `classifier_rule_blocked`) register under the EXISTING `evidence_corruption` failure_class rather than a dedicated `snapshot` failure_class. Pragmatic compromise to honor the MUST-NOT-EDIT-SUPERVISOR-MODULES rule — a new `snapshot` failure_class would have required a coverage row in `supervisor/classifier_mapping.py` (READ-ONLY). A future Slice 13A sub-slice or maintenance pass MAY introduce a dedicated `snapshot` failure_class once the supervisor classifier mapping change-control window opens. |
| **P3-13A-6-2** | Slice 13A 6th sub-slice | **CARRIED** | `LegacySnapshotCompanionAdapter` is a stateless wrapper that simply delegates to `derive_snapshot_companion`. Mirrors P3-13A-5-1 (the 5th-sub-slice `LegacyGateCompanionAdapter` stateless-wrapper carry). Kept for symmetry with the 4th + 5th sub-slices' opt-in port pattern. |
| **P3-13A-6-3** | Slice 13A 6th sub-slice finalizer (reframed from reviewer P2-V-1) | **CARRIED — dead-until-wired binding statement** | See § "Dead-until-wired binding statement" below. |

## Dead-until-wired binding statement (P3-13A-6-3)

**The composite `LegacyGateConsumerSnapshotAdapter` chain in
`src/iriai_build_v2/execution_control/snapshot_companion.py` (Slice
13A 6th sub-slice) composes the 5th-sub-slice
`LegacyGateCompanionAdapter` with the 6th-sub-slice
`LegacySnapshotCompanionAdapter`. As of this acceptance artifact,
NEITHER underlying adapter has external production callers; the
composite chain is dead-until-wired.**

Composition of two dead adapters does NOT constitute production
wiring (the 6th-sub-slice implementer's prior P3-13A-5-4 CLOSURE
claim was OVERSTATED; the reviewer's P2-V-1 correctly identified
this; the 6th-sub-slice finalizer DOWNGRADED the claim and restated
it as this binding statement).

**Binding statement**: a future Slice 13A sub-slice (likely the LAST
— 13An — which also covers doc-13a step 9 + the slice-end six-vector
review) **OR** the Slice 17 policy interface per doc-13a:286-287
**MUST** wire the `LegacyGateConsumerSnapshotAdapter` (or equivalent
composite) into a real gate / verifier / classifier production
consumer site **BEFORE** any Slice 14-19 governance slice can claim
gate execution authority. The wiring closes:

- The **doc-13a:18-23 + 111-115** invariant that gates may NOT
  approve from `preview_only` evidence.
- The **doc-13a:280-282** invariant that classifier rules MUST fail
  closed when their required snapshot fields are incomplete.

The wiring target is likely either the supervisor classifier consumer
site (`src/iriai_build_v2/supervisor/classifier.py`) OR the dashboard
snapshot consumer site (`src/iriai_build_v2/public_dashboard.py`).
Per doc-13a:42-46 + 124-126 + `feedback_no_refactor`, the wiring
**must land as a NEW external opt-in code path** (not an in-place
edit of either accepted Slice 10 module).

## Decision: CO-BUNDLE-VS-SPLIT for P3-13A-6-3 binding closure (this sub-slice)

**Outcome**: **DEFER to the LAST sub-slice (13An).**

Rationale:

- The composite wiring requires (a) a new external opt-in wrapper
  module around `supervisor/classifier.py` OR `public_dashboard.py`,
  (b) a wiring test surface proving the composite is invoked at the
  real call site, (c) a byte-identical legacy-path proof when the
  wiring is OFF. That is significant new code in a sub-slice already
  producing a documentation artifact + README index update +
  targeted test surface.
- Bundling the wiring with the LAST sub-slice (13An) is consistent
  with the precedent that the slice-end SIX-VECTOR review fires at
  the LAST sub-slice (the 13n / 12f / 11n pattern).
- The LAST sub-slice 13An will naturally pair the wiring with
  doc-13a step 9 dependency reconciliation (per doc-13a:285-287) +
  the slice-end six-vector review, scoped to a single coherent
  governance-precursor closure.

The binding statement P3-13A-6-3 stays CARRIED through this
sub-slice unchanged.

## Test surface (Slice 13A scope)

| Test module | Test count | Sub-slice |
|-------------|------------|-----------|
| `tests/test_governance_completeness_scanner.py` | 18 | 1st |
| `tests/test_execution_control_completeness.py` | 35 | 2nd |
| `tests/test_execution_control_prompt_context_adapter.py` | 23 | 3rd |
| `tests/test_execution_control_dispatcher_prompt_context.py` | 21 | 4th |
| `tests/test_execution_control_gate_companion.py` | 44 | 5th |
| `tests/test_execution_control_snapshot_companion.py` | 54 | 6th |
| `tests/test_governance_13a_acceptance_artifact.py` | (this sub-slice) | 7th |

All sub-slice tests run in well under one second; all are
byte-identical to the post-implementer baselines per the gates in
STATUS.md § "Hygiene gate (this iteration)".

## Change-control discipline preserved

Per doc-13a:42-46 + 124-126 + `feedback_no_refactor`:

- **NO** in-place edit to any accepted Slice 00-12 plan doc this
  sub-slice. The acceptance artifact is a NEW file; the README index
  entry is APPEND-ONLY.
- **NO** in-place edit to any accepted Slice 00-12 module. The 13A
  surfaces are NEW execution-control modules (2nd-6th sub-slice) +
  pure-data additions to the existing `failure_router.py`.
- **NO** silent migration of in-flight features. The Slice 12d
  adoption marker remains the only path.
- **NO** new authority introduced. The 13A surfaces are read-only
  wrappers + opt-in ports + typed companion records; the 6th-sub-slice
  composite chain remains dead-until-wired per P3-13A-6-3.

## Pending after this sub-slice (LAST sub-slice 13An)

- **Doc-13a step 9** (doc-13a:285-287): "Update governance Slices
  13-20 and context Slice 21 to depend on this shared completeness
  model instead of redefining authority semantics locally."
- **P3-13A-6-3 binding closure**: wire the composite adapter into a
  real gate / verifier / classifier production consumer site
  (external opt-in wrapper around `supervisor/classifier.py` OR
  `public_dashboard.py`).
- **Slice-end SIX-VECTOR review** (V1 doc-acceptance / V2 contract
  integrity / V3 test honesty / V4 Slice 00-12 preservation / V5
  fail-closed + deps / V6 Slice 13A invariant compliance) — fires at
  the LAST sub-slice 13An.

**13An overloading risk acknowledged.** The LAST sub-slice as
currently scoped bundles 3 workstreams (step 9 reconciliation across
8-9 docs + P3-13A-6-3 wiring + slice-end SIX-VECTOR review). 13An
MAY SPLIT into **13An-1** (step 9 reconciliation), **13An-2**
(P3-13A-6-3 wiring + binding closure tests), and **13An-3** (slice-end
six-vector review + finalizer) if scope inflates beyond a single
coherent chunk. Mirrors the 12a-1 / 12a-2 / 12a-3 SPLIT precedent
per `IMPLEMENTATION_PROMPT_GOVERNANCE.md:188-189`.

After 13An (or the 13An-1 / 13An-2 / 13An-3 SPLIT) closes, the
governance loop advances to **Slice 14 — Commit And Line Provenance**.
