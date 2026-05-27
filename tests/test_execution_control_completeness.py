"""Slice 13A second sub-slice -- unit tests for the foundational
``execution_control/completeness.py`` typed-shape module.

Covers the 6 doc-13a:127-192 typed shapes:

- The 2 ``Literal`` enums (``CompletenessState``, ``EvidenceAuthority``)
  -- exact members + cardinality + the **namespace-distinction** assertion
  vs the 9-value ``governance.models.EvidenceAuthority``.
- The 4 ``BaseModel`` classes:
  - :class:`EvidencePageRef` -- 9 fields + 7-value ``source_kind`` Literal
    + Optional ``start`` / ``end`` per doc-13a:153-154 + round-trip.
  - :class:`EvidenceCompleteness` -- 8 fields + default-empty lists +
    Optional ``preview_ref`` / ``unavailable_reason``.
  - :class:`ExactEvidenceManifest` -- 11 fields per doc-13a:172-184 +
    Optional ``group_idx`` / ``display_preview_ref``.
  - :class:`AuthoritativeContextRef` -- 5 fields per doc-13a:186-192.
- The ``compute_completeness_digest`` helper -- deterministic across two
  runs (byte-identical sha256 hex) + canonical-JSON discipline.

Every model enforces ``extra="forbid"`` (typo-d kwargs -> ``ValidationError``).
Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; the doc-13a:42-55 change-control rule forbids executor
wiring outside this slice's own acceptance tests.
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    CompletenessState,
    EvidenceAuthority,
    EvidenceCompleteness,
    EvidencePageRef,
    ExactEvidenceManifest,
    compute_completeness_digest,
)
from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceAuthority as GovernanceEvidenceAuthority,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 6 typed shapes + the digest helper.

    Per doc-13a:127-192 the 6 typed shapes are
    ``CompletenessState`` + ``EvidenceAuthority`` + ``EvidencePageRef`` +
    ``EvidenceCompleteness`` + ``ExactEvidenceManifest`` +
    ``AuthoritativeContextRef``. Plus the
    ``compute_completeness_digest`` helper at doc-13a:264. Total: 7
    exported names.
    """

    from iriai_build_v2.execution_control import completeness as mod

    expected = {
        "CompletenessState",
        "EvidenceAuthority",
        "EvidencePageRef",
        "EvidenceCompleteness",
        "ExactEvidenceManifest",
        "AuthoritativeContextRef",
        "compute_completeness_digest",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 7
    for name in expected:
        assert hasattr(mod, name)


# ── CompletenessState (doc-13a:128-133) ────────────────────────────────────


_COMPLETENESS_STATE_MEMBERS = (
    "complete",
    "paged",
    "preview_only",
    "unavailable",
)


def test_completeness_state_has_doc_13a_members_exactly() -> None:
    """``CompletenessState`` mirrors the doc-13a:128-133 4-value enum verbatim."""

    assert get_args(CompletenessState) == _COMPLETENESS_STATE_MEMBERS
    assert len(_COMPLETENESS_STATE_MEMBERS) == 4


# ── EvidenceAuthority + namespace distinction (doc-13a:135-141) ─────────────


_EXECUTION_CONTROL_AUTHORITY_MEMBERS = (
    "execution_authority",
    "gate_authority",
    "routing_authority",
    "advisory",
    "display_only",
)


def test_evidence_authority_has_doc_13a_members_exactly() -> None:
    """``EvidenceAuthority`` mirrors the doc-13a:135-141 5-value taxonomy."""

    assert get_args(EvidenceAuthority) == _EXECUTION_CONTROL_AUTHORITY_MEMBERS
    assert len(_EXECUTION_CONTROL_AUTHORITY_MEMBERS) == 5


def test_evidence_authority_is_intentionally_distinct_from_governance() -> None:
    """The 5-value execution-control ``EvidenceAuthority`` is **INTENTIONALLY
    DISTINCT** from the 9-value ``governance.models.EvidenceAuthority``.

    Per doc-13a:135-141 the execution-control namespace defines a separate
    taxonomy (``execution_authority`` / ``gate_authority`` /
    ``routing_authority`` / ``advisory`` / ``display_only``); per
    doc-13:74-84 the governance namespace defines a separate taxonomy
    (``typed_journal`` / ``compatibility_projection`` / ``git_provenance`` /
    ``implementation_journal`` / ``implementation_decision_log`` /
    ``supervisor_digest`` / ``resource_snapshot`` / ``legacy_event`` /
    ``legacy_artifact_summary``). The two share the name by design --
    future Slice 13A sub-slices that wire compatibility adapters between
    the two namespaces MUST disambiguate via fully-qualified or
    namespace-aliased imports, NOT via name collision.
    """

    exec_members = set(get_args(EvidenceAuthority))
    gov_members = set(get_args(GovernanceEvidenceAuthority))
    # Cardinality is distinct (5 vs 9).
    assert len(exec_members) == 5
    assert len(gov_members) == 9
    # Membership sets are disjoint -- no value appears in both taxonomies.
    assert exec_members != gov_members
    assert exec_members.isdisjoint(gov_members)
    # And the two typed aliases are distinct Python objects.
    assert EvidenceAuthority is not GovernanceEvidenceAuthority


# ── EvidencePageRef (doc-13a:143-160) ───────────────────────────────────────


def _page_ref(**overrides: object) -> EvidencePageRef:
    """Construct a fully-specified :class:`EvidencePageRef` for tests."""

    base: dict[str, object] = dict(
        ref_id="page-ref-1",
        source_kind="typed_row",
        source_id=42,
        sha256="abc123",
        start=10,
        end=20,
        item_count=5,
        bytes=1024,
        reason="required-evidence-for-gate-X",
    )
    base.update(overrides)
    return EvidencePageRef(**base)


def test_evidence_page_ref_accepts_all_9_fields() -> None:
    """The 9 doc-13a:144-160 fields all populate cleanly."""

    page = _page_ref()
    assert page.ref_id == "page-ref-1"
    assert page.source_kind == "typed_row"
    assert page.source_id == 42
    assert page.sha256 == "abc123"
    assert page.start == 10
    assert page.end == 20
    assert page.item_count == 5
    assert page.bytes == 1024
    assert page.reason == "required-evidence-for-gate-X"


def test_evidence_page_ref_accepts_none_for_start_end_per_doc_13a_153_154() -> None:
    """Per doc-13a:153-154 ``start`` / ``end`` are Optional (``int | None = None``).

    A complete-in-single-page evidence may omit range markers.
    """

    page = _page_ref(start=None, end=None, item_count=None, bytes=None)
    assert page.start is None
    assert page.end is None
    assert page.item_count is None
    assert page.bytes is None
    # And the 5 required fields still validate.
    assert page.ref_id == "page-ref-1"
    assert page.source_kind == "typed_row"
    assert page.source_id == 42
    assert page.sha256 == "abc123"
    assert page.reason == "required-evidence-for-gate-X"


def test_evidence_page_ref_source_kind_literal_rejects_unknown_value() -> None:
    """``source_kind`` is a 7-value Literal; unknown values fail closed."""

    with pytest.raises(ValidationError):
        _page_ref(source_kind="invalid_source_kind")


@pytest.mark.parametrize(
    "valid_kind",
    [
        "typed_row",
        "artifact",
        "event",
        "file",
        "diff",
        "provider_record",
        "projection",
    ],
)
def test_evidence_page_ref_source_kind_literal_accepts_all_7_values(
    valid_kind: str,
) -> None:
    """Each of the 7 doc-13a:145-153 ``source_kind`` Literal values populates."""

    page = _page_ref(source_kind=valid_kind)
    assert page.source_kind == valid_kind


def test_evidence_page_ref_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _page_ref(unknown_field="oops")  # type: ignore[arg-type]


def test_evidence_page_ref_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    page = _page_ref()
    serialised = page.model_dump_json()
    restored = EvidencePageRef.model_validate_json(serialised)
    assert restored == page
    # And the round-trip payload itself is byte-identical.
    assert restored.model_dump_json() == serialised


def test_evidence_page_ref_round_trips_with_none_range_markers() -> None:
    """Round-trip is identity when ``start`` / ``end`` are None."""

    page = _page_ref(start=None, end=None, item_count=None, bytes=None)
    serialised = page.model_dump_json()
    restored = EvidencePageRef.model_validate_json(serialised)
    assert restored == page
    assert restored.start is None
    assert restored.end is None


# ── EvidenceCompleteness (doc-13a:162-170) ──────────────────────────────────


def _completeness(**overrides: object) -> EvidenceCompleteness:
    """Construct a fully-specified :class:`EvidenceCompleteness` for tests."""

    base: dict[str, object] = dict(
        state="complete",
        authority="execution_authority",
        complete_for=["scope-a", "scope-b"],
        missing_required_refs=[],
        page_refs=[_page_ref()],
        preview_ref=None,
        unavailable_reason=None,
        completeness_digest="digest-placeholder",
    )
    base.update(overrides)
    return EvidenceCompleteness(**base)


def test_evidence_completeness_accepts_all_8_fields() -> None:
    """The 8 doc-13a:163-170 fields all populate cleanly."""

    rec = _completeness()
    assert rec.state == "complete"
    assert rec.authority == "execution_authority"
    assert rec.complete_for == ["scope-a", "scope-b"]
    assert rec.missing_required_refs == []
    assert len(rec.page_refs) == 1
    assert rec.preview_ref is None
    assert rec.unavailable_reason is None
    assert rec.completeness_digest == "digest-placeholder"


def test_evidence_completeness_defaults_lists_to_empty() -> None:
    """``missing_required_refs`` + ``page_refs`` default to empty lists."""

    rec = EvidenceCompleteness(
        state="unavailable",
        authority="display_only",
        complete_for=[],
        completeness_digest="d",
    )
    assert rec.missing_required_refs == []
    assert rec.page_refs == []
    assert rec.preview_ref is None
    assert rec.unavailable_reason is None


def test_evidence_completeness_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _completeness(unknown_field="oops")  # type: ignore[arg-type]


def test_evidence_completeness_state_literal_rejects_unknown_value() -> None:
    """``state`` is a 4-value Literal; unknown values fail closed."""

    with pytest.raises(ValidationError):
        _completeness(state="not_a_state")


def test_evidence_completeness_authority_literal_rejects_unknown_value() -> None:
    """``authority`` is the 5-value execution-control Literal; unknown
    values fail closed.

    Critically, a 9-value governance authority value (e.g.
    ``typed_journal``) is NOT a valid execution-control authority.
    """

    with pytest.raises(ValidationError):
        _completeness(authority="not_an_authority")
    with pytest.raises(ValidationError):
        # ``typed_journal`` is a valid 9-value governance authority but
        # NOT a valid 5-value execution-control authority.
        _completeness(authority="typed_journal")


# ── ExactEvidenceManifest (doc-13a:172-184) ─────────────────────────────────


def _manifest(**overrides: object) -> ExactEvidenceManifest:
    """Construct a fully-specified :class:`ExactEvidenceManifest` for tests."""

    base: dict[str, object] = dict(
        manifest_id="manifest-1",
        manifest_digest="manifest-digest",
        feature_id="feature-abc",
        dag_sha256="dag-sha",
        group_idx=0,
        task_ids=["task-1", "task-2"],
        selection_scope=["scope-a"],
        completeness=_completeness(),
        required_page_refs=[_page_ref()],
        optional_page_refs=[],
        display_preview_ref=None,
        advisory_only=False,
    )
    base.update(overrides)
    return ExactEvidenceManifest(**base)


def test_exact_evidence_manifest_accepts_all_11_fields() -> None:
    """The 11 doc-13a:173-184 fields all populate cleanly."""

    m = _manifest()
    assert m.manifest_id == "manifest-1"
    assert m.manifest_digest == "manifest-digest"
    assert m.feature_id == "feature-abc"
    assert m.dag_sha256 == "dag-sha"
    assert m.group_idx == 0
    assert m.task_ids == ["task-1", "task-2"]
    assert m.selection_scope == ["scope-a"]
    assert isinstance(m.completeness, EvidenceCompleteness)
    assert len(m.required_page_refs) == 1
    assert m.optional_page_refs == []
    assert m.display_preview_ref is None
    assert m.advisory_only is False


def test_exact_evidence_manifest_group_idx_accepts_none() -> None:
    """Per doc-13a:177 ``group_idx`` is ``int | None`` (None for feature-
    level manifests that span all groups)."""

    m = _manifest(group_idx=None)
    assert m.group_idx is None


def test_exact_evidence_manifest_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _manifest(unknown_field="oops")  # type: ignore[arg-type]


def test_exact_evidence_manifest_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    m = _manifest()
    serialised = m.model_dump_json()
    restored = ExactEvidenceManifest.model_validate_json(serialised)
    assert restored == m


# ── AuthoritativeContextRef (doc-13a:186-192) ───────────────────────────────


def _context_ref(**overrides: object) -> AuthoritativeContextRef:
    """Construct a fully-specified :class:`AuthoritativeContextRef` for tests."""

    base: dict[str, object] = dict(
        manifest_id="manifest-1",
        manifest_digest="manifest-digest",
        completeness_digest="completeness-digest",
        required_complete_for=["scope-a"],
        authority="gate_authority",
    )
    base.update(overrides)
    return AuthoritativeContextRef(**base)


def test_authoritative_context_ref_accepts_all_5_fields() -> None:
    """The 5 doc-13a:187-191 fields all populate cleanly."""

    ref = _context_ref()
    assert ref.manifest_id == "manifest-1"
    assert ref.manifest_digest == "manifest-digest"
    assert ref.completeness_digest == "completeness-digest"
    assert ref.required_complete_for == ["scope-a"]
    assert ref.authority == "gate_authority"


def test_authoritative_context_ref_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _context_ref(unknown_field="oops")  # type: ignore[arg-type]


def test_authoritative_context_ref_authority_literal_rejects_unknown_value() -> None:
    """``authority`` is the 5-value execution-control Literal."""

    with pytest.raises(ValidationError):
        _context_ref(authority="not_an_authority")


# ── compute_completeness_digest (doc-13a:264) ───────────────────────────────


def _digest_inputs(
    *, state: str = "complete", authority: str = "execution_authority"
) -> dict[str, object]:
    """Build a canonical digest-input kwargs dict for tests."""

    return dict(
        state=state,
        authority=authority,
        complete_for=["scope-a", "scope-b"],
        missing_required_refs=[],
        page_refs=[_page_ref()],
        preview_ref=None,
        unavailable_reason=None,
    )


def test_compute_completeness_digest_is_deterministic_across_two_runs() -> None:
    """Two calls with the same logical input produce byte-identical
    SHA-256 hex digests.

    Per doc-13a:264 + doc-13:201-204 the canonical-JSON discipline
    (``json.dumps(..., sort_keys=True, separators=(",", ":"))`` then
    ``hashlib.sha256(...).hexdigest()``) guarantees byte-identical
    digests across processes / restarts / Python versions / platforms.
    """

    kwargs = _digest_inputs()
    digest_a = compute_completeness_digest(**kwargs)  # type: ignore[arg-type]
    digest_b = compute_completeness_digest(**kwargs)  # type: ignore[arg-type]
    assert digest_a == digest_b
    # And the digest is a 64-char SHA-256 hex string.
    assert len(digest_a) == 64
    int(digest_a, 16)  # raises if not valid hex


def test_compute_completeness_digest_distinguishes_different_state() -> None:
    """A different ``state`` value produces a different digest."""

    a = compute_completeness_digest(**_digest_inputs(state="complete"))  # type: ignore[arg-type]
    b = compute_completeness_digest(**_digest_inputs(state="paged"))  # type: ignore[arg-type]
    assert a != b


def test_compute_completeness_digest_distinguishes_different_authority() -> None:
    """A different ``authority`` value produces a different digest."""

    a = compute_completeness_digest(
        **_digest_inputs(authority="execution_authority")  # type: ignore[arg-type]
    )
    b = compute_completeness_digest(
        **_digest_inputs(authority="gate_authority")  # type: ignore[arg-type]
    )
    assert a != b


def test_compute_completeness_digest_is_sensitive_to_complete_for_order() -> None:
    """Per the helper docstring the digest IS sensitive to list element
    order. Producers that wish to achieve order-invariance MUST sort
    their lists canonically before calling this helper.

    Pins the contract so future re-orderings of producer code do not
    accidentally introduce silent digest equality.
    """

    a = compute_completeness_digest(
        state="complete",
        authority="execution_authority",
        complete_for=["scope-a", "scope-b"],
        missing_required_refs=[],
        page_refs=[],
        preview_ref=None,
        unavailable_reason=None,
    )
    b = compute_completeness_digest(
        state="complete",
        authority="execution_authority",
        complete_for=["scope-b", "scope-a"],  # reversed
        missing_required_refs=[],
        page_refs=[],
        preview_ref=None,
        unavailable_reason=None,
    )
    assert a != b


def test_compute_completeness_digest_includes_preview_ref_when_present() -> None:
    """The digest distinguishes a populated ``preview_ref`` from None."""

    kwargs_none = _digest_inputs()
    kwargs_with_preview = dict(kwargs_none)
    kwargs_with_preview["preview_ref"] = _page_ref(ref_id="preview-page")

    digest_none = compute_completeness_digest(**kwargs_none)  # type: ignore[arg-type]
    digest_with_preview = compute_completeness_digest(**kwargs_with_preview)  # type: ignore[arg-type]
    assert digest_none != digest_with_preview


def test_compute_completeness_digest_includes_unavailable_reason() -> None:
    """The digest distinguishes a populated ``unavailable_reason`` from None."""

    kwargs_none = _digest_inputs(state="unavailable")
    kwargs_with_reason = dict(kwargs_none)
    kwargs_with_reason["unavailable_reason"] = "required ref missing"

    digest_none = compute_completeness_digest(**kwargs_none)  # type: ignore[arg-type]
    digest_with_reason = compute_completeness_digest(**kwargs_with_reason)  # type: ignore[arg-type]
    assert digest_none != digest_with_reason
