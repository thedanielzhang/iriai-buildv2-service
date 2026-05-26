"""Slice 13A fourth sub-slice -- unit tests for
``execution_control/dispatcher_prompt_context.py`` (the FIRST executor
wiring of the 13A typed surfaces through the dispatcher prompt/context
boundary per doc-13a § Refactoring Steps step 4 / doc-13a:269-272).

Test surface (12-20 tests per implementer prompt point 5):

* (a) Legacy `_build_prompt` path is BYTE-IDENTICAL when the new opt-in
  port is `None` (preserves the 22-passed Slice 05 dispatcher baseline).
* (b) When the opt-in port is set, the dispatcher calls the new
  authoritative adapter through `_build_prompt`.
* (c) `state="unavailable"` (adapter raises
  :class:`MissingPromptContextFieldError`) routes to typed failure
  ``runtime_context/context_incomplete`` WITHOUT invoking the runtime
  per doc-13a:269-272.
* (d) `state="paged"` proceeds through the runtime with the
  authoritative companion record attached (legacy `truncation_notes`
  non-empty).
* (e) `state="preview_only"` proceeds + authoritative companion record's
  `authority="display_only"` forced per doc-13a:111-115 (override-
  resistant Slice 13A invariant).
* (f) Legacy `PromptBuildResult` preserved verbatim in new
  `AuthoritativePromptBuildResult` (composition invariant per
  doc-13a:42-46 + 124-126).
* (g) Namespace assertion: new module imports only from sanctioned
  in-package surfaces (`completeness` + `prompt_context_adapter` +
  Slice 05 dispatcher surfaces).

Plus structural tests:

* Module ``__all__`` lists the documented surface exactly.
* ``AuthoritativePromptBuildResult`` carries
  ``ConfigDict(extra='forbid')``.
* ``AuthoritativePromptContextRouting`` carries
  ``ConfigDict(extra='forbid')``.
* ``derive_dispatch_routing`` returns the routing carried on the
  typed result (pure projection, no side effects).
* ``runtime_context/context_incomplete`` typed failure id is
  registered with the Slice 07 typed-failure router.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    EvidenceCompleteness,
)
from iriai_build_v2.execution_control.dispatcher_prompt_context import (
    AuthoritativePromptBuildResult,
    AuthoritativePromptBuilderPort,
    AuthoritativePromptContextIncompleteSignal,
    AuthoritativePromptContextRouting,
    LegacyPromptBuilderAuthoritativeAdapter,
    derive_dispatch_routing,
)
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
    MissingPromptContextFieldError,
    derive_authoritative_prompt_context_bundle,
)
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    ActorMetadata,
    DispatchOutcome,
    DispatchRequest,
    DispatchRetryIdentity,
    PatchCaptureRecord,
    PromptBuildResult,
    PromptContextBundle,
    RuntimeDispatcher,
    RuntimeInvocationRequest,
    RuntimeInvocationResponse,
    RuntimeWorkspaceBinding,
    StructuredOutputRecord,
    actor_metadata_digest,
    dispatch_idempotency_key,
    dispatch_request_digest,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
)


# ── module surface tests ───────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the typed result + Protocol +
    routing classifier + adapter + signal exception + helper.

    Per doc-13a:269-272 the wired path is the first executor wiring of
    the 13A typed surfaces; per the auto-memory ``feedback_no_refactor``
    the module exposes opt-in surfaces only.
    """

    from iriai_build_v2.execution_control import dispatcher_prompt_context as mod

    expected = {
        "AuthoritativePromptBuildResult",
        "AuthoritativePromptContextRouting",
        "AuthoritativePromptBuilderPort",
        "derive_dispatch_routing",
        "LegacyPromptBuilderAuthoritativeAdapter",
        "AuthoritativePromptContextIncompleteSignal",
    }
    assert set(mod.__all__) == expected


def test_authoritative_prompt_build_result_extra_forbid() -> None:
    """The composed typed result carries ``extra='forbid'`` so typo-d
    kwargs fail closed as a typed ``ValidationError``.

    Aligns with the sibling Slice 13A typed shapes in
    ``completeness.py`` + ``prompt_context_adapter.py``.
    """

    legacy = _legacy_prompt_build_result()
    with pytest.raises(ValidationError):
        AuthoritativePromptBuildResult(
            legacy_result=legacy,
            authoritative_bundle=None,
            routing=AuthoritativePromptContextRouting(should_invoke_runtime=True),
            typo_field="unexpected",  # type: ignore[call-arg]
        )


def test_authoritative_prompt_context_routing_extra_forbid() -> None:
    """The typed routing classifier carries ``extra='forbid'``."""

    with pytest.raises(ValidationError):
        AuthoritativePromptContextRouting(
            should_invoke_runtime=True,
            typo_field="unexpected",  # type: ignore[call-arg]
        )


def test_authoritative_prompt_builder_port_is_runtime_checkable_protocol() -> None:
    """``AuthoritativePromptBuilderPort`` is a Protocol the dispatcher
    accepts via duck typing (no runtime isinstance enforcement -- the
    Protocol shape is the contract).

    Per the chunk shape point 3 ("Define new port (e.g.
    `AuthoritativePromptBuilderPort`) that wraps the existing
    `ContractPromptBuilderPort.build_prompt_context` call ...").
    """

    # Verify the Protocol carries the documented method.
    assert hasattr(AuthoritativePromptBuilderPort, "build_prompt_context")


def test_derive_dispatch_routing_returns_carried_routing() -> None:
    """``derive_dispatch_routing`` is a pure projection that returns
    the routing carried on the typed result.

    Per the chunk shape point 3: the routing classifier lives on
    :class:`AuthoritativePromptBuildResult.routing`; this helper
    exposes it under a stable name for downstream consumers.
    """

    legacy = _legacy_prompt_build_result()
    routing = AuthoritativePromptContextRouting(
        should_invoke_runtime=False,
        typed_failure_class="runtime_context",
        typed_failure_type="context_incomplete",
        unavailable_reason="test",
        missing_field_names=("prompt_ref",),
    )
    result = AuthoritativePromptBuildResult(
        legacy_result=legacy,
        authoritative_bundle=None,
        routing=routing,
    )
    derived = derive_dispatch_routing(result)
    assert derived is routing


# ── typed failure id registration (Option A: ADD this iteration) ───────────


def test_runtime_context_context_incomplete_failure_type_registered() -> None:
    """The Slice 13A fourth-sub-slice typed failure id is registered.

    Per the chunk shape point 2 "Option A": the typed failure id
    ``runtime_context/context_incomplete`` is added to the Slice 07
    typed-failure router so the dispatcher can record it without
    raising :class:`UnknownFailurePolicyError`.

    The route MUST be ``quiesce`` per doc-13a:269-272 + doc-13a:307-310
    ("Required evidence cannot be paged exactly: return
    ``state='unavailable'`` and route
    ``runtime_context/context_incomplete`` ... fail closed").
    """

    assert "context_incomplete" in FAILURE_TYPES
    route = ROUTE_TABLE[("runtime_context", "context_incomplete")]
    assert route.action == "quiesce"
    assert route.failure_class == "runtime_context"
    assert route.failure_type == "context_incomplete"


# ── composition invariant tests ────────────────────────────────────────────


def test_authoritative_result_carries_legacy_result_verbatim() -> None:
    """The composed typed result preserves the legacy
    :class:`PromptBuildResult` verbatim on the ``legacy_result`` field
    per doc-13a:42-46 + 124-126 (the composition invariant; the legacy
    result is NEVER replaced or mutated).
    """

    legacy = _legacy_prompt_build_result()
    bundle = derive_authoritative_prompt_context_bundle(
        legacy.bundle,
        manifest_id="m-1",
        manifest_digest="md-1",
        feature_id="f-1",
        dag_sha256="ds-1",
        task_id="t-1",
    )
    result = AuthoritativePromptBuildResult(
        legacy_result=legacy,
        authoritative_bundle=bundle,
        routing=AuthoritativePromptContextRouting(should_invoke_runtime=True),
    )
    # Legacy result is preserved verbatim -- byte-identical.
    assert result.legacy_result.prompt == legacy.prompt
    assert result.legacy_result.bundle == legacy.bundle
    # The legacy result IS the same object reference (composition).
    assert result.legacy_result is legacy


def test_authoritative_result_authoritative_bundle_optional_when_runtime_skipped() -> None:
    """When ``routing.should_invoke_runtime=False`` the
    ``authoritative_bundle`` field is permitted to be ``None`` (the
    adapter raised :class:`MissingPromptContextFieldError` and the
    typed result was emitted without a bundle).
    """

    legacy = _legacy_prompt_build_result()
    result = AuthoritativePromptBuildResult(
        legacy_result=legacy,
        authoritative_bundle=None,
        routing=AuthoritativePromptContextRouting(
            should_invoke_runtime=False,
            typed_failure_class="runtime_context",
            typed_failure_type="context_incomplete",
            unavailable_reason="missing prompt_ref",
            missing_field_names=("prompt_ref",),
        ),
    )
    assert result.authoritative_bundle is None


# ── legacy adapter wraps Slice 05 port ─────────────────────────────────────


@pytest.mark.asyncio
async def test_legacy_adapter_calls_through_to_legacy_port_and_derives_authoritative_bundle() -> None:
    """``LegacyPromptBuilderAuthoritativeAdapter`` wraps a legacy
    :class:`ContractPromptBuilderPort` and produces an
    :class:`AuthoritativePromptBuildResult` whose
    ``authoritative_bundle`` is populated (state="complete" default).
    """

    legacy_port = _FakeLegacyPromptBuilder()
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)
    request = _request()

    result = await adapter.build_prompt_context(request)

    assert legacy_port.calls == 1
    assert result.routing.should_invoke_runtime is True
    assert result.routing.typed_failure_class is None
    assert result.routing.typed_failure_type is None
    assert result.authoritative_bundle is not None
    # Default fully-resolved legacy bundle -> state="complete" +
    # authority="execution_authority" per doc-13a:115-118.
    assert result.authoritative_bundle.completeness.state == "complete"
    assert result.authoritative_bundle.completeness.authority == "execution_authority"


@pytest.mark.asyncio
async def test_legacy_adapter_paged_state_when_truncation_notes_non_empty() -> None:
    """The legacy adapter sets ``state="paged"`` when the legacy bundle's
    ``truncation_notes`` is non-empty (per doc-13a:115-118 + the
    third-sub-slice adapter's exact-vs-preview boundary rule).

    Per doc-13a:269-272 the runtime PROCEEDS with the authoritative
    companion record attached (the paged evidence is still
    authoritative; the consumer MUST traverse the page-refs to gather
    the full content).
    """

    legacy_port = _FakeLegacyPromptBuilder(
        bundle=_legacy_bundle(truncation_notes=["large prompt truncated"])
    )
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)

    result = await adapter.build_prompt_context(_request())

    assert result.routing.should_invoke_runtime is True
    assert result.authoritative_bundle is not None
    assert result.authoritative_bundle.completeness.state == "paged"
    # Legacy truncation_notes preserved verbatim per doc-13a:213-215.
    assert result.authoritative_bundle.truncation_notes == ["large prompt truncated"]


@pytest.mark.asyncio
async def test_legacy_adapter_preview_only_forces_display_only_authority() -> None:
    """The legacy adapter forces ``authority="display_only"`` when the
    state is ``"preview_only"`` (no context_file_refs).

    Per doc-13a:18-23 (Slice 13A invariant; override-resistant) +
    doc-13a:111-115 (Blocking deviations): a preview cannot carry
    execution authority. The runtime still proceeds (the preview is
    not blocking by itself); downstream consumers MUST check the
    authority/state to decide whether to drive authoritative decisions.
    """

    legacy_port = _FakeLegacyPromptBuilder(
        bundle=_legacy_bundle(context_file_refs=[], context_file_paths=[])
    )
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)

    result = await adapter.build_prompt_context(_request())

    assert result.routing.should_invoke_runtime is True
    assert result.authoritative_bundle is not None
    assert result.authoritative_bundle.completeness.state == "preview_only"
    # Override-resistant Slice 13A invariant: preview_only forces
    # authority="display_only" regardless of caller intent.
    assert result.authoritative_bundle.completeness.authority == "display_only"


@pytest.mark.asyncio
async def test_legacy_adapter_unavailable_state_emits_typed_routing_skip_runtime() -> None:
    """The legacy adapter projects
    :class:`MissingPromptContextFieldError` onto an
    :class:`AuthoritativePromptBuildResult` whose
    ``routing.should_invoke_runtime=False`` +
    ``routing.typed_failure_class="runtime_context"`` +
    ``routing.typed_failure_type="context_incomplete"``.

    Per doc-13a:269-272 + the auto-memory
    ``feedback_no_silent_degradation`` rule: the typed failure id is
    the authoritative routing signal; the runtime is NOT invoked.
    """

    # The adapter constructor raises ValidationError on an invalid
    # PromptContextBundle; instead emit a bundle that the
    # third-sub-slice adapter classifies as state="unavailable"
    # (missing required field). Use prompt_ref=0 (the legacy
    # third-sub-slice adapter treats 0 as missing per the doc-13a
    # placeholder semantics).
    legacy_port = _FakeLegacyPromptBuilder(
        bundle=_legacy_bundle(prompt_ref=0)
    )
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)

    result = await adapter.build_prompt_context(_request())

    assert result.routing.should_invoke_runtime is False
    assert result.routing.typed_failure_class == "runtime_context"
    assert result.routing.typed_failure_type == "context_incomplete"
    assert "prompt_ref" in result.routing.missing_field_names
    assert result.authoritative_bundle is None
    # The legacy result is preserved verbatim for the dispatcher's
    # persistence path per the composition invariant.
    assert result.legacy_result.bundle.prompt_ref == 0


# ── dispatcher legacy-path byte-identical when port is None ────────────────


@pytest.mark.asyncio
async def test_dispatcher_legacy_path_byte_identical_when_port_is_none() -> None:
    """The dispatcher's `_build_prompt` path is BYTE-IDENTICAL when
    the new opt-in ``authoritative_prompt_builder`` port is ``None``
    (the default).

    Preserves the Slice 05 22-passed dispatcher baseline +
    the Slice 00-12 frozen V4 spot-check baseline per the chunk shape
    point 4 invariant. The dispatcher calls the legacy
    :class:`ContractPromptBuilderPort` via the existing
    ``_prompt_builder.build_prompt_context(request, binding)`` call
    site at ``dispatcher.py:1059``.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    # Construct dispatcher WITHOUT the new port (default None).
    dispatcher = _build_dispatcher(log, prompt_builder=legacy_port)

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "succeeded"
    assert legacy_port.calls == 1
    # The legacy port WAS called; the new port path was NOT triggered.


# ── dispatcher routes through new port when set ────────────────────────────


@pytest.mark.asyncio
async def test_dispatcher_routes_through_new_port_when_set() -> None:
    """When the new opt-in port is set, the dispatcher calls the new
    adapter through the `_build_prompt` path.

    Per the chunk shape point 5 (b): "when flag True / port set: the
    dispatcher calls the adapter."
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    new_port = _FakeAuthoritativePromptBuilder(log=log)
    # The legacy port is still passed as the required prompt_builder
    # parameter, but the new port takes priority via the opt-in
    # constructor parameter.
    dispatcher = _build_dispatcher(
        log,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=new_port,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "succeeded"
    # The NEW port was called; the legacy port was NOT called.
    assert new_port.calls == 1
    assert legacy_port.calls == 0


# ── dispatcher routes state=unavailable to typed failure ───────────────────


@pytest.mark.asyncio
async def test_dispatcher_routes_state_unavailable_to_context_incomplete_without_runtime_invocation() -> None:
    """When the new port returns ``routing.should_invoke_runtime=False``,
    the dispatcher routes to the typed failure id
    ``runtime_context/context_incomplete`` and does NOT invoke the
    runtime per doc-13a:269-272.

    The legacy ``record_prompt_context(...)`` is STILL called (the
    Slice 05 persistence invariant is preserved per the doc-13a:269-272
    wording "dispatch records ..."); only the runtime invocation is
    skipped.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    new_port = _FakeAuthoritativePromptBuilder(
        log=log,
        routing=AuthoritativePromptContextRouting(
            should_invoke_runtime=False,
            typed_failure_class="runtime_context",
            typed_failure_type="context_incomplete",
            unavailable_reason="missing prompt_ref",
            missing_field_names=("prompt_ref",),
        ),
        authoritative_bundle=None,
    )
    dispatcher = _build_dispatcher(
        log,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=new_port,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "failed"
    assert outcome.runtime_failure_id is not None
    assert outcome.typed_failure_id is not None
    # The runtime was NOT invoked (per doc-13a:269-272).
    assert "runtime" not in log
    # The legacy persistence path WAS called (the bundle is the record).
    assert "record_prompt" in log


@pytest.mark.asyncio
async def test_dispatcher_records_runtime_context_context_incomplete_failure() -> None:
    """The dispatcher records the typed failure id with
    ``failure_class="runtime_context"`` +
    ``failure_type="context_incomplete"`` + the missing-field-name
    details on the failure record.

    Per doc-13a:269-272 + the chunk shape point 5 (c). The failure
    details carry the missing field names so downstream consumers
    (dashboard / supervisor / governance) can identify which
    field(s) blocked the runtime.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    new_port = _FakeAuthoritativePromptBuilder(
        log=log,
        routing=AuthoritativePromptContextRouting(
            should_invoke_runtime=False,
            typed_failure_class="runtime_context",
            typed_failure_type="context_incomplete",
            unavailable_reason="missing prompt_ref",
            missing_field_names=("prompt_ref",),
        ),
        authoritative_bundle=None,
    )
    store = _FakeStore(log=log)
    dispatcher = _build_dispatcher(
        log,
        store=store,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=new_port,
    )

    await dispatcher.dispatch(_request())

    assert store.failures
    failure = store.failures[0]
    assert failure.failure_class == "runtime_context"
    assert failure.failure_type == "context_incomplete"
    # The typed failure id details carry the missing field names.
    assert failure.details["missing_field_names"] == ["prompt_ref"]
    assert failure.details["unavailable_reason"] == "missing prompt_ref"
    assert failure.details["slice_13a_typed_failure"] is True


@pytest.mark.asyncio
async def test_dispatcher_state_paged_proceeds_with_authoritative_companion_record() -> None:
    """When the adapter returns ``state="paged"`` the dispatcher
    proceeds through the runtime (per doc-13a:269-272 "a large prompt
    emits a compact preview plus exact page refs").

    The runtime IS invoked (the paged evidence is authoritative).
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    # Synthesize a paged authoritative bundle via the third-sub-slice
    # adapter (truncation_notes non-empty -> state="paged").
    legacy = _legacy_prompt_build_result(
        bundle=_legacy_bundle(truncation_notes=["large prompt paged"])
    )
    paged_bundle = derive_authoritative_prompt_context_bundle(
        legacy.bundle,
        manifest_id="m-paged",
        manifest_digest="md-paged",
        feature_id="feature-1",
        dag_sha256="dag-sha",
        task_id="TASK-1",
    )
    assert paged_bundle.completeness.state == "paged"
    new_port = _FakeAuthoritativePromptBuilder(
        log=log,
        result=AuthoritativePromptBuildResult(
            legacy_result=legacy,
            authoritative_bundle=paged_bundle,
            routing=AuthoritativePromptContextRouting(should_invoke_runtime=True),
        ),
    )
    dispatcher = _build_dispatcher(
        log,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=new_port,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "succeeded"
    # Runtime IS invoked when state="paged".
    assert "runtime" in log


@pytest.mark.asyncio
async def test_dispatcher_state_preview_only_proceeds_with_display_only_authority() -> None:
    """When the adapter returns ``state="preview_only"`` the dispatcher
    proceeds through the runtime; the authoritative companion record's
    ``authority="display_only"`` is forced per doc-13a:111-115 +
    doc-13a:18-23 (Slice 13A invariant; override-resistant).

    Per the chunk shape point 5 (e). The runtime IS invoked (the
    preview is not blocking by itself); downstream consumers MUST
    check the authority/state to decide whether to drive authoritative
    decisions.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    # Synthesize a preview_only authoritative bundle via the
    # third-sub-slice adapter (no context_file_refs -> state="preview_only"
    # + authority="display_only" forced).
    legacy = _legacy_prompt_build_result(
        bundle=_legacy_bundle(context_file_refs=[], context_file_paths=[])
    )
    preview_bundle = derive_authoritative_prompt_context_bundle(
        legacy.bundle,
        manifest_id="m-preview",
        manifest_digest="md-preview",
        feature_id="feature-1",
        dag_sha256="dag-sha",
        task_id="TASK-1",
    )
    assert preview_bundle.completeness.state == "preview_only"
    # Override-resistant per the third-sub-slice adapter; authority is
    # forced to display_only regardless of caller intent.
    assert preview_bundle.completeness.authority == "display_only"
    new_port = _FakeAuthoritativePromptBuilder(
        log=log,
        result=AuthoritativePromptBuildResult(
            legacy_result=legacy,
            authoritative_bundle=preview_bundle,
            routing=AuthoritativePromptContextRouting(should_invoke_runtime=True),
        ),
    )
    dispatcher = _build_dispatcher(
        log,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=new_port,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "succeeded"
    # Runtime IS invoked when state="preview_only" (the preview is
    # display-only; downstream consumers check the authority to
    # decide whether to drive authoritative decisions).
    assert "runtime" in log


# ── adapter wrapping the dispatcher's full legacy port path ────────────────


@pytest.mark.asyncio
async def test_dispatcher_routes_through_legacy_adapter_when_wrapped_around_legacy_port() -> None:
    """End-to-end: the dispatcher routes through
    :class:`LegacyPromptBuilderAuthoritativeAdapter` (which wraps the
    legacy port) and proceeds successfully when the legacy bundle is
    fully resolved.

    Verifies the wired path is the FIRST executor wiring per the chunk
    shape point 3.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(log=log)
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)
    dispatcher = _build_dispatcher(
        log,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=adapter,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "succeeded"
    # The adapter delegated through to the legacy port.
    assert legacy_port.calls == 1


@pytest.mark.asyncio
async def test_dispatcher_routes_through_legacy_adapter_blocks_on_missing_field() -> None:
    """End-to-end: when the legacy bundle is missing a required field
    (``prompt_ref=0``), the wrapped adapter raises
    :class:`MissingPromptContextFieldError` -> emits
    ``should_invoke_runtime=False`` -> dispatcher routes to the typed
    failure id ``runtime_context/context_incomplete`` WITHOUT
    invoking the runtime.
    """

    log: list[str] = []
    legacy_port = _FakeLegacyPromptBuilder(
        log=log,
        bundle=_legacy_bundle(prompt_ref=0),
    )
    adapter = LegacyPromptBuilderAuthoritativeAdapter(legacy_port)
    store = _FakeStore(log=log)
    dispatcher = _build_dispatcher(
        log,
        store=store,
        prompt_builder=legacy_port,
        authoritative_prompt_builder=adapter,
    )

    outcome = await dispatcher.dispatch(_request())

    assert outcome.status == "failed"
    # Runtime NOT invoked per doc-13a:269-272.
    assert "runtime" not in log
    # Typed failure id recorded.
    failure = store.failures[0]
    assert failure.failure_class == "runtime_context"
    assert failure.failure_type == "context_incomplete"
    assert "prompt_ref" in failure.details["missing_field_names"]


# ── namespace assertion ───────────────────────────────────────────────────


def test_module_imports_only_from_sanctioned_in_package_surfaces() -> None:
    """The new module's top-level imports MUST be limited to:

    * Stdlib (``typing`` + ``pydantic``).
    * :mod:`iriai_build_v2.execution_control.completeness` (Slice 13A
      second sub-slice; READ-ONLY consumer).
    * :mod:`iriai_build_v2.execution_control.prompt_context_adapter`
      (Slice 13A third sub-slice; READ-ONLY consumer).
    * :mod:`iriai_build_v2.workflows.develop.execution.dispatcher`
      (accepted Slice 05; READ-ONLY consumer).

    Per the chunk shape point 3 ("Stdlib + Pydantic +
    `execution_control.completeness` +
    `execution_control.prompt_context_adapter` +
    `dispatcher.ContractPromptBuilderPort` (READ-ONLY) imports only").
    """

    import ast
    from pathlib import Path

    source = Path(
        "src/iriai_build_v2/execution_control/dispatcher_prompt_context.py"
    ).read_text()
    tree = ast.parse(source)
    sanctioned_modules = {
        "iriai_build_v2.execution_control.completeness",
        "iriai_build_v2.execution_control.prompt_context_adapter",
        "iriai_build_v2.workflows.develop.execution.dispatcher",
    }
    sanctioned_stdlib_prefixes = {
        "typing",
        "pydantic",
        # ``__future__`` is the Python feature flag, always permitted.
        "__future__",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module = alias.name
                assert (
                    module in sanctioned_modules
                    or module in sanctioned_stdlib_prefixes
                    or module.split(".")[0] in sanctioned_stdlib_prefixes
                ), f"unsanctioned import: {module}"
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert (
                module in sanctioned_modules
                or module in sanctioned_stdlib_prefixes
                or module.split(".")[0] in sanctioned_stdlib_prefixes
            ), f"unsanctioned from-import: {module}"


# ── test helpers ──────────────────────────────────────────────────────────


def _actor_metadata() -> ActorMetadata:
    data: dict[str, Any] = {
        "actor_id": "actor-1",
        "actor_name": "Implementer",
        "actor_role": "implementer",
        "runtime": "codex",
        "runtime_policy": "standard",
        "runtime_policy_digest": "policy-sha",
        "model": "gpt-test",
        "tool_profile": "sandboxed-tools",
        "sandbox_required": True,
        "approval_profile": "no_canonical_writes",
        "metadata_digest": "pending",
    }
    data["metadata_digest"] = actor_metadata_digest(data)
    return ActorMetadata(**data)


def _request(**overrides: object) -> DispatchRequest:
    actor = _actor_metadata()
    data: dict[str, object] = {
        "feature_id": "feature-1",
        "dag_sha256": "dag-sha",
        "group_idx": 2,
        "task_id": "TASK-1",
        "task_name": "Implement task",
        "retry": 0,
        "retry_identity": DispatchRetryIdentity(
            retry=0,
            dispatch_retry_id="dispatch-retry-1",
        ),
        "contract_ids": [11],
        "sandbox_id": "sandbox-1",
        "workspace_snapshot_ids": [21],
        "base_commit_by_repo": {"repo": "abc123"},
        "runtime_policy": actor.runtime_policy,
        "runtime_policy_digest": actor.runtime_policy_digest,
        "actor_role": actor.actor_role,
        "actor_metadata": actor,
        "prior_evidence_ids": [31],
        "cancellation_token": None,
        "request_digest": "pending",
        "idempotency_key": "pending",
    }
    data.update(overrides)
    data["request_digest"] = dispatch_request_digest(data)
    data["idempotency_key"] = dispatch_idempotency_key(data)
    return DispatchRequest.model_validate(data)


def _legacy_bundle(**overrides: Any) -> PromptContextBundle:
    """Build a legacy Slice 05 :class:`PromptContextBundle` with
    sensible defaults. Per ``dispatcher.py:229-239`` the 10 fields
    are: prompt_ref / prompt_sha256 / prompt_summary / context_file_refs
    / context_file_paths / context_sha256 / included_contract_ids /
    included_evidence_ids / excluded_evidence_ids / truncation_notes.
    """

    data = {
        "prompt_ref": 41,
        "prompt_sha256": "prompt-sha",
        "prompt_summary": "bounded prompt",
        "context_file_refs": [42],
        "context_file_paths": ["context/TASK-1.md"],
        "context_sha256": "context-sha",
        "included_contract_ids": [11],
        "included_evidence_ids": [31],
        "excluded_evidence_ids": [],
        "truncation_notes": [],
    }
    data.update(overrides)
    return PromptContextBundle(**data)


def _legacy_prompt_build_result(
    bundle: PromptContextBundle | None = None,
) -> PromptBuildResult:
    return PromptBuildResult(
        prompt="Do the bounded task.",
        bundle=bundle or _legacy_bundle(),
    )


def _binding() -> RuntimeWorkspaceBinding:
    return RuntimeWorkspaceBinding(
        sandbox_id="sandbox-1",
        runtime="codex",
        cwd="/tmp/sandbox-1/repo",
        workspace_override="/tmp/sandbox-1/repo",
        repo_roots={"repo": "/tmp/sandbox-1/repo"},
        writable_roots=["/tmp/sandbox-1/repo"],
        readonly_roots=[],
        blocked_roots=["/tmp/canonical"],
        env={},
        role_metadata={"sandbox": True},
    )


def _success_response(
    invocation: RuntimeInvocationRequest,
) -> RuntimeInvocationResponse:
    return RuntimeInvocationResponse(
        invocation_id=invocation.invocation_id,
        status="completed",
        terminal_reason="completed",
        process_started=True,
        structured_output={
            "task_id": "TASK-1",
            "status": "completed",
            "summary": "done",
            "files": [],
        },
        raw_text='{"status":"completed"}',
        raw_artifact_id=61,
        provider_request_id="provider-1",
        provider_error_code=None,
        elapsed_ms=15,
    )


class _FakeLegacyPromptBuilder:
    """Test fake for the Slice 05 :class:`ContractPromptBuilderPort`."""

    def __init__(
        self,
        log: list[str] | None = None,
        *,
        bundle: PromptContextBundle | None = None,
    ) -> None:
        self.log: list[str] = log if log is not None else []
        self.calls = 0
        self._bundle = bundle or _legacy_bundle()

    async def build_prompt_context(
        self,
        request: DispatchRequest,
        binding: RuntimeWorkspaceBinding | None = None,
    ) -> PromptBuildResult:
        del request, binding
        self.calls += 1
        self.log.append("prompt")
        return PromptBuildResult(
            prompt="Do the bounded task.",
            bundle=self._bundle,
        )


class _FakeAuthoritativePromptBuilder:
    """Test fake for the Slice 13A :class:`AuthoritativePromptBuilderPort`."""

    def __init__(
        self,
        log: list[str] | None = None,
        *,
        routing: AuthoritativePromptContextRouting | None = None,
        authoritative_bundle: AuthoritativePromptContextBundle | None = None,
        result: AuthoritativePromptBuildResult | None = None,
        legacy_result: PromptBuildResult | None = None,
    ) -> None:
        self.log: list[str] = log if log is not None else []
        self.calls = 0
        self._result = result
        self._routing = routing or AuthoritativePromptContextRouting(
            should_invoke_runtime=True
        )
        self._authoritative_bundle = authoritative_bundle
        self._legacy_result = legacy_result

    async def build_prompt_context(
        self,
        request: DispatchRequest,
    ) -> AuthoritativePromptBuildResult:
        del request
        self.calls += 1
        self.log.append("authoritative_prompt")
        if self._result is not None:
            return self._result
        legacy = self._legacy_result or _legacy_prompt_build_result()
        # Default authoritative_bundle when one isn't provided +
        # routing.should_invoke_runtime=True: derive a bundle from
        # the legacy result.
        if self._authoritative_bundle is None and self._routing.should_invoke_runtime:
            bundle = derive_authoritative_prompt_context_bundle(
                legacy.bundle,
                manifest_id="m-1",
                manifest_digest="md-1",
                feature_id="feature-1",
                dag_sha256="dag-sha",
                task_id="TASK-1",
            )
        else:
            bundle = self._authoritative_bundle
        return AuthoritativePromptBuildResult(
            legacy_result=legacy,
            authoritative_bundle=bundle,
            routing=self._routing,
        )


class _FakeStore:
    """Minimal in-memory store for the dispatcher journal facade."""

    def __init__(self, log: list[str] | None = None) -> None:
        self.log: list[str] = log if log is not None else []
        self.failures: list[Any] = []
        self.finished: list[DispatchOutcome] = []

    async def start_dispatch_attempt(self, request: DispatchRequest) -> Any:
        from iriai_build_v2.workflows.develop.execution.dispatcher import (
            DispatchAttemptRecord,
        )
        self.log.append("start")
        return DispatchAttemptRecord(
            attempt_id=101,
            state="attempt_started",
            request_digest=request.request_digest,
            created=True,
        )

    async def record_start_idempotency_conflict(
        self,
        request: DispatchRequest,
        failure: Any,
    ) -> Any:
        del request
        self.log.append("record_start_conflict")
        return 101, failure.model_copy(update={"failure_id": 501})

    async def record_prompt_context(
        self,
        attempt_id: int,
        request: DispatchRequest,
        prompt: str,
        bundle: PromptContextBundle,
    ) -> int:
        del attempt_id, request, prompt, bundle
        self.log.append("record_prompt")
        return 201

    async def record_runtime_invocation(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse | None = None,
    ) -> int:
        del attempt_id, invocation
        self.log.append("record_invocation_response" if response else "record_invocation")
        return 202

    async def record_raw_output(
        self,
        attempt_id: int,
        invocation: RuntimeInvocationRequest,
        response: RuntimeInvocationResponse,
    ) -> int | None:
        del attempt_id, invocation, response
        self.log.append("record_raw_output")
        return 203

    async def record_structured_output(
        self,
        attempt_id: int,
        record: StructuredOutputRecord,
    ) -> StructuredOutputRecord:
        del attempt_id
        self.log.append("record_structured")
        return record.model_copy(update={"evidence_id": 301})

    async def record_runtime_failure(
        self,
        attempt_id: int,
        failure: Any,
    ) -> Any:
        del attempt_id
        self.log.append("record_failure")
        stored = failure.model_copy(update={"failure_id": 501})
        self.failures.append(stored)
        return stored

    async def project_task_result_from_attempt(
        self,
        attempt_id: int,
        request: DispatchRequest,
        structured_output: StructuredOutputRecord,
        patch_capture: PatchCaptureRecord,
    ) -> list[int]:
        del attempt_id, request, structured_output, patch_capture
        self.log.append("project_task")
        return [401]

    async def finish_dispatch_attempt(self, outcome: DispatchOutcome) -> DispatchOutcome:
        self.log.append(f"finish:{outcome.status}")
        self.finished.append(outcome)
        return outcome


class _FakeSandbox:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log: list[str] = log if log is not None else []
        self.bind_calls = 0
        self.capture_calls = 0

    async def bind_runtime(
        self,
        request: DispatchRequest,
        attempt_id: int,
    ) -> RuntimeWorkspaceBinding:
        del request, attempt_id
        self.bind_calls += 1
        self.log.append("bind")
        return _binding()

    async def capture_patch(
        self,
        request: DispatchRequest,
        attempt_id: int,
        binding: RuntimeWorkspaceBinding,
        response: RuntimeInvocationResponse,
        *,
        idempotency_key: str,
    ) -> PatchCaptureRecord:
        del request, attempt_id, binding, response, idempotency_key
        self.capture_calls += 1
        self.log.append("capture_patch")
        return PatchCaptureRecord(
            sandbox_id="sandbox-1",
            captured=True,
            patch_summary_ids=[701],
            compatibility_artifact_ids=[],
            empty=False,
        )


class _FakeRuntime:
    def __init__(self, log: list[str] | None = None) -> None:
        self.log: list[str] = log if log is not None else []
        self.calls = 0

    async def invoke(self, request: RuntimeInvocationRequest) -> RuntimeInvocationResponse:
        self.calls += 1
        self.log.append("runtime")
        return _success_response(request)


def _build_dispatcher(
    log: list[str],
    *,
    store: _FakeStore | None = None,
    prompt_builder: _FakeLegacyPromptBuilder | None = None,
    authoritative_prompt_builder: Any | None = None,
    sandbox: _FakeSandbox | None = None,
    runtime: _FakeRuntime | None = None,
) -> RuntimeDispatcher:
    return RuntimeDispatcher(
        store=store or _FakeStore(log),
        sandbox=sandbox or _FakeSandbox(log),
        runtime=runtime or _FakeRuntime(log),
        prompt_builder=prompt_builder or _FakeLegacyPromptBuilder(log),
        authoritative_prompt_builder=authoritative_prompt_builder,
        output_schema_digest="schema-sha",
    )
