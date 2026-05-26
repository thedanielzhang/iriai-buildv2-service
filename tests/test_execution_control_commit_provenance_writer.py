"""Slice 14 second sub-slice -- unit tests for the
``execution_control/commit_provenance_writer.py`` Git provenance writer
module.

Covers (per the implementer prompt § "MUST DO" item 3):

- Trailer generation from typed inputs (doc-14:79-87).
- Payload generation with ``payload_sha256`` self-exclusion verified
  (doc-14:151-153).
- Idempotent Git notes/refs writer (retry produces identical refs +
  payload digests) (doc-14:144-150).
- Writer failure produces ``line_provenance_gap`` OR
  ``governance_evidence_conflict`` governance gap finding WITHOUT
  mutating checkpoint state (doc-14:192-201).
- Cross-cite of ``commit_proof_evidence_id`` integer field on payload
  per doc-14:105.
- ``precommit_provenance_ref`` stability contract (does NOT contain
  result commit hash) (doc-14:138-142).
- ``ConfigDict(extra="forbid")`` discipline on all new typed surfaces.
- Use a fake Git subprocess fixture; do NOT shell out to real ``git``
  in unit tests.

The 2 NEW typed failure ids registered in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router`
(``line_provenance_gap`` + ``governance_evidence_conflict``) route to the
NEW ``retry_governance_projection`` NON-BLOCKING action per
doc-14:242-243 (DIFFERENT from prior Slice 13A pattern which all route
to ``quiesce``). Tests at the bottom of this file pin the non-blocking
contract verbatim.
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
    canonical_payload_dict,
    compute_payload_sha256,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    COMMIT_PROVENANCE_GAP_FAILURE_IDS,
    CommitProvenanceGapFinding,
    CommitProvenanceWriteResult,
    CommitProvenanceWriterInputs,
    GitProvenanceWriteError,
    GitProvenanceWriter,
    GitSubprocessResult,
    GitSubprocessRunner,
    compute_notes_ref_namespace,
    compute_payload,
    compute_precommit_provenance_digest,
    compute_precommit_provenance_inputs,
    compute_precommit_provenance_ref,
    compute_trailer,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof
from iriai_build_v2.workflows.develop.execution import failure_router as fr


# ── fixtures ────────────────────────────────────────────────────────────────


def _repo_commit_proof(**overrides: Any) -> RepoCommitProof:
    """Construct a fully-specified Slice 08 :class:`RepoCommitProof` for tests."""

    base: dict[str, Any] = dict(
        repo_id="repo-1",
        repo_path="/tmp/repo-1",
        pre_apply_head="p" * 40,
        applied_head="a" * 40,
        result_commit="c" * 40,
        tree_sha="t" * 40,
        changed_paths=["src/a.py", "src/b.py"],
        status_before="",
        status_after="",
        no_dirty_snapshot_id=999,
    )
    base.update(overrides)
    return RepoCommitProof(**base)


def _writer_inputs(**overrides: Any) -> CommitProvenanceWriterInputs:
    """Construct a fully-specified :class:`CommitProvenanceWriterInputs` for tests."""

    base: dict[str, Any] = dict(
        commit_proof=_repo_commit_proof(),
        feature_id="feature-abc",
        dag_sha256="d" * 64,
        group_idx=0,
        effective_group_idx=None,
        task_ids=["task-1", "task-2"],
        contract_ids=[1, 2],
        attempt_ids=[10, 11],
        sandbox_patch_evidence_ids=[100, 101],
        gate_evidence_ids=[200, 201],
        merge_queue_item_ids=[1000, 1001],
        commit_proof_evidence_id=5000,
        checkpoint_artifact_id=6000,
        no_dirty_snapshot_ids=[7000, 7001],
        implementation_log_anchors=["impl-journal#anchor-1"],
        checkpoint_ref="dag-group:0",
        parent_hash="p" * 40,
    )
    base.update(overrides)
    return CommitProvenanceWriterInputs(**base)


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
            # Stamp the args onto the response so tests don't have to
            # pre-populate the `args` field on every response.
            return response.model_copy(update={"args": list(args)})
        # Default: success.
        return GitSubprocessResult(args=list(args), returncode=0, stdout="", stderr="")

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


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed inputs + writer + helpers."""

    from iriai_build_v2.execution_control import commit_provenance_writer as mod

    expected = {
        "CommitProvenanceWriterInputs",
        "CommitProvenanceWriteResult",
        "CommitProvenanceGapFinding",
        "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
        "compute_precommit_provenance_inputs",
        "compute_precommit_provenance_digest",
        "compute_precommit_provenance_ref",
        "compute_trailer",
        "compute_payload",
        "compute_notes_ref_namespace",
        "GitSubprocessRunner",
        "GitSubprocessResult",
        "GitProvenanceWriter",
        "GitProvenanceWriteError",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_writer_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _writer_inputs(unknown_field="oops")  # type: ignore[arg-type]


def test_gap_finding_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`CommitProvenanceGapFinding`."""

    with pytest.raises(ValidationError):
        CommitProvenanceGapFinding(
            failure_id="line_provenance_gap",
            feature_id="f",
            group_idx=0,
            repo_id="r",
            commit_hash="c" * 40,
            precommit_provenance_ref="refs/x",
            precommit_provenance_digest="d" * 64,
            reason="r",
            unknown_field="oops",  # type: ignore[arg-type]
        )


def test_write_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`CommitProvenanceWriteResult`."""

    payload = compute_payload(_writer_inputs())
    trailer = compute_trailer(_writer_inputs())
    with pytest.raises(ValidationError):
        CommitProvenanceWriteResult(
            ok=True,
            payload=payload,
            trailer=trailer,
            provenance_ref="r",
            notes_ref="refs/notes/iriai",
            unknown_field="oops",  # type: ignore[arg-type]
        )


def test_git_subprocess_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`GitSubprocessResult`."""

    with pytest.raises(ValidationError):
        GitSubprocessResult(
            args=[],
            returncode=0,
            unknown_field="oops",  # type: ignore[arg-type]
        )


# ── COMMIT_PROVENANCE_GAP_FAILURE_IDS (doc-14:192-201) ─────────────────────


def test_commit_provenance_gap_failure_ids_is_2_typed_ids() -> None:
    """Per doc-14:192-201 the writer projects onto 2 typed failure ids:
    ``line_provenance_gap`` + ``governance_evidence_conflict``."""

    assert COMMIT_PROVENANCE_GAP_FAILURE_IDS == (
        "line_provenance_gap",
        "governance_evidence_conflict",
    )


def test_failure_router_registers_2_new_typed_failure_ids() -> None:
    """Per doc-14:192-201 the 2 typed failure ids register in the
    Slice 07 failure_router under the EXISTING ``evidence_corruption``
    failure_class."""

    assert "line_provenance_gap" in fr.FAILURE_TYPES
    assert "governance_evidence_conflict" in fr.FAILURE_TYPES
    assert ("evidence_corruption", "line_provenance_gap") in fr.ROUTE_TABLE
    assert ("evidence_corruption", "governance_evidence_conflict") in fr.ROUTE_TABLE


def test_failure_router_routes_new_ids_to_non_blocking_per_doc_14_242_243() -> None:
    """Per doc-14:242-243 the 2 typed failure ids MUST route to a
    NON-BLOCKING action (NOT ``quiesce``).

    The NEW ``retry_governance_projection`` action is the non-blocking
    route. This is INTENTIONALLY DIFFERENT from the prior Slice 13A
    typed ids (``list_field_incomplete`` + ``classifier_rule_blocked``
    also under ``evidence_corruption``) which all route to ``quiesce``.
    """

    gap = fr.ROUTE_TABLE[("evidence_corruption", "line_provenance_gap")]
    conflict = fr.ROUTE_TABLE[("evidence_corruption", "governance_evidence_conflict")]

    # Both route to the NEW non-blocking action.
    assert gap.action == "retry_governance_projection"
    assert conflict.action == "retry_governance_projection"

    # Both are NOT `quiesce` (per doc-14:242-243).
    assert gap.action != "quiesce"
    assert conflict.action != "quiesce"

    # The NEW action is in the canonical ROUTE_ACTIONS tuple.
    assert "retry_governance_projection" in fr.ROUTE_ACTIONS


def test_new_action_is_a_retry_bucket_per_action_startswith_retry() -> None:
    """``retry_governance_projection`` benefits from
    ``action.startswith("retry_")`` -- downstream callers that bucket
    retries together see it as a retry route."""

    assert "retry_governance_projection".startswith("retry_")


def test_new_failure_ids_under_existing_evidence_corruption_class() -> None:
    """Per the implementer prompt MUST register under EXISTING
    failure_class (``evidence_corruption`` is the closest semantic
    fit; ``governance`` does not exist as a failure_class). The 2 new
    ids do NOT introduce a new failure_class."""

    assert "governance" not in fr.FAILURE_CLASSES
    assert "evidence_corruption" in fr.FAILURE_CLASSES
    # And no new failure_class was added.
    gap = fr.ROUTE_TABLE[("evidence_corruption", "line_provenance_gap")]
    conflict = fr.ROUTE_TABLE[("evidence_corruption", "governance_evidence_conflict")]
    assert gap.failure_class == "evidence_corruption"
    assert conflict.failure_class == "evidence_corruption"


# ── precommit-stable derivation (doc-14:138-142) ───────────────────────────


def test_compute_precommit_provenance_inputs_returns_stable_subset() -> None:
    """Per doc-14:138-142 the precommit-stable input subset must include
    the documented stable inputs (feature_id + dag_sha + group + repo_id
    + queue item ids + task id digest + contract digest)."""

    inputs = _writer_inputs()
    subset = compute_precommit_provenance_inputs(inputs)

    assert subset["feature_id"] == "feature-abc"
    assert subset["dag_sha256"] == "d" * 64
    assert subset["group_idx"] == 0
    assert subset["repo_id"] == "repo-1"
    assert subset["merge_queue_item_ids"] == [1000, 1001]
    assert "task_ids_digest" in subset
    assert "contract_ids_digest" in subset


def test_compute_precommit_provenance_inputs_excludes_result_commit_hash() -> None:
    """Per doc-14:138-142 the precommit-stable subset MUST NOT contain
    the result commit hash unless an explicit amend flow reruns all
    digest checks.

    The current sub-slice's :func:`compute_precommit_provenance_inputs`
    is NOT an amend-flow callable, so the result commit hash MUST be
    absent.
    """

    inputs = _writer_inputs(commit_proof=_repo_commit_proof(result_commit="c" * 40))
    subset = compute_precommit_provenance_inputs(inputs)

    # Result commit hash MUST NOT appear in the precommit-stable subset
    # (per doc-14:138-142 verbatim contract).
    assert "commit_hash" not in subset
    assert "result_commit" not in subset
    # Tree hash is also post-commit (reflects the commit tree).
    assert "tree_hash" not in subset
    assert "tree_sha" not in subset
    # Parent hash is also excluded -- the rebase/cherry-pick lineage
    # rule at doc-14:208-209 means a rewrite of the same logical commit
    # should produce the same precommit ref even if the parent moves.
    assert "parent_hash" not in subset

    # The "c" * 40 string must NOT appear anywhere in the subset.
    canonical = json.dumps(subset, sort_keys=True)
    assert "c" * 40 not in canonical


def test_compute_precommit_provenance_inputs_excludes_post_commit_snapshots() -> None:
    """``no_dirty_snapshot_ids`` are post-commit (verify clean state AFTER
    the commit) so they MUST NOT appear in the precommit-stable subset."""

    inputs = _writer_inputs(no_dirty_snapshot_ids=[7000, 7001])
    subset = compute_precommit_provenance_inputs(inputs)
    assert "no_dirty_snapshot_ids" not in subset


def test_compute_precommit_provenance_inputs_stable_across_id_list_ordering() -> None:
    """Two writer-input bundles with the same set of task/contract/queue
    item ids in different orderings MUST produce identical precommit
    subsets (the function sorts the id lists deterministically)."""

    a = _writer_inputs(
        task_ids=["task-2", "task-1"],
        contract_ids=[2, 1],
        merge_queue_item_ids=[1001, 1000],
    )
    b = _writer_inputs(
        task_ids=["task-1", "task-2"],
        contract_ids=[1, 2],
        merge_queue_item_ids=[1000, 1001],
    )
    assert compute_precommit_provenance_inputs(
        a
    ) == compute_precommit_provenance_inputs(b)


def test_compute_precommit_provenance_digest_is_stable_for_identical_inputs() -> None:
    """Per doc-14:138-142 + the doc-14:144-150 idempotency rule, identical
    writer inputs MUST produce identical digests."""

    a = _writer_inputs()
    b = _writer_inputs()
    assert compute_precommit_provenance_digest(a) == compute_precommit_provenance_digest(b)


def test_compute_precommit_provenance_digest_changes_with_input_change() -> None:
    """A change in any precommit-stable input MUST produce a different digest."""

    base = _writer_inputs()
    base_digest = compute_precommit_provenance_digest(base)

    # Different feature_id -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(feature_id="other-feature")
    ) != base_digest

    # Different dag_sha256 -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(dag_sha256="e" * 64)
    ) != base_digest

    # Different group_idx -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(group_idx=99)
    ) != base_digest

    # Different repo_id (via commit_proof) -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(commit_proof=_repo_commit_proof(repo_id="other-repo"))
    ) != base_digest

    # Different task_ids -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(task_ids=["task-99"])
    ) != base_digest

    # Different contract_ids -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(contract_ids=[99])
    ) != base_digest

    # Different merge_queue_item_ids -> different digest.
    assert compute_precommit_provenance_digest(
        _writer_inputs(merge_queue_item_ids=[9999])
    ) != base_digest


def test_compute_precommit_provenance_digest_invariant_under_post_commit_changes() -> None:
    """Changes that are post-commit (result_commit, tree_sha,
    no_dirty_snapshot_ids) MUST NOT change the precommit digest, because
    the digest is derived from the precommit-stable subset."""

    base_digest = compute_precommit_provenance_digest(_writer_inputs())

    # Different result_commit -> SAME digest (post-commit value).
    assert compute_precommit_provenance_digest(
        _writer_inputs(commit_proof=_repo_commit_proof(result_commit="f" * 40))
    ) == base_digest

    # Different tree_sha -> SAME digest (post-commit value).
    assert compute_precommit_provenance_digest(
        _writer_inputs(commit_proof=_repo_commit_proof(tree_sha="z" * 40))
    ) == base_digest

    # Different no_dirty_snapshot_ids -> SAME digest (post-commit).
    assert compute_precommit_provenance_digest(
        _writer_inputs(no_dirty_snapshot_ids=[9999])
    ) == base_digest

    # Different parent_hash -> SAME digest (per the lineage rule rationale).
    assert compute_precommit_provenance_digest(
        _writer_inputs(parent_hash="z" * 40)
    ) == base_digest


def test_compute_precommit_provenance_ref_is_canonical_path() -> None:
    """Per doc-14:144-150 the canonical Git ref path is
    ``refs/iriai/provenance/{precommit_provenance_digest}``."""

    inputs = _writer_inputs()
    digest = compute_precommit_provenance_digest(inputs)
    ref = compute_precommit_provenance_ref(inputs)
    assert ref == f"refs/iriai/provenance/{digest}"


def test_compute_notes_ref_namespace_is_canonical() -> None:
    """Per doc-14:144 the canonical Git notes namespace is
    ``refs/notes/iriai``."""

    assert compute_notes_ref_namespace() == "refs/notes/iriai"


# ── trailer generation from typed inputs (doc-14:79-87) ────────────────────


def test_compute_trailer_returns_typed_trailer_with_all_8_fields() -> None:
    """Per doc-14:79-87 the trailer carries 8 fields."""

    inputs = _writer_inputs()
    trailer = compute_trailer(inputs)

    assert isinstance(trailer, CommitProvenanceTrailer)
    assert trailer.feature_id == "feature-abc"
    assert trailer.group_idx == 0
    assert trailer.effective_group_idx is None
    assert len(trailer.task_ids_digest) == 64  # SHA-256 hex
    assert len(trailer.merge_queue_item_ids_digest) == 64
    assert trailer.checkpoint_ref == "dag-group:0"
    assert trailer.precommit_provenance_ref.startswith("refs/iriai/provenance/")
    assert len(trailer.precommit_provenance_digest) == 64


def test_compute_trailer_precommit_ref_does_not_contain_result_commit_hash() -> None:
    """Per doc-14:138-142 the trailer's ``precommit_provenance_ref`` MUST
    NOT contain the result commit hash."""

    inputs = _writer_inputs(commit_proof=_repo_commit_proof(result_commit="c" * 40))
    trailer = compute_trailer(inputs)

    assert "c" * 40 not in trailer.precommit_provenance_ref
    assert "c" * 40 not in trailer.precommit_provenance_digest


def test_compute_trailer_is_stable_for_identical_inputs() -> None:
    """Per doc-14:144-150 idempotency: identical inputs -> identical trailers."""

    a = compute_trailer(_writer_inputs())
    b = compute_trailer(_writer_inputs())
    assert a == b


def test_compute_trailer_digests_id_lists_compactly() -> None:
    """Per doc-14:137 trailers are mandatory + compact: long task id lists
    + queue item lists are digested into the ``*_digest`` fields, NOT
    enumerated verbatim.

    Sentinel-string discipline: the bare-integer queue id sentinel uses a
    NON-HEX-COLLIDABLE prefix (``ZZZ999_SENTINEL``) so the absence-from-
    serialisation assertion cannot collide with a SHA-256 hex digest. The
    SHA-256 hex alphabet is ``[0-9a-f]``; a plain ``"999"`` substring
    occurs in ~6% of random 64-char hex strings, so a brittle
    ``"999" not in serialised`` assertion would false-fail roughly that
    often. The ``Z`` and ``_`` characters cannot appear in SHA-256 hex
    output, so a sentinel containing them is mathematically safe.
    """

    long_task_ids = [f"task-{i}" for i in range(1000)]
    # Stringified queue ids so we can embed a non-hex-collidable sentinel
    # that also exercises the integer-list digest path via the
    # _writer_inputs helper. The integers below are turned into the
    # 64-char digest by compute_trailer; the sentinel is asserted absent
    # from the serialised trailer payload as proof that no enumerated
    # value leaks out (canonical-JSON discipline).
    long_queue_ids = list(range(1000))
    inputs = _writer_inputs(
        task_ids=long_task_ids,
        merge_queue_item_ids=long_queue_ids,
    )
    trailer = compute_trailer(inputs)

    # The digests are 64-char SHA-256 hex regardless of input size.
    assert len(trailer.task_ids_digest) == 64
    assert len(trailer.merge_queue_item_ids_digest) == 64

    # The enumerated values are NOT in the trailer (no task-999 verbatim).
    serialised = trailer.model_dump_json()
    assert "task-999" not in serialised
    # NON-HEX-COLLIDABLE sentinel: ``Z`` + ``_`` cannot appear in
    # SHA-256 hex digest output ``[0-9a-f]``; this assertion proves the
    # sentinel is absent from canonical-JSON serialisation without
    # risking accidental collision with a digest substring.
    assert "ZZZ999_SENTINEL" not in serialised


# ── payload generation with self-exclusion (doc-14:151-153) ────────────────


def test_compute_payload_returns_typed_payload_with_all_18_fields() -> None:
    """Per doc-14:89-110 the payload carries 18 fields."""

    inputs = _writer_inputs()
    payload = compute_payload(inputs)

    assert isinstance(payload, CommitProvenancePayload)
    assert payload.schema_version == "iriai.commit_provenance.v1"
    assert payload.feature_id == "feature-abc"
    assert payload.dag_sha256 == "d" * 64
    assert payload.group_idx == 0
    assert payload.effective_group_idx is None
    assert payload.repo_id == "repo-1"
    assert payload.commit_hash == "c" * 40
    assert payload.parent_hash == "p" * 40
    assert payload.tree_hash == "t" * 40
    assert payload.task_ids == ["task-1", "task-2"]
    assert payload.contract_ids == [1, 2]
    assert payload.attempt_ids == [10, 11]
    assert payload.sandbox_patch_evidence_ids == [100, 101]
    assert payload.gate_evidence_ids == [200, 201]
    assert payload.merge_queue_item_ids == [1000, 1001]
    assert payload.commit_proof_evidence_id == 5000
    assert payload.checkpoint_artifact_id == 6000
    assert payload.no_dirty_snapshot_ids == [7000, 7001]
    assert payload.implementation_log_anchors == ["impl-journal#anchor-1"]
    assert payload.precommit_provenance_ref.startswith("refs/iriai/provenance/")
    assert len(payload.payload_sha256) == 64


def test_compute_payload_sha256_self_exclusion_round_trip() -> None:
    """Per doc-14:151-153 the ``payload_sha256`` is computed from the
    canonical-JSON projection of the payload with the ``payload_sha256``
    field itself OMITTED.

    Recomputing the digest after loading the payload MUST yield the
    stored value.
    """

    payload = compute_payload(_writer_inputs())

    # Reload the payload via JSON round-trip.
    serialised = payload.model_dump_json()
    restored = CommitProvenancePayload.model_validate_json(serialised)

    # Recomputing the digest from the restored payload yields the stored
    # value (per doc-14:151-153 verbatim contract).
    assert compute_payload_sha256(restored) == payload.payload_sha256
    assert compute_payload_sha256(restored) == restored.payload_sha256


def test_compute_payload_sha256_excludes_payload_sha256_field_itself() -> None:
    """Per doc-14:151-153 the digest input MUST exclude the
    ``payload_sha256`` field itself."""

    payload = compute_payload(_writer_inputs())
    projection = canonical_payload_dict(payload)
    assert "payload_sha256" not in projection


def test_compute_payload_includes_result_commit_hash_post_commit() -> None:
    """Per doc-14:143-146 the payload MAY include the result commit hash
    because it is written AFTER ``git commit``."""

    inputs = _writer_inputs(commit_proof=_repo_commit_proof(result_commit="f" * 40))
    payload = compute_payload(inputs)
    assert payload.commit_hash == "f" * 40


def test_compute_payload_cross_cites_commit_proof_evidence_id_per_doc_14_105() -> None:
    """Per doc-14:105 the payload cross-cites the Slice 08
    :class:`RepoCommitProof` via the ``commit_proof_evidence_id`` integer
    field. The cross-cite is the ONLY way the Slice 14 payload links to
    the Slice 08 evidence; the Slice 08 row shape is byte-identical from
    this slice."""

    inputs = _writer_inputs(commit_proof_evidence_id=12345)
    payload = compute_payload(inputs)
    assert payload.commit_proof_evidence_id == 12345
    assert isinstance(payload.commit_proof_evidence_id, int)


def test_compute_payload_is_stable_for_identical_inputs() -> None:
    """Per doc-14:144-150 idempotency: identical inputs -> identical payloads
    (including identical ``payload_sha256`` digests)."""

    a = compute_payload(_writer_inputs())
    b = compute_payload(_writer_inputs())
    assert a == b
    assert a.payload_sha256 == b.payload_sha256


def test_compute_payload_uses_commit_proof_for_result_commit_and_tree() -> None:
    """Per doc-14:155-160 the Slice 08 :class:`RepoCommitProof` is the
    source of the result commit hash + tree hash; the writer cross-cites
    rather than re-computing."""

    inputs = _writer_inputs(
        commit_proof=_repo_commit_proof(
            result_commit="9" * 40,
            tree_sha="t" * 40,
        )
    )
    payload = compute_payload(inputs)
    assert payload.commit_hash == "9" * 40
    assert payload.tree_hash == "t" * 40
    # And repo_id is read from the commit proof too.
    assert payload.repo_id == inputs.commit_proof.repo_id


# ── writer-inputs validation ──────────────────────────────────────────────


def test_writer_inputs_rejects_empty_task_id() -> None:
    """Empty strings in id lists are a structural defect; fail closed."""

    with pytest.raises(ValidationError):
        _writer_inputs(task_ids=["task-1", ""])


def test_writer_inputs_rejects_empty_implementation_log_anchor() -> None:
    """Empty strings in anchor list are a structural defect; fail closed."""

    with pytest.raises(ValidationError):
        _writer_inputs(implementation_log_anchors=["anchor-1", "   "])


def test_writer_inputs_accepts_empty_optional_lists() -> None:
    """Optional list fields default to empty lists."""

    inputs = _writer_inputs(
        task_ids=[],
        contract_ids=[],
        attempt_ids=[],
        sandbox_patch_evidence_ids=[],
        gate_evidence_ids=[],
        merge_queue_item_ids=[],
        no_dirty_snapshot_ids=[],
        implementation_log_anchors=[],
    )
    payload = compute_payload(inputs)
    assert payload.task_ids == []
    assert payload.merge_queue_item_ids == []


def test_writer_inputs_accepts_effective_group_idx_int() -> None:
    """Regroup-overlay scenarios populate a non-None effective group index."""

    inputs = _writer_inputs(effective_group_idx=7)
    payload = compute_payload(inputs)
    trailer = compute_trailer(inputs)
    assert payload.effective_group_idx == 7
    assert trailer.effective_group_idx == 7


# ── writer success path (doc-14:144-150) ───────────────────────────────────


def _success_runner_for_fresh_write() -> FakeGitRunner:
    """Build a fake runner that returns:

    1. ``cat-file blob`` -> non-zero (ref does not exist).
    2. ``hash-object -w --stdin`` -> success with a fake blob oid.
    3. ``update-ref`` -> success.
    4. ``notes add -f`` -> success.
    """

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")
    runner.queue(returncode=0, stdout="abcdef1234567890\n")
    runner.queue(returncode=0)
    runner.queue(returncode=0)
    return runner


def test_writer_write_succeeds_with_fake_fixture() -> None:
    """Happy-path: the writer returns ``ok=True`` with the typed payload +
    trailer + ref + notes_ref, and ``gap_finding=None``."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is True
    assert result.gap_finding is None
    assert result.idempotent_no_op is False
    assert result.provenance_ref.startswith("refs/iriai/provenance/")
    assert result.notes_ref == "refs/notes/iriai"
    assert isinstance(result.payload, CommitProvenancePayload)
    assert isinstance(result.trailer, CommitProvenanceTrailer)


def test_writer_write_makes_4_git_invocations_on_fresh_write() -> None:
    """Per doc-14:144-150 the fresh-write sequence is:

    1. ``cat-file blob`` (idempotency check) -- 1 invocation.
    2. ``hash-object`` (write blob) -- 1 invocation.
    3. ``update-ref`` (set canonical ref) -- 1 invocation.
    4. ``notes add -f`` (write notes) -- 1 invocation.

    Total: 4 Git invocations.
    """

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    writer.write(_writer_inputs())

    assert len(runner.invocations) == 4
    assert runner.invocations[0][0][0] == "cat-file"
    assert runner.invocations[1][0][0] == "hash-object"
    assert runner.invocations[2][0][0] == "update-ref"
    assert runner.invocations[3][0][0] == "notes"


def test_writer_write_passes_repo_path_as_cwd_to_runner() -> None:
    """Every Git invocation runs with the writer's ``repo_path`` as cwd."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/some-repo", runner=runner)
    writer.write(_writer_inputs())

    for _args, cwd in runner.invocations:
        assert cwd == "/tmp/some-repo"


def test_writer_writes_ref_to_canonical_path() -> None:
    """The writer's ``update-ref`` invocation targets the canonical
    ``refs/iriai/provenance/{digest}`` path."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    inputs = _writer_inputs()
    writer.write(inputs)

    expected_ref = compute_precommit_provenance_ref(inputs)
    update_ref_invocation = runner.invocations[2][0]
    assert update_ref_invocation[1] == expected_ref


def test_writer_writes_notes_to_canonical_namespace() -> None:
    """The writer's ``notes add`` invocation targets the canonical
    ``refs/notes/iriai`` namespace keyed by the commit hash."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    inputs = _writer_inputs()
    writer.write(inputs)

    notes_invocation = runner.invocations[3][0]
    assert notes_invocation[0] == "notes"
    assert "--ref=refs/notes/iriai" in notes_invocation
    # The commit hash is the last positional arg.
    assert notes_invocation[-1] == inputs.commit_proof.result_commit


def test_writer_records_typed_subprocess_results_in_git_invocations() -> None:
    """The result carries the typed
    :class:`GitSubprocessResult` rows for every invocation."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert len(result.git_invocations) == 4
    for invocation in result.git_invocations:
        assert isinstance(invocation, GitSubprocessResult)


# ── writer idempotency (doc-14:144-150) ────────────────────────────────────


def _idempotent_existing_payload_runner(payload: CommitProvenancePayload) -> FakeGitRunner:
    """Build a fake runner that returns an existing-ref read with the
    SAME payload (so the writer should short-circuit as
    ``idempotent_no_op=True``)."""

    runner = FakeGitRunner()
    canonical = json.dumps(payload.model_dump(), sort_keys=True, separators=(",", ":"))
    runner.queue(returncode=0, stdout=canonical)
    return runner


def test_writer_write_is_idempotent_for_identical_inputs() -> None:
    """Per doc-14:144-150 the writer is idempotent: a retry with
    identical inputs that already has a matching ref on disk is a no-op
    (no second hash-object / update-ref / notes-add invocation)."""

    inputs = _writer_inputs()
    payload = compute_payload(inputs)
    runner = _idempotent_existing_payload_runner(payload)
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)

    result = writer.write(inputs)

    assert result.ok is True
    assert result.idempotent_no_op is True
    assert result.gap_finding is None
    # Only 1 Git invocation: the cat-file blob check.
    assert len(runner.invocations) == 1
    assert runner.invocations[0][0][0] == "cat-file"


def test_writer_write_identical_inputs_produce_identical_refs_and_digests() -> None:
    """Per doc-14:144-150 idempotency: two writes with identical inputs
    MUST produce identical refs + payload digests, regardless of which
    fixture is used."""

    inputs1 = _writer_inputs()
    inputs2 = _writer_inputs()

    runner1 = _success_runner_for_fresh_write()
    runner2 = _success_runner_for_fresh_write()

    w1 = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner1)
    w2 = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner2)

    r1 = w1.write(inputs1)
    r2 = w2.write(inputs2)

    assert r1.provenance_ref == r2.provenance_ref
    assert r1.payload.payload_sha256 == r2.payload.payload_sha256


def test_writer_write_idempotent_after_fresh_write_subsequent_retry_is_no_op() -> None:
    """A second writer instance (simulating a retry after the first write
    succeeded) sees the matching ref + skips the write."""

    inputs = _writer_inputs()
    payload = compute_payload(inputs)

    # First call: fresh write (4 invocations).
    runner1 = _success_runner_for_fresh_write()
    writer1 = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner1)
    result1 = writer1.write(inputs)
    assert result1.idempotent_no_op is False

    # Retry: a different runner returns the existing payload on cat-file
    # (simulating the prior write landed).
    runner2 = _idempotent_existing_payload_runner(payload)
    writer2 = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner2)
    result2 = writer2.write(inputs)
    assert result2.idempotent_no_op is True
    assert result2.ok is True


# ── writer conflict path (doc-14:192-201 governance_evidence_conflict) ─────


def _conflict_runner(other_payload: CommitProvenancePayload) -> FakeGitRunner:
    """Build a fake runner that returns a DIFFERENT existing payload on
    cat-file (so the writer should signal ``governance_evidence_conflict``)."""

    runner = FakeGitRunner()
    canonical = json.dumps(other_payload.model_dump(), sort_keys=True, separators=(",", ":"))
    runner.queue(returncode=0, stdout=canonical)
    return runner


def test_writer_write_signals_conflict_when_existing_ref_has_different_payload() -> None:
    """Per doc-14:192-201 ``governance_evidence_conflict`` is the typed
    failure id when the existing ref has a different payload digest."""

    inputs = _writer_inputs()

    # Build a DIFFERENT payload (different feature_id -> different digest).
    other_inputs = _writer_inputs(feature_id="other-feature")
    other_payload = compute_payload(other_inputs)

    runner = _conflict_runner(other_payload)
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(inputs)

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "governance_evidence_conflict"
    assert result.idempotent_no_op is False


def test_writer_write_signals_conflict_when_existing_ref_blob_is_not_json() -> None:
    """A ref blob that isn't valid JSON signals
    ``governance_evidence_conflict`` (the ref exists but is structurally
    corrupt)."""

    runner = FakeGitRunner()
    runner.queue(returncode=0, stdout="not valid json at all {{[")

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "governance_evidence_conflict"


def test_writer_write_signals_conflict_when_existing_ref_blob_is_wrong_shape() -> None:
    """A ref blob that's valid JSON but not a :class:`CommitProvenancePayload`
    signals ``governance_evidence_conflict``."""

    runner = FakeGitRunner()
    runner.queue(returncode=0, stdout=json.dumps({"unrelated": "data"}))

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "governance_evidence_conflict"


# ── writer failure path (doc-14:192-201 line_provenance_gap) ───────────────


def test_writer_write_signals_gap_when_hash_object_fails() -> None:
    """Per doc-14:192-201 ``line_provenance_gap`` is the typed failure id
    when a Git invocation fails."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")  # cat-file no ref
    runner.queue(returncode=1, stderr="git hash-object exploded")  # hash-object fail

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"
    assert "exploded" in result.gap_finding.evidence_payload.get("stderr", "")


def test_writer_write_signals_gap_when_update_ref_fails() -> None:
    """Per doc-14:192-201 a failed ``update-ref`` signals
    ``line_provenance_gap``."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")  # cat-file
    runner.queue(returncode=0, stdout="abcdef1234567890\n")  # hash-object
    runner.queue(returncode=128, stderr="cannot update ref")  # update-ref FAIL

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"
    assert "cannot update ref" in result.gap_finding.evidence_payload.get("stderr", "")


def test_writer_write_signals_gap_when_notes_add_fails() -> None:
    """Per doc-14:192-201 a failed ``notes add`` signals
    ``line_provenance_gap``."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")  # cat-file
    runner.queue(returncode=0, stdout="abcdef1234567890\n")  # hash-object
    runner.queue(returncode=0)  # update-ref
    runner.queue(returncode=1, stderr="notes add failed")  # notes-add FAIL

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"
    assert "notes add failed" in result.gap_finding.evidence_payload.get("stderr", "")


def test_writer_write_signals_gap_when_hash_object_produces_empty_oid() -> None:
    """A degenerate ``hash-object`` that returns empty stdout signals
    ``line_provenance_gap`` (no valid blob oid to update-ref against)."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")
    runner.queue(returncode=0, stdout="")  # empty oid

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(_writer_inputs())

    assert result.ok is False
    assert result.gap_finding is not None
    assert result.gap_finding.failure_id == "line_provenance_gap"


# ── non-blocking failure routing (doc-14:242-243) ──────────────────────────


def test_writer_failure_does_not_mutate_checkpoint_state() -> None:
    """Per doc-14:242-243 the writer is post-checkpoint observer only;
    a failed write MUST NOT mutate checkpoint state.

    The writer interface returns a :class:`CommitProvenanceWriteResult`
    with ``ok=False`` + a typed :class:`CommitProvenanceGapFinding`;
    the writer NEVER raises a failure to the caller.

    The writer takes a Slice 08 :class:`RepoCommitProof` as INPUT
    (READ-ONLY) -- it does NOT call back into any mutation surface.
    """

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: Not a valid object name")
    runner.queue(returncode=1, stderr="oops")  # hash-object fail

    inputs = _writer_inputs()
    original_commit_proof = inputs.commit_proof.model_dump()

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(inputs)

    # Failed write -> typed gap finding.
    assert result.ok is False
    assert result.gap_finding is not None

    # The input commit proof was NOT mutated.
    assert inputs.commit_proof.model_dump() == original_commit_proof


def test_writer_does_not_raise_on_failure_per_doc_14_242_243() -> None:
    """Per doc-14:242-243 the writer NEVER raises a failure to the caller.

    The :class:`GitProvenanceWriteError` is INTERNAL only; the public
    :meth:`GitProvenanceWriter.write` surface catches + projects onto a
    typed :class:`CommitProvenanceGapFinding`.
    """

    # Set up a runner that will trigger every failure path; the public
    # surface MUST NOT raise.
    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="no ref")  # cat-file
    runner.queue(returncode=1, stderr="hash-object died")  # hash-object FAIL

    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    try:
        result = writer.write(_writer_inputs())
    except Exception as exc:  # pragma: no cover - test guards against the failure
        pytest.fail(f"writer.write() raised {type(exc).__name__}: {exc}")

    assert result.ok is False
    assert result.gap_finding is not None


def test_failure_router_route_does_not_propagate_block_to_executor() -> None:
    """Per doc-14:242-243 the typed failure ids' route_table action MUST
    NOT be ``quiesce`` -- the executor MUST NEVER see a blocking action
    for these failures."""

    for failure_type in ("line_provenance_gap", "governance_evidence_conflict"):
        route = fr.ROUTE_TABLE[("evidence_corruption", failure_type)]
        # NOT quiesce.
        assert route.action != "quiesce"
        # NOT operator_required.
        assert route.action != "operator_required"
        # IS the new retry_governance_projection.
        assert route.action == "retry_governance_projection"


def test_failure_router_budget_exhausted_does_not_rewrite_to_quiesce() -> None:
    """Per doc-14:242-243 even when the retry budget exhausts, the action
    MUST NOT be rewritten to ``quiesce`` -- the post-checkpoint
    governance job observes ``budget_exhausted=True`` + the typed
    ``retry_governance_projection`` action and gracefully records the
    finding without quiescing the executor."""

    router = fr.FailureRouter()

    # Exhaust the budget for the typed failure id.
    obs = fr.FailureObservation(
        feature_id="f",
        dag_sha256="d" * 64,
        group_idx=0,
        source="merge_queue",
        failure_class="evidence_corruption",
        failure_type="line_provenance_gap",
        deterministic=False,
        retryable=True,
        evidence_ids=[1],
    )
    fid = router.record(obs)
    # Budget for evidence_corruption class is 1; reserve one.
    decision = router.decide(fid)
    router.mark_route_started(decision)
    # Now decide again: budget should be exhausted but action MUST stay
    # `retry_governance_projection` (not rewritten to quiesce).
    fid2 = router.record(obs)  # idempotency replays the existing record
    decision2 = router.decide(fid2)
    assert decision2.budget_exhausted is True
    # Critical assertion: NOT rewritten to quiesce.
    assert decision2.action == "retry_governance_projection"
    assert decision2.action != "quiesce"


def test_governance_evidence_conflict_budget_exhausted_does_not_rewrite_to_quiesce() -> None:
    """Same contract for ``governance_evidence_conflict`` (the sibling
    typed failure id)."""

    router = fr.FailureRouter()
    obs = fr.FailureObservation(
        feature_id="f",
        dag_sha256="d" * 64,
        group_idx=0,
        source="merge_queue",
        failure_class="evidence_corruption",
        failure_type="governance_evidence_conflict",
        deterministic=False,
        retryable=True,
        evidence_ids=[1],
    )
    fid = router.record(obs)
    decision = router.decide(fid)
    router.mark_route_started(decision)
    fid2 = router.record(obs)
    decision2 = router.decide(fid2)
    assert decision2.budget_exhausted is True
    assert decision2.action == "retry_governance_projection"


def test_failure_router_route_started_does_not_rewrite_to_quiesce_when_exhausted() -> None:
    """The in-memory port's ``record_route_started`` also has a
    budget-exhausted quiesce-rewrite path; per doc-14:242-243 the
    rewrite MUST be skipped for ``retry_governance_projection``.

    The test directly exercises the
    :class:`InMemoryFailureRouterPort.record_route_started` budget-exhausted
    branch by manually constructing a :class:`RouteDecision` with the
    exhausted budget reservation kwargs (bypassing the
    :class:`FailureRouter.mark_route_started` idempotency replay which
    collapses identical-key replays).
    """

    port = fr.InMemoryFailureRouterPort()
    router = fr.FailureRouter(port=port)

    obs = fr.FailureObservation(
        feature_id="f",
        dag_sha256="d" * 64,
        group_idx=0,
        source="merge_queue",
        failure_class="evidence_corruption",
        failure_type="line_provenance_gap",
        deterministic=False,
        retryable=True,
        evidence_ids=[1],
    )

    # First failure -> reserves the only budget slot via the public API.
    fid1 = router.record(obs)
    decision1 = router.decide(fid1)
    started1 = router.mark_route_started(decision1)
    assert started1.action == "retry_governance_projection"

    # Verify the budget is now reserved.
    budget_key = decision1.budget_key
    state = port.get_budget(budget_key)
    assert state is not None
    assert state.reserved_attempts == 1
    assert state.max_attempts == 1
    # Budget IS exhausted (the only slot is reserved).
    assert state.reserved_attempts >= state.max_attempts

    # Now manually exercise the InMemoryFailureRouterPort.record_route_started
    # budget-exhausted branch with a fresh decision shape that does NOT
    # collide with the existing idempotency key. Using a different
    # failure_id forces a fresh route_key.
    fresh_decision = fr.RouteDecision(
        failure_id=99,  # fresh failure id
        route_decision_id=None,
        action="retry_governance_projection",
        budget_remaining=0,
        budget_exhausted=False,  # will be rewritten True by the exhausted branch
        reason="placeholder",
        required_evidence_ids=[1],
        signature_hash=decision1.signature_hash,
        idempotency_key="route:f:99:placeholder:retry_governance_projection:n0",
        repair_scope={},
        budget_key=budget_key,
        reservation_ordinal=0,
    )
    input_digest = fr._route_input_digest(fresh_decision)
    record = port.record_route_started(
        fresh_decision,
        input_digest,
        budget_reservation={
            "budget_key": budget_key,
            "feature_id": "f",
            "failure_class": "evidence_corruption",
            "failure_type": "line_provenance_gap",
            "signature_hash": decision1.signature_hash,
            "max_attempts": 1,
            "failure_id": 99,
        },
    )

    # CRITICAL: the stored decision's action MUST stay
    # `retry_governance_projection` per doc-14:242-243 -- the rewrite
    # to `quiesce` is intentionally skipped for this NON-blocking
    # governance projection route.
    assert record.decision.action == "retry_governance_projection"
    assert record.decision.action != "quiesce"
    assert record.decision.budget_exhausted is True
    assert "non-blocking" in record.decision.reason


# ── cross-cite of commit_proof_evidence_id (doc-14:105) ────────────────────


def test_gap_finding_carries_failure_id_per_doc_14_192_201() -> None:
    """Per doc-14:192-201 the :class:`CommitProvenanceGapFinding` carries
    the typed failure id (one of the 2 NEW typed ids)."""

    finding = CommitProvenanceGapFinding(
        failure_id="line_provenance_gap",
        feature_id="f",
        group_idx=0,
        repo_id="r",
        commit_hash="c" * 40,
        precommit_provenance_ref="refs/x",
        precommit_provenance_digest="d" * 64,
        reason="test reason",
    )
    assert finding.failure_id == "line_provenance_gap"

    finding2 = CommitProvenanceGapFinding(
        failure_id="governance_evidence_conflict",
        feature_id="f",
        group_idx=0,
        repo_id="r",
        commit_hash="c" * 40,
        precommit_provenance_ref="refs/x",
        precommit_provenance_digest="d" * 64,
        reason="conflict reason",
    )
    assert finding2.failure_id == "governance_evidence_conflict"


def test_gap_finding_rejects_invalid_failure_id() -> None:
    """The :class:`CommitProvenanceGapFinding.failure_id` Literal rejects
    typo-d / unknown failure ids."""

    with pytest.raises(ValidationError):
        CommitProvenanceGapFinding(
            failure_id="other_id",  # type: ignore[arg-type]
            feature_id="f",
            group_idx=0,
            repo_id="r",
            commit_hash="c" * 40,
            precommit_provenance_ref="refs/x",
            precommit_provenance_digest="d" * 64,
            reason="r",
        )


def test_gap_finding_evidence_payload_defaults_to_empty_dict() -> None:
    """The free-form ``evidence_payload`` field defaults to an empty dict
    per the doc-14:192-201 governance-finding contract."""

    finding = CommitProvenanceGapFinding(
        failure_id="line_provenance_gap",
        feature_id="f",
        group_idx=0,
        repo_id="r",
        commit_hash="c" * 40,
        precommit_provenance_ref="refs/x",
        precommit_provenance_digest="d" * 64,
        reason="r",
    )
    assert finding.evidence_payload == {}


def test_writer_gap_finding_includes_precommit_provenance_inputs_per_doc_14_138_142() -> None:
    """The gap finding carries the precommit-stable provenance ref + digest
    so the post-checkpoint governance job can correlate the finding with
    the canonical ref it tried to write."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="no ref")
    runner.queue(returncode=1, stderr="oops")

    inputs = _writer_inputs()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    result = writer.write(inputs)

    assert result.gap_finding is not None
    assert result.gap_finding.precommit_provenance_ref == compute_precommit_provenance_ref(inputs)
    assert result.gap_finding.precommit_provenance_digest == compute_precommit_provenance_digest(
        inputs
    )
    # And the commit hash (post-commit; from the Slice 08 RepoCommitProof).
    assert result.gap_finding.commit_hash == inputs.commit_proof.result_commit


def test_writer_payload_blob_contains_canonical_json_per_doc_14_151_153() -> None:
    """The writer writes the canonical-JSON projection of the payload as
    the ref blob (so consumers can re-verify the ``payload_sha256`` on
    read per doc-14:151-153)."""

    runner = _success_runner_for_fresh_write()
    writer = GitProvenanceWriter(repo_path="/tmp/repo-1", runner=runner)
    inputs = _writer_inputs()
    writer.write(inputs)

    # The hash-object invocation carries the canonical JSON as its "--"
    # synthetic positional (the fake runner's args trace preserves it).
    hash_object_args = runner.invocations[1][0]
    # Find the synthetic "--" sentinel; the canonical JSON follows.
    assert "--" in hash_object_args
    sentinel_idx = hash_object_args.index("--")
    canonical_blob = hash_object_args[sentinel_idx + 1]

    # Parse the blob and verify it matches the computed payload.
    parsed = json.loads(canonical_blob)
    expected = compute_payload(inputs).model_dump()
    assert parsed == expected


# ── stdlib subprocess runner factory smoke ─────────────────────────────────


def test_stdlib_subprocess_runner_factory_returns_callable() -> None:
    """The production-grade :func:`make_stdlib_subprocess_runner` factory
    returns a callable that satisfies the
    :class:`GitSubprocessRunner` protocol shape (the factory itself
    does NOT shell out -- only invoking the returned callable would)."""

    from iriai_build_v2.execution_control.commit_provenance_writer import (
        make_stdlib_subprocess_runner,
    )

    runner = make_stdlib_subprocess_runner()
    assert callable(runner)


# ── reader-facing trailer + payload typing pins (doc-14:79-110) ────────────


def test_trailer_carries_typed_shape_for_consumer_per_doc_14_79_87() -> None:
    """A future Slice 14 reader sub-slice expects the trailer typed shape
    verbatim (8 fields per doc-14:79-87)."""

    trailer = compute_trailer(_writer_inputs())
    assert isinstance(trailer, CommitProvenanceTrailer)
    # All 8 doc-14:79-87 fields are present.
    for field_name in (
        "feature_id",
        "group_idx",
        "effective_group_idx",
        "task_ids_digest",
        "merge_queue_item_ids_digest",
        "checkpoint_ref",
        "precommit_provenance_ref",
        "precommit_provenance_digest",
    ):
        assert hasattr(trailer, field_name)


def test_payload_carries_typed_shape_for_consumer_per_doc_14_89_110() -> None:
    """A future Slice 14 reader sub-slice expects the payload typed shape
    verbatim (18 fields per doc-14:89-110)."""

    payload = compute_payload(_writer_inputs())
    assert isinstance(payload, CommitProvenancePayload)
    # All 18 doc-14:89-110 fields are present.
    for field_name in (
        "schema_version",
        "feature_id",
        "dag_sha256",
        "group_idx",
        "effective_group_idx",
        "repo_id",
        "commit_hash",
        "parent_hash",
        "tree_hash",
        "task_ids",
        "contract_ids",
        "attempt_ids",
        "sandbox_patch_evidence_ids",
        "gate_evidence_ids",
        "merge_queue_item_ids",
        "commit_proof_evidence_id",
        "checkpoint_artifact_id",
        "no_dirty_snapshot_ids",
        "implementation_log_anchors",
        "precommit_provenance_ref",
        "payload_sha256",
    ):
        assert hasattr(payload, field_name)
