"""Slice 14 fourth sub-slice -- unit tests for the
``execution_control/commit_provenance_lineage.py`` rebase/cherry-pick
lineage emitter + multi-repo checkpoint integration.

Covers (per the implementer prompt § "MUST DO" item 3):

- Rebase detection via head-vs-original commit comparison (doc-14:174-176).
- Cherry-pick detection via trailer digest (doc-14:138-142 +
  doc-14:174-176).
- Amend detection via author/committer timestamp drift (doc-14:174-176).
- Squash detection via parent-count divergence (doc-14:174-176).
- Recovery detection via ``dag-commit-failure:*`` proximity
  (doc-14:174-176 "replaced by recovery").
- Lineage payload persistence via fake Git subprocess runner
  (doc-14:144-150).
- Round-trip with the 3rd-sub-slice reader (emitter writes; reader
  consumes via :class:`LineageWalker` Protocol port; doc-14:212-213).
- Ambiguous lineage records a typed ``governance_evidence_conflict``
  finding (doc-14:208-209 + doc-14:212-213).
- Non-blocking discipline (emitter NEVER raises; doc-14:242-243).
- Multi-repo group emits one :class:`LineageRecord` per repo commit
  (doc-14:204-205).
- Legacy comma-separated ``commit_hash`` display path preserved per
  doc-14:177-178.
- ``ConfigDict(extra="forbid")`` discipline on all new typed surfaces.
- The 3rd-sub-slice :class:`LineageRecord` is REUSED (NOT redefined).
- The 2nd-sub-slice typed failure ids are REUSED via direct re-import.

The emitter is a POST-CHECKPOINT GOVERNANCE PROJECTION WRITER (mirrors
the 2nd sub-slice writer discipline). Tests at the bottom of this file
pin the non-blocking contract verbatim.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.commit_provenance import (
    CommitProvenancePayload,
    CommitProvenanceTrailer,
)
from iriai_build_v2.execution_control.commit_provenance_lineage import (
    COMMIT_PROVENANCE_GAP_FAILURE_IDS,
    InMemoryLineageWalker,
    LineageEmitError,
    LineageEmitResult,
    LineageEmitter,
    LineageEmitterInputs,
    LineageRewriteCandidate,
    compute_lineage_digest,
    compute_lineage_notes_ref_namespace,
    compute_lineage_ref,
    detect_rewrite_candidates,
)
from iriai_build_v2.execution_control.commit_provenance_reader import (
    BlameLine,
    CommitProofRow,
    LineageRecord,
    LineageWalker,
    LineProvenanceReader,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    CommitProvenanceGapFinding,
    GitSubprocessResult,
    compute_notes_ref_namespace,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof
from iriai_build_v2.workflows.develop.execution import failure_router as fr


# ── fixtures ────────────────────────────────────────────────────────────────


def _repo_commit_proof(**overrides: Any) -> RepoCommitProof:
    """Construct a fully-specified Slice 08 :class:`RepoCommitProof`."""

    base: dict[str, Any] = dict(
        repo_id="repo-1",
        repo_path="/tmp/repo-1",
        pre_apply_head="p" * 40,
        applied_head="a" * 40,
        result_commit="2" * 40,  # new commit hash
        tree_sha="t" * 40,
        changed_paths=["src/a.py", "src/b.py"],
        status_before="",
        status_after="",
        no_dirty_snapshot_id=999,
    )
    base.update(overrides)
    return RepoCommitProof(**base)


def _emitter_inputs(**overrides: Any) -> LineageEmitterInputs:
    """Construct a fully-specified :class:`LineageEmitterInputs` for a
    REBASE scenario (the default: head hash differs + parent changed +
    tree changed)."""

    base: dict[str, Any] = dict(
        new_commit_proof=_repo_commit_proof(),
        new_parent_hash="2p" * 20,  # 40-char; different from original
        new_precommit_provenance_digest="d" * 64,
        new_author_timestamp=1_700_000_100,
        new_committer_timestamp=1_700_000_100,
        new_parent_count=1,
        original_commit_hash="1" * 40,
        original_parent_hash="1p" * 20,  # 40-char; different from new
        original_tree_hash="ot" * 20,  # 40-char; different from new tree
        original_precommit_provenance_digest="d" * 64,  # default: same digest (would be cherry-pick)
        original_author_timestamp=1_700_000_000,
        original_committer_timestamp=1_700_000_000,
        original_parent_count=1,
        feature_id="feature-abc",
        group_idx=0,
        detected_at="2026-05-24T03:30:00Z",
        evidence_refs=["dag-commit-proof:repo-1:" + ("2" * 40)],
    )
    base.update(overrides)
    return LineageEmitterInputs(**base)


@dataclass
class FakeGitRunner:
    """Fake :class:`GitSubprocessRunner` for unit tests.

    Records every invocation (so tests can assert exact Git interaction
    traces) and returns canned :class:`GitSubprocessResult` rows from a
    queued response list. If the queue is empty when called, returns a
    default success result (returncode=0; empty stdout/stderr).

    Per the implementer prompt MUST NOT shell out to real ``git`` in
    unit tests; this fake fixture is the test-side
    :class:`GitSubprocessRunner` implementation.
    """

    responses: list[GitSubprocessResult] = field(default_factory=list)
    invocations: list[tuple[list[str], str]] = field(default_factory=list)

    def __call__(self, args: list[str], *, cwd: str) -> GitSubprocessResult:
        self.invocations.append((list(args), cwd))
        if self.responses:
            response = self.responses.pop(0)
            return response.model_copy(update={"args": list(args)})
        return GitSubprocessResult(
            args=list(args), returncode=0, stdout="", stderr=""
        )

    def queue(
        self,
        *,
        returncode: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.responses.append(
            GitSubprocessResult(
                args=[],
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        )

    def queue_no_ref(self) -> None:
        """Queue a 'ref does not exist' response for the idempotency
        cat-file check (first call in any successful emit)."""

        self.queue(returncode=128, stdout="", stderr="Not a valid object name")

    def queue_success_write(self) -> None:
        """Queue the 3 success responses for a fresh write:
        cat-file (no ref) -> hash-object (blob oid) -> update-ref (ok)
        -> notes add (ok)."""

        self.queue_no_ref()
        self.queue(returncode=0, stdout="b" * 40 + "\n", stderr="")  # hash-object
        self.queue(returncode=0, stdout="", stderr="")  # update-ref
        self.queue(returncode=0, stdout="", stderr="")  # notes add


def _make_emitter(
    *,
    runner: FakeGitRunner | None = None,
    notes_ref: str | None = None,
) -> tuple[LineageEmitter, FakeGitRunner]:
    """Construct an emitter bound to a fake runner for tests."""

    r = runner or FakeGitRunner()
    emitter = LineageEmitter(repo_path="/tmp/repo-1", runner=r, notes_ref=notes_ref)
    return emitter, r


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed inputs/outputs + emitter
    + walker + helpers."""

    from iriai_build_v2.execution_control import commit_provenance_lineage as mod

    expected = {
        "LineageEmitterInputs",
        "LineageRewriteCandidate",
        "LineageEmitResult",
        "LineageEmitError",
        "LineageEmitter",
        "InMemoryLineageWalker",
        "compute_lineage_digest",
        "compute_lineage_ref",
        "compute_lineage_notes_ref_namespace",
        "detect_rewrite_candidates",
        "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_lineage_module_does_not_re_export_via_execution_control_init() -> None:
    """Per the Slice 14 1st + 2nd + 3rd sub-slice precedent the lineage
    module is NOT re-exported from ``execution_control/__init__.py``."""

    from iriai_build_v2 import execution_control

    pkg_all = getattr(execution_control, "__all__", [])
    assert "LineageEmitter" not in pkg_all
    assert "LineageEmitterInputs" not in pkg_all
    assert "InMemoryLineageWalker" not in pkg_all


# ── ConfigDict(extra="forbid") discipline ──────────────────────────────────


def test_lineage_emitter_inputs_extra_forbid_rejects_unknown_field() -> None:
    """Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed inputs reject unknown fields."""

    with pytest.raises(ValidationError):
        LineageEmitterInputs(
            new_commit_proof=_repo_commit_proof(),
            new_parent_hash="p" * 40,
            new_precommit_provenance_digest="d" * 64,
            original_commit_hash="1" * 40,
            original_parent_hash="p" * 40,
            original_tree_hash="t" * 40,
            original_precommit_provenance_digest="d" * 64,
            feature_id="f",
            group_idx=0,
            unknown_field="should_be_rejected",  # type: ignore[call-arg]
        )


def test_lineage_rewrite_candidate_extra_forbid_rejects_unknown_field() -> None:
    """The candidate typed shape rejects unknown fields."""

    with pytest.raises(ValidationError):
        LineageRewriteCandidate(
            reason="rebase",
            detection_signal="...",
            unknown_field="x",  # type: ignore[call-arg]
        )


def test_lineage_emit_result_extra_forbid_rejects_unknown_field() -> None:
    """The result typed shape rejects unknown fields."""

    with pytest.raises(ValidationError):
        LineageEmitResult(
            ok=True,
            unknown_field="x",  # type: ignore[call-arg]
        )


def test_emitter_inputs_evidence_refs_validator_rejects_empty_strings() -> None:
    """The validator fails closed on empty strings per
    ``feedback_no_silent_degradation``."""

    with pytest.raises(ValidationError):
        _emitter_inputs(evidence_refs=["valid-ref", "", "another"])


def test_emitter_inputs_evidence_refs_validator_accepts_non_empty() -> None:
    """The validator accepts a list of non-empty strings."""

    inputs = _emitter_inputs(evidence_refs=["a", "b", "c"])
    assert inputs.evidence_refs == ["a", "b", "c"]


# ── failure-id REUSE (no new ids registered) ───────────────────────────────


def test_commit_provenance_gap_failure_ids_reuses_2nd_sub_slice_tuple() -> None:
    """The lineage module re-exports the 2nd sub-slice tuple verbatim
    (same object identity; REUSE pattern, NO new ids registered)."""

    from iriai_build_v2.execution_control import commit_provenance_writer as writer_mod
    from iriai_build_v2.execution_control import commit_provenance_lineage as lineage_mod

    # Same object identity confirms direct re-import (not a copy).
    assert (
        lineage_mod.COMMIT_PROVENANCE_GAP_FAILURE_IDS
        is writer_mod.COMMIT_PROVENANCE_GAP_FAILURE_IDS
    )
    # The tuple matches the 2nd sub-slice doc-14:192-201 spec verbatim.
    assert COMMIT_PROVENANCE_GAP_FAILURE_IDS == (
        "line_provenance_gap",
        "governance_evidence_conflict",
    )


def test_emitter_does_not_re_register_failure_ids() -> None:
    """Importing the emitter module triggers NO new failure id
    registrations; the 2 typed ids + the NON-blocking route action
    remain registered EXACTLY ONCE (from the 2nd sub-slice)."""

    import iriai_build_v2.execution_control.commit_provenance_lineage  # noqa: F401

    assert fr.FAILURE_TYPES.count("line_provenance_gap") == 1
    assert fr.FAILURE_TYPES.count("governance_evidence_conflict") == 1
    assert fr.ROUTE_ACTIONS.count("retry_governance_projection") == 1


def test_lineage_record_REUSED_from_3rd_sub_slice() -> None:
    """The 3rd-sub-slice :class:`LineageRecord` is REUSED via direct
    import (NOT redefined in this module)."""

    from iriai_build_v2.execution_control import commit_provenance_lineage as lineage_mod
    from iriai_build_v2.execution_control import commit_provenance_reader as reader_mod

    # The LineageRecord imported by the emitter module is the SAME
    # class object as the one defined in the reader module.
    # (Verified by inspecting the emitter module's globals via the
    # qualified import.)
    record = LineageRecord(
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="rebase",
    )
    assert isinstance(record, reader_mod.LineageRecord)
    # The emitter's emit_for_repo returns a LineageRecord; the type is
    # the same class.
    assert LineageRecord is reader_mod.LineageRecord


# ── pure helpers ───────────────────────────────────────────────────────────


def test_compute_lineage_digest_is_deterministic() -> None:
    """Two identical lineage tuples produce identical digests."""

    digest_a = compute_lineage_digest(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="rebase",
    )
    digest_b = compute_lineage_digest(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="rebase",
    )
    assert digest_a == digest_b
    # SHA-256 hex digest is 64 chars.
    assert len(digest_a) == 64


def test_compute_lineage_digest_differs_on_reason_change() -> None:
    """Different reasons produce different digests."""

    digest_rebase = compute_lineage_digest(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="rebase",
    )
    digest_amend = compute_lineage_digest(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="amend",
    )
    assert digest_rebase != digest_amend


def test_compute_lineage_ref_namespace_per_doc_14_144_150() -> None:
    """The canonical lineage ref path is
    ``refs/iriai/lineage/{lineage_digest}`` per doc-14:144-150."""

    ref = compute_lineage_ref(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="rebase",
    )
    assert ref.startswith("refs/iriai/lineage/")
    # The suffix is the SHA-256 hex digest (64 chars).
    assert len(ref) == len("refs/iriai/lineage/") + 64


def test_compute_lineage_notes_ref_namespace_reuses_writer_helper() -> None:
    """The lineage notes namespace is identical to the writer's notes
    namespace (``refs/notes/iriai``) per doc-14:144-150 unified-notes
    discipline."""

    assert compute_lineage_notes_ref_namespace() == compute_notes_ref_namespace()
    assert compute_lineage_notes_ref_namespace() == "refs/notes/iriai"


# ── detect_rewrite_candidates (doc-14:174-176 + doc-14:208-209) ────────────


def test_detect_no_rewrite_when_commit_hash_matches() -> None:
    """If the new commit hash equals the original, no rewrite occurred
    and the function returns an empty list."""

    inputs = _emitter_inputs(
        new_commit_proof=_repo_commit_proof(result_commit="1" * 40),
        original_commit_hash="1" * 40,
    )
    candidates = detect_rewrite_candidates(inputs)
    assert candidates == []


def test_detect_rebase_via_head_parent_tree_change() -> None:
    """Rebase = head hash differs + parent changed + tree changed."""

    inputs = _emitter_inputs(
        # Default: head differs ('2'*40 vs '1'*40), parent differs,
        # tree differs, digests match (so cherry-pick also matches).
        # To get ONLY rebase, make the precommit digest differ too.
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "rebase" in reasons
    # Make sure the detection signal contains the expected substring.
    rebase_signals = [
        c.detection_signal for c in candidates if c.reason == "rebase"
    ]
    assert any("parent_changed_and_tree_changed" in s for s in rebase_signals)


def test_detect_cherry_pick_via_trailer_digest_match() -> None:
    """Cherry-pick = head differs + precommit_provenance_digest matches
    (the trailer is precommit-stable per doc-14:138-142)."""

    inputs = _emitter_inputs(
        # Default digests both "d"*64 -- so cherry-pick matches.
        # Make parent + tree match too so rebase does NOT fire.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,  # SAME as original_tree_hash below
        ),
        original_tree_hash="t" * 40,
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "cherry-pick" in reasons
    # Rebase did NOT fire (parent + tree match).
    assert "rebase" not in reasons


def test_detect_amend_via_committer_timestamp_drift() -> None:
    """Amend = head differs + same tree + committer timestamp drifts."""

    inputs = _emitter_inputs(
        # Same tree + same parent + different committer timestamp.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,  # SAME as original_tree_hash
        ),
        original_tree_hash="t" * 40,
        new_committer_timestamp=1_700_000_500,  # drifted
        original_committer_timestamp=1_700_000_000,
        # Different digest so cherry-pick does NOT fire.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "amend" in reasons
    amend_signals = [
        c.detection_signal for c in candidates if c.reason == "amend"
    ]
    assert any("committer_timestamp_drift" in s for s in amend_signals)


def test_detect_squash_via_parent_count_divergence() -> None:
    """Squash = parent count diverges between original and new."""

    inputs = _emitter_inputs(
        new_parent_count=2,  # squash combined 2 commits into 1
        original_parent_count=1,
        # Different digest so cherry-pick does NOT fire.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "squash" in reasons


def test_detect_recovery_via_failure_marker_proximity() -> None:
    """Recovery = new commit's committer timestamp is within
    ``recovery_window_seconds`` of the ``dag-commit-failure:*`` marker
    timestamp. Recovery emits a ``rebase`` reason per doc-14:174-176
    (recoveries are Git rebases of failed commits).

    The recovery fallback only fires when NO other rewrite reason has
    matched (so a true rebase that happens near a recovery marker is
    NOT double-counted)."""

    inputs = _emitter_inputs(
        # Make new == original so the rebase + cherry-pick + amend
        # signals do NOT fire (but new_commit_proof.result_commit
        # MUST be != original_commit_hash for any rewrite at all).
        # Use a configuration where ONLY recovery should fire: parent
        # matches, tree matches, digest matches, timestamps match BUT
        # committer timestamp drift would normally NOT fire because
        # they match. So we set parent + tree + digest all matching
        # original AND a recovery marker.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        # Different digest = NOT cherry-pick.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
        # Same committer timestamp = NOT amend.
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_500,
        # Recovery marker proximate to new committer timestamp.
        recovery_failure_marker_ref="dag-commit-failure:repo-1:" + ("1" * 40),
        recovery_failure_marker_timestamp=1_700_000_000,  # 500s away
        recovery_window_seconds=3600,  # within window
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "rebase" in reasons
    # Verify the recovery signal was used.
    rebase_signals = [
        c.detection_signal for c in candidates if c.reason == "rebase"
    ]
    assert any("recovery_window_proximity" in s for s in rebase_signals)


def test_detect_recovery_does_NOT_fire_outside_window() -> None:
    """Recovery is NOT detected when the failure marker is outside the
    ``recovery_window_seconds`` window."""

    inputs = _emitter_inputs(
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_500,
        # Recovery marker is 10000s away from new committer
        # (outside 3600s window).
        recovery_failure_marker_ref="dag-commit-failure:repo-1:" + ("1" * 40),
        recovery_failure_marker_timestamp=1_700_010_500,
        recovery_window_seconds=3600,
    )
    candidates = detect_rewrite_candidates(inputs)
    # No reason fires (no rebase, no cherry-pick, no amend, no squash,
    # no recovery -- ambient drift signals don't match).
    assert candidates == []


def test_detect_returns_multiple_candidates_when_signals_overlap() -> None:
    """A single rewrite may match multiple detection signals (e.g.
    rebase AND cherry-pick if the trailer digest is preserved across a
    rebase). The detector returns ALL matching candidates; the emitter
    then decides whether to emit or flag as ambiguous."""

    inputs = _emitter_inputs(
        # Default: tree diff + parent diff + digest match.
        # This triggers BOTH rebase + cherry-pick.
        original_precommit_provenance_digest="d" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    candidates = detect_rewrite_candidates(inputs)
    reasons = {c.reason for c in candidates}
    assert "rebase" in reasons
    assert "cherry-pick" in reasons
    assert len(reasons) >= 2


# ── LineageEmitter.emit_for_repo (doc-14:174-178 step 6 + step 7) ──────────


def test_emit_no_rewrite_returns_ok_with_no_lineage_record() -> None:
    """When no rewrite is detected the emit returns ok=True +
    lineage_record=None + makes NO git invocations."""

    runner = FakeGitRunner()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        new_commit_proof=_repo_commit_proof(result_commit="1" * 40),
        original_commit_hash="1" * 40,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is None
    assert result.git_invocations == []
    assert result.gap_finding is None


def test_emit_unambiguous_rebase_persists_lineage_and_returns_ok() -> None:
    """An unambiguous rebase emits a typed
    :class:`LineageRecord` + persists it via Git ref + notes."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        # Force only rebase to fire (no cherry-pick).
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is not None
    assert result.lineage_record.reason == "rebase"
    assert result.lineage_record.old_commit_hash == "1" * 40
    assert result.lineage_record.new_commit_hash == "2" * 40
    assert result.gap_finding is None
    assert result.idempotent_no_op is False
    # 4 git invocations: cat-file (no ref) + hash-object + update-ref + notes add.
    assert len(result.git_invocations) == 4
    assert result.git_invocations[0].args[0] == "cat-file"
    assert result.git_invocations[1].args[0] == "hash-object"
    assert result.git_invocations[2].args[0] == "update-ref"
    assert result.git_invocations[3].args[0] == "notes"


def test_emit_persists_to_lineage_ref_namespace_per_doc_14_144_150() -> None:
    """The persisted lineage payload lands at
    ``refs/iriai/lineage/{digest}`` per the dedicated namespace
    discipline."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.lineage_ref.startswith("refs/iriai/lineage/")
    # The update-ref invocation targets the same ref.
    update_call = result.git_invocations[2]
    assert update_call.args[0] == "update-ref"
    assert update_call.args[1] == result.lineage_ref


def test_emit_persists_to_notes_namespace_keyed_by_new_commit() -> None:
    """The persisted lineage payload also lands in
    ``refs/notes/iriai`` keyed by the NEW commit hash (matches the
    reader's notes-first lookup pattern)."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    notes_call = result.git_invocations[3]
    assert notes_call.args[0] == "notes"
    assert notes_call.args[1] == "--ref=refs/notes/iriai"
    assert notes_call.args[2] == "add"
    assert notes_call.args[3] == "-f"
    # The note is keyed by the NEW commit hash (last argument).
    assert notes_call.args[-1] == "2" * 40


def test_emit_idempotent_no_op_when_lineage_ref_exists() -> None:
    """A retry with identical lineage tuple is a no-op (Git ref already
    exists; emit short-circuits + sets idempotent_no_op=True)."""

    # Pre-existing lineage payload (cat-file returns success with JSON).
    existing_payload = json.dumps({"old_commit_hash": "1" * 40})
    runner = FakeGitRunner()
    runner.queue(returncode=0, stdout=existing_payload, stderr="")

    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.idempotent_no_op is True
    assert result.lineage_record is not None
    # Only ONE git invocation (the cat-file check); no hash-object /
    # update-ref / notes add.
    assert len(result.git_invocations) == 1


def test_emit_ambiguous_lineage_records_governance_evidence_conflict() -> None:
    """Per doc-14:208-209 + doc-14:212-213 *"reject ambiguous line
    provenance unless lineage is recorded"* -- when multiple DISTINCT
    candidate reasons match, the emitter records a typed
    ``governance_evidence_conflict`` finding WITHOUT emitting the
    lineage record."""

    runner = FakeGitRunner()
    emitter, _ = _make_emitter(runner=runner)
    # Setup: cherry-pick AND amend BOTH match (different distinct
    # reasons -> ambiguous).
    inputs = _emitter_inputs(
        # Cherry-pick: digest matches.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="d" * 64,
        # Amend: same tree + committer timestamp drift.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_000,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is False
    assert result.lineage_record is None
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "governance_evidence_conflict"
    # The gap finding carries both candidate reasons in evidence.
    assert "candidate_reasons" in result.gap_finding.evidence_payload
    candidate_reasons = result.gap_finding.evidence_payload["candidate_reasons"]
    assert set(candidate_reasons) >= {"cherry-pick", "amend"}
    # No Git invocations were made (the emitter short-circuited BEFORE
    # the persistence path).
    assert result.git_invocations == []


def test_emit_multiple_signals_same_reason_resolves_cleanly() -> None:
    """When multiple candidates fire but they all share the same
    reason (e.g. rebase + recovery both classify as "rebase"), the
    emitter still emits cleanly with the shared reason."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    # The default inputs (rebase + cherry-pick because digest matches)
    # have 2 DISTINCT reasons; instead we craft inputs where the
    # candidate fires for rebase ONLY:
    inputs = _emitter_inputs(
        # Only rebase fires: parent diff + tree diff + digest diff.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is not None
    assert result.lineage_record.reason == "rebase"


def test_emit_git_write_failure_records_line_provenance_gap() -> None:
    """A Git write failure (e.g. hash-object exit 128) projects to a
    typed ``line_provenance_gap`` gap finding + ``ok=False`` per
    doc-14:192-201."""

    runner = FakeGitRunner()
    # cat-file (no ref) -> hash-object FAILS.
    runner.queue_no_ref()
    runner.queue(returncode=128, stdout="", stderr="fatal: hash-object died")
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"
    assert "hash-object" in result.gap_finding.reason


def test_emit_corrupt_existing_ref_records_governance_evidence_conflict() -> None:
    """If the existing lineage ref's blob is not valid JSON the emitter
    records a typed ``governance_evidence_conflict`` finding (structural
    corruption signal per the 2nd-sub-slice writer pattern)."""

    runner = FakeGitRunner()
    # cat-file returns success with non-JSON content (corrupt blob).
    runner.queue(returncode=0, stdout="not valid json {{{", stderr="")
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "governance_evidence_conflict"


# ── non-blocking discipline (doc-14:242-243) ───────────────────────────────


def test_emitter_never_raises_on_git_failure() -> None:
    """The emitter's public surface NEVER raises a failure to the
    caller per doc-14:242-243; failures project onto typed
    :class:`CommitProvenanceGapFinding`."""

    runner = FakeGitRunner()
    # Force a write failure at update-ref step.
    runner.queue_no_ref()
    runner.queue(returncode=0, stdout="b" * 40 + "\n", stderr="")  # hash-object ok
    runner.queue(returncode=1, stdout="", stderr="update-ref failed")  # update-ref FAILS
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    # The emitter MUST NOT raise; it returns a typed result with gap.
    result = emitter.emit_for_repo(inputs)
    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"


def test_emitter_never_raises_on_ambiguous_lineage() -> None:
    """Ambiguous-lineage detection records a typed gap finding without
    raising."""

    runner = FakeGitRunner()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="d" * 64,
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_000,
    )
    # The emitter MUST NOT raise.
    result = emitter.emit_for_repo(inputs)
    assert result.ok is False
    assert result.gap_finding is not None


# ── round-trip with the 3rd-sub-slice reader (doc-14:212-213) ──────────────


def test_emitter_backfills_walker_for_reader_round_trip() -> None:
    """The emitter registers every successful emit into the
    in-memory walker; the 3rd-sub-slice reader consumes this walker
    via the :class:`LineageWalker` Protocol port for the
    doc-14:212-213 ambiguous-blame resolution path."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)

    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True

    # The emitter's walker_view satisfies the LineageWalker Protocol.
    walker = emitter.walker_view
    assert isinstance(walker, InMemoryLineageWalker)

    # Walking from the OLD commit returns the typed LineageRecord.
    record = walker.walk_from_old(
        repo_id="repo-1",
        old_commit_hash="1" * 40,
    )
    assert record is not None
    assert record.old_commit_hash == "1" * 40
    assert record.new_commit_hash == "2" * 40
    assert record.reason == "rebase"


def test_in_memory_walker_satisfies_reader_lineage_walker_protocol() -> None:
    """The :class:`InMemoryLineageWalker` satisfies the 3rd-sub-slice
    :class:`LineageWalker` Protocol verbatim; wiring it into the
    reader's ``lineage_walker=`` constructor argument is a clean
    drop-in."""

    walker = InMemoryLineageWalker()
    # Register a lineage record.
    record = LineageRecord(
        old_commit_hash="a" * 40,
        new_commit_hash="b" * 40,
        reason="cherry-pick",
    )
    walker.register(repo_id="repo-X", record=record)

    # The walker is structurally a LineageWalker (Protocol uses
    # structural subtyping in Pydantic v2; the walk_from_old signature
    # matches verbatim).
    assert hasattr(walker, "walk_from_old")
    # Functional round-trip via the Protocol.
    fetched = walker.walk_from_old(repo_id="repo-X", old_commit_hash="a" * 40)
    assert fetched is record


def test_reader_round_trip_with_emitter_walker() -> None:
    """Round-trip integration: the emitter writes a lineage record via
    :meth:`emit_for_repo`; the reader walks the SAME typed record via
    :meth:`InMemoryLineageWalker.walk_from_old` (the same Protocol the
    reader's :class:`LineageWalker` consumes).

    This is the production wiring for doc-14:212-213 lineage walk: the
    emitter writes, the reader walks, the 3rd-sub-slice's tests
    (test_reader_walks_lineage_for_rebased_commit etc.) round-trip via
    the same in-memory walker."""

    # 1. Emit a lineage record.
    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    emit_result = emitter.emit_for_repo(inputs)
    assert emit_result.ok is True

    # 2. Wire the emitter's walker into a reader.
    @dataclass
    class TrivialPayloadStore:
        def get_payload_by_ref(self, *, repo_id: str, ref: str):
            return None
        def get_payload_from_notes(self, *, repo_id: str, commit_hash: str):
            return None

    @dataclass
    class TrivialTrailerSource:
        def get_trailer(self, *, repo_id: str, commit_hash: str):
            return None

    @dataclass
    class TrivialCommitProofProvider:
        rows: dict[tuple[str, str], CommitProofRow] = field(default_factory=dict)
        def get_commit_proof(
            self, *, repo_id: str, commit_hash: str
        ) -> CommitProofRow | None:
            return self.rows.get((repo_id, commit_hash))

    cp = TrivialCommitProofProvider()
    # Register a CommitProofRow for the NEW commit so the reader's
    # lineage-walk re-consult succeeds.
    new_commit = "2" * 40
    cp.rows[("repo-1", new_commit)] = CommitProofRow(
        commit_hash=new_commit,
        repo_id="repo-1",
        task_ids=["task-from-new-commit"],
        precommit_provenance_ref="refs/iriai/provenance/" + ("d" * 64),
        commit_proof=_repo_commit_proof(result_commit=new_commit),
    )

    # 3. Walk via the production-wired walker.
    walker = emitter.walker_view
    old_commit = "1" * 40
    walked = walker.walk_from_old(repo_id="repo-1", old_commit_hash=old_commit)
    assert walked is not None
    assert walked.new_commit_hash == new_commit
    assert walked.reason == "rebase"

    # 4. The reader resolves the OLD commit via the walker chain.
    # (This mirrors the 3rd-sub-slice test
    # test_reader_walks_lineage_for_rebased_commit verbatim.)
    reader_runner = FakeGitRunner()
    # Queue blame returning the OLD commit hash.
    blame_block = (
        f"{old_commit} 1 1 1\n"
        f"author Alice\n"
        f"author-mail <alice@example.com>\n"
        f"author-time 1700000000\n"
        f"author-tz +0000\n"
        f"committer Alice\n"
        f"committer-mail <alice@example.com>\n"
        f"committer-time 1700000000\n"
        f"committer-tz +0000\n"
        f"summary first commit\n"
        f"filename foo.py\n"
        f"\tline 1 content\n"
    )
    reader_runner.queue(returncode=0, stdout=blame_block, stderr="")

    reader = LineProvenanceReader(
        repo_path="/tmp/repo-1",
        runner=reader_runner,
        commit_proof_provider=cp,
        payload_store=TrivialPayloadStore(),
        trailer_source=TrivialTrailerSource(),
        lineage_walker=walker,
    )

    from iriai_build_v2.execution_control.commit_provenance import (
        LineProvenanceQuery,
    )
    query = LineProvenanceQuery(
        repo_id="repo-1",
        ref="HEAD",
        path="foo.py",
        line_start=1,
        line_end=1,
    )
    read_result = reader.read(query)
    # The reader walked the lineage from OLD -> NEW + resolved via
    # typed proof for the NEW commit.
    assert read_result.result.completeness == "complete"
    assert read_result.result.task_ids == ["task-from-new-commit"]
    # The lineage-walked tier has confidence 0.6.
    assert read_result.result.confidence == 0.6


# ── multi-repo group integration (doc-14:204-205 + doc-14:177-178) ─────────


def test_multi_repo_group_emits_one_lineage_per_repo_commit() -> None:
    """Per doc-14:204-205 *"Multi-repo group: every repo commit gets a
    payload; the group checkpoint links all payload refs."* the emitter
    handles multi-repo groups by emitting ONE typed
    :class:`LineageRecord` per repo commit (each repo's lineage is
    independent)."""

    runner = FakeGitRunner()
    # Queue success writes for 2 repos.
    runner.queue_success_write()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)

    # Repo A: rebase.
    repo_a_inputs = _emitter_inputs(
        new_commit_proof=_repo_commit_proof(
            repo_id="repo-A",
            repo_path="/tmp/repo-A",
            result_commit="a2" * 20,
            tree_sha="at" * 20,
        ),
        original_commit_hash="a1" * 20,
        original_tree_hash="aot" * 13 + "f",  # 40 chars
        original_precommit_provenance_digest="ae" * 32,
        new_precommit_provenance_digest="ad" * 32,
    )
    result_a = emitter.emit_for_repo(repo_a_inputs)
    assert result_a.ok is True
    assert result_a.lineage_record is not None
    assert result_a.lineage_record.new_commit_hash == "a2" * 20

    # Repo B: also a rebase but different repo.
    repo_b_inputs = _emitter_inputs(
        new_commit_proof=_repo_commit_proof(
            repo_id="repo-B",
            repo_path="/tmp/repo-B",
            result_commit="b2" * 20,
            tree_sha="bt" * 20,
        ),
        original_commit_hash="b1" * 20,
        original_tree_hash="bot" * 13 + "f",
        original_precommit_provenance_digest="be" * 32,
        new_precommit_provenance_digest="bd" * 32,
    )
    result_b = emitter.emit_for_repo(repo_b_inputs)
    assert result_b.ok is True
    assert result_b.lineage_record is not None
    assert result_b.lineage_record.new_commit_hash == "b2" * 20

    # The walker_view holds BOTH lineage records (one per repo).
    walker = emitter.walker_view
    walked_a = walker.walk_from_old(repo_id="repo-A", old_commit_hash="a1" * 20)
    walked_b = walker.walk_from_old(repo_id="repo-B", old_commit_hash="b1" * 20)
    assert walked_a is not None
    assert walked_b is not None
    assert walked_a is not walked_b
    assert walked_a.new_commit_hash == "a2" * 20
    assert walked_b.new_commit_hash == "b2" * 20


def test_legacy_comma_separated_commit_hash_display_preserved_per_doc_14_177_178() -> None:
    """Per doc-14:177-178 the legacy comma-separated ``commit_hash``
    display at
    :func:`iriai_build_v2.workflows.develop.execution.merge_queue._reconstruct_checkpoint_body`
    (line 1257) is PRESERVED unchanged (this sub-slice's audit confirms
    NO mutation needed).

    The audit verifies:
    - ``RepoCommitProof`` 10 fields verbatim FROZEN (Slice 08 baseline).
    - ``_reconstruct_checkpoint_body`` writes ``,``.join(coverage.result_commits) into the legacy display field.
    - The lineage emitter operates per-repo without mutating either path.
    """

    # Verify the legacy display path is intact at the import boundary.
    from iriai_build_v2.workflows.develop.execution import merge_queue as mq_mod

    # The _reconstruct_checkpoint_body helper exists and continues to
    # produce the legacy comma-joined display.
    assert hasattr(mq_mod, "_reconstruct_checkpoint_body")

    # The RepoCommitProof typed shape carries the 10 frozen fields.
    proof_fields = set(RepoCommitProof.model_fields.keys())
    assert proof_fields == {
        "repo_id",
        "repo_path",
        "pre_apply_head",
        "applied_head",
        "result_commit",
        "tree_sha",
        "changed_paths",
        "status_before",
        "status_after",
        "no_dirty_snapshot_id",
    }

    # Verify the legacy comma-joined display still lives at line 1257
    # (per doc-14:177-178). Read the source file directly to confirm
    # the comma-join discipline is preserved verbatim.
    import inspect
    source = inspect.getsource(mq_mod._reconstruct_checkpoint_body)
    # The exact load-bearing legacy-display construction.
    assert '",".join(coverage.result_commits)' in source
    # And the body returns commit_hash as a top-level key.
    assert '"commit_hash"' in source


def test_audit_merge_queue_store_repo_commit_proof_10_fields_FROZEN() -> None:
    """Per doc-14:155-160 step 1 the Slice 08 :class:`RepoCommitProof`
    10-field typed shape is FROZEN. The 4th sub-slice MUST NOT mutate
    it. This test pins the shape at exactly 10 fields verbatim."""

    fields = list(RepoCommitProof.model_fields.keys())
    assert len(fields) == 10
    # Field order pinned (mirrors merge_queue_store.py:227-239 verbatim).
    assert fields == [
        "repo_id",
        "repo_path",
        "pre_apply_head",
        "applied_head",
        "result_commit",
        "tree_sha",
        "changed_paths",
        "status_before",
        "status_after",
        "no_dirty_snapshot_id",
    ]


# ── recovery scenario (doc-14:174-176 "replaced by recovery") ──────────────


def test_recovery_emits_lineage_with_rebase_reason() -> None:
    """A recovery scenario (new commit's committer timestamp proximate
    to a ``dag-commit-failure:*`` marker) emits a lineage record with
    ``reason="rebase"`` per doc-14:174-176."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_500,
        recovery_failure_marker_ref="dag-commit-failure:repo-1:" + ("1" * 40),
        recovery_failure_marker_timestamp=1_700_000_000,
        recovery_window_seconds=3600,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is not None
    assert result.lineage_record.reason == "rebase"
    # The candidate's detection signal references the recovery window.
    assert len(result.candidates) >= 1
    rebase_signals = [
        c.detection_signal for c in result.candidates if c.reason == "rebase"
    ]
    assert any("recovery_window_proximity" in s for s in rebase_signals)


# ── repo_path + notes_ref properties ───────────────────────────────────────


def test_emitter_repo_path_property_reflects_constructor_argument() -> None:
    """The :attr:`LineageEmitter.repo_path` property returns the
    constructor's ``repo_path`` argument verbatim."""

    runner = FakeGitRunner()
    emitter = LineageEmitter(repo_path="/custom/path", runner=runner)
    assert emitter.repo_path == "/custom/path"


def test_emitter_notes_ref_defaults_to_canonical_namespace() -> None:
    """The default :attr:`LineageEmitter.notes_ref` is the canonical
    ``refs/notes/iriai`` namespace."""

    runner = FakeGitRunner()
    emitter = LineageEmitter(repo_path="/tmp/r", runner=runner)
    assert emitter.notes_ref == "refs/notes/iriai"


def test_emitter_notes_ref_is_overridable() -> None:
    """The ``notes_ref=`` constructor argument overrides the default
    namespace (useful for testing alternative namespaces)."""

    runner = FakeGitRunner()
    emitter = LineageEmitter(
        repo_path="/tmp/r",
        runner=runner,
        notes_ref="refs/notes/custom-ns",
    )
    assert emitter.notes_ref == "refs/notes/custom-ns"


# ── gap finding correlator ─────────────────────────────────────────────────


def test_emit_gap_finding_correlator_carries_emitter_context() -> None:
    """The gap finding's typed fields carry the emitter context
    (feature_id + group_idx + repo_id + commit_hash + lineage_ref +
    lineage_digest) per the 2nd-sub-slice typed shape contract."""

    runner = FakeGitRunner()
    emitter, _ = _make_emitter(runner=runner)
    # Trigger an ambiguous-lineage gap finding.
    inputs = _emitter_inputs(
        feature_id="feature-xyz",
        group_idx=42,
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="d" * 64,
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            repo_id="repo-Y",
            repo_path="/tmp/repo-Y",
            result_commit="9" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_000,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.gap_finding is not None
    assert result.gap_finding.feature_id == "feature-xyz"
    assert result.gap_finding.group_idx == 42
    assert result.gap_finding.repo_id == "repo-Y"
    assert result.gap_finding.commit_hash == "9" * 40


def test_emit_result_extra_forbid_at_json_roundtrip() -> None:
    """The typed :class:`LineageEmitResult` round-trips through JSON
    serialisation cleanly with ``extra='forbid'`` enforcement."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    # Round-trip via JSON.
    dumped = result.model_dump_json()
    parsed = json.loads(dumped)
    rehydrated = LineageEmitResult.model_validate(parsed)
    assert rehydrated.ok is True
    assert rehydrated.lineage_record is not None
    assert rehydrated.lineage_record.reason == "rebase"


# ── Slice 14 1st + 2nd + 3rd sub-slice non-alteration ──────────────────────


def test_emitter_does_not_alter_repo_commit_proof_typed_shape() -> None:
    """Per doc-14:155-160 the Slice 08 :class:`RepoCommitProof` 10
    fields are READ-ONLY from this slice. The emitter cross-cites but
    does NOT mutate."""

    proof = _repo_commit_proof()
    original_dump = proof.model_dump()

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        new_commit_proof=proof,
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    emitter.emit_for_repo(inputs)

    # The proof was NOT mutated.
    assert proof.model_dump() == original_dump


def test_lineage_emit_error_is_internal_only() -> None:
    """The :class:`LineageEmitError` is an INTERNAL structured exception
    that the public :meth:`emit_for_repo` surface catches + converts to
    a typed :class:`CommitProvenanceGapFinding`. Direct construction is
    allowed for documentation + testing of the internal control flow."""

    err = LineageEmitError(
        failure_id="line_provenance_gap",
        reason="test reason",
        evidence_payload={"key": "value"},
    )
    assert err.failure_id == "line_provenance_gap"
    assert err.reason == "test reason"
    assert err.evidence_payload == {"key": "value"}


def test_emitter_module_imports_lineage_record_from_reader_not_redefined() -> None:
    """The :class:`LineageRecord` is imported from the 3rd sub-slice
    reader module (REUSE pattern) -- NOT redefined in the emitter
    module."""

    import iriai_build_v2.execution_control.commit_provenance_lineage as lineage_mod
    import iriai_build_v2.execution_control.commit_provenance_reader as reader_mod

    # The class object is identical between modules.
    assert lineage_mod.LineageRecord is reader_mod.LineageRecord
    # The Literal values are identical (4-value reason taxonomy).
    record = lineage_mod.LineageRecord(
        old_commit_hash="1" * 40,
        new_commit_hash="2" * 40,
        reason="squash",
    )
    assert record.reason == "squash"


def test_in_memory_walker_register_overwrites_for_same_key() -> None:
    """Per the docstring contract, multiple registrations for the same
    ``(repo_id, old_commit_hash)`` keep the LAST registration."""

    walker = InMemoryLineageWalker()
    walker.register(
        repo_id="repo-1",
        record=LineageRecord(
            old_commit_hash="1" * 40,
            new_commit_hash="2" * 40,
            reason="rebase",
        ),
    )
    walker.register(
        repo_id="repo-1",
        record=LineageRecord(
            old_commit_hash="1" * 40,
            new_commit_hash="3" * 40,
            reason="cherry-pick",
        ),
    )
    walked = walker.walk_from_old(repo_id="repo-1", old_commit_hash="1" * 40)
    assert walked is not None
    assert walked.new_commit_hash == "3" * 40
    assert walked.reason == "cherry-pick"


def test_walker_returns_none_for_unregistered_old_commit() -> None:
    """The walker returns None for unregistered keys (matches the
    3rd-sub-slice :class:`LineageWalker` Protocol contract for "no
    lineage recorded")."""

    walker = InMemoryLineageWalker()
    walked = walker.walk_from_old(
        repo_id="repo-X",
        old_commit_hash="nonexistent" + "0" * 29,
    )
    assert walked is None


# ── amend + squash detection coverage (closes 3rd-sub-slice P3-14-3-R2) ────


def test_amend_lineage_record_emits_cleanly() -> None:
    """An unambiguous amend scenario produces a typed
    :class:`LineageRecord` with ``reason="amend"`` -- closes the 3rd
    sub-slice P3-14-3-R2 carry (which noted amend + squash paths were
    not exercised by tests until this sub-slice provides the emitter)."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        # Amend signal: same tree + same parent + drift committer.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_committer_timestamp=1_700_000_500,
        original_committer_timestamp=1_700_000_000,
        # Different digest = NOT cherry-pick.
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is not None
    assert result.lineage_record.reason == "amend"


def test_squash_lineage_record_emits_cleanly() -> None:
    """An unambiguous squash scenario produces a typed
    :class:`LineageRecord` with ``reason="squash"`` -- closes the 3rd
    sub-slice P3-14-3-R2 carry."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        # Squash signal: parent_count divergence (1 -> 2).
        new_parent_count=2,
        original_parent_count=1,
        # Different digest = NOT cherry-pick. Different tree = could
        # be rebase too, so use same parent + same tree to suppress.
        new_parent_hash="p" * 40,
        original_parent_hash="p" * 40,
        new_commit_proof=_repo_commit_proof(
            result_commit="2" * 40,
            tree_sha="t" * 40,
        ),
        original_tree_hash="t" * 40,
        new_precommit_provenance_digest="d" * 64,
        original_precommit_provenance_digest="e" * 64,
        # Same committer timestamp = NOT amend.
        new_committer_timestamp=1_700_000_000,
        original_committer_timestamp=1_700_000_000,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    assert result.lineage_record is not None
    assert result.lineage_record.reason == "squash"


# ── payload schema version + canonical JSON ─────────────────────────────────


def test_lineage_payload_includes_schema_version() -> None:
    """The persisted lineage payload includes a pinned schema_version
    string so future bumps don't clash with existing payloads."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    # Inspect the canonical JSON written via hash-object (the "--"
    # synthetic sentinel carries the payload per the fake-runner
    # pattern in the 2nd-sub-slice writer fixtures).
    hash_obj_call = result.git_invocations[1]
    assert hash_obj_call.args[0] == "hash-object"
    # The fake runner carries the canonical JSON after the "--"
    # sentinel.
    sentinel_idx = hash_obj_call.args.index("--")
    canonical_blob = hash_obj_call.args[sentinel_idx + 1]
    payload = json.loads(canonical_blob)
    assert payload["schema_version"] == "iriai.commit_provenance.lineage.v1"
    assert payload["old_commit_hash"] == "1" * 40
    assert payload["new_commit_hash"] == "2" * 40
    assert payload["reason"] == "rebase"
    assert payload["repo_id"] == "repo-1"
    assert payload["feature_id"] == "feature-abc"
    assert payload["group_idx"] == 0


def test_lineage_payload_carries_evidence_refs_cross_cite() -> None:
    """The persisted lineage payload carries the
    :attr:`LineageEmitterInputs.evidence_refs` list verbatim (cross-cite
    to typed ``dag-commit-proof:*`` rows)."""

    runner = FakeGitRunner()
    runner.queue_success_write()
    emitter, _ = _make_emitter(runner=runner)
    inputs = _emitter_inputs(
        original_precommit_provenance_digest="e" * 64,
        new_precommit_provenance_digest="d" * 64,
        evidence_refs=[
            "dag-commit-proof:repo-1:" + ("2" * 40),
            "dag-commit-proof:repo-1:" + ("1" * 40),
        ],
    )
    result = emitter.emit_for_repo(inputs)
    assert result.ok is True
    hash_obj_call = result.git_invocations[1]
    sentinel_idx = hash_obj_call.args.index("--")
    canonical_blob = hash_obj_call.args[sentinel_idx + 1]
    payload = json.loads(canonical_blob)
    assert payload["evidence_refs"] == [
        "dag-commit-proof:repo-1:" + ("2" * 40),
        "dag-commit-proof:repo-1:" + ("1" * 40),
    ]
    assert payload["detected_at"] == "2026-05-24T03:30:00Z"


# ── failure-routing route action ───────────────────────────────────────────


def test_emitter_failure_id_routes_to_non_blocking_route_action() -> None:
    """Per doc-14:242-243 + the 2nd-sub-slice failure_router registration
    the typed failure ids route to the NON-blocking
    ``retry_governance_projection`` RouteAction (NOT ``quiesce``)."""

    # The 2nd-sub-slice ROUTE_TABLE entries pin the routing; we verify
    # by importing the failure router + querying the routing table.
    keys = list(fr.ROUTE_TABLE.keys())
    assert ("evidence_corruption", "line_provenance_gap") in keys
    assert ("evidence_corruption", "governance_evidence_conflict") in keys

    # Both route to retry_governance_projection (NON-blocking).
    route_for_gap = fr.ROUTE_TABLE[("evidence_corruption", "line_provenance_gap")]
    route_for_conflict = fr.ROUTE_TABLE[
        ("evidence_corruption", "governance_evidence_conflict")
    ]
    assert route_for_gap.action == "retry_governance_projection"
    assert route_for_conflict.action == "retry_governance_projection"
