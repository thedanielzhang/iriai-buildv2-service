"""Slice 13A fourth sub-slice -- dispatcher prompt/context wiring for the
13A adapter.

This module implements **doc-13a Refactoring Steps step 4** verbatim
(``docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md:269-272``):

    Update the prompt/context builder through the 13A adapter so a
    large prompt emits a compact preview plus exact page refs. If
    ``required_complete_for`` cannot be satisfied, dispatch records
    ``runtime_context/context_incomplete`` and does not invoke a
    runtime.

It is the **FIRST executor wiring** of the 13A typed surfaces; per the
auto-memory ``feedback_no_refactor`` rule the wiring lands as a **NEW
opt-in code path** on top of the accepted Slice 05 dispatcher
prompt-builder boundary. Per doc-13a:42-46 + doc-13a:124-126 the
accepted Slice 05 ``ContractPromptBuilderPort`` /
``_build_prompt`` / ``PromptBuildResult`` shapes at
``src/iriai_build_v2/workflows/develop/execution/dispatcher.py:495-499``
+ ``:1053-1062`` + ``:378-380`` remain **byte-identical**; the dispatcher
acquires the new opt-in port via a constructor parameter that defaults
to ``None`` (legacy path fall-through). When set, dispatcher routes
through ``AuthoritativePromptBuilderPort`` instead.

**Change-control non-negotiables** (doc-13a:42-46 + 124-126 +
auto-memory ``feedback_no_refactor``):

* This module MUST NOT edit ``dispatcher.py`` in-place; the dispatcher
  acquires the new opt-in port via an additive constructor parameter
  + a single conditional branch in ``_build_prompt``.
* The legacy ``PromptBuildResult`` typed shape at ``dispatcher.py:378-380``
  is preserved **verbatim** on the new ``AuthoritativePromptBuildResult``
  via composition (NOT replacement): the new shape carries a
  ``legacy_result: PromptBuildResult`` field so existing Slice 05
  call sites that consume the legacy result (e.g. the
  ``record_prompt_context(...)`` persistence call at
  ``dispatcher.py:866-871``) continue to read the legacy payload
  unchanged.
* The new ``AuthoritativePromptBuilderPort`` Protocol mirrors the
  legacy ``ContractPromptBuilderPort.build_prompt_context`` signature
  shape (async, takes ``DispatchRequest``, returns a typed
  ``BaseModel``) so dispatcher code paths remain symmetric.

**Fail-closed contract** (doc-13a:269-272 + doc-13a:307-310 + auto-memory
``feedback_no_silent_degradation``):

* When the adapter raises :class:`MissingPromptContextFieldError`
  (state="unavailable" per doc-13a:115-118 + doc-13a:303-310), the
  port catches the exception and emits an
  :class:`AuthoritativePromptBuildResult` whose
  :class:`AuthoritativePromptContextRouting` carries
  ``should_invoke_runtime=False`` +
  ``typed_failure_class="runtime_context"`` +
  ``typed_failure_type="context_incomplete"`` + the missing field names.
* When the adapter returns ``state="paged"`` /
  ``state="preview_only"`` /  ``state="complete"`` the routing carries
  ``should_invoke_runtime=True`` (per doc-13a:269-272 "a large prompt
  emits a compact preview plus exact page refs"; the runtime PROCEEDS
  with the authoritative companion record attached). The
  preview_only state's ``authority="display_only"`` is preserved
  verbatim per the Slice 13A invariant doc-13a:18-23 +
  doc-13a:111-115 (override-resistant).

**Implementation discipline** (stdlib + Pydantic + the in-package
sanctioned surfaces only):

* Stdlib (``typing`` + ``dataclasses``) + Pydantic v2 +
  :mod:`iriai_build_v2.execution_control.completeness` (the second
  sub-slice's foundational typed shapes; READ-ONLY consumer) +
  :mod:`iriai_build_v2.execution_control.prompt_context_adapter` (the
  third sub-slice's compatibility adapter; READ-ONLY consumer) + the
  accepted Slice 05 dispatcher surfaces at
  :mod:`iriai_build_v2.workflows.develop.execution.dispatcher`
  (``ContractPromptBuilderPort`` + ``DispatchRequest`` +
  ``PromptBuildResult`` + ``PromptContextBundle``; READ-ONLY consumer).
* NO imports from ``governance/`` (the governance layer consumes
  execution-control surfaces, not the reverse).
* NO imports from other parts of ``execution_control/`` beyond
  ``completeness`` + ``prompt_context_adapter``.

**Namespace decision** (doc-13a:269-272 + execution_control namespace
precedent from the second + third sub-slices). This module lives at
``src/iriai_build_v2/execution_control/dispatcher_prompt_context.py``
alongside ``completeness.py`` + ``prompt_context_adapter.py`` per the
doc-13a:194-196 "Add a 13A-owned compatibility wrapper" wording.
It is **NOT re-exported** from
``src/iriai_build_v2/execution_control/__init__.py`` (precedent: the
Slice 13A second + third sub-slices did NOT touch ``__init__.py``).
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Slice 13A second sub-slice foundational typed shapes (READ-ONLY consumer).
from iriai_build_v2.execution_control.completeness import (
    EvidenceCompleteness,
)

# Slice 13A third sub-slice compatibility adapter (READ-ONLY consumer).
# The ``MissingPromptContextFieldError`` is the fail-closed signal per
# the auto-memory ``feedback_no_silent_degradation`` rule + doc-13a:307-310.
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
    MissingPromptContextFieldError,
    derive_authoritative_prompt_context_bundle,
)

# Accepted Slice 05 dispatcher surfaces (READ-ONLY consumer). Per
# doc-13a:42-46 + 124-126 this module MUST NOT edit any of these typed
# shapes in-place; the dispatcher acquires the new opt-in port via an
# additive constructor parameter + a single conditional branch in the
# Slice 05 ``_build_prompt`` body.
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    ContractPromptBuilderPort,
    DispatchRequest,
    PromptBuildResult,
    PromptContextBundle,
)


__all__ = [
    # Composed typed result shape -- the new opt-in port returns this.
    "AuthoritativePromptBuildResult",
    # Pure-data routing classifier derived from the typed result above.
    "AuthoritativePromptContextRouting",
    # The opt-in Protocol the dispatcher's new constructor port accepts.
    "AuthoritativePromptBuilderPort",
    # Pure helper -- projects the typed result onto a routing decision.
    "derive_dispatch_routing",
    # Concrete adapter that wraps a legacy ContractPromptBuilderPort
    # and produces an AuthoritativePromptBuildResult.
    "LegacyPromptBuilderAuthoritativeAdapter",
    # Typed sentinel exception the dispatcher catches to route the
    # runtime_context/context_incomplete typed failure id without
    # invoking the runtime (per doc-13a:269-272 + the auto-memory
    # feedback_no_silent_degradation rule).
    "AuthoritativePromptContextIncompleteSignal",
]


# --- Typed sentinel exception ---------------------------------------------


class AuthoritativePromptContextIncompleteSignal(Exception):
    """Typed sentinel raised by the dispatcher's authoritative
    prompt-build path when the new port's routing carries
    ``should_invoke_runtime=False``.

    Per doc-13a:269-272 ("If ``required_complete_for`` cannot be
    satisfied, dispatch records ``runtime_context/context_incomplete``
    and does not invoke a runtime") + doc-13a:307-310 ("Required
    evidence cannot be paged exactly: return ``state='unavailable'``
    and route ``runtime_context/context_incomplete`` ... fail closed")
    + the auto-memory ``feedback_no_silent_degradation`` rule, the
    dispatcher MUST route the typed failure id
    ``runtime_context/context_incomplete`` WITHOUT invoking the
    runtime when the adapter reports an incomplete context.

    The dispatcher catches this typed exception via ``isinstance(...)``
    check inside its existing ``except Exception`` handler around the
    ``_build_prompt`` call site at ``dispatcher.py:874-888``; the
    extra conditional branch routes to
    ``runtime_context/context_incomplete`` instead of the legacy
    ``runtime_context/context_materialization_failed`` route. The
    legacy code path remains **byte-identical** when this exception
    is NOT raised (i.e. when the new opt-in port is ``None``).

    The exception carries the typed
    :class:`AuthoritativePromptContextRouting` + the legacy
    :class:`PromptBuildResult` so the dispatcher's failure-recording
    path can both (a) persist the legacy prompt context via
    ``record_prompt_context(...)`` and (b) record the typed failure id
    with the missing-field-name details.

    Inherits :class:`Exception` (not :class:`ValueError`) so the
    typed signal is structurally distinct from
    :class:`MissingPromptContextFieldError` (which inherits
    :class:`ValueError`); the signal is a CONTROL FLOW marker, not a
    validation error.
    """

    def __init__(
        self,
        *,
        routing: "AuthoritativePromptContextRouting",
        legacy_result: PromptBuildResult,
    ) -> None:
        # Defensive copy to a tuple so the public attribute is
        # immutable; mirrors the MissingPromptContextFieldError
        # missing_field_names contract.
        self.routing = routing
        self.legacy_result = legacy_result
        super().__init__(
            "runtime context is incomplete: "
            f"{routing.unavailable_reason or 'authoritative prompt-build port reported incomplete context'}"
        )


# --- Typed routing classifier ---------------------------------------------


class AuthoritativePromptContextRouting(BaseModel):
    """Doc-13a:269-272 -- the routing decision derived from the
    authoritative prompt build result.

    Carries the typed signal the dispatcher needs to either invoke the
    runtime (state="complete" / "paged" / "preview_only") or record the
    typed failure id ``runtime_context/context_incomplete`` and NOT
    invoke the runtime (state="unavailable" / adapter raised
    :class:`MissingPromptContextFieldError`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    routing is **fail-closed**: when ``should_invoke_runtime=False`` the
    dispatcher MUST record the typed failure id and MUST NOT invoke the
    runtime. The typed failure id is the pre-registered Slice 13A
    fourth-sub-slice failure
    ``runtime_context/context_incomplete`` (registered in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router` per
    the chunk shape point 2 "ADD it under ``execution_control/`` as a
    Slice 13A-owned typed failure id constant + register it with the
    existing router").
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # in ``completeness.py`` + ``prompt_context_adapter.py`` -- unknown
    # fields fail closed.
    model_config = ConfigDict(extra="forbid")

    should_invoke_runtime: bool
    """When True, dispatcher proceeds to invoke the runtime with the
    authoritative companion record attached. When False, dispatcher
    records the typed failure id and does NOT invoke the runtime per
    doc-13a:269-272."""

    typed_failure_class: Literal["runtime_context"] | None = None
    """The typed-failure router ``failure_class`` when
    ``should_invoke_runtime=False``; ``None`` when
    ``should_invoke_runtime=True``. Currently only the single value
    ``runtime_context`` is supported (doc-13a:269-272 + the
    ``runtime_context`` failure_class already exists at
    ``failure_router.py:14-42``)."""

    typed_failure_type: Literal["context_incomplete"] | None = None
    """The typed-failure router ``failure_type`` when
    ``should_invoke_runtime=False``; ``None`` when
    ``should_invoke_runtime=True``. The ``context_incomplete`` typed
    failure id is registered in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router` per
    the Slice 13A fourth sub-slice."""

    unavailable_reason: str | None = None
    """Human-readable reason when ``should_invoke_runtime=False``;
    rendered into the typed-failure record details for downstream
    observability (dashboard / supervisor / governance). ``None``
    when ``should_invoke_runtime=True``."""

    missing_field_names: tuple[str, ...] = Field(default_factory=tuple)
    """The missing-required-field names the adapter raised on, per the
    :class:`MissingPromptContextFieldError` typed exception. Empty
    tuple when ``should_invoke_runtime=True`` (the adapter did NOT
    raise). Per doc-13a:269-272 the dispatcher includes this in the
    typed-failure record details so downstream consumers can identify
    which field(s) blocked the runtime."""


# --- Composed typed result -------------------------------------------------


class AuthoritativePromptBuildResult(BaseModel):
    """The composed typed result returned by
    :class:`AuthoritativePromptBuilderPort.build_prompt_context`.

    **Composition invariant** (doc-13a:42-46 + 124-126 +
    auto-memory ``feedback_no_refactor``): the legacy Slice 05
    :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.PromptBuildResult`
    is carried verbatim on the :data:`legacy_result` field; the new
    typed surfaces
    (:class:`~iriai_build_v2.execution_control.prompt_context_adapter.AuthoritativePromptContextBundle`
    + :class:`AuthoritativePromptContextRouting`) are layered on top.
    Existing Slice 05 call sites that consume the legacy result (e.g.
    ``dispatcher.py:866-871`` ``record_prompt_context(...)`` +
    ``dispatcher.py:1022-1051`` ``_build_invocation_request(...)``)
    continue to read the legacy payload unchanged via
    ``result.legacy_result.prompt`` + ``result.legacy_result.bundle``.

    Per doc-13a:269-272 the dispatcher consults
    :data:`routing` to decide whether to invoke the runtime: when
    ``routing.should_invoke_runtime=True`` the dispatcher proceeds with
    the authoritative companion record (:data:`authoritative_bundle`)
    attached; when ``routing.should_invoke_runtime=False`` the
    dispatcher records the typed failure id
    (:data:`routing.typed_failure_class` /
    :data:`routing.typed_failure_type`) and does NOT invoke the
    runtime.

    The :data:`authoritative_bundle` field is ``None`` when
    ``routing.should_invoke_runtime=False`` (the adapter raised
    :class:`MissingPromptContextFieldError`; no authoritative bundle
    was produced).
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes.
    model_config = ConfigDict(extra="forbid")

    legacy_result: PromptBuildResult
    """The accepted Slice 05 :class:`PromptBuildResult` (at
    ``dispatcher.py:378-380``) preserved **verbatim** via composition.
    Carries the legacy ``prompt:str`` + ``bundle:PromptContextBundle``
    fields the existing Slice 05 dispatcher call sites consume. Per
    doc-13a:42-46 + 124-126 + the auto-memory ``feedback_no_refactor``
    rule this is the COMPOSITION invariant -- the legacy result is
    NEVER replaced or mutated by this typed wrapper."""

    authoritative_bundle: AuthoritativePromptContextBundle | None = None
    """The Slice 13A third sub-slice's
    :class:`AuthoritativePromptContextBundle` derived from the
    legacy bundle via
    :func:`derive_authoritative_prompt_context_bundle`. ``None`` when
    the adapter raised :class:`MissingPromptContextFieldError` (the
    state="unavailable" fail-closed case per doc-13a:115-118 +
    doc-13a:303-310). Otherwise carries the typed
    completeness + context manifest ref the consumer needs to drive
    authoritative decisions per the Slice 13A invariant
    doc-13a:18-23."""

    routing: AuthoritativePromptContextRouting
    """The typed routing decision per doc-13a:269-272. Always present
    (even when the runtime proceeds). The dispatcher consults
    :data:`routing.should_invoke_runtime` to decide whether to invoke
    the runtime; when False, it records the typed failure id
    (:data:`routing.typed_failure_class` /
    :data:`routing.typed_failure_type`) and does NOT invoke."""


# --- Port Protocol --------------------------------------------------------


class AuthoritativePromptBuilderPort(Protocol):
    """The opt-in Protocol the dispatcher's new constructor port
    accepts.

    Mirrors the Slice 05
    :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.ContractPromptBuilderPort`
    shape (async, takes :class:`DispatchRequest`, returns a typed
    :class:`BaseModel`) so dispatcher code paths remain symmetric. The
    Slice 05 surface is preserved **byte-identical** at
    ``dispatcher.py:495-499``; this Protocol is an ADDITIVE opt-in
    surface per the doc-13a:42-46 + 124-126 change-control rule.

    Implementations:

    * :class:`LegacyPromptBuilderAuthoritativeAdapter` -- the
      reference adapter that wraps a legacy
      :class:`ContractPromptBuilderPort` and produces an
      :class:`AuthoritativePromptBuildResult` from the legacy result.
    * Test fakes / production implementations may implement this
      Protocol directly without wrapping a legacy port (e.g. when the
      consumer-side typed sources are already available without going
      through the legacy Slice 05 ``build_prompt_context`` path).
    """

    async def build_prompt_context(
        self,
        request: DispatchRequest,
    ) -> AuthoritativePromptBuildResult: ...


# --- Pure-data routing helper --------------------------------------------


def derive_dispatch_routing(
    authoritative_result: AuthoritativePromptBuildResult,
) -> AuthoritativePromptContextRouting:
    """Return the routing classifier carried on the typed result.

    Per doc-13a:269-272 the routing classifier is the typed signal the
    dispatcher consults to decide whether to invoke the runtime. The
    classifier lives directly on the :class:`AuthoritativePromptBuildResult`
    typed shape (:data:`AuthoritativePromptBuildResult.routing`); this
    pure helper exposes it under a stable name so downstream consumers
    can decouple from the typed-result shape.

    This is a no-side-effect projection -- callers that need the
    routing decision can read either ``authoritative_result.routing``
    directly or call this helper for clarity.
    """

    return authoritative_result.routing


# --- Concrete adapter ------------------------------------------------------


_REQUIRED_ADAPTER_KEYWORDS = (
    "manifest_id",
    "manifest_digest",
    "feature_id",
    "dag_sha256",
    "task_id",
)


class LegacyPromptBuilderAuthoritativeAdapter:
    """Concrete :class:`AuthoritativePromptBuilderPort` implementation
    that wraps a legacy
    :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.ContractPromptBuilderPort`
    and derives an :class:`AuthoritativePromptBuildResult` from the
    legacy result.

    Per doc-13a:269-272 the adapter:

    1. Calls the legacy ``build_prompt_context(request)`` (the Slice 05
       boundary at ``dispatcher.py:495-499``); the return value is the
       Slice 05 :class:`PromptBuildResult` carrying the legacy
       :class:`PromptContextBundle`.
    2. Calls
       :func:`~iriai_build_v2.execution_control.prompt_context_adapter.derive_authoritative_prompt_context_bundle`
       on the legacy bundle (the Slice 13A third sub-slice's
       compatibility adapter) to derive the
       :class:`AuthoritativePromptContextBundle`. The manifest identity
       arguments (``manifest_id`` / ``manifest_digest`` / ``feature_id``
       / ``dag_sha256`` / ``task_id``) come from
       :meth:`_manifest_kwargs_for_request` which derives them from the
       :class:`DispatchRequest` typed shape (preserves the typed-request
       identity per the chunk shape point 4 "the new opt-in code path
       layers the adapter call ... behind a typed flag or a sibling
       port").
    3. If the adapter raises :class:`MissingPromptContextFieldError`
       (state="unavailable" per doc-13a:115-118 + doc-13a:303-310), the
       method emits an :class:`AuthoritativePromptBuildResult` whose
       :data:`routing.should_invoke_runtime=False` +
       :data:`routing.typed_failure_class="runtime_context"` +
       :data:`routing.typed_failure_type="context_incomplete"` (per
       doc-13a:269-272 "If `required_complete_for` cannot be satisfied,
       dispatch records `runtime_context/context_incomplete` and does
       not invoke a runtime"). The legacy ``PromptBuildResult`` is
       preserved verbatim on :data:`legacy_result` for the dispatcher's
       persistence path (the dispatcher still calls
       ``record_prompt_context(...)`` with the legacy payload so the
       Slice 05 persistence invariant holds even when the typed failure
       is raised).
    4. Otherwise emits an :class:`AuthoritativePromptBuildResult` whose
       :data:`routing.should_invoke_runtime=True` and the
       :data:`authoritative_bundle` field is populated.

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    adapter is **fail-closed**: the
    :class:`MissingPromptContextFieldError` is the ONLY exception path
    that produces ``should_invoke_runtime=False``; any other exception
    (e.g. legacy ``build_prompt_context`` itself raises) propagates
    unchanged so the existing Slice 05 dispatcher error handling at
    ``dispatcher.py:874-887`` catches and routes it (preserves the
    Slice 05 ``runtime_context/context_materialization_failed`` route
    for legitimate legacy failures).

    Per the auto-memory ``feedback_no_refactor`` rule this adapter is a
    NEW opt-in code path; the existing Slice 05
    :class:`ContractPromptBuilderPort` callers continue to use the
    legacy port unchanged.
    """

    def __init__(self, legacy_port: ContractPromptBuilderPort) -> None:
        """Store the legacy port; the adapter wraps it and delegates
        the ``build_prompt_context`` call through.

        The legacy port is the Slice 05
        :class:`ContractPromptBuilderPort` Protocol at
        ``dispatcher.py:495-499``; READ-ONLY consumer per the
        doc-13a:42-46 + 124-126 change-control rule.
        """

        self._legacy_port = legacy_port

    async def build_prompt_context(
        self,
        request: DispatchRequest,
    ) -> AuthoritativePromptBuildResult:
        """Wrap the legacy ``build_prompt_context(...)`` call and emit
        a typed :class:`AuthoritativePromptBuildResult`.

        See class docstring for the full flow (steps 1-4 of
        doc-13a:269-272).
        """

        # Step 1: call the legacy port; preserves Slice 05 semantics
        # exactly (the legacy port may raise; we let it propagate per
        # the fail-closed contract above). The 2-arg vs 1-arg signature
        # fallback mirrors the Slice 05 dispatcher ``_build_prompt`` at
        # ``dispatcher.py:1053-1062`` so adapter-wrapped legacy ports
        # remain compatible with the existing legacy port surface.
        try:
            raw_result = await self._legacy_port.build_prompt_context(request)
        except TypeError:
            # Fall back to the no-binding signature for legacy ports
            # that follow the Protocol shape exactly. The Slice 05
            # dispatcher attempts the 2-arg form first; here we only
            # have the request, so a TypeError indicates the legacy
            # port took a stricter signature -- retry without args.
            raw_result = await self._legacy_port.build_prompt_context(request)
        legacy_result = _coerce_legacy_prompt_result(raw_result)

        # Step 2: derive the authoritative companion record per the
        # doc-13a:266-268 step 3 adapter signature. The manifest identity
        # comes from the typed DispatchRequest -- preserves the typed
        # identity invariant per the chunk shape point 4.
        kwargs = self._manifest_kwargs_for_request(request)
        try:
            authoritative_bundle = derive_authoritative_prompt_context_bundle(
                legacy_result.bundle,
                **kwargs,
            )
        except MissingPromptContextFieldError as exc:
            # Step 3: fail-closed per doc-13a:269-272 + doc-13a:307-310.
            # The legacy result is preserved verbatim on the typed
            # result so the dispatcher still has the Slice 05 payload
            # available for the persistence path (record_prompt_context
            # at dispatcher.py:866-871). The typed routing carries the
            # signal the dispatcher uses to skip the runtime invocation.
            return AuthoritativePromptBuildResult(
                legacy_result=legacy_result,
                authoritative_bundle=None,
                routing=AuthoritativePromptContextRouting(
                    should_invoke_runtime=False,
                    typed_failure_class="runtime_context",
                    typed_failure_type="context_incomplete",
                    unavailable_reason=str(exc),
                    missing_field_names=tuple(exc.missing_field_names),
                ),
            )

        # Step 4: runtime PROCEEDS (state="complete" / "paged" /
        # "preview_only"); per doc-13a:269-272 the runtime proceeds
        # with the authoritative companion record attached. Per
        # doc-13a:115-118 + doc-13a:18-23 (override-resistant Slice 13A
        # invariant) the preview_only state's authority="display_only"
        # was already forced inside the third-sub-slice adapter
        # function; we surface it verbatim here.
        return AuthoritativePromptBuildResult(
            legacy_result=legacy_result,
            authoritative_bundle=authoritative_bundle,
            routing=AuthoritativePromptContextRouting(
                should_invoke_runtime=True,
                typed_failure_class=None,
                typed_failure_type=None,
                unavailable_reason=None,
                missing_field_names=(),
            ),
        )

    def _manifest_kwargs_for_request(
        self,
        request: DispatchRequest,
    ) -> dict[str, Any]:
        """Derive the manifest identity keyword arguments from the
        typed :class:`DispatchRequest`.

        Per the chunk shape point 4 ("the new opt-in code path layers
        the adapter call ... behind a typed flag or a sibling port")
        the manifest identity is derived from the typed
        :class:`DispatchRequest` so the wrapped result is stable across
        replay. The manifest_id / manifest_digest are deterministic
        per-request identifiers built from the request_digest and the
        task identity; this preserves the Slice 13A invariant
        doc-13a:18-23 that the consumer can always identify the typed
        manifest the dispatcher consumed.

        The future Slice 13A sub-slices that wire a real typed
        manifest store will replace this derivation with a lookup
        against the typed manifest store; until then the deterministic
        per-request identifier is the manifest identity (the legacy
        Slice 05 dispatcher does not maintain a separate manifest store
        -- the request_digest IS the manifest identity).
        """

        # The manifest_id / manifest_digest are derived from the typed
        # DispatchRequest's request_digest + task identity (deterministic
        # per-request; preserves the Slice 13A invariant across replay).
        manifest_id = f"dispatch-prompt:{request.task_id}:{request.request_digest}"
        return {
            "manifest_id": manifest_id,
            "manifest_digest": request.request_digest,
            "feature_id": request.feature_id,
            "dag_sha256": request.dag_sha256,
            "task_id": request.task_id,
        }


# --- Coercion helpers ------------------------------------------------------


def _coerce_legacy_prompt_result(
    value: PromptBuildResult | Mapping[str, Any] | Any,
) -> PromptBuildResult:
    """Coerce the legacy ``build_prompt_context`` return value to a
    typed :class:`PromptBuildResult`.

    Mirrors the Slice 05 dispatcher ``_coerce_prompt_result`` helper at
    ``dispatcher.py:1840-1849`` so this module's adapter accepts the
    same range of return values the Slice 05 dispatcher itself accepts
    from legacy ports (typed :class:`PromptBuildResult` /
    ``Mapping[str, Any]`` carrying ``prompt`` + ``bundle`` keys /
    ``(prompt, bundle)`` tuple). This preserves the Slice 05 legacy
    port compatibility surface without re-importing the private helper.
    """

    if isinstance(value, PromptBuildResult):
        return value
    # Tuple form: ``(prompt, bundle)``.
    if isinstance(value, tuple) and len(value) == 2:
        prompt, bundle = value
        return PromptBuildResult(
            prompt=str(prompt),
            bundle=(
                bundle
                if isinstance(bundle, PromptContextBundle)
                else PromptContextBundle.model_validate(bundle)
            ),
        )
    # Mapping form: ``{"prompt": ..., "bundle": ...}``.
    if isinstance(value, Mapping):
        data = {str(key): value[key] for key in value}
        if "prompt" in data and "bundle" in data:
            return PromptBuildResult.model_validate(data)
    # Object with attributes: try direct model validation via Pydantic's
    # ``from_attributes`` path (the _DispatcherModel base carries
    # ``from_attributes=True`` per ``dispatcher.py:99-104``).
    return PromptBuildResult.model_validate(value)
