"""Slice 11i -- extraction proof for `execution/failure_router.py` extension.

Verifies the doc-11 § "How To Use This Map" four-question contract for the
pure decision-payload adapter primitive extraction:

1. What behavior moved: two pure typed→legacy `RouteDecision`-to-dict adapter
   helpers -- `_route_decision_retry_budget_payload(decision, *, action,
   max_attempts=None)` (builds the nested `retry_budget` dict) and
   `_route_decision_compat_payload(decision, *, failure_class, failure_type,
   max_attempts=None, legacy_route="", legacy_failure_type="")` (builds the
   full legacy-shape `route_decision` payload that gets persisted on every
   `runtime_failure_context` evidence row as the REAL typed budget source
   the Slice 10c-2 `_typed_retry_budgets` reads) -- moved byte-for-byte from
   `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/failure_router.py`. The Slice-07
   `FailureRouter` + `RouteDecision` + `FailureObservation` +
   `InMemoryFailureRouterPort` + `ROUTE_TABLE` surface already in
   `failure_router.py` is UNTOUCHED -- Slice 11i EXTENDS, never modifies.

2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X` for
   one of the two moved names keeps resolving to the SAME object as the
   canonical definition in `execution/failure_router.py` (the shim is
   `is`-equivalent, not a copy). `monkeypatch.setattr(implementation_module,
   X, ...)` continues to mutate the SAME function object that any direct
   `from execution.failure_router import X` reader sees. The two helpers are
   not externally imported (no `from implementation import
   _route_decision_*_payload` in `tests/` or `src/`), but the persisted
   `route_decision` payload shape they build is referenced by docstring at
   `tests/workflows/develop/execution/test_doc10_atomic_scenario.py:176` and
   `tests/workflows/develop/execution/test_snapshots_store.py:124` (which
   mirror the dict shape inline) AND by docstring at
   `src/iriai_build_v2/execution_control/store.py:1664, :2166` (which
   document the `route_decision` schema this helper produces).

3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is the proof; it pins both moved names' shim equivalence,
   `__module__` rebinding, behavioral smoke including the nested
   `retry_budget` dict + the optional-`legacy_route` / -`legacy_failure_type`
   tail keys + the `action.startswith("retry_")` retryable derivation, the
   cluster-ownership pin AND a back-import guard against `failure_router.py`
   ever importing from `implementation.py`.

4. Why is the PR still refactor-only: nothing else moves. The two pure
   typed→legacy `RouteDecision` adapter helpers moved byte-for-byte. The
   phase-level failure-router PORT surface (`_failure_router_for_runner(runner)`
   resolves the router from `runner.services`/`runner._failure_router_port` so
   the in-process router survives across DAG dispatches;
   `_typed_direct_route_payload(runner, feature, group_idx, retry, route,
   *, ...)` builds a typed FailureObservation around the direct-route
   classifier output via `_failure_router_for_runner`;
   `_route_merge_queue_drain_failure(runner, feature, *, ..., item_id,
   failure_class, ...)` + the `_MERGE_QUEUE_DRAIN_FAILURE_PAIRS` mapping
   that maps worker `failure_class` to the Slice 07 router pair -- merge-
   queue drain surface, properly Slice 11j territory; the impl.py-local
   `runtime_provider` retry-route adapter at `implementation.py:13190` that
   wraps the router for the dispatcher-retry path) is genuinely PHASE-LEVEL
   (each one takes `runner: WorkflowRunner` + `feature: Feature` or reads
   `runner.services`/`runner._failure_router_port`) and CORRECTLY stays in
   `implementation.py` per the prompt hard rule against splitting non-pure
   helpers.

Note on the P3-6 fold-in: Slice 11i is the pure refactor-only extraction.
The producer-side contract change (extending `MergeApplyResult` with a
structured `escaped_paths: list[str]` field that lets
`FailureRouter._allows_product_repair` preserve the `contract_violation`
route instead of downgrading to `quiesce`) lands inside the SAME atomic
chunk in Slice 11j (the merge_queue extraction) so the contract change is
applied end-to-end together. This file therefore tests only the existing
typed→legacy payload shape, not the P3-6 routing fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# Each entry is a name moved from `implementation.py` to
# `execution/failure_router.py` in Slice 11i. The order is the import-line
# order in the Slice-11i shim block in `implementation.py` so a grep over
# either file lists the names in the same order.
MOVED_NAMES = [
    "_route_decision_compat_payload",
    "_route_decision_retry_budget_payload",
]

# Both moved names are module-level functions; each has a `__module__`.
MOVED_CALLABLES = list(MOVED_NAMES)


@pytest.mark.parametrize("name", MOVED_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved helper imported via the OLD path is the SAME object as
    the import via the NEW canonical path. Proves the shim is a re-export,
    not a copy. Locks the monkeypatch target equivalence --
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate the
    SAME function object that any direct
    `from execution.failure_router import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import (
        failure_router as failure_router_mod,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(failure_router_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.failure_router.{name}"
    )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CALLABLES)
def test_canonical_module_is_failure_router(name: str) -> None:
    """The moved function objects' `__module__` is the new canonical
    `iriai_build_v2.workflows.develop.execution.failure_router` -- not the
    legacy `...phases.implementation`. Proves the definition genuinely
    moved rather than being re-aliased from the old module.
    """

    from iriai_build_v2.workflows.develop.execution import (
        failure_router as failure_router_mod,
    )

    canonical = getattr(failure_router_mod, name)
    assert canonical.__module__ == (
        "iriai_build_v2.workflows.develop.execution.failure_router"
    ), (
        f"{name}.__module__ = {canonical.__module__!r}; expected the new "
        "failure_router-module path"
    )


# -- Behavioral smoke ---------------------------------------------------------


class _FakeDecision:
    """A `RouteDecision`-shaped duck-typed stub. The moved helpers read the
    decision exclusively through `getattr` with explicit defaults, so any
    plain object with these attributes is accepted (this matches the way
    `implementation.py` uses these helpers via the typed `RouteDecision`
    returned by `FailureRouter.decide(failure_id)` + `mark_route_started`).
    """

    def __init__(
        self,
        *,
        action: str = "retry_dispatch",
        budget_remaining: int = 2,
        reservation_ordinal: int = 1,
        budget_key: str = "budget:product_defect:abc",
        idempotency_key: str = "route:f1:1:abc:retry_dispatch:n2",
        failure_id: int | None = 1,
        route_decision_id: int | None = 5,
        budget_exhausted: bool = False,
        reason: str = "retry",
        required_evidence_ids: list[int] | None = None,
        signature_hash: str = "abc",
        repair_scope: dict | None = None,
    ) -> None:
        self.action = action
        self.budget_remaining = budget_remaining
        self.reservation_ordinal = reservation_ordinal
        self.budget_key = budget_key
        self.idempotency_key = idempotency_key
        self.failure_id = failure_id
        self.route_decision_id = route_decision_id
        self.budget_exhausted = budget_exhausted
        self.reason = reason
        self.required_evidence_ids = required_evidence_ids or [3, 7]
        self.signature_hash = signature_hash
        self.repair_scope = repair_scope or {"feature_id": "f1", "target_paths": ["/x"]}


def test_retry_budget_payload_derives_max_attempts_from_ordinal_when_unspecified() -> None:
    """`_route_decision_retry_budget_payload(decision, *, action,
    max_attempts=None)` returns a flat dict with the legacy `retry_budget`
    keys (`route`, `budget_key`, `max_attempts`, `max_retries`,
    `remaining_attempts`, `idempotency_key`, `reservation_ordinal`,
    `budget_exhausted`). When `max_attempts` is None, it is derived from
    `remaining + ordinal` if `ordinal > 0`, else `remaining`. Pinned by the
    `_route_decision_compat_payload` callers at `implementation.py:872, :13262`
    that pass `max_attempts=None` (the dispatcher-retry path uses the
    `VERIFY_RETRIES` cap; the direct-route path lets the helper derive it).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_retry_budget_payload,
    )

    # `ordinal > 0` -> max_attempts = remaining + ordinal.
    payload = _route_decision_retry_budget_payload(
        _FakeDecision(budget_remaining=2, reservation_ordinal=1),
        action="retry_dispatch",
    )
    assert payload["max_attempts"] == 3  # 2 + 1
    assert payload["max_retries"] == 3
    assert payload["remaining_attempts"] == 2
    assert payload["reservation_ordinal"] == 1
    assert payload["route"] == "retry_dispatch"
    assert payload["budget_key"] == "budget:product_defect:abc"
    assert payload["idempotency_key"] == "route:f1:1:abc:retry_dispatch:n2"
    assert payload["budget_exhausted"] is False

    # `ordinal == 0` -> max_attempts = remaining.
    payload2 = _route_decision_retry_budget_payload(
        _FakeDecision(budget_remaining=5, reservation_ordinal=0),
        action="retry_dispatch",
    )
    assert payload2["max_attempts"] == 5
    assert payload2["max_retries"] == 5
    assert payload2["remaining_attempts"] == 5
    assert payload2["reservation_ordinal"] == 0


def test_retry_budget_payload_respects_explicit_max_attempts() -> None:
    """An explicit `max_attempts` overrides the `remaining + ordinal`
    derivation. Pinned by the `_route_decision_compat_payload` caller at
    `implementation.py:13266` which passes `max_attempts=VERIFY_RETRIES` (= 2)
    to clamp the dispatcher-retry runtime_provider/provider_internal_error
    route's max_attempts to the impl-local retry cap rather than the
    router's per-class budget (CLASS_RETRY_BUDGETS['runtime_provider'] = 2).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_retry_budget_payload,
    )

    payload = _route_decision_retry_budget_payload(
        _FakeDecision(budget_remaining=2, reservation_ordinal=1),
        action="retry_dispatch",
        max_attempts=2,  # explicit cap
    )
    assert payload["max_attempts"] == 2
    assert payload["max_retries"] == 2
    assert payload["remaining_attempts"] == 2  # remaining is unaffected


def test_retry_budget_payload_clamps_negative_to_zero() -> None:
    """`max(0, int(...))` clamps both `budget_remaining` and
    `reservation_ordinal` to non-negative. A `None`/negative payload from a
    misshapen decision falls back to `0`. Fail-soft against a decision shape
    that loses a field.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_retry_budget_payload,
    )

    # `or 0` short-circuits a None value.
    class _SparseDecision:
        budget_remaining = None  # type: ignore[assignment]
        reservation_ordinal = None  # type: ignore[assignment]
        budget_key = None  # type: ignore[assignment]
        idempotency_key = None  # type: ignore[assignment]
        budget_exhausted = False

    payload = _route_decision_retry_budget_payload(
        _SparseDecision(),
        action="quiesce",
    )
    assert payload["remaining_attempts"] == 0
    assert payload["reservation_ordinal"] == 0
    assert payload["max_attempts"] == 0
    assert payload["budget_key"] == ""
    assert payload["idempotency_key"] == ""
    assert payload["route"] == "quiesce"

    # Negative ints should also clamp to zero.
    payload2 = _route_decision_retry_budget_payload(
        _FakeDecision(budget_remaining=-3, reservation_ordinal=-1),
        action="retry_dispatch",
    )
    assert payload2["remaining_attempts"] == 0
    assert payload2["reservation_ordinal"] == 0


def test_retry_budget_payload_exhausted_flag_propagates() -> None:
    """`budget_exhausted` is propagated unchanged from the input decision.
    Pinned by `FailureRouter.decide` (`failure_router.py:1537`) which sets
    `budget_exhausted=True` when `budget_remaining <= 0` and the action is
    rewritten to `quiesce`.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_retry_budget_payload,
    )

    payload = _route_decision_retry_budget_payload(
        _FakeDecision(budget_exhausted=True, budget_remaining=0, reservation_ordinal=2),
        action="quiesce",
    )
    assert payload["budget_exhausted"] is True
    assert payload["max_attempts"] == 2  # 0 + 2 (ordinal-derived)
    assert payload["remaining_attempts"] == 0


def test_compat_payload_includes_typed_failure_id_and_dual_signature_hash() -> None:
    """`_route_decision_compat_payload(decision, *, failure_class,
    failure_type)` builds the legacy-shape payload. Asserts:
    - `failure_id` AND `typed_failure_id` both alias `decision.failure_id`
    - `signature_hash` AND `stable_signature_hash` both alias
      `decision.signature_hash` (the dual key is intentional -- legacy
      readers use `stable_signature_hash`; new readers use `signature_hash`)
    - `action` AND `route` both alias `decision.action`
    - `retryable = decision.action.startswith("retry_")` (purely string-based,
      derived from the typed action enum)
    - `operator_required = decision.action == "operator_required"` (single
      value check; the router uses this action enum for operator escalation)
    - `repair_scope` is a deep-copied dict (not aliased to the decision's)
    - `required_evidence_ids` is materialized as a list (not the original
      sequence) so mutating the result does not touch the decision.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    decision = _FakeDecision(
        action="retry_dispatch",
        failure_id=42,
        route_decision_id=7,
        signature_hash="sig-abc",
        required_evidence_ids=[11, 22],
        repair_scope={"feature_id": "f1", "target_paths": ["/a", "/b"]},
        reason="provider rate limited",
    )
    payload = _route_decision_compat_payload(
        decision,
        failure_class="runtime_provider",
        failure_type="provider_rate_limited",
    )

    # ID dual-aliasing.
    assert payload["failure_id"] == 42
    assert payload["typed_failure_id"] == 42
    assert payload["route_decision_id"] == 7

    # Action / route dual-aliasing.
    assert payload["action"] == "retry_dispatch"
    assert payload["route"] == "retry_dispatch"

    # Signature dual-aliasing.
    assert payload["signature_hash"] == "sig-abc"
    assert payload["stable_signature_hash"] == "sig-abc"

    # Action-derived booleans.
    assert payload["retryable"] is True
    assert payload["operator_required"] is False

    # Class + type embedded.
    assert payload["failure_class"] == "runtime_provider"
    assert payload["failure_type"] == "provider_rate_limited"

    # Reason + budget pass-through.
    assert payload["reason"] == "provider rate limited"
    assert payload["budget_exhausted"] is False
    assert payload["budget_remaining"] == 2  # mirrors retry_budget.remaining

    # repair_scope is a NEW dict (deep enough that mutating it does not
    # touch the decision's repair_scope).
    assert payload["repair_scope"] == {"feature_id": "f1", "target_paths": ["/a", "/b"]}
    payload["repair_scope"]["mutate_me"] = "ok"
    assert "mutate_me" not in decision.repair_scope

    # required_evidence_ids is a NEW list.
    assert payload["required_evidence_ids"] == [11, 22]
    payload["required_evidence_ids"].append(99)
    assert decision.required_evidence_ids == [11, 22]

    # retry_budget is nested as a dict with the expected keys.
    rb = payload["retry_budget"]
    assert isinstance(rb, dict)
    assert rb["route"] == "retry_dispatch"
    assert rb["remaining_attempts"] == 2
    assert rb["reservation_ordinal"] == 1
    assert rb["budget_exhausted"] is False


def test_compat_payload_operator_required_action_flips_flag_and_clears_retryable() -> None:
    """`operator_required` action is the typed escalation route. The compat
    payload sets `operator_required=True` and `retryable=False` (the action
    does not start with `retry_`). Pinned by the Slice 07
    `_OPERATOR_REQUIRED_FAILURE_TYPES = {"context_permission_denied",
    "operator_clearance_required"}` route table at `failure_router.py:1361,
    :449-451`.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    decision = _FakeDecision(action="operator_required")
    payload = _route_decision_compat_payload(
        decision,
        failure_class="operator_required",
        failure_type="operator_clearance_required",
    )
    assert payload["action"] == "operator_required"
    assert payload["route"] == "operator_required"
    assert payload["operator_required"] is True
    assert payload["retryable"] is False


def test_compat_payload_quiesce_action_clears_both_action_flags() -> None:
    """`quiesce` action is the typed soft-stop route. Both
    `operator_required` and `retryable` are False for a quiesce route.
    The Slice 07 route table maps several deterministic classes to
    `quiesce`: `contract_compile/contract_scope_conflict`,
    `dispatcher_internal/idempotency_conflict`,
    `checkpoint_contradiction/checkpoint_after_failed_gate`, etc.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    decision = _FakeDecision(action="quiesce", budget_exhausted=True)
    payload = _route_decision_compat_payload(
        decision,
        failure_class="checkpoint_contradiction",
        failure_type="checkpoint_after_failed_gate",
    )
    assert payload["action"] == "quiesce"
    assert payload["operator_required"] is False
    assert payload["retryable"] is False
    assert payload["budget_exhausted"] is True


def test_compat_payload_retry_action_family_marks_retryable() -> None:
    """Every Slice 07 typed `retry_*` action (retry_dispatch, retry_verifier,
    retry_merge, retry_sandbox_capture) sets `retryable=True` via the
    `action.startswith("retry_")` derivation. Pinned by the Slice 07
    `_RETRY_ACTIONS = frozenset({"retry_dispatch", "retry_verifier",
    "retry_merge", "retry_sandbox_capture"})` at `failure_router.py:453-455`.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    for action in (
        "retry_dispatch",
        "retry_verifier",
        "retry_merge",
        "retry_sandbox_capture",
    ):
        payload = _route_decision_compat_payload(
            _FakeDecision(action=action),
            failure_class="runtime_provider",
            failure_type="provider_internal_error",
        )
        assert payload["retryable"] is True, f"{action} should be retryable"
        assert payload["operator_required"] is False


def test_compat_payload_run_product_repair_is_not_retryable_via_string_prefix() -> None:
    """The `run_*` repair actions are not retry actions (they do not start
    with `retry_`), so `retryable=False`. This is intentional: a product
    repair is its own typed lane, not a dispatcher retry. Pinned by the
    Slice 07 `_RETRY_ACTIONS` set which lists only `retry_*` actions.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    for action in (
        "run_product_repair",
        "run_contract_repair",
        "run_canonicalization_repair",
        "run_workspace_repair",
        "run_commit_hygiene_repair",
        "run_sandbox_cleanup",
    ):
        payload = _route_decision_compat_payload(
            _FakeDecision(action=action),
            failure_class="product_defect",
            failure_type="semantic_verifier_rejected",
        )
        assert payload["retryable"] is False, (
            f"{action} is a run_* repair action, not a retry action"
        )
        assert payload["operator_required"] is False


def test_compat_payload_legacy_route_and_legacy_failure_type_tail_keys() -> None:
    """The optional `legacy_route` and `legacy_failure_type` kwargs
    (default `""`) inject tail-aliasing keys for callers that need the
    pre-typed-router legacy route name + legacy failure type name. The
    empty default does NOT emit a key (truthiness check) so byte-for-byte
    the absence is preserved when the caller does not need the legacy
    aliases. Pinned by the `_typed_direct_route_payload` caller at
    `implementation.py:876` (passes `legacy_route=route.route` for the
    direct-route classification path -- e.g. `normal_verify_repair` or
    `manifest_forbidden_product_cleanup`) and the dispatcher-retry caller
    at `implementation.py:13267` (passes `legacy_failure_type="provider_
    crash"` while the typed pair is `runtime_provider/provider_internal_
    error`).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    # Neither legacy key emitted by default.
    base = _route_decision_compat_payload(
        _FakeDecision(),
        failure_class="product_defect",
        failure_type="semantic_verifier_rejected",
    )
    assert "legacy_route" not in base
    assert "legacy_failure_type" not in base

    # Both legacy keys emitted when non-empty strings are passed.
    with_legacy = _route_decision_compat_payload(
        _FakeDecision(),
        failure_class="product_defect",
        failure_type="semantic_verifier_rejected",
        legacy_route="normal_verify_repair",
        legacy_failure_type="semantic_reject",
    )
    assert with_legacy["legacy_route"] == "normal_verify_repair"
    assert with_legacy["legacy_failure_type"] == "semantic_reject"

    # Falsy strings (empty) still suppress the key (`if legacy_route:` check).
    suppressed = _route_decision_compat_payload(
        _FakeDecision(),
        failure_class="product_defect",
        failure_type="semantic_verifier_rejected",
        legacy_route="",
        legacy_failure_type="",
    )
    assert "legacy_route" not in suppressed
    assert "legacy_failure_type" not in suppressed


def test_compat_payload_max_attempts_propagates_to_retry_budget() -> None:
    """The explicit `max_attempts` kwarg flows through
    `_route_decision_retry_budget_payload` -> `retry_budget.max_attempts`
    AND `retry_budget.max_retries`. The compat payload's top-level
    `budget_remaining` continues to mirror `retry_budget.remaining_attempts`,
    which is independent of `max_attempts` (it tracks the decision's actual
    remaining budget, not the cap). Pinned by `implementation.py:13266`
    which caps the dispatcher-retry runtime_provider route at
    `VERIFY_RETRIES = 2`.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    payload = _route_decision_compat_payload(
        _FakeDecision(budget_remaining=2, reservation_ordinal=1),
        failure_class="runtime_provider",
        failure_type="provider_internal_error",
        max_attempts=2,  # VERIFY_RETRIES cap
    )
    assert payload["retry_budget"]["max_attempts"] == 2
    assert payload["retry_budget"]["max_retries"] == 2
    assert payload["retry_budget"]["remaining_attempts"] == 2
    assert payload["budget_remaining"] == 2  # mirrors remaining_attempts


def test_compat_payload_persists_canonical_shape_for_slice_10c2_typed_budgets() -> None:
    """The Slice 10c-2 typed-control-plane snapshot reads this exact
    `route_decision` shape (with the nested `retry_budget` dict) from
    every `runtime_failure_context` evidence row. The Slice-10c-2
    `_typed_retry_budgets` source-of-truth path consumes:
    - `route_decision.route` (the action string)
    - `route_decision.failure_class`
    - `route_decision.budget_remaining`
    - `route_decision.budget_exhausted`
    - `route_decision.reservation_ordinal`
    - `route_decision.signature_hash`
    - `route_decision.retry_budget.max_attempts`
    - `route_decision.retry_budget.budget_key`

    All EIGHT keys MUST be present at canonical positions. Belt-and-braces
    test against a future schema-shape drift that would invalidate the
    persisted evidence rows. Mirror of the `_simulate_route_decision_*`
    test helper at `tests/workflows/develop/execution/test_snapshots_
    store.py:124` (which inline-mirrors this exact dict shape).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
    )

    payload = _route_decision_compat_payload(
        _FakeDecision(
            action="retry_dispatch",
            budget_remaining=1,
            reservation_ordinal=2,
            budget_key="budget:runtime_provider:sig-xyz",
            signature_hash="sig-xyz",
            idempotency_key="route:f1:1:sig-xyz:retry_dispatch:n3",
        ),
        failure_class="runtime_provider",
        failure_type="provider_internal_error",
        max_attempts=3,
    )

    # Top-level keys consumed by `_typed_retry_budgets`.
    assert payload["route"] == "retry_dispatch"
    assert payload["failure_class"] == "runtime_provider"
    assert payload["budget_remaining"] == 1
    assert payload["budget_exhausted"] is False
    assert payload["reservation_ordinal"] == 2
    assert payload["signature_hash"] == "sig-xyz"

    # Nested retry_budget keys consumed by `_typed_retry_budgets`.
    rb = payload["retry_budget"]
    assert rb["max_attempts"] == 3
    assert rb["budget_key"] == "budget:runtime_provider:sig-xyz"


# -- Structural ---------------------------------------------------------------


def test_cluster_ownership_pin_failure_router_module() -> None:
    """Both moved names land in the canonical `execution/failure_router.py`
    module (not in any other `execution/` sibling like `types.py`,
    `git_service.py`, `task_contracts.py`, `sandbox.py`, `dispatcher.py`,
    `gates.py`, `verification.py`, or `repair.py`). Belt-and-braces guard
    against a future refactor accidentally relocating one of the helpers
    to the wrong canonical module while leaving the shim intact.
    """

    from iriai_build_v2.workflows.develop.execution import (
        failure_router as failure_router_mod,
    )

    expected = "iriai_build_v2.workflows.develop.execution.failure_router"
    for name in MOVED_CALLABLES:
        obj = getattr(failure_router_mod, name)
        assert obj.__module__ == expected, (
            f"{name}.__module__ = {obj.__module__!r}; expected {expected!r}"
        )

    # Cross-check that the names are NOT served by any of the sibling
    # execution modules (a deliberate "did anyone else accidentally define
    # a copy?" probe). repair.py is the closest sibling -- it owns the
    # Slice-11h repair-domain primitives + the pre-Slice-08 `RouteExecutor`
    # request types -- but it MUST NOT redefine the typed→legacy decision
    # adapters; failure_router.py is their canonical home.
    from iriai_build_v2.workflows.develop.execution import (
        dispatcher as dispatcher_mod,
        gates as gates_mod,
        git_service as git_service_mod,
        repair as repair_mod,
        sandbox as sandbox_mod,
        task_contracts as task_contracts_mod,
        types as types_mod,
        verification as verification_mod,
    )
    for name in MOVED_NAMES:
        for sibling, sibling_name in (
            (dispatcher_mod, "dispatcher"),
            (gates_mod, "gates"),
            (git_service_mod, "git_service"),
            (repair_mod, "repair"),
            (sandbox_mod, "sandbox"),
            (task_contracts_mod, "task_contracts"),
            (types_mod, "types"),
            (verification_mod, "verification"),
        ):
            assert not hasattr(sibling, name), (
                f"sibling drift: {sibling_name}.{name} unexpectedly exists; "
                "cluster ownership pin failed"
            )


def test_shim_block_exports_all_two_names() -> None:
    """The Slice-11i shim block in `implementation.py` re-exports exactly
    the two moved names from `..execution.failure_router`. This test
    asserts the shim block actually carries both (a deliberate "did the
    shim block lose a name?" probe).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _route_decision_compat_payload,
        _route_decision_retry_budget_payload,
    )
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    # Both moved names accessible via the impl module.
    for name in MOVED_NAMES:
        assert hasattr(impl_mod, name), (
            f"implementation.{name} missing -- the Slice-11i shim block "
            "dropped a re-export"
        )

    # Both shim entries point to the SAME canonical objects.
    assert impl_mod._route_decision_compat_payload is _route_decision_compat_payload
    assert (
        impl_mod._route_decision_retry_budget_payload
        is _route_decision_retry_budget_payload
    )

    # The pre-existing Slice-07 surface aliases (`RouterFailureObservation`,
    # `RouterFailureRouter`, `RouterInMemoryFailureRouterPort`,
    # `RouterRouteDecision`) at the impl.py-local `try: from
    # ..execution.failure_router import ...` block are STILL present and
    # STILL point to the canonical Slice-07 surface (the alias-import block
    # at implementation.py:145-156 is untouched by Slice 11i).
    from iriai_build_v2.workflows.develop.execution.failure_router import (
        FailureObservation as CanonicalFailureObservation,
        FailureRouter as CanonicalFailureRouter,
        InMemoryFailureRouterPort as CanonicalInMemoryFailureRouterPort,
        RouteDecision as CanonicalRouteDecision,
    )
    assert impl_mod.RouterFailureObservation is CanonicalFailureObservation
    assert impl_mod.RouterFailureRouter is CanonicalFailureRouter
    assert (
        impl_mod.RouterInMemoryFailureRouterPort
        is CanonicalInMemoryFailureRouterPort
    )
    assert impl_mod.RouterRouteDecision is CanonicalRouteDecision


def test_failure_router_module_does_not_import_implementation() -> None:
    """The compatibility-arrow direction (per doc 11 § "How To Use This
    Map" Q4) is: `execution/failure_router.py` MUST NOT import from
    `workflows.develop.phases.implementation`. This test reads the on-disk
    source of `failure_router.py` and asserts the import line is absent.
    Belt-and-braces guard against a future refactor accidentally
    introducing a back-import.
    """

    import iriai_build_v2.workflows.develop.execution.failure_router as failure_router_mod

    source_path = Path(failure_router_mod.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "from iriai_build_v2.workflows.develop.phases.implementation"
        not in text
    ), (
        "execution/failure_router.py imports from phases/implementation -- "
        "violates the doc-11 compatibility-arrow direction"
    )
    assert "from ..phases.implementation" not in text, (
        "execution/failure_router.py uses a relative back-import to "
        "phases/implementation -- violates the doc-11 compatibility-arrow "
        "direction"
    )


def test_all_export_includes_two_moved_names() -> None:
    """`failure_router.py.__all__` includes both moved names. Belt-and-
    braces probe against a refactor that forgets to add the new public
    symbols to the module's public surface (which would cause
    `from execution.failure_router import *` to silently lose them).
    """

    from iriai_build_v2.workflows.develop.execution import (
        failure_router as failure_router_mod,
    )

    for name in MOVED_NAMES:
        assert name in failure_router_mod.__all__, (
            f"{name} missing from execution/failure_router.py __all__"
        )
