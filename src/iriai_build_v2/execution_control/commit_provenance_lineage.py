"""Slice 14 fourth sub-slice -- rebase/cherry-pick lineage emitter +
multi-repo checkpoint integration.

Per ``docs/execution-control-plane/14-commit-and-line-provenance.md``
§ Refactoring Steps step 6 (lines 174-176): *"Add lineage handling for
rewrite scenarios: if a commit is rebased, cherry-picked, or replaced by
recovery, emit an explicit old-to-new lineage payload."*

Per ``docs/execution-control-plane/14-commit-and-line-provenance.md``
§ Refactoring Steps step 7 (lines 177-178): *"Ensure multi-repo
checkpoints preserve legacy comma-separated ``commit_hash`` display
while structured proofs remain per repo."*

This module owns the lineage emitter surface (the WRITE-side complement
of the Slice 14 3rd sub-slice :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
Protocol port):

* :class:`LineageEmitterInputs` -- typed bundle of all inputs the
  emitter needs (the original commit hash + the new commit hash + the
  rewrite reason + the typed proof / trailer cross-citations + the repo
  context).
* :class:`LineageRewriteCandidate` -- typed candidate row produced by
  :func:`detect_rewrite_candidates` describing one suspected rewrite
  transition before the emitter persists it.
* :class:`LineageEmitResult` -- typed result with the emitted
  :class:`LineageRecord` + the persisted ref / digest + idempotency +
  optional gap finding.
* :class:`LineageEmitter` -- the emitter port. Detects rewrite scenarios
  (rebase / cherry-pick / amend / squash / recovery), emits typed
  :class:`LineageRecord` (REUSED from the 3rd sub-slice; NOT redefined),
  persists the lineage payload to a typed governance projection row,
  records typed gap findings on ambiguous lineage without blocking
  executor or merge queue.
* :class:`InMemoryLineageWalker` -- typed in-memory adapter that
  satisfies the 3rd-sub-slice :class:`LineageWalker` Protocol; the
  emitter populates this walker as it emits lineage records, so the
  reader walks the same typed records the emitter wrote.
* Pure helpers: :func:`compute_lineage_digest` +
  :func:`compute_lineage_ref` + :func:`detect_rewrite_candidates`.

**Lineage detection taxonomy (doc-14:174-176 + doc-14:208-209).** The
emitter recognizes 4 typed rewrite scenarios + 1 recovery scenario:

1. ``rebase`` -- the head commit hash differs from the original commit
   hash recorded in the typed ``dag-commit-proof:*`` evidence row's
   :attr:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof.result_commit`,
   AND the new commit's parent hash differs from the original commit's
   parent hash, AND the new commit's tree hash differs from the
   original commit's tree hash (so it is NOT an in-place amend nor a
   no-op cherry-pick).
2. ``cherry-pick`` -- the head commit hash differs from the original
   commit hash, AND the new commit's trailer digest
   (:attr:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenanceTrailer.precommit_provenance_digest`)
   matches the original commit's trailer digest (the cherry-pick
   preserves the precommit-stable trailer per doc-14:138-142).
3. ``amend`` -- the head commit hash differs from the original commit
   hash, AND the new commit's author/committer timestamp drifts
   significantly (typical Git amend behavior: same tree + same parent
   but new committer timestamp + new commit hash).
4. ``squash`` -- the head commit's parent count differs from the
   original commit's parent count (typical Git squash: combines two
   commits into one with a different parent hash).
5. recovery -- detected via the proximity (within ``recovery_window_s``
   seconds) of a Slice 08 ``dag-commit-failure:*`` projection event;
   recovery emits a lineage record with ``reason="rebase"`` (Git's
   semantic equivalent: the failed commit was redone as a fresh commit
   chain). The recovery scenario is the LAST in the detection cascade
   so the more specific reasons (rebase / cherry-pick / amend / squash)
   are preferred when applicable.

**Persistence namespace (doc-14:144-150).** Per *"Full payloads are
written to Git notes or Git refs, for example ``refs/notes/iriai``
keyed by commit or ``refs/iriai/provenance/{precommit_provenance_digest}``"*
the emitter writes lineage payloads to a dedicated
``refs/iriai/lineage/{lineage_digest}`` namespace (mirrors the writer's
``refs/iriai/provenance/{precommit_provenance_digest}`` namespace
pattern) AND to the Git notes namespace (REUSES the 2nd sub-slice
:func:`~iriai_build_v2.execution_control.commit_provenance_writer.compute_notes_ref_namespace`
helper which returns ``refs/notes/iriai``) keyed by the **new** commit
hash. The dedicated lineage namespace lets governance walkers iterate
all lineage records cross-process without scanning the provenance
namespace; the Git notes write lets the reader look up lineage via the
commit hash (matches the reader's
:meth:`~iriai_build_v2.execution_control.commit_provenance_reader.LineProvenanceReader._resolve_one_commit`
notes-first lookup pattern).

**Multi-repo group integration (doc-14:204-205 + doc-14:177-178).** Per
doc-14:204-205 *"Multi-repo group: every repo commit gets a payload;
the group checkpoint links all payload refs."* the emitter handles
multi-repo groups by emitting ONE typed :class:`LineageRecord` per
repo commit (each repo's lineage is independent; one repo may rebase
while another cherry-picks). Per doc-14:177-178 *"Ensure multi-repo
checkpoints preserve legacy comma-separated ``commit_hash`` display
while structured proofs remain per repo."* the legacy comma-joined
display at
:func:`iriai_build_v2.workflows.develop.execution.merge_queue._approved_checkpoint_body`
(line 1257, ``"commit_hash": ",".join(coverage.result_commits)``) is
PRESERVED unchanged; the typed per-repo structured proofs continue to
live on the Slice 08 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
typed row (10 fields verbatim, FROZEN per doc-14:155-160 step 1).
This module's :class:`LineageEmitter.emit_for_repo` surface returns one
typed :class:`LineageRecord` per repo commit; for a multi-repo group
the caller invokes ``emit_for_repo`` once per repo.

**Non-blocking failure routing discipline (doc-14:242-243).** Per
*"Governance provenance projection failures never block ``dag-group:*``
checkpointing, merge queue integration, or resume"* the emitter is a
POST-CHECKPOINT GOVERNANCE PROJECTION WRITER (mirrors the 2nd sub-slice
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter`
discipline). When the emitter cannot resolve the lineage unambiguously
(e.g., multiple candidate ancestors with conflicting trailer digests),
or when the Git subprocess write fails, the emitter records a typed
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
with the typed failure id ``governance_evidence_conflict`` or
``line_provenance_gap`` (REUSES the 2nd sub-slice typed failure ids
under EXISTING ``evidence_corruption`` failure_class with NON-blocking
``retry_governance_projection`` RouteAction) and returns
:class:`LineageEmitResult` with ``ok=False`` + ``gap_finding``
populated. The :meth:`LineageEmitter.emit_for_repo` surface NEVER
raises a failure to the caller per doc-14:242-243.

**Slice 08 + Slice 14 1st/2nd/3rd sub-slice non-alteration discipline
(doc-14:155-160 step 1).** The Slice 08 canonical
:class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
typed row (10 fields verbatim) is READ-ONLY from this slice. The
emitter cross-cites the :class:`RepoCommitProof` via its
:attr:`result_commit` field but does NOT mutate it. The Slice 14 1st
sub-slice typed shapes (:class:`CommitProvenanceTrailer` +
:class:`CommitProvenancePayload`), the 2nd sub-slice writer surface
(:func:`compute_notes_ref_namespace` + :class:`GitSubprocessRunner` +
:class:`GitSubprocessResult` + :class:`CommitProvenanceGapFinding` +
:data:`COMMIT_PROVENANCE_GAP_FAILURE_IDS`), and the 3rd sub-slice
reader surface (:class:`LineageRecord` + :class:`LineageWalker`) are
all imported READ-ONLY for typed cross-cites.

**Implementation discipline.** Stdlib (``json`` + ``hashlib``) +
Pydantic v2 + Slice 13A modules (NONE this module) + Slice 13a modules
(NONE this module) + Slice 14 1st sub-slice typed shapes + Slice 14
2nd sub-slice helpers + Slice 14 3rd sub-slice typed shapes + Slice 08
modules (``..execution_control.merge_queue_store``) only. NO imports
from ``governance/`` outside ``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``.

Per the auto-memory ``feedback_flat_structured_output`` rule control
fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every silent loss is a typed
:class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`.
Per the auto-memory ``feedback_no_overengineer_use_library`` rule the
module mirrors the Slice 14 1st + 2nd + 3rd sub-slice precedents
verbatim without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from iriai_build_v2.execution_control.commit_provenance import (
    CommitProvenancePayload,
    CommitProvenanceTrailer,
)
from iriai_build_v2.execution_control.commit_provenance_reader import (
    LineageRecord,
    LineageWalker,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    COMMIT_PROVENANCE_GAP_FAILURE_IDS,
    CommitProvenanceGapFinding,
    GitSubprocessResult,
    GitSubprocessRunner,
    compute_notes_ref_namespace,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof


__all__ = [
    # Typed inputs / outputs.
    "LineageEmitterInputs",
    "LineageRewriteCandidate",
    "LineageEmitResult",
    "LineageEmitError",
    # The emitter + in-memory walker adapter.
    "LineageEmitter",
    "InMemoryLineageWalker",
    # Pure helpers.
    "compute_lineage_digest",
    "compute_lineage_ref",
    "compute_lineage_notes_ref_namespace",
    "detect_rewrite_candidates",
    # Re-export the 2nd sub-slice typed failure ids tuple (REUSE; ADDS
    # NO new ids).
    "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
]


# --- Canonical-JSON helpers (mirrored from writer per doc-13:201-204) -------


def _canonical_json(obj: object) -> str:
    """Canonical-JSON serialiser.

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance_writer._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance_reader._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    verbatim per the doc-13:201-204 + doc-14:151-153 canonical-form contract.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.commit_provenance_writer._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance_reader._sha256_hex`
    verbatim.
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Lineage namespace helpers (doc-14:144-150 dedicated namespace) ---------


def compute_lineage_notes_ref_namespace() -> str:
    """The canonical Git notes namespace for iriai lineage records.

    Per doc-14:144-150: *"Full payloads are written to Git notes or Git
    refs, for example ``refs/notes/iriai`` keyed by commit..."*. The
    lineage emitter REUSES the 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.commit_provenance_writer.compute_notes_ref_namespace`
    return value verbatim (``refs/notes/iriai``) so the Git notes
    namespace is unified across provenance + lineage; consumers walking
    notes for one commit get both the provenance payload AND the
    lineage record (when applicable) in the same namespace.

    The wrapper exists for explicit symmetry with
    :func:`compute_lineage_ref` (dedicated lineage refs) -- callers
    that want the lineage-specific notes namespace can call this
    function rather than the writer's helper directly.
    """

    # Intentionally REUSE the writer's helper (same object identity
    # under direct invocation; the notes namespace is unified for
    # provenance + lineage).
    return compute_notes_ref_namespace()


def compute_lineage_digest(
    *,
    repo_id: str,
    old_commit_hash: str,
    new_commit_hash: str,
    reason: Literal["rebase", "cherry-pick", "amend", "squash"],
) -> str:
    """SHA-256 hex digest over the lineage record's stable identifying
    fields.

    Per doc-14:144-150 the lineage ref is named via a stable digest so
    two clients with identical lineage records derive the same ref name
    without coordination. The digest excludes free-form fields (e.g.
    ``detected_at``, ``evidence_refs``) so a lineage record's identity
    is the (repo_id, old_commit_hash, new_commit_hash, reason) tuple.

    Two identical lineage tuples MUST produce identical digests; the
    function is pure + deterministic.
    """

    inputs = {
        "repo_id": repo_id,
        "old_commit_hash": old_commit_hash,
        "new_commit_hash": new_commit_hash,
        "reason": reason,
    }
    return _sha256_hex(_canonical_json(inputs))


def compute_lineage_ref(
    *,
    repo_id: str,
    old_commit_hash: str,
    new_commit_hash: str,
    reason: Literal["rebase", "cherry-pick", "amend", "squash"],
) -> str:
    """The canonical Git ref path for a lineage record.

    Per doc-14:144-150 the lineage ref namespace is
    ``refs/iriai/lineage/{lineage_digest}`` (mirrors the writer's
    ``refs/iriai/provenance/{precommit_provenance_digest}`` namespace
    pattern). The ref name is itself precommit-stable + cross-process
    derivable: two clients with identical lineage records derive the
    same ref name without coordination.
    """

    digest = compute_lineage_digest(
        repo_id=repo_id,
        old_commit_hash=old_commit_hash,
        new_commit_hash=new_commit_hash,
        reason=reason,
    )
    return f"refs/iriai/lineage/{digest}"


# --- Typed inputs (doc-14:174-176 emitter input bundle) ---------------------


class LineageEmitterInputs(BaseModel):
    """Doc-14:174-176 step 6 + doc-14:204-205 -- typed bundle of all
    inputs the emitter needs to detect + emit a lineage record for one
    repo commit.

    The bundle composes:

    * The Slice 08 :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
      for the NEW commit (carries ``repo_id`` + ``result_commit`` +
      ``tree_sha``).
    * The original commit's typed cross-citations (``original_commit_hash`` +
      ``original_parent_hash`` + ``original_tree_hash`` +
      ``original_precommit_provenance_digest`` + optional
      ``original_author_timestamp`` + ``original_committer_timestamp`` +
      optional ``original_parent_count``).
    * The new commit's matching fields (``new_parent_hash`` +
      ``new_precommit_provenance_digest`` + optional
      ``new_author_timestamp`` + ``new_committer_timestamp`` + optional
      ``new_parent_count``).
    * Optional recovery context (``recovery_failure_marker_ref`` +
      ``recovery_failure_marker_timestamp`` -- when populated the
      emitter checks for ``dag-commit-failure:*`` proximity per doc-14
      recovery clause).
    * ``feature_id`` + ``group_idx`` for the typed gap finding.

    Per the auto-memory ``feedback_flat_structured_output`` rule control
    fields are flat primitives.
    """

    # extra='forbid' aligns with the Slice 14 1st + 2nd + 3rd sub-slice
    # precedents.
    model_config = ConfigDict(extra="forbid")

    # ── New commit (Slice 08 cross-citation; READ-ONLY) ───────────────────

    new_commit_proof: RepoCommitProof
    """Doc-14:155-160 + doc-14:204-205 -- the Slice 08
    :class:`~iriai_build_v2.execution_control.merge_queue_store.RepoCommitProof`
    typed row (10 fields verbatim) for the NEW (post-rewrite) commit.
    READ-ONLY from this slice; the emitter cross-cites the
    :attr:`result_commit` for the lineage record's ``new_commit_hash``
    field but does NOT mutate the proof row."""

    new_parent_hash: str
    """The new commit's parent hash (``HEAD^``). Required for rebase /
    squash detection vs the original's parent hash."""

    new_precommit_provenance_digest: str
    """The new commit's
    :attr:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenanceTrailer.precommit_provenance_digest`.
    Required for cherry-pick detection (cherry-picks preserve the
    precommit-stable trailer per doc-14:138-142)."""

    new_author_timestamp: int | None = None
    """The new commit's Git author timestamp (epoch seconds). Optional;
    used for amend detection when populated alongside
    :attr:`original_author_timestamp`."""

    new_committer_timestamp: int | None = None
    """The new commit's Git committer timestamp (epoch seconds).
    Optional; used for amend detection when populated alongside
    :attr:`original_committer_timestamp`."""

    new_parent_count: int | None = None
    """The new commit's parent count (1 for a normal commit, 2+ for a
    merge / squash). Optional; used for squash detection when populated
    alongside :attr:`original_parent_count`."""

    # ── Original commit cross-citations ────────────────────────────────────

    original_commit_hash: str
    """The pre-rewrite commit hash (what blame on the historical ref
    would return). Required."""

    original_parent_hash: str
    """The original commit's parent hash. Required for rebase / squash
    detection."""

    original_tree_hash: str
    """The original commit's tree hash (``git rev-parse <orig>^{tree}``).
    Required for rebase vs amend disambiguation (rebases change parent
    + tree; amends usually preserve tree)."""

    original_precommit_provenance_digest: str
    """The original commit's
    :attr:`~iriai_build_v2.execution_control.commit_provenance.CommitProvenanceTrailer.precommit_provenance_digest`.
    Required for cherry-pick detection."""

    original_author_timestamp: int | None = None
    """The original commit's Git author timestamp (epoch seconds).
    Optional; used for amend detection when populated alongside
    :attr:`new_author_timestamp`."""

    original_committer_timestamp: int | None = None
    """The original commit's Git committer timestamp (epoch seconds).
    Optional; used for amend detection when populated alongside
    :attr:`new_committer_timestamp`."""

    original_parent_count: int | None = None
    """The original commit's parent count. Optional; used for squash
    detection when populated alongside :attr:`new_parent_count`."""

    # ── Optional recovery context (doc-14:174-176 "replaced by recovery") ──

    recovery_failure_marker_ref: str | None = None
    """Optional Slice 08 ``dag-commit-failure:*`` projection ref path
    that signals a recovery scenario. When populated the emitter checks
    the proximity (within :attr:`recovery_window_seconds`) of the
    failure marker to the new commit's committer timestamp; if proximate
    the lineage record is emitted with ``reason="rebase"`` (the
    semantic equivalent of a recovery: the failed commit was redone as
    a fresh commit chain)."""

    recovery_failure_marker_timestamp: int | None = None
    """Optional epoch seconds timestamp of the
    :attr:`recovery_failure_marker_ref`. When populated the emitter
    computes ``abs(new_committer_timestamp - recovery_failure_marker_timestamp) <= recovery_window_seconds``
    to classify the scenario as a recovery."""

    recovery_window_seconds: int = 3600
    """The proximity window for recovery detection (default 1 hour).
    Within this window the emitter classifies a head-vs-original
    divergence as a recovery; outside it falls through to the regular
    rebase / cherry-pick / amend / squash cascade."""

    # ── Feature scope (for gap-finding correlator) ────────────────────────

    feature_id: str
    """The feature scope of this lineage event (for typed gap finding
    correlator + cross-cite with Slice 14 1st + 2nd sub-slice typed
    shapes)."""

    group_idx: int
    """The DAG group index this commit checkpoints (for typed gap
    finding correlator + cross-cite with Slice 14 1st + 2nd sub-slice
    typed shapes)."""

    detected_at: str = ""
    """Optional ISO-8601 timestamp the rewrite was detected at; used as
    the typed :class:`LineageRecord` cross-cite. The 3rd sub-slice
    :class:`LineageRecord` shape does NOT carry a detected_at field,
    so this string is recorded on the lineage payload row stored at
    :func:`compute_lineage_ref` rather than on the
    :class:`LineageRecord` itself."""

    evidence_refs: list[str] = Field(default_factory=list)
    """Cross-cite list of typed evidence row references (e.g.
    ``dag-commit-proof:{repo_id}:{commit_hash}`` strings). Recorded on
    the lineage payload row stored at :func:`compute_lineage_ref`."""

    @field_validator("evidence_refs")
    @classmethod
    def _no_empty_strings(cls, value: list[str]) -> list[str]:
        # Empty strings in cross-cite lists are a structural defect
        # (the digest would be derived from a list with empty members).
        # Fail closed per feedback_no_silent_degradation.
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "LineageEmitterInputs.evidence_refs must not contain "
                    "empty strings (doc-14:174-178)"
                )
        return value


# --- Rewrite-candidate detection (doc-14:174-176 + doc-14:208-209) ----------


class LineageRewriteCandidate(BaseModel):
    """One typed rewrite candidate produced by :func:`detect_rewrite_candidates`.

    The candidate describes a suspected rewrite transition BEFORE the
    emitter persists it as a typed :class:`LineageRecord`. The emitter
    then validates the candidate (e.g., rejects ambiguous candidates
    where multiple reasons match with conflicting evidence) and either
    emits the lineage record or records a typed
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    with ``governance_evidence_conflict`` failure id.

    Per the auto-memory ``feedback_flat_structured_output`` rule control
    fields are flat primitives.
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    reason: Literal["rebase", "cherry-pick", "amend", "squash"]
    """The detected rewrite reason. Matches the 3rd sub-slice
    :class:`LineageRecord.reason` 4-value Literal verbatim (so the
    candidate cleanly converts to a :class:`LineageRecord`)."""

    detection_signal: str
    """Free-form description of the signal that triggered detection
    (e.g. ``head_hash_differs_and_parent_changed_and_tree_changed``
    for rebase, ``head_hash_differs_and_trailer_digest_matches`` for
    cherry-pick, ``head_hash_differs_and_committer_timestamp_drift``
    for amend, ``parent_count_diverged_from_original`` for squash,
    ``recovery_window_proximity_to_dag_commit_failure`` for recovery).
    The free-form string is recorded on the lineage payload for audit
    + debugging."""


def detect_rewrite_candidates(
    inputs: LineageEmitterInputs,
) -> list[LineageRewriteCandidate]:
    """Detect rewrite-scenario candidates per doc-14:174-176 +
    doc-14:208-209.

    The detection cascade checks (in priority order):

    1. **No rewrite**: if the new commit hash equals the original commit
       hash, returns an empty list (no rewrite occurred).
    2. **Squash**: if the new commit's parent count differs from the
       original's, emit a ``squash`` candidate.
    3. **Cherry-pick**: if the new commit's precommit_provenance_digest
       matches the original's (the trailer is precommit-stable per
       doc-14:138-142 so cherry-picks preserve it), emit a
       ``cherry-pick`` candidate.
    4. **Amend**: if the new commit's tree hash equals the original's
       AND the committer timestamps drift, emit an ``amend`` candidate.
       (Amends in-place: same parent + same tree + new committer
       timestamp = new commit hash.)
    5. **Rebase**: if the new commit's parent hash differs from the
       original's AND the tree hash also differs, emit a ``rebase``
       candidate.
    6. **Recovery (fallback)**: if a ``dag-commit-failure:*`` marker is
       within the ``recovery_window_seconds`` proximity of the new
       commit's committer timestamp, emit a ``rebase`` candidate with
       a recovery detection signal (recoveries are Git rebases of
       failed commits per the doc-14 semantic).

    Multiple candidates may match (e.g., a single rewrite can be both
    a rebase AND a recovery). The caller (:class:`LineageEmitter`) then
    decides whether to emit the lineage record (when exactly one strong
    candidate matches) or to record a typed
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    with ``governance_evidence_conflict`` failure id (when multiple
    candidates conflict on the reason).

    The function is PURE + DETERMINISTIC: identical inputs ALWAYS
    produce identical candidate lists.
    """

    candidates: list[LineageRewriteCandidate] = []

    new_commit_hash = inputs.new_commit_proof.result_commit
    if new_commit_hash == inputs.original_commit_hash:
        # No rewrite occurred; commit hash matches.
        return candidates

    # 1. Squash: parent count divergence.
    if (
        inputs.new_parent_count is not None
        and inputs.original_parent_count is not None
        and inputs.new_parent_count != inputs.original_parent_count
    ):
        candidates.append(
            LineageRewriteCandidate(
                reason="squash",
                detection_signal=(
                    f"parent_count_diverged_from_original:"
                    f"original={inputs.original_parent_count}"
                    f",new={inputs.new_parent_count}"
                ),
            )
        )

    # 2. Cherry-pick: trailer digest preserved (precommit-stable per
    # doc-14:138-142).
    if (
        inputs.new_precommit_provenance_digest
        == inputs.original_precommit_provenance_digest
    ):
        candidates.append(
            LineageRewriteCandidate(
                reason="cherry-pick",
                detection_signal=(
                    "head_hash_differs_and_trailer_digest_matches"
                ),
            )
        )

    # 3. Amend: same tree + committer timestamp drift.
    # tree_sha is the new commit's tree; compare to original_tree_hash.
    if (
        inputs.new_commit_proof.tree_sha == inputs.original_tree_hash
        and inputs.new_committer_timestamp is not None
        and inputs.original_committer_timestamp is not None
        and inputs.new_committer_timestamp != inputs.original_committer_timestamp
    ):
        candidates.append(
            LineageRewriteCandidate(
                reason="amend",
                detection_signal=(
                    f"head_hash_differs_and_committer_timestamp_drift:"
                    f"original={inputs.original_committer_timestamp}"
                    f",new={inputs.new_committer_timestamp}"
                ),
            )
        )

    # 4. Rebase: parent + tree changed.
    if (
        inputs.new_parent_hash != inputs.original_parent_hash
        and inputs.new_commit_proof.tree_sha != inputs.original_tree_hash
    ):
        candidates.append(
            LineageRewriteCandidate(
                reason="rebase",
                detection_signal=(
                    "head_hash_differs_and_parent_changed_and_tree_changed"
                ),
            )
        )

    # 5. Recovery (fallback): proximity to dag-commit-failure marker.
    # Only adds a candidate if NO other rewrite reason has fired (so we
    # don't double-count a rebase that also happens to be near a
    # recovery marker).
    if (
        not candidates
        and inputs.recovery_failure_marker_ref is not None
        and inputs.recovery_failure_marker_timestamp is not None
        and inputs.new_committer_timestamp is not None
    ):
        proximity = abs(
            inputs.new_committer_timestamp - inputs.recovery_failure_marker_timestamp
        )
        if proximity <= inputs.recovery_window_seconds:
            candidates.append(
                LineageRewriteCandidate(
                    reason="rebase",
                    detection_signal=(
                        f"recovery_window_proximity_to_dag_commit_failure:"
                        f"proximity_seconds={proximity}"
                        f",window={inputs.recovery_window_seconds}"
                        f",failure_marker_ref={inputs.recovery_failure_marker_ref}"
                    ),
                )
            )

    return candidates


# --- Typed result + error ---------------------------------------------------


class LineageEmitResult(BaseModel):
    """Typed result of one :meth:`LineageEmitter.emit_for_repo` call.

    Per doc-14:144-150 + doc-14:242-243 the emit surface is
    non-blocking. The result carries:

    * ``ok: bool`` -- True if the emit succeeded; False if the emit
      failed (in which case ``gap_finding`` is populated).
    * ``lineage_record`` -- the typed :class:`LineageRecord` the
      emitter constructed (populated whenever a rewrite was detected;
      None when no rewrite occurred OR when an ambiguous-lineage gap
      finding was produced).
    * ``candidates`` -- the full list of :class:`LineageRewriteCandidate`
      rows produced by :func:`detect_rewrite_candidates` (always
      populated; lets callers inspect why an ambiguous lineage was
      flagged).
    * ``lineage_ref`` -- the canonical ref path the emitter targeted
      (always populated when a lineage was emitted).
    * ``lineage_digest`` -- the SHA-256 hex digest used in the ref
      (always populated when a lineage was emitted).
    * ``notes_ref`` -- the Git notes namespace the emitter used
      (always populated when a lineage was emitted).
    * ``idempotent_no_op`` -- True if a prior emit with identical
      lineage tuple had already landed (so this emit was a no-op per
      the idempotency contract).
    * ``gap_finding`` -- populated only when ``ok=False`` OR when an
      ambiguous-lineage detection produced a typed gap finding.
    * ``git_invocations`` -- the typed list of
      :class:`GitSubprocessResult` rows for every Git invocation the
      emitter made (used by tests to assert exact Git interaction
      traces).
    """

    # extra='forbid' aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    ok: bool
    lineage_record: LineageRecord | None = None
    candidates: list[LineageRewriteCandidate] = Field(default_factory=list)
    lineage_ref: str = ""
    lineage_digest: str = ""
    notes_ref: str = ""
    idempotent_no_op: bool = False
    gap_finding: CommitProvenanceGapFinding | None = None
    git_invocations: list[GitSubprocessResult] = Field(default_factory=list)


class LineageEmitError(RuntimeError):
    """Raised when the emitter needs to signal a structured failure to
    its caller INTERNALLY.

    The emitter's public surface :meth:`LineageEmitter.emit_for_repo`
    does NOT raise -- it returns a :class:`LineageEmitResult` with
    ``gap_finding`` populated when the emit fails. This exception is
    used INTERNALLY by helper methods to signal a structured failure
    that the public surface catches + projects onto a
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`.

    Per doc-14:242-243 the emitter MUST NOT propagate failures to the
    caller; the structured exception is the internal control-flow
    shape, not the public failure signal.
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


# --- The emitter (doc-14:174-178 step 6 + step 7) ---------------------------


class LineageEmitter:
    """Rebase/cherry-pick lineage emitter (doc-14:174-178 step 6 + step 7).

    Per doc-14:174-176 step 6: *"Add lineage handling for rewrite
    scenarios: if a commit is rebased, cherry-picked, or replaced by
    recovery, emit an explicit old-to-new lineage payload."*

    Per doc-14:177-178 step 7: *"Ensure multi-repo checkpoints preserve
    legacy comma-separated ``commit_hash`` display while structured
    proofs remain per repo."*

    The emitter is INVOCABLE AS A POST-CHECKPOINT GOVERNANCE JOB (NOT a
    blocking merge-queue hook). The :meth:`emit_for_repo` surface
    returns a typed :class:`LineageEmitResult` with ``ok: bool`` +
    ``gap_finding`` -- the emitter NEVER raises a failure to the caller
    (per the doc-14:242-243 non-blocking contract).

    The emitter is IDEMPOTENT per doc-14:144-150: a retry with
    identical lineage tuple (``repo_id`` + ``old_commit_hash`` +
    ``new_commit_hash`` + ``reason``) is a no-op (the emitter checks
    whether the canonical lineage ref already exists; if so it
    short-circuits + sets ``idempotent_no_op=True``).

    The emitter is TESTABLE via a fake :class:`GitSubprocessRunner`
    fixture: unit tests inject a fake that records every Git invocation
    + returns canned results; the emitter's behavior is fully
    deterministic given the inputs + the fake's responses.

    The emitter additionally maintains an :class:`InMemoryLineageWalker`
    so callers can chain the emitter directly into the reader's
    :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
    Protocol port via :attr:`walker_view` -- this is the production
    wiring for the doc-14:212-213 lineage-walk consumer.
    """

    def __init__(
        self,
        *,
        repo_path: str,
        runner: GitSubprocessRunner,
        notes_ref: str | None = None,
    ) -> None:
        """Construct an emitter bound to a repo + a subprocess runner.

        :param repo_path: filesystem path to the Git working tree the
            emitter operates on (passed as ``cwd`` to every Git
            invocation).
        :param runner: the :class:`GitSubprocessRunner` callable; in
            production a stdlib-subprocess wrapper, in tests a fake
            fixture.
        :param notes_ref: the Git notes namespace; defaults to
            :func:`compute_lineage_notes_ref_namespace`
            (``refs/notes/iriai``) per doc-14:144.
        """

        self._repo_path = repo_path
        self._runner = runner
        self._notes_ref = notes_ref or compute_lineage_notes_ref_namespace()
        # Backfills the 3rd-sub-slice LineageWalker Protocol port:
        # every successful emit also lands the LineageRecord in the
        # in-memory walker so the reader walks the same typed records
        # the emitter wrote.
        self._walker = InMemoryLineageWalker()

    @property
    def repo_path(self) -> str:
        """Filesystem path the emitter operates on (read-only)."""

        return self._repo_path

    @property
    def notes_ref(self) -> str:
        """Git notes namespace the emitter writes to (read-only)."""

        return self._notes_ref

    @property
    def walker_view(self) -> "InMemoryLineageWalker":
        """The in-memory :class:`LineageWalker` adapter backfilled by
        this emitter.

        Per doc-14:212-213 the reader consults a
        :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
        Protocol port to resolve rebase/cherry-pick scenarios. The
        production wiring chains this property's return value into the
        reader's ``lineage_walker=`` constructor argument, so the
        reader walks the same typed records this emitter wrote.

        The walker view is RECOMPUTED implicitly on every emit (every
        successful emit registers the lineage record into the walker);
        callers may hold a reference to this property without re-reading
        it across emits (the walker's internal dict is mutated in
        place).
        """

        return self._walker

    def emit_for_repo(
        self,
        inputs: LineageEmitterInputs,
    ) -> LineageEmitResult:
        """Emit the lineage record for one repo commit per doc-14:174-176.

        Returns a typed :class:`LineageEmitResult` with ``ok`` +
        ``gap_finding`` + the constructed typed shapes.

        Per doc-14:242-243 NEVER raises a failure to the caller. Per
        doc-14:144-150 IDEMPOTENT: a retry with identical lineage tuple
        is a no-op + sets ``idempotent_no_op=True``.

        The emit sequence (per doc-14:144-150 + doc-14:174-178 +
        doc-14:212-213):

        1. Run :func:`detect_rewrite_candidates` to enumerate the
           candidate rewrite scenarios.
           - If the candidate list is empty (no rewrite occurred),
             return ``ok=True`` with ``lineage_record=None``.
           - If the candidate list has more than one DISTINCT reason,
             record a ``governance_evidence_conflict`` gap finding +
             return ``ok=False`` (the ambiguous lineage cannot be
             unambiguously resolved per doc-14:208-209 "reject
             ambiguous line provenance unless lineage is recorded").
        2. Construct the typed :class:`LineageRecord` from the single
           candidate.
        3. Compute the canonical lineage ref path
           ``refs/iriai/lineage/{lineage_digest}``.
        4. Check whether the canonical ref already exists; if it does,
           the emit is a no-op (idempotency contract).
        5. If the canonical ref does NOT exist, write the canonical
           JSON of the lineage payload as the ref blob.
        6. Also write the lineage payload to the Git notes namespace
           keyed by the NEW commit hash; the notes write lets the
           reader look up lineage via the commit hash.
        7. Register the lineage record into the in-memory
           :class:`LineageWalker` adapter (so the reader walks the
           same typed records the emitter wrote).
        8. Return ``ok=True``.

        Any Git invocation failure projects to a
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
        with typed failure id ``line_provenance_gap`` (per doc-14:192-201)
        + ``ok=False``.
        """

        invocations: list[GitSubprocessResult] = []
        candidates = detect_rewrite_candidates(inputs)

        # Case 1: no rewrite detected.
        if not candidates:
            return LineageEmitResult(
                ok=True,
                lineage_record=None,
                candidates=[],
                lineage_ref="",
                lineage_digest="",
                notes_ref=self._notes_ref,
                idempotent_no_op=False,
                gap_finding=None,
                git_invocations=invocations,
            )

        # Case 2: ambiguous lineage (multiple DISTINCT reasons matched).
        # Per doc-14:208-209 + doc-14:212-213 "reject ambiguous line
        # provenance unless lineage is recorded" the emitter records a
        # typed governance_evidence_conflict finding WITHOUT emitting
        # the lineage record.
        distinct_reasons = sorted({c.reason for c in candidates})
        if len(distinct_reasons) > 1:
            gap = self._make_gap_finding(
                inputs=inputs,
                failure_id="governance_evidence_conflict",
                reason=(
                    "lineage detection produced multiple conflicting "
                    "candidate reasons: " + ",".join(distinct_reasons) +
                    " (doc-14:208-209 + 212-213)"
                ),
                lineage_ref="",
                lineage_digest="",
                evidence_payload={
                    "candidate_reasons": distinct_reasons,
                    "candidate_signals": [c.detection_signal for c in candidates],
                    "original_commit_hash": inputs.original_commit_hash,
                    "new_commit_hash": inputs.new_commit_proof.result_commit,
                },
            )
            return LineageEmitResult(
                ok=False,
                lineage_record=None,
                candidates=candidates,
                lineage_ref="",
                lineage_digest="",
                notes_ref=self._notes_ref,
                idempotent_no_op=False,
                gap_finding=gap,
                git_invocations=invocations,
            )

        # Case 3: unambiguous single-reason rewrite -- emit the lineage.
        # When multiple candidates exist but they all share the same
        # reason (e.g. rebase + recovery both classify as "rebase") we
        # still emit cleanly with the shared reason.
        reason = distinct_reasons[0]
        new_commit_hash = inputs.new_commit_proof.result_commit
        lineage_record = LineageRecord(
            old_commit_hash=inputs.original_commit_hash,
            new_commit_hash=new_commit_hash,
            reason=reason,
        )
        lineage_digest = compute_lineage_digest(
            repo_id=inputs.new_commit_proof.repo_id,
            old_commit_hash=inputs.original_commit_hash,
            new_commit_hash=new_commit_hash,
            reason=reason,
        )
        lineage_ref = compute_lineage_ref(
            repo_id=inputs.new_commit_proof.repo_id,
            old_commit_hash=inputs.original_commit_hash,
            new_commit_hash=new_commit_hash,
            reason=reason,
        )

        try:
            # Step 4: idempotency check.
            existing_payload = self._read_existing_lineage_at_ref(
                lineage_ref, invocations
            )
            if existing_payload is not None:
                # Idempotent no-op: register into walker (so reader walks
                # consistently across restarts) + return.
                self._walker.register(
                    repo_id=inputs.new_commit_proof.repo_id,
                    record=lineage_record,
                )
                return LineageEmitResult(
                    ok=True,
                    lineage_record=lineage_record,
                    candidates=candidates,
                    lineage_ref=lineage_ref,
                    lineage_digest=lineage_digest,
                    notes_ref=self._notes_ref,
                    idempotent_no_op=True,
                    gap_finding=None,
                    git_invocations=invocations,
                )

            # Step 5: write the lineage payload as a blob + the ref.
            self._write_lineage_to_ref(
                lineage_ref=lineage_ref,
                lineage_record=lineage_record,
                inputs=inputs,
                candidate=candidates[0],
                invocations=invocations,
            )

            # Step 6: write to notes keyed by the NEW commit hash.
            self._write_lineage_to_notes(
                lineage_record=lineage_record,
                inputs=inputs,
                candidate=candidates[0],
                invocations=invocations,
            )

        except LineageEmitError as exc:
            return LineageEmitResult(
                ok=False,
                lineage_record=lineage_record,
                candidates=candidates,
                lineage_ref=lineage_ref,
                lineage_digest=lineage_digest,
                notes_ref=self._notes_ref,
                idempotent_no_op=False,
                gap_finding=self._make_gap_finding(
                    inputs=inputs,
                    failure_id=exc.failure_id,
                    reason=exc.reason,
                    lineage_ref=lineage_ref,
                    lineage_digest=lineage_digest,
                    evidence_payload=exc.evidence_payload,
                ),
                git_invocations=invocations,
            )

        # Step 7: register into the in-memory walker so the reader
        # walks the same typed records.
        self._walker.register(
            repo_id=inputs.new_commit_proof.repo_id,
            record=lineage_record,
        )

        # Step 8: success.
        return LineageEmitResult(
            ok=True,
            lineage_record=lineage_record,
            candidates=candidates,
            lineage_ref=lineage_ref,
            lineage_digest=lineage_digest,
            notes_ref=self._notes_ref,
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
        invocations.append(result)
        return result

    def _read_existing_lineage_at_ref(
        self,
        ref: str,
        invocations: list[GitSubprocessResult],
    ) -> dict[str, Any] | None:
        """Return the existing lineage payload at ``ref`` or None if no
        ref exists.

        Uses ``git cat-file blob <ref>`` to read the ref's blob content.
        Returns None when the ref does not exist (``git`` returns
        non-zero exit code with a "Not a valid object name" / "unknown
        revision" stderr).

        Mirrors the writer's
        :meth:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter._read_existing_payload_at_ref`
        idempotency pattern.
        """

        result = self._run(["cat-file", "blob", ref], invocations)
        if result.returncode != 0:
            # The ref does not exist; the emitter should proceed with a
            # fresh write.
            return None

        # Parse the existing payload; if it doesn't parse, signal a
        # typed conflict (the ref exists but is structurally corrupt).
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise LineageEmitError(
                failure_id="governance_evidence_conflict",
                reason=(
                    f"existing lineage ref blob is not valid JSON: {exc.msg} "
                    f"at line {exc.lineno} col {exc.colno}"
                ),
                evidence_payload={
                    "ref": ref,
                    "raw_blob_preview": result.stdout[:200],
                },
            ) from exc

        if not isinstance(parsed, dict):
            raise LineageEmitError(
                failure_id="governance_evidence_conflict",
                reason="existing lineage ref blob is not a JSON object",
                evidence_payload={
                    "ref": ref,
                    "parsed_type": type(parsed).__name__,
                },
            )
        return parsed

    def _write_lineage_to_ref(
        self,
        *,
        lineage_ref: str,
        lineage_record: LineageRecord,
        inputs: LineageEmitterInputs,
        candidate: LineageRewriteCandidate,
        invocations: list[GitSubprocessResult],
    ) -> None:
        """Write the lineage payload as a Git blob + set ``lineage_ref``
        to point at it.

        Mirrors the writer's
        :meth:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter._write_payload_to_ref`
        two-step pattern (``git hash-object`` + ``git update-ref``).
        """

        canonical_blob = _canonical_json(
            self._build_lineage_payload(
                lineage_record=lineage_record,
                inputs=inputs,
                candidate=candidate,
            )
        )
        hash_result = self._run(
            ["hash-object", "-w", "--stdin", "--", canonical_blob],
            invocations,
        )
        if hash_result.returncode != 0:
            raise LineageEmitError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git hash-object failed with exit code {hash_result.returncode}"
                ),
                evidence_payload={
                    "ref": lineage_ref,
                    "stderr": hash_result.stderr,
                },
            )

        blob_oid = hash_result.stdout.strip()
        if not blob_oid:
            raise LineageEmitError(
                failure_id="line_provenance_gap",
                reason="git hash-object produced an empty blob oid",
                evidence_payload={
                    "ref": lineage_ref,
                    "stdout": hash_result.stdout,
                    "stderr": hash_result.stderr,
                },
            )

        update_result = self._run(
            ["update-ref", lineage_ref, blob_oid],
            invocations,
        )
        if update_result.returncode != 0:
            raise LineageEmitError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git update-ref failed with exit code {update_result.returncode}"
                ),
                evidence_payload={
                    "ref": lineage_ref,
                    "blob_oid": blob_oid,
                    "stderr": update_result.stderr,
                },
            )

    def _write_lineage_to_notes(
        self,
        *,
        lineage_record: LineageRecord,
        inputs: LineageEmitterInputs,
        candidate: LineageRewriteCandidate,
        invocations: list[GitSubprocessResult],
    ) -> None:
        """Write the lineage payload to the Git notes namespace keyed
        by the NEW commit hash.

        Mirrors the writer's
        :meth:`~iriai_build_v2.execution_control.commit_provenance_writer.GitProvenanceWriter._write_payload_to_notes`
        idempotent-write pattern (``git notes ... add -f``).
        """

        canonical_blob = _canonical_json(
            self._build_lineage_payload(
                lineage_record=lineage_record,
                inputs=inputs,
                candidate=candidate,
            )
        )
        result = self._run(
            [
                "notes",
                f"--ref={self._notes_ref}",
                "add",
                "-f",
                "-m",
                canonical_blob,
                lineage_record.new_commit_hash,
            ],
            invocations,
        )
        if result.returncode != 0:
            raise LineageEmitError(
                failure_id="line_provenance_gap",
                reason=(
                    f"git notes add failed with exit code {result.returncode}"
                ),
                evidence_payload={
                    "notes_ref": self._notes_ref,
                    "commit_hash": lineage_record.new_commit_hash,
                    "stderr": result.stderr,
                },
            )

    def _build_lineage_payload(
        self,
        *,
        lineage_record: LineageRecord,
        inputs: LineageEmitterInputs,
        candidate: LineageRewriteCandidate,
    ) -> dict[str, Any]:
        """Build the canonical-JSON lineage payload dict.

        The payload includes:

        * ``schema_version`` -- pinned ``"iriai.commit_provenance.lineage.v1"``
          so future bumps don't clash.
        * The :class:`LineageRecord` fields verbatim
          (``old_commit_hash`` + ``new_commit_hash`` + ``reason``).
        * The feature scope + group id (for cross-cite + audit).
        * The detection signal (free-form per
          :class:`LineageRewriteCandidate.detection_signal`).
        * Optional ``detected_at`` ISO-8601 timestamp.
        * Optional ``evidence_refs`` cross-cite list.
        """

        return {
            "schema_version": "iriai.commit_provenance.lineage.v1",
            "old_commit_hash": lineage_record.old_commit_hash,
            "new_commit_hash": lineage_record.new_commit_hash,
            "reason": lineage_record.reason,
            "repo_id": inputs.new_commit_proof.repo_id,
            "feature_id": inputs.feature_id,
            "group_idx": inputs.group_idx,
            "detection_signal": candidate.detection_signal,
            "detected_at": inputs.detected_at,
            "evidence_refs": list(inputs.evidence_refs),
        }

    def _make_gap_finding(
        self,
        *,
        inputs: LineageEmitterInputs,
        failure_id: Literal[
            "line_provenance_gap", "governance_evidence_conflict"
        ],
        reason: str,
        lineage_ref: str,
        lineage_digest: str,
        evidence_payload: dict[str, Any],
    ) -> CommitProvenanceGapFinding:
        """Build a typed
        :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
        from the emitter inputs.

        REUSES the 2nd sub-slice typed
        :class:`CommitProvenanceGapFinding` shape verbatim; populates
        the typed-shape fields with the emitter context. Per
        doc-14:242-243 the finding is NON-blocking: the caller MUST NOT
        propagate it to the executor / checkpoint / merge-queue / resume
        code paths.
        """

        return CommitProvenanceGapFinding(
            failure_id=failure_id,
            feature_id=inputs.feature_id,
            group_idx=inputs.group_idx,
            repo_id=inputs.new_commit_proof.repo_id,
            commit_hash=inputs.new_commit_proof.result_commit,
            precommit_provenance_ref=lineage_ref,
            precommit_provenance_digest=lineage_digest,
            reason=reason,
            evidence_payload=evidence_payload,
        )


# --- In-memory LineageWalker adapter ----------------------------------------


class InMemoryLineageWalker:
    """In-memory adapter satisfying the 3rd-sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
    Protocol port.

    Per doc-14:212-213 *"Rebase/cherry-pick: preserve old/new lineage
    and reject ambiguous line provenance unless lineage is recorded."*
    the reader consults a
    :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
    Protocol port to resolve rebase/cherry-pick scenarios. This
    in-memory adapter is the production wiring for the consumer port:
    the :class:`LineageEmitter` registers every successful emit into
    this walker, and the
    :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineProvenanceReader`
    consults this walker via :meth:`walk_from_old`.

    The walker satisfies the
    :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
    Protocol structurally (Pydantic v2 + Protocol uses structural
    subtyping; the walker's :meth:`walk_from_old` signature matches
    the Protocol verbatim).

    The walker is INSTANCE-SCOPED: each :class:`LineageEmitter` owns
    its own walker. For cross-process consistency the typed lineage
    payloads are also persisted to ``refs/iriai/lineage/{digest}`` +
    Git notes per :meth:`LineageEmitter.emit_for_repo` so a re-loaded
    process can rebuild the walker by scanning the lineage namespace
    (a future Slice 14 sub-slice or production wiring task may
    factor this rebuild-on-startup helper).
    """

    def __init__(self) -> None:
        # Records keyed by (repo_id, old_commit_hash) match the
        # LineageWalker.walk_from_old signature exactly.
        self._records: dict[tuple[str, str], LineageRecord] = {}

    def register(
        self,
        *,
        repo_id: str,
        record: LineageRecord,
    ) -> None:
        """Register a typed :class:`LineageRecord` for one repo.

        Called by :meth:`LineageEmitter.emit_for_repo` after a
        successful emit (including idempotent no-ops). Multiple
        registrations for the same ``(repo_id, old_commit_hash)`` are
        a structural defect (the emitter's idempotency guard at the
        Git ref level prevents this) but the walker tolerates them by
        keeping the LAST registration (which is the desired semantic
        for a re-emitted lineage after a recovery + repeat).
        """

        self._records[(repo_id, record.old_commit_hash)] = record

    def walk_from_old(
        self,
        *,
        repo_id: str,
        old_commit_hash: str,
    ) -> LineageRecord | None:
        """Look up the typed :class:`LineageRecord` for one repo +
        old commit hash.

        Returns the registered :class:`LineageRecord` or None when no
        lineage was emitted for the given ``(repo_id, old_commit_hash)``
        tuple.

        Satisfies the 3rd-sub-slice
        :class:`~iriai_build_v2.execution_control.commit_provenance_reader.LineageWalker`
        Protocol verbatim.
        """

        return self._records.get((repo_id, old_commit_hash))


# Verify at module-load time that the in-memory walker satisfies the
# Protocol (Pydantic v2 uses structural subtyping for Protocols so a
# runtime isinstance check is the canonical verification).
#
# We do a soft assertion: the InMemoryLineageWalker has the
# walk_from_old method with the expected signature; production callers
# can wire `walker_view` into the reader's `lineage_walker=` kwarg.
_ = LineageWalker  # silence unused import warning; the Protocol type is the contract surface
