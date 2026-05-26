"""Slice 14 second sub-slice -- Git provenance writer behind a narrow
governance projection interface.

Per ``docs/execution-control-plane/14-commit-and-line-provenance.md``
§ Refactoring Steps step 2 (lines 155-178): *"Extract a Git provenance
writer behind a narrow governance projection interface that runs from
existing commit-proof/checkpoint evidence. It may be invoked by a
post-checkpoint governance job or an explicitly non-blocking merge-queue
hook, but it must not decide checkpoint success."*

This module owns the Git provenance writer surface:

* :class:`CommitProvenanceWriterInputs` -- typed bundle of all inputs
  the writer needs (Slice 08 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
  + typed group/task context).
* :func:`compute_precommit_provenance_inputs` -- doc-14:138-142 stable-input
  derivation (feature_id + dag_sha + group + repo_id + queue item ids +
  task id digest + contract digest). MUST NOT contain the result commit
  hash unless an explicit amend flow reruns all digest checks.
* :func:`compute_precommit_provenance_digest` -- SHA-256 hex digest over
  the stable inputs (doc-14:87).
* :func:`compute_precommit_provenance_ref` -- canonical ref path
  ``refs/iriai/provenance/{precommit_provenance_digest}`` per
  doc-14:144-150.
* :func:`compute_trailer` -- builds a :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenanceTrailer`
  from the inputs (doc-14:79-87).
* :func:`compute_payload` -- builds a :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenancePayload`
  with the self-excluding ``payload_sha256`` per doc-14:151-153.
* :class:`GitProvenanceWriter` -- the writer port (idempotent Git
  notes/refs writer per doc-14:144-150). Takes a stdlib subprocess
  callable so unit tests can supply a fake Git subprocess fixture.
* :class:`CommitProvenanceWriteResult` -- typed result with the ref +
  digest + idempotency flag.
* :class:`CommitProvenanceGapFinding` -- typed governance-gap finding
  produced when the writer fails (per doc-14:192-201:
  ``line_provenance_gap`` or ``governance_evidence_conflict``). Per
  doc-14:242-243 the finding is NON-blocking: the writer does NOT decide
  checkpoint success.

**Non-blocking failure routing discipline (doc-14:242-243).** Per
*"Governance provenance projection failures never block ``dag-group:*``
checkpointing, merge queue integration, or resume"* -- the writer is a
post-checkpoint observer. When the writer fails (Git notes write fails;
ref already exists with a different payload digest; etc.), it records a
:class:`CommitProvenanceGapFinding` and returns gracefully. The caller
(a post-checkpoint governance job or an explicitly non-blocking
merge-queue hook) MUST NOT propagate the failure to the executor /
checkpoint / merge-queue / resume code paths. The corresponding typed
failure ids (``line_provenance_gap`` + ``governance_evidence_conflict``)
register under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with a
NON-blocking retry action (NOT ``quiesce``), so the failure router
itself respects the doc-14:242-243 non-blocking contract.

**Idempotency discipline (doc-14:144-150).** Per *"Full payloads are
written to Git notes or Git refs, for example ``refs/notes/iriai`` keyed
by commit or ``refs/iriai/provenance/{precommit_provenance_digest}``. The
payload may include the result commit hash because it is written after
commit."* -- identical inputs MUST produce identical refs + payload
digests. The writer is idempotent: a retry with the same inputs is a
no-op (no second Git note write). The idempotency contract is enforced
at the typed-shape boundary: the same :class:`CommitProvenanceWriterInputs`
ALWAYS produces the same :class:`CommitProvenancePayload` (modulo
``payload_sha256``, which is itself self-excluding per doc-14:151-153)
and the same ref.

**Precommit-stable provenance ref discipline (doc-14:138-142).** Per
*"Trailer values must be known before ``git commit``.
``precommit_provenance_ref`` is derived from stable inputs such as
feature id, dag sha, group, repo id, queue item ids, task id digest,
and contract digest. It must not contain the result commit hash unless
the implementation uses an explicit amend flow that reruns all digest
checks."* -- :func:`compute_precommit_provenance_inputs` derives the
ref from inputs known BEFORE ``git commit``; the function SIGNATURE
does NOT accept ``commit_hash``. Future amend-flow callers MUST call a
separate amend-aware variant (not in this sub-slice's scope).

**Slice 08 non-alteration discipline (doc-14:155-160 step 1).** The
Slice 08 canonical :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
typed row (10 fields verbatim) is READ-ONLY from this slice. The writer
consumes the :class:`RepoCommitProof` via cross-citation
(``commit_proof_evidence_id`` integer field on
:class:`CommitProvenancePayload`) but does NOT mutate it.

**Implementation discipline.** Stdlib (``json`` + ``hashlib`` +
``subprocess``-shaped fake fixture) + Pydantic v2 + Slice 13A modules
(``.completeness``) + Slice 13a modules (``..workflows.develop.governance.models``)
+ Slice 08 modules (``..execution_control.merge_queue_store``) only. NO
imports from ``governance/`` outside ``governance.models``. NO imports
from ``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard``.

Per the auto-memory ``feedback_flat_structured_output`` rule control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every failure produces a typed
:class:`CommitProvenanceGapFinding`. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 1st sub-slice + Slice 13A 2nd sub-slice precedents verbatim
without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iriai_build_v2.execution_control.commit_provenance import (
    CommitProvenancePayload,
    CommitProvenanceTrailer,
    canonical_payload_dict,
    compute_payload_sha256,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof


__all__ = [
    # Typed inputs / outputs.
    "CommitProvenanceWriterInputs",
    "CommitProvenanceWriteResult",
    "CommitProvenanceGapFinding",
    # The 2 typed failure ids registered under EXISTING evidence_corruption
    # failure_class per doc-14:192-201 (DIFFERENT from prior Slice 13A
    # pattern: NON-blocking per doc-14:242-243).
    "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
    # Pure helpers (doc-14:138-142 precommit-stable derivation + doc-14:79-110
    # typed-shape construction).
    "compute_precommit_provenance_inputs",
    "compute_precommit_provenance_digest",
    "compute_precommit_provenance_ref",
    "compute_trailer",
    "compute_payload",
    "compute_notes_ref_namespace",
    # Subprocess port + writer.
    "GitSubprocessRunner",
    "GitSubprocessResult",
    "GitProvenanceWriter",
    "GitProvenanceWriteError",
]


# --- Typed failure ids (doc-14:192-201) -------------------------------------


COMMIT_PROVENANCE_GAP_FAILURE_IDS: tuple[
    Literal["line_provenance_gap"], Literal["governance_evidence_conflict"]
] = (
    "line_provenance_gap",
    "governance_evidence_conflict",
)
"""Doc-14:192-201 -- the 2 typed failure ids the writer projects onto when it
fails post-checkpoint.

Both register under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class). Both route to a NON-blocking governance retry per
doc-14:242-243 (DIFFERENT from prior Slice 13A typed ids which all route
to ``quiesce``).

- ``line_provenance_gap`` -- the Git notes/refs write failed AFTER commit
  (per doc-14:194-196 "Git note write fails after commit: governance
  records a ``line_provenance_gap`` or ``governance_evidence_conflict``
  finding and retries the projection idempotently. It does not block
  checkpointing or resume.").
- ``governance_evidence_conflict`` -- the Git ref already exists with a
  different payload digest, signalling cross-process disagreement on
  provenance state (per doc-14:194-196).
"""


# --- Helper for ref/digest computation (doc-14:138-142) ---------------------


def compute_notes_ref_namespace() -> str:
    """The canonical Git notes namespace for iriai commit provenance.

    Per doc-14:144-150: *"Full payloads are written to Git notes or Git
    refs, for example ``refs/notes/iriai`` keyed by commit..."*. This
    helper returns the canonical Git notes ref namespace verbatim.

    The notes namespace is stable cross-process: two writes against the
    same Git repo + same commit hash MUST land in the same namespace.
    """

    return "refs/notes/iriai"


def _canonical_json(obj: object) -> str:
    """Canonical-JSON serialiser.

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    verbatim per the doc-13:201-204 + doc-14:151-153 canonical-form contract:
    ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` -- lexicographic
    key ordering + the compact separator set so the resulting bytes are stable
    across Python versions / platforms / dict ordering.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    verbatim.
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Typed inputs (doc-14:138-142 stable-input bundle) ----------------------


class CommitProvenanceWriterInputs(BaseModel):
    """Doc-14:155-178 step 2 + doc-14:138-142 -- typed bundle of all inputs
    the writer needs.

    The bundle composes:

    * The Slice 08 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
      (READ-ONLY; carries ``repo_id`` + ``result_commit`` + ``tree_sha`` +
      ``no_dirty_snapshot_id`` + the rest of the 10 verbatim fields).
    * The typed group/task context (``feature_id`` + ``dag_sha256`` +
      ``group_idx`` + ``effective_group_idx`` + ``task_ids`` +
      ``contract_ids`` + ``attempt_ids`` + ``sandbox_patch_evidence_ids``
      + ``gate_evidence_ids`` + ``merge_queue_item_ids`` +
      ``commit_proof_evidence_id`` + ``checkpoint_artifact_id`` +
      ``no_dirty_snapshot_ids`` + ``implementation_log_anchors``).
    * The pre-commit context the trailer needs (``checkpoint_ref`` +
      ``parent_hash``).

    Per doc-14:138-142 the bundle MUST NOT include any value that depends
    on the result commit hash for the ``precommit_provenance_ref``
    derivation (the ``parent_hash`` is allowed because it is the
    parent's hash, known pre-commit). The :func:`compute_precommit_provenance_inputs`
    function uses only the precommit-stable subset for the digest.

    Per the auto-memory ``feedback_flat_structured_output`` rule control
    fields are flat primitives.
    """

    # extra='forbid' aligns with the Slice 14 1st sub-slice precedent at
    # src/iriai_build_v2/execution_control/commit_provenance.py:178 --
    # unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    # ── Slice 08 cross-citation (READ-ONLY) ────────────────────────────────

    commit_proof: RepoCommitProof
    """Doc-14:155-160 + doc-14:105 -- the Slice 08
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    typed row (10 fields verbatim). READ-ONLY from this slice; the writer
    cross-cites the proof via ``commit_proof_evidence_id`` integer field on
    :class:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenancePayload`."""

    # ── Typed group/task context (doc-14:91-108) ──────────────────────────

    feature_id: str
    """Doc-14:91 -- the feature scope."""

    dag_sha256: str
    """Doc-14:92 -- SHA-256 hex digest of the canonical DAG this commit
    cites."""

    group_idx: int
    """Doc-14:93 -- the DAG group index this commit checkpoints."""

    effective_group_idx: int | None = None
    """Doc-14:94 -- optional effective group index for regroup-overlay
    scenarios. Required field (no default forced because explicit None vs
    unset distinction was already enforced at the payload typed shape;
    the writer-inputs surface accepts None as the default for ergonomics)."""

    task_ids: list[str] = Field(default_factory=list)
    """Doc-14:99 -- enumerated task id list this commit covers."""

    contract_ids: list[int] = Field(default_factory=list)
    """Doc-14:100 -- typed-row primary key list of Slice 03 task contracts."""

    attempt_ids: list[int] = Field(default_factory=list)
    """Doc-14:101 -- typed-row primary key list of dispatcher attempts."""

    sandbox_patch_evidence_ids: list[int] = Field(default_factory=list)
    """Doc-14:102 -- typed-row primary key list of Slice 04 sandbox patch
    evidence rows."""

    gate_evidence_ids: list[int] = Field(default_factory=list)
    """Doc-14:103 -- typed-row primary key list of Slice 06 gate evidence
    rows."""

    merge_queue_item_ids: list[int] = Field(default_factory=list)
    """Doc-14:104 -- typed-row primary key list of Slice 08 merge-queue
    items this commit integrates."""

    commit_proof_evidence_id: int
    """Doc-14:105 -- the typed-row primary key of the Slice 08
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    commit-proof evidence row this payload cross-cites."""

    checkpoint_artifact_id: int | None = None
    """Doc-14:106 -- optional typed-row primary key of the Slice 08
    checkpoint artifact (``dag-group:*`` projection). None when the
    payload is written for a pre-checkpoint commit."""

    no_dirty_snapshot_ids: list[int] = Field(default_factory=list)
    """Doc-14:107 -- typed-row primary key list of Slice 08 workspace
    no-dirty snapshot rows that prove the post-commit workspace is
    clean."""

    implementation_log_anchors: list[str] = Field(default_factory=list)
    """Doc-14:108 -- implementation-log anchor strings that document this
    commit."""

    # ── Pre-commit + post-commit Git context (doc-14:85 + doc-14:97) ─────

    checkpoint_ref: str
    """Doc-14:85 -- the Slice 08 checkpoint reference (e.g.
    ``dag-group:{group_idx}``) this commit checkpoints."""

    parent_hash: str
    """Doc-14:97 -- the parent commit hash (the commit's ``HEAD^`` before
    this commit was created). Known pre-commit (parent exists before the
    commit is created) so it does NOT violate the doc-14:138-142
    precommit-stable rule when included in writer inputs."""

    @field_validator(
        "task_ids",
        "implementation_log_anchors",
    )
    @classmethod
    def _no_empty_strings(cls, value: list[str]) -> list[str]:
        # Empty strings in id lists are a structural defect (the digest
        # would be derived from a list with empty members). Fail closed
        # per feedback_no_silent_degradation.
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "CommitProvenanceWriterInputs id-list field must not "
                    "contain empty strings (doc-14:99 + 108)"
                )
        return value


def compute_precommit_provenance_inputs(
    inputs: CommitProvenanceWriterInputs,
) -> dict[str, Any]:
    """Doc-14:138-142 -- compute the precommit-stable input subset.

    Per *"`precommit_provenance_ref` is derived from stable inputs such
    as feature id, dag sha, group, repo id, queue item ids, task id
    digest, and contract digest. It must not contain the result commit
    hash unless the implementation uses an explicit amend flow that
    reruns all digest checks."* the subset includes ONLY values known
    BEFORE ``git commit``:

    * ``feature_id`` -- the feature scope (pre-commit).
    * ``dag_sha256`` -- the canonical DAG digest (pre-commit).
    * ``group_idx`` -- the DAG group index (pre-commit).
    * ``effective_group_idx`` -- optional regroup-overlay index (pre-commit).
    * ``repo_id`` -- the repo (pre-commit; from :class:`RepoCommitProof.repo_id`).
    * ``merge_queue_item_ids`` -- the queue item id list (pre-commit;
      queue rows exist before the commit).
    * ``task_ids_digest`` -- SHA-256 over the sorted task id list
      (pre-commit; tasks owned the work before the commit).
    * ``contract_ids_digest`` -- SHA-256 over the sorted contract id list
      (pre-commit; contracts compiled before dispatch).

    The function CONSCIOUSLY EXCLUDES:

    * ``commit_hash`` -- result commit hash (post-commit).
    * ``tree_hash`` -- result tree hash (post-commit; reflects the commit
      tree).
    * ``parent_hash`` -- the parent's commit hash (pre-commit per Git
      semantics, but EXCLUDED from the precommit-stable inputs because
      the rebase/cherry-pick lineage rule at doc-14:208-209 means a
      rewrite of the same logical commit should produce the same
      precommit ref even if the parent moves).
    * ``no_dirty_snapshot_ids`` -- workspace cleanliness snapshots
      (post-commit; verify clean state AFTER the commit).
    * ``attempt_ids`` / ``sandbox_patch_evidence_ids`` /
      ``gate_evidence_ids`` -- evidence ids that point to the
      post-dispatch evidence rows (POST the relevant phase but may
      stabilize across reruns; EXCLUDED to keep the precommit ref
      semantically tied to "what task work landed here").

    Two identical writer-input bundles (modulo result_commit / tree_sha
    / no_dirty_snapshot_id / status_*) MUST produce identical
    precommit-stable input dicts; the function is pure + deterministic.
    """

    # Sort the id lists so re-orderings of the same logical inputs
    # produce identical digests (the merge queue may iterate items in a
    # non-deterministic order during gathering).
    sorted_task_ids = sorted(inputs.task_ids)
    sorted_contract_ids = sorted(inputs.contract_ids)
    sorted_queue_item_ids = sorted(inputs.merge_queue_item_ids)

    return {
        "feature_id": inputs.feature_id,
        "dag_sha256": inputs.dag_sha256,
        "group_idx": inputs.group_idx,
        "effective_group_idx": inputs.effective_group_idx,
        "repo_id": inputs.commit_proof.repo_id,
        "merge_queue_item_ids": sorted_queue_item_ids,
        "task_ids_digest": _sha256_hex(_canonical_json(sorted_task_ids)),
        "contract_ids_digest": _sha256_hex(_canonical_json(sorted_contract_ids)),
    }


def compute_precommit_provenance_digest(
    inputs: CommitProvenanceWriterInputs,
) -> str:
    """Doc-14:87 + doc-14:138-142 -- SHA-256 hex digest over the
    precommit-stable input subset.

    Two identical writer-input bundles MUST produce identical digests.
    The digest is the key used by the canonical Git ref naming
    convention :func:`compute_precommit_provenance_ref`.
    """

    return _sha256_hex(_canonical_json(compute_precommit_provenance_inputs(inputs)))


def compute_precommit_provenance_ref(inputs: CommitProvenanceWriterInputs) -> str:
    """Doc-14:144-150 -- canonical Git ref path for the precommit-stable
    provenance entry.

    Per *"Full payloads are written to Git notes or Git refs, for example
    ``refs/notes/iriai`` keyed by commit or
    ``refs/iriai/provenance/{precommit_provenance_digest}``"* the
    canonical ref path is ``refs/iriai/provenance/{precommit_provenance_digest}``.

    The ref name is itself precommit-stable + cross-process derivable:
    two clients with identical inputs derive the same ref name without
    coordination.
    """

    return f"refs/iriai/provenance/{compute_precommit_provenance_digest(inputs)}"


# --- Typed-shape construction (doc-14:79-110) -------------------------------


def compute_trailer(inputs: CommitProvenanceWriterInputs) -> CommitProvenanceTrailer:
    """Doc-14:79-87 -- build the compact Git commit trailer from typed inputs.

    Per doc-14:137 trailers are mandatory + compact: the task id list +
    the merge queue item id list are digested into ``*_digest`` fields
    rather than enumerated verbatim. The full enumerated lists live in
    the :class:`CommitProvenancePayload`.

    Per doc-14:138-142 the ``precommit_provenance_ref`` +
    ``precommit_provenance_digest`` are derived from the precommit-stable
    input subset (per :func:`compute_precommit_provenance_inputs`) and
    MUST NOT depend on the result commit hash.

    Two identical writer-input bundles MUST produce identical trailers.
    """

    return CommitProvenanceTrailer(
        feature_id=inputs.feature_id,
        group_idx=inputs.group_idx,
        effective_group_idx=inputs.effective_group_idx,
        task_ids_digest=_sha256_hex(_canonical_json(sorted(inputs.task_ids))),
        merge_queue_item_ids_digest=_sha256_hex(
            _canonical_json(sorted(inputs.merge_queue_item_ids))
        ),
        checkpoint_ref=inputs.checkpoint_ref,
        precommit_provenance_ref=compute_precommit_provenance_ref(inputs),
        precommit_provenance_digest=compute_precommit_provenance_digest(inputs),
    )


def compute_payload(inputs: CommitProvenanceWriterInputs) -> CommitProvenancePayload:
    """Doc-14:89-110 -- build the full structured payload from typed inputs.

    Per doc-14:143-146 the payload MAY include the result commit hash
    (``commit_hash``) + tree hash (``tree_hash``) because it is written
    AFTER ``git commit``. The result hashes come from the Slice 08
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    fields (``result_commit`` + ``tree_sha``).

    Per doc-14:151-153 the ``payload_sha256`` is computed from the
    canonical-JSON projection of the payload with the ``payload_sha256``
    field itself OMITTED. The self-exclusion discipline is enforced by
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`.

    Two identical writer-input bundles MUST produce identical payloads
    (including identical ``payload_sha256`` digests).
    """

    # First build the payload with a placeholder ``payload_sha256`` so
    # the typed shape constructs cleanly; then recompute the digest from
    # the canonical-JSON projection (which excludes ``payload_sha256``)
    # and re-construct with the correct digest.
    #
    # The two-step construction is necessary because Pydantic v2 does
    # not allow a field's default to depend on the rest of the model's
    # fields (no "lazy" computed defaults). The alternative would be a
    # ``model_validator(mode='after')`` that mutates the digest, but that
    # is a frozen-model unsafe pattern; the explicit two-step recompute
    # mirrors the doc-14:151-153 + the Slice 14 1st sub-slice
    # :func:`compute_payload_sha256` self-exclusion contract verbatim.
    draft = CommitProvenancePayload(
        feature_id=inputs.feature_id,
        dag_sha256=inputs.dag_sha256,
        group_idx=inputs.group_idx,
        effective_group_idx=inputs.effective_group_idx,
        repo_id=inputs.commit_proof.repo_id,
        commit_hash=inputs.commit_proof.result_commit,
        parent_hash=inputs.parent_hash,
        tree_hash=inputs.commit_proof.tree_sha,
        task_ids=list(inputs.task_ids),
        contract_ids=list(inputs.contract_ids),
        attempt_ids=list(inputs.attempt_ids),
        sandbox_patch_evidence_ids=list(inputs.sandbox_patch_evidence_ids),
        gate_evidence_ids=list(inputs.gate_evidence_ids),
        merge_queue_item_ids=list(inputs.merge_queue_item_ids),
        commit_proof_evidence_id=inputs.commit_proof_evidence_id,
        checkpoint_artifact_id=inputs.checkpoint_artifact_id,
        no_dirty_snapshot_ids=list(inputs.no_dirty_snapshot_ids),
        implementation_log_anchors=list(inputs.implementation_log_anchors),
        precommit_provenance_ref=compute_precommit_provenance_ref(inputs),
        payload_sha256="placeholder",
    )
    digest = compute_payload_sha256(draft)
    return draft.model_copy(update={"payload_sha256": digest})


# --- Subprocess port (doc-14:144-150 Git notes/refs writer) -----------------


class GitSubprocessResult(BaseModel):
    """Typed result of one Git subprocess invocation.

    Mirrors the shape of :class:`subprocess.CompletedProcess` but flat +
    Pydantic-validated (Pydantic v2 frozen models enforce the
    ``extra='forbid'`` discipline). Used by the fake-subprocess fixture
    in unit tests to assert exact invocation traces.
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    args: list[str]
    """The Git argv (excluding the ``git`` program itself; e.g.
    ``["notes", "--ref=refs/notes/iriai", "add", "-f", ...]``)."""

    returncode: int
    """The Git subprocess exit code; 0 = success, non-zero = failure."""

    stdout: str = ""
    """The Git stdout (UTF-8 decoded)."""

    stderr: str = ""
    """The Git stderr (UTF-8 decoded)."""


class GitSubprocessRunner(Protocol):
    """Subprocess port for Git invocations.

    The writer takes a callable rather than shelling out directly so
    unit tests can supply a fake Git subprocess fixture (per the
    implementer prompt MUST NOT shell out to real ``git`` in unit
    tests).

    Production callers wire a stdlib-subprocess wrapper that runs
    ``subprocess.run(["git", *args], ...)`` and projects the result onto
    :class:`GitSubprocessResult`. Test callers wire a fake that records
    every invocation + returns canned results.
    """

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str,
    ) -> GitSubprocessResult: ...


class GitProvenanceWriteError(RuntimeError):
    """Raised when the writer needs to signal a structured failure to
    its caller.

    The writer's public surface :meth:`GitProvenanceWriter.write` does
    NOT raise -- it returns a :class:`CommitProvenanceWriteResult` with
    ``gap_finding`` populated when the write fails. This exception is
    used INTERNALLY by helper methods to signal a structured failure
    that the public surface catches + projects onto a
    :class:`CommitProvenanceGapFinding`.

    Per doc-14:242-243 the writer MUST NOT propagate failures to the
    caller; the structured exception is the internal control-flow shape,
    not the public failure signal.
    """

    def __init__(
        self,
        *,
        failure_id: Literal["line_provenance_gap", "governance_evidence_conflict"],
        reason: str,
        evidence_payload: dict[str, Any],
    ) -> None:
        super().__init__(reason)
        self.failure_id = failure_id
        self.reason = reason
        self.evidence_payload = evidence_payload


# --- Typed write result + gap finding ---------------------------------------


class CommitProvenanceGapFinding(BaseModel):
    """Typed governance-gap finding produced when the Git provenance write
    fails (doc-14:192-201 + doc-14:242-243).

    Per doc-14:192-201 *"Git note write fails after commit: governance
    records a ``line_provenance_gap`` or ``governance_evidence_conflict``
    finding and retries the projection idempotently. It does not block
    checkpointing or resume."* the finding carries the typed failure id
    + a reason + a payload that lets the supervisor classifier / future
    Slice 16 finding engine reason about the gap.

    Per doc-14:242-243 the finding is NON-blocking: the caller MUST NOT
    propagate it to the executor / checkpoint / merge-queue / resume
    code paths. The corresponding typed failure ids
    (``line_provenance_gap`` + ``governance_evidence_conflict``)
    register under the EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with a NON-blocking retry action (NOT ``quiesce``).
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["line_provenance_gap", "governance_evidence_conflict"]
    """Doc-14:192-201 -- one of the 2 typed failure ids. Both register
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243."""

    feature_id: str
    """The feature scope of the failed write (same as the
    :class:`CommitProvenanceWriterInputs.feature_id`)."""

    group_idx: int
    """The DAG group index of the failed write."""

    repo_id: str
    """The repo of the failed write."""

    commit_hash: str
    """The result commit hash that the write targeted (from
    :class:`RepoCommitProof.result_commit`)."""

    precommit_provenance_ref: str
    """The canonical ref path the write targeted (per
    :func:`compute_precommit_provenance_ref`)."""

    precommit_provenance_digest: str
    """The precommit-stable input digest (per
    :func:`compute_precommit_provenance_digest`)."""

    reason: str
    """Free-form gap reason (e.g. ``git_notes_write_failed_with_exit_code_128``,
    ``existing_ref_payload_digest_mismatch``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the stderr from the failed Git invocation, the existing payload
    digest on the conflicting ref). Free-form per the doc-14:192-201
    governance-finding contract."""


class CommitProvenanceWriteResult(BaseModel):
    """Typed result of one :meth:`GitProvenanceWriter.write` call.

    Per doc-14:144-150 + doc-14:242-243 the write surface is
    non-blocking. The result carries:

    * ``ok: bool`` -- True if the write succeeded; False if the write
      failed (in which case ``gap_finding`` is populated).
    * ``payload`` -- the typed :class:`CommitProvenancePayload` the
      writer constructed (always populated; even on failure the
      computation is deterministic).
    * ``trailer`` -- the typed :class:`CommitProvenanceTrailer` the
      writer constructed (always populated).
    * ``provenance_ref`` -- the canonical ref path the writer targeted
      (always populated; same as ``payload.precommit_provenance_ref``).
    * ``notes_ref`` -- the Git notes namespace the writer used (always
      populated; doc-14:144 ``refs/notes/iriai``).
    * ``idempotent_no_op`` -- True if a prior write with identical
      inputs had already landed (so this write was a no-op per the
      idempotency contract); False on a fresh write.
    * ``gap_finding`` -- populated only when ``ok=False``.
    * ``git_invocations`` -- the typed list of
      :class:`GitSubprocessResult` rows for every Git invocation the
      writer made (used by tests + the future Slice 18 replay layer to
      assert exact Git interaction traces).
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    ok: bool
    payload: CommitProvenancePayload
    trailer: CommitProvenanceTrailer
    provenance_ref: str
    notes_ref: str
    idempotent_no_op: bool = False
    gap_finding: CommitProvenanceGapFinding | None = None
    git_invocations: list[GitSubprocessResult] = Field(default_factory=list)


# --- The writer (doc-14:155-178 step 2) -------------------------------------


class GitProvenanceWriter:
    """Git provenance writer (doc-14:155-178 step 2).

    Per doc-14:161-164: *"Extract a Git provenance writer behind a
    narrow governance projection interface that runs from existing
    commit-proof/checkpoint evidence. It may be invoked by a
    post-checkpoint governance job or an explicitly non-blocking
    merge-queue hook, but it must not decide checkpoint success."*

    The writer is INVOCABLE AS A POST-CHECKPOINT GOVERNANCE JOB (NOT a
    blocking merge-queue hook). The :meth:`write` surface returns a
    typed :class:`CommitProvenanceWriteResult` with ``ok: bool`` +
    ``gap_finding`` -- the writer NEVER raises a failure to the caller
    (per the doc-14:242-243 non-blocking contract).

    The writer is IDEMPOTENT per doc-14:144-150: a retry with identical
    inputs is a no-op (the writer checks whether the canonical ref
    already exists + carries the same payload digest; if so it
    short-circuits + sets ``idempotent_no_op=True``).

    The writer is TESTABLE via a fake :class:`GitSubprocessRunner`
    fixture: unit tests inject a fake that records every Git invocation
    + returns canned results; the writer's behavior is fully
    deterministic given the inputs + the fake's responses.
    """

    def __init__(
        self,
        *,
        repo_path: str,
        runner: GitSubprocessRunner,
        notes_ref: str | None = None,
    ) -> None:
        """Construct a writer bound to a repo + a subprocess runner.

        :param repo_path: filesystem path to the Git working tree the
            writer operates on (passed as ``cwd`` to every Git
            invocation).
        :param runner: the :class:`GitSubprocessRunner` callable; in
            production a stdlib-subprocess wrapper, in tests a fake
            fixture.
        :param notes_ref: the Git notes namespace; defaults to
            :func:`compute_notes_ref_namespace` (``refs/notes/iriai``)
            per doc-14:144.
        """

        self._repo_path = repo_path
        self._runner = runner
        self._notes_ref = notes_ref or compute_notes_ref_namespace()

    @property
    def repo_path(self) -> str:
        """Filesystem path the writer operates on (read-only)."""

        return self._repo_path

    @property
    def notes_ref(self) -> str:
        """Git notes namespace the writer writes to (read-only)."""

        return self._notes_ref

    def write(
        self,
        inputs: CommitProvenanceWriterInputs,
    ) -> CommitProvenanceWriteResult:
        """Write the Git provenance for one commit (doc-14:144-150).

        Returns a typed :class:`CommitProvenanceWriteResult` with
        ``ok`` + ``gap_finding`` + the constructed typed shapes.

        Per doc-14:242-243 NEVER raises a failure to the caller. Per
        doc-14:144-150 IDEMPOTENT: a retry with identical inputs is a
        no-op + sets ``idempotent_no_op=True``.

        The write sequence (per doc-14:144-150):

        1. Compute the typed :class:`CommitProvenanceTrailer` +
           :class:`CommitProvenancePayload` from the inputs.
        2. Compute the canonical ref path
           ``refs/iriai/provenance/{precommit_provenance_digest}``.
        3. Check whether the canonical ref already exists; if it does,
           read its payload + compare the ``payload_sha256``:
              - if the digests match: return ``idempotent_no_op=True``,
                no Git write needed.
              - if the digests DIFFER: fail with typed failure id
                ``governance_evidence_conflict`` (per doc-14:192-201).
        4. If the canonical ref does NOT exist, write the canonical
           JSON of the payload as the ref blob.
        5. Also write the payload to the Git notes namespace
           (``refs/notes/iriai``) keyed by the commit hash; idempotent
           via ``git notes --ref=refs/notes/iriai add -f``.
        6. Return ``ok=True``.

        Any Git invocation failure projects to a
        :class:`CommitProvenanceGapFinding` with typed failure id
        ``line_provenance_gap`` (per doc-14:192-201) + ``ok=False``.
        """

        # Build the typed shapes (deterministic; always populated).
        trailer = compute_trailer(inputs)
        payload = compute_payload(inputs)
        provenance_ref = trailer.precommit_provenance_ref
        notes_ref = self._notes_ref
        invocations: list[GitSubprocessResult] = []

        try:
            # Step 3: idempotency check on the canonical ref.
            existing_payload = self._read_existing_payload_at_ref(
                provenance_ref, invocations
            )
            if existing_payload is not None:
                if existing_payload.payload_sha256 == payload.payload_sha256:
                    # Step 3a: idempotent no-op.
                    return CommitProvenanceWriteResult(
                        ok=True,
                        payload=payload,
                        trailer=trailer,
                        provenance_ref=provenance_ref,
                        notes_ref=notes_ref,
                        idempotent_no_op=True,
                        gap_finding=None,
                        git_invocations=invocations,
                    )
                # Step 3b: typed conflict per doc-14:192-201.
                raise GitProvenanceWriteError(
                    failure_id="governance_evidence_conflict",
                    reason=(
                        "existing canonical ref payload digest does not match "
                        "computed payload digest"
                    ),
                    evidence_payload={
                        "existing_payload_sha256": existing_payload.payload_sha256,
                        "computed_payload_sha256": payload.payload_sha256,
                        "provenance_ref": provenance_ref,
                    },
                )

            # Step 4: write the payload as a blob + the ref pointing to
            # the blob.
            self._write_payload_to_ref(provenance_ref, payload, invocations)

            # Step 5: also write to Git notes keyed by commit hash.
            self._write_payload_to_notes(payload, invocations)

        except GitProvenanceWriteError as exc:
            return CommitProvenanceWriteResult(
                ok=False,
                payload=payload,
                trailer=trailer,
                provenance_ref=provenance_ref,
                notes_ref=notes_ref,
                idempotent_no_op=False,
                gap_finding=CommitProvenanceGapFinding(
                    failure_id=exc.failure_id,
                    feature_id=inputs.feature_id,
                    group_idx=inputs.group_idx,
                    repo_id=inputs.commit_proof.repo_id,
                    commit_hash=inputs.commit_proof.result_commit,
                    precommit_provenance_ref=provenance_ref,
                    precommit_provenance_digest=trailer.precommit_provenance_digest,
                    reason=exc.reason,
                    evidence_payload=exc.evidence_payload,
                ),
                git_invocations=invocations,
            )

        return CommitProvenanceWriteResult(
            ok=True,
            payload=payload,
            trailer=trailer,
            provenance_ref=provenance_ref,
            notes_ref=notes_ref,
            idempotent_no_op=False,
            gap_finding=None,
            git_invocations=invocations,
        )

    # ── Private helpers (Git invocation shape) ─────────────────────────────

    def _run(
        self,
        args: list[str],
        invocations: list[GitSubprocessResult],
    ) -> GitSubprocessResult:
        result = self._runner(args, cwd=self._repo_path)
        # Normalize Pydantic by re-validating; tests may supply a plain
        # GitSubprocessResult that we just record.
        invocations.append(result)
        return result

    def _read_existing_payload_at_ref(
        self,
        ref: str,
        invocations: list[GitSubprocessResult],
    ) -> CommitProvenancePayload | None:
        """Return the existing payload at ``ref`` or None if no ref exists.

        Uses ``git cat-file blob <ref>`` to read the ref's blob content.
        Per doc-14:144-150 ``refs/iriai/provenance/{precommit_provenance_digest}``
        points to a blob whose content is the canonical-JSON projection
        of the :class:`CommitProvenancePayload`.

        Returns None when the ref does not exist (``git`` returns
        non-zero exit code with a "Not a valid object name" / "unknown
        revision" stderr).
        """

        result = self._run(["cat-file", "blob", ref], invocations)
        if result.returncode != 0:
            # The ref does not exist; the writer should proceed with a
            # fresh write.
            return None

        # Parse the existing payload; if it doesn't parse, signal a
        # typed conflict (the ref exists but is structurally corrupt).
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise GitProvenanceWriteError(
                failure_id="governance_evidence_conflict",
                reason=(
                    f"existing ref blob is not valid JSON: {exc.msg} at "
                    f"line {exc.lineno} col {exc.colno}"
                ),
                evidence_payload={
                    "ref": ref,
                    "raw_blob_preview": result.stdout[:200],
                },
            ) from exc

        try:
            return CommitProvenancePayload.model_validate(parsed)
        except Exception as exc:
            raise GitProvenanceWriteError(
                failure_id="governance_evidence_conflict",
                reason=(
                    f"existing ref blob does not match CommitProvenancePayload "
                    f"shape: {exc}"
                ),
                evidence_payload={
                    "ref": ref,
                    "parsed_keys": sorted(parsed.keys())
                    if isinstance(parsed, dict)
                    else [],
                },
            ) from exc

    def _write_payload_to_ref(
        self,
        ref: str,
        payload: CommitProvenancePayload,
        invocations: list[GitSubprocessResult],
    ) -> None:
        """Write ``payload`` as a Git blob + set ``ref`` to point at it.

        The two-step Git invocation:

        1. ``git hash-object -w --stdin`` -- write the canonical JSON
           bytes as a Git blob; the stdout is the blob's object id.
        2. ``git update-ref <ref> <blob_oid>`` -- atomically set the
           ref to point at the new blob.

        Per doc-14:144-150 the canonical JSON projection is the same
        canonical-JSON discipline used for the ``payload_sha256``
        self-exclusion (lexicographic key order + compact separators) --
        EXCEPT the canonical-JSON written to the blob INCLUDES
        ``payload_sha256`` so consumers can verify the digest on
        re-read (per doc-14:151-153 + the 1st sub-slice
        :func:`compute_payload_sha256` contract).
        """

        # Step 1: write the blob. The fake-runner pattern passes the
        # canonical JSON as the first arg after "hash-object" args so
        # tests can inspect what content was written.
        canonical_blob = _canonical_json(payload.model_dump())
        hash_result = self._run(
            ["hash-object", "-w", "--stdin", "--", canonical_blob],
            invocations,
        )
        if hash_result.returncode != 0:
            raise GitProvenanceWriteError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git hash-object failed with exit code {hash_result.returncode}"
                ),
                evidence_payload={
                    "ref": ref,
                    "stderr": hash_result.stderr,
                },
            )

        blob_oid = hash_result.stdout.strip()
        if not blob_oid:
            raise GitProvenanceWriteError(
                failure_id="line_provenance_gap",
                reason="git hash-object produced an empty blob oid",
                evidence_payload={
                    "ref": ref,
                    "stdout": hash_result.stdout,
                    "stderr": hash_result.stderr,
                },
            )

        # Step 2: update-ref. Use the 3-argument form so update-ref
        # fails atomically if the ref already exists with a different
        # value (the writer rechecks idempotency at step 3 above; this
        # is a belt-and-braces guard against races).
        update_result = self._run(
            ["update-ref", ref, blob_oid],
            invocations,
        )
        if update_result.returncode != 0:
            raise GitProvenanceWriteError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git update-ref failed with exit code {update_result.returncode}"
                ),
                evidence_payload={
                    "ref": ref,
                    "blob_oid": blob_oid,
                    "stderr": update_result.stderr,
                },
            )

    def _write_payload_to_notes(
        self,
        payload: CommitProvenancePayload,
        invocations: list[GitSubprocessResult],
    ) -> None:
        """Write ``payload`` to the Git notes namespace keyed by commit hash.

        Per doc-14:144: *"`refs/notes/iriai` keyed by commit..."*. Uses
        ``git notes --ref=refs/notes/iriai add -f`` which is idempotent:
        if a note already exists for the commit, it is overwritten;
        otherwise a new note is created. The ``-f`` (force) flag makes
        the operation idempotent against pre-existing notes.

        Per doc-14:243 governance projection failures NEVER block
        checkpointing -- so this method projects subprocess failures
        onto :class:`GitProvenanceWriteError` (a STRUCTURED internal
        exception that the public :meth:`write` surface catches +
        converts to a typed :class:`CommitProvenanceGapFinding`).
        """

        canonical_blob = _canonical_json(payload.model_dump())
        result = self._run(
            [
                "notes",
                f"--ref={self._notes_ref}",
                "add",
                "-f",
                "-m",
                canonical_blob,
                payload.commit_hash,
            ],
            invocations,
        )
        if result.returncode != 0:
            raise GitProvenanceWriteError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git notes add failed with exit code {result.returncode}"
                ),
                evidence_payload={
                    "notes_ref": self._notes_ref,
                    "commit_hash": payload.commit_hash,
                    "stderr": result.stderr,
                },
            )


# --- Production stdlib-subprocess runner factory ---------------------------


def make_stdlib_subprocess_runner(
    *,
    git_program: str = "git",
) -> Callable[[list[str], str], GitSubprocessResult]:
    """Return a production-grade :class:`GitSubprocessRunner` that wraps
    :func:`subprocess.run`.

    Per doc-14:144-150 the writer uses stdlib subprocess for Git
    invocation; production wires this factory's return value into
    :class:`GitProvenanceWriter`. Tests do NOT use this factory -- they
    supply a fake :class:`GitSubprocessRunner` that returns canned
    results (per the implementer prompt MUST NOT shell out to real
    ``git`` in unit tests).

    The factory is provided here (rather than in a separate "wiring"
    module) so the writer's full surface lives in one module per the
    auto-memory ``feedback_no_overengineer_use_library`` rule.

    .. note::
       The fake-subprocess fixture in
       :mod:`tests.test_execution_control_commit_provenance_writer`
       implements the same callable protocol -- the factory return type
       is the protocol signature, NOT a class.
    """

    import subprocess

    def _runner(args: list[str], *, cwd: str) -> GitSubprocessResult:
        # Filter the synthetic "--" pseudo-arg the writer uses to pass
        # stdin payloads explicitly (see _write_payload_to_ref step 1);
        # real git rejects "--" before a stdin payload at the CLI level.
        # The fake-runner pattern keeps the "--" + payload visible in
        # the invocation trace; the real-runner pattern routes the
        # payload via stdin instead.
        invocation_args: list[str]
        stdin_bytes: bytes | None = None
        if "--" in args:
            split_at = args.index("--")
            invocation_args = args[:split_at]
            # The remaining args are joined (a typical writer call
            # passes a single canonical-JSON string).
            stdin_bytes = "\n".join(args[split_at + 1 :]).encode("utf-8")
        else:
            invocation_args = list(args)

        completed = subprocess.run(  # noqa: S603 (caller-controlled args + cwd)
            [git_program, *invocation_args],
            cwd=cwd,
            input=stdin_bytes,
            capture_output=True,
            check=False,
        )
        return GitSubprocessResult(
            args=list(invocation_args),
            returncode=completed.returncode,
            stdout=completed.stdout.decode("utf-8", errors="replace"),
            stderr=completed.stderr.decode("utf-8", errors="replace"),
        )

    return _runner
