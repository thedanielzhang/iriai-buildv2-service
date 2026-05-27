"""Slice 13A fifth sub-slice -- unit tests for the
``execution_control/gate_companion.py`` module.

Covers doc-13a § Refactoring Steps step 5 + step 6 co-bundled into one
sub-slice (doc-13a:273-278):

* :class:`AuthoritativeGateCompanionRecord` -- the 6-field Pydantic
  BaseModel projecting EvidenceCompleteness + AuthoritativeContextRef +
  gate scope id + gate input digest + approval routing + proof rows
  (per doc-13a:273-275).
* :class:`AuthoritativeGateProofRow` -- the 4-mandatory-field Pydantic
  BaseModel carrying source_digest + page_refs + proof_algorithm +
  verification_time (per doc-13a:276-278).
* :class:`AuthoritativeGateApprovalRouting` -- the typed routing
  decision per doc-13a:273-275 (should_approve_gate=True/False).
* :class:`AuthoritativeGateCompanionPort` Protocol +
  :class:`LegacyGateCompanionAdapter` concrete adapter.
* :func:`derive_gate_companion` + :func:`derive_proof_row` pure helpers.
* :class:`MissingGateCompanionFieldError` + :class:`MissingProofRowFieldError`
  typed exceptions (fail-closed; NOT silent degrade per
  ``feedback_no_silent_degradation``).

Test surface (co-bundled estimate 25-40 tests per implementer prompt
point 5):

Step 5 (gate companion record) tests:

* (a) Companion record produced from a complete
  :class:`AuthoritativePromptContextBundle`.
* (b) Companion record FAILS CLOSED on ``state="preview_only"`` (gate
  must NOT approve per doc-13a:273-275).
* (c) Companion record proceeds on ``state="complete"`` /
  ``state="paged"`` per doc-13a:273-275 ("either complete for the gate
  scope or exactly paged").
* (d) Missing required fields (gate_scope_id / gate_input_digest) ->
  typed :class:`MissingGateCompanionFieldError`.
* (e) Round-trip via ``model_dump_json`` -> ``model_validate_json``.
* (f) Namespace assertion -- the new module imports only from
  sanctioned in-package surfaces.

Step 6 (typed proof row) tests:

* (g) Proof row produced from all 4 mandatory fields.
* (h) Proof row FAILS CLOSED on missing source_digest / page_refs /
  proof_algorithm / verification_time per doc-13a:276-278.
* (i) Multiple missing fields -> ALL reported (per
  ``feedback_never_truncate_decisions``).

Wiring (legacy adapter) tests:

* (j) :class:`LegacyGateCompanionAdapter` implements the
  :class:`AuthoritativeGateCompanionPort` Protocol.
* (k) Legacy path byte-identical when the new opt-in port is unused
  (the adapter is external; the legacy
  :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunner`
  is byte-identical because this sub-slice does NOT edit
  ``gates.py``).
* (l) Typed failure ids
  ``verifier_context/companion_record_unavailable`` +
  ``verifier_context/proof_row_required`` are registered in the
  Slice 07 failure router.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    CompletenessState,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)
from iriai_build_v2.execution_control.gate_companion import (
    AuthoritativeGateApprovalRouting,
    AuthoritativeGateCompanionPort,
    AuthoritativeGateCompanionRecord,
    AuthoritativeGateProofRow,
    LegacyGateCompanionAdapter,
    MissingGateCompanionFieldError,
    MissingProofRowFieldError,
    derive_gate_companion,
    derive_proof_row,
)
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
    derive_authoritative_prompt_context_bundle,
)
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    PromptContextBundle,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    _DETERMINISTIC_FAILURE_TYPES,
    FailureType,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 9 documented public names.

    Per doc-13a:273-275 (step 5) the 4 step-5 surfaces are
    AuthoritativeGateCompanionRecord + AuthoritativeGateApprovalRouting +
    AuthoritativeGateCompanionPort + LegacyGateCompanionAdapter +
    derive_gate_companion. Per doc-13a:276-278 (step 6) the 2 step-6
    surfaces are AuthoritativeGateProofRow + derive_proof_row. Plus the
    2 fail-closed typed exceptions per
    ``feedback_no_silent_degradation``.
    """

    from iriai_build_v2.execution_control import gate_companion as mod

    expected = {
        # Step 5 typed shapes + helpers + port.
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateApprovalRouting",
        "AuthoritativeGateCompanionPort",
        "LegacyGateCompanionAdapter",
        "derive_gate_companion",
        # Step 6 typed shapes + helpers.
        "AuthoritativeGateProofRow",
        "derive_proof_row",
        # Fail-closed typed exceptions.
        "MissingGateCompanionFieldError",
        "MissingProofRowFieldError",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 9
    for name in expected:
        assert hasattr(mod, name)


# ── test fixtures ──────────────────────────────────────────────────────────


def _legacy_bundle(**overrides: Any) -> PromptContextBundle:
    """Construct a fully-resolved legacy Slice 05 :class:`PromptContextBundle`.

    Mirrors the third-sub-slice test fixture at
    ``tests/test_execution_control_prompt_context_adapter.py:110``.
    """

    base: dict[str, Any] = dict(
        prompt_ref=41,
        prompt_sha256="prompt-sha",
        prompt_summary="bounded prompt",
        context_file_refs=[42],
        context_file_paths=["context/TASK-1.md"],
        context_sha256="context-sha",
        included_contract_ids=[11],
        included_evidence_ids=[31],
        excluded_evidence_ids=[],
        truncation_notes=[],
    )
    base.update(overrides)
    return PromptContextBundle(**base)


def _bundle(
    legacy_bundle: PromptContextBundle | None = None,
    **derive_kwargs: Any,
) -> AuthoritativePromptContextBundle:
    """Derive an :class:`AuthoritativePromptContextBundle` via the
    third-sub-slice adapter from a legacy bundle.
    """

    legacy = legacy_bundle if legacy_bundle is not None else _legacy_bundle()
    base: dict[str, Any] = dict(
        manifest_id="manifest-1",
        manifest_digest="manifest-digest",
        feature_id="feature-abc",
        dag_sha256="dag-sha",
        task_id="TASK-1",
    )
    base.update(derive_kwargs)
    return derive_authoritative_prompt_context_bundle(legacy, **base)


def _bundle_for_gate(
    gate_scope_ids: str | list[str],
    legacy_bundle: PromptContextBundle | None = None,
    **derive_kwargs: Any,
) -> AuthoritativePromptContextBundle:
    """Return a complete bundle whose upstream evidence covers gate scopes."""

    bundle = _bundle(legacy_bundle, **derive_kwargs)
    scopes = (
        [gate_scope_ids]
        if isinstance(gate_scope_ids, str)
        else list(gate_scope_ids)
    )
    complete_for = list(
        dict.fromkeys([*bundle.completeness.complete_for, *scopes])
    )
    completeness_digest = compute_completeness_digest(
        state=bundle.completeness.state,
        authority=bundle.completeness.authority,
        complete_for=complete_for,
        missing_required_refs=list(bundle.completeness.missing_required_refs),
        page_refs=list(bundle.completeness.page_refs),
        preview_ref=bundle.completeness.preview_ref,
        unavailable_reason=bundle.completeness.unavailable_reason,
    )
    completeness = bundle.completeness.model_copy(
        update={
            "complete_for": complete_for,
            "completeness_digest": completeness_digest,
        }
    )
    required_complete_for = list(
        dict.fromkeys(
            [*bundle.context_manifest_ref.required_complete_for, *scopes]
        )
    )
    context_manifest_ref = bundle.context_manifest_ref.model_copy(
        update={
            "required_complete_for": required_complete_for,
            "completeness_digest": completeness_digest,
        }
    )
    return bundle.model_copy(
        update={
            "completeness": completeness,
            "context_manifest_ref": context_manifest_ref,
        }
    )


def _page_ref(**overrides: Any) -> EvidencePageRef:
    """Construct a fully-populated :class:`EvidencePageRef`."""

    base: dict[str, Any] = dict(
        ref_id="page-1",
        source_kind="typed_row",
        source_id=42,
        sha256="page-sha",
        start=0,
        end=100,
        item_count=10,
        bytes=2048,
        reason="required-evidence-for-gate-atomic_landing",
    )
    base.update(overrides)
    return EvidencePageRef(**base)


def _bundle_with_completeness(
    *,
    state: CompletenessState,
    complete_for: list[str],
    page_refs: list[EvidencePageRef] | None = None,
    missing_required_refs: list[EvidencePageRef] | None = None,
    required_complete_for: list[str] | None = None,
    unavailable_reason: str | None = None,
) -> AuthoritativePromptContextBundle:
    """Return a bundle with an explicit completeness/context-ref pair."""

    base_bundle = _bundle(
        _legacy_bundle(
            truncation_notes=(["paged gate evidence"] if state == "paged" else [])
        )
    )
    resolved_page_refs = list(page_refs or [])
    resolved_missing_refs = list(missing_required_refs or [])
    completeness_digest = compute_completeness_digest(
        state=state,
        authority="execution_authority",
        complete_for=list(complete_for),
        missing_required_refs=resolved_missing_refs,
        page_refs=resolved_page_refs,
        preview_ref=None,
        unavailable_reason=unavailable_reason,
    )
    completeness = EvidenceCompleteness(
        state=state,
        authority="execution_authority",
        complete_for=list(complete_for),
        missing_required_refs=resolved_missing_refs,
        page_refs=resolved_page_refs,
        preview_ref=None,
        unavailable_reason=unavailable_reason,
        completeness_digest=completeness_digest,
    )
    context_ref = AuthoritativeContextRef(
        manifest_id=base_bundle.context_manifest_ref.manifest_id,
        manifest_digest=base_bundle.context_manifest_ref.manifest_digest,
        completeness_digest=completeness_digest,
        required_complete_for=list(required_complete_for or complete_for),
        authority="execution_authority",
    )
    return base_bundle.model_copy(
        update={
            "completeness": completeness,
            "context_manifest_ref": context_ref,
        }
    )


# ════════════════════════════════════════════════════════════════════════════
# Step 5 tests (doc-13a:273-275) -- gate companion record
# ════════════════════════════════════════════════════════════════════════════


# ── (a) companion record produced from a complete bundle ──────────────────


def test_derive_gate_companion_from_complete_bundle() -> None:
    """A fully-resolved :class:`AuthoritativePromptContextBundle` with
    ``state="complete"`` produces a populated
    :class:`AuthoritativeGateCompanionRecord` with
    ``approval_routing.should_approve_gate=True``.

    Per doc-13a:273-275 the gate consumer may approve the gate when
    the companion record carries complete or paged evidence.
    """

    bundle = _bundle_for_gate("gate:atomic_landing")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:atomic_landing",
        gate_input_digest="input-digest-abc",
    )

    assert isinstance(record, AuthoritativeGateCompanionRecord)
    assert record.gate_scope_id == "gate:atomic_landing"
    assert record.gate_input_digest == "input-digest-abc"
    assert isinstance(record.completeness, EvidenceCompleteness)
    assert isinstance(record.context_manifest_ref, AuthoritativeContextRef)
    assert isinstance(record.approval_routing, AuthoritativeGateApprovalRouting)
    assert record.approval_routing.should_approve_gate is True
    assert record.approval_routing.typed_failure_class is None
    assert record.approval_routing.typed_failure_type is None
    assert record.approval_routing.missing_field_names == ()
    assert record.proof_rows == []


def test_derive_gate_companion_completeness_scoped_to_gate() -> None:
    """The companion record's completeness.complete_for is re-scoped
    to the gate (NOT the upstream bundle's task / feature scope).

    Per doc-13a:273-275 the gate companion record is per-gate-scope;
    the completeness covers the gate scope id only.
    """

    bundle = _bundle_for_gate("gate:code_review:g3")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:code_review:g3",
        gate_input_digest="input-digest",
    )
    assert record.completeness.complete_for == ["gate:code_review:g3"]
    assert (
        record.context_manifest_ref.required_complete_for == ["gate:code_review:g3"]
    )


def test_derive_gate_companion_authority_is_gate_authority() -> None:
    """The companion record's completeness.authority is
    ``"gate_authority"`` per doc-13a:135-141 (the 5-value execution-
    control authority taxonomy carries the gate-specific value).
    """

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert record.completeness.authority == "gate_authority"
    assert record.context_manifest_ref.authority == "gate_authority"


def test_derive_gate_companion_preserves_upstream_page_refs() -> None:
    """The companion record's completeness preserves the upstream
    bundle's page_refs verbatim. Per the third sub-slice adapter the
    bundle's page_refs is empty; we test the propagation path.
    """

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    # Third-sub-slice adapter emits empty page_refs by default; the
    # gate companion record mirrors that.
    assert record.completeness.page_refs == []
    assert record.completeness.missing_required_refs == []


# ── (b) companion record FAILS CLOSED on state="preview_only" ─────────────


def test_derive_gate_companion_fails_closed_on_preview_only_state() -> None:
    """Per doc-13a:273-275 a gate may NOT approve from preview_only
    evidence after 13A is enabled; the helper raises
    :class:`MissingGateCompanionFieldError` carrying the typed failure
    signal.
    """

    legacy = _legacy_bundle(context_file_refs=[])  # Triggers preview_only.
    bundle = _bundle(legacy)
    assert bundle.completeness.state == "preview_only"

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )
    assert "completeness.state" in exc_info.value.missing_field_names
    assert exc_info.value.gate_scope_id == "gate:scope"
    assert exc_info.value.unavailable_reason is not None
    assert "preview_only" in exc_info.value.unavailable_reason


def test_derive_gate_companion_fails_closed_on_unavailable_state() -> None:
    """Per doc-13a:303-310 the unavailable state must fail closed;
    the helper raises :class:`MissingGateCompanionFieldError`.

    We construct a synthetic AuthoritativePromptContextBundle with
    state="unavailable" by direct Pydantic construction since the
    third-sub-slice adapter only emits unavailable via raising.
    """

    page_ref = _page_ref()
    completeness_digest = compute_completeness_digest(
        state="unavailable",
        authority="display_only",
        complete_for=["task:T1"],
        missing_required_refs=[page_ref],
        page_refs=[],
        preview_ref=None,
        unavailable_reason="missing exact page",
    )
    unavailable_completeness = EvidenceCompleteness(
        state="unavailable",
        authority="display_only",
        complete_for=["task:T1"],
        missing_required_refs=[page_ref],
        page_refs=[],
        preview_ref=None,
        unavailable_reason="missing exact page",
        completeness_digest=completeness_digest,
    )
    context_ref = AuthoritativeContextRef(
        manifest_id="m-1",
        manifest_digest="m-digest",
        completeness_digest=completeness_digest,
        required_complete_for=["task:T1"],
        authority="display_only",
    )
    bundle = AuthoritativePromptContextBundle(
        prompt_ref=1,
        prompt_sha256="prompt-sha",
        display_prompt_summary="summary",
        context_manifest_ref=context_ref,
        context_file_refs=[],
        context_file_paths=[],
        context_sha256="ctx-sha",
        included_contract_ids=[],
        included_evidence_ids=[],
        excluded_evidence_ids=[],
        excluded_evidence_refs=[],
        completeness=unavailable_completeness,
        truncation_notes=[],
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )
    assert "completeness.state" in exc_info.value.missing_field_names
    assert exc_info.value.unavailable_reason == "missing exact page"


# ── (c) state="complete" / "paged" proceeds ────────────────────────────────


def test_derive_gate_companion_state_paged_proceeds() -> None:
    """Per doc-13a:273-275 "model verifier input is either complete
    for the gate scope or exactly paged" -- the helper allows the
    state="paged" path to proceed only when exact page refs and proof
    rows cover the gate scope.
    """

    page_ref = _page_ref(ref_id="gate-page-1")
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[page_ref],
    )
    assert bundle.completeness.state == "paged"
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )

    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
        proof_rows=[proof_row],
    )
    assert record.completeness.state == "paged"
    assert record.completeness.page_refs == [page_ref]
    assert record.approval_routing.should_approve_gate is True


def test_derive_gate_companion_paged_requires_exact_page_refs() -> None:
    """Paged gate evidence without exact page refs fails closed."""

    legacy = _legacy_bundle(
        truncation_notes=["budget exhausted; 2 of 5 pages dropped"]
    )
    bundle = _bundle(legacy)
    assert bundle.completeness.state == "paged"
    assert bundle.completeness.page_refs == []

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )

    assert "completeness.page_refs" in exc_info.value.missing_field_names
    assert "proof_rows" in exc_info.value.missing_field_names
    assert exc_info.value.gate_scope_id == "gate:scope"
    assert "exact page refs" in (exc_info.value.unavailable_reason or "")


def test_derive_gate_companion_paged_rejects_non_exact_page_refs() -> None:
    """Paged gate evidence rejects page refs without stable digests."""

    page_ref = _page_ref(ref_id="gate-page-1", sha256="")
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[page_ref],
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
            proof_rows=[proof_row],
        )

    assert exc_info.value.missing_field_names == (
        "completeness.page_refs",
        "proof_rows.page_refs",
    )
    assert "non-exact page refs" in (
        exc_info.value.unavailable_reason or ""
    )


def test_derive_gate_companion_paged_requires_proof_rows() -> None:
    """Paged gate evidence with page refs still needs typed proof rows."""

    page_ref = _page_ref(ref_id="gate-page-1")
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[page_ref],
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )

    assert exc_info.value.missing_field_names == ("proof_rows",)


def test_derive_gate_companion_paged_rejects_scope_mismatch() -> None:
    """Paged gate evidence must already cover the requested gate scope."""

    page_ref = _page_ref(ref_id="gate-page-1")
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:other"],
        page_refs=[page_ref],
        required_complete_for=["gate:other"],
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
            proof_rows=[proof_row],
        )

    assert set(exc_info.value.missing_field_names) == {
        "completeness.complete_for",
        "context_manifest_ref.required_complete_for",
    }
    assert "gate scope" in (exc_info.value.unavailable_reason or "")


def test_derive_gate_companion_paged_rejects_proof_row_page_ref_mismatch() -> None:
    """Paged proof rows must cover all exact page refs on completeness."""

    required_page_ref = _page_ref(ref_id="gate-page-1")
    other_page_ref = _page_ref(ref_id="gate-page-2", sha256="other-sha")
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[required_page_ref],
    )
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[other_page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
            proof_rows=[proof_row],
        )

    assert exc_info.value.missing_field_names == ("proof_rows.page_refs",)
    assert "cover all exact page refs" in (
        exc_info.value.unavailable_reason or ""
    )


def test_derive_gate_companion_paged_rejects_same_ref_id_digest_mismatch() -> None:
    """Proof rows must cover exact ref id plus digest, not id alone."""

    required_page_ref = _page_ref(ref_id="gate-page-1", sha256="required-sha")
    stale_page_ref = _page_ref(ref_id="gate-page-1", sha256="stale-sha")
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[required_page_ref],
    )
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[stale_page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
            proof_rows=[proof_row],
        )

    assert exc_info.value.missing_field_names == ("proof_rows.page_refs",)
    assert "cover all exact page refs" in (
        exc_info.value.unavailable_reason or ""
    )


def test_derive_gate_companion_state_complete_proceeds() -> None:
    """Per doc-13a:273-275 the state="complete" path proceeds (gate
    may approve)."""

    bundle = _bundle_for_gate("gate:scope")
    assert bundle.completeness.state == "complete"

    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert record.completeness.state == "complete"
    assert record.approval_routing.should_approve_gate is True


def test_derive_gate_companion_state_complete_rejects_scope_mismatch() -> None:
    """Complete gate evidence must already cover the requested scope."""

    bundle = _bundle_with_completeness(
        state="complete",
        complete_for=["gate:other"],
        required_complete_for=["gate:other"],
    )

    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )

    assert set(exc_info.value.missing_field_names) == {
        "completeness.complete_for",
        "context_manifest_ref.required_complete_for",
    }
    assert "gate scope" in (exc_info.value.unavailable_reason or "")


# ── (d) missing required fields -> typed exception ─────────────────────────


def test_derive_gate_companion_raises_on_empty_gate_scope_id() -> None:
    """An empty gate_scope_id is missing required identity; the helper
    raises :class:`MissingGateCompanionFieldError`."""

    bundle = _bundle_for_gate("gate:scope")
    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="",
            gate_input_digest="digest",
        )
    assert "gate_scope_id" in exc_info.value.missing_field_names


def test_derive_gate_companion_raises_on_whitespace_gate_scope_id() -> None:
    """A whitespace-only gate_scope_id is missing required identity."""

    bundle = _bundle_for_gate("gate:scope")
    with pytest.raises(MissingGateCompanionFieldError):
        derive_gate_companion(
            bundle,
            gate_scope_id="   ",
            gate_input_digest="digest",
        )


def test_derive_gate_companion_raises_on_empty_gate_input_digest() -> None:
    """An empty gate_input_digest is missing required content digest."""

    bundle = _bundle_for_gate("gate:scope")
    with pytest.raises(MissingGateCompanionFieldError) as exc_info:
        derive_gate_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="",
        )
    assert "gate_input_digest" in exc_info.value.missing_field_names


def test_missing_gate_companion_field_error_inherits_value_error() -> None:
    """:class:`MissingGateCompanionFieldError` inherits :class:`ValueError`
    so any caller that already catches :class:`ValueError` for
    malformed-input handling sees the failure (mirrors the sibling
    :class:`~iriai_build_v2.execution_control.prompt_context_adapter.MissingPromptContextFieldError`
    precedent).
    """

    bundle = _bundle_for_gate("gate:scope")
    with pytest.raises(ValueError):
        derive_gate_companion(
            bundle,
            gate_scope_id="",
            gate_input_digest="digest",
        )


# ── (e) round-trip via model_dump_json -> model_validate_json ──────────────


def test_authoritative_gate_companion_record_round_trips_via_json() -> None:
    """The typed record round-trips through JSON serialization without
    field loss.

    Critical for cross-process persistence: the gate companion record
    is persisted via the typed failure router; round-trip stability is
    the doc-13a:298-301 freshness contract.
    """

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    payload = record.model_dump_json()
    restored = AuthoritativeGateCompanionRecord.model_validate_json(payload)
    assert restored == record


# ── (f) namespace assertion ────────────────────────────────────────────────


def test_module_imports_only_from_sanctioned_in_package_surfaces() -> None:
    """The new module imports only from stdlib + Pydantic + the
    sanctioned ``execution_control`` surfaces. NO imports from
    ``governance/`` (the governance layer consumes execution-control
    surfaces, not the reverse). NO imports from
    ``workflows/develop/execution/`` (the legacy gate / verifier
    surfaces are wrapped externally, not imported as a dependency).
    """

    import ast
    import pathlib

    src = pathlib.Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2"
        "/execution_control/gate_companion.py"
    ).read_text()
    tree = ast.parse(src)

    seen_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            seen_modules.add(node.module)
        if isinstance(node, ast.Import):
            for alias in node.names:
                seen_modules.add(alias.name)

    allowed_prefixes = (
        "__future__",
        "typing",
        "pydantic",
        "iriai_build_v2.execution_control.completeness",
        "iriai_build_v2.execution_control.prompt_context_adapter",
    )
    for module in seen_modules:
        assert any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in allowed_prefixes
        ), f"module {module!r} not in allowed import set"


# ── Pydantic extra="forbid" tests ──────────────────────────────────────────


def test_authoritative_gate_companion_record_forbids_unknown_fields() -> None:
    """The Pydantic model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    payload = record.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeGateCompanionRecord.model_validate(payload)


def test_authoritative_gate_approval_routing_forbids_unknown_fields() -> None:
    """The Pydantic model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    routing = AuthoritativeGateApprovalRouting(
        should_approve_gate=True,
        typed_failure_class=None,
        typed_failure_type=None,
        unavailable_reason=None,
        missing_field_names=(),
    )
    payload = routing.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeGateApprovalRouting.model_validate(payload)


# ════════════════════════════════════════════════════════════════════════════
# Step 6 tests (doc-13a:276-278) -- typed proof row
# ════════════════════════════════════════════════════════════════════════════


# ── (g) proof row produced from all 4 mandatory fields ─────────────────────


def test_derive_proof_row_with_all_mandatory_fields() -> None:
    """A proof row with all 4 mandatory fields populated produces a
    populated :class:`AuthoritativeGateProofRow`."""

    page_ref = _page_ref()
    row = derive_proof_row(
        source_digest="source-sha256-abc",
        page_refs=[page_ref],
        proof_algorithm="sha256_concatenation",
        verification_time="2026-05-26T09:30:00Z",
    )

    assert isinstance(row, AuthoritativeGateProofRow)
    assert row.source_digest == "source-sha256-abc"
    assert row.page_refs == [page_ref]
    assert row.proof_algorithm == "sha256_concatenation"
    assert row.verification_time == "2026-05-26T09:30:00Z"
    assert row.summary_digest is None
    assert row.proof_metadata == {}


def test_derive_proof_row_with_optional_fields() -> None:
    """The optional summary_digest + proof_metadata fields populate
    verbatim."""

    page_ref = _page_ref()
    row = derive_proof_row(
        source_digest="source-sha256-abc",
        page_refs=[page_ref],
        proof_algorithm="merkle_root",
        verification_time="2026-05-26T09:30:00Z",
        summary_digest="summary-sha256-xyz",
        proof_metadata={"tree_depth": 4, "leaf_count": 16},
    )
    assert row.summary_digest == "summary-sha256-xyz"
    assert row.proof_metadata == {"tree_depth": 4, "leaf_count": 16}


def test_derive_proof_row_accepts_multiple_page_refs() -> None:
    """The proof row may carry multiple page refs for the paged
    evidence case."""

    page_refs = [
        _page_ref(ref_id=f"page-{i}", source_id=100 + i, sha256=f"p{i}-sha")
        for i in range(5)
    ]
    row = derive_proof_row(
        source_digest="source-sha",
        page_refs=page_refs,
        proof_algorithm="deterministic_aggregate_v1",
        verification_time="2026-05-26T09:30:00Z",
    )
    assert len(row.page_refs) == 5


# ── (h) proof row FAILS CLOSED on missing mandatory fields ─────────────────


def test_derive_proof_row_raises_on_empty_source_digest() -> None:
    """Per doc-13a:276-278 the source_digest is mandatory; empty value
    raises :class:`MissingProofRowFieldError`."""

    page_ref = _page_ref()
    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="",
            page_refs=[page_ref],
            proof_algorithm="sha256",
            verification_time="2026-05-26T09:30:00Z",
        )
    assert "source_digest" in exc_info.value.missing_field_names


def test_derive_proof_row_raises_on_whitespace_source_digest() -> None:
    """A whitespace-only source_digest is missing."""

    page_ref = _page_ref()
    with pytest.raises(MissingProofRowFieldError):
        derive_proof_row(
            source_digest="   ",
            page_refs=[page_ref],
            proof_algorithm="sha256",
            verification_time="2026-05-26T09:30:00Z",
        )


def test_derive_proof_row_raises_on_empty_page_refs() -> None:
    """Per doc-13a:276-278 the page_refs list is mandatory; empty list
    raises :class:`MissingProofRowFieldError` (a proof row without
    page refs cannot point at any source evidence)."""

    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="src-sha",
            page_refs=[],
            proof_algorithm="sha256",
            verification_time="2026-05-26T09:30:00Z",
        )
    assert "page_refs" in exc_info.value.missing_field_names


def test_derive_proof_row_raises_on_empty_proof_algorithm() -> None:
    """Per doc-13a:276-278 the proof_algorithm is mandatory."""

    page_ref = _page_ref()
    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="src-sha",
            page_refs=[page_ref],
            proof_algorithm="",
            verification_time="2026-05-26T09:30:00Z",
        )
    assert "proof_algorithm" in exc_info.value.missing_field_names


def test_derive_proof_row_raises_on_empty_verification_time() -> None:
    """Per doc-13a:276-278 the verification_time is mandatory."""

    page_ref = _page_ref()
    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="src-sha",
            page_refs=[page_ref],
            proof_algorithm="sha256",
            verification_time="",
        )
    assert "verification_time" in exc_info.value.missing_field_names


# ── (i) multiple missing fields -> ALL reported ────────────────────────────


def test_derive_proof_row_reports_all_missing_fields_at_once() -> None:
    """Per the auto-memory ``feedback_never_truncate_decisions`` rule:
    when multiple mandatory fields are missing, the typed exception
    reports ALL of them in a single raise (not one-by-one)."""

    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="",
            page_refs=[],
            proof_algorithm="",
            verification_time="",
        )
    missing = set(exc_info.value.missing_field_names)
    assert missing == {
        "source_digest",
        "page_refs",
        "proof_algorithm",
        "verification_time",
    }


def test_missing_proof_row_field_error_inherits_value_error() -> None:
    """:class:`MissingProofRowFieldError` inherits :class:`ValueError`
    per the sibling precedents (MissingPromptContextFieldError +
    MissingGateCompanionFieldError)."""

    with pytest.raises(ValueError):
        derive_proof_row(
            source_digest="",
            page_refs=[_page_ref()],
            proof_algorithm="sha256",
            verification_time="2026-05-26T09:30:00Z",
        )


def test_missing_proof_row_field_error_carries_summary_digest() -> None:
    """The typed exception carries the optional summary_digest for
    cross-process observability."""

    with pytest.raises(MissingProofRowFieldError) as exc_info:
        derive_proof_row(
            source_digest="",
            page_refs=[_page_ref()],
            proof_algorithm="sha256",
            verification_time="2026-05-26T09:30:00Z",
            summary_digest="summary-sha-xyz",
        )
    assert exc_info.value.summary_digest == "summary-sha-xyz"


# ── proof row Pydantic extra="forbid" ──────────────────────────────────────


def test_authoritative_gate_proof_row_forbids_unknown_fields() -> None:
    """The Pydantic model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    row = derive_proof_row(
        source_digest="src-sha",
        page_refs=[_page_ref()],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )
    payload = row.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeGateProofRow.model_validate(payload)


def test_authoritative_gate_proof_row_round_trips_via_json() -> None:
    """The proof row round-trips through JSON serialization."""

    row = derive_proof_row(
        source_digest="src-sha",
        page_refs=[_page_ref()],
        proof_algorithm="merkle_root",
        verification_time="2026-05-26T09:30:00Z",
        summary_digest="summary-sha",
        proof_metadata={"depth": 3},
    )
    payload = row.model_dump_json()
    restored = AuthoritativeGateProofRow.model_validate_json(payload)
    assert restored == row


# ── companion record carries proof rows ────────────────────────────────────


def test_derive_gate_companion_carries_proof_rows() -> None:
    """The companion record carries the supplied proof rows verbatim
    per doc-13a:276-278.
    """

    bundle = _bundle_for_gate("gate:scope")
    proof_row = derive_proof_row(
        source_digest="src-sha",
        page_refs=[_page_ref()],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
        proof_rows=[proof_row],
    )
    assert record.proof_rows == [proof_row]


def test_derive_gate_companion_default_proof_rows_is_empty() -> None:
    """When no proof_rows argument is supplied, the companion record's
    proof_rows is an empty list for complete gate evidence.

    Per doc-13a:276-278 proof rows are optional for complete gate
    evidence; paged gate evidence is covered separately by the
    exact-page-ref proof-row tests above.
    """

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert record.proof_rows == []


# ════════════════════════════════════════════════════════════════════════════
# Wiring (legacy adapter) tests
# ════════════════════════════════════════════════════════════════════════════


# ── (j) LegacyGateCompanionAdapter implements the port Protocol ────────────


def test_legacy_gate_companion_adapter_implements_port_protocol() -> None:
    """The :class:`LegacyGateCompanionAdapter` is a concrete
    implementation of the :class:`AuthoritativeGateCompanionPort`
    Protocol.

    The Protocol carries a single method ``derive_companion``; the
    adapter's method signature matches the Protocol's exactly.
    """

    adapter = LegacyGateCompanionAdapter()
    # Duck-typing check -- the adapter has the Protocol method.
    assert hasattr(adapter, "derive_companion")
    assert callable(adapter.derive_companion)


def test_legacy_gate_companion_adapter_derives_companion_record() -> None:
    """The :class:`LegacyGateCompanionAdapter` delegates to
    :func:`derive_gate_companion`.
    """

    bundle = _bundle_for_gate("gate:scope")
    adapter = LegacyGateCompanionAdapter()
    record = adapter.derive_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert isinstance(record, AuthoritativeGateCompanionRecord)
    assert record.gate_scope_id == "gate:scope"


def test_legacy_gate_companion_adapter_fails_closed_on_preview_only() -> None:
    """The adapter inherits the fail-closed contract from
    :func:`derive_gate_companion`."""

    legacy = _legacy_bundle(context_file_refs=[])
    bundle = _bundle(legacy)
    adapter = LegacyGateCompanionAdapter()
    with pytest.raises(MissingGateCompanionFieldError):
        adapter.derive_companion(
            bundle,
            gate_scope_id="gate:scope",
            gate_input_digest="digest",
        )


# ── (l) Typed failure ids registered in Slice 07 router ────────────────────


def test_companion_record_unavailable_failure_type_registered() -> None:
    """The Slice 13A fifth sub-slice adds
    ``verifier_context/companion_record_unavailable`` to the typed
    failure router per chunk shape point 2 + doc-13a:273-275.
    """

    assert "companion_record_unavailable" in FAILURE_TYPES
    assert ("verifier_context", "companion_record_unavailable") in ROUTE_TABLE
    route = ROUTE_TABLE[("verifier_context", "companion_record_unavailable")]
    assert route.action == "quiesce"
    assert "preview_only" in route.reason
    assert "companion_record_unavailable" in _DETERMINISTIC_FAILURE_TYPES


def test_proof_row_required_failure_type_registered() -> None:
    """The Slice 13A fifth sub-slice adds
    ``verifier_context/proof_row_required`` to the typed failure
    router per chunk shape point 2 + doc-13a:276-278.
    """

    assert "proof_row_required" in FAILURE_TYPES
    assert ("verifier_context", "proof_row_required") in ROUTE_TABLE
    route = ROUTE_TABLE[("verifier_context", "proof_row_required")]
    assert route.action == "quiesce"
    assert "proof row" in route.reason.lower()
    assert "proof_row_required" in _DETERMINISTIC_FAILURE_TYPES


def test_typed_failure_class_for_new_ids_is_verifier_context() -> None:
    """Both new typed failure ids belong to the ``verifier_context``
    failure class (per the gate-side authority taxonomy)."""

    for failure_type in ("companion_record_unavailable", "proof_row_required"):
        # FailureType Literal contains both
        assert failure_type in FAILURE_TYPES
        # Route table maps both under verifier_context
        assert ("verifier_context", failure_type) in ROUTE_TABLE


def test_new_typed_failure_ids_route_to_quiesce() -> None:
    """Both new typed failure ids route to ``quiesce`` per the
    fail-closed rule (doc-13a:273-275 + doc-13a:276-278 +
    auto-memory feedback_no_silent_degradation)."""

    for failure_type in ("companion_record_unavailable", "proof_row_required"):
        route = ROUTE_TABLE[("verifier_context", failure_type)]
        assert route.action == "quiesce"


# ── routing carries typed failure id ───────────────────────────────────────


def test_companion_record_routing_should_approve_gate_on_complete() -> None:
    """On state="complete", the approval_routing carries
    should_approve_gate=True with no typed failure id."""

    bundle = _bundle_for_gate("gate:scope")
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    routing = record.approval_routing
    assert routing.should_approve_gate is True
    assert routing.typed_failure_class is None
    assert routing.typed_failure_type is None
    assert routing.unavailable_reason is None
    assert routing.missing_field_names == ()


def test_companion_record_routing_should_approve_gate_on_paged() -> None:
    """On state="paged", the approval_routing carries
    should_approve_gate=True when exact page refs and proof rows cover
    the gate scope per doc-13a:273-275."""

    page_ref = _page_ref(ref_id="gate-page-1")
    bundle = _bundle_with_completeness(
        state="paged",
        complete_for=["gate:scope"],
        page_refs=[page_ref],
    )
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[page_ref],
        proof_algorithm="sha256",
        verification_time="2026-05-26T09:30:00Z",
    )
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
        proof_rows=[proof_row],
    )
    assert record.approval_routing.should_approve_gate is True


# ── completeness digest deterministic ──────────────────────────────────────


def test_derive_gate_companion_completeness_digest_is_deterministic() -> None:
    """Two adapter calls with byte-identical inputs produce
    byte-identical completeness_digest values per the doc-13a:298-301
    freshness contract."""

    bundle1 = _bundle_for_gate("gate:scope")
    bundle2 = _bundle_for_gate("gate:scope")
    record1 = derive_gate_companion(
        bundle1,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    record2 = derive_gate_companion(
        bundle2,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert (
        record1.completeness.completeness_digest
        == record2.completeness.completeness_digest
    )
    assert (
        record1.context_manifest_ref.completeness_digest
        == record2.context_manifest_ref.completeness_digest
    )


def test_derive_gate_companion_different_scopes_yield_different_digests() -> None:
    """Two adapter calls with different gate_scope_id values produce
    different completeness_digest values (the scope is part of the
    digest material via complete_for)."""

    bundle = _bundle_for_gate(["gate:atomic_landing", "gate:code_review"])
    record_atomic = derive_gate_companion(
        bundle,
        gate_scope_id="gate:atomic_landing",
        gate_input_digest="digest",
    )
    record_review = derive_gate_companion(
        bundle,
        gate_scope_id="gate:code_review",
        gate_input_digest="digest",
    )
    assert (
        record_atomic.completeness.completeness_digest
        != record_review.completeness.completeness_digest
    )


# ── manifest identity carries through ──────────────────────────────────────


def test_derive_gate_companion_preserves_upstream_manifest_identity() -> None:
    """The companion record's context_manifest_ref preserves the
    upstream bundle's manifest_id + manifest_digest verbatim (only
    the completeness_digest + required_complete_for + authority
    change per the gate-scope re-projection)."""

    bundle = _bundle_for_gate(
        "gate:scope", manifest_id="m-abc", manifest_digest="m-digest-xyz"
    )
    record = derive_gate_companion(
        bundle,
        gate_scope_id="gate:scope",
        gate_input_digest="digest",
    )
    assert record.context_manifest_ref.manifest_id == "m-abc"
    assert record.context_manifest_ref.manifest_digest == "m-digest-xyz"
    # The completeness_digest is RE-SCOPED (different from upstream).
    assert (
        record.context_manifest_ref.completeness_digest
        != bundle.context_manifest_ref.completeness_digest
    )
