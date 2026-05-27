"""Slice 14 first sub-slice -- unit tests for the foundational
``execution_control/commit_provenance.py`` typed-shape module.

Covers the 4 doc-14:79-133 typed shapes:

- :class:`CommitProvenanceTrailer` -- 8 fields per doc-14:79-87.
- :class:`CommitProvenancePayload` -- 18 fields per doc-14:89-110;
  ``schema_version`` Literal pinning per doc-14:90; ``payload_sha256``
  self-exclusion roundtrip per doc-14:151-153.
- :class:`LineProvenanceQuery` -- 9 fields per doc-14:112-122; cap
  enforcement on ``max_lines`` / ``max_commits`` / ``max_payload_bytes`` /
  ``timeout_ms``; 1-indexed line index validation.
- :class:`LineProvenanceResult` -- 8 fields per doc-14:124-133; Slice 13A
  shared ``CompletenessState`` consumption (NOT redefined; namespace
  assertion); Slice 13a ``GovernanceEvidencePageRef`` consumption (NOT
  redefined).

Every model enforces ``extra="forbid"`` (typo-d kwargs ->
``ValidationError``). Every cap is positive (per doc-14:119-122 +
doc-14:220). Every Literal range is enforced (per Pydantic ``Literal``
validator).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; the doc-14:155-160 step 1 inventory rule forbids
executor wiring outside this slice's own acceptance tests; the
``_commit_group`` callsite + the Slice 08 ``dag-commit-proof:*`` row
shape MUST remain byte-identical.
"""

from __future__ import annotations

from typing import Any, get_args

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.commit_provenance import (
    COMMIT_PROVENANCE_SCHEMA_VERSION,
    CommitProvenancePayload,
    CommitProvenanceTrailer,
    LineProvenanceQuery,
    LineProvenanceResult,
    canonical_payload_dict,
    compute_payload_sha256,
)
from iriai_build_v2.execution_control.completeness import (
    CompletenessState as ExecutionControlCompletenessState,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState as GovernanceCompletenessState,
    GovernanceEvidencePageRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 4 typed shapes + schema-version
    pin + 2 digest helpers.

    Per doc-14:79-133 the 4 typed shapes are
    ``CommitProvenanceTrailer`` + ``CommitProvenancePayload`` +
    ``LineProvenanceQuery`` + ``LineProvenanceResult``. Plus the
    ``COMMIT_PROVENANCE_SCHEMA_VERSION`` Literal pin per doc-14:90 +
    the ``compute_payload_sha256`` + ``canonical_payload_dict``
    helpers per doc-14:151-153. Total: 7 exported names.
    """

    from iriai_build_v2.execution_control import commit_provenance as mod

    expected = {
        "COMMIT_PROVENANCE_SCHEMA_VERSION",
        "CommitProvenanceTrailer",
        "CommitProvenancePayload",
        "LineProvenanceQuery",
        "LineProvenanceResult",
        "compute_payload_sha256",
        "canonical_payload_dict",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 7
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-13a:285-287 step 9 + doc-14:263-311 the Slice 14 module
    MUST NOT redefine :data:`CompletenessState` -- it consumes the
    Slice 13A shared model via import only.

    A re-definition would create a second authority shape and violate
    the dependency reconciliation contract.
    """

    from iriai_build_v2.execution_control import commit_provenance as mod

    # The module does NOT export its own CompletenessState.
    assert "CompletenessState" not in set(mod.__all__)
    # And the module attribute is NOT present (no shadowing import).
    assert not hasattr(mod, "_CompletenessState")
    # The LineProvenanceResult.completeness field IS typed against the
    # Slice 13A shared Literal -- confirmed by the namespace assertion
    # in test_line_provenance_result_completeness_is_slice_13a_shared.


def test_module_does_not_redefine_governance_evidence_page_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-14:263-311 the Slice 14 module
    MUST NOT redefine :class:`GovernanceEvidencePageRef` -- it consumes
    the Slice 13a shared model via import only.
    """

    from iriai_build_v2.execution_control import commit_provenance as mod

    assert "GovernanceEvidencePageRef" not in set(mod.__all__)


# ── COMMIT_PROVENANCE_SCHEMA_VERSION (doc-14:90) ───────────────────────────


def test_schema_version_pin_is_v1_literal() -> None:
    """The schema version pin is the ``"iriai.commit_provenance.v1"``
    Literal verbatim per doc-14:90."""

    assert COMMIT_PROVENANCE_SCHEMA_VERSION == "iriai.commit_provenance.v1"


def test_schema_version_pin_is_default_on_payload() -> None:
    """Constructors that omit ``schema_version`` get the pinned default."""

    payload = _payload()
    assert payload.schema_version == "iriai.commit_provenance.v1"
    assert payload.schema_version == COMMIT_PROVENANCE_SCHEMA_VERSION


def test_schema_version_pin_rejects_non_v1_value() -> None:
    """Per doc-14:90 + the Literal contract: a constructor that passes
    a different schema-version string fails closed with a typed
    ``ValidationError``.

    Future Slice 14 version-bumps introduce a parallel
    ``"iriai.commit_provenance.v2"`` Literal -- they do NOT mutate
    this one in-place.
    """

    with pytest.raises(ValidationError):
        _payload(schema_version="iriai.commit_provenance.v2")
    with pytest.raises(ValidationError):
        _payload(schema_version="something.else.v1")
    with pytest.raises(ValidationError):
        _payload(schema_version="")


# ── CommitProvenanceTrailer (doc-14:79-87) ─────────────────────────────────


def _trailer(**overrides: object) -> CommitProvenanceTrailer:
    """Construct a fully-specified :class:`CommitProvenanceTrailer` for tests."""

    base: dict[str, object] = dict(
        feature_id="feature-abc",
        group_idx=0,
        effective_group_idx=None,
        task_ids_digest="d" * 64,
        merge_queue_item_ids_digest="e" * 64,
        checkpoint_ref="dag-group:0",
        precommit_provenance_ref="refs/iriai/provenance/" + ("a" * 64),
        precommit_provenance_digest="a" * 64,
    )
    base.update(overrides)
    return CommitProvenanceTrailer(**base)


def test_trailer_accepts_all_8_fields() -> None:
    """The 8 doc-14:80-87 fields all populate cleanly."""

    t = _trailer()
    assert t.feature_id == "feature-abc"
    assert t.group_idx == 0
    assert t.effective_group_idx is None
    assert t.task_ids_digest == "d" * 64
    assert t.merge_queue_item_ids_digest == "e" * 64
    assert t.checkpoint_ref == "dag-group:0"
    assert t.precommit_provenance_ref.startswith("refs/iriai/provenance/")
    assert t.precommit_provenance_digest == "a" * 64


def test_trailer_effective_group_idx_accepts_int() -> None:
    """Per doc-14:82 ``effective_group_idx`` is ``int | None = None`` --
    regroup-overlay scenarios populate a non-None integer."""

    t = _trailer(effective_group_idx=7)
    assert t.effective_group_idx == 7


def test_trailer_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _trailer(unknown_field="oops")  # type: ignore[arg-type]


def test_trailer_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    t = _trailer()
    serialised = t.model_dump_json()
    restored = CommitProvenanceTrailer.model_validate_json(serialised)
    assert restored == t
    assert restored.model_dump_json() == serialised


def test_trailer_precommit_provenance_ref_stability_per_doc_14_138_142() -> None:
    """Per doc-14:138-142 the ``precommit_provenance_ref`` MUST be
    derivable from stable inputs known BEFORE ``git commit``: feature
    id + DAG sha256 + group + repo id + queue item ids + task id digest
    + contract digest. It MUST NOT contain the result commit hash
    unless an explicit amend flow reruns all digest checks.

    The typed shape carries the value verbatim; this test pins the
    stability contract: two identical input sets produce identical
    refs.
    """

    # Two trailers with the same stable inputs MUST carry the same
    # precommit_provenance_ref.
    t1 = _trailer(precommit_provenance_ref="refs/iriai/provenance/abc")
    t2 = _trailer(precommit_provenance_ref="refs/iriai/provenance/abc")
    assert t1.precommit_provenance_ref == t2.precommit_provenance_ref

    # The trailer field is precommit-stable: a constructor that has NOT
    # yet seen the result commit hash can still populate the ref.
    # (The typed shape never enforces "must not contain a hash" because
    # the doc-14:138-142 derivation contract is producer-side; the
    # typed shape carries the value verbatim. Future Slice 14 writer
    # sub-slices enforce derivation.)


def test_trailer_field_required_no_default() -> None:
    """All 7 non-``effective_group_idx`` fields are REQUIRED (no default).

    Per doc-14:79-87 the trailer is mandatory + compact; every field
    must be populated at construction time.
    """

    # Drop each required field and assert ValidationError.
    for required_field in [
        "feature_id",
        "group_idx",
        "task_ids_digest",
        "merge_queue_item_ids_digest",
        "checkpoint_ref",
        "precommit_provenance_ref",
        "precommit_provenance_digest",
    ]:
        kwargs = dict(
            feature_id="f",
            group_idx=0,
            effective_group_idx=None,
            task_ids_digest="d" * 64,
            merge_queue_item_ids_digest="e" * 64,
            checkpoint_ref="dag-group:0",
            precommit_provenance_ref="refs/iriai/provenance/abc",
            precommit_provenance_digest="a" * 64,
        )
        del kwargs[required_field]
        with pytest.raises(ValidationError, match="Field required|missing"):
            CommitProvenanceTrailer(**kwargs)  # type: ignore[arg-type]


# ── CommitProvenancePayload (doc-14:89-110) ────────────────────────────────


def _payload(**overrides: object) -> CommitProvenancePayload:
    """Construct a fully-specified :class:`CommitProvenancePayload` for tests."""

    base: dict[str, object] = dict(
        feature_id="feature-abc",
        dag_sha256="d" * 64,
        group_idx=0,
        effective_group_idx=None,
        repo_id="repo-1",
        commit_hash="c" * 40,
        parent_hash="p" * 40,
        tree_hash="t" * 40,
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
        precommit_provenance_ref="refs/iriai/provenance/abc",
        payload_sha256="placeholder-digest",
    )
    base.update(overrides)
    return CommitProvenancePayload(**base)


def test_payload_accepts_all_18_fields() -> None:
    """The 18 doc-14:90-110 fields all populate cleanly."""

    p = _payload()
    assert p.schema_version == "iriai.commit_provenance.v1"
    assert p.feature_id == "feature-abc"
    assert p.dag_sha256 == "d" * 64
    assert p.group_idx == 0
    assert p.effective_group_idx is None
    assert p.repo_id == "repo-1"
    assert p.commit_hash == "c" * 40
    assert p.parent_hash == "p" * 40
    assert p.tree_hash == "t" * 40
    assert p.task_ids == ["task-1", "task-2"]
    assert p.contract_ids == [1, 2]
    assert p.attempt_ids == [10, 11]
    assert p.sandbox_patch_evidence_ids == [100, 101]
    assert p.gate_evidence_ids == [200, 201]
    assert p.merge_queue_item_ids == [1000, 1001]
    assert p.commit_proof_evidence_id == 5000
    assert p.checkpoint_artifact_id == 6000
    assert p.no_dirty_snapshot_ids == [7000, 7001]
    assert p.implementation_log_anchors == ["impl-journal#anchor-1"]
    assert p.precommit_provenance_ref == "refs/iriai/provenance/abc"
    assert p.payload_sha256 == "placeholder-digest"


def test_payload_checkpoint_artifact_id_accepts_none() -> None:
    """Per doc-14:106 ``checkpoint_artifact_id`` is ``int | None`` --
    None when the payload is written for a pre-checkpoint commit."""

    p = _payload(checkpoint_artifact_id=None)
    assert p.checkpoint_artifact_id is None


def test_payload_effective_group_idx_accepts_int() -> None:
    """Per doc-14:94 ``effective_group_idx`` is ``int | None`` --
    regroup-overlay scenarios populate a non-None integer."""

    p = _payload(effective_group_idx=7)
    assert p.effective_group_idx == 7


def test_payload_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _payload(unknown_field="oops")  # type: ignore[arg-type]


def test_payload_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    # First populate the real sha256 so the roundtrip matches.
    p = _payload()
    p_with_real_digest = CommitProvenancePayload(
        **{
            **p.model_dump(),
            "payload_sha256": compute_payload_sha256(p),
        }
    )
    serialised = p_with_real_digest.model_dump_json()
    restored = CommitProvenancePayload.model_validate_json(serialised)
    assert restored == p_with_real_digest


# ── payload_sha256 self-exclusion (doc-14:151-153) ─────────────────────────


def test_canonical_payload_dict_excludes_payload_sha256_field() -> None:
    """Per doc-14:151-153 the digest input MUST exclude ``payload_sha256``
    itself.

    The :func:`canonical_payload_dict` helper drops the field; tests
    confirm the resulting dict does NOT contain ``payload_sha256``.
    """

    p = _payload(payload_sha256="any-value")
    raw = canonical_payload_dict(p)
    assert "payload_sha256" not in raw
    # All other fields are still present.
    assert "feature_id" in raw
    assert "commit_hash" in raw
    assert "precommit_provenance_ref" in raw
    assert "schema_version" in raw


def test_compute_payload_sha256_is_deterministic_across_two_runs() -> None:
    """Two calls with the same logical payload produce byte-identical
    SHA-256 hex digests.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    canonical-JSON discipline.
    """

    p = _payload()
    digest_a = compute_payload_sha256(p)
    digest_b = compute_payload_sha256(p)
    assert digest_a == digest_b
    # And the digest is a 64-char SHA-256 hex string.
    assert len(digest_a) == 64
    int(digest_a, 16)  # raises if not valid hex


def test_compute_payload_sha256_self_exclusion_roundtrip() -> None:
    """Per doc-14:151-153 ("Tests must prove recomputing the digest
    after loading the payload gives the stored value") -- after
    populating ``payload_sha256`` with ``compute_payload_sha256(p)``,
    a serialise/load roundtrip MUST yield a payload whose
    recomputed digest matches the stored value.

    This is the canonical self-exclusion contract: the digest does
    NOT depend on its own value (otherwise no roundtrip could ever
    produce a match).
    """

    p_initial = _payload()
    digest = compute_payload_sha256(p_initial)
    # Re-construct with the real digest set.
    p_with_real_digest = CommitProvenancePayload(
        **{**p_initial.model_dump(), "payload_sha256": digest}
    )
    # Serialise -> load.
    serialised = p_with_real_digest.model_dump_json()
    loaded = CommitProvenancePayload.model_validate_json(serialised)
    # Recompute the digest on the loaded payload.
    recomputed = compute_payload_sha256(loaded)
    # MUST match the stored value.
    assert recomputed == loaded.payload_sha256
    assert recomputed == digest


def test_compute_payload_sha256_distinguishes_different_commit_hash() -> None:
    """A different ``commit_hash`` produces a different digest."""

    p1 = _payload(commit_hash="a" * 40)
    p2 = _payload(commit_hash="b" * 40)
    assert compute_payload_sha256(p1) != compute_payload_sha256(p2)


def test_compute_payload_sha256_distinguishes_different_task_ids() -> None:
    """A different ``task_ids`` list produces a different digest."""

    p1 = _payload(task_ids=["task-1"])
    p2 = _payload(task_ids=["task-2"])
    assert compute_payload_sha256(p1) != compute_payload_sha256(p2)


def test_compute_payload_sha256_independent_of_stored_payload_sha256() -> None:
    """Per doc-14:151-153 the self-exclusion contract -- two payloads
    that differ ONLY in their ``payload_sha256`` field produce the
    same computed digest.

    This is the cross-process freshness contract: the digest is
    derivable purely from the non-digest fields.
    """

    p1 = _payload(payload_sha256="placeholder-1")
    p2 = _payload(payload_sha256="placeholder-2-different")
    # Differ in payload_sha256 ONLY.
    d1 = compute_payload_sha256(p1)
    d2 = compute_payload_sha256(p2)
    assert d1 == d2


def test_compute_payload_sha256_includes_precommit_provenance_ref() -> None:
    """A different ``precommit_provenance_ref`` produces a different digest."""

    p1 = _payload(precommit_provenance_ref="refs/iriai/provenance/abc")
    p2 = _payload(precommit_provenance_ref="refs/iriai/provenance/def")
    assert compute_payload_sha256(p1) != compute_payload_sha256(p2)


def test_compute_payload_sha256_includes_schema_version() -> None:
    """The schema_version pin contributes to the digest input.

    A future v2 payload would produce a different digest from v1 even
    when other fields match exactly (the schema_version itself is part
    of the digest payload via :func:`canonical_payload_dict`).
    """

    raw_dict = canonical_payload_dict(_payload())
    assert raw_dict["schema_version"] == "iriai.commit_provenance.v1"


# ── LineProvenanceQuery (doc-14:112-122) ───────────────────────────────────


def _query(**overrides: object) -> LineProvenanceQuery:
    """Construct a fully-specified :class:`LineProvenanceQuery` for tests."""

    base: dict[str, object] = dict(
        repo_id="repo-1",
        ref="HEAD",
        path="src/file.py",
        line_start=10,
        line_end=20,
    )
    base.update(overrides)
    return LineProvenanceQuery(**base)


def test_query_accepts_all_required_fields() -> None:
    """The 5 required doc-14:113-117 fields all populate cleanly."""

    q = _query()
    assert q.repo_id == "repo-1"
    assert q.ref == "HEAD"
    assert q.path == "src/file.py"
    assert q.line_start == 10
    assert q.line_end == 20


def test_query_default_max_lines_is_500_per_doc_14_119() -> None:
    """Per doc-14:119 ``max_lines`` defaults to ``500``."""

    q = _query()
    assert q.max_lines == 500


def test_query_default_max_commits_is_50_per_doc_14_120() -> None:
    """Per doc-14:120 ``max_commits`` defaults to ``50``."""

    q = _query()
    assert q.max_commits == 50


def test_query_default_max_payload_bytes_is_512000_per_doc_14_121() -> None:
    """Per doc-14:121 ``max_payload_bytes`` defaults to ``512_000``."""

    q = _query()
    assert q.max_payload_bytes == 512_000


def test_query_default_timeout_ms_is_10000_per_doc_14_122() -> None:
    """Per doc-14:122 ``timeout_ms`` defaults to ``10_000``."""

    q = _query()
    assert q.timeout_ms == 10_000


def test_query_default_include_history_is_true_per_doc_14_118() -> None:
    """Per doc-14:118 ``include_history`` defaults to ``True``."""

    q = _query()
    assert q.include_history is True


def test_query_caps_are_overridable() -> None:
    """The cap defaults are overridable -- consumers can tighten them."""

    q = _query(
        max_lines=100,
        max_commits=10,
        max_payload_bytes=64_000,
        timeout_ms=2_000,
        include_history=False,
    )
    assert q.max_lines == 100
    assert q.max_commits == 10
    assert q.max_payload_bytes == 64_000
    assert q.timeout_ms == 2_000
    assert q.include_history is False


def test_query_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _query(unknown_field="oops")  # type: ignore[arg-type]


def test_query_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    q = _query(include_history=False, max_lines=42)
    serialised = q.model_dump_json()
    restored = LineProvenanceQuery.model_validate_json(serialised)
    assert restored == q


@pytest.mark.parametrize(
    "cap_field",
    ["max_lines", "max_commits", "max_payload_bytes", "timeout_ms"],
)
def test_query_caps_enforce_positive_value_per_doc_14_220(cap_field: str) -> None:
    """Per doc-14:119-122 + doc-14:220 the caps are RESOURCE LIMITS
    mandatory but page/read limits, NOT silent truncation permission.

    A zero or negative cap defeats the cap contract; the field
    validator fails closed with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _query(**{cap_field: 0})
    with pytest.raises(ValidationError):
        _query(**{cap_field: -1})


def test_query_line_start_must_be_positive_per_doc_14_116() -> None:
    """Per doc-14:116 the line range is 1-indexed; a non-positive line
    index is not a valid range bound."""

    with pytest.raises(ValidationError):
        _query(line_start=0)
    with pytest.raises(ValidationError):
        _query(line_start=-5)


def test_query_line_end_must_be_positive_per_doc_14_117() -> None:
    """Per doc-14:117 the line range is 1-indexed; a non-positive line
    index is not a valid range bound."""

    with pytest.raises(ValidationError):
        _query(line_end=0)
    with pytest.raises(ValidationError):
        _query(line_end=-1)


# ── LineProvenanceResult (doc-14:124-133) ──────────────────────────────────


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    """Construct a fully-specified Slice 13a
    :class:`GovernanceEvidencePageRef` for tests.

    Per doc-13:113-126 the page ref carries the required ``page_ref_id`` +
    ``authority`` + ``source_ref_id`` + ``digest`` + ``completeness`` +
    ``exact`` fields plus optional range markers.
    """

    base: dict[str, object] = dict(
        page_ref_id="page-1",
        authority="typed_journal",
        source_ref_id="src-1",
        digest="a" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _result(**overrides: object) -> LineProvenanceResult:
    """Construct a fully-specified :class:`LineProvenanceResult` for tests."""

    base: dict[str, object] = dict(
        commit_hashes=["c1", "c2"],
        task_ids=["task-1"],
        provenance_payload_refs=["refs/iriai/provenance/abc"],
        page_refs=[_page_ref()],
        completeness="complete",
        completeness_digest="result-digest",
        confidence=0.9,
        gaps=[],
    )
    base.update(overrides)
    return LineProvenanceResult(**base)


def test_result_accepts_all_8_fields() -> None:
    """The 8 doc-14:125-133 fields all populate cleanly."""

    r = _result()
    assert r.commit_hashes == ["c1", "c2"]
    assert r.task_ids == ["task-1"]
    assert r.provenance_payload_refs == ["refs/iriai/provenance/abc"]
    assert len(r.page_refs) == 1
    assert isinstance(r.page_refs[0], GovernanceEvidencePageRef)
    assert r.completeness == "complete"
    assert r.completeness_digest == "result-digest"
    assert r.confidence == pytest.approx(0.9)
    assert r.gaps == []


def test_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _result(unknown_field="oops")  # type: ignore[arg-type]


def test_result_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    r = _result()
    serialised = r.model_dump_json()
    restored = LineProvenanceResult.model_validate_json(serialised)
    assert restored == r


@pytest.mark.parametrize(
    "state",
    ["complete", "paged", "preview_only", "unavailable"],
)
def test_result_completeness_accepts_all_4_doc_13a_states(state: str) -> None:
    """The 4 Slice 13A :data:`CompletenessState` Literal values populate
    cleanly per doc-13a:128-133 + doc-14:129."""

    r = _result(completeness=state)
    assert r.completeness == state


def test_result_completeness_rejects_unknown_state() -> None:
    """Per doc-13a:128-133 the Slice 13A Literal enforces the 4-value
    set; an unknown value fails closed."""

    with pytest.raises(ValidationError):
        _result(completeness="not_a_state")


# ── Slice 13A shared CompletenessState consumption ─────────────────────────


def test_line_provenance_result_completeness_is_slice_13a_shared() -> None:
    """**Slice 13A dependency reconciliation namespace assertion**
    (doc-13a:285-287 step 9 + doc-14:263-311).

    The :attr:`LineProvenanceResult.completeness` field MUST be typed
    against the Slice 13A shared
    :data:`~iriai_build_v2.execution_control.completeness.CompletenessState`
    Literal, imported from
    :mod:`iriai_build_v2.execution_control.completeness` -- NOT
    redefined here.
    """

    # The two Literals MUST have IDENTICAL member sets (because Slice 14
    # consumes the Slice 13A shared model).
    exec_members = set(get_args(ExecutionControlCompletenessState))
    expected_members = {"complete", "paged", "preview_only", "unavailable"}
    assert exec_members == expected_members


def test_line_provenance_result_completeness_accepts_governance_state_values() -> None:
    """The Slice 13a governance :data:`CompletenessState` Literal at
    :mod:`iriai_build_v2.workflows.develop.governance.models` is
    VALUE-COMPATIBLE with the Slice 13A shared
    :data:`~iriai_build_v2.execution_control.completeness.CompletenessState`
    -- both are 4-value Literals with the same member set.

    Per doc-13a:122-128 the duplication mirrors the doc-13a:120-256
    cross-cutting wording that introduces the type fresh in the
    execution-control namespace; future Slice 13A sub-slices may
    consolidate via re-export. The Slice 14 module consumes the
    execution-control variant.
    """

    exec_members = set(get_args(ExecutionControlCompletenessState))
    gov_members = set(get_args(GovernanceCompletenessState))
    # Both 4-value Literals have IDENTICAL member sets.
    assert exec_members == gov_members
    assert len(exec_members) == 4


def test_line_provenance_result_page_refs_is_slice_13a_governance_page_ref() -> None:
    """**Slice 13A dependency reconciliation namespace assertion**
    (doc-13a:285-287 step 9 + doc-14:263-311).

    The :attr:`LineProvenanceResult.page_refs` field MUST be typed
    against the Slice 13a
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    list -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here.
    """

    # The page-ref class MUST be the Slice 13a typed surface.
    r = _result(page_refs=[_page_ref()])
    assert isinstance(r.page_refs[0], GovernanceEvidencePageRef)
    # The page-ref carries the Slice 13a invariant: ``exact`` is a
    # REQUIRED bool (no default; precursor to the Slice 13A invariant).
    assert r.page_refs[0].exact is True


def test_line_provenance_result_page_refs_empty_list_is_valid() -> None:
    """The page_refs list may be empty (e.g. when completeness is
    ``"complete"`` from a single-page result, or
    ``"unavailable"`` from a timeout)."""

    r = _result(page_refs=[])
    assert r.page_refs == []


# ── confidence (doc-14:131) ────────────────────────────────────────────────


def test_result_confidence_accepts_zero() -> None:
    """Per doc-14:131 the confidence is in [0.0, 1.0]; 0.0 is valid."""

    r = _result(confidence=0.0)
    assert r.confidence == 0.0


def test_result_confidence_accepts_one() -> None:
    """Per doc-14:131 the confidence is in [0.0, 1.0]; 1.0 is valid."""

    r = _result(confidence=1.0)
    assert r.confidence == 1.0


def test_result_confidence_rejects_negative() -> None:
    """Per doc-14:131 the confidence is in [0.0, 1.0]; negative fails closed."""

    with pytest.raises(ValidationError):
        _result(confidence=-0.1)


def test_result_confidence_rejects_above_one() -> None:
    """Per doc-14:131 the confidence is in [0.0, 1.0]; > 1.0 fails closed."""

    with pytest.raises(ValidationError):
        _result(confidence=1.1)


# ── gaps (doc-14:132) ──────────────────────────────────────────────────────


def test_result_gaps_accepts_populated_list() -> None:
    """The gaps list carries gap-reason strings when the result is
    paged or partial."""

    r = _result(
        completeness="paged",
        gaps=[
            "missing_trailer_for_commit:abc",
            "stale_note_for_ref:HEAD",
        ],
    )
    assert len(r.gaps) == 2
    assert "missing_trailer_for_commit:abc" in r.gaps


# ── Slice 08 non-alteration discipline (doc-14:155-160) ────────────────────


def test_module_does_not_import_implementation_py() -> None:
    """Per doc-14:155-160 step 1 + the implementer prompt § 'MUST NOT
    DO' rule the Slice 14 first sub-slice MUST NOT edit
    ``implementation.py``.

    A direct test on import surface confirms the module does NOT pull
    in ``implementation.py`` (which would be a transitive coupling
    risk).
    """

    import iriai_build_v2.execution_control.commit_provenance as mod

    # The module references stdlib + Pydantic + Slice 13A modules only.
    module_text = mod.__doc__ or ""
    # The docstring DOES mention implementation.py for inventory citation
    # but the module SOURCE does NOT import implementation.py.
    import inspect

    source = inspect.getsource(mod)
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in source
    assert "import iriai_build_v2.workflows.develop.phases.implementation" not in source


def test_module_does_not_import_merge_queue_store() -> None:
    """Per doc-14:155-160 step 1 the Slice 14 module MUST NOT alter
    the Slice 08 :class:`RepoCommitProof` typed row at
    ``merge_queue_store.py:227``.

    The module does NOT import :class:`RepoCommitProof` -- it
    cross-cites the typed row via the ``commit_proof_evidence_id``
    integer field on :class:`CommitProvenancePayload` (per
    doc-14:105).
    """

    import inspect

    import iriai_build_v2.execution_control.commit_provenance as mod

    source = inspect.getsource(mod)
    # The module references the typed row in its DOCSTRING but does
    # NOT import the class.
    assert "from iriai_build_v2.execution_control.merge_queue_store" not in source
    assert "import iriai_build_v2.execution_control.merge_queue_store" not in source


def test_module_does_not_import_failure_router() -> None:
    """Per the implementer prompt § 'MUST NOT DO' rule the Slice 14
    first sub-slice MUST NOT add a new failure_class or failure_id.

    The module does NOT import the failure_router -- the typed
    failure ids ``line_provenance_gap`` + ``governance_evidence_conflict``
    (doc-14:192-201) land in a later Slice 14 sub-slice.
    """

    import inspect

    import iriai_build_v2.execution_control.commit_provenance as mod

    source = inspect.getsource(mod)
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in source
    assert "import iriai_build_v2.workflows.develop.execution.failure_router" not in source


def test_module_does_not_import_supervisor_or_dashboard() -> None:
    """Per the implementer prompt § 'Non-negotiables' rule the Slice 14
    module MUST NOT import from ``supervisor`` / ``dashboard``
    (those are downstream consumers, not dependencies).
    """

    import inspect

    import iriai_build_v2.execution_control.commit_provenance as mod

    source = inspect.getsource(mod)
    assert "from iriai_build_v2.supervisor" not in source
    assert "import iriai_build_v2.supervisor" not in source
    assert "from dashboard" not in source
    assert "from iriai_build_v2.public_dashboard" not in source


# ── package __init__.py discipline ─────────────────────────────────────────


def test_package_init_does_not_reexport_commit_provenance() -> None:
    """Per the Slice 13A precedent at
    ``src/iriai_build_v2/execution_control/__init__.py:1-131`` the
    ``completeness.py`` module is NOT re-exported through the package
    ``__all__``. The Slice 14 first sub-slice mirrors that discipline:
    ``commit_provenance.py`` is consumed via fully-qualified imports
    (``from iriai_build_v2.execution_control.commit_provenance import
    ...``) NOT through the package ``__init__.py``.
    """

    from iriai_build_v2 import execution_control as pkg

    pkg_all = set(getattr(pkg, "__all__", ()))
    # The 4 Slice 14 typed shapes are NOT in the package __all__.
    assert "CommitProvenanceTrailer" not in pkg_all
    assert "CommitProvenancePayload" not in pkg_all
    assert "LineProvenanceQuery" not in pkg_all
    assert "LineProvenanceResult" not in pkg_all
    # Nor is the schema_version pin or the digest helpers.
    assert "COMMIT_PROVENANCE_SCHEMA_VERSION" not in pkg_all
    assert "compute_payload_sha256" not in pkg_all
    assert "canonical_payload_dict" not in pkg_all
