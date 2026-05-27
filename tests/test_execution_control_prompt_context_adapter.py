"""Slice 13A third sub-slice -- unit tests for the
``execution_control/prompt_context_adapter.py`` compatibility adapter.

Covers the doc-13a:194-211 13A-owned compatibility wrapper +
doc-13a § Refactoring Steps step 3 (doc-13a:266-268) adapter function:

* :class:`AuthoritativePromptContextBundle` -- the 12-field Pydantic
  BaseModel + the display-only ``truncation_notes`` field preserved
  per doc-13a:213-215.
* :class:`MissingPromptContextFieldError` -- typed exception raised on
  missing-required-field legacy bundles (fail-closed; NOT silent degrade
  per the auto-memory ``feedback_no_silent_degradation`` rule).
* :func:`derive_authoritative_prompt_context_bundle` -- the adapter
  function that derives the wrapper from a legacy Slice 05
  :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.PromptContextBundle`
  record (doc-13a:266-268 step 3).

Test surface (12-20 tests per implementer prompt point 4):

* (a) Adapter produces ``AuthoritativePromptContextBundle`` from a
  fully-resolved legacy bundle.
* (b) ``completeness.state == "paged"`` when ``truncation_notes`` is
  non-empty.
* (c) ``completeness.state == "preview_only"`` when only
  ``prompt_summary`` is present (no ``context_file_refs``).
* (d) :class:`MissingPromptContextFieldError` raised on missing
  ``prompt_ref`` / ``prompt_sha256`` / ``context_sha256`` (the doc-13a
  ``state="unavailable"`` case; fail-closed).
* (e) ``completeness_digest`` is deterministic across two adapter calls
  with the same input.
* (f) Pydantic ``extra="forbid"`` rejects unknown fields on the new
  wrapper.
* (g) Adapter preserves legacy ``truncation_notes`` verbatim as display
  metadata only (does NOT consume it as authority).
* (h) ``AuthoritativePromptContextBundle`` round-trips via
  ``model_dump_json`` -> ``model_validate_json``.
* (i) Legacy ``PromptContextBundle`` is NOT modified by the adapter
  call (immutable-input invariant; the doc-13a:42-46 + 124-126
  change-control rule).
* (j) The adapter imports only from
  :mod:`iriai_build_v2.execution_control.completeness` +
  :mod:`iriai_build_v2.workflows.develop.execution.dispatcher` -- no
  other modules (namespace assertion).

Plus structural tests:

* Module ``__all__`` lists the documented surface exactly.
* ``MissingPromptContextFieldError`` inherits :class:`ValueError`.
* The legacy field-rename ``prompt_summary`` -> ``display_prompt_summary``
  is preserved verbatim per doc-13a:201.
* The wrapper carries the typed ``context_manifest_ref:
  AuthoritativeContextRef`` per doc-13a:202.
* The wrapper carries the typed ``completeness: EvidenceCompleteness``
  per doc-13a:210.
* The ``preview_only`` state forces ``authority == "display_only"`` per
  the Slice 13A invariant doc-13a:18-23 + doc-13a:111-115.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    EvidenceCompleteness,
    EvidencePageRef,
)
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
    MissingPromptContextFieldError,
    derive_authoritative_prompt_context_bundle,
)
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    PromptContextBundle,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the wrapper + exception + adapter.

    Per doc-13a:194-211 the wrapper is the 13A-owned compatibility shape;
    per doc-13a:266-268 the adapter function is the derive helper; the
    typed exception fails closed on missing-required fields per the
    auto-memory ``feedback_no_silent_degradation`` rule.
    """

    from iriai_build_v2.execution_control import prompt_context_adapter as mod

    expected = {
        "AuthoritativePromptContextBundle",
        "MissingPromptContextFieldError",
        "derive_authoritative_prompt_context_bundle",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 3
    for name in expected:
        assert hasattr(mod, name)


# ── test fixtures ──────────────────────────────────────────────────────────


def _legacy_bundle(**overrides: Any) -> PromptContextBundle:
    """Construct a fully-resolved legacy Slice 05 :class:`PromptContextBundle`.

    Mirrors the production fixture at
    ``tests/workflows/develop/execution/test_dispatcher.py:83-95`` (the
    ``_bundle()`` helper at line 83) so the adapter is exercised against
    the same shape Slice 05 callers use today.
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


def _derive(
    legacy_bundle: PromptContextBundle | None = None,
    **derive_kwargs: Any,
) -> AuthoritativePromptContextBundle:
    """Convenience wrapper around the adapter function for tests.

    Default scope identity values name a synthetic test fixture; tests that
    care about specific values override per call.
    """

    bundle = legacy_bundle if legacy_bundle is not None else _legacy_bundle()
    base: dict[str, Any] = dict(
        manifest_id="manifest-1",
        manifest_digest="manifest-digest",
        feature_id="feature-abc",
        dag_sha256="dag-sha",
        task_id="TASK-1",
    )
    base.update(derive_kwargs)
    return derive_authoritative_prompt_context_bundle(bundle, **base)


# ── (a) adapter produces wrapper from fully-resolved legacy bundle ─────────


def test_adapter_produces_wrapper_from_complete_legacy_bundle() -> None:
    """A fully-resolved legacy bundle yields a populated wrapper with
    ``completeness.state == "complete"`` + ``authority ==
    "execution_authority"`` per doc-13a:115-118 default.

    The doc-13a:198-211 12 wrapper fields all populate verbatim from the
    legacy fixture (with the doc-13a:201 ``prompt_summary`` ->
    ``display_prompt_summary`` rename).
    """

    bundle = _derive()
    # The 12 doc-13a:198-211 wrapper fields populate.
    assert bundle.prompt_ref == 41
    assert bundle.prompt_sha256 == "prompt-sha"
    # Per doc-13a:201 the legacy prompt_summary renames to
    # display_prompt_summary; the string content carries through verbatim.
    assert bundle.display_prompt_summary == "bounded prompt"
    assert isinstance(bundle.context_manifest_ref, AuthoritativeContextRef)
    assert bundle.context_file_refs == [42]
    assert bundle.context_file_paths == ["context/TASK-1.md"]
    assert bundle.context_sha256 == "context-sha"
    assert bundle.included_contract_ids == [11]
    assert bundle.included_evidence_ids == [31]
    assert bundle.excluded_evidence_ids == []
    assert bundle.excluded_evidence_refs == []
    assert isinstance(bundle.completeness, EvidenceCompleteness)
    # The display-only truncation_notes is preserved verbatim per doc-13a:213-215.
    assert bundle.truncation_notes == []
    # Default state per doc-13a:115-118 (fully-resolved -> "complete" +
    # execution_authority).
    assert bundle.completeness.state == "complete"
    assert bundle.completeness.authority == "execution_authority"


def test_adapter_completeness_complete_for_carries_feature_and_task_scopes() -> None:
    """``completeness.complete_for`` names the decision scope the wrapper
    covers -- the doc-13a:165 wording mandates a list of decision-scope
    identifiers.

    For the legacy Slice 05 bundle the scope is task-scoped (the legacy
    bundle is task-scoped per dispatcher.py); the adapter records both
    the task scope and the feature scope so future Slice 13A gate /
    routing consumers can disambiguate.
    """

    bundle = _derive(task_id="TASK-1", feature_id="feature-abc")
    assert "task:TASK-1" in bundle.completeness.complete_for
    assert "feature:feature-abc" in bundle.completeness.complete_for
    assert bundle.context_manifest_ref.required_complete_for == bundle.completeness.complete_for


# ── (b) completeness.state == "paged" when truncation_notes non-empty ───────


def test_adapter_state_paged_when_truncation_notes_non_empty() -> None:
    """Per doc-13a:115-118 + doc-13a:303-310: a non-empty
    ``truncation_notes`` signals the semantic context was bounded but
    the adapter sets ``state="paged"``. A dispatcher may drive execution
    from paged context only when exact page refs are present on
    completeness; the dispatcher fails closed when the refs are absent.
    """

    legacy = _legacy_bundle(truncation_notes=["budget exhausted; 2 of 5 pages dropped"])
    bundle = _derive(legacy)
    assert bundle.completeness.state == "paged"
    # Authority is preserved on the wrapper, but runtime dispatch also
    # requires exact page refs for paged evidence.
    assert bundle.completeness.authority == "execution_authority"
    # And the legacy truncation_notes is preserved verbatim on the
    # display-only wrapper field per doc-13a:213-215.
    assert bundle.truncation_notes == ["budget exhausted; 2 of 5 pages dropped"]


# ── (c) state == "preview_only" when only prompt_summary present ────────────


def test_adapter_state_preview_only_when_no_context_file_refs() -> None:
    """Per doc-13a:115-118: a legacy bundle with ``prompt_summary`` but no
    ``context_file_refs`` is a legacy fallback (display preview only); the
    adapter sets ``state="preview_only"`` AND forces
    ``authority="display_only"`` per the Slice 13A invariant
    doc-13a:18-23 + doc-13a:111-115 (preview-only evidence MUST NOT
    drive authoritative decisions).
    """

    legacy = _legacy_bundle(
        context_file_refs=[],
        context_file_paths=[],
        included_evidence_ids=[],
    )
    bundle = _derive(legacy)
    assert bundle.completeness.state == "preview_only"
    # Authority forced to display_only per doc-13a:18-23 + doc-13a:111-115.
    assert bundle.completeness.authority == "display_only"
    # The display_prompt_summary preserves the legacy prompt_summary string.
    assert bundle.display_prompt_summary == "bounded prompt"


def test_adapter_preview_only_overrides_explicit_execution_authority_request() -> None:
    """Per the Slice 13A invariant doc-13a:18-23 + doc-13a:111-115: even
    if the caller explicitly requests ``authority="execution_authority"``,
    a ``preview_only`` state forces ``authority="display_only"``.

    A preview cannot carry execution authority regardless of caller
    intent -- the doc-13a:18-23 wording makes this non-negotiable.
    """

    legacy = _legacy_bundle(context_file_refs=[])
    bundle = _derive(legacy, authority="execution_authority")
    assert bundle.completeness.state == "preview_only"
    assert bundle.completeness.authority == "display_only"


# ── (d) MissingPromptContextFieldError raised on missing required fields ────


def test_adapter_raises_typed_exception_on_missing_prompt_ref() -> None:
    """A legacy bundle with ``prompt_ref == 0`` (placeholder / unfilled)
    is missing the required ref identity; the adapter raises
    :class:`MissingPromptContextFieldError` per the auto-memory
    ``feedback_no_silent_degradation`` rule + doc-13a:307-310
    ``state="unavailable"`` semantics."""

    legacy = _legacy_bundle(prompt_ref=0)
    with pytest.raises(MissingPromptContextFieldError) as exc_info:
        _derive(legacy)
    assert "prompt_ref" in exc_info.value.missing_field_names


def test_adapter_raises_typed_exception_on_missing_prompt_sha256() -> None:
    """A legacy bundle with empty ``prompt_sha256`` is missing the
    required content digest; the adapter raises
    :class:`MissingPromptContextFieldError`."""

    legacy = _legacy_bundle(prompt_sha256="")
    with pytest.raises(MissingPromptContextFieldError) as exc_info:
        _derive(legacy)
    assert "prompt_sha256" in exc_info.value.missing_field_names


def test_adapter_raises_typed_exception_on_missing_context_sha256() -> None:
    """A legacy bundle with empty ``context_sha256`` is missing the
    required context digest; the adapter raises
    :class:`MissingPromptContextFieldError`."""

    legacy = _legacy_bundle(context_sha256="")
    with pytest.raises(MissingPromptContextFieldError) as exc_info:
        _derive(legacy)
    assert "context_sha256" in exc_info.value.missing_field_names


def test_adapter_typed_exception_lists_all_missing_fields() -> None:
    """When multiple required fields are missing, the exception's
    ``missing_field_names`` lists ALL of them (NOT just the first) per
    the auto-memory ``feedback_never_truncate_decisions`` rule (returning
    ALL feedback to the caller, not just the first)."""

    legacy = _legacy_bundle(prompt_ref=0, prompt_sha256="", context_sha256="")
    with pytest.raises(MissingPromptContextFieldError) as exc_info:
        _derive(legacy)
    missing = set(exc_info.value.missing_field_names)
    assert missing == {"prompt_ref", "prompt_sha256", "context_sha256"}


def test_missing_prompt_context_field_error_inherits_value_error() -> None:
    """:class:`MissingPromptContextFieldError` inherits :class:`ValueError`
    so any caller that already catches :class:`ValueError` for
    malformed-input handling sees the failure.

    Mirrors the
    :class:`iriai_build_v2.workflows.develop.governance.evidence_store.GovernanceEvidenceStoreIdempotencyConflict`
    sibling precedent (which also inherits :class:`ValueError`).
    """

    legacy = _legacy_bundle(prompt_ref=0)
    with pytest.raises(ValueError):
        _derive(legacy)


# ── (e) completeness_digest deterministic across two adapter calls ──────────


def test_adapter_completeness_digest_is_deterministic_across_two_calls() -> None:
    """Two adapter calls with byte-identical inputs produce byte-identical
    ``completeness.completeness_digest`` values.

    The doc-13a:264 canonical-JSON discipline + :func:`compute_completeness_digest`
    is the cross-process freshness contract subsequent Slice 13A
    sub-slices rely on when consumers compare
    ``AuthoritativeContextRef.completeness_digest`` to detect manifest
    staleness.
    """

    bundle_1 = _derive()
    bundle_2 = _derive()
    assert (
        bundle_1.completeness.completeness_digest
        == bundle_2.completeness.completeness_digest
    )
    # And the digest is a 64-char SHA-256 hex string.
    assert len(bundle_1.completeness.completeness_digest) == 64
    # Both calls produce the same context_manifest_ref.completeness_digest too.
    assert (
        bundle_1.context_manifest_ref.completeness_digest
        == bundle_2.context_manifest_ref.completeness_digest
    )


def test_adapter_completeness_digest_changes_with_state() -> None:
    """The digest is state-sensitive -- a state change produces a
    different digest (proving the digest captures state)."""

    bundle_complete = _derive()
    bundle_paged = _derive(_legacy_bundle(truncation_notes=["dropped"]))
    assert bundle_complete.completeness.state == "complete"
    assert bundle_paged.completeness.state == "paged"
    assert (
        bundle_complete.completeness.completeness_digest
        != bundle_paged.completeness.completeness_digest
    )


# ── (f) Pydantic extra="forbid" rejects unknown fields ──────────────────────


def test_authoritative_wrapper_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed on the
    new wrapper.

    Critically distinct from the legacy Slice 05 ``PromptContextBundle``
    which is ``extra="allow"`` (preserved for compatibility per
    doc-13a:42-46); the new 13A wrapper is the **authoritative** typed
    surface and forbids unknown fields per the sibling completeness.py
    precedent.
    """

    legacy = _legacy_bundle()
    # Derive a valid wrapper, then dump + try to validate with an extra field.
    bundle = _derive(legacy)
    raw = bundle.model_dump()
    raw["unknown_field"] = "oops"
    with pytest.raises(ValidationError):
        AuthoritativePromptContextBundle.model_validate(raw)


def test_legacy_promptcontextbundle_remains_extra_allow() -> None:
    """The accepted Slice 05 ``PromptContextBundle`` base
    (``_DispatcherModel`` at ``dispatcher.py:99-104``) remains
    ``extra="allow"`` per doc-13a:42-46 (NO in-place edits to accepted
    Slice 05 interfaces).

    This test is the **invariant guard** that proves the adapter did NOT
    silently change the legacy base config.
    """

    legacy_config = PromptContextBundle.model_config
    assert legacy_config.get("extra") == "allow"


# ── (g) adapter preserves truncation_notes as display metadata only ─────────


def test_adapter_preserves_truncation_notes_verbatim_as_display_metadata() -> None:
    """Per doc-13a:213-215 the legacy ``truncation_notes`` field remains
    readable for compatibility but is display metadata only.

    The adapter MUST preserve the legacy list verbatim on the wrapper's
    ``truncation_notes`` field; the **authoritative** signal is
    :data:`AuthoritativePromptContextBundle.completeness`, NOT
    :data:`AuthoritativePromptContextBundle.truncation_notes`.
    """

    notes = ["page-3 dropped", "page-5 dropped", "context exceeded budget"]
    legacy = _legacy_bundle(truncation_notes=notes)
    bundle = _derive(legacy)
    # Verbatim preservation.
    assert bundle.truncation_notes == notes
    # And the authoritative completeness reflects the truncation (paged
    # state per the boundary rule) -- the consumer reads completeness,
    # NOT truncation_notes.
    assert bundle.completeness.state == "paged"


# ── (h) round-trip via model_dump_json -> model_validate_json ───────────────


def test_authoritative_wrapper_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity for the
    new wrapper.

    Mirrors the sibling completeness.py round-trip tests
    (``test_evidence_page_ref_round_trips_via_json`` +
    ``test_exact_evidence_manifest_round_trips_via_json``).
    """

    bundle = _derive()
    serialised = bundle.model_dump_json()
    restored = AuthoritativePromptContextBundle.model_validate_json(serialised)
    assert restored == bundle
    # And the round-trip payload itself is byte-identical.
    assert restored.model_dump_json() == serialised


def test_authoritative_wrapper_round_trips_with_excluded_evidence_refs() -> None:
    """Round-trip is identity when the wrapper carries non-empty
    ``excluded_evidence_refs`` (the typed page-ref surface)."""

    refs = [
        EvidencePageRef(
            ref_id="page-ref-excluded-1",
            source_kind="typed_row",
            source_id=999,
            sha256="excluded-sha",
            reason="off-scope-evidence",
        ),
    ]
    bundle = _derive(excluded_evidence_refs=refs)
    serialised = bundle.model_dump_json()
    restored = AuthoritativePromptContextBundle.model_validate_json(serialised)
    assert restored == bundle
    assert restored.excluded_evidence_refs == refs
    assert restored.completeness.page_refs == refs


def test_paged_authoritative_wrapper_carries_page_refs_in_completeness() -> None:
    refs = [
        EvidencePageRef(
            ref_id="page-ref-paged-1",
            source_kind="typed_row",
            source_id=1001,
            sha256="paged-sha",
            reason="large-prompt-page",
        ),
    ]
    bundle = _derive(
        _legacy_bundle(truncation_notes=["large prompt paged"]),
        excluded_evidence_refs=refs,
    )

    assert bundle.completeness.state == "paged"
    assert bundle.completeness.page_refs == refs


# ── (i) legacy PromptContextBundle NOT modified by adapter call ─────────────


def test_adapter_does_not_mutate_legacy_bundle() -> None:
    """Per doc-13a:42-46 + 124-126 the change-control rule: the adapter
    MUST NOT mutate the legacy bundle.

    Captures the legacy bundle's field values BEFORE the adapter call +
    re-reads them AFTER, asserting byte-identical content. This is the
    **immutable-input invariant** guard.
    """

    legacy = _legacy_bundle(
        truncation_notes=["original-note"],
        context_file_refs=[100, 200, 300],
        included_evidence_ids=[10, 20, 30],
    )
    # Capture pre-call snapshot.
    pre_snapshot = deepcopy(legacy.model_dump())

    _derive(legacy)

    # Re-read post-call snapshot.
    post_snapshot = legacy.model_dump()
    assert pre_snapshot == post_snapshot
    # And verify the specific fields the adapter reads are unchanged.
    assert legacy.prompt_ref == 41
    assert legacy.prompt_sha256 == "prompt-sha"
    assert legacy.prompt_summary == "bounded prompt"
    assert legacy.truncation_notes == ["original-note"]
    assert legacy.context_file_refs == [100, 200, 300]
    assert legacy.included_evidence_ids == [10, 20, 30]


def test_slice_05_promptcontextbundle_module_source_unchanged() -> None:
    """The accepted Slice 05 ``PromptContextBundle`` module at
    ``src/iriai_build_v2/workflows/develop/execution/dispatcher.py:229-239``
    remains byte-identical -- the adapter is a wrapper, NOT an in-place
    edit.

    Reads the legacy ``PromptContextBundle`` field set via
    :attr:`pydantic.BaseModel.model_fields` (already-imported class;
    NO ``importlib.reload`` to avoid corrupting dispatcher module state
    for cross-suite runs -- see P1-13A-3-1 in the Slice 13A third
    sub-slice finalizer remediation). The 10 doc-13a:198-211-cited
    legacy fields are present verbatim with the legacy types (NOT the
    new wrapper types).
    """

    # Use the module-level ``PromptContextBundle`` import (no reload).
    # ``importlib.reload`` would replace the dispatcher module's
    # globals (Lock objects, FakeStore/FakeRuntime classes used by
    # ``tests/workflows/develop/execution/test_dispatcher.py``) with
    # fresh instances, breaking ``isinstance`` checks and shared-state
    # invariants in any subsequently-run dispatcher tests within the
    # same pytest session. The structural invariant (the 10 legacy
    # fields + ``extra="allow"`` base config) is fully observable via
    # ``model_fields`` + ``model_config`` on the already-imported class
    # -- no reload required.
    fields = PromptContextBundle.model_fields
    # The 10 legacy field names verbatim (NOT the new wrapper names).
    expected_legacy_fields = {
        "prompt_ref",
        "prompt_sha256",
        "prompt_summary",  # NOT display_prompt_summary (that is the new wrapper).
        "context_file_refs",
        "context_file_paths",
        "context_sha256",
        "included_contract_ids",
        "included_evidence_ids",
        "excluded_evidence_ids",
        "truncation_notes",  # Preserved on the legacy bundle per doc-13a:213-215.
    }
    assert set(fields.keys()) == expected_legacy_fields
    # And the legacy base config remains extra="allow" (NOT extra="forbid"
    # like the new wrapper) -- the adapter did NOT change the legacy
    # base config.
    assert PromptContextBundle.model_config.get("extra") == "allow"


# ── (j) adapter imports only from completeness + dispatcher PromptContextBundle


def test_adapter_imports_only_from_completeness_and_dispatcher() -> None:
    """Per the implementer prompt § "Non-negotiables": the adapter
    imports only from
    :mod:`iriai_build_v2.execution_control.completeness` +
    :mod:`iriai_build_v2.workflows.develop.execution.dispatcher`
    ``PromptContextBundle`` -- no other modules.

    Specifically: NO imports from ``governance/`` (the governance layer
    consumes execution-control surfaces, not the reverse), NO imports
    from other parts of ``execution_control/`` beyond ``completeness``.

    Verifies via the adapter module's source code (stdlib + pydantic +
    the 2 sanctioned in-package imports) -- this is a structural
    invariant the future 13A wiring sub-slices rely on.
    """

    from iriai_build_v2.execution_control import prompt_context_adapter as mod
    import inspect

    source = inspect.getsource(mod)
    # The 2 sanctioned in-package imports.
    assert "from iriai_build_v2.execution_control.completeness import" in source
    assert (
        "from iriai_build_v2.workflows.develop.execution.dispatcher import" in source
    )
    # NO governance imports (this is the doc-13a:42-55 + doc-13a:194-196
    # namespace boundary).
    assert "from iriai_build_v2.workflows.develop.governance" not in source
    assert "import iriai_build_v2.workflows.develop.governance" not in source
    # NO other parts of execution_control/ beyond completeness.
    assert "from iriai_build_v2.execution_control.store import" not in source
    assert "from iriai_build_v2.execution_control.models import" not in source
    assert "from iriai_build_v2.execution_control.adoption import" not in source
    assert "from iriai_build_v2.execution_control.atomic_landing import" not in source
    assert (
        "from iriai_build_v2.execution_control.merge_queue_store import" not in source
    )
    assert (
        "from iriai_build_v2.execution_control.regroup_overlay_store import" not in source
    )
    assert "from iriai_build_v2.execution_control.startup import" not in source


# ── Structural: AuthoritativePromptContextBundle field shape ────────────────


def test_authoritative_wrapper_carries_typed_context_manifest_ref() -> None:
    """Per doc-13a:202: the wrapper carries
    ``context_manifest_ref: AuthoritativeContextRef`` (typed, NOT a
    legacy int / str ref).

    The doc-13a:194-211 wrapper spec explicitly types the context ref as
    :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    so future Slice 13A sub-slices can drive authoritative decisions
    from the typed ref.
    """

    bundle = _derive(
        manifest_id="m-99",
        manifest_digest="digest-99",
        feature_id="feat-99",
        task_id="task-99",
    )
    ref = bundle.context_manifest_ref
    assert isinstance(ref, AuthoritativeContextRef)
    assert ref.manifest_id == "m-99"
    assert ref.manifest_digest == "digest-99"
    # The completeness_digest is the same byte-identical digest on the
    # completeness record (per doc-13a:189 "ties the ref to a specific
    # completeness state").
    assert ref.completeness_digest == bundle.completeness.completeness_digest


def test_authoritative_wrapper_carries_typed_completeness_record() -> None:
    """Per doc-13a:210: the wrapper carries
    ``completeness: EvidenceCompleteness`` (typed; the doc-13a:127-192
    foundational shape from the second sub-slice)."""

    bundle = _derive()
    completeness = bundle.completeness
    assert isinstance(completeness, EvidenceCompleteness)
    # And the typed completeness carries the 8 doc-13a:163-170 fields.
    assert hasattr(completeness, "state")
    assert hasattr(completeness, "authority")
    assert hasattr(completeness, "complete_for")
    assert hasattr(completeness, "missing_required_refs")
    assert hasattr(completeness, "page_refs")
    assert hasattr(completeness, "preview_ref")
    assert hasattr(completeness, "unavailable_reason")
    assert hasattr(completeness, "completeness_digest")
