"""Slice 14 third sub-slice -- unit tests for the
``execution_control/commit_provenance_reader.py`` line-provenance reader
module.

Covers (per the implementer prompt § "MUST DO" item 3):

- Query -> result round-trip via fake Git subprocess runner.
- Precedence order: typed ``dag-commit-proof:*`` evidence FIRST, then Git
  notes/refs payload, then commit trailers (doc-14:182-184); NEVER treats
  trailers alone as full proof (doc-14:188).
- ``completeness="paged"`` -> exact :class:`GovernanceEvidencePageRef` list
  when line range exceeds inline caps (doc-14:199-201).
- ``completeness="preview_only"`` -> INELIGIBLE to feed downstream
  consumers (doc-14:202-205; negative test).
- Rebase/cherry-pick lineage walk (doc-14:212-213).
- Trailers-only case -> reader returns partial evidence + records gap
  finding (NEVER treats trailers alone as full proof per doc-14:182-184).
- Fake Git subprocess fixture; NO shell-out to real ``git``.
- ``ConfigDict(extra="forbid")`` discipline on all new typed surfaces.

The 2 typed failure ids registered in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router`
(``line_provenance_gap`` + ``governance_evidence_conflict``) are REUSED;
this 3rd sub-slice ADDS NO new failure ids. Tests pin the REUSE invariant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.commit_provenance import (
    CommitProvenancePayload,
    CommitProvenanceTrailer,
    LineProvenanceQuery,
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.commit_provenance_reader import (
    COMMIT_PROVENANCE_GAP_FAILURE_IDS,
    BlameLine,
    CommitProofProvider,
    CommitProofRow,
    LineageRecord,
    LineageWalker,
    LineProvenanceReader,
    LineProvenanceReadResult,
    PayloadStore,
    TrailerSource,
    compute_line_provenance_completeness_digest,
    parse_blame_porcelain,
    parse_trailer_from_commit_body,
)
from iriai_build_v2.execution_control.commit_provenance_writer import (
    CommitProvenanceGapFinding,
    GitSubprocessResult,
    GitSubprocessRunner,
    compute_payload,
    compute_precommit_provenance_ref,
    compute_trailer,
    CommitProvenanceWriterInputs,
)
from iriai_build_v2.execution_control.merge_queue_store import RepoCommitProof
from iriai_build_v2.workflows.develop.execution import failure_router as fr
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidencePageRef,
)


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
    """Construct full writer inputs (mirrors writer test helpers)."""

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


def _commit_proof_row(commit_hash: str = "c" * 40, **overrides: Any) -> CommitProofRow:
    """Construct a fully-specified :class:`CommitProofRow` for tests."""

    base: dict[str, Any] = dict(
        commit_hash=commit_hash,
        repo_id="repo-1",
        task_ids=["task-1", "task-2"],
        precommit_provenance_ref=(
            f"refs/iriai/provenance/{('d' * 64)}"
        ),
        commit_proof=_repo_commit_proof(result_commit=commit_hash),
    )
    base.update(overrides)
    return CommitProofRow(**base)


def _query(**overrides: Any) -> LineProvenanceQuery:
    """Construct a fully-specified :class:`LineProvenanceQuery` for tests."""

    base: dict[str, Any] = dict(
        repo_id="repo-1",
        ref="HEAD",
        path="src/a.py",
        line_start=1,
        line_end=2,
    )
    base.update(overrides)
    return LineProvenanceQuery(**base)


@dataclass
class FakeGitRunner:
    """Fake :class:`GitSubprocessRunner` for unit tests.

    Records every invocation; returns canned results from a queue. Per the
    implementer prompt MUST NOT shell out to real git in unit tests.
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

    def queue_blame_porcelain(self, blame_text: str) -> None:
        """Queue a canned ``git blame --porcelain`` response."""

        self.queue(returncode=0, stdout=blame_text)


@dataclass
class FakeCommitProofProvider:
    """Fake :class:`CommitProofProvider` for unit tests.

    Returns canned :class:`CommitProofRow` rows from a dict keyed by
    ``(repo_id, commit_hash)``.
    """

    rows: dict[tuple[str, str], CommitProofRow] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_commit_proof(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProofRow | None:
        self.calls.append((repo_id, commit_hash))
        return self.rows.get((repo_id, commit_hash))

    def add(self, row: CommitProofRow) -> None:
        self.rows[(row.repo_id, row.commit_hash)] = row


@dataclass
class FakePayloadStore:
    """Fake :class:`PayloadStore` for unit tests.

    Returns canned :class:`CommitProvenancePayload` rows from two dicts:
    one keyed by ``(repo_id, ref)`` for the ref-lookup path, one keyed by
    ``(repo_id, commit_hash)`` for the notes-lookup path.
    """

    payloads_by_ref: dict[tuple[str, str], CommitProvenancePayload] = field(
        default_factory=dict
    )
    payloads_by_notes: dict[tuple[str, str], CommitProvenancePayload] = field(
        default_factory=dict
    )
    ref_calls: list[tuple[str, str]] = field(default_factory=list)
    notes_calls: list[tuple[str, str]] = field(default_factory=list)

    def get_payload_by_ref(
        self,
        *,
        repo_id: str,
        ref: str,
    ) -> CommitProvenancePayload | None:
        self.ref_calls.append((repo_id, ref))
        return self.payloads_by_ref.get((repo_id, ref))

    def get_payload_from_notes(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProvenancePayload | None:
        self.notes_calls.append((repo_id, commit_hash))
        return self.payloads_by_notes.get((repo_id, commit_hash))


@dataclass
class FakeTrailerSource:
    """Fake :class:`TrailerSource` for unit tests."""

    trailers: dict[tuple[str, str], CommitProvenanceTrailer] = field(
        default_factory=dict
    )
    calls: list[tuple[str, str]] = field(default_factory=list)

    def get_trailer(
        self,
        *,
        repo_id: str,
        commit_hash: str,
    ) -> CommitProvenanceTrailer | None:
        self.calls.append((repo_id, commit_hash))
        return self.trailers.get((repo_id, commit_hash))


@dataclass
class FakeLineageWalker:
    """Fake :class:`LineageWalker` for unit tests."""

    records: dict[tuple[str, str], LineageRecord] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)

    def walk_from_old(
        self,
        *,
        repo_id: str,
        old_commit_hash: str,
    ) -> LineageRecord | None:
        self.calls.append((repo_id, old_commit_hash))
        return self.records.get((repo_id, old_commit_hash))


def _make_reader(
    *,
    runner: FakeGitRunner | None = None,
    commit_proof_provider: FakeCommitProofProvider | None = None,
    payload_store: FakePayloadStore | None = None,
    trailer_source: FakeTrailerSource | None = None,
    lineage_walker: FakeLineageWalker | None = None,
) -> tuple[
    LineProvenanceReader,
    FakeGitRunner,
    FakeCommitProofProvider,
    FakePayloadStore,
    FakeTrailerSource,
    FakeLineageWalker,
]:
    """Construct a reader bound to fake ports for tests."""

    r = runner or FakeGitRunner()
    cp = commit_proof_provider or FakeCommitProofProvider()
    ps = payload_store or FakePayloadStore()
    ts = trailer_source or FakeTrailerSource()
    lw = lineage_walker or FakeLineageWalker()
    reader = LineProvenanceReader(
        repo_path="/tmp/repo-1",
        runner=r,
        commit_proof_provider=cp,
        payload_store=ps,
        trailer_source=ts,
        lineage_walker=lw,
    )
    return reader, r, cp, ps, ts, lw


def _blame_porcelain_single_commit(
    commit_hash: str,
    line_start: int = 1,
    line_count: int = 2,
) -> str:
    """Build a porcelain blame block with all lines from one commit."""

    blocks = []
    for i in range(line_count):
        orig = line_start + i
        final = line_start + i
        block = (
            f"{commit_hash} {orig} {final} 1\n"
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
            f"\tline {final} content\n"
        )
        blocks.append(block)
    return "".join(blocks)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed projections + ports + reader + helpers."""

    from iriai_build_v2.execution_control import commit_provenance_reader as mod

    expected = {
        "CommitProofRow",
        "BlameLine",
        "LineageRecord",
        "CommitProofProvider",
        "PayloadStore",
        "TrailerSource",
        "LineageWalker",
        "LineProvenanceReader",
        "LineProvenanceReadResult",
        "parse_blame_porcelain",
        "parse_trailer_from_commit_body",
        "compute_line_provenance_completeness_digest",
        "COMMIT_PROVENANCE_GAP_FAILURE_IDS",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_reader_module_does_not_re_export_via_execution_control_init() -> None:
    """Per the Slice 14 1st + 2nd sub-slice precedent the reader module is
    NOT re-exported from ``execution_control/__init__.py``."""

    from iriai_build_v2 import execution_control

    pkg_all = getattr(execution_control, "__all__", [])
    assert "LineProvenanceReader" not in pkg_all
    assert "CommitProofRow" not in pkg_all


# ── ConfigDict(extra="forbid") discipline ─────────────────────────────────


def test_commit_proof_row_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`CommitProofRow`."""

    with pytest.raises(ValidationError):
        _commit_proof_row(unknown_field="oops")  # type: ignore[arg-type]


def test_blame_line_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`BlameLine`."""

    with pytest.raises(ValidationError):
        BlameLine(
            commit_hash="c" * 40,
            original_line=1,
            final_line=1,
            unknown_field="oops",  # type: ignore[arg-type]
        )


def test_lineage_record_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`LineageRecord`."""

    with pytest.raises(ValidationError):
        LineageRecord(
            old_commit_hash="c" * 40,
            new_commit_hash="d" * 40,
            reason="rebase",
            unknown_field="oops",  # type: ignore[arg-type]
        )


def test_lineage_record_rejects_invalid_reason() -> None:
    """The :class:`LineageRecord.reason` Literal rejects typo'd values."""

    with pytest.raises(ValidationError):
        LineageRecord(
            old_commit_hash="c" * 40,
            new_commit_hash="d" * 40,
            reason="not_a_real_reason",  # type: ignore[arg-type]
        )


def test_line_provenance_read_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` on :class:`LineProvenanceReadResult`."""

    result = LineProvenanceResult(
        commit_hashes=[],
        task_ids=[],
        provenance_payload_refs=[],
        page_refs=[],
        completeness="unavailable",
        completeness_digest="abc",
        confidence=0.0,
        gaps=[],
    )
    with pytest.raises(ValidationError):
        LineProvenanceReadResult(
            result=result,
            unknown_field="oops",  # type: ignore[arg-type]
        )


# ── COMMIT_PROVENANCE_GAP_FAILURE_IDS reuse (no new ids) ───────────────────


def test_commit_provenance_gap_failure_ids_reuses_2nd_sub_slice_tuple() -> None:
    """Per the implementer prompt MUST NOT register new typed failure ids;
    REUSE the 2nd sub-slice tuple verbatim."""

    assert COMMIT_PROVENANCE_GAP_FAILURE_IDS == (
        "line_provenance_gap",
        "governance_evidence_conflict",
    )

    # Confirm the same Object identity comes from the writer module too.
    from iriai_build_v2.execution_control.commit_provenance_writer import (
        COMMIT_PROVENANCE_GAP_FAILURE_IDS as writer_ids,
    )

    assert COMMIT_PROVENANCE_GAP_FAILURE_IDS is writer_ids


def test_reader_uses_existing_failure_router_registrations() -> None:
    """The reader REUSES the failure ids already registered by the 2nd sub-slice.
    No new failure_class / failure_type / route_action entries are added."""

    # The 2 typed failure ids exist under EXISTING evidence_corruption class.
    assert "line_provenance_gap" in fr.FAILURE_TYPES
    assert "governance_evidence_conflict" in fr.FAILURE_TYPES
    assert ("evidence_corruption", "line_provenance_gap") in fr.ROUTE_TABLE
    assert (
        ("evidence_corruption", "governance_evidence_conflict") in fr.ROUTE_TABLE
    )

    # Both route to NON-blocking retry_governance_projection (NOT quiesce).
    gap = fr.ROUTE_TABLE[("evidence_corruption", "line_provenance_gap")]
    conflict = fr.ROUTE_TABLE[
        ("evidence_corruption", "governance_evidence_conflict")
    ]
    assert gap.action == "retry_governance_projection"
    assert conflict.action == "retry_governance_projection"


# ── parse_blame_porcelain (doc-14:171-173) ─────────────────────────────────


def test_parse_blame_porcelain_single_commit_single_line() -> None:
    """Parses a one-commit / one-line porcelain block."""

    porcelain = _blame_porcelain_single_commit("c" * 40, line_start=1, line_count=1)
    lines = parse_blame_porcelain(porcelain)
    assert len(lines) == 1
    assert lines[0].commit_hash == "c" * 40
    assert lines[0].original_line == 1
    assert lines[0].final_line == 1
    assert lines[0].content == "line 1 content"


def test_parse_blame_porcelain_multi_line_multi_commit() -> None:
    """Parses a multi-commit multi-line porcelain stream."""

    line1 = _blame_porcelain_single_commit("a" * 40, 1, 1)
    line2 = _blame_porcelain_single_commit("b" * 40, 2, 1)
    lines = parse_blame_porcelain(line1 + line2)
    assert len(lines) == 2
    assert lines[0].commit_hash == "a" * 40
    assert lines[1].commit_hash == "b" * 40


def test_parse_blame_porcelain_empty_input_returns_empty_list() -> None:
    """Empty porcelain output yields an empty list."""

    assert parse_blame_porcelain("") == []


def test_parse_blame_porcelain_malformed_skips_bad_lines() -> None:
    """Per the auto-memory ``feedback_no_silent_degradation`` rule malformed
    lines are SKIPPED (not silently merged into the next valid line)."""

    porcelain = (
        "not-a-hash 1 1\n"
        "garbage\n"
        + _blame_porcelain_single_commit("a" * 40, 1, 1)
    )
    lines = parse_blame_porcelain(porcelain)
    # Only the well-formed block parses.
    assert len(lines) == 1
    assert lines[0].commit_hash == "a" * 40


def test_parse_blame_porcelain_tolerates_crlf_line_endings() -> None:
    """Tolerates Windows-style ``\\r\\n`` line endings."""

    porcelain = _blame_porcelain_single_commit("a" * 40, 1, 1)
    crlf = porcelain.replace("\n", "\r\n")
    lines = parse_blame_porcelain(crlf)
    assert len(lines) == 1
    assert lines[0].commit_hash == "a" * 40


# ── parse_trailer_from_commit_body (doc-14:79-87) ──────────────────────────


def test_parse_trailer_from_commit_body_full_8_fields() -> None:
    """Parses a complete commit body with all 8 iriai trailer keys."""

    body = (
        "Commit subject\n"
        "\n"
        "Some commit body text.\n"
        "\n"
        "Iriai-Feature-Id: feature-abc\n"
        "Iriai-Group-Idx: 0\n"
        "Iriai-Effective-Group-Idx: -\n"
        "Iriai-Task-Ids-Digest: " + ("a" * 64) + "\n"
        "Iriai-Merge-Queue-Item-Ids-Digest: " + ("b" * 64) + "\n"
        "Iriai-Checkpoint-Ref: dag-group:0\n"
        "Iriai-Precommit-Provenance-Ref: refs/iriai/provenance/abc\n"
        "Iriai-Precommit-Provenance-Digest: " + ("c" * 64) + "\n"
    )
    trailer = parse_trailer_from_commit_body(body)
    assert trailer is not None
    assert trailer.feature_id == "feature-abc"
    assert trailer.group_idx == 0
    assert trailer.effective_group_idx is None  # parsed from "-"
    assert trailer.checkpoint_ref == "dag-group:0"


def test_parse_trailer_with_int_effective_group_idx() -> None:
    """An integer ``effective_group_idx`` is parsed correctly."""

    body = (
        "subject\n"
        "\n"
        "Iriai-Feature-Id: f\n"
        "Iriai-Group-Idx: 1\n"
        "Iriai-Effective-Group-Idx: 7\n"
        "Iriai-Task-Ids-Digest: " + ("a" * 64) + "\n"
        "Iriai-Merge-Queue-Item-Ids-Digest: " + ("b" * 64) + "\n"
        "Iriai-Checkpoint-Ref: r\n"
        "Iriai-Precommit-Provenance-Ref: r\n"
        "Iriai-Precommit-Provenance-Digest: " + ("c" * 64) + "\n"
    )
    trailer = parse_trailer_from_commit_body(body)
    assert trailer is not None
    assert trailer.effective_group_idx == 7


def test_parse_trailer_returns_none_for_partial_trailer() -> None:
    """Per feedback_no_silent_degradation a body with only SOME trailer keys
    returns None (treated as missing, not as a partial overlay)."""

    body = (
        "subject\n"
        "Iriai-Feature-Id: f\n"
        "Iriai-Group-Idx: 0\n"
        # ... missing the other 6 required keys ...
    )
    assert parse_trailer_from_commit_body(body) is None


def test_parse_trailer_returns_none_for_no_iriai_trailers() -> None:
    """A legacy commit body without iriai trailers returns None."""

    body = "subject\n\nSome body text without any trailers.\n"
    assert parse_trailer_from_commit_body(body) is None


def test_parse_trailer_returns_none_for_invalid_int_group_idx() -> None:
    """A non-integer group_idx returns None (typed validation failure)."""

    body = (
        "subject\n"
        "Iriai-Feature-Id: f\n"
        "Iriai-Group-Idx: not-an-int\n"
        "Iriai-Effective-Group-Idx: -\n"
        "Iriai-Task-Ids-Digest: " + ("a" * 64) + "\n"
        "Iriai-Merge-Queue-Item-Ids-Digest: " + ("b" * 64) + "\n"
        "Iriai-Checkpoint-Ref: r\n"
        "Iriai-Precommit-Provenance-Ref: r\n"
        "Iriai-Precommit-Provenance-Digest: " + ("c" * 64) + "\n"
    )
    assert parse_trailer_from_commit_body(body) is None


# ── compute_line_provenance_completeness_digest ────────────────────────────


def test_completeness_digest_self_excludes_completeness_digest_field() -> None:
    """The digest helper excludes the ``completeness_digest`` field itself
    (mirrors the payload_sha256 self-exclusion at doc-14:151-153)."""

    result = LineProvenanceResult(
        commit_hashes=["c1"],
        task_ids=["task-1"],
        provenance_payload_refs=["refs/r/1"],
        page_refs=[],
        completeness="complete",
        completeness_digest="placeholder",
        confidence=1.0,
        gaps=[],
    )
    digest1 = compute_line_provenance_completeness_digest(result)

    # Re-load with a DIFFERENT placeholder; the digest must be identical.
    other = result.model_copy(update={"completeness_digest": "different-value"})
    digest2 = compute_line_provenance_completeness_digest(other)
    assert digest1 == digest2
    # And it's a 64-char SHA-256 hex.
    assert len(digest1) == 64


def test_completeness_digest_is_stable_across_field_ordering() -> None:
    """The digest helper uses canonical JSON so dict-key ordering is
    irrelevant."""

    r1 = LineProvenanceResult(
        commit_hashes=["c1"],
        task_ids=["t1"],
        provenance_payload_refs=["r1"],
        page_refs=[],
        completeness="complete",
        completeness_digest="x",
        confidence=1.0,
        gaps=[],
    )
    r2 = LineProvenanceResult(
        commit_hashes=["c1"],
        task_ids=["t1"],
        provenance_payload_refs=["r1"],
        page_refs=[],
        completeness="complete",
        completeness_digest="y",
        confidence=1.0,
        gaps=[],
    )
    assert compute_line_provenance_completeness_digest(
        r1
    ) == compute_line_provenance_completeness_digest(r2)


# ── happy-path reader (precedence: typed proof FIRST) ──────────────────────


def test_reader_returns_complete_result_via_typed_proof_first() -> None:
    """Per doc-14:182-184 typed proof is the FIRST priority. The happy path
    is: blame returns commits -> proof provider returns typed proofs ->
    result is ``completeness="complete"`` + confidence=1.0."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 2))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=commit_hash))

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    rr = reader.read(_query())

    assert rr.gap_finding is None
    assert rr.result.completeness == "complete"
    assert rr.result.confidence == 1.0
    assert rr.result.commit_hashes == [commit_hash]
    assert rr.result.task_ids == ["task-1", "task-2"]
    assert rr.is_eligible_for_downstream_consumers is True


def test_reader_consults_typed_proof_before_payload() -> None:
    """Per doc-14:182-184 the reader MUST consult typed proof BEFORE payload.
    If both are available, typed proof wins."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=commit_hash, task_ids=["task-typed"]))

    # Also queue a payload at the notes namespace.
    ps = FakePayloadStore()
    other_payload = compute_payload(
        _writer_inputs(
            commit_proof=_repo_commit_proof(result_commit=commit_hash),
            task_ids=["task-payload"],
        )
    )
    ps.payloads_by_notes[("repo-1", commit_hash)] = other_payload

    reader, _, _, _, _, _ = _make_reader(
        runner=runner, commit_proof_provider=cp, payload_store=ps
    )
    rr = reader.read(_query())

    # Typed proof wins -> task_ids from typed proof.
    assert rr.result.task_ids == ["task-typed"]
    # The payload store's notes lookup was NEVER consulted (typed proof
    # short-circuited).
    assert ps.notes_calls == []


# ── precedence: payload (SECOND) ───────────────────────────────────────────


def test_reader_falls_back_to_payload_when_typed_proof_missing() -> None:
    """Per doc-14:182-184 when typed proof is missing the reader falls back
    to the payload (SECOND priority)."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    # No typed proof.
    cp = FakeCommitProofProvider()

    # Payload at notes.
    ps = FakePayloadStore()
    payload = compute_payload(
        _writer_inputs(
            commit_proof=_repo_commit_proof(result_commit=commit_hash),
            task_ids=["task-from-payload"],
        )
    )
    ps.payloads_by_notes[("repo-1", commit_hash)] = payload

    reader, _, cp_out, ps_out, _, _ = _make_reader(
        runner=runner, commit_proof_provider=cp, payload_store=ps
    )
    rr = reader.read(_query())

    # Typed proof was consulted (FIRST), then fell through to payload.
    assert cp_out.calls == [("repo-1", commit_hash)]
    assert ps_out.notes_calls == [("repo-1", commit_hash)]

    # Result is complete (payload is exact per doc-14:182-184).
    assert rr.result.completeness == "complete"
    assert rr.result.task_ids == ["task-from-payload"]
    assert rr.is_eligible_for_downstream_consumers is True


# ── precedence: trailer-only (THIRD; NEVER full proof alone) ───────────────


def test_reader_trailer_only_yields_preview_only_per_doc_14_188() -> None:
    """Per doc-14:188 *"It never treats trailers alone as full proof."*
    A trailer-only resolution yields ``completeness="preview_only"`` + a
    typed :class:`CommitProvenanceGapFinding` (per doc-14:198-199)."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    # No typed proof / payload / lineage.
    cp = FakeCommitProofProvider()
    ps = FakePayloadStore()
    lw = FakeLineageWalker()

    # ONLY a trailer.
    ts = FakeTrailerSource()
    ts.trailers[("repo-1", commit_hash)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="dag-group:0",
        precommit_provenance_ref="refs/iriai/provenance/" + ("d" * 64),
        precommit_provenance_digest="d" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(
        runner=runner,
        commit_proof_provider=cp,
        payload_store=ps,
        trailer_source=ts,
        lineage_walker=lw,
    )
    rr = reader.read(_query())

    # Per doc-14:188 NEVER full proof -> preview_only.
    assert rr.result.completeness == "preview_only"
    # Trailer-only confidence is 0.4 (the weakest exact-evidence tier).
    assert rr.result.confidence == 0.4
    # The trailer payload ref is still tracked (consumers may dereference it).
    assert "refs/iriai/provenance/" + ("d" * 64) in rr.result.provenance_payload_refs
    # And a typed gap finding is recorded.
    assert rr.gap_finding is not None
    assert rr.gap_finding.failure_id == "line_provenance_gap"


def test_trailer_only_carries_only_digest_not_enumerated_task_ids() -> None:
    """Per doc-14:137 the trailer carries the task ids DIGEST, NOT the
    enumerated list. The reader cannot recover the full task ids from
    a trailer-only resolution."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    ts = FakeTrailerSource()
    ts.trailers[("repo-1", commit_hash)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="r",
        precommit_provenance_ref="r",
        precommit_provenance_digest="c" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(runner=runner, trailer_source=ts)
    rr = reader.read(_query())

    # task_ids is empty -- the trailer carries only a digest.
    assert rr.result.task_ids == []


# ── preview_only INELIGIBLE invariant (doc-14:202-205) ─────────────────────


def test_preview_only_is_ineligible_for_downstream_consumers() -> None:
    """Per doc-14:202-205 a ``preview_only`` result MUST be INELIGIBLE to
    feed context packages, governance findings, metrics, or policy
    recommendations as line provenance authority.

    The typed-surface mechanism is
    :attr:`LineProvenanceReadResult.is_eligible_for_downstream_consumers`
    which is False whenever ``completeness == "preview_only"``.
    """

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    ts = FakeTrailerSource()
    ts.trailers[("repo-1", commit_hash)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="r",
        precommit_provenance_ref="r",
        precommit_provenance_digest="c" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(runner=runner, trailer_source=ts)
    rr = reader.read(_query())

    assert rr.result.completeness == "preview_only"
    # Critical INVARIANT: preview_only is INELIGIBLE for downstream consumers.
    assert rr.is_eligible_for_downstream_consumers is False


def test_unavailable_result_is_ineligible_for_downstream_consumers() -> None:
    """``completeness="unavailable"`` is also INELIGIBLE (paged + complete
    are the only eligible states)."""

    result = LineProvenanceResult(
        commit_hashes=[],
        task_ids=[],
        provenance_payload_refs=[],
        page_refs=[],
        completeness="unavailable",
        completeness_digest="abc",
        confidence=0.0,
        gaps=["no_evidence"],
    )
    rr = LineProvenanceReadResult(result=result)
    assert rr.is_eligible_for_downstream_consumers is False


def test_complete_result_is_eligible_for_downstream_consumers() -> None:
    """``completeness="complete"`` IS eligible."""

    result = LineProvenanceResult(
        commit_hashes=["c"],
        task_ids=["t"],
        provenance_payload_refs=["r"],
        page_refs=[],
        completeness="complete",
        completeness_digest="abc",
        confidence=1.0,
        gaps=[],
    )
    rr = LineProvenanceReadResult(result=result)
    assert rr.is_eligible_for_downstream_consumers is True


def test_paged_result_is_eligible_for_downstream_consumers() -> None:
    """``completeness="paged"`` IS eligible (paged exact is authoritative
    per doc-13a:18-23 Slice 13A invariant)."""

    page_ref = GovernanceEvidencePageRef(
        page_ref_id="p1",
        authority="git_provenance",
        source_ref_id="src",
        digest="a" * 64,
        completeness="paged",
        exact=True,
    )
    result = LineProvenanceResult(
        commit_hashes=[],
        task_ids=[],
        provenance_payload_refs=[],
        page_refs=[page_ref],
        completeness="paged",
        completeness_digest="abc",
        confidence=1.0,
        gaps=[],
    )
    rr = LineProvenanceReadResult(result=result)
    assert rr.is_eligible_for_downstream_consumers is True


# ── completeness="paged" (doc-14:199-201) ──────────────────────────────────


def test_reader_returns_paged_when_line_range_exceeds_max_lines() -> None:
    """Per doc-14:199-201 a query whose line range exceeds the
    ``max_lines`` cap returns ``completeness="paged"`` with exact
    :class:`GovernanceEvidencePageRef` rows."""

    runner = FakeGitRunner()
    # No blame needed -- the reader short-circuits to paged BEFORE blame.
    reader, _, _, _, _, _ = _make_reader(runner=runner)

    # Query 1001 lines with max_lines=500 -> paged with 3 pages
    # (500 + 500 + 1).
    query = _query(line_start=1, line_end=1001, max_lines=500)
    rr = reader.read(query)

    assert rr.result.completeness == "paged"
    assert rr.is_eligible_for_downstream_consumers is True
    assert len(rr.result.page_refs) == 3
    # First page: lines 1-500.
    assert rr.result.page_refs[0].line_start == 1
    assert rr.result.page_refs[0].line_end == 500
    # Second page: lines 501-1000.
    assert rr.result.page_refs[1].line_start == 501
    assert rr.result.page_refs[1].line_end == 1000
    # Third page: lines 1001-1001.
    assert rr.result.page_refs[2].line_start == 1001
    assert rr.result.page_refs[2].line_end == 1001


def test_reader_paged_short_circuits_before_blame() -> None:
    """A paged-cap short-circuit MUST happen BEFORE blame invocation (no
    wasted Git subprocess calls for over-sized queries)."""

    runner = FakeGitRunner()
    reader, runner_out, _, _, _, _ = _make_reader(runner=runner)

    query = _query(line_start=1, line_end=1001, max_lines=500)
    rr = reader.read(query)

    assert rr.result.completeness == "paged"
    # NO git invocations (paged short-circuits before blame).
    assert len(runner_out.invocations) == 0


def test_paged_page_refs_are_exact_per_doc_13a_invariant() -> None:
    """Per the Slice 13A invariant doc-13a:18-23 paged page refs MUST be
    exact (the cross-process freshness contract)."""

    runner = FakeGitRunner()
    reader, _, _, _, _, _ = _make_reader(runner=runner)

    query = _query(line_start=1, line_end=600, max_lines=500)
    rr = reader.read(query)

    for page_ref in rr.result.page_refs:
        assert isinstance(page_ref, GovernanceEvidencePageRef)
        assert page_ref.exact is True
        assert page_ref.completeness == "paged"
        # The Slice 13a page_ref carries a non-empty digest.
        assert len(page_ref.digest) == 64


def test_paged_returns_max_commits_overflow_too() -> None:
    """A query whose blame returns more than ``max_commits`` unique commits
    triggers paged."""

    # Set max_commits=2; blame returns 3 unique commits -> paged.
    runner = FakeGitRunner()
    blame = (
        _blame_porcelain_single_commit("a" * 40, 1, 1)
        + _blame_porcelain_single_commit("b" * 40, 2, 1)
        + _blame_porcelain_single_commit("c" * 40, 3, 1)
    )
    runner.queue_blame_porcelain(blame)

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    query = _query(line_start=1, line_end=3, max_lines=500, max_commits=2)
    rr = reader.read(query)

    assert rr.result.completeness == "paged"
    assert len(rr.result.page_refs) >= 1


# ── rebase / cherry-pick lineage walk (doc-14:212-213) ─────────────────────


def test_reader_walks_lineage_for_rebased_commit() -> None:
    """Per doc-14:212-213 *"Rebase/cherry-pick: preserve old/new lineage
    and reject ambiguous line provenance unless lineage is recorded."*
    The reader walks the lineage when blame returns a commit hash that
    lacks typed proof / payload at the natural ref."""

    old_commit = "1" * 40  # the pre-rewrite commit (what blame sees)
    new_commit = "2" * 40  # the post-rewrite commit (what typed proof attests)

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(old_commit, 1, 1))

    # No typed proof for the OLD commit; no payload either.
    cp = FakeCommitProofProvider()
    # But typed proof EXISTS for the NEW commit (post-rewrite).
    cp.add(_commit_proof_row(commit_hash=new_commit, task_ids=["task-after-rebase"]))

    ps = FakePayloadStore()
    ts = FakeTrailerSource()

    # Lineage walker maps old -> new.
    lw = FakeLineageWalker()
    lw.records[("repo-1", old_commit)] = LineageRecord(
        old_commit_hash=old_commit,
        new_commit_hash=new_commit,
        reason="rebase",
    )

    reader, _, _, _, _, _ = _make_reader(
        runner=runner,
        commit_proof_provider=cp,
        payload_store=ps,
        trailer_source=ts,
        lineage_walker=lw,
    )
    rr = reader.read(_query(line_start=1, line_end=1))

    # The reader resolved via lineage -> new commit's typed proof.
    assert rr.result.completeness == "complete"
    assert rr.result.task_ids == ["task-after-rebase"]
    # The lineage-walked tier is confidence 0.6.
    assert rr.result.confidence == 0.6
    # Result is still eligible for downstream consumers (lineage-walked
    # is exact per doc-14:212-213 "preserve old/new lineage").
    assert rr.is_eligible_for_downstream_consumers is True


def test_reader_rejects_ambiguous_blame_without_lineage_per_doc_14_212_213() -> None:
    """Per doc-14:212-213 *"reject ambiguous line provenance unless lineage
    is recorded"* the reader records a typed
    ``governance_evidence_conflict`` gap finding when blame returns a
    commit hash with NO proof / payload / lineage."""

    orphan_commit = "9" * 40

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(orphan_commit, 1, 1))

    # All sources return None for this commit.
    cp = FakeCommitProofProvider()
    ps = FakePayloadStore()
    ts = FakeTrailerSource()
    lw = FakeLineageWalker()

    reader, _, _, _, _, _ = _make_reader(
        runner=runner,
        commit_proof_provider=cp,
        payload_store=ps,
        trailer_source=ts,
        lineage_walker=lw,
    )
    rr = reader.read(_query(line_start=1, line_end=1))

    # Per doc-14:212-213: unresolved commit -> unavailable + conflict gap.
    assert rr.result.completeness == "unavailable"
    assert rr.gap_finding is not None
    assert rr.gap_finding.failure_id == "governance_evidence_conflict"
    # Result is NOT eligible for downstream consumers.
    assert rr.is_eligible_for_downstream_consumers is False


def test_lineage_walk_with_cherry_pick_reason() -> None:
    """The :class:`LineageRecord` ``reason`` field accepts ``cherry-pick``."""

    old_commit = "1" * 40
    new_commit = "2" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(old_commit, 1, 1))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=new_commit, task_ids=["task-cherry-picked"]))

    lw = FakeLineageWalker()
    lw.records[("repo-1", old_commit)] = LineageRecord(
        old_commit_hash=old_commit,
        new_commit_hash=new_commit,
        reason="cherry-pick",
    )

    reader, _, _, _, _, _ = _make_reader(
        runner=runner, commit_proof_provider=cp, lineage_walker=lw
    )
    rr = reader.read(_query(line_start=1, line_end=1))

    assert rr.result.completeness == "complete"
    assert rr.result.task_ids == ["task-cherry-picked"]


# ── trailers-only -> partial evidence + gap finding ────────────────────────


def test_trailers_only_records_partial_evidence_and_gap_finding() -> None:
    """Per doc-14:198-199 *"Commit has trailers but missing note/ref: line
    query returns partial evidence and a gap; governance records
    provenance-gap findings."* The reader's trailer-only path returns
    partial evidence + a typed gap finding."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    # No proof / payload / lineage; ONLY trailer.
    ts = FakeTrailerSource()
    ts.trailers[("repo-1", commit_hash)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="r",
        precommit_provenance_ref="refs/iriai/provenance/" + ("c" * 64),
        precommit_provenance_digest="c" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(runner=runner, trailer_source=ts)
    rr = reader.read(_query(line_start=1, line_end=1))

    # Partial result.
    assert rr.result.completeness == "preview_only"
    # Carries the trailer's payload ref (partial evidence).
    assert "refs/iriai/provenance/" + ("c" * 64) in rr.result.provenance_payload_refs
    # And the gap finding.
    assert rr.gap_finding is not None
    assert rr.gap_finding.failure_id == "line_provenance_gap"


def test_trailer_only_gap_carries_query_correlator() -> None:
    """The gap finding's ``commit_hash`` + ``precommit_provenance_ref``
    fields carry the query target as a structural correlator (not the
    individual blamed commit)."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    ts = FakeTrailerSource()
    ts.trailers[("repo-1", commit_hash)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="r",
        precommit_provenance_ref="r",
        precommit_provenance_digest="c" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(runner=runner, trailer_source=ts)
    rr = reader.read(_query(repo_id="repo-1", ref="HEAD", path="src/a.py",
                            line_start=1, line_end=1))

    assert rr.gap_finding is not None
    # The correlator carries the query target.
    assert "repo-1" in rr.gap_finding.commit_hash
    assert "src/a.py" in rr.gap_finding.commit_hash


# ── non-blocking failure routing (doc-14:242-243) ──────────────────────────


def test_reader_does_not_raise_on_unresolvable_query() -> None:
    """Per doc-14:242-243 the reader NEVER raises a failure to the caller.

    An unresolvable query returns a typed
    :class:`LineProvenanceReadResult` with ``gap_finding`` populated; it
    does NOT raise.
    """

    runner = FakeGitRunner()
    # Blame returns garbage.
    runner.queue(returncode=128, stderr="fatal: bad revision")

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    try:
        rr = reader.read(_query())
    except Exception as exc:  # pragma: no cover
        pytest.fail(f"reader.read() raised {type(exc).__name__}: {exc}")

    # Result is unavailable + gap finding is recorded.
    assert rr.result.completeness == "unavailable"
    assert rr.gap_finding is not None


def test_reader_records_typed_gap_finding_on_blame_failure() -> None:
    """When blame fails the reader records a typed
    :class:`CommitProvenanceGapFinding` with ``line_provenance_gap``
    failure id."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: bad revision")

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    rr = reader.read(_query())

    assert rr.gap_finding is not None
    assert rr.gap_finding.failure_id == "line_provenance_gap"


def test_reader_gap_finding_uses_reused_failure_id_per_2nd_sub_slice() -> None:
    """The reader REUSES the 2nd sub-slice typed failure ids;
    :attr:`CommitProvenanceGapFinding.failure_id` Literal accepts both."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: bad revision")

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    rr = reader.read(_query())

    assert rr.gap_finding is not None
    # Per the 2nd sub-slice typed Literal: one of the 2 REUSED ids.
    assert rr.gap_finding.failure_id in COMMIT_PROVENANCE_GAP_FAILURE_IDS


def test_reader_failure_does_not_mutate_query() -> None:
    """The query is an INPUT object; the reader does not mutate it."""

    runner = FakeGitRunner()
    runner.queue(returncode=128, stderr="fatal: bad revision")

    query = _query()
    original_dump = query.model_dump()

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    reader.read(query)

    assert query.model_dump() == original_dump


# ── Git subprocess fixture: NO real git shell-out ───────────────────────────


def test_reader_uses_fake_runner_for_blame_invocation() -> None:
    """Per the implementer prompt MUST NOT shell out to real git in unit
    tests. The reader takes a :class:`GitSubprocessRunner` callable -- the
    fake fixture intercepts every invocation."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    reader, runner_out, _, _, _, _ = _make_reader(runner=runner)
    reader.read(_query(line_start=1, line_end=1))

    # Exactly 1 git invocation (the blame call).
    assert len(runner_out.invocations) == 1
    blame_args, cwd = runner_out.invocations[0]
    assert blame_args[0] == "blame"
    assert "--porcelain" in blame_args
    assert cwd == "/tmp/repo-1"


def test_reader_passes_repo_path_as_cwd() -> None:
    """Every Git invocation runs with the reader's ``repo_path`` as cwd."""

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit("a" * 40, 1, 1))

    reader = LineProvenanceReader(
        repo_path="/tmp/custom-repo",
        runner=runner,
        commit_proof_provider=FakeCommitProofProvider(),
        payload_store=FakePayloadStore(),
        trailer_source=FakeTrailerSource(),
        lineage_walker=FakeLineageWalker(),
    )
    reader.read(_query())

    for _args, cwd in runner.invocations:
        assert cwd == "/tmp/custom-repo"


def test_reader_records_typed_git_invocations_in_result() -> None:
    """The result carries the typed :class:`GitSubprocessResult` rows for
    every invocation (used by tests + the future Slice 18 replay layer)."""

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit("a" * 40, 1, 1))

    reader, _, _, _, _, _ = _make_reader(runner=runner)
    rr = reader.read(_query(line_start=1, line_end=1))

    assert len(rr.git_invocations) >= 1
    for invocation in rr.git_invocations:
        assert isinstance(invocation, GitSubprocessResult)


def test_reader_notes_ref_defaults_to_canonical_per_doc_14_144() -> None:
    """The reader's ``notes_ref`` defaults to the canonical
    ``refs/notes/iriai`` per doc-14:144."""

    runner = FakeGitRunner()
    reader = LineProvenanceReader(
        repo_path="/tmp/repo-1",
        runner=runner,
        commit_proof_provider=FakeCommitProofProvider(),
        payload_store=FakePayloadStore(),
        trailer_source=FakeTrailerSource(),
        lineage_walker=FakeLineageWalker(),
    )
    assert reader.notes_ref == "refs/notes/iriai"


def test_reader_notes_ref_is_overridable() -> None:
    """The reader's ``notes_ref`` accepts a custom namespace."""

    runner = FakeGitRunner()
    reader = LineProvenanceReader(
        repo_path="/tmp/repo-1",
        runner=runner,
        commit_proof_provider=FakeCommitProofProvider(),
        payload_store=FakePayloadStore(),
        trailer_source=FakeTrailerSource(),
        lineage_walker=FakeLineageWalker(),
        notes_ref="refs/notes/custom",
    )
    assert reader.notes_ref == "refs/notes/custom"


# ── query -> result round-trip ──────────────────────────────────────────────


def test_query_to_result_round_trip_via_fake_runner() -> None:
    """The reader's full query -> result round trip via fake runner +
    fake source ports yields a typed :class:`LineProvenanceResult`."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 3))

    cp = FakeCommitProofProvider()
    cp.add(
        _commit_proof_row(commit_hash=commit_hash, task_ids=["task-1", "task-2"])
    )

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    rr = reader.read(_query(line_start=1, line_end=3))

    # Typed result with all 8 doc-14:125-133 fields populated.
    assert isinstance(rr.result, LineProvenanceResult)
    assert rr.result.commit_hashes == [commit_hash]
    assert rr.result.task_ids == ["task-1", "task-2"]
    assert len(rr.result.provenance_payload_refs) == 1
    assert rr.result.page_refs == []
    assert rr.result.completeness == "complete"
    assert len(rr.result.completeness_digest) == 64  # SHA-256 hex
    assert rr.result.confidence == 1.0
    assert rr.result.gaps == []


def test_result_serialises_via_json_round_trip() -> None:
    """The result serialises cleanly via ``model_dump_json`` ->
    ``model_validate_json`` (round-trip identity)."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=commit_hash))

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    rr = reader.read(_query(line_start=1, line_end=1))

    serialised = rr.result.model_dump_json()
    restored = LineProvenanceResult.model_validate_json(serialised)
    assert restored == rr.result


def test_multiple_commits_aggregate_task_ids() -> None:
    """Multiple commits in the blame range -> aggregated task_ids list."""

    c1 = "1" * 40
    c2 = "2" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(
        _blame_porcelain_single_commit(c1, 1, 1)
        + _blame_porcelain_single_commit(c2, 2, 1)
    )

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=c1, task_ids=["task-1"]))
    cp.add(_commit_proof_row(commit_hash=c2, task_ids=["task-2"]))

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    rr = reader.read(_query(line_start=1, line_end=2))

    # Both commits + aggregated task ids.
    assert sorted(rr.result.commit_hashes) == [c1, c2]
    assert rr.result.task_ids == ["task-1", "task-2"]


# ── mixed precedence (some commits typed, some via payload) ─────────────────


def test_reader_mixes_typed_proof_and_payload_resolutions() -> None:
    """A blame range with multiple commits where some resolve via typed
    proof and others via payload: the result aggregates both."""

    c1 = "1" * 40  # via typed proof
    c2 = "2" * 40  # via payload only

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(
        _blame_porcelain_single_commit(c1, 1, 1)
        + _blame_porcelain_single_commit(c2, 2, 1)
    )

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=c1, task_ids=["task-1"]))

    ps = FakePayloadStore()
    payload = compute_payload(
        _writer_inputs(
            commit_proof=_repo_commit_proof(result_commit=c2),
            task_ids=["task-2"],
        )
    )
    ps.payloads_by_notes[("repo-1", c2)] = payload

    reader, _, _, _, _, _ = _make_reader(
        runner=runner, commit_proof_provider=cp, payload_store=ps
    )
    rr = reader.read(_query(line_start=1, line_end=2))

    # Both commits resolve to exact evidence (typed_proof + payload are
    # both exact per doc-14:182-184) -> completeness=complete.
    assert rr.result.completeness == "complete"
    # Confidence is the min across all per-commit confidences: payload (0.8).
    assert rr.result.confidence == 0.8
    # Both task ids aggregated.
    assert sorted(rr.result.task_ids) == ["task-1", "task-2"]


def test_reader_mixed_typed_and_trailer_yields_preview_only() -> None:
    """When a blame range has some commits via typed proof and some via
    trailer-only, the result is ``preview_only`` (the strongest state is
    bounded by the weakest source)."""

    c1 = "1" * 40  # typed proof
    c2 = "2" * 40  # trailer only

    runner = FakeGitRunner()
    runner.queue_blame_porcelain(
        _blame_porcelain_single_commit(c1, 1, 1)
        + _blame_porcelain_single_commit(c2, 2, 1)
    )

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=c1, task_ids=["task-1"]))

    ts = FakeTrailerSource()
    ts.trailers[("repo-1", c2)] = CommitProvenanceTrailer(
        feature_id="f",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="a" * 64,
        merge_queue_item_ids_digest="b" * 64,
        checkpoint_ref="r",
        precommit_provenance_ref="r",
        precommit_provenance_digest="c" * 64,
    )

    reader, _, _, _, _, _ = _make_reader(
        runner=runner, commit_proof_provider=cp, trailer_source=ts
    )
    rr = reader.read(_query(line_start=1, line_end=2))

    # preview_only because at least one commit is trailer-only.
    assert rr.result.completeness == "preview_only"
    # And INELIGIBLE.
    assert rr.is_eligible_for_downstream_consumers is False
    # Gap finding.
    assert rr.gap_finding is not None
    assert rr.gap_finding.failure_id == "line_provenance_gap"


# ── reader interaction with writer-produced payload (round-trip) ────────────


def test_reader_resolves_writer_produced_payload_round_trip() -> None:
    """The reader correctly consumes a payload produced by the Slice 14
    2nd sub-slice writer (the read-side complement of the writer)."""

    inputs = _writer_inputs(
        feature_id="feature-roundtrip", task_ids=["roundtrip-task-1"]
    )
    payload = compute_payload(inputs)
    trailer = compute_trailer(inputs)

    commit_hash = inputs.commit_proof.result_commit
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(
        _blame_porcelain_single_commit(commit_hash, 1, 1)
    )

    # No typed proof; payload is at the notes namespace.
    ps = FakePayloadStore()
    ps.payloads_by_notes[("repo-1", commit_hash)] = payload

    reader, _, _, _, _, _ = _make_reader(runner=runner, payload_store=ps)
    rr = reader.read(_query(line_start=1, line_end=1))

    # The payload's task_ids match what the writer wrote.
    assert rr.result.task_ids == ["roundtrip-task-1"]
    # The provenance ref matches what the writer wrote.
    assert trailer.precommit_provenance_ref in rr.result.provenance_payload_refs


# ── completeness digest stability ──────────────────────────────────────────


def test_result_completeness_digest_is_recomputed_after_construction() -> None:
    """The reader recomputes the completeness_digest after building the
    result so the stored digest matches the canonical content."""

    commit_hash = "a" * 40
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=commit_hash))

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    rr = reader.read(_query(line_start=1, line_end=1))

    # The stored digest matches a recomputation.
    recomputed = compute_line_provenance_completeness_digest(rr.result)
    assert rr.result.completeness_digest == recomputed


# ── Slice 14 1st + 2nd sub-slice non-alteration ─────────────────────────────


def test_reader_does_not_alter_repo_commit_proof_typed_shape() -> None:
    """Per doc-14:155-160 the Slice 08 :class:`RepoCommitProof` 10 fields
    are READ-ONLY from this slice. The reader cross-cites but does NOT
    mutate."""

    proof = _repo_commit_proof()
    original_dump = proof.model_dump()

    commit_hash = proof.result_commit
    runner = FakeGitRunner()
    runner.queue_blame_porcelain(_blame_porcelain_single_commit(commit_hash, 1, 1))

    cp = FakeCommitProofProvider()
    cp.add(_commit_proof_row(commit_hash=commit_hash, commit_proof=proof))

    reader, _, _, _, _, _ = _make_reader(runner=runner, commit_proof_provider=cp)
    reader.read(_query(line_start=1, line_end=1))

    # The proof was NOT mutated.
    assert proof.model_dump() == original_dump


def test_reader_does_not_re_register_failure_ids() -> None:
    """The reader REUSES the 2nd sub-slice failure ids; the failure router
    contains exactly 1 registration per id (no double-registration from
    importing the reader)."""

    # Import the reader module to trigger any registration side-effects.
    import iriai_build_v2.execution_control.commit_provenance_reader  # noqa: F401

    # Each typed failure id appears exactly once in FAILURE_TYPES.
    assert fr.FAILURE_TYPES.count("line_provenance_gap") == 1
    assert fr.FAILURE_TYPES.count("governance_evidence_conflict") == 1

    # ROUTE_TABLE has exactly one row per (failure_class, failure_type).
    keys = list(fr.ROUTE_TABLE.keys())
    assert keys.count(("evidence_corruption", "line_provenance_gap")) == 1
    assert keys.count(("evidence_corruption", "governance_evidence_conflict")) == 1
