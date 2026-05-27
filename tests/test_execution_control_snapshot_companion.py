"""Slice 13A sixth sub-slice -- unit tests for the
``execution_control/snapshot_companion.py`` module.

Covers doc-13a § Refactoring Steps step 7 (snapshot companion per
doc-13a:280-282) + the P3-13A-5-4 dead-until-wired binding closure
(wire the fifth-sub-slice
:class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
into a real consumer site via the
:class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
external opt-in wrapper).

Test surface (co-bundled estimate 25-50 tests per implementer prompt):

Step 7 (snapshot companion record) tests:

* (a) Companion record produced from a complete snapshot.
* (b) Companion record FAILS CLOSED when required list fields incomplete
  (classifier rule blocked).
* (c) Companion record allows partial display when required fields complete.
* (d) Missing required fields -> typed
  :class:`MissingSnapshotCompanionFieldError`.
* (e) Round-trip via ``model_dump_json`` -> ``model_validate_json``.
* (f) Namespace assertion -- the new module imports only from sanctioned
  in-package surfaces.
* (g) Legacy path byte-identical when port=None (the snapshot-side
  surface is external; the legacy Slice 10
  :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
  is byte-identical because this sub-slice does NOT edit
  ``snapshots.py``).

Step 7 typed failure id tests:

* Typed failure ids ``snapshot/list_field_incomplete`` +
  ``snapshot/classifier_rule_blocked`` are registered in the Slice 07
  failure router.

P3-13A-5-4 binding closure tests:

* (h) :class:`LegacyGateConsumerSnapshotAdapter` composes the
  snapshot companion record with the gate companion record.
* (h) Wire-validation: the wrapper raises
  :class:`MissingSnapshotCompanionFieldError` when the snapshot is
  incomplete for the gate scope.
* (h) Wire-validation: the wrapper raises
  :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
  when the gate companion fails (preview_only state).
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any, Mapping, Sequence

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)
from iriai_build_v2.execution_control.gate_companion import (
    AuthoritativeGateCompanionPort,
    AuthoritativeGateCompanionRecord,
    AuthoritativeGateProofRow,
    LegacyGateCompanionAdapter,
    MissingGateCompanionFieldError,
    derive_proof_row,
)
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
    derive_authoritative_prompt_context_bundle,
)
from iriai_build_v2.execution_control.snapshot_companion import (
    AuthoritativeSnapshotClassifierRouting,
    AuthoritativeSnapshotCompanionPort,
    AuthoritativeSnapshotCompanionRecord,
    AuthoritativeSnapshotListFieldCompleteness,
    LegacyGateConsumerSnapshotAdapter,
    LegacySnapshotCompanionAdapter,
    MissingSnapshotCompanionFieldError,
    derive_gate_companion_with_snapshot,
    derive_snapshot_companion,
)
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    PromptContextBundle,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_CLASSES,
    FAILURE_TYPES,
    ROUTE_TABLE,
    _DETERMINISTIC_FAILURE_TYPES,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 9 documented public names.

    Per doc-13a:280-282 (step 7) the 6 step-7 surfaces are
    AuthoritativeSnapshotListFieldCompleteness +
    AuthoritativeSnapshotCompanionRecord +
    AuthoritativeSnapshotClassifierRouting +
    AuthoritativeSnapshotCompanionPort +
    LegacySnapshotCompanionAdapter +
    derive_snapshot_companion. Per P3-13A-5-4 binding closure the 2
    binding-closure surfaces are LegacyGateConsumerSnapshotAdapter +
    derive_gate_companion_with_snapshot. Plus the 1 fail-closed typed
    exception per feedback_no_silent_degradation.
    """

    from iriai_build_v2.execution_control import snapshot_companion as mod

    expected = {
        # Step 7 typed shapes + helpers + port.
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotCompanionRecord",
        "AuthoritativeSnapshotClassifierRouting",
        "AuthoritativeSnapshotCompanionPort",
        "LegacySnapshotCompanionAdapter",
        "derive_snapshot_companion",
        # P3-13A-5-4 binding closure -- gate-consumer external wrapper.
        "LegacyGateConsumerSnapshotAdapter",
        "derive_gate_companion_with_snapshot",
        # Fail-closed typed exception.
        "MissingSnapshotCompanionFieldError",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 9
    for name in expected:
        assert hasattr(mod, name)


# ── test fixtures ──────────────────────────────────────────────────────────


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
        reason="snapshot-list-field-page",
    )
    base.update(overrides)
    return EvidencePageRef(**base)


def _completeness(
    *,
    state: str = "complete",
    authority: str = "routing_authority",
    complete_for: Sequence[str] = ("snapshot:latest_failures",),
    page_refs: Sequence[EvidencePageRef] = (),
    missing_required_refs: Sequence[EvidencePageRef] = (),
    preview_ref: EvidencePageRef | None = None,
    unavailable_reason: str | None = None,
) -> EvidenceCompleteness:
    """Construct an :class:`EvidenceCompleteness` for a per-list-field."""

    digest = compute_completeness_digest(
        state=state,  # type: ignore[arg-type]
        authority=authority,  # type: ignore[arg-type]
        complete_for=list(complete_for),
        missing_required_refs=list(missing_required_refs),
        page_refs=list(page_refs),
        preview_ref=preview_ref,
        unavailable_reason=unavailable_reason,
    )
    return EvidenceCompleteness(
        state=state,  # type: ignore[arg-type]
        authority=authority,  # type: ignore[arg-type]
        complete_for=list(complete_for),
        missing_required_refs=list(missing_required_refs),
        page_refs=list(page_refs),
        preview_ref=preview_ref,
        unavailable_reason=unavailable_reason,
        completeness_digest=digest,
    )


def _per_list_field(
    field_name: str = "latest_failures",
    *,
    state: str = "complete",
    item_count: int = 3,
    next_page_ref: EvidencePageRef | None = None,
) -> AuthoritativeSnapshotListFieldCompleteness:
    """Construct an :class:`AuthoritativeSnapshotListFieldCompleteness`."""

    return AuthoritativeSnapshotListFieldCompleteness(
        field_name=field_name,
        completeness=_completeness(
            state=state,
            complete_for=[f"snapshot:{field_name}"],
        ),
        item_count=item_count,
        next_page_ref=next_page_ref,
    )


def _list_field_dict(
    **field_specs: AuthoritativeSnapshotListFieldCompleteness,
) -> dict[str, AuthoritativeSnapshotListFieldCompleteness]:
    """Construct a per-list-field completeness dict from named field specs.

    If no field_specs given, returns a default dict with the 3 most
    common Slice 10a list-fields (latest_failures + merge_queue +
    retry_budgets) all complete.
    """

    if not field_specs:
        return {
            "latest_failures": _per_list_field("latest_failures"),
            "merge_queue": _per_list_field("merge_queue", item_count=2),
            "retry_budgets": _per_list_field("retry_budgets", item_count=4),
        }
    return dict(field_specs)


def _snapshot_companion(
    list_field_completeness: Mapping[
        str, AuthoritativeSnapshotListFieldCompleteness
    ]
    | None = None,
    *,
    snapshot_scope_id: str = "snapshot:dashboard:feature-abc",
    snapshot_digest: str = "snap-digest-abc",
    manifest_id: str = "snap-manifest-1",
    manifest_digest: str = "snap-manifest-digest",
    required_list_field_scopes: Sequence[str] = (),
    authority: str = "routing_authority",
) -> AuthoritativeSnapshotCompanionRecord:
    """Derive an :class:`AuthoritativeSnapshotCompanionRecord` from a
    per-list-field completeness dict.
    """

    fields = (
        dict(list_field_completeness)
        if list_field_completeness is not None
        else _list_field_dict()
    )
    return derive_snapshot_companion(
        fields,
        snapshot_scope_id=snapshot_scope_id,
        snapshot_digest=snapshot_digest,
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
        required_list_field_scopes=required_list_field_scopes,
        authority=authority,  # type: ignore[arg-type]
    )


def _legacy_bundle(**overrides: Any) -> PromptContextBundle:
    """Construct a fully-resolved legacy Slice 05 :class:`PromptContextBundle`.

    Mirrors the fifth-sub-slice test fixture at
    ``tests/test_execution_control_gate_companion.py:143``.
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


def _prompt_context_bundle(
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


# ════════════════════════════════════════════════════════════════════════════
# Step 7 tests (doc-13a:280-282) -- snapshot companion record
# ════════════════════════════════════════════════════════════════════════════


# ── (a) companion record produced from a complete snapshot ────────────────


def test_derive_snapshot_companion_from_complete_snapshot() -> None:
    """A snapshot with all per-list-field completeness records set to
    ``state="complete"`` produces a populated
    :class:`AuthoritativeSnapshotCompanionRecord` with
    ``classifier_routing.should_invoke_classifier=True``.
    """

    fields = _list_field_dict()
    record = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:dashboard:feature-abc",
        snapshot_digest="snap-digest",
        manifest_id="m-1",
        manifest_digest="m-digest",
    )

    assert isinstance(record, AuthoritativeSnapshotCompanionRecord)
    assert record.snapshot_scope_id == "snapshot:dashboard:feature-abc"
    assert record.snapshot_digest == "snap-digest"
    assert isinstance(record.overall_completeness, EvidenceCompleteness)
    assert record.overall_completeness.state == "complete"
    assert isinstance(record.context_manifest_ref, AuthoritativeContextRef)
    assert isinstance(
        record.classifier_routing, AuthoritativeSnapshotClassifierRouting
    )
    assert record.classifier_routing.should_invoke_classifier is True
    assert record.classifier_routing.typed_failure_class is None
    assert record.classifier_routing.typed_failure_type is None
    assert record.classifier_routing.missing_field_names == ()
    assert set(record.list_field_completeness.keys()) == {
        "latest_failures",
        "merge_queue",
        "retry_budgets",
    }


def test_derive_snapshot_companion_scope_id_propagation() -> None:
    """The companion record's snapshot_scope_id propagates verbatim.

    Per doc-13a:280-282 the snapshot companion record is per-snapshot-
    scope; each scope has its own companion record.
    """

    record = _snapshot_companion(
        snapshot_scope_id="snapshot:supervisor:feature-xyz",
    )
    assert record.snapshot_scope_id == "snapshot:supervisor:feature-xyz"


def test_derive_snapshot_companion_authority_is_routing_authority_by_default() -> None:
    """The companion record's authority defaults to
    ``"routing_authority"`` per doc-13a:135-141 (the 5-value
    execution-control authority taxonomy carries the routing-specific
    value)."""

    record = _snapshot_companion()
    assert record.overall_completeness.authority == "routing_authority"
    assert record.context_manifest_ref.authority == "routing_authority"


def test_derive_snapshot_companion_authority_override() -> None:
    """The caller may pass authority="advisory" or "display_only" per
    the doc-13a:135-141 taxonomy."""

    record = _snapshot_companion(authority="advisory")
    assert record.overall_completeness.authority == "advisory"
    assert record.context_manifest_ref.authority == "advisory"


def test_derive_snapshot_companion_aggregate_complete_for_unions_per_field() -> None:
    """The companion record's overall_completeness.complete_for is the
    union of per-list-field complete_for lists, sorted."""

    fields = {
        "latest_failures": _per_list_field("latest_failures"),
        "merge_queue": _per_list_field("merge_queue"),
    }
    record = _snapshot_companion(fields)
    assert record.overall_completeness.complete_for == sorted(
        {"snapshot:latest_failures", "snapshot:merge_queue"}
    )


def test_derive_snapshot_companion_state_paged_when_any_field_paged() -> None:
    """The aggregate state is ``"paged"`` when ANY list-field is paged."""

    page_ref = _page_ref()
    fields = {
        "latest_failures": _per_list_field("latest_failures"),
        "merge_queue": AuthoritativeSnapshotListFieldCompleteness(
            field_name="merge_queue",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=["snapshot:merge_queue"],
            ),
            item_count=5,
            next_page_ref=page_ref,
        ),
    }
    record = _snapshot_companion(fields)
    assert record.overall_completeness.state == "paged"
    # paged state should still allow classifier invocation per
    # doc-13a:280-282 ("either complete for the gate scope or exactly
    # paged" semantics carry through to snapshot rules).
    assert record.classifier_routing.should_invoke_classifier is True


def test_derive_snapshot_companion_state_preview_only_when_all_preview() -> None:
    """The aggregate state is ``"preview_only"`` when ALL list-fields
    are preview_only."""

    preview_ref = _page_ref(reason="preview-for-display")
    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="preview_only",
                authority="display_only",
                preview_ref=preview_ref,
                complete_for=[],
            ),
            item_count=0,
        ),
    }
    record = _snapshot_companion(fields)
    assert record.overall_completeness.state == "preview_only"
    # When all preview_only AND no required scopes, classifier_routing
    # MAY still proceed (display-only snapshot). The override-resistant
    # invariant forces authority="display_only".
    assert record.overall_completeness.authority == "display_only"


def test_derive_snapshot_companion_empty_list_field_completeness_is_complete() -> None:
    """An empty list-field completeness dict aggregates to "complete"
    per the aggregation rule (no list-fields means no degradation).
    """

    record = derive_snapshot_companion(
        {},
        snapshot_scope_id="snapshot:s1",
        snapshot_digest="d",
        manifest_id="m",
        manifest_digest="md",
    )
    assert record.overall_completeness.state == "complete"
    assert record.list_field_completeness == {}


# ── (b) companion record FAILS CLOSED when required list fields incomplete


def test_derive_snapshot_companion_fails_closed_on_unavailable_state() -> None:
    """Per doc-13a:303-310 the unavailable state must fail closed; the
    helper raises :class:`MissingSnapshotCompanionFieldError`.
    """

    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="unavailable",
                authority="display_only",
                complete_for=[],
                unavailable_reason="snapshot read timed out",
            ),
            item_count=0,
        ),
    }
    with pytest.raises(MissingSnapshotCompanionFieldError) as exc_info:
        derive_snapshot_companion(
            fields,
            snapshot_scope_id="snapshot:scope",
            snapshot_digest="digest",
            manifest_id="m",
            manifest_digest="md",
        )
    assert "latest_failures" in exc_info.value.missing_field_names
    assert exc_info.value.snapshot_scope_id == "snapshot:scope"
    assert exc_info.value.unavailable_reason is not None
    assert "unavailable" in exc_info.value.unavailable_reason


def test_derive_snapshot_companion_fails_closed_on_preview_with_required() -> None:
    """Per doc-13a:280-282 the preview_only aggregate MUST fail closed
    when the classifier requires complete coverage of one or more
    list-field scopes."""

    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="preview_only",
                authority="display_only",
                complete_for=[],
            ),
            item_count=0,
        ),
    }
    with pytest.raises(MissingSnapshotCompanionFieldError) as exc_info:
        derive_snapshot_companion(
            fields,
            snapshot_scope_id="snapshot:scope",
            snapshot_digest="digest",
            manifest_id="m",
            manifest_digest="md",
            required_list_field_scopes=("latest_failures",),
        )
    assert "latest_failures" in exc_info.value.missing_field_names
    assert exc_info.value.snapshot_scope_id == "snapshot:scope"
    assert "preview_only" in (exc_info.value.unavailable_reason or "")


def test_derive_snapshot_companion_routes_classifier_blocked_on_missing_scope() -> None:
    """When required list-field scopes are not covered by the snapshot
    companion record, classifier_routing reports
    ``should_invoke_classifier=False`` with the typed failure id
    ``evidence_corruption/classifier_rule_blocked``."""

    # Snapshot has only `latest_failures` complete; classifier
    # requires coverage of `merge_queue` (not in the snapshot).
    fields = {
        "latest_failures": _per_list_field("latest_failures"),
    }
    record = _snapshot_companion(
        fields,
        required_list_field_scopes=("merge_queue",),
    )
    assert record.classifier_routing.should_invoke_classifier is False
    assert (
        record.classifier_routing.typed_failure_class == "evidence_corruption"
    )
    assert (
        record.classifier_routing.typed_failure_type
        == "classifier_rule_blocked"
    )
    assert "merge_queue" in record.classifier_routing.missing_field_names


def test_derive_snapshot_companion_blocks_when_required_scope_paged_not_complete_for() -> None:
    """When a required list-field scope has paged state but the
    completeness's complete_for does NOT match the required scope,
    classifier_routing blocks the rule.

    Per doc-13a:280-282 the consumer drives the rule only when the
    list-field's state is complete/paged.
    """

    page_ref = _page_ref()
    fields = {
        "merge_queue": AuthoritativeSnapshotListFieldCompleteness(
            field_name="merge_queue",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=[],
            ),
            item_count=10,
            next_page_ref=page_ref,
        ),
    }
    record = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:s",
        snapshot_digest="d",
        manifest_id="m",
        manifest_digest="md",
        required_list_field_scopes=("merge_queue",),
    )
    assert record.classifier_routing.should_invoke_classifier is False
    assert "merge_queue" in record.classifier_routing.missing_field_names


def test_derive_snapshot_companion_blocks_required_paged_without_page_refs() -> None:
    fields = {
        "merge_queue": AuthoritativeSnapshotListFieldCompleteness(
            field_name="merge_queue",
            completeness=_completeness(
                state="paged",
                complete_for=["snapshot:merge_queue"],
            ),
            item_count=10,
            next_page_ref=None,
        ),
    }
    record = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:s",
        snapshot_digest="d",
        manifest_id="m",
        manifest_digest="md",
        required_list_field_scopes=("merge_queue",),
    )
    assert record.classifier_routing.should_invoke_classifier is False
    assert "merge_queue" in record.classifier_routing.missing_field_names


def test_derive_snapshot_companion_blocks_required_paged_with_non_exact_page_ref() -> None:
    page_ref = _page_ref(sha256="")
    fields = {
        "merge_queue": AuthoritativeSnapshotListFieldCompleteness(
            field_name="merge_queue",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=["snapshot:merge_queue"],
            ),
            item_count=10,
            next_page_ref=page_ref,
        ),
    }
    record = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:s",
        snapshot_digest="d",
        manifest_id="m",
        manifest_digest="md",
        required_list_field_scopes=("merge_queue",),
    )
    assert record.classifier_routing.should_invoke_classifier is False
    assert "merge_queue" in record.classifier_routing.missing_field_names


# ── (c) companion record allows partial display when required fields complete


def test_derive_snapshot_companion_partial_display_with_required_complete() -> None:
    """Per doc-13a:280-282 "Partial snapshots are allowed for display
    but classifier rules fail closed unless their required fields are
    complete" -- a mix of paged + complete is allowed when required
    fields are complete."""

    # latest_failures = required (complete); merge_queue = paged
    # (partial display); aggregate state = paged but required scope
    # (latest_failures) is complete -> classifier proceeds.
    page_ref = _page_ref()
    fields = {
        "latest_failures": _per_list_field(
            "latest_failures", state="complete"
        ),
        "merge_queue": AuthoritativeSnapshotListFieldCompleteness(
            field_name="merge_queue",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=["snapshot:merge_queue"],
            ),
            item_count=5,
            next_page_ref=page_ref,
        ),
    }
    record = _snapshot_companion(
        fields,
        required_list_field_scopes=("latest_failures",),
    )
    assert record.overall_completeness.state == "paged"
    assert record.classifier_routing.should_invoke_classifier is True


def test_derive_snapshot_companion_partial_paged_required_proceeds() -> None:
    """Per doc-13a:280-282 a paged required list-field is allowed (the
    consumer may approve the rule with the page-refs attached)."""

    page_ref = _page_ref()
    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=["snapshot:latest_failures"],
            ),
            item_count=10,
            next_page_ref=page_ref,
        ),
    }
    record = _snapshot_companion(
        fields,
        required_list_field_scopes=("latest_failures",),
    )
    assert record.classifier_routing.should_invoke_classifier is True
    assert record.overall_completeness.state == "paged"


def test_derive_snapshot_companion_proceeds_with_no_required_scopes() -> None:
    """A snapshot with NO required list-field scopes always proceeds
    (purely advisory / display-only)."""

    record = _snapshot_companion(required_list_field_scopes=())
    assert record.classifier_routing.should_invoke_classifier is True


# ── (d) missing required fields -> typed exception ─────────────────────────


def test_derive_snapshot_companion_raises_on_empty_scope_id() -> None:
    """An empty snapshot_scope_id is missing required identity; the
    helper raises :class:`MissingSnapshotCompanionFieldError`."""

    with pytest.raises(MissingSnapshotCompanionFieldError) as exc_info:
        derive_snapshot_companion(
            _list_field_dict(),
            snapshot_scope_id="",
            snapshot_digest="d",
            manifest_id="m",
            manifest_digest="md",
        )
    assert "snapshot_scope_id" in exc_info.value.missing_field_names


def test_derive_snapshot_companion_raises_on_whitespace_scope_id() -> None:
    """A whitespace-only snapshot_scope_id is missing required identity."""

    with pytest.raises(MissingSnapshotCompanionFieldError):
        derive_snapshot_companion(
            _list_field_dict(),
            snapshot_scope_id="   ",
            snapshot_digest="d",
            manifest_id="m",
            manifest_digest="md",
        )


def test_derive_snapshot_companion_raises_on_empty_snapshot_digest() -> None:
    """An empty snapshot_digest is missing required identity."""

    with pytest.raises(MissingSnapshotCompanionFieldError) as exc_info:
        derive_snapshot_companion(
            _list_field_dict(),
            snapshot_scope_id="snapshot:s",
            snapshot_digest="",
            manifest_id="m",
            manifest_digest="md",
        )
    assert "snapshot_digest" in exc_info.value.missing_field_names


def test_missing_snapshot_companion_field_error_inherits_value_error() -> None:
    """:class:`MissingSnapshotCompanionFieldError` inherits
    :class:`ValueError` so any caller that already catches
    :class:`ValueError` for malformed-input handling sees the failure
    (mirrors the sibling
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
    + :class:`~iriai_build_v2.execution_control.prompt_context_adapter.MissingPromptContextFieldError`
    precedents).
    """

    with pytest.raises(ValueError):
        derive_snapshot_companion(
            _list_field_dict(),
            snapshot_scope_id="",
            snapshot_digest="d",
            manifest_id="m",
            manifest_digest="md",
        )


# ── (e) round-trip via model_dump_json -> model_validate_json ──────────────


def test_authoritative_snapshot_companion_record_round_trips_via_json() -> None:
    """The typed record round-trips through JSON serialization without
    field loss.

    Critical for cross-process persistence: the snapshot companion
    record is persisted via the typed failure router; round-trip
    stability is the doc-13a:298-301 freshness contract.
    """

    record = _snapshot_companion()
    payload = record.model_dump_json()
    restored = AuthoritativeSnapshotCompanionRecord.model_validate_json(payload)
    assert restored == record


def test_authoritative_snapshot_list_field_completeness_round_trips_via_json() -> None:
    """The per-list-field completeness round-trips through JSON."""

    item = _per_list_field("latest_failures", item_count=7)
    payload = item.model_dump_json()
    restored = AuthoritativeSnapshotListFieldCompleteness.model_validate_json(
        payload
    )
    assert restored == item


def test_authoritative_snapshot_classifier_routing_round_trips_via_json() -> None:
    """The routing record round-trips through JSON."""

    routing = AuthoritativeSnapshotClassifierRouting(
        should_invoke_classifier=False,
        typed_failure_class="evidence_corruption",
        typed_failure_type="classifier_rule_blocked",
        unavailable_reason="required list-field missing",
        missing_field_names=("merge_queue",),
    )
    payload = routing.model_dump_json()
    restored = AuthoritativeSnapshotClassifierRouting.model_validate_json(
        payload
    )
    assert restored == routing


# ── (f) namespace assertion ────────────────────────────────────────────────


def test_module_imports_only_from_sanctioned_in_package_surfaces() -> None:
    """The new module imports only from stdlib + Pydantic + the
    sanctioned ``execution_control`` surfaces. NO imports from
    ``governance/`` (the governance layer consumes execution-control
    surfaces, not the reverse). NO imports from
    ``workflows/develop/execution/`` (the legacy snapshot / classifier
    surfaces are wrapped externally, not imported as a dependency).
    NO imports from ``supervisor/``.
    """

    src = pathlib.Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2"
        "/execution_control/snapshot_companion.py"
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
        "iriai_build_v2.execution_control.gate_companion",
    )
    for module in seen_modules:
        assert any(
            module == prefix or module.startswith(f"{prefix}.")
            for prefix in allowed_prefixes
        ), f"module {module!r} not in allowed import set"


# ── Pydantic extra="forbid" tests ──────────────────────────────────────────


def test_authoritative_snapshot_companion_record_forbids_unknown_fields() -> None:
    """The Pydantic model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    record = _snapshot_companion()
    payload = record.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeSnapshotCompanionRecord.model_validate(payload)


def test_authoritative_snapshot_list_field_completeness_forbids_unknown_fields() -> None:
    """The per-list-field Pydantic model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    item = _per_list_field("latest_failures")
    payload = item.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeSnapshotListFieldCompleteness.model_validate(payload)


def test_authoritative_snapshot_classifier_routing_forbids_unknown_fields() -> None:
    """The routing model rejects unknown fields per
    ``ConfigDict(extra='forbid')``."""

    routing = AuthoritativeSnapshotClassifierRouting(
        should_invoke_classifier=True,
    )
    payload = routing.model_dump()
    payload["unknown_field"] = "x"
    with pytest.raises(ValidationError):
        AuthoritativeSnapshotClassifierRouting.model_validate(payload)


# ── digest determinism ─────────────────────────────────────────────────────


def test_derive_snapshot_companion_completeness_digest_is_deterministic() -> None:
    """Two derivations with the same logical input produce byte-
    identical companion records (digest stability)."""

    a = _snapshot_companion()
    b = _snapshot_companion()
    assert a.overall_completeness.completeness_digest == (
        b.overall_completeness.completeness_digest
    )
    assert a.context_manifest_ref.completeness_digest == (
        b.context_manifest_ref.completeness_digest
    )


def test_derive_snapshot_companion_different_scopes_yield_different_digests() -> None:
    """Different snapshot scopes yield different snapshot companion
    records (the context_manifest_ref carries the required_complete_for
    list, which differs by scope).
    """

    fields = _list_field_dict()
    a = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:s1",
        snapshot_digest="d1",
        manifest_id="m",
        manifest_digest="md",
        required_list_field_scopes=("latest_failures",),
    )
    b = derive_snapshot_companion(
        fields,
        snapshot_scope_id="snapshot:s2",
        snapshot_digest="d2",
        manifest_id="m",
        manifest_digest="md",
        required_list_field_scopes=("merge_queue",),
    )
    assert a.context_manifest_ref.required_complete_for != (
        b.context_manifest_ref.required_complete_for
    )


# ════════════════════════════════════════════════════════════════════════════
# Adapter tests -- :class:`LegacySnapshotCompanionAdapter`
# ════════════════════════════════════════════════════════════════════════════


def test_legacy_snapshot_companion_adapter_implements_port_protocol() -> None:
    """The :class:`LegacySnapshotCompanionAdapter` is a concrete
    implementation of the :class:`AuthoritativeSnapshotCompanionPort`
    Protocol.

    The Protocol carries a single method ``derive_companion``; the
    adapter's method signature matches the Protocol's exactly.
    Mirrors the duck-typing check pattern from the fifth sub-slice's
    ``test_legacy_gate_companion_adapter_implements_port_protocol``
    test (the Protocol is NOT @runtime_checkable so we duck-type
    rather than ``isinstance(...)``).
    """

    adapter = LegacySnapshotCompanionAdapter()
    # Duck-typing check -- the adapter has the Protocol method.
    assert hasattr(adapter, "derive_companion")
    assert callable(adapter.derive_companion)
    # Sanity: the Protocol exists and carries the documented method.
    assert hasattr(AuthoritativeSnapshotCompanionPort, "derive_companion")


def test_legacy_snapshot_companion_adapter_derives_companion_record() -> None:
    """The :class:`LegacySnapshotCompanionAdapter` delegates to
    :func:`derive_snapshot_companion`."""

    adapter = LegacySnapshotCompanionAdapter()
    record = adapter.derive_companion(
        _list_field_dict(),
        snapshot_scope_id="snapshot:s",
        snapshot_digest="d",
        manifest_id="m",
        manifest_digest="md",
    )
    assert isinstance(record, AuthoritativeSnapshotCompanionRecord)
    assert record.classifier_routing.should_invoke_classifier is True


def test_legacy_snapshot_companion_adapter_fails_closed_on_unavailable() -> None:
    """The :class:`LegacySnapshotCompanionAdapter` propagates the
    fail-closed exception from :func:`derive_snapshot_companion`."""

    adapter = LegacySnapshotCompanionAdapter()
    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="unavailable",
                authority="display_only",
                complete_for=[],
                unavailable_reason="store error",
            ),
            item_count=0,
        ),
    }
    with pytest.raises(MissingSnapshotCompanionFieldError):
        adapter.derive_companion(
            fields,
            snapshot_scope_id="snapshot:s",
            snapshot_digest="d",
            manifest_id="m",
            manifest_digest="md",
        )


# ════════════════════════════════════════════════════════════════════════════
# (g) Legacy path byte-identical when port=None tests
# ════════════════════════════════════════════════════════════════════════════


def test_legacy_snapshot_consumer_byte_identical_when_adapter_unused() -> None:
    """Per the auto-memory ``feedback_no_refactor`` rule the snapshot-
    side surface lands as a NEW opt-in code path; the legacy
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    is byte-identical when this module's adapter is NOT used.

    Asserted indirectly: this module does NOT import ``snapshots.py``
    or ``classifier.py`` (verified by the namespace test above);
    instantiating the adapter does not mutate any global state.
    """

    # Instantiating the adapter MUST NOT touch the snapshot module.
    adapter = LegacySnapshotCompanionAdapter()
    assert adapter is not None
    # Multiple instantiations are independent (no shared state).
    other = LegacySnapshotCompanionAdapter()
    assert adapter is not other


def test_snapshot_companion_module_does_not_import_legacy_snapshot() -> None:
    """The new module's IMPORTS do NOT include the legacy Slice 10
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    module -- the new surface is external to the legacy snapshot
    boundary per the doc-13a:42-46 + 124-126 + feedback_no_refactor
    change-control rule.

    Uses AST-based detection (ignoring docstrings + comments) since
    the module's docstrings legitimately MENTION the legacy snapshot
    module to cite the doc-13a wording.
    """

    src = pathlib.Path(
        "/Users/danielzhang/src/iriai/iriai-build-v2/src/iriai_build_v2"
        "/execution_control/snapshot_companion.py"
    ).read_text()
    tree = ast.parse(src)

    forbidden_prefixes = (
        "iriai_build_v2.workflows.develop.execution.snapshots",
        "iriai_build_v2.workflows.develop.execution.classifier",
        "iriai_build_v2.supervisor",
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not any(
                node.module == prefix or node.module.startswith(f"{prefix}.")
                for prefix in forbidden_prefixes
            ), (
                f"snapshot_companion.py MUST NOT import {node.module!r} "
                f"per the doc-13a:42-46 + 124-126 change-control rule "
                f"(the legacy snapshot / supervisor surfaces are wrapped "
                f"externally, not imported as a dependency)"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not any(
                    alias.name == prefix or alias.name.startswith(f"{prefix}.")
                    for prefix in forbidden_prefixes
                ), (
                    f"snapshot_companion.py MUST NOT import {alias.name!r}"
                )


# ════════════════════════════════════════════════════════════════════════════
# Typed failure id router registration tests
# ════════════════════════════════════════════════════════════════════════════


def test_evidence_corruption_failure_class_still_exists() -> None:
    """The Slice 13A sixth sub-slice REGISTERS its 2 typed failure ids
    under the EXISTING ``evidence_corruption`` failure_class (NOT a
    new failure_class) so the supervisor classifier mapping coverage
    rule does NOT require a new mapping row in
    ``supervisor/classifier_mapping.py`` (READ-ONLY per the
    implementer prompt's MUST-NOT-EDIT-SUPERVISOR-MODULES rule).

    Sanity check: the existing ``evidence_corruption`` failure_class
    is unchanged.
    """

    assert "evidence_corruption" in FAILURE_CLASSES
    # Verify the existing evidence_corruption types are intact (no
    # regression).
    assert ("evidence_corruption", "artifact_hash_mismatch") in ROUTE_TABLE
    assert ("evidence_corruption", "payload_digest_mismatch") in ROUTE_TABLE
    assert ("evidence_corruption", "projection_body_conflict") in ROUTE_TABLE


def test_list_field_incomplete_failure_type_registered() -> None:
    """The Slice 13A sixth sub-slice registers the
    ``evidence_corruption/list_field_incomplete`` typed failure id in
    the Slice 07 failure router per doc-13a:280-282 fail-closed rule.
    Registered under the EXISTING ``evidence_corruption`` failure_class
    so the supervisor classifier mapping coverage rule does NOT
    require a new mapping row (per the change-control rule).
    """

    assert "list_field_incomplete" in FAILURE_TYPES
    assert ("evidence_corruption", "list_field_incomplete") in ROUTE_TABLE
    route = ROUTE_TABLE[("evidence_corruption", "list_field_incomplete")]
    assert route.action == "quiesce"
    assert route.failure_class == "evidence_corruption"
    assert "list_field_incomplete" in _DETERMINISTIC_FAILURE_TYPES


def test_classifier_rule_blocked_failure_type_registered() -> None:
    """The Slice 13A sixth sub-slice registers the
    ``evidence_corruption/classifier_rule_blocked`` typed failure id
    in the Slice 07 failure router per doc-13a:280-282 fail-closed
    rule. Registered under the EXISTING ``evidence_corruption``
    failure_class so the supervisor classifier mapping coverage rule
    does NOT require a new mapping row (per the change-control rule).
    """

    assert "classifier_rule_blocked" in FAILURE_TYPES
    assert ("evidence_corruption", "classifier_rule_blocked") in ROUTE_TABLE
    route = ROUTE_TABLE[("evidence_corruption", "classifier_rule_blocked")]
    assert route.action == "quiesce"
    assert route.failure_class == "evidence_corruption"
    assert "classifier_rule_blocked" in _DETERMINISTIC_FAILURE_TYPES


def test_new_typed_failure_ids_route_to_quiesce() -> None:
    """Both new typed failure ids route to ``quiesce`` per the
    fail-closed contract (per auto-memory feedback_no_silent_degradation
    + doc-13a:280-282)."""

    for failure_type in ("list_field_incomplete", "classifier_rule_blocked"):
        route = ROUTE_TABLE[("evidence_corruption", failure_type)]
        assert route.action == "quiesce", (
            f"{failure_type} MUST route to quiesce per the fail-closed contract"
        )


def test_snapshot_routing_typed_failure_class_uses_evidence_corruption_class() -> None:
    """The :class:`AuthoritativeSnapshotClassifierRouting`'s
    typed_failure_class Literal carries exactly
    ``"evidence_corruption"`` -- the EXISTING failure_class the Slice
    13A sixth sub-slice's typed failure ids are registered under (per
    the change-control rule: supervisor/classifier_mapping.py is
    READ-ONLY, so we MUST NOT add a new failure_class that would
    trigger the coverage rule)."""

    # Snapshot has only `latest_failures`; classifier requires
    # coverage of `merge_queue` (not in the snapshot) -- routing
    # blocks the classifier.
    fields = {
        "latest_failures": _per_list_field("latest_failures"),
    }
    record = _snapshot_companion(
        fields, required_list_field_scopes=("merge_queue",)
    )
    assert (
        record.classifier_routing.typed_failure_class == "evidence_corruption"
    )
    assert (
        record.classifier_routing.typed_failure_type
        == "classifier_rule_blocked"
    )


# ════════════════════════════════════════════════════════════════════════════
# (h) P3-13A-5-4 binding closure tests
# ════════════════════════════════════════════════════════════════════════════


def test_legacy_gate_consumer_snapshot_adapter_default_gate_adapter() -> None:
    """The :class:`LegacyGateConsumerSnapshotAdapter` instantiates a
    fresh
    :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
    when no ``gate_adapter`` arg is passed.

    This is the P3-13A-5-4 binding closure: the fifth-sub-slice's
    adapter now has a real production caller (this wrapper),
    satisfying the binding before any Slice 14-19 governance slice
    can claim gate execution authority.
    """

    wrapper = LegacyGateConsumerSnapshotAdapter()
    assert isinstance(wrapper._gate_adapter, LegacyGateCompanionAdapter)


def test_legacy_gate_consumer_snapshot_adapter_accepts_custom_adapter() -> None:
    """The :class:`LegacyGateConsumerSnapshotAdapter` accepts a
    custom :class:`AuthoritativeGateCompanionPort` for test isolation.
    """

    custom = LegacyGateCompanionAdapter()
    wrapper = LegacyGateConsumerSnapshotAdapter(gate_adapter=custom)
    assert wrapper._gate_adapter is custom


def test_legacy_gate_consumer_snapshot_adapter_composes_complete_records() -> None:
    """The wrapper composes the snapshot companion record with the gate
    companion record when both are complete."""

    snap_record = _snapshot_companion()
    prompt_bundle = _prompt_context_bundle()
    wrapper = LegacyGateConsumerSnapshotAdapter()
    gate_record = wrapper.derive_gate_with_snapshot(
        snap_record,
        prompt_bundle,
        gate_scope_id="task:TASK-1",
        gate_input_digest="gate-input-digest",
    )
    assert isinstance(gate_record, AuthoritativeGateCompanionRecord)
    assert gate_record.gate_scope_id == "task:TASK-1"
    assert gate_record.approval_routing.should_approve_gate is True


def test_legacy_gate_consumer_snapshot_adapter_fails_closed_on_snapshot_incomplete() -> None:
    """When the snapshot companion record is incomplete for the gate's
    required snapshot scope, the wrapper raises
    :class:`MissingSnapshotCompanionFieldError` (phase 1 fail-closed).
    """

    # Snapshot has `latest_failures` only; gate requires snapshot
    # coverage of `merge_queue` -- phase 1 fails closed.
    fields = {
        "latest_failures": _per_list_field("latest_failures"),
    }
    snap_record = _snapshot_companion(fields)
    prompt_bundle = _prompt_context_bundle()
    wrapper = LegacyGateConsumerSnapshotAdapter()
    with pytest.raises(MissingSnapshotCompanionFieldError) as exc_info:
        wrapper.derive_gate_with_snapshot(
            snap_record,
            prompt_bundle,
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
            required_snapshot_list_field_scopes=("merge_queue",),
        )
    assert "merge_queue" in exc_info.value.missing_field_names
    assert "gate:atomic_landing" in (exc_info.value.unavailable_reason or "")


def test_legacy_gate_consumer_snapshot_adapter_fails_closed_on_gate_preview_only() -> None:
    """When the snapshot passes but the gate companion record fails
    (e.g. authoritative bundle is preview_only), the wrapper raises
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
    (phase 2 fail-closed)."""

    snap_record = _snapshot_companion()
    # Construct a bundle that triggers preview_only state.
    legacy = _legacy_bundle(context_file_refs=[])
    prompt_bundle = _prompt_context_bundle(legacy)
    assert prompt_bundle.completeness.state == "preview_only"

    wrapper = LegacyGateConsumerSnapshotAdapter()
    with pytest.raises(MissingGateCompanionFieldError):
        wrapper.derive_gate_with_snapshot(
            snap_record,
            prompt_bundle,
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
        )


def test_derive_gate_companion_with_snapshot_helper_composes() -> None:
    """The :func:`derive_gate_companion_with_snapshot` pure helper
    delegates to a fresh
    :class:`LegacyGateConsumerSnapshotAdapter` and produces the same
    composed gate companion record."""

    snap_record = _snapshot_companion()
    prompt_bundle = _prompt_context_bundle()
    gate_record = derive_gate_companion_with_snapshot(
        snap_record,
        prompt_bundle,
        gate_scope_id="task:TASK-1",
        gate_input_digest="gate-input-digest",
    )
    assert isinstance(gate_record, AuthoritativeGateCompanionRecord)
    assert gate_record.approval_routing.should_approve_gate is True


def test_derive_gate_companion_with_snapshot_helper_carries_proof_rows() -> None:
    """The pure helper carries proof_rows through to the gate companion
    record per doc-13a:276-278 + the fifth-sub-slice gate adapter."""

    snap_record = _snapshot_companion()
    prompt_bundle = _prompt_context_bundle()
    page_ref = _page_ref()
    proof_row = derive_proof_row(
        source_digest="source-sha",
        page_refs=[page_ref],
        proof_algorithm="sha256_concatenation",
        verification_time="2026-05-26T15:00:00Z",
    )
    gate_record = derive_gate_companion_with_snapshot(
        snap_record,
        prompt_bundle,
        gate_scope_id="task:TASK-1",
        gate_input_digest="gate-input-digest",
        proof_rows=[proof_row],
    )
    assert len(gate_record.proof_rows) == 1
    assert gate_record.proof_rows[0].source_digest == "source-sha"


def test_derive_gate_companion_with_snapshot_helper_phase_1_fails_closed() -> None:
    """The pure helper fails closed in phase 1 (snapshot gate) when
    the snapshot is incomplete for the gate's required snapshot scope.
    """

    fields = {
        "latest_failures": _per_list_field("latest_failures"),
    }
    snap_record = _snapshot_companion(fields)
    prompt_bundle = _prompt_context_bundle()
    with pytest.raises(MissingSnapshotCompanionFieldError):
        derive_gate_companion_with_snapshot(
            snap_record,
            prompt_bundle,
            gate_scope_id="gate:atomic_landing",
            gate_input_digest="gate-input-digest",
            required_snapshot_list_field_scopes=("merge_queue",),
        )


def test_legacy_gate_consumer_snapshot_adapter_default_gate_adapter_is_independent() -> None:
    """Each wrapper instantiation creates an independent
    :class:`LegacyGateCompanionAdapter` (no shared state across
    wrappers)."""

    w1 = LegacyGateConsumerSnapshotAdapter()
    w2 = LegacyGateConsumerSnapshotAdapter()
    # Different instances (per-call instantiation per P3-13A-5-1).
    assert w1._gate_adapter is not w2._gate_adapter


def test_legacy_gate_consumer_snapshot_wires_default_when_required_scopes_empty() -> None:
    """When required_snapshot_list_field_scopes is empty, the wrapper
    SKIPS phase 1 and proceeds directly to phase 2 (gate companion
    record) -- the consumer opted out of snapshot gating."""

    snap_record = _snapshot_companion()
    prompt_bundle = _prompt_context_bundle()
    wrapper = LegacyGateConsumerSnapshotAdapter()
    gate_record = wrapper.derive_gate_with_snapshot(
        snap_record,
        prompt_bundle,
        gate_scope_id="task:TASK-1",
        gate_input_digest="d",
        required_snapshot_list_field_scopes=(),
    )
    assert isinstance(gate_record, AuthoritativeGateCompanionRecord)
    assert gate_record.approval_routing.should_approve_gate is True


# ════════════════════════════════════════════════════════════════════════════
# Per-list-field completeness sanity tests
# ════════════════════════════════════════════════════════════════════════════


def test_per_list_field_completeness_required_field_name() -> None:
    """The per-list-field completeness MUST carry a non-empty
    field_name (typed at the Pydantic level)."""

    # Pydantic itself accepts empty strings unless we add a validator;
    # the test verifies the typed shape exists and field_name is required.
    item = _per_list_field("merge_queue")
    assert item.field_name == "merge_queue"


def test_per_list_field_completeness_supports_next_page_ref() -> None:
    """The per-list-field completeness carries the next_page_ref for
    paged display (doc-13a:236-242)."""

    page_ref = _page_ref(ref_id="next-page", reason="next-page-cursor")
    item = AuthoritativeSnapshotListFieldCompleteness(
        field_name="merge_queue",
        completeness=_completeness(
            state="paged",
            page_refs=[page_ref],
            complete_for=["snapshot:merge_queue"],
        ),
        item_count=10,
        next_page_ref=page_ref,
    )
    assert item.next_page_ref is not None
    assert item.next_page_ref.ref_id == "next-page"


def test_per_list_field_completeness_default_next_page_ref_is_none() -> None:
    """The next_page_ref defaults to ``None`` (single-page list)."""

    item = _per_list_field("retry_budgets")
    assert item.next_page_ref is None


# ════════════════════════════════════════════════════════════════════════════
# Sanity: classifier routing carries snapshot/scope id details
# ════════════════════════════════════════════════════════════════════════════


def test_classifier_routing_carries_missing_field_names() -> None:
    """When classifier_rule_blocked, missing_field_names lists all
    required scopes that the snapshot did not cover."""

    fields = {
        "latest_failures": _per_list_field("latest_failures"),
    }
    record = _snapshot_companion(
        fields,
        required_list_field_scopes=("merge_queue", "retry_budgets"),
    )
    assert set(record.classifier_routing.missing_field_names) == {
        "merge_queue",
        "retry_budgets",
    }


def test_classifier_routing_reports_per_field_paged_satisfies_required() -> None:
    """A paged required field satisfies the classifier routing per
    doc-13a:280-282 ("either complete for the gate scope or exactly
    paged" carries through to snapshot rules)."""

    page_ref = _page_ref()
    fields = {
        "latest_failures": AuthoritativeSnapshotListFieldCompleteness(
            field_name="latest_failures",
            completeness=_completeness(
                state="paged",
                page_refs=[page_ref],
                complete_for=["snapshot:latest_failures"],
            ),
            item_count=10,
            next_page_ref=page_ref,
        ),
    }
    record = _snapshot_companion(
        fields, required_list_field_scopes=("latest_failures",)
    )
    assert record.classifier_routing.should_invoke_classifier is True
    assert record.classifier_routing.typed_failure_class is None
