"""Slice 14 first sub-slice -- foundational commit + line provenance typed-shape module.

This module owns the 4 doc-14 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/14-commit-and-line-provenance.md:79-133``):

* :class:`CommitProvenanceTrailer` -- the compact Git commit trailer carried
  in workflow-authored commit messages (doc-14:79-87).
* :class:`CommitProvenancePayload` -- the full structured payload written to
  Git notes / refs (e.g. ``refs/notes/iriai`` keyed by commit or
  ``refs/iriai/provenance/{precommit_provenance_digest}``) (doc-14:89-110).
* :class:`LineProvenanceQuery` -- the bounded query the line-provenance reader
  consumes (doc-14:112-122).
* :class:`LineProvenanceResult` -- the bounded result the reader returns,
  carrying the Slice 13A shared :data:`CompletenessState` + the Slice 13a
  :class:`GovernanceEvidencePageRef` paged-evidence list (doc-14:124-133).

It is the **cross-cutting typed foundation** that subsequent Slice 14
sub-slices (the Git provenance writer + the line-provenance reader + the
governance projection per doc-14 § Refactoring Steps steps 2-7) build on;
this first sub-slice does NOT yet wire these typed shapes into any
executor / checkpoint / merge-queue / governance-projection consumer --
that wiring lands in subsequent sub-slices per doc-14:155-178.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Per doc-14:7-9 the entire slice is a NON-BLOCKING
governance projection that MUST NOT create new checkpoint authority after
the execution-control-plane landing.

**Slice 13A dependency reconciliation (doc-13a:285-287 step 9; doc-14:263-311
Slice 13A Shared Completeness Model Dependency).** The
:class:`LineProvenanceResult.completeness` field is the Slice 13A shared
:data:`CompletenessState` :data:`Literal` (imported from
:mod:`iriai_build_v2.execution_control.completeness`). The
:class:`LineProvenanceResult.page_refs` field is the list of Slice 13a
:class:`GovernanceEvidencePageRef` (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`). Neither type
is redefined here -- per doc-13a:285-287 step 9 ("Update governance Slices
13-20 and context Slice 21 to depend on this shared completeness model
instead of redefining authority semantics locally") this module consumes
the shared models directly.

**Slice 08 non-alteration discipline (doc-14:155-160 step 1).** Per the
governance prompt § "Non-Negotiables" + the doc-14:155-160 step 1 inventory
rule ("If Slice 08 already landed trailer/Git-provenance fields, verify
and index them; if not, do not alter ``dag-commit-proof:*``") the Slice 08
canonical commit-proof typed row
(:class:`iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
at ``merge_queue_store.py:227``; 10 fields verbatim) is the canonical
``dag-commit-proof:*`` evidence and remains byte-identical from this
governance slice. The Slice 14 trailer/payload typed shapes here are
STRICTLY ADDITIVE governance projections that consume the Slice 08 typed
row via cross-citation (``commit_proof_evidence_id`` field on
:class:`CommitProvenancePayload`).

**Precommit-stable provenance ref discipline (doc-14:138-142).** The
``precommit_provenance_ref`` field on both :class:`CommitProvenanceTrailer`
and :class:`CommitProvenancePayload` MUST be derivable from stable inputs
**known before** ``git commit``: feature id + DAG sha256 + group + repo id
+ queue item ids + task id digest + contract digest. It MUST NOT contain
the result commit hash unless an explicit amend flow reruns all digest
checks. The typed shape carries the value verbatim (string field with no
default) and the doc-14:138-142 stability contract is enforced by
producer tests + future Slice 14 writer sub-slices.

**Payload self-exclusion digest discipline (doc-14:151-153).** The
``payload_sha256`` field on :class:`CommitProvenancePayload` MUST be
computed from the canonical-JSON projection of the payload with the
``payload_sha256`` field itself OMITTED. The :func:`compute_payload_sha256`
helper below implements this self-exclusion discipline; tests in
:mod:`tests.test_execution_control_commit_provenance` prove that
recomputing the digest after loading the payload returns the stored
value. This mirrors the Slice 13a + Slice 13A canonical-JSON discipline
(``json.dumps(..., sort_keys=True, separators=(",", ":"))``; then
``hashlib.sha256(...).hexdigest()``).

**Implementation discipline.** Stdlib (``hashlib`` + ``json``) + Pydantic v2
+ Slice 13A modules (``.completeness``) + Slice 13a modules
(``..workflows.develop.governance.models``) only. NO imports from
``governance/`` outside ``governance.models`` (this module is foundational;
the governance layer consumes execution-control surfaces, not the
reverse). NO imports from other parts of ``execution_control/`` (this
module is foundational for the future Slice 14 writer + reader; the
existing Slice 00-12 ``execution_control`` modules are NOT modified).
NO imports from ``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard`` (those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.completeness` (Slice 13A 2nd
sub-slice): ``BaseModel`` subclasses with ``ConfigDict(extra="forbid")``
so typo-d kwargs fail closed as a typed ``ValidationError`` rather than
being silently absorbed. ``schema_version`` is pinned to the
``"iriai.commit_provenance.v1"`` literal default per doc-14:90.

Per the auto-memory ``feedback_flat_structured_output`` rule the trailer
control fields are flat primitives (``str`` / ``int | None``). Per the
auto-memory ``feedback_no_silent_degradation`` rule every field validator
fails closed. Per the auto-memory ``feedback_no_overengineer_use_library``
rule the module mirrors the Slice 13A 2nd sub-slice precedent verbatim
without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iriai_build_v2.execution_control.completeness import CompletenessState
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidencePageRef,
)


__all__ = [
    # Schema version pin (doc-14:90).
    "COMMIT_PROVENANCE_SCHEMA_VERSION",
    # The 4 doc-14:79-133 typed shapes.
    "CommitProvenanceTrailer",
    "CommitProvenancePayload",
    "LineProvenanceQuery",
    "LineProvenanceResult",
    # Helpers (doc-14:151-153 payload-self-exclusion digest discipline).
    "compute_payload_sha256",
    "canonical_payload_dict",
]


# --- Schema version pin (doc-14:90) -----------------------------------------


COMMIT_PROVENANCE_SCHEMA_VERSION: Literal["iriai.commit_provenance.v1"] = (
    "iriai.commit_provenance.v1"
)
"""Doc-14:90 -- the pinned schema version literal for
:class:`CommitProvenancePayload`.

The single-value Literal serves as both the typed-surface contract
(constructors that pass a different string fail closed at Pydantic
validation) and the cross-process identity tag (future Slice 14 writer
sub-slices version-bump the payload by introducing a parallel
``"iriai.commit_provenance.v2"`` Literal rather than mutating this one
in-place).
"""


# --- The 4 doc-14:79-133 typed shapes ---------------------------------------


class CommitProvenanceTrailer(BaseModel):
    """Doc-14:79-87 -- the compact Git commit trailer carried in
    workflow-authored commit messages.

    Per doc-14:136-142 trailer values MUST be known before ``git commit``
    (the trailer is part of the commit message body); :attr:`precommit_provenance_ref`
    is derived from stable inputs (feature id + DAG sha256 + group + repo id
    + queue item ids + task id digest + contract digest) and MUST NOT
    contain the result commit hash unless an explicit amend flow reruns
    all digest checks.

    Per doc-14:137 trailers are mandatory + compact: oversized values
    (e.g. long task id lists) are digested into the ``*_digest`` fields
    rather than enumerated verbatim. The full enumerated payload is
    written to Git notes/refs as a :class:`CommitProvenancePayload`.

    The 8 fields land verbatim from doc-14:79-87.
    """

    # extra='forbid' aligns with the Slice 13A precedent at
    # src/iriai_build_v2/execution_control/completeness.py:204 + the
    # sibling governance model precedent at
    # src/iriai_build_v2/workflows/develop/governance/models.py:548 --
    # unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    feature_id: str
    """Doc-14:80 -- the feature this trailer scopes to. Stable across the
    lifetime of the feature (matches the ``feature_id`` field on
    :class:`iriai_build_v2.execution_control.merge_queue_store.MergeQueueItem`)."""

    group_idx: int
    """Doc-14:81 -- the DAG group index this commit checkpoints. Matches
    the ``group_idx`` field on
    :class:`iriai_build_v2.execution_control.merge_queue_store.MergeQueueItem`
    and the Slice 08 ``dag-group:{group_idx}`` projection."""

    effective_group_idx: int | None = None
    """Doc-14:82 -- optional effective group index for regroup-overlay
    scenarios (doc-09 § "Regroup overlay"). When the regroup overlay
    re-assigns a group to a new effective index, the trailer records
    both the original ``group_idx`` and the effective post-overlay
    index. None when no overlay is active."""

    task_ids_digest: str
    """Doc-14:83 -- SHA-256 hex digest over the sorted task id list this
    commit covers. Per doc-14:137 the digest is compact (a 64-char hex
    string) regardless of how many task ids the commit covers; the full
    enumerated list lives in the :class:`CommitProvenancePayload`
    written to Git notes/refs."""

    merge_queue_item_ids_digest: str
    """Doc-14:84 -- SHA-256 hex digest over the sorted merge-queue
    ``item_id`` list this commit covers. Per doc-14:137 the digest is
    compact. The full enumerated list lives in
    :class:`CommitProvenancePayload`."""

    checkpoint_ref: str
    """Doc-14:85 -- the Slice 08 checkpoint reference (e.g.
    ``dag-group:{group_idx}`` artifact key) this commit checkpoints.
    Stable cross-process per the Slice 08 ``dag-group:*`` projection
    naming contract."""

    precommit_provenance_ref: str
    """Doc-14:86 + doc-14:138-142 -- the precommit-stable provenance
    reference (e.g. ``refs/iriai/provenance/{precommit_provenance_digest}``).

    Per doc-14:138-142 the value is derived from stable inputs known
    BEFORE ``git commit`` (feature id + DAG sha256 + group + repo id +
    queue item ids + task id digest + contract digest); it MUST NOT
    contain the result commit hash unless an explicit amend flow
    reruns all digest checks. Future Slice 14 writer sub-slices enforce
    the derivation contract; the typed shape carries the value verbatim."""

    precommit_provenance_digest: str
    """Doc-14:87 -- SHA-256 hex digest over the precommit-stable
    provenance inputs (same derivation as :attr:`precommit_provenance_ref`
    but a digest rather than a ref path). Used by the Git ref naming
    convention (e.g. ``refs/iriai/provenance/{precommit_provenance_digest}``)
    so the ref name is itself precommit-stable + cross-process derivable."""


class CommitProvenancePayload(BaseModel):
    """Doc-14:89-110 -- the full structured payload written to Git notes / refs.

    Per doc-14:143-146 the payload is written to Git notes or Git refs
    (e.g. ``refs/notes/iriai`` keyed by commit or
    ``refs/iriai/provenance/{precommit_provenance_digest}``) AFTER ``git
    commit``; therefore the payload MAY include the result commit hash
    (``commit_hash``) because it is written post-commit.

    Per doc-14:147-148 Postgres stores the canonical ``dag-commit-proof:*``
    evidence (the Slice 08 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    typed row, ``merge_queue_store.py:227``, unchanged from this slice)
    plus the Git provenance ref + digest. Git notes/refs are verified
    during resume but are NOT the source of execution authority.

    The 18 fields land verbatim from doc-14:89-110.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["iriai.commit_provenance.v1"] = (
        COMMIT_PROVENANCE_SCHEMA_VERSION
    )
    """Doc-14:90 -- the pinned schema version literal (default
    :data:`COMMIT_PROVENANCE_SCHEMA_VERSION`). Constructors that pass a
    different string fail closed at Pydantic validation. Future Slice 14
    payload version-bumps introduce a parallel
    ``"iriai.commit_provenance.v2"`` Literal rather than mutating this
    one in-place."""

    feature_id: str
    """Doc-14:91 -- the feature this payload scopes to."""

    dag_sha256: str
    """Doc-14:92 -- SHA-256 hex digest of the canonical DAG this payload
    cites. Ties the payload to a specific DAG version (matches the
    ``dag_sha256`` field on
    :class:`iriai_build_v2.execution_control.merge_queue_store.MergeQueueItem`)."""

    group_idx: int
    """Doc-14:93 -- the DAG group index this commit checkpoints."""

    effective_group_idx: int | None
    """Doc-14:94 -- optional effective group index for regroup-overlay
    scenarios (mirrors :attr:`CommitProvenanceTrailer.effective_group_idx`).
    Required field (no default) -- explicit None vs unset distinction
    forces every constructor site to declare its regroup-overlay claim."""

    repo_id: str
    """Doc-14:95 -- the repo this commit belongs to. Multi-repo
    checkpoints (per doc-14:178 + doc-14:204) write one payload per
    repo; the group checkpoint links all per-repo payload refs."""

    commit_hash: str
    """Doc-14:96 -- the result commit hash (written post-commit; per
    doc-14:145-146 the payload MAY include the result commit hash
    because it is written AFTER the commit). For multi-repo checkpoints
    this is the per-repo commit hash (one payload per repo); the legacy
    comma-joined display preserved at
    :func:`iriai_build_v2.workflows.develop.execution.merge_queue.GroupMergeCoverage.result_commits`."""

    parent_hash: str
    """Doc-14:97 -- the parent commit hash (the commit's ``HEAD^``
    immediately before this commit was created). Stable post-commit;
    enables Git lineage queries without an additional ``git rev-parse``."""

    tree_hash: str
    """Doc-14:98 -- the Git tree object hash of the committed tree
    (``git rev-parse HEAD^{tree}``). Used by the Slice 14 reader to
    distinguish content-identical commits from semantically-equivalent
    rebased commits."""

    task_ids: list[str]
    """Doc-14:99 -- the full enumerated task id list this commit covers
    (compared to the compact :attr:`CommitProvenanceTrailer.task_ids_digest`
    in the trailer). Stable cross-process per the Slice 03 task contract
    ownership."""

    contract_ids: list[int]
    """Doc-14:100 -- the typed-row primary key list of the Slice 03 task
    contracts this commit covers."""

    attempt_ids: list[int]
    """Doc-14:101 -- the typed-row primary key list of the dispatcher
    attempts (per Slice 05 ``DispatchAttemptResult``) this commit
    integrates."""

    sandbox_patch_evidence_ids: list[int]
    """Doc-14:102 -- the typed-row primary key list of the Slice 04
    sandbox patch evidence rows this commit applies."""

    gate_evidence_ids: list[int]
    """Doc-14:103 -- the typed-row primary key list of the Slice 06
    gate evidence rows that approved this commit."""

    merge_queue_item_ids: list[int]
    """Doc-14:104 -- the typed-row primary key list of the Slice 08
    merge-queue items this commit integrates (compared to the compact
    :attr:`CommitProvenanceTrailer.merge_queue_item_ids_digest`)."""

    commit_proof_evidence_id: int
    """Doc-14:105 -- the typed-row primary key of the Slice 08
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    commit-proof evidence row (kind=``commit_proof``,
    ``merge_queue_store.py:799-820``). The Slice 14 payload cross-cites
    the Slice 08 typed evidence but does NOT replace it -- per
    doc-14:155-160 the Slice 08 row shape MUST remain byte-identical."""

    checkpoint_artifact_id: int | None
    """Doc-14:106 -- optional typed-row primary key of the Slice 08
    checkpoint artifact (``dag-group:*`` projection). None when the
    payload is written for a pre-checkpoint commit (e.g. an in-flight
    integration without a finalized group checkpoint)."""

    no_dirty_snapshot_ids: list[int]
    """Doc-14:107 -- the typed-row primary key list of the Slice 08
    workspace no-dirty snapshot rows that prove the post-commit workspace
    is clean. Multi-repo checkpoints carry one snapshot id per repo."""

    implementation_log_anchors: list[str]
    """Doc-14:108 -- the list of implementation-log anchor strings
    (per the Slice 13c implementation-journal parser
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`)
    that document this commit. Free-form anchor strings that the Slice
    13c parser emits."""

    precommit_provenance_ref: str
    """Doc-14:109 -- the precommit-stable provenance reference (matches
    :attr:`CommitProvenanceTrailer.precommit_provenance_ref` verbatim).

    Per doc-14:138-142 derived from stable inputs known BEFORE ``git
    commit``; MUST NOT contain the result commit hash unless an explicit
    amend flow reruns all digest checks. Future Slice 14 writer
    sub-slices enforce the derivation contract."""

    payload_sha256: str
    """Doc-14:110 + doc-14:151-153 -- SHA-256 hex digest over the
    canonical-JSON projection of THIS payload WITH ``payload_sha256``
    OMITTED.

    Per doc-14:151-153 ("``payload_sha256`` is computed from canonical
    JSON with ``payload_sha256`` omitted. Tests must prove recomputing
    the digest after loading the payload gives the stored value") the
    self-exclusion discipline is enforced by :func:`compute_payload_sha256`;
    constructors set the digest to ``compute_payload_sha256(payload)``
    BEFORE the payload is finalized + stored.

    Tests in
    :mod:`tests.test_execution_control_commit_provenance` prove that
    ``compute_payload_sha256(payload) == payload.payload_sha256`` after
    loading the payload via ``model_validate_json``."""


class LineProvenanceQuery(BaseModel):
    """Doc-14:112-122 -- the bounded query the line-provenance reader consumes.

    Per doc-14:220 + doc-14:199-201 the reader enforces ``max_lines`` /
    ``max_commits`` / ``max_payload_bytes`` / ``timeout_ms`` caps and
    returns ``completeness="paged"`` with exact
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    rows when the query exceeds inline caps; a bounded partial result
    without exact page refs is ``preview_only`` and per the Slice 13A
    invariant doc-13a:18-23 cannot feed context packages, governance
    findings, metrics, or policy recommendations as line provenance
    authority.

    The 9 fields land verbatim from doc-14:113-122.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    """Doc-14:113 -- the repo to query."""

    ref: str
    """Doc-14:114 -- the Git ref (commit hash, branch, tag, or symbolic
    ref) at which to evaluate the query. Stable cross-process per Git's
    ref naming contract."""

    path: str
    """Doc-14:115 -- the repo-relative file path to query."""

    line_start: int
    """Doc-14:116 -- the inclusive 1-indexed start line of the range to
    query."""

    line_end: int
    """Doc-14:117 -- the inclusive 1-indexed end line of the range to
    query."""

    include_history: bool = True
    """Doc-14:118 -- when True (default), the query traverses Git history
    to gather provenance for all commits that touched the range; when
    False the query returns only the current ref's provenance."""

    max_lines: int = 500
    """Doc-14:119 -- maximum lines the reader may return inline. When
    ``line_end - line_start + 1 > max_lines`` the reader returns
    ``completeness="paged"`` with exact page refs (per doc-14:199-201).

    Default 500 matches doc-14:119 verbatim."""

    max_commits: int = 50
    """Doc-14:120 -- maximum commits the reader may return inline. When
    the history traversal would return more than ``max_commits`` commits
    the reader returns ``completeness="paged"`` with exact page refs.

    Default 50 matches doc-14:120 verbatim."""

    max_payload_bytes: int = 512_000
    """Doc-14:121 -- maximum total payload size (in bytes) the reader
    may return inline. When the inline result would exceed
    ``max_payload_bytes`` the reader returns ``completeness="paged"``
    with exact page refs.

    Default 512_000 (~512 KB) matches doc-14:121 verbatim."""

    timeout_ms: int = 10_000
    """Doc-14:122 -- maximum query time (in milliseconds). When the
    reader cannot complete the query within ``timeout_ms`` it returns
    ``completeness="unavailable"`` with a reason string.

    Default 10_000 (10 seconds) matches doc-14:122 verbatim. Mirrors
    the Slice 10 supervisor's bounded-query discipline (per the
    ``SET LOCAL statement_timeout`` pattern)."""

    @field_validator("max_lines", "max_commits", "max_payload_bytes", "timeout_ms")
    @classmethod
    def _positive_caps(cls, value: int) -> int:
        # Per doc-14:220 + doc-14:199-201 the caps are RESOURCE LIMITS
        # mandatory but page/read limits, NOT silent truncation
        # permission. A non-positive cap defeats the cap contract; fail
        # closed.
        if value <= 0:
            raise ValueError(
                "LineProvenanceQuery cap field must be positive "
                "(doc-14:119-122 + doc-14:220)"
            )
        return value

    @field_validator("line_start", "line_end")
    @classmethod
    def _positive_line_indices(cls, value: int) -> int:
        # Per doc-14:116-117 the line range is 1-indexed; a non-positive
        # line index is not a valid range bound. Fail closed.
        if value <= 0:
            raise ValueError(
                "LineProvenanceQuery line index must be positive "
                "(1-indexed; doc-14:116-117)"
            )
        return value


class LineProvenanceResult(BaseModel):
    """Doc-14:124-133 -- the bounded result the line-provenance reader returns.

    Per doc-14:199-203 + doc-14:220 the result carries:

    * The list of commit hashes that touch the queried range
      (``commit_hashes``).
    * The list of task ids that own the queried range (``task_ids``),
      derived by combining Git blame + commit trailers + Git notes/refs
      + typed ``dag-commit-proof:*`` evidence (per doc-14:172-173).
    * The list of :class:`CommitProvenancePayload` ref paths that back
      the result (``provenance_payload_refs``).
    * The list of Slice 13a
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
      paged-evidence rows (``page_refs``) when the result is paged
      exact.
    * The Slice 13A shared :data:`CompletenessState` Literal
      (``completeness``) -- per doc-13a:285-287 step 9 the field is the
      shared 13A enum, NOT a locally-redefined one.
    * The SHA-256 hex digest over the completeness payload
      (``completeness_digest``) -- mirrors the Slice 13A
      :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
      cross-process freshness contract.
    * A confidence score in [0.0, 1.0] (``confidence``).
    * The list of gap-reason strings (``gaps``) when the result is
      paged or partial.

    The 8 fields land verbatim from doc-14:125-133.

    **Slice 13A dependency reconciliation (doc-13a:285-287 step 9;
    doc-14:263-311).** The :attr:`completeness` + :attr:`page_refs` fields
    are the shared-13A typed surfaces, imported from
    :mod:`iriai_build_v2.execution_control.completeness` and
    :mod:`iriai_build_v2.workflows.develop.governance.models` respectively.
    They are NOT redefined here; future Slice 14 sub-slices that wire
    the reader into dispatcher / gate / classifier production sites MUST
    consume the shared-13A completeness contract via these typed
    surfaces (per the Slice 13A invariant doc-13a:18-23: anything that
    can influence dispatch / verify / merge / checkpoint / routing /
    scheduler feedback / policy recommendation consumes exact cited
    evidence or an exact paged manifest).

    **Authority routing (doc-14:200-203 + doc-13a:18-23).** A bounded
    partial result without exact page refs MUST be
    ``completeness="preview_only"`` and cannot feed context packages,
    governance findings, metrics, or policy recommendations as line
    provenance authority. The typed shape carries the completeness state
    verbatim; the future Slice 14 sub-slice that wires the reader into
    classifier rules + the future ``governance_evidence_conflict`` typed
    failure id (per doc-14:192-201) enforces the authority routing.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    commit_hashes: list[str]
    """Doc-14:125 -- the list of commit hashes that touch the queried
    range. Stable cross-process per Git's commit hash naming. The list
    may be a paged subset (when ``completeness == "paged"`` the full
    list lives behind the :attr:`page_refs` paged exact references)."""

    task_ids: list[str]
    """Doc-14:126 -- the list of task ids that own the queried range,
    derived by combining Git blame + commit trailers + Git notes/refs +
    typed ``dag-commit-proof:*`` evidence (per doc-14:172-173). Stable
    cross-process per the Slice 03 task contract ownership."""

    provenance_payload_refs: list[str]
    """Doc-14:127 -- the list of :class:`CommitProvenancePayload` ref
    paths (e.g. ``refs/iriai/provenance/{precommit_provenance_digest}``)
    that back the result. Stable cross-process per the Git ref naming
    contract."""

    page_refs: list[GovernanceEvidencePageRef]
    """Doc-14:128 -- the list of Slice 13a paged-evidence references
    when the result is paged exact.

    **Slice 13A dependency reconciliation (doc-13a:285-287 step 9;
    doc-14:263-311).** This field is the Slice 13a
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    list -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for paged-evidence references; future Slice 14 reader
    sub-slices populate this list using the Slice 13a typed shape
    directly."""

    completeness: CompletenessState
    """Doc-14:129 -- the Slice 13A shared completeness state.

    **Slice 13A dependency reconciliation (doc-13a:285-287 step 9;
    doc-14:263-311).** This field is the Slice 13A shared
    :data:`~iriai_build_v2.execution_control.completeness.CompletenessState`
    Literal (4 values: ``complete`` / ``paged`` / ``preview_only`` /
    ``unavailable``) -- imported from
    :mod:`iriai_build_v2.execution_control.completeness`, NOT redefined
    here. Per doc-13a:285-287 step 9 the shared model is the authority
    for completeness state; future Slice 14 reader sub-slices populate
    this field using the Slice 13A typed Literal directly.

    Per the Slice 13A invariant doc-13a:18-23: a ``preview_only`` result
    MUST NOT feed context packages, governance findings, metrics, or
    policy recommendations as line provenance authority. The authority
    routing is enforced by the future Slice 14 classifier wiring +
    the future ``governance_evidence_conflict`` typed failure id (per
    doc-14:192-201)."""

    completeness_digest: str
    """Doc-14:130 -- SHA-256 hex digest over the completeness payload.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    cross-process freshness contract: two reads of the same logical
    result produce byte-identical digests. Used by future Slice 14
    consumer wiring to detect stale completeness state."""

    confidence: float = Field(ge=0.0, le=1.0)
    """Doc-14:131 -- the confidence score in [0.0, 1.0] for the result.

    Per the Slice 13 confidence-scoring contract (doc-13:173-175) the
    score reflects evidence quality: typed-first sources score higher
    than legacy fallbacks; complete + paged results score higher than
    preview-only or unavailable results."""

    gaps: list[str]
    """Doc-14:132 -- the list of gap-reason strings when the result is
    paged or partial. Each string names a specific gap (e.g.
    ``missing_trailer_for_commit:<hash>``, ``stale_note_for_ref:<ref>``,
    ``unattributed_blame_for_line:<line>``); the future Slice 14 reader
    enforces a controlled vocabulary."""


# --- Payload-self-exclusion digest helpers (doc-14:151-153) -----------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates lexicographic
    key ordering and the compact separator set so the resulting bytes
    are stable across Python versions / platforms / dict ordering.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_payload_dict(payload: CommitProvenancePayload) -> dict[str, Any]:
    """Project a :class:`CommitProvenancePayload` to its canonical-JSON dict
    representation with the ``payload_sha256`` field OMITTED.

    Per doc-14:151-153 the self-exclusion discipline is: the digest is
    computed over the canonical-JSON projection of the payload with the
    ``payload_sha256`` field itself OMITTED. This helper produces that
    projection deterministically so consumers can recompute the digest
    after loading the payload and verify equality with
    :attr:`CommitProvenancePayload.payload_sha256`.

    The projection uses :meth:`BaseModel.model_dump` with default
    serialisation (no mode='json' coercion is needed because the typed
    shape projects to JSON-safe primitives only); the ``payload_sha256``
    key is then dropped from the resulting dict. The dict is the input
    to :func:`compute_payload_sha256`; both this helper and
    :func:`compute_payload_sha256` use :func:`_canonical_json` for
    deterministic serialisation.
    """

    raw = payload.model_dump()
    # Per doc-14:151-153 the digest field itself MUST be excluded from
    # the digest input -- otherwise the digest depends on its own value
    # and no roundtrip recompute can ever match.
    raw.pop("payload_sha256", None)
    return raw


def compute_payload_sha256(payload: CommitProvenancePayload) -> str:
    """Compute the deterministic ``payload_sha256`` digest for a
    :class:`CommitProvenancePayload`.

    Per doc-14:151-153 the digest is computed from the canonical-JSON
    projection of the payload with the ``payload_sha256`` field itself
    OMITTED. This helper implements the self-exclusion discipline; tests
    in :mod:`tests.test_execution_control_commit_provenance` prove that
    recomputing the digest after loading the payload via
    ``model_validate_json`` returns the stored value.

    **Determinism contract.** Two calls with the same logical payload
    (regardless of dict-key insertion order on either side of a
    serialisation roundtrip) MUST produce byte-identical hex digests.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    canonical-JSON + SHA-256 discipline + the Slice 13 governance
    :func:`~iriai_build_v2.workflows.develop.governance.evidence_set._sha256_hex`
    helper verbatim.
    """

    return _sha256_hex(_canonical_json(canonical_payload_dict(payload)))
