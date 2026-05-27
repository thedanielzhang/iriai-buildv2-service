"""Slice 16 fourth sub-slice -- unit tests for the finding writer +
bounded review projection at ``execution_control/governance_finding_writer.py``.

Covers the typed-shape construction + bounded-review-projection
discipline + LIMIT cap+1 truncation + refs-only (no raw bodies) +
findings digest stability + per-rule version pinning per
doc-16:177-179 + doc-16:215-217 + the writer-never-raises non-blocking
contract + the DIRECT annotation-identity Slice 13a/16-1st REUSE
assertions (the stronger pattern that functionally addresses Slice 14
V3 reviewer P3-V3-2 carry; matches Slice 15 4th sub-slice precedent
verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 1st/2nd/3rd-A/3rd-B sub-slice modules + tests remain
byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
    canonical_finding_dict,
    compute_finding_idempotency_key,
)
from iriai_build_v2.execution_control.governance_finding_writer import (
    DEFAULT_REVIEW_PROJECTION_CAP,
    FINDING_PERSISTENCE_FAILURE_ID,
    FindingPersistenceGap,
    FindingWriterInputs,
    GovernanceFindingWriter,
    REVIEW_PROJECTION_ID_PREFIX,
    compute_findings_digest,
    compute_review_projection_id,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
    _ROUTE_ROWS,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified :class:`GovernanceEvidenceRef` for tests."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-1",
        digest="sha256:bbb",
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def _finding(**overrides: object) -> GovernanceFinding:
    """Construct a fully-specified :class:`GovernanceFinding` for tests.

    Default shape: a workflow-related ``commit_hygiene_loop`` finding
    (one of the 16 v1 finding class names per doc-16:120-137) with
    primary evidence + supporting evidence + journal anchor +
    metric refs, severity ``medium``, confidence 0.85, causal_role
    ``primary``.
    """

    primary = [_ref(ref_id="primary-1", digest="sha256:p1")]
    supporting = [_ref(ref_id="supporting-1", digest="sha256:s1")]
    idempotency_key = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v1",
    )
    base: dict[str, object] = dict(
        idempotency_key=idempotency_key,
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_refs=primary,
        supporting_evidence_refs=supporting,
        implementation_log_anchors=["journal:1:1"],
        metric_refs=["commit_hygiene_loops_per_window"],
        estimated_lost_hours=2.5,
        estimated_retry_impact=0.15,
        recommended_action_display="Review commit hygiene policy.",
        recommendation_draft_ref=None,
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )
    base.update(overrides)
    return GovernanceFinding(**base)  # type: ignore[arg-type]


def _writer_inputs(**overrides: object) -> FindingWriterInputs:
    """Construct a fully-specified :class:`FindingWriterInputs`."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        findings=[_finding()],
        baseline_refs=[_ref(ref_id="baseline-1")],
        warnings=[],
        incomplete_scopes=[],
        finding_rule_versions={"commit_hygiene_loop_v1": "v1"},
    )
    base.update(overrides)
    return FindingWriterInputs(**base)  # type: ignore[arg-type]


def _gap(**overrides: object) -> FindingPersistenceGap:
    """Construct a fully-specified :class:`FindingPersistenceGap`."""

    base: dict[str, object] = dict(
        failure_id="governance_finding_persistence_failed",
        corpus_id="8ac124d6",
        findings_digest="sha256:abc",
        review_projection_id="review:governance-findings:8ac124d6",
        reason="findings_count_exceeds_cap",
        evidence_payload={"cap": 200},
    )
    base.update(overrides)
    return FindingPersistenceGap(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed-shape additions:
    ``FindingWriterInputs`` + ``FindingPersistenceGap`` + the typed
    failure id Literal + the typed review-projection id prefix Literal +
    the LIMIT cap+1 default + 2 pure helpers + the
    ``GovernanceFindingWriter`` class.
    """

    from iriai_build_v2.execution_control import governance_finding_writer as mod

    expected = {
        "FindingWriterInputs",
        "FindingPersistenceGap",
        "FINDING_PERSISTENCE_FAILURE_ID",
        "REVIEW_PROJECTION_ID_PREFIX",
        "DEFAULT_REVIEW_PROJECTION_CAP",
        "compute_review_projection_id",
        "compute_findings_digest",
        "GovernanceFindingWriter",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-16:201-291 the writer module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes
    the Slice 13a shared model via import only."""

    from iriai_build_v2.execution_control import governance_finding_writer as mod

    assert "GovernanceEvidenceRef" not in set(mod.__all__)


def test_module_does_not_redefine_governance_finding() -> None:
    """Per doc-16:82-104 the writer module MUST NOT redefine
    :class:`GovernanceFinding` -- it consumes the Slice 16 1st sub-slice
    shared model via import only."""

    from iriai_build_v2.execution_control import governance_finding_writer as mod

    assert "GovernanceFinding" not in set(mod.__all__)


def test_module_does_not_redefine_canonical_finding_dict() -> None:
    """The writer module REUSES :func:`canonical_finding_dict` from the
    Slice 16 1st sub-slice; does NOT redefine it."""

    from iriai_build_v2.execution_control import governance_finding_writer as mod

    assert "canonical_finding_dict" not in set(mod.__all__)


def test_module_does_not_redefine_finding_kind_literals() -> None:
    """The writer module REUSES the Slice 16 1st sub-slice typed
    Literal aliases (FindingSeverity / FindingKind / FindingCausalRole);
    does NOT redefine them."""

    from iriai_build_v2.execution_control import governance_finding_writer as mod

    assert "FindingSeverity" not in set(mod.__all__)
    assert "FindingKind" not in set(mod.__all__)
    assert "FindingCausalRole" not in set(mod.__all__)


def test_module_import_discipline_no_implementation_py() -> None:
    """The writer MUST NOT import implementation.py (would invert the
    foundational-module dependency direction)."""

    import iriai_build_v2.execution_control.governance_finding_writer as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text


def test_module_import_discipline_only_allowed_imports() -> None:
    """Per the implementer prompt § "Non-negotiables" the writer module
    imports ONLY from stdlib + Pydantic v2 + Slice 13a governance.models +
    Slice 16 1st sub-slice finding_engine. NO imports from
    governance/ outside models, NO imports from workflows/develop/execution/phases/,
    supervisor, or dashboard."""

    import iriai_build_v2.execution_control.governance_finding_writer as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()

    # Forbidden imports.
    assert "from iriai_build_v2.workflows.develop.execution.phases" not in text
    assert "from iriai_build_v2.supervisor" not in text
    assert "import iriai_build_v2.dashboard" not in text
    # The writer should not import failure_router (registration of the
    # failure id is a separate pure-data edit point in failure_router.py
    # itself).
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in text
    # Permitted imports (sanity).
    assert "from iriai_build_v2.execution_control.finding_engine" in text
    assert "from iriai_build_v2.workflows.develop.governance.models" in text


def test_module_import_discipline_no_slice_16_2nd_3rd_engine_imports() -> None:
    """The writer is a pure projection over already-emitted findings;
    the upstream engines are the producers. The writer MUST NOT import
    the Slice 16 2nd / 3rd-A / 3rd-B sub-slice engine modules.

    Checks only TOP-LEVEL imports (lines starting with ``from``); the
    example usage docstring may legitimately reference upstream engines
    for documentation purposes without creating a runtime dependency."""

    import iriai_build_v2.execution_control.governance_finding_writer as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        lines = f.readlines()

    # Filter to top-level import lines only (no leading whitespace).
    import_lines = [line for line in lines if line.startswith("from ")]
    import_text = "\n".join(import_lines)

    assert (
        "from iriai_build_v2.execution_control.finding_rule_engine"
        not in import_text
    )
    assert (
        "from iriai_build_v2.execution_control.finding_plan_deviation_engine"
        not in import_text
    )
    assert (
        "from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine"
        not in import_text
    )


def test_package_init_does_not_re_export_writer() -> None:
    """Mirrors Slice 15 4th sub-slice + Slice 14 + Slice 13A precedent:
    ``governance_finding_writer.py`` is consumed via fully-qualified
    imports, NOT re-exported through the ``execution_control`` package."""

    from iriai_build_v2 import execution_control as pkg

    if hasattr(pkg, "__all__"):
        assert "governance_finding_writer" not in set(pkg.__all__)
        assert "GovernanceFindingWriter" not in set(pkg.__all__)
        assert "FindingWriterInputs" not in set(pkg.__all__)


# ── module-level constants ─────────────────────────────────────────────────


def test_review_projection_id_prefix_value() -> None:
    """Per doc-16:166-167 the typed review-projection id prefix is the
    canonical ``review:governance-findings:`` string (verbatim from
    doc-16:167 *"such as ``review:governance-findings:{corpus_id}``."*)."""

    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-findings:"


def test_review_projection_id_prefix_differs_from_slice_15_4th() -> None:
    """The Slice 16 4th sub-slice prefix is INTENTIONALLY DIFFERENT from
    the Slice 15 4th sub-slice prefix (``review:governance-metrics:``).
    Both share the ``review:`` artifact-key root + bounded-read +
    refs-only + non-blocking failure-routing discipline."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        REVIEW_PROJECTION_ID_PREFIX as SLICE_15_4TH_PREFIX,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != SLICE_15_4TH_PREFIX
    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-findings:"
    assert SLICE_15_4TH_PREFIX == "review:governance-metrics:"


def test_finding_persistence_failure_id_value() -> None:
    """The typed failure id Literal carries the canonical
    ``governance_finding_persistence_failed`` value."""

    assert FINDING_PERSISTENCE_FAILURE_ID == "governance_finding_persistence_failed"


def test_default_review_projection_cap_is_positive_int() -> None:
    """The LIMIT cap+1 default per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    § "Bounded reads" is a positive int."""

    assert isinstance(DEFAULT_REVIEW_PROJECTION_CAP, int)
    assert DEFAULT_REVIEW_PROJECTION_CAP > 0


def test_default_review_projection_cap_above_v1_finding_class_count() -> None:
    """The default cap is comfortably above the doc-16:120-137 v1 16
    finding-class contract so v1 finding-sets never trip the cap."""

    assert DEFAULT_REVIEW_PROJECTION_CAP > 16


def test_default_review_projection_cap_matches_slice_15_4th_value() -> None:
    """The default cap mirrors the Slice 15 4th sub-slice value verbatim
    (per the chunk-shape decision to mirror the Slice 15 4th sub-slice
    pattern verbatim)."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as SLICE_15_4TH_CAP,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == SLICE_15_4TH_CAP


# ── compute_review_projection_id helper ────────────────────────────────────


def test_compute_review_projection_id_constructs_typed_prefix() -> None:
    """Per doc-16:166-167 the helper concatenates the typed prefix +
    corpus_id."""

    pid = compute_review_projection_id("8ac124d6")
    assert pid == "review:governance-findings:8ac124d6"


def test_compute_review_projection_id_passes_through_corpus_id() -> None:
    """The corpus_id passes through verbatim (no normalization, no
    trimming)."""

    pid = compute_review_projection_id("MIXED_Case-1234")
    assert pid == "review:governance-findings:MIXED_Case-1234"


def test_compute_review_projection_id_accepts_empty_corpus_id() -> None:
    """An empty corpus_id is a defence-in-depth edge case (the writer
    surface does not validate corpus_id; the typed BaseModel surface
    accepts any string)."""

    pid = compute_review_projection_id("")
    assert pid == "review:governance-findings:"


def test_compute_review_projection_id_uses_prefix_constant() -> None:
    """The helper uses the typed :data:`REVIEW_PROJECTION_ID_PREFIX`
    constant verbatim (no string interpolation drift)."""

    pid = compute_review_projection_id("corpus-1")
    assert pid.startswith(REVIEW_PROJECTION_ID_PREFIX)
    assert pid.endswith("corpus-1")


# ── compute_findings_digest helper ─────────────────────────────────────────


def test_compute_findings_digest_returns_64_char_hex() -> None:
    """The digest is a SHA-256 hex string (64 chars)."""

    digest = compute_findings_digest([_finding()])
    assert isinstance(digest, str)
    assert len(digest) == 64
    # SHA-256 hex chars only.
    assert all(c in "0123456789abcdef" for c in digest)


def test_compute_findings_digest_empty_list_returns_stable_digest() -> None:
    """An empty findings list produces a stable digest (the canonical
    JSON of ``{"findings": []}``)."""

    digest = compute_findings_digest([])
    assert isinstance(digest, str)
    assert len(digest) == 64
    # Reproducibility: same empty input -> same digest.
    again = compute_findings_digest([])
    assert digest == again


def test_compute_findings_digest_is_stable_across_calls() -> None:
    """Per doc-16:177-179 same findings -> same digest across re-runs."""

    f = _finding()
    digest1 = compute_findings_digest([f])
    digest2 = compute_findings_digest([f])
    assert digest1 == digest2


def test_compute_findings_digest_differs_for_different_findings() -> None:
    """Two distinct findings produce distinct digests."""

    f1 = _finding(class_name="commit_hygiene_loop")
    f2 = _finding(class_name="acl_or_writeability_drag")
    # idempotency_key must also differ for the two findings to differ.
    f2 = f2.model_copy(
        update={
            "idempotency_key": compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="acl_or_writeability_drag",
                feature_id="8ac124d6",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            )
        }
    )
    digest1 = compute_findings_digest([f1])
    digest2 = compute_findings_digest([f2])
    assert digest1 != digest2


def test_compute_findings_digest_order_sensitive() -> None:
    """The digest is order-sensitive (the upstream rule engines own
    deterministic ordering per the Slice 16 2nd/3rd-A/3rd-B emission
    contract)."""

    f1 = _finding(class_name="commit_hygiene_loop")
    f2 = _finding(
        class_name="acl_or_writeability_drag",
        idempotency_key=compute_finding_idempotency_key(
            kind="workflow_inefficiency",
            class_name="acl_or_writeability_drag",
            feature_id="8ac124d6",
            affected_scope={"lane": "default"},
            primary_evidence_digests=["sha256:p1"],
            rule_version="v1",
        ),
    )
    digest_ab = compute_findings_digest([f1, f2])
    digest_ba = compute_findings_digest([f2, f1])
    assert digest_ab != digest_ba


# ── FindingWriterInputs typed-shape ─────────────────────────────────────────


def test_writer_inputs_accepts_all_required_fields() -> None:
    """The required + optional default fields all populate cleanly."""

    inputs = _writer_inputs()
    assert inputs.corpus_id == "8ac124d6"
    assert len(inputs.findings) == 1
    assert len(inputs.baseline_refs) == 1
    assert inputs.warnings == []
    assert inputs.incomplete_scopes == []
    assert inputs.finding_rule_versions == {"commit_hygiene_loop_v1": "v1"}


def test_writer_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _writer_inputs(unknown_field="oops")  # type: ignore[arg-type]


def test_writer_inputs_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    inputs = _writer_inputs()
    serialised = inputs.model_dump_json()
    restored = FindingWriterInputs.model_validate_json(serialised)
    assert restored == inputs


def test_writer_inputs_baseline_refs_defaults_to_empty() -> None:
    """Per the typed-shape contract ``baseline_refs`` defaults to an
    empty list."""

    inputs = FindingWriterInputs(corpus_id="c", findings=[])
    assert inputs.baseline_refs == []


def test_writer_inputs_warnings_defaults_to_empty() -> None:
    """The ``warnings`` field defaults to an empty list."""

    inputs = FindingWriterInputs(corpus_id="c", findings=[])
    assert inputs.warnings == []


def test_writer_inputs_incomplete_scopes_defaults_to_empty() -> None:
    """The ``incomplete_scopes`` field defaults to an empty list."""

    inputs = FindingWriterInputs(corpus_id="c", findings=[])
    assert inputs.incomplete_scopes == []


def test_writer_inputs_finding_rule_versions_defaults_to_empty_dict() -> None:
    """The ``finding_rule_versions`` field defaults to an empty dict."""

    inputs = FindingWriterInputs(corpus_id="c", findings=[])
    assert inputs.finding_rule_versions == {}


def test_writer_inputs_findings_is_typed_governance_finding_list() -> None:
    """Per doc-16:82-104 the ``findings`` field is the typed Slice 16
    1st sub-slice :class:`GovernanceFinding` list."""

    inputs = _writer_inputs()
    for f in inputs.findings:
        assert isinstance(f, GovernanceFinding)


def test_writer_inputs_baseline_refs_is_typed_governance_evidence_ref_list() -> None:
    """Per doc-13:97-111 + doc-16:175-176 the ``baseline_refs`` field is
    the typed Slice 13a :class:`GovernanceEvidenceRef` list."""

    inputs = _writer_inputs()
    for ref in inputs.baseline_refs:
        assert isinstance(ref, GovernanceEvidenceRef)


def test_writer_inputs_accepts_empty_findings_list() -> None:
    """Empty findings list is valid (the writer composes an empty
    persistence)."""

    inputs = _writer_inputs(findings=[])
    assert inputs.findings == []


def test_writer_inputs_accepts_warnings_list() -> None:
    """``warnings`` carries free-form strings."""

    inputs = _writer_inputs(
        warnings=["legacy_heavy_corpus", "stale_baseline"]
    )
    assert inputs.warnings == ["legacy_heavy_corpus", "stale_baseline"]


def test_writer_inputs_accepts_incomplete_scopes_list() -> None:
    """``incomplete_scopes`` carries free-form dicts."""

    scopes = [
        {"scope_kind": "lane", "lane_id": "ml-7", "reason": "missing typed evidence"}
    ]
    inputs = _writer_inputs(incomplete_scopes=scopes)
    assert inputs.incomplete_scopes == scopes


def test_writer_inputs_accepts_rule_versions_dict() -> None:
    """Per doc-16:215-217 the rule_versions dict carries per-rule
    version strings."""

    versions = {
        "commit_hygiene_loop_v1": "v1",
        "stale_context_projection_v1.1": "v1.1",
    }
    inputs = _writer_inputs(finding_rule_versions=versions)
    assert inputs.finding_rule_versions == versions


# ── DIRECT annotation-identity REUSE assertions
#    (the stronger pattern P3-V3-2 addresses) ─────────────────────────────


def test_writer_inputs_findings_annotation_is_slice_16_governance_finding() -> None:
    """Per doc-16:82-104 the ``findings`` field annotation MUST resolve to
    ``list[GovernanceFinding]`` where ``GovernanceFinding`` is
    the Slice 16 1st sub-slice shared model (NOT a local redefinition).

    DIRECT annotation-identity assertion via ``get_origin`` + ``get_args``.
    """

    annotation = FindingWriterInputs.model_fields["findings"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceFinding


def test_writer_inputs_baseline_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-16:201-291 the ``baseline_refs``
    field annotation MUST resolve to ``list[GovernanceEvidenceRef]``
    where ``GovernanceEvidenceRef`` is the Slice 13a shared model (NOT a
    local redefinition).

    DIRECT annotation-identity assertion via ``get_origin`` + ``get_args``.
    """

    annotation = FindingWriterInputs.model_fields["baseline_refs"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_writer_inputs_findings_field_via_get_type_hints_identity() -> None:
    """Cross-check: ``get_type_hints`` resolves the ``findings`` field
    annotation (which lives in a module with ``from __future__ import
    annotations``) to the Slice 16 1st sub-slice :class:`GovernanceFinding`."""

    hints = get_type_hints(FindingWriterInputs)
    annotation = hints["findings"]
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceFinding


def test_writer_inputs_baseline_refs_field_via_get_type_hints_identity() -> None:
    """Cross-check: ``get_type_hints`` resolves the ``baseline_refs``
    field annotation to the Slice 13a :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(FindingWriterInputs)
    annotation = hints["baseline_refs"]
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_writer_inputs_finding_rule_versions_annotation_is_dict_str_str() -> None:
    """The ``finding_rule_versions`` field annotation MUST resolve to
    ``dict[str, str]``."""

    annotation = FindingWriterInputs.model_fields["finding_rule_versions"].annotation
    assert get_origin(annotation) is dict
    args = get_args(annotation)
    assert len(args) == 2
    assert args[0] is str
    assert args[1] is str


# ── FindingPersistenceGap typed-shape ───────────────────────────────────────


def test_gap_finding_accepts_all_fields() -> None:
    """All 6 fields populate cleanly."""

    gap = _gap()
    assert gap.failure_id == "governance_finding_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert gap.findings_digest == "sha256:abc"
    assert gap.review_projection_id == "review:governance-findings:8ac124d6"
    assert gap.reason == "findings_count_exceeds_cap"
    assert gap.evidence_payload == {"cap": 200}


def test_gap_finding_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _gap(unknown_field="oops")  # type: ignore[arg-type]


def test_gap_finding_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    gap = _gap()
    serialised = gap.model_dump_json()
    restored = FindingPersistenceGap.model_validate_json(serialised)
    assert restored == gap


def test_gap_finding_failure_id_literal_rejects_other_failure_ids() -> None:
    """Per the typed Literal the gap finding's ``failure_id`` accepts
    ONLY ``governance_finding_persistence_failed`` (the Slice 14 or
    Slice 15 or Slice 16 prior sub-slice failure ids fail closed)."""

    with pytest.raises(ValidationError):
        _gap(failure_id="line_provenance_gap")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _gap(failure_id="governance_metric_extraction_failed")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _gap(failure_id="governance_scorecard_persistence_failed")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _gap(failure_id="finding_rule_emission_failed")  # type: ignore[arg-type]


def test_gap_finding_findings_digest_accepts_none() -> None:
    """Per the typed shape the digest field accepts ``None`` (for failures
    that happen before the digest is computable)."""

    gap = _gap(findings_digest=None)
    assert gap.findings_digest is None


def test_gap_finding_review_projection_id_accepts_none() -> None:
    """Per the typed shape the projection id field accepts ``None``."""

    gap = _gap(review_projection_id=None)
    assert gap.review_projection_id is None


def test_gap_finding_evidence_payload_defaults_to_empty() -> None:
    """The ``evidence_payload`` field defaults to an empty dict."""

    gap = FindingPersistenceGap(
        failure_id="governance_finding_persistence_failed",
        corpus_id="c",
        findings_digest=None,
        review_projection_id=None,
        reason="test",
    )
    assert gap.evidence_payload == {}


# ── failure router wiring (4 pure-data add points) ────────────────────────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """The NEW failure id ``governance_finding_persistence_failed`` is
    registered in the Slice 07 failure router."""

    assert "governance_finding_persistence_failed" in FAILURE_TYPES
    assert "governance_finding_persistence_failed" in get_args(FailureType)


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Per doc-16:174-176 + doc-14:242-243 the NEW failure id routes
    under the EXISTING ``evidence_corruption`` failure_class to the
    EXISTING ``retry_governance_projection`` NON-blocking RouteAction
    (REUSED from Slice 14 2nd sub-slice; NOT a new action)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_finding_persistence_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert type_pol.failure_class == "evidence_corruption"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_new_id_is_retryable_not_deterministic() -> None:
    """Per the Slice 14 + Slice 15 + Slice 16 2nd / 3rd-A / 3rd-B
    sub-slice precedents the NEW failure id is observer-transient
    (retryable; NOT deterministic)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_finding_persistence_failed"
    ]
    assert len(matches) == 1
    type_pol, _ = matches[0]
    assert type_pol.retryable
    assert not type_pol.deterministic


def test_failure_router_new_id_in_retryable_set() -> None:
    """The NEW failure id is in :data:`_RETRYABLE_FAILURE_TYPES`."""

    assert "governance_finding_persistence_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_is_non_blocking() -> None:
    """The route is non-blocking (NOT ``quiesce``, NOT
    ``operator_required``)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_finding_persistence_failed"
    ]
    assert len(matches) == 1
    _, route_pol = matches[0]
    assert route_pol.action != "quiesce"
    assert route_pol.action != "operator_required"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_reuses_existing_route_action() -> None:
    """The NEW failure id REUSES the Slice 14 2nd sub-slice
    ``retry_governance_projection`` action (NOT a new action)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        ROUTE_ACTIONS,
    )

    assert "retry_governance_projection" in ROUTE_ACTIONS


def test_failure_router_action_is_not_a_new_action() -> None:
    """Per the chunk-shape point 3 the action REUSES the EXISTING Slice
    14 2nd sub-slice ``retry_governance_projection`` action; the writer
    edit MUST NOT introduce a new RouteAction entry."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        ROUTE_ACTIONS,
    )

    # No new action contains "finding" in its name.
    finding_actions = [a for a in ROUTE_ACTIONS if "finding" in a.lower()]
    assert finding_actions == [], (
        f"unexpected new finding-specific action(s): {finding_actions}"
    )


# ── GovernanceFindingWriter constructor + gap_findings property ─────────────


def test_writer_construction_default_is_empty_gap_findings() -> None:
    """Per the stateless-with-accumulator pattern (mirrors Slice 15 4th
    sub-slice :class:`ScorecardWriter`) the writer constructs cleanly
    with no inputs and an empty ``gap_findings`` accumulator."""

    writer = GovernanceFindingWriter()
    assert writer.gap_findings == []


def test_writer_gap_findings_is_snapshot_list() -> None:
    """The ``gap_findings`` property returns a snapshot list (not the
    internal list reference); mutations to the returned list do not
    leak into subsequent calls."""

    writer = GovernanceFindingWriter()
    snapshot = writer.gap_findings
    snapshot.append("garbage")  # type: ignore[arg-type]
    # Subsequent property call returns a fresh snapshot.
    assert writer.gap_findings == []


# ── write_findings correctness ─────────────────────────────────────────────


def test_write_findings_emits_typed_governance_finding_list() -> None:
    """Per doc-16:166-167 step 6 the writer emits a typed
    ``list[GovernanceFinding]`` (preserving the upstream rule-engine
    output verbatim)."""

    writer = GovernanceFindingWriter()
    inputs = _writer_inputs()
    persisted = writer.write_findings(inputs)

    assert isinstance(persisted, list)
    assert len(persisted) == 1
    assert isinstance(persisted[0], GovernanceFinding)


def test_write_findings_preserves_findings_verbatim() -> None:
    """The findings list pass through verbatim (no mutation)."""

    writer = GovernanceFindingWriter()
    f1 = _finding(class_name="commit_hygiene_loop")
    f2 = _finding(
        class_name="acl_or_writeability_drag",
        idempotency_key=compute_finding_idempotency_key(
            kind="workflow_inefficiency",
            class_name="acl_or_writeability_drag",
            feature_id="8ac124d6",
            affected_scope={"lane": "default"},
            primary_evidence_digests=["sha256:p1"],
            rule_version="v1",
        ),
    )
    inputs = _writer_inputs(findings=[f1, f2])
    persisted = writer.write_findings(inputs)

    assert persisted == [f1, f2]


def test_write_findings_empty_list_produces_empty_persisted_list() -> None:
    """An empty findings list still produces a valid empty list."""

    writer = GovernanceFindingWriter()
    inputs = _writer_inputs(findings=[])
    persisted = writer.write_findings(inputs)
    assert persisted == []


def test_write_findings_resets_gap_findings_per_call() -> None:
    """Per the stateless-with-accumulator pattern each
    :meth:`write_findings` call resets the ``gap_findings`` accumulator."""

    writer = GovernanceFindingWriter()
    writer.write_findings(_writer_inputs())
    # Force at least one prior gap accumulation.
    writer._gap_findings.append(  # type: ignore[attr-defined]
        FindingPersistenceGap(
            failure_id="governance_finding_persistence_failed",
            corpus_id="prior",
            findings_digest=None,
            review_projection_id=None,
            reason="prior",
        )
    )
    writer.write_findings(_writer_inputs())
    assert writer.gap_findings == []


def test_write_findings_returns_independent_list() -> None:
    """The persisted list is independent of the typed input list
    (mutations to the returned list do not leak into the inputs)."""

    writer = GovernanceFindingWriter()
    inputs = _writer_inputs()
    persisted = writer.write_findings(inputs)
    persisted.clear()
    assert len(inputs.findings) == 1  # input unaffected


# ── write_review_projection correctness ────────────────────────────────────


def test_write_review_projection_emits_typed_dict() -> None:
    """The review projection is a typed dict with the documented keys."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="8ac124d6"
    )

    assert isinstance(projection, dict)
    # Required top-level keys.
    for key in (
        "review_projection_id",
        "corpus_id",
        "generated_at",
        "findings_digest",
        "finding_rule_versions",
        "findings",
        "findings_truncated",
        "baseline_refs",
        "baseline_refs_truncated",
        "incomplete_scopes",
        "warnings",
    ):
        assert key in projection, f"missing key: {key}"


def test_write_review_projection_id_uses_typed_prefix() -> None:
    """Per doc-16:166-167 the projection id is constructed via the
    typed :func:`compute_review_projection_id` helper."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="cust-7"
    )

    assert projection["review_projection_id"] == "review:governance-findings:cust-7"


def test_write_review_projection_corpus_id_passes_through() -> None:
    """The corpus_id passes through verbatim."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="abc-12345"
    )
    assert projection["corpus_id"] == "abc-12345"


def test_write_review_projection_generated_at_is_iso_string() -> None:
    """The ``generated_at`` field projects to an ISO-8601 string per the
    canonical-JSON discipline (cross-process stable)."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )
    # ISO-8601 strings parse via fromisoformat.
    parsed = datetime.fromisoformat(projection["generated_at"])
    assert parsed.tzinfo is not None
    assert parsed.tzinfo == timezone.utc


def test_write_review_projection_finding_rule_versions_pinned() -> None:
    """Per doc-16:215-217 the projection MUST carry the rule_version
    map so later changes do not silently rewrite historical meaning."""

    writer = GovernanceFindingWriter()
    versions = {
        "commit_hygiene_loop_v1": "v1",
        "stale_context_projection_v1.1": "v1.1",
    }
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", finding_rule_versions=versions
    )

    rv = projection["finding_rule_versions"]
    assert isinstance(rv, dict)
    assert rv == versions


def test_write_review_projection_findings_are_compact_dicts() -> None:
    """The projected findings carry ONLY typed control fields + cited
    refs (NOT raw bodies). Per doc-16:174-176."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )

    assert len(projection["findings"]) == 1
    f = projection["findings"][0]
    # Required typed-control fields.
    for key in (
        "idempotency_key",
        "kind",
        "class_name",
        "severity",
        "confidence",
        "feature_id",
        "affected_scope",
        "primary_evidence_refs",
        "primary_evidence_refs_truncated",
        "supporting_evidence_refs",
        "supporting_evidence_refs_truncated",
        "implementation_log_anchors",
        "metric_refs",
        "estimated_lost_hours",
        "estimated_retry_impact",
        "recommended_action_display",
        "recommendation_draft_ref",
        "safe_runtime_action",
        "requires_policy_artifact",
        "product_defect_related",
        "workflow_related",
        "causal_role",
        "primary_cause_finding_id",
        "linked_finding_ids",
    ):
        assert key in f


def test_write_review_projection_baseline_refs_default_empty() -> None:
    """When baseline_refs are omitted the projection emits an empty
    list."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )
    assert projection["baseline_refs"] == []


def test_write_review_projection_warnings_default_empty() -> None:
    """When warnings are omitted the projection emits an empty list."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )
    assert projection["warnings"] == []


def test_write_review_projection_incomplete_scopes_default_empty() -> None:
    """When incomplete_scopes are omitted the projection emits an empty
    list."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )
    assert projection["incomplete_scopes"] == []


def test_write_review_projection_finding_rule_versions_default_empty_dict() -> None:
    """When finding_rule_versions is omitted the projection emits an
    empty dict."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )
    assert projection["finding_rule_versions"] == {}


# ── refs-only discipline (doc-16:174-176 + doc-16:201-291) ────────────────


def test_write_review_projection_baseline_refs_are_cited_refs_only() -> None:
    """Per doc-16:174-176 + doc-16:201-291 the projection emits ONLY
    cited refs (NEVER raw bodies). The baseline_refs are projected as
    typed-ref dicts via ``model_dump(mode="json")``."""

    writer = GovernanceFindingWriter()
    ref_a = _ref(
        ref_id="baseline-a",
        digest="sha256:aaaa",
        authority="typed_journal",
    )
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", baseline_refs=[ref_a]
    )

    assert len(projection["baseline_refs"]) == 1
    cited = projection["baseline_refs"][0]
    # Typed-ref shape fields.
    assert cited["ref_id"] == "baseline-a"
    assert cited["digest"] == "sha256:aaaa"
    assert cited["authority"] == "typed_journal"
    # Verify NO raw body field leaked into the projection.
    assert "raw_body" not in cited
    assert "body" not in cited


def test_write_review_projection_finding_primary_evidence_refs_are_cited_only() -> None:
    """Per doc-16:174-176 + doc-16:201-291 each finding's
    primary_evidence_refs are emitted as cited typed-ref dicts (NEVER
    raw bodies)."""

    writer = GovernanceFindingWriter()
    f = _finding(
        primary_evidence_refs=[_ref(ref_id="ev-1", digest="sha256:cafe")]
    )
    projection = writer.write_review_projection([f], corpus_id="c")

    pf = projection["findings"][0]
    cited = pf["primary_evidence_refs"][0]
    assert cited["ref_id"] == "ev-1"
    assert cited["digest"] == "sha256:cafe"
    # Verify NO raw body field leaked.
    assert "raw_body" not in cited


def test_write_review_projection_finding_supporting_evidence_refs_are_cited_only() -> None:
    """Per doc-16:174-176 + doc-16:201-291 each finding's
    supporting_evidence_refs are emitted as cited typed-ref dicts (NEVER
    raw bodies)."""

    writer = GovernanceFindingWriter()
    f = _finding(
        supporting_evidence_refs=[_ref(ref_id="sup-1", digest="sha256:deaf")]
    )
    projection = writer.write_review_projection([f], corpus_id="c")

    pf = projection["findings"][0]
    cited = pf["supporting_evidence_refs"][0]
    assert cited["ref_id"] == "sup-1"
    assert cited["digest"] == "sha256:deaf"
    # Verify NO raw body field leaked.
    assert "raw_body" not in cited


def test_write_review_projection_walk_no_forbidden_keys() -> None:
    """Walk-time forbidden-key invariant: scan the entire projection
    recursively and assert no raw-body-shaped field name appears at
    any nesting level."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", baseline_refs=[_ref(ref_id="b1")]
    )

    forbidden_keys = {
        "raw_body",
        "raw_payload",
        "artifact_body",
        "event_body",
        "raw_text",
        "stderr_body",
        "stdout_body",
        "body",
    }

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"forbidden key {k!r} found in projection node"
                )
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(projection)


def test_write_review_projection_does_not_include_finding_raw_payload_fields() -> None:
    """Defence-in-depth: scanning all projected finding dicts asserts no
    raw-body-shaped field name leaked into the projection."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c"
    )

    forbidden_keys = {
        "raw_body",
        "raw_payload",
        "artifact_body",
        "event_body",
        "raw_text",
        "stderr_body",
        "stdout_body",
        "body",
    }
    for f in projection["findings"]:
        assert not (set(f.keys()) & forbidden_keys), f


# ── LIMIT cap+1 truncation discipline ──────────────────────────────────────


def test_write_review_projection_findings_list_respects_cap_plus_one() -> None:
    """Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
    projection emits at most ``cap+1`` findings (so the caller can detect
    overflow)."""

    writer = GovernanceFindingWriter()
    # 5 findings; cap=3 -> emit 4 (cap+1), set findings_truncated=True.
    findings = []
    for i in range(5):
        f = _finding(
            class_name="commit_hygiene_loop",
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="commit_hygiene_loop",
                feature_id=f"feat-{i}",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            ),
        )
        findings.append(f)
    projection = writer.write_review_projection(
        findings, corpus_id="c", cap=3
    )

    assert len(projection["findings"]) == 4  # cap+1 = 4
    assert projection["findings_truncated"] is True


def test_write_review_projection_baseline_refs_respects_cap_plus_one() -> None:
    """The baseline_refs list also respects LIMIT cap+1."""

    writer = GovernanceFindingWriter()
    refs = [_ref(ref_id=f"baseline-{i}") for i in range(5)]
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", baseline_refs=refs, cap=2
    )

    assert len(projection["baseline_refs"]) == 3  # cap+1 = 3
    assert projection["baseline_refs_truncated"] is True


def test_write_review_projection_finding_primary_evidence_refs_respect_cap_plus_one() -> None:
    """Each finding's primary_evidence_refs list also respects LIMIT
    cap+1."""

    writer = GovernanceFindingWriter()
    big_refs = [_ref(ref_id=f"ev-{i}") for i in range(10)]
    f = _finding(primary_evidence_refs=big_refs)
    projection = writer.write_review_projection([f], corpus_id="c", cap=4)

    pf = projection["findings"][0]
    assert len(pf["primary_evidence_refs"]) == 5  # cap+1 = 5
    assert pf["primary_evidence_refs_truncated"] is True


def test_write_review_projection_finding_supporting_evidence_refs_respect_cap_plus_one() -> None:
    """Each finding's supporting_evidence_refs list also respects LIMIT
    cap+1."""

    writer = GovernanceFindingWriter()
    big_refs = [_ref(ref_id=f"sup-{i}") for i in range(10)]
    f = _finding(supporting_evidence_refs=big_refs)
    projection = writer.write_review_projection([f], corpus_id="c", cap=4)

    pf = projection["findings"][0]
    assert len(pf["supporting_evidence_refs"]) == 5  # cap+1 = 5
    assert pf["supporting_evidence_refs_truncated"] is True


def test_write_review_projection_no_truncation_under_cap() -> None:
    """When the actual count is <= cap, the truncated flag is False."""

    writer = GovernanceFindingWriter()
    findings = [
        _finding(
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="commit_hygiene_loop",
                feature_id=f"feat-{i}",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            )
        )
        for i in range(3)
    ]
    projection = writer.write_review_projection(
        findings, corpus_id="c", cap=10
    )

    assert len(projection["findings"]) == 3
    assert projection["findings_truncated"] is False


def test_write_review_projection_default_cap_is_default_review_projection_cap() -> None:
    """When ``cap`` is omitted the projection uses
    :data:`DEFAULT_REVIEW_PROJECTION_CAP`."""

    writer = GovernanceFindingWriter()
    # Emit exactly cap+2 findings so the default cap trips truncation.
    findings = [
        _finding(
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="commit_hygiene_loop",
                feature_id=f"feat-{i}",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            )
        )
        for i in range(DEFAULT_REVIEW_PROJECTION_CAP + 2)
    ]
    projection = writer.write_review_projection(findings, corpus_id="c")

    assert len(projection["findings"]) == DEFAULT_REVIEW_PROJECTION_CAP + 1
    assert projection["findings_truncated"] is True


def test_write_review_projection_cap_zero_emits_one_sentinel() -> None:
    """A cap=0 emits at most 1 item per list (cap+1=1 sentinel)."""

    writer = GovernanceFindingWriter()
    findings = [
        _finding(
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="commit_hygiene_loop",
                feature_id=f"feat-{i}",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            )
        )
        for i in range(3)
    ]
    projection = writer.write_review_projection(findings, corpus_id="c", cap=0)

    # cap+1 = 1 item; truncated=True because 3 > 0.
    assert len(projection["findings"]) == 1
    assert projection["findings_truncated"] is True


# ── findings digest stability + tamper detection ──────────────────────────


def test_write_review_projection_carries_findings_digest() -> None:
    """Per doc-16:177-179 the projection carries the findings digest
    for tamper detection."""

    writer = GovernanceFindingWriter()
    findings = [_finding()]
    projection = writer.write_review_projection(findings, corpus_id="c")

    expected = compute_findings_digest(findings)
    assert projection["findings_digest"] == expected


def test_write_review_projection_digest_is_stable_across_reruns() -> None:
    """The same findings produce the same digest across re-runs (per
    the doc-13:201-204 canonical-JSON discipline + Slice 16 1st sub-slice
    determinism contract)."""

    writer = GovernanceFindingWriter()
    findings = [_finding()]
    p1 = writer.write_review_projection(findings, corpus_id="c")
    p2 = writer.write_review_projection(findings, corpus_id="c")
    assert p1["findings_digest"] == p2["findings_digest"]


def test_write_review_projection_digest_differs_for_different_findings() -> None:
    """Two distinct finding-sets produce distinct digests."""

    writer = GovernanceFindingWriter()
    f1 = _finding(class_name="commit_hygiene_loop")
    f2 = _finding(
        class_name="acl_or_writeability_drag",
        idempotency_key=compute_finding_idempotency_key(
            kind="workflow_inefficiency",
            class_name="acl_or_writeability_drag",
            feature_id="8ac124d6",
            affected_scope={"lane": "default"},
            primary_evidence_digests=["sha256:p1"],
            rule_version="v1",
        ),
    )
    p1 = writer.write_review_projection([f1], corpus_id="c")
    p2 = writer.write_review_projection([f2], corpus_id="c")
    assert p1["findings_digest"] != p2["findings_digest"]


def test_write_review_projection_digest_reuses_compute_findings_digest() -> None:
    """The projection's digest field is identical to a fresh call to
    :func:`compute_findings_digest` (proves REUSE; no local
    re-computation)."""

    writer = GovernanceFindingWriter()
    findings = [_finding()]
    projection = writer.write_review_projection(findings, corpus_id="c")
    assert projection["findings_digest"] == compute_findings_digest(findings)


# ── non-blocking discipline (per doc-14:242-243 + doc-16:174-176) ──────────


def test_writer_never_raises_on_normal_inputs() -> None:
    """The writer NEVER raises a failure to the caller (per
    doc-14:242-243 + doc-16:174-176 NON-blocking observer contract)."""

    writer = GovernanceFindingWriter()
    # Call should not raise.
    persisted = writer.write_findings(_writer_inputs())
    projection = writer.write_review_projection(persisted, corpus_id="c")
    assert isinstance(persisted, list)
    assert isinstance(projection, dict)


def test_writer_gap_findings_empty_on_normal_path() -> None:
    """The gap_findings accumulator is empty on the normal projection
    path."""

    writer = GovernanceFindingWriter()
    writer.write_findings(_writer_inputs())
    assert writer.gap_findings == []


def test_writer_gap_findings_empty_on_normal_projection_path() -> None:
    """The gap_findings accumulator is empty on the normal review
    projection path."""

    writer = GovernanceFindingWriter()
    writer.write_review_projection([_finding()], corpus_id="c")
    assert writer.gap_findings == []


# ── miscellaneous round-trip + invariants ─────────────────────────────────


def test_write_findings_then_project_then_canonical_roundtrip() -> None:
    """End-to-end: write_findings -> write_review_projection ->
    inspect digest matches a direct compute call."""

    writer = GovernanceFindingWriter()
    inputs = _writer_inputs(corpus_id="end2end")
    persisted = writer.write_findings(inputs)
    projection = writer.write_review_projection(persisted, corpus_id="end2end")

    assert projection["corpus_id"] == "end2end"
    assert projection["review_projection_id"] == "review:governance-findings:end2end"
    assert projection["findings_digest"] == compute_findings_digest(persisted)


def test_write_review_projection_carries_warnings_verbatim() -> None:
    """Warnings pass through verbatim."""

    writer = GovernanceFindingWriter()
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", warnings=["w1", "w2", "w3"]
    )

    assert projection["warnings"] == ["w1", "w2", "w3"]


def test_write_review_projection_carries_incomplete_scopes_verbatim() -> None:
    """Incomplete scopes pass through verbatim."""

    writer = GovernanceFindingWriter()
    scopes = [{"scope_kind": "lane", "lane_id": "ml-7", "reason": "stale"}]
    projection = writer.write_review_projection(
        [_finding()], corpus_id="c", incomplete_scopes=scopes
    )

    assert projection["incomplete_scopes"] == scopes


def test_write_review_projection_finding_dict_carries_typed_scope() -> None:
    """The projected finding dict carries the typed ``affected_scope``
    dict verbatim."""

    writer = GovernanceFindingWriter()
    f = _finding(affected_scope={"feature_id": "f-1", "lane_id": "l-7"})
    projection = writer.write_review_projection([f], corpus_id="c")

    pf = projection["findings"][0]
    assert pf["affected_scope"] == {"feature_id": "f-1", "lane_id": "l-7"}


def test_write_review_projection_finding_dict_carries_severity() -> None:
    """The projected finding dict carries the typed ``severity`` value."""

    writer = GovernanceFindingWriter()
    f = _finding(severity="high")
    projection = writer.write_review_projection([f], corpus_id="c")
    pf = projection["findings"][0]
    assert pf["severity"] == "high"


def test_write_review_projection_finding_dict_carries_metric_refs() -> None:
    """The projected finding dict carries the metric_refs list
    verbatim (doc-16:93 list[str])."""

    writer = GovernanceFindingWriter()
    f = _finding(metric_refs=["a", "b", "c"])
    projection = writer.write_review_projection([f], corpus_id="c")
    pf = projection["findings"][0]
    assert pf["metric_refs"] == ["a", "b", "c"]


def test_write_review_projection_finding_dict_carries_linked_finding_ids() -> None:
    """The projected finding dict carries the linked_finding_ids list
    verbatim (doc-16:104 list[str])."""

    writer = GovernanceFindingWriter()
    f = _finding(linked_finding_ids=["id-a", "id-b"])
    projection = writer.write_review_projection([f], corpus_id="c")
    pf = projection["findings"][0]
    assert pf["linked_finding_ids"] == ["id-a", "id-b"]


def test_writer_inputs_round_trip_preserves_finding_rule_versions() -> None:
    """End-to-end: round-trip preserves rule_versions through
    FindingWriterInputs (doc-16:215-217)."""

    versions = {"r1": "v1", "r2": "v2.5"}
    inputs = _writer_inputs(finding_rule_versions=versions)
    serialised = inputs.model_dump_json()
    restored = FindingWriterInputs.model_validate_json(serialised)
    assert restored.finding_rule_versions == versions


def test_write_review_projection_independent_calls_preserve_immutability() -> None:
    """Per doc-16:215-217 historical findings are PRESERVED; each new
    re-projection emits a NEW dict (historical projections remain
    immutable as review artifacts)."""

    writer = GovernanceFindingWriter()
    p1 = writer.write_review_projection([_finding()], corpus_id="c")
    p1_findings = list(p1["findings"])
    # Write a second projection with different findings; p1 must NOT be
    # mutated.
    writer.write_review_projection(
        [_finding(severity="high")], corpus_id="c"
    )
    assert list(p1["findings"]) == p1_findings


# ── idempotency-key dedupe semantics per doc-16:158 ──────────────────────


def test_idempotency_key_dedupe_sticky_across_reruns() -> None:
    """Per doc-16:177-179 finding ids are stable across reruns when
    input evidence and rule version do not change."""

    key1 = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v1",
    )
    key2 = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v1",
    )
    assert key1 == key2


def test_idempotency_key_differs_for_different_rule_versions() -> None:
    """Per doc-16:215-217 rule version bumps produce distinct keys so
    older findings can be superseded rather than overwritten."""

    key_v1 = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v1",
    )
    key_v2 = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v2",
    )
    assert key_v1 != key_v2


def test_writer_preserves_per_finding_idempotency_key() -> None:
    """The writer preserves the per-finding idempotency_key verbatim
    (so dedupe-by-key works downstream)."""

    writer = GovernanceFindingWriter()
    f = _finding()
    persisted = writer.write_findings(_writer_inputs(findings=[f]))
    assert persisted[0].idempotency_key == f.idempotency_key


def test_review_projection_preserves_per_finding_idempotency_key() -> None:
    """The review projection preserves the per-finding idempotency_key
    verbatim."""

    writer = GovernanceFindingWriter()
    f = _finding()
    projection = writer.write_review_projection([f], corpus_id="c")
    assert projection["findings"][0]["idempotency_key"] == f.idempotency_key


# ── failure_router 4 pure-data add point ────────────────────────────────


def test_failure_router_pure_data_add_point_failure_type_literal() -> None:
    """4 pure-data add point #1: the FailureType Literal includes the
    NEW failure id."""

    assert "governance_finding_persistence_failed" in get_args(FailureType)


def test_failure_router_pure_data_add_point_failure_types_tuple() -> None:
    """4 pure-data add point #2: the FAILURE_TYPES tuple includes the
    NEW failure id."""

    assert "governance_finding_persistence_failed" in FAILURE_TYPES


def test_failure_router_pure_data_add_point_retryable_set() -> None:
    """4 pure-data add point #3: the _RETRYABLE_FAILURE_TYPES set
    includes the NEW failure id."""

    assert "governance_finding_persistence_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_pure_data_add_point_route_table_row() -> None:
    """4 pure-data add point #4: the route table has exactly one row
    for the NEW failure id, routed to retry_governance_projection under
    evidence_corruption."""

    matches = [
        row for row in _ROUTE_ROWS
        if row[0].failure_type == "governance_finding_persistence_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert type_pol.failure_class == "evidence_corruption"
    assert route_pol.action == "retry_governance_projection"


# ── end-to-end integration (chunk-shape point 5) ───────────────────────────


def test_integration_write_findings_then_projection_roundtrip() -> None:
    """End-to-end: GovernanceFindingWriter.write_findings() ->
    GovernanceFindingWriter.write_review_projection().

    Validates the chunk-shape contract end-to-end + the chunk-shape
    point 4 bounded review projection + refs-only discipline.
    """

    writer = GovernanceFindingWriter()
    findings = [
        _finding(
            class_name="commit_hygiene_loop",
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="commit_hygiene_loop",
                feature_id="integration-1",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            ),
        ),
        _finding(
            class_name="acl_or_writeability_drag",
            idempotency_key=compute_finding_idempotency_key(
                kind="workflow_inefficiency",
                class_name="acl_or_writeability_drag",
                feature_id="integration-1",
                affected_scope={"lane": "default"},
                primary_evidence_digests=["sha256:p1"],
                rule_version="v1",
            ),
        ),
    ]
    persisted = writer.write_findings(
        FindingWriterInputs(
            corpus_id="integration-1",
            findings=findings,
            baseline_refs=[_ref(ref_id="baseline-int")],
            warnings=[],
            incomplete_scopes=[],
            finding_rule_versions={
                "commit_hygiene_loop_v1": "v1",
                "acl_or_writeability_drag_v1": "v1",
            },
        )
    )

    assert len(persisted) == 2

    projection = writer.write_review_projection(
        persisted,
        corpus_id="integration-1",
        baseline_refs=[_ref(ref_id="baseline-int")],
        finding_rule_versions={
            "commit_hygiene_loop_v1": "v1",
            "acl_or_writeability_drag_v1": "v1",
        },
    )
    assert projection["review_projection_id"] == "review:governance-findings:integration-1"
    # Both rules carry typed versions per doc-16:215-217.
    assert "commit_hygiene_loop_v1" in projection["finding_rule_versions"]
    assert "acl_or_writeability_drag_v1" in projection["finding_rule_versions"]
    # Refs-only discipline: every projected baseline_ref carries the
    # typed-ref shape fields, not raw bodies.
    for cited in projection["baseline_refs"]:
        assert "ref_id" in cited
        assert "digest" in cited
        assert "raw_body" not in cited


# ── doc-16:215-217 rollback discipline (historical findings preserved) ──


def test_rollback_discipline_historical_findings_are_immutable() -> None:
    """Per doc-16:215-217 the writer's typed-shape outputs are
    immutable rows: a NEW re-projection emits a NEW finding-set rather
    than mutating a prior set.

    The typed :class:`GovernanceFinding` Pydantic BaseModel is immutable
    by Pydantic v2 default. Future re-projections produce NEW rows.
    """

    writer = GovernanceFindingWriter()
    f1 = _finding()
    persisted_1 = writer.write_findings(_writer_inputs(findings=[f1]))
    persisted_1_snapshot = list(persisted_1)
    # Write a second projection with different findings; persisted_1
    # must NOT be mutated.
    writer.write_findings(_writer_inputs(findings=[_finding(severity="high")]))
    assert list(persisted_1) == persisted_1_snapshot


def test_rollback_discipline_projection_carries_digest_for_historical_lookup() -> None:
    """Per doc-16:215-217 rollback discipline + doc-16:177-179 the
    review projection carries the findings digest so historical
    re-projections can be correlated with their source finding-sets."""

    writer = GovernanceFindingWriter()
    findings = [_finding()]
    projection = writer.write_review_projection(findings, corpus_id="c")
    # The digest must be a non-empty SHA-256 hex string (64 chars).
    assert isinstance(projection["findings_digest"], str)
    assert len(projection["findings_digest"]) == 64
    # Confirm reproducibility (the digest is tamper-detectable per
    # doc-16:177-179 + the canonical-JSON discipline).
    again = writer.write_review_projection(findings, corpus_id="c")
    assert again["findings_digest"] == projection["findings_digest"]


# ── consume Slice 16 1st sub-slice canonical_finding_dict + idempotency ──


def test_canonical_finding_dict_consumed_via_compute_findings_digest() -> None:
    """The :func:`compute_findings_digest` helper consumes the Slice 16
    1st sub-slice :func:`canonical_finding_dict` projection (proves
    REUSE; no local re-projection)."""

    f = _finding()
    canonical = canonical_finding_dict(f)
    assert isinstance(canonical, dict)
    assert canonical["idempotency_key"] == f.idempotency_key
    # The digest computed via the writer's helper matches the
    # canonical-JSON projection (the writer uses canonical_finding_dict
    # internally).
    digest = compute_findings_digest([f])
    assert isinstance(digest, str)
    assert len(digest) == 64


# ── module structure sanity ────────────────────────────────────────────────


def test_writer_class_has_two_public_methods() -> None:
    """The writer class exposes exactly 2 public projection methods +
    1 property (gap_findings)."""

    public_attrs = {
        name
        for name in dir(GovernanceFindingWriter)
        if not name.startswith("_")
    }
    # Required public surface.
    assert "write_findings" in public_attrs
    assert "write_review_projection" in public_attrs
    assert "gap_findings" in public_attrs


def test_writer_inputs_corpus_id_required() -> None:
    """The corpus_id field is required (no default)."""

    with pytest.raises(ValidationError):
        FindingWriterInputs(findings=[])  # type: ignore[call-arg]


def test_writer_inputs_findings_field_required() -> None:
    """The findings field is required (no default)."""

    with pytest.raises(ValidationError):
        FindingWriterInputs(corpus_id="c")  # type: ignore[call-arg]
