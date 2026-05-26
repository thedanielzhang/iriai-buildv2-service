"""Slice 19 first sub-slice -- unit tests for the foundational
``execution_control/governance_agent.py`` typed-shape module.

Covers the 2 doc-19:71-87 + doc-19:103-117 typed BaseModels + the
canonical-JSON helper functions + the 5 default-budget constants:

- :class:`GovernanceSnapshot` -- 16 fields per doc-19:71-87. Slice 13a
  ``CompletenessState`` consumption on :attr:`completeness` (NOT
  redefined; DIRECT annotation-identity assertion). Slice 13a
  ``EvidenceQuality`` consumption on :attr:`evidence_quality` (NOT
  redefined; DIRECT annotation-identity assertion). Slice 16
  ``GovernanceFinding`` consumption on :attr:`top_findings` (NOT
  redefined; DIRECT annotation-identity assertion via
  ``get_args(... .annotation)[0] is GovernanceFinding``). Slice 17
  ``GovernancePolicyRecommendation`` consumption on
  :attr:`recommendations` (NOT redefined; DIRECT annotation-identity
  assertion). Slice 18 ``CounterfactualResult`` consumption on
  :attr:`replay_results` (NOT redefined; DIRECT annotation-identity
  assertion).
- :class:`GovernanceAgentContext` -- 12 fields per doc-19:103-117 with
  the Slice 21-conditional ``context_package`` field intentionally
  deferred to a future sub-slice (per doc-19:89-101 +
  doc-19:125-127 *"After Slice 21, this response must include
  `ContextLayerPackageSummary`..."*). Slice 16 ``GovernanceFinding``
  consumption on :attr:`relevant_findings` (NOT redefined; DIRECT
  annotation-identity assertion). Slice 17
  ``GovernancePolicyRecommendation`` consumption on
  :attr:`policy_guidance` (NOT redefined; DIRECT annotation-identity
  assertion). Slice 13a ``CompletenessState`` consumption on
  :attr:`completeness`. The
  :attr:`policy_guidance_authority: Literal["advisory_only"] =
  "advisory_only"` hard-coded literal default per doc-19:110 +
  doc-19:230-231 AC5 enforces the advisory-only invariant at the
  typed-shape layer.
- :func:`compute_governance_snapshot_digest` +
  :func:`canonical_governance_snapshot_dict` -- canonical-JSON +
  SHA-256 helpers mirroring Slice 13A ``compute_completeness_digest``
  + Slice 14 ``compute_payload_sha256`` + Slice 15
  ``compute_scorecard_digest`` + Slice 16 1st sub-slice
  ``compute_finding_idempotency_key`` + ``canonical_finding_dict`` +
  Slice 17 1st sub-slice
  ``compute_policy_recommendation_idempotency_key`` +
  ``canonical_policy_recommendation_dict`` + Slice 18 1st sub-slice
  ``compute_counterfactual_idempotency_key`` +
  ``canonical_counterfactual_dict`` discipline.
- 5 default-budget constants per doc-19:121-127:
  :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES` (262 144) +
  :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS` (20) +
  :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS` (10) +
  :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS` (10) +
  :data:`GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP` (20 000).

Every BaseModel enforces ``extra="forbid"`` (typo-d kwargs ->
``ValidationError``). Every Literal range is enforced (per Pydantic
``Literal`` validator).

**P3-V3-2 stronger-pattern annotation-identity assertions.** Per the
Slice 14 close-out (P3-V3-2 CARRY) + the Slice 15 1st + Slice 16 1st
+ Slice 17 1st + Slice 18 1st sub-slice adoption the Slice 19 1st
sub-slice continues the stronger pattern verbatim for the 5 REUSE
fields on :class:`GovernanceSnapshot` (``top_findings`` /
``recommendations`` / ``replay_results`` / ``completeness`` /
``evidence_quality``) + the 3 REUSE fields on
:class:`GovernanceAgentContext` (``relevant_findings`` /
``policy_guidance`` / ``completeness``). The DIRECT
``get_args(... .annotation)[0] is <SHARED_CLASS>`` (for list types) or
``annotation is <SHARED_LITERAL>`` (for Literal types) assertions
enforce that this module consumes the upstream typed shapes by
identity, not via a redefinition.

**Activation-boundary discipline (per doc-19:230-231 AC5 +
doc-19:348-349 AC).** The
:attr:`GovernanceAgentContext.policy_guidance_authority:
Literal["advisory_only"]` default IS the typed-shape enforcer for AC5
(*"Workflow agents receive governance policy guidance only as advisory
context; contracts, gates, router, and merge queue remain
authoritative."*). The activation-boundary tests assert (a) the
module has NO mutation methods on any BaseModel, (b) NO ``dag-*``
authority-artifact-key string literals are carried in the source, and
(c) NO ``CONTROL_PLANE_WRITER_METHODS`` set is extended (per
doc-19:348-349 AC: *"Supervisor/dashboard read-only contract
preserved (no governance writer extends the Slice 10c-1
`CONTROL_PLANE_WRITER_METHODS` set)."*).

**Slice 13A awareness asserted (doc-19:256-303 Slice 13A Shared
Completeness Model Dependency).** No local ``CompletenessState`` /
``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The governance-agent module exposes the typed surface
that future Slice 19 sub-slices wire to the Slice 13A typed shapes;
this 1st sub-slice enforces the no-redefinition discipline at the
test-file level.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 modules + tests remain byte-identical.
"""

from __future__ import annotations

import json as _json
from datetime import datetime, timezone
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS,
    GovernanceAgentContext,
    GovernanceSnapshot,
    canonical_governance_snapshot_dict,
    compute_governance_snapshot_digest,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    SchedulerPolicyArtifact,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
    GovernanceEvidenceRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 2 typed BaseModels + 2
    canonical-JSON helpers + 5 default-budget constants.

    Per doc-19:70-127 the surface is:
    * 2 typed BaseModels (``GovernanceSnapshot`` 16-field +
      ``GovernanceAgentContext`` 12-field per doc-19:103-117 with the
      Slice 21-conditional ``context_package`` deferred).
    * 2 canonical-JSON helpers (``compute_governance_snapshot_digest``
      + ``canonical_governance_snapshot_dict``).
    * 5 default-budget constants (per doc-19:121-127).

    Total: 9 exported names.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    expected = {
        "GovernanceSnapshot",
        "GovernanceAgentContext",
        "compute_governance_snapshot_digest",
        "canonical_governance_snapshot_dict",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS",
        "GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 9
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_finding() -> None:
    """Per doc-19:83 + doc-19:107 + doc-13a:285-287 step 9 the Slice 19
    module MUST NOT redefine :class:`GovernanceFinding` -- it consumes
    the Slice 16 1st sub-slice shared BaseModel via direct import.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    # The Slice 19 module does NOT re-export GovernanceFinding (it
    # uses the typed REUSE via field annotation; consumers import
    # GovernanceFinding directly from finding_engine).
    assert getattr(mod, "GovernanceFinding", None) is None or (
        mod.GovernanceFinding is GovernanceFinding  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """Per doc-19:84 + doc-19:109 + doc-13a:285-287 step 9 the Slice 19
    module MUST NOT redefine :class:`GovernancePolicyRecommendation`
    -- it consumes the Slice 17 1st sub-slice shared BaseModel via
    direct import.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "GovernancePolicyRecommendation", None) is None or (
        mod.GovernancePolicyRecommendation is GovernancePolicyRecommendation  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_counterfactual_result() -> None:
    """Per doc-19:85 + doc-13a:285-287 step 9 the Slice 19 module MUST
    NOT redefine :class:`CounterfactualResult` -- it consumes the Slice
    18 1st sub-slice shared BaseModel via direct import.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "CounterfactualResult", None) is None or (
        mod.CounterfactualResult is CounterfactualResult  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-19:80 + doc-19:113 + doc-13a:285-287 step 9 the Slice 19
    module MUST NOT redefine :data:`CompletenessState` -- it consumes
    the Slice 13a shared Literal via direct import (per doc-19:256-303
    Slice 13A Shared Completeness Model Dependency).
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "CompletenessState", None) is None or (
        mod.CompletenessState is CompletenessState  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_evidence_quality() -> None:
    """Per doc-19:86 + doc-13a:285-287 step 9 the Slice 19 module MUST
    NOT redefine :data:`EvidenceQuality` -- it consumes the Slice 13a
    shared Literal via direct import.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "EvidenceQuality", None) is None or (
        mod.EvidenceQuality is EvidenceQuality  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_evidence_completeness() -> None:
    """Per doc-19:256-303 Slice 13A Shared Completeness Model
    Dependency the Slice 19 module MUST NOT redefine
    :class:`EvidenceCompleteness` -- the shared model is the Slice 13A
    2nd sub-slice source-of-truth shape.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "EvidenceCompleteness", None) is None


def test_module_does_not_redefine_authoritative_prompt_context_routing() -> None:
    """Per doc-19:256-303 + the Slice 13A 4th sub-slice
    ``dispatcher_prompt_context.py`` source-of-truth contract the
    Slice 19 module MUST NOT redefine
    :class:`AuthoritativePromptContextRouting`.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "AuthoritativePromptContextRouting", None) is None


def test_module_does_not_redefine_authoritative_gate_proof_row() -> None:
    """Per doc-19:256-303 + the Slice 13A 5th sub-slice
    ``gate_companion.py`` source-of-truth contract the Slice 19 module
    MUST NOT redefine :class:`AuthoritativeGateProofRow`.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "AuthoritativeGateProofRow", None) is None


def test_module_does_not_redefine_authoritative_snapshot_list_field_completeness() -> None:
    """Per doc-19:256-303 + the Slice 13A 6th sub-slice
    ``snapshot_companion.py`` source-of-truth contract the Slice 19
    module MUST NOT redefine
    :class:`AuthoritativeSnapshotListFieldCompleteness`.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert (
        getattr(mod, "AuthoritativeSnapshotListFieldCompleteness", None)
        is None
    )


def test_module_does_not_redefine_exact_evidence_manifest() -> None:
    """Per doc-19:256-303 + the Slice 13A 2nd sub-slice
    ``completeness.py`` source-of-truth contract the Slice 19 module
    MUST NOT redefine :class:`ExactEvidenceManifest`.
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "ExactEvidenceManifest", None) is None


def test_module_does_not_redefine_context_layer_package_summary() -> None:
    """Per doc-19:89-101 + doc-19:125-127 the
    :class:`ContextLayerPackageSummary` shape is a Slice 21-conditional
    contract (*"After Slice 21, this response must include
    `ContextLayerPackageSummary`..."*); the Slice 19 1st sub-slice does
    NOT yet expose the typed shape (it lands at the future Slice 21
    integration sub-slice).
    """

    from iriai_build_v2.execution_control import governance_agent as mod

    assert getattr(mod, "ContextLayerPackageSummary", None) is None


def test_module_import_discipline_no_implementation_py() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 19 1st sub-slice module MUST NOT import from the legacy
    ``implementation.py`` Slice 00-12 monolith (this module is
    foundational; the Slice 00-12 monolith is the runtime authority
    layer and is downstream of Slice 19's typed-shape surface).
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.implementation",
        "import iriai_build_v2.workflows.develop.execution.implementation",
    )
    for f in forbidden:
        assert f not in src, (
            f"governance_agent.py must not import from the Slice 00-12 "
            f"implementation.py monolith; found {f!r}"
        )


def test_module_import_discipline_no_failure_router() -> None:
    """Per the implementer prompt § "Non-negotiables" the Slice 19 1st
    sub-slice module MUST NOT import from
    ``workflows.develop.execution.failure_router`` (the typed failure
    router is the Slice 07 runtime authority; Slice 19 1st sub-slice
    is a pure typed-shape foundation that does not yet emit any failure
    ids).
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.failure_router",
        "import iriai_build_v2.workflows.develop.execution.failure_router",
    )
    for f in forbidden:
        assert f not in src, (
            f"governance_agent.py must not import from failure_router.py; "
            f"found {f!r}"
        )


def test_module_import_discipline_no_other_execution_control_modules() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 19 1st sub-slice module MUST NOT import from other parts of
    ``execution_control/`` beyond the 3 Slice 16/17/18 1st-sub-slice
    typed-shape modules + the Slice 13a shared model at
    ``workflows.develop.governance.models``.

    The existing Slice 00-18 ``execution_control`` modules are
    READ-ONLY; subsequent Slice 19 sub-slices wire to additional
    Slice 13A / Slice 14 / Slice 15 / Slice 16 / Slice 17 / Slice 18
    modules.
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.execution_control.commit_provenance",
        "from iriai_build_v2.execution_control.completeness",
        "from iriai_build_v2.execution_control.dispatcher_prompt_context",
        "from iriai_build_v2.execution_control.gate_companion",
        "from iriai_build_v2.execution_control.snapshot_companion",
        "from iriai_build_v2.execution_control.governance_metrics",
        "from iriai_build_v2.execution_control.governance_metric_extractor",
        "from iriai_build_v2.execution_control.governance_scorecard_writer",
        "from iriai_build_v2.execution_control.finding_rule_engine",
        "from iriai_build_v2.execution_control.finding_plan_deviation_engine",
        "from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine",
        "from iriai_build_v2.execution_control.governance_finding_writer",
        "from iriai_build_v2.execution_control.recommendation_builder",
        "from iriai_build_v2.execution_control.policy_validation_interface",
        "from iriai_build_v2.execution_control.decision_record_writer",
        "from iriai_build_v2.execution_control.replay_requirement_hook",
        "from iriai_build_v2.execution_control.consumer_read_api",
        "from iriai_build_v2.execution_control.counterfactual_replay_loader",
        "from iriai_build_v2.execution_control.counterfactual_summary_replay",
        "from iriai_build_v2.execution_control.counterfactual_event_replay",
        "from iriai_build_v2.execution_control.counterfactual_metrics_comparator",
        "from iriai_build_v2.execution_control.counterfactual_result_writer",
        "from iriai_build_v2.execution_control.recommendation_citation_hook",
        "from iriai_build_v2.execution_control.store",
        "from iriai_build_v2.execution_control.atomic_landing",
        "from iriai_build_v2.execution_control.adoption",
        "from iriai_build_v2.execution_control.startup",
    )
    for f in forbidden:
        assert f not in src, (
            f"governance_agent.py 1st sub-slice must not import from other "
            f"execution_control modules; found {f!r}. Subsequent Slice 19 "
            f"sub-slices may add these imports."
        )


def test_module_import_discipline_no_supervisor_or_dashboard() -> None:
    """Per the governance prompt § "Non-Negotiables" + STATUS.md §
    "Loop discipline" activation-authority-boundary note the Slice 19
    typed-shape module MUST NOT import from ``supervisor`` or
    ``dashboard`` (those are downstream consumers of the governance-
    agent surface, not dependencies).
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.supervisor",
        "import iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.dashboard",
        "from dashboard",
        "import dashboard",
    )
    for f in forbidden:
        assert f not in src, (
            f"governance_agent.py must not import from supervisor or "
            f"dashboard; found {f!r}"
        )


def test_package_init_does_not_re_export_governance_agent() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17 +
    Slice 18 precedent (no-re-export discipline) the
    ``src/iriai_build_v2/execution_control/__init__.py`` MUST NOT
    re-export the Slice 19 1st sub-slice module's typed shapes.
    Consumers import directly from
    :mod:`iriai_build_v2.execution_control.governance_agent`.
    """

    from iriai_build_v2 import execution_control as pkg

    forbidden_re_exports = (
        "GovernanceSnapshot",
        "GovernanceAgentContext",
        "compute_governance_snapshot_digest",
        "canonical_governance_snapshot_dict",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS",
        "GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS",
        "GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP",
    )
    for name in forbidden_re_exports:
        assert name not in pkg.__all__, (
            f"execution_control/__init__.py must not re-export Slice 19 "
            f"module symbol {name!r} (per Slice 13A/14/15/16/17/18 "
            f"no-re-export discipline)"
        )
        assert not hasattr(pkg, name), (
            f"execution_control/__init__.py must not re-export Slice 19 "
            f"module symbol {name!r} (per Slice 13A/14/15/16/17/18 "
            f"no-re-export discipline)"
        )


# ── Test fixtures / constructors ────────────────────────────────────────────


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified Slice 13a :class:`GovernanceEvidenceRef`
    for tests.
    """

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-1",
        digest="a" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    """Construct a fully-specified Slice 16 :class:`GovernanceFinding`
    for tests.
    """

    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-abc",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk", "runtime": "claude-sdk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-27#slice-19-1st"],
        metric_refs=["tasks_per_hour"],
        estimated_lost_hours=3.5,
        estimated_retry_impact=0.12,
        recommended_action_display="Consider tightening commit retry budget.",
        recommendation_draft_ref=None,
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
        primary_cause_finding_id=None,
        linked_finding_ids=[],
    )
    base.update(overrides)
    return GovernanceFinding(**base)


def _recommendation(**overrides: object) -> GovernancePolicyRecommendation:
    """Construct a fully-specified Slice 17
    :class:`GovernancePolicyRecommendation` for tests.
    """

    base: dict[str, object] = dict(
        idempotency_key="recommendation-key-19-abc",
        recommendation_id="rec-19-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-abc"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml-7"},
            value={"wave_cap": 7},
            guardrails=["max_concurrent_tasks_per_lane"],
        ),
        activation_requirements=["replay_against_8ac124d6_passes"],
        rollback_requirements=["revert_to_prior_wave_cap"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)


def _replay_result(**overrides: object) -> CounterfactualResult:
    """Construct a fully-specified Slice 18 :class:`CounterfactualResult`
    for tests.
    """

    base: dict[str, object] = dict(
        result_id="result-19-1",
        result_version="v1",
        scenario_id="scenario-19-1",
        corpus_id="corpus-19-1",
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=["sample_size<10"],
        policy_provenance_refs=[_evidence_ref()],
        safety_guard_class=None,
        estimated_delta_hours=-2.5,
        estimated_delta_repair_cycles=-0.5,
        estimated_delta_commit_failures=-0.1,
        estimated_risk_change="lower",
        confidence=0.65,
        invalidated_by=[],
        supporting_finding_ids=["finding-key-19-abc"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)


def _snapshot(**overrides: object) -> GovernanceSnapshot:
    """Construct a fully-specified :class:`GovernanceSnapshot` for
    tests.
    """

    base: dict[str, object] = dict(
        corpus_id="corpus-19-1",
        snapshot_version="v1",
        snapshot_digest="d" * 64,
        generated_at=datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc),
        scorecard_id="scorecard-19-1",
        max_response_bytes=GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES,
        truncated=False,
        omitted_counts={"findings": 0, "recommendations": 0, "replay_results": 0},
        completeness="complete",
        page_refs=[],
        next_cursor=None,
        top_findings=[_finding()],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        evidence_quality="canonical",
        blocked_by=[],
    )
    base.update(overrides)
    return GovernanceSnapshot(**base)


def _agent_context(**overrides: object) -> GovernanceAgentContext:
    """Construct a fully-specified :class:`GovernanceAgentContext` for
    tests.
    """

    base: dict[str, object] = dict(
        task_id="task-19-1",
        repo_id="repo-19-1",
        relevant_findings=[_finding()],
        relevant_line_provenance=[
            {
                "path": "src/example.py",
                "line": 42,
                "blame_sha": "abc123",
            }
        ],
        policy_guidance=[_recommendation()],
        omitted_detail_refs=[],
        omitted_counts={"findings": 0, "line_provenance": 0, "policy_guidance": 0},
        completeness="complete",
        page_refs=[],
        truncated=False,
        max_prompt_chars=10_000,
    )
    base.update(overrides)
    return GovernanceAgentContext(**base)


# ── GovernanceSnapshot (doc-19:71-87) ──────────────────────────────────────


def test_snapshot_accepts_all_16_fields() -> None:
    """The 16 doc-19:71-87 fields all populate cleanly on a
    fully-specified :class:`GovernanceSnapshot`.

    Per doc-19:71-87 the surface is:
    corpus_id, snapshot_version, snapshot_digest, generated_at,
    scorecard_id, max_response_bytes, truncated, omitted_counts,
    completeness, page_refs, next_cursor, top_findings,
    recommendations, replay_results, evidence_quality, blocked_by =
    16 fields.
    """

    s = _snapshot()
    assert s.corpus_id == "corpus-19-1"
    assert s.snapshot_version == "v1"
    assert s.snapshot_digest == "d" * 64
    assert s.generated_at == datetime(2026, 5, 27, 12, 0, tzinfo=timezone.utc)
    assert s.scorecard_id == "scorecard-19-1"
    assert s.max_response_bytes == GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES
    assert s.truncated is False
    assert s.omitted_counts == {
        "findings": 0,
        "recommendations": 0,
        "replay_results": 0,
    }
    assert s.completeness == "complete"
    assert s.page_refs == []
    assert s.next_cursor is None
    assert len(s.top_findings) == 1
    assert isinstance(s.top_findings[0], GovernanceFinding)
    assert len(s.recommendations) == 1
    assert isinstance(s.recommendations[0], GovernancePolicyRecommendation)
    assert len(s.replay_results) == 1
    assert isinstance(s.replay_results[0], CounterfactualResult)
    assert s.evidence_quality == "canonical"
    assert s.blocked_by == []


def test_snapshot_field_count_is_16() -> None:
    """Per doc-19:71-87 :class:`GovernanceSnapshot` has exactly 16
    declared fields (the 16-field shape the chunk-shape spec calls
    out).
    """

    assert len(GovernanceSnapshot.model_fields) == 16


def test_snapshot_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _snapshot(unknown_field="oops")  # type: ignore[arg-type]


def test_snapshot_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed :class:`GovernanceSnapshot` ->
    JSON -> :class:`GovernanceSnapshot` round-trip is value-equivalent.
    """

    s = _snapshot()
    j = s.model_dump_json()
    s2 = GovernanceSnapshot.model_validate_json(j)
    assert s == s2


def test_snapshot_carries_extra_forbid_config() -> None:
    """Per the Slice 13A / 14 / 15 / 16 / 17 / 18 precedent
    :class:`GovernanceSnapshot` carries
    ``model_config = ConfigDict(extra="forbid")``.
    """

    assert GovernanceSnapshot.model_config.get("extra") == "forbid"


def test_snapshot_scorecard_id_optional_default_none() -> None:
    """Per the typed-shape design + the doc-19:194-195 *"Active workflow
    pressure: reporting returns cached snapshots..."* edge case the
    :attr:`scorecard_id` accepts ``None`` (cached / cross-corpus
    snapshots without a scorecard grounding).
    """

    s = _snapshot(scorecard_id=None)
    assert s.scorecard_id is None


def test_snapshot_next_cursor_optional_default_none() -> None:
    """Per doc-19:82 the :attr:`next_cursor` field accepts ``None``
    (the typical single-page-of-corpus case)."""

    s = _snapshot(next_cursor=None)
    assert s.next_cursor is None


def test_snapshot_truncated_flag_accepts_true() -> None:
    """Per doc-19:78 the :attr:`truncated` field accepts ``True`` (the
    truncated-preview case)."""

    s = _snapshot(
        truncated=True,
        page_refs=["page-ref-1", "page-ref-2"],
        omitted_counts={
            "findings": 5,
            "recommendations": 2,
            "replay_results": 1,
        },
    )
    assert s.truncated is True
    assert s.page_refs == ["page-ref-1", "page-ref-2"]


def test_snapshot_blocked_by_accepts_blocker_ids() -> None:
    """Per doc-19:87 + doc-19:186-189 the :attr:`blocked_by` field
    accepts a list of typed blocker-id strings.
    """

    blockers = ["stale_evidence:8ac124d6", "rate_limited:slack"]
    s = _snapshot(blocked_by=blockers)
    assert s.blocked_by == blockers


def test_snapshot_completeness_rejects_unknown_value() -> None:
    """Per the Slice 13a :data:`CompletenessState` Literal validation
    an unknown completeness value fails closed at construction.
    """

    with pytest.raises(ValidationError):
        _snapshot(completeness="random_made_up_state")


def test_snapshot_evidence_quality_rejects_unknown_value() -> None:
    """Per the Slice 13a :data:`EvidenceQuality` Literal validation an
    unknown evidence-quality value fails closed at construction.
    """

    with pytest.raises(ValidationError):
        _snapshot(evidence_quality="random_made_up_quality")


@pytest.mark.parametrize(
    "state",
    ["complete", "paged", "preview_only", "unavailable"],
)
def test_snapshot_accepts_all_4_completeness_values(state: str) -> None:
    """Per the Slice 13a :data:`CompletenessState` 4-value Literal
    every value is constructible.
    """

    s = _snapshot(completeness=state)
    assert s.completeness == state


@pytest.mark.parametrize(
    "quality",
    ["canonical", "derived", "sampled", "advisory", "stale", "insufficient"],
)
def test_snapshot_accepts_all_6_evidence_quality_values(quality: str) -> None:
    """Per the Slice 13a :data:`EvidenceQuality` 6-value Literal every
    value is constructible.
    """

    s = _snapshot(evidence_quality=quality)
    assert s.evidence_quality == quality


def test_snapshot_top_findings_annotation_is_list_of_direct_governance_finding() -> None:
    """**Slice 16 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:83 REUSE).

    The :attr:`GovernanceSnapshot.top_findings` field MUST be typed
    against ``list[GovernanceFinding]`` where :class:`GovernanceFinding`
    IS the Slice 16 1st sub-slice shared BaseModel imported from
    :mod:`iriai_build_v2.execution_control.finding_engine` (NOT a
    re-aliased copy).
    """

    annotation = GovernanceSnapshot.model_fields["top_findings"].annotation
    assert get_origin(annotation) is list
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert get_args(annotation)[0] is GovernanceFinding


def test_snapshot_recommendations_annotation_is_list_of_direct_governance_policy_recommendation() -> None:
    """**Slice 17 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:84 REUSE).

    The :attr:`GovernanceSnapshot.recommendations` field MUST be typed
    against ``list[GovernancePolicyRecommendation]`` where
    :class:`GovernancePolicyRecommendation` IS the Slice 17 1st
    sub-slice shared BaseModel imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation`.
    """

    annotation = GovernanceSnapshot.model_fields["recommendations"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is GovernancePolicyRecommendation


def test_snapshot_replay_results_annotation_is_list_of_direct_counterfactual_result() -> None:
    """**Slice 18 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:85 REUSE).

    The :attr:`GovernanceSnapshot.replay_results` field MUST be typed
    against ``list[CounterfactualResult]`` where
    :class:`CounterfactualResult` IS the Slice 18 1st sub-slice shared
    BaseModel imported from
    :mod:`iriai_build_v2.execution_control.counterfactual_replay`.
    """

    annotation = GovernanceSnapshot.model_fields["replay_results"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is CounterfactualResult


def test_snapshot_completeness_annotation_is_direct_completeness_state_literal() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:80 REUSE).

    The :attr:`GovernanceSnapshot.completeness` field MUST be typed
    against the DIRECT Slice 13a :data:`CompletenessState` Literal
    (NOT a re-aliased copy).
    """

    annotation = GovernanceSnapshot.model_fields["completeness"].annotation
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert annotation is CompletenessState
    assert set(get_args(annotation)) == {
        "complete",
        "paged",
        "preview_only",
        "unavailable",
    }


def test_snapshot_evidence_quality_annotation_is_direct_evidence_quality_literal() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:86 REUSE).

    The :attr:`GovernanceSnapshot.evidence_quality` field MUST be typed
    against the DIRECT Slice 13a :data:`EvidenceQuality` Literal.
    """

    annotation = GovernanceSnapshot.model_fields["evidence_quality"].annotation
    assert annotation is EvidenceQuality
    assert set(get_args(annotation)) == {
        "canonical",
        "derived",
        "sampled",
        "advisory",
        "stale",
        "insufficient",
    }


def test_snapshot_page_refs_is_list_of_str() -> None:
    """Per doc-19:81 the :attr:`page_refs` field is ``list[str]`` (the
    by-name reference shape mirroring the Slice 17 1st sub-slice
    ``GovernancePolicyRecommendation.source_finding_ids: list[str]``
    pattern).
    """

    annotation = GovernanceSnapshot.model_fields["page_refs"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is str


def test_snapshot_blocked_by_is_list_of_str() -> None:
    """Per doc-19:87 the :attr:`blocked_by` field is ``list[str]``
    (typed blocker-id strings).
    """

    annotation = GovernanceSnapshot.model_fields["blocked_by"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is str


def test_snapshot_omitted_counts_is_dict_str_int() -> None:
    """Per doc-19:79 the :attr:`omitted_counts` field is
    ``dict[str, int]``.
    """

    annotation = GovernanceSnapshot.model_fields["omitted_counts"].annotation
    assert get_origin(annotation) is dict
    args = get_args(annotation)
    assert args[0] is str
    assert args[1] is int


def test_snapshot_generated_at_is_datetime() -> None:
    """Per doc-19:75 the :attr:`generated_at` field is a typed
    ``datetime``.
    """

    annotation = GovernanceSnapshot.model_fields["generated_at"].annotation
    assert annotation is datetime


def test_snapshot_max_response_bytes_is_int() -> None:
    """Per doc-19:77 the :attr:`max_response_bytes` field is an
    ``int``.
    """

    annotation = GovernanceSnapshot.model_fields["max_response_bytes"].annotation
    assert annotation is int


def test_snapshot_truncated_is_bool() -> None:
    """Per doc-19:78 the :attr:`truncated` field is a ``bool``."""

    annotation = GovernanceSnapshot.model_fields["truncated"].annotation
    assert annotation is bool


# ── GovernanceAgentContext (doc-19:103-117) ────────────────────────────────


def test_agent_context_accepts_all_12_fields() -> None:
    """The 12 doc-19:103-117 fields (excluding Slice 21-conditional
    ``context_package``) all populate cleanly on a fully-specified
    :class:`GovernanceAgentContext`.

    Per doc-19:103-117 the surface is:
    task_id, repo_id, [context_package -- Slice 21 conditional, NOT
    in this 1st sub-slice], relevant_findings,
    relevant_line_provenance, policy_guidance,
    policy_guidance_authority, omitted_detail_refs, omitted_counts,
    completeness, page_refs, truncated, max_prompt_chars = 12 fields
    (13 with ``context_package`` after Slice 21).
    """

    c = _agent_context()
    assert c.task_id == "task-19-1"
    assert c.repo_id == "repo-19-1"
    assert len(c.relevant_findings) == 1
    assert isinstance(c.relevant_findings[0], GovernanceFinding)
    assert len(c.relevant_line_provenance) == 1
    assert c.relevant_line_provenance[0]["line"] == 42
    assert len(c.policy_guidance) == 1
    assert isinstance(c.policy_guidance[0], GovernancePolicyRecommendation)
    assert c.policy_guidance_authority == "advisory_only"
    assert c.omitted_detail_refs == []
    assert c.omitted_counts == {
        "findings": 0,
        "line_provenance": 0,
        "policy_guidance": 0,
    }
    assert c.completeness == "complete"
    assert c.page_refs == []
    assert c.truncated is False
    assert c.max_prompt_chars == 10_000


def test_agent_context_field_count_is_12() -> None:
    """Per doc-19:103-117 :class:`GovernanceAgentContext` has 12
    declared fields in this 1st sub-slice (the Slice 21-conditional
    ``context_package`` field per doc-19:106 + doc-19:125-127 lands
    at the future Slice 21 integration sub-slice).
    """

    assert len(GovernanceAgentContext.model_fields) == 12


def test_agent_context_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _agent_context(unknown_field="oops")  # type: ignore[arg-type]


def test_agent_context_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed :class:`GovernanceAgentContext`
    -> JSON -> :class:`GovernanceAgentContext` round-trip is value-
    equivalent.
    """

    c = _agent_context()
    j = c.model_dump_json()
    c2 = GovernanceAgentContext.model_validate_json(j)
    assert c == c2


def test_agent_context_carries_extra_forbid_config() -> None:
    """Per the Slice 13A / 14 / 15 / 16 / 17 / 18 precedent
    :class:`GovernanceAgentContext` carries
    ``model_config = ConfigDict(extra="forbid")``.
    """

    assert GovernanceAgentContext.model_config.get("extra") == "forbid"


def test_agent_context_task_id_accepts_none() -> None:
    """Per doc-19:104 the :attr:`task_id` field accepts ``None`` (the
    cross-task context case, e.g. a repo-level governance summary).
    """

    c = _agent_context(task_id=None)
    assert c.task_id is None


def test_agent_context_repo_id_accepts_none() -> None:
    """Per doc-19:105 the :attr:`repo_id` field accepts ``None`` (the
    cross-repo context case).
    """

    c = _agent_context(repo_id=None)
    assert c.repo_id is None


def test_agent_context_policy_guidance_authority_defaults_to_advisory_only() -> None:
    """**Doc-19:110 + doc-19:230-231 AC5 enforcement.** Per doc-19:110
    ``policy_guidance_authority: Literal["advisory_only"] =
    "advisory_only"`` (verbatim) the field has a hard-coded literal
    default; omitting the kwarg produces the default value.
    """

    c = _agent_context()
    assert c.policy_guidance_authority == "advisory_only"


def test_agent_context_policy_guidance_authority_explicit_advisory_only_accepted() -> None:
    """The :attr:`policy_guidance_authority` field accepts the
    explicit ``"advisory_only"`` value (the only allowed value per the
    typed Literal).
    """

    c = _agent_context(policy_guidance_authority="advisory_only")
    assert c.policy_guidance_authority == "advisory_only"


def test_agent_context_policy_guidance_authority_rejects_any_other_value() -> None:
    """**Doc-19:230-231 AC5 enforcement.** Per the typed
    ``Literal["advisory_only"]`` annotation any other value fails
    closed at construction with a typed ``ValidationError`` -- the
    typed-shape layer enforces the AC5 invariant *"Workflow agents
    receive governance policy guidance only as advisory context..."*
    by REJECTING any attempt to construct an
    :class:`GovernanceAgentContext` with a non-advisory authority.
    """

    for forbidden in (
        "authoritative",
        "policy_authority",
        "merge_authority",
        "checkpoint_authority",
        "runtime_authority",
        "executor_authority",
    ):
        with pytest.raises(ValidationError):
            _agent_context(policy_guidance_authority=forbidden)


def test_agent_context_policy_guidance_authority_annotation_is_literal_advisory_only() -> None:
    """**Doc-19:110 hard-coded literal annotation assertion.** The
    :attr:`policy_guidance_authority` annotation MUST be exactly
    ``Literal["advisory_only"]`` (the 1-value Literal that enforces
    AC5 at the typed-shape layer).
    """

    annotation = GovernanceAgentContext.model_fields[
        "policy_guidance_authority"
    ].annotation
    # The Literal has exactly 1 value: "advisory_only".
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] == "advisory_only"


def test_agent_context_completeness_rejects_unknown_value() -> None:
    """Per the Slice 13a :data:`CompletenessState` Literal validation
    an unknown completeness value fails closed at construction.
    """

    with pytest.raises(ValidationError):
        _agent_context(completeness="random_made_up_state")


@pytest.mark.parametrize(
    "state",
    ["complete", "paged", "preview_only", "unavailable"],
)
def test_agent_context_accepts_all_4_completeness_values(state: str) -> None:
    """Per the Slice 13a :data:`CompletenessState` 4-value Literal
    every value is constructible on a :class:`GovernanceAgentContext`.
    """

    c = _agent_context(completeness=state)
    assert c.completeness == state


def test_agent_context_relevant_findings_annotation_is_list_of_direct_governance_finding() -> None:
    """**Slice 16 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:107 REUSE).
    """

    annotation = GovernanceAgentContext.model_fields[
        "relevant_findings"
    ].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is GovernanceFinding


def test_agent_context_policy_guidance_annotation_is_list_of_direct_governance_policy_recommendation() -> None:
    """**Slice 17 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:109 REUSE).

    Per doc-19:174-176 *"Agent `policy_guidance` is prompt context
    only..."* + doc-19:230-231 AC5 the typed surface enforces the
    advisory-only contract via the typed
    :attr:`policy_guidance_authority: Literal["advisory_only"]` hard-
    coded literal default; the :attr:`policy_guidance` list itself is
    typed as ``list[GovernancePolicyRecommendation]`` (the Slice 17
    1st sub-slice shared BaseModel).
    """

    annotation = GovernanceAgentContext.model_fields[
        "policy_guidance"
    ].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is GovernancePolicyRecommendation


def test_agent_context_completeness_annotation_is_direct_completeness_state_literal() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern; doc-19:113 REUSE).
    """

    annotation = GovernanceAgentContext.model_fields["completeness"].annotation
    assert annotation is CompletenessState


def test_agent_context_omitted_detail_refs_is_list_of_str() -> None:
    """Per doc-19:111 the :attr:`omitted_detail_refs` field is
    ``list[str]`` (the by-name reference shape for omitted-evidence
    page-refs).
    """

    annotation = GovernanceAgentContext.model_fields[
        "omitted_detail_refs"
    ].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is str


def test_agent_context_page_refs_is_list_of_str() -> None:
    """Per doc-19:114 the :attr:`page_refs` field is ``list[str]`` (the
    by-name reference shape mirroring
    :attr:`GovernanceSnapshot.page_refs`).
    """

    annotation = GovernanceAgentContext.model_fields["page_refs"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is str


def test_agent_context_relevant_line_provenance_is_list_of_dict() -> None:
    """Per doc-19:108 the :attr:`relevant_line_provenance` field is
    ``list[dict[str, Any]]`` (the free-form per-line-provenance dict
    shape; the typed Slice 14 line-provenance shape lands at the
    future Slice 19 5th sub-slice agent-context builder).
    """

    annotation = GovernanceAgentContext.model_fields[
        "relevant_line_provenance"
    ].annotation
    assert get_origin(annotation) is list
    inner = get_args(annotation)[0]
    assert get_origin(inner) is dict
    inner_args = get_args(inner)
    assert inner_args[0] is str
    assert inner_args[1] is Any


def test_agent_context_max_prompt_chars_is_int() -> None:
    """Per doc-19:116 the :attr:`max_prompt_chars` field is an
    ``int``.
    """

    annotation = GovernanceAgentContext.model_fields["max_prompt_chars"].annotation
    assert annotation is int


def test_agent_context_truncated_is_bool() -> None:
    """Per doc-19:115 the :attr:`truncated` field is a ``bool``."""

    annotation = GovernanceAgentContext.model_fields["truncated"].annotation
    assert annotation is bool


# ── canonical_governance_snapshot_dict (doc-19:152-153) ────────────────────


def test_canonical_governance_snapshot_dict_is_json_serialisable() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17 +
    Slice 18 canonical-JSON discipline the canonical-dict projection
    of a :class:`GovernanceSnapshot` is JSON-serialisable.
    """

    s = _snapshot()
    d = canonical_governance_snapshot_dict(s)
    assert isinstance(d, dict)
    # The dict roundtrips through json.dumps (proves all values
    # serialise).
    _json.dumps(d, sort_keys=True)


def test_canonical_governance_snapshot_dict_is_deterministic() -> None:
    """Two calls with the same logical :class:`GovernanceSnapshot` MUST
    produce equal canonical dicts (the cross-process determinism
    contract :func:`compute_governance_snapshot_digest` relies on).
    """

    s1 = _snapshot()
    s2 = _snapshot()
    assert canonical_governance_snapshot_dict(s1) == (
        canonical_governance_snapshot_dict(s2)
    )


def test_canonical_governance_snapshot_dict_round_trip_stable() -> None:
    """A :class:`GovernanceSnapshot` -> canonical dict -> JSON -> dict
    round-trip preserves the canonical dict (no key reordering).
    """

    s = _snapshot()
    d = canonical_governance_snapshot_dict(s)
    j = _json.dumps(d, sort_keys=True)
    d2 = _json.loads(j)
    assert d == d2


def test_canonical_governance_snapshot_dict_serialises_datetime_to_iso8601() -> None:
    """Per the ``model_dump(mode='json')`` discipline the
    :attr:`GovernanceSnapshot.generated_at` typed ``datetime`` projects
    to its ISO-8601 string form (cross-process stable).
    """

    s = _snapshot()
    d = canonical_governance_snapshot_dict(s)
    assert isinstance(d["generated_at"], str)
    # ISO-8601 format starts with year-month-day.
    assert d["generated_at"].startswith("2026-05-27")


# ── compute_governance_snapshot_digest (doc-19:152-153) ────────────────────


def _digest_kwargs(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = dict(
        corpus_id="corpus-19-1",
        snapshot_version="v1",
        scorecard_id="scorecard-19-1",
        finding_idempotency_keys=["finding-key-19-abc"],
        recommendation_idempotency_keys=["recommendation-key-19-abc"],
        replay_result_ids=["result-19-1"],
        replay_result_versions=["v1"],
        omitted_counts={"findings": 0, "recommendations": 0, "replay_results": 0},
        evidence_quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return base


def test_compute_governance_snapshot_digest_is_deterministic() -> None:
    """The helper MUST produce identical digests for identical inputs
    (the cross-process freshness contract mirroring Slice 18 1st
    sub-slice :func:`compute_counterfactual_idempotency_key`).
    """

    k1 = compute_governance_snapshot_digest(**_digest_kwargs())  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(**_digest_kwargs())  # type: ignore[arg-type]
    assert k1 == k2
    # And the digest is a 64-char SHA-256 hex digest.
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


def test_compute_governance_snapshot_digest_differs_on_corpus_id_change() -> None:
    """A different :attr:`corpus_id` MUST produce a different digest."""

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(corpus_id="corpus-19-1")
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(corpus_id="corpus-19-2")
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_snapshot_version_change() -> None:
    """**Doc-19:152-153 binding statement enforcement.** Per
    doc-19:152-153 *"The API computes `snapshot_digest` from bounded
    row ids, row digests, omitted-counts, evidence-quality values, and
    recommendation/replay versions."* a different
    :attr:`snapshot_version` MUST produce a different digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(snapshot_version="v1")
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(snapshot_version="v2")
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_scorecard_id_change() -> None:
    """A different :attr:`scorecard_id` MUST produce a different
    digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(scorecard_id="scorecard-19-1")
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(scorecard_id="scorecard-19-2")
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_finding_keys_change() -> None:
    """A different :attr:`finding_idempotency_keys` set MUST produce a
    different digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(finding_idempotency_keys=["finding-a"])
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(finding_idempotency_keys=["finding-b"])
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_recommendation_keys_change() -> None:
    """A different :attr:`recommendation_idempotency_keys` set MUST
    produce a different digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(recommendation_idempotency_keys=["rec-a"])
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(recommendation_idempotency_keys=["rec-b"])
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_replay_result_ids_change() -> None:
    """A different :attr:`replay_result_ids` set MUST produce a
    different digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_ids=["result-a"])
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_ids=["result-b"])
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_replay_result_versions_change() -> None:
    """**Doc-19:152-153 binding statement enforcement (replay version
    axis).** A different :attr:`replay_result_versions` set MUST
    produce a different digest (per doc-19:152-153 *"...and
    recommendation/replay versions."*).
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_versions=["v1"])
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_versions=["v2"])
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_omitted_counts_change() -> None:
    """A different :attr:`omitted_counts` dict MUST produce a different
    digest.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(omitted_counts={"findings": 0})
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(omitted_counts={"findings": 5})
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_evidence_quality_change() -> None:
    """**Doc-19:152-153 binding statement enforcement (evidence-quality
    axis).** A different :attr:`evidence_quality` MUST produce a
    different digest (per doc-19:152-153 *"...evidence-quality
    values..."*).
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(evidence_quality="canonical")
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(evidence_quality="stale")
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_differs_on_completeness_change() -> None:
    """A different :attr:`completeness` value MUST produce a different
    digest (per the Slice 13A invariant + doc-19:152-153 row-shape
    contract).
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(completeness="complete")
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(completeness="paged")
    )  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_governance_snapshot_digest_is_order_invariant_on_finding_keys() -> None:
    """Per the Slice 18 1st sub-slice
    :func:`compute_counterfactual_idempotency_key` order-invariance
    pattern the digest MUST be order-invariant w.r.t.
    finding-key ordering (the helper sorts the list before digesting).
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            finding_idempotency_keys=["finding-a", "finding-b", "finding-c"]
        )
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            finding_idempotency_keys=["finding-c", "finding-b", "finding-a"]
        )
    )  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_governance_snapshot_digest_is_order_invariant_on_recommendation_keys() -> None:
    """The digest MUST be order-invariant w.r.t. recommendation-key
    ordering.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            recommendation_idempotency_keys=["rec-a", "rec-b", "rec-c"]
        )
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            recommendation_idempotency_keys=["rec-c", "rec-b", "rec-a"]
        )
    )  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_governance_snapshot_digest_is_order_invariant_on_replay_result_ids() -> None:
    """The digest MUST be order-invariant w.r.t. replay-result-id
    ordering.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            replay_result_ids=["result-a", "result-b", "result-c"]
        )
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(
            replay_result_ids=["result-c", "result-b", "result-a"]
        )
    )  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_governance_snapshot_digest_is_order_invariant_on_replay_result_versions() -> None:
    """The digest MUST be order-invariant w.r.t. replay-result-version
    ordering.
    """

    k1 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_versions=["v1", "v2", "v3"])
    )  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(
        **_digest_kwargs(replay_result_versions=["v3", "v2", "v1"])
    )  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_governance_snapshot_digest_accepts_empty_lists() -> None:
    """The helper MUST accept all-empty list inputs and produce a
    stable digest (the typed empty-snapshot case for a blocked or
    no-findings corpus).
    """

    k = compute_governance_snapshot_digest(
        **_digest_kwargs(
            finding_idempotency_keys=[],
            recommendation_idempotency_keys=[],
            replay_result_ids=[],
            replay_result_versions=[],
            omitted_counts={},
        )
    )  # type: ignore[arg-type]
    assert isinstance(k, str)
    assert len(k) == 64


def test_compute_governance_snapshot_digest_accepts_none_scorecard_id() -> None:
    """The helper MUST accept ``None`` for the :attr:`scorecard_id`
    input (the cached / cross-corpus snapshot case where no scorecard
    grounding exists).
    """

    k = compute_governance_snapshot_digest(
        **_digest_kwargs(scorecard_id=None)
    )  # type: ignore[arg-type]
    assert isinstance(k, str)
    assert len(k) == 64


def test_compute_governance_snapshot_digest_distinguishes_none_from_empty_string() -> None:
    """The helper MUST distinguish ``None`` scorecard from the empty
    string (the JSON canonical-form null/empty distinction).
    """

    k_none = compute_governance_snapshot_digest(
        **_digest_kwargs(scorecard_id=None)
    )  # type: ignore[arg-type]
    k_empty = compute_governance_snapshot_digest(
        **_digest_kwargs(scorecard_id="")
    )  # type: ignore[arg-type]
    assert k_none != k_empty


# ── ConfigDict(extra="forbid") discipline (2 BaseModels) ───────────────────


@pytest.mark.parametrize(
    "model_cls",
    [
        GovernanceSnapshot,
        GovernanceAgentContext,
    ],
)
def test_all_2_base_models_carry_extra_forbid(model_cls: type) -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17 +
    Slice 18 precedent all Slice 19 typed BaseModels carry
    ``model_config = ConfigDict(extra="forbid")`` so typo-d kwargs
    raise a typed ``ValidationError`` (per the auto-memory
    ``feedback_no_silent_degradation`` rule).
    """

    assert model_cls.model_config.get("extra") == "forbid"  # type: ignore[attr-defined]


# ── Default-budget constants (doc-19:121-127) ──────────────────────────────


def test_governance_snapshot_default_max_bytes_is_262144() -> None:
    """**Doc-19:121 enforcement.** Per doc-19:121 *"Governance snapshot:
    256 KB serialized JSON..."* the typed default is exactly 262 144
    bytes (256 KB).
    """

    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES == 262_144


def test_governance_snapshot_default_max_findings_is_20() -> None:
    """**Doc-19:121 enforcement.** Per doc-19:121 *"...20 findings..."*
    the typed default is exactly 20.
    """

    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS == 20


def test_governance_snapshot_default_max_recommendations_is_10() -> None:
    """**Doc-19:121 enforcement.** Per doc-19:121 *"...10
    recommendations..."* the typed default is exactly 10.
    """

    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS == 10


def test_governance_snapshot_default_max_replay_results_is_10() -> None:
    """**Doc-19:121-122 enforcement.** Per doc-19:121-122 *"...10
    replay results..."* the typed default is exactly 10.
    """

    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS == 10


def test_governance_agent_context_max_prompt_chars_cap_is_20000() -> None:
    """**Doc-19:124 enforcement.** Per doc-19:124 *"Agent context:
    `max_prompt_chars` from caller, hard-capped at 20,000 chars..."*
    the typed hard cap is exactly 20 000.
    """

    assert GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP == 20_000


def test_default_budget_constants_are_all_int() -> None:
    """All 5 default-budget constants are typed as ``int`` (per the
    typed-shape design)."""

    assert isinstance(GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES, int)
    assert isinstance(GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS, int)
    assert isinstance(GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS, int)
    assert isinstance(GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS, int)
    assert isinstance(GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP, int)


def test_default_budget_constants_are_all_positive() -> None:
    """All 5 default-budget constants are positive (a non-positive
    default would not be a sensible budget)."""

    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES > 0
    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS > 0
    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS > 0
    assert GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS > 0
    assert GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP > 0


# ── Activation-boundary discipline (doc-19:230-231 AC5 + doc-19:348-349 AC)


def test_no_mutation_methods_on_any_basemodel() -> None:
    """**Doc-19:234 AC7 + doc-19:348-349 AC enforcement.** Per
    doc-19:234 *"Reporting honors Slice 10 read-only and bounded-read
    guarantees."* + doc-19:348-349 *"Supervisor/dashboard read-only
    contract preserved (no governance writer extends the Slice 10c-1
    `CONTROL_PLANE_WRITER_METHODS` set)."* the typed surface MUST NOT
    expose mutation methods on either of the 2 BaseModels (the read-
    only typed-shape design is the AC7 axis at the typed-shape layer;
    the persistence-side enforcement lives in future Slice 19
    sub-slices).
    """

    forbidden_method_prefixes = (
        "activate_",
        "apply_",
        "commit_",
        "mutate_",
        "dispatch_",
        "schedule_",
        "write_route_table",
        "write_scheduler_policy",
        "write_merge_queue_policy",
    )
    for model_cls in (GovernanceSnapshot, GovernanceAgentContext):
        for attr in dir(model_cls):
            # Skip private + dunder + Pydantic-internal methods.
            if attr.startswith("_"):
                continue
            # Skip Pydantic BaseModel standard methods (model_*, etc.).
            if attr.startswith("model_"):
                continue
            # Skip Pydantic BaseModel standard methods (copy, dict, etc.).
            if attr in ("copy", "dict", "json", "construct"):
                continue
            for forbidden in forbidden_method_prefixes:
                assert not attr.startswith(forbidden), (
                    f"{model_cls.__name__} must not expose mutation method "
                    f"{attr!r} (per doc-19:234 AC7 + doc-19:348-349 AC)."
                )


def test_no_local_dag_authority_artifact_keys() -> None:
    """**Doc-19:348-349 AC enforcement.** Per doc-19:348-349
    *"Supervisor/dashboard read-only contract preserved (no governance
    writer extends the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS`
    set)."* + the Slice 17 7th sub-slice activation-boundary
    discipline the Slice 19 1st sub-slice typed-shape module MUST NOT
    carry any ``dag-*`` execution-authority artifact-key literals
    (those would imply the typed-shape module is writing consumer
    authority artifacts, which violates the doc-19:348-349 AC).

    The (future) Slice 19 6th sub-slice report-artifact writer at
    ``review:governance-report:{corpus_id}`` may carry the review
    artifact-key literal; this 1st sub-slice is pure typed-shape and
    MUST NOT carry consumer-authority artifact keys.
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden_consumer_artifact_keys = (
        '"dag-regroup-active:',
        '"dag-regroup-overlay:',
        '"route-budget:',
        '"supervisor-action:',
        '"merge-queue:',
    )
    for f in forbidden_consumer_artifact_keys:
        assert f not in src, (
            f"governance_agent.py 1st sub-slice must not carry consumer-"
            f"owned artifact-key literal {f!r}; per doc-19:348-349 the "
            f"governance-agent surface does NOT write consumer authority "
            f"artifacts."
        )


def test_no_control_plane_writer_methods_extension() -> None:
    """**Doc-19:348-349 AC enforcement.** Per doc-19:348-349 *"...no
    governance writer extends the Slice 10c-1 `CONTROL_PLANE_WRITER_METHODS`
    set)."* the Slice 19 1st sub-slice typed-shape module MUST NOT
    contain code that extends the
    ``CONTROL_PLANE_WRITER_METHODS`` set (e.g. via ``.add(``,
    ``.update(``, ``|=`` operators on that name).
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    # Negative-extend patterns -- the typed surface must not write to
    # the Slice 10c-1 control-plane-writer-methods set.
    forbidden_extension_patterns = (
        "CONTROL_PLANE_WRITER_METHODS.add(",
        "CONTROL_PLANE_WRITER_METHODS.update(",
        "CONTROL_PLANE_WRITER_METHODS |=",
        "CONTROL_PLANE_WRITER_METHODS = CONTROL_PLANE_WRITER_METHODS |",
        "CONTROL_PLANE_WRITER_METHODS += ",
    )
    for f in forbidden_extension_patterns:
        assert f not in src, (
            f"governance_agent.py 1st sub-slice must not extend the Slice "
            f"10c-1 `CONTROL_PLANE_WRITER_METHODS` set; found {f!r}. Per "
            f"doc-19:348-349 the supervisor/dashboard read-only contract "
            f"is preserved by NOT extending the writer methods set."
        )


# ── doc-19:222-235 Acceptance Criteria awareness ───────────────────────────


def test_doc_19_224_ac1_bounded_reproducible_evidence_cited_surface_present() -> None:
    """**Doc-19:224 AC1 binding statement awareness.** Per doc-19:224
    *"Reports are bounded, reproducible, evidence-cited, and structured
    first."* the typed surface MUST expose all 4 axes:

    * **Bounded** -- via :attr:`GovernanceSnapshot.max_response_bytes`
      + :attr:`GovernanceSnapshot.truncated` +
      :attr:`GovernanceSnapshot.omitted_counts` +
      :attr:`GovernanceSnapshot.page_refs`.
    * **Reproducible** -- via :attr:`GovernanceSnapshot.snapshot_digest`
      + the :func:`compute_governance_snapshot_digest` helper.
    * **Evidence-cited** -- via the typed REUSE on
      :attr:`GovernanceSnapshot.top_findings` (Slice 16) +
      :attr:`GovernanceSnapshot.recommendations` (Slice 17) +
      :attr:`GovernanceSnapshot.replay_results` (Slice 18).
    * **Structured first** -- via the typed BaseModel design (no prose
      fields on the typed shape).

    This test PINs the 4-axis surface at the typed-shape layer.
    """

    # 1. Bounded axis -- max_response_bytes + truncated + omitted_counts
    # + page_refs all present.
    assert "max_response_bytes" in GovernanceSnapshot.model_fields
    assert "truncated" in GovernanceSnapshot.model_fields
    assert "omitted_counts" in GovernanceSnapshot.model_fields
    assert "page_refs" in GovernanceSnapshot.model_fields

    # 2. Reproducible axis -- snapshot_digest field + helper.
    assert "snapshot_digest" in GovernanceSnapshot.model_fields
    assert callable(compute_governance_snapshot_digest)
    assert callable(canonical_governance_snapshot_dict)

    # 3. Evidence-cited axis -- top_findings + recommendations +
    # replay_results all typed REUSE on Slice 16/17/18.
    assert "top_findings" in GovernanceSnapshot.model_fields
    assert (
        get_args(GovernanceSnapshot.model_fields["top_findings"].annotation)[0]
        is GovernanceFinding
    )
    assert "recommendations" in GovernanceSnapshot.model_fields
    assert (
        get_args(
            GovernanceSnapshot.model_fields["recommendations"].annotation
        )[0]
        is GovernancePolicyRecommendation
    )
    assert "replay_results" in GovernanceSnapshot.model_fields
    assert (
        get_args(
            GovernanceSnapshot.model_fields["replay_results"].annotation
        )[0]
        is CounterfactualResult
    )


def test_doc_19_225_226_ac2_truncated_preview_authority_blocker_surface_present() -> None:
    """**Doc-19:225-226 AC2 binding statement awareness.** Per
    doc-19:225-226 *"Truncated or preview reports are never
    authoritative unless exact page refs and completeness metadata
    cover the consumer's required scope."* the typed surface MUST
    expose the 3-field triple that lets consumers detect display-only
    state at construction:

    * :attr:`truncated` -- the bool flag.
    * :attr:`page_refs` -- the exact page-ref-id list.
    * :attr:`completeness` -- the Slice 13a
      :data:`CompletenessState` Literal.
    """

    # All 3 fields present on GovernanceSnapshot.
    assert "truncated" in GovernanceSnapshot.model_fields
    assert "page_refs" in GovernanceSnapshot.model_fields
    assert "completeness" in GovernanceSnapshot.model_fields

    # All 3 fields present on GovernanceAgentContext (the same AC2
    # axis for the agent-context surface).
    assert "truncated" in GovernanceAgentContext.model_fields
    assert "page_refs" in GovernanceAgentContext.model_fields
    assert "completeness" in GovernanceAgentContext.model_fields


def test_doc_19_227_ac3_compact_governance_context_at_task_execute_time_surface_present() -> None:
    """**Doc-19:227 AC3 binding statement awareness.** Per doc-19:227
    *"Workflow agents can receive compact governance context at task
    execute time."* the typed surface MUST expose the
    :class:`GovernanceAgentContext` shape (the compact context
    surface).
    """

    # GovernanceAgentContext exists + has the required scoping fields.
    assert "task_id" in GovernanceAgentContext.model_fields
    assert "repo_id" in GovernanceAgentContext.model_fields
    assert "relevant_findings" in GovernanceAgentContext.model_fields
    assert "relevant_line_provenance" in GovernanceAgentContext.model_fields
    assert "policy_guidance" in GovernanceAgentContext.model_fields
    assert "max_prompt_chars" in GovernanceAgentContext.model_fields


def test_doc_19_230_231_ac5_advisory_only_policy_guidance_surface_present() -> None:
    """**Doc-19:230-231 AC5 binding statement awareness.** Per
    doc-19:230-231 *"Workflow agents receive governance policy
    guidance only as advisory context; contracts, gates, router, and
    merge queue remain authoritative."* the typed surface MUST expose
    the advisory-only enforcer:

    * :attr:`policy_guidance_authority: Literal["advisory_only"] =
      "advisory_only"` -- the hard-coded literal default per
      doc-19:110.

    Pydantic Literal validation rejects any other value at
    construction with a typed ``ValidationError``; this test PINs the
    AC5 axis at the typed-shape layer.
    """

    # The field exists + has the typed Literal["advisory_only"]
    # annotation + default value "advisory_only".
    annotation = GovernanceAgentContext.model_fields[
        "policy_guidance_authority"
    ].annotation
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] == "advisory_only"
    assert (
        GovernanceAgentContext.model_fields[
            "policy_guidance_authority"
        ].default
        == "advisory_only"
    )


def test_doc_19_232_233_ac6_human_facing_evidence_quality_omitted_details_surface_present() -> None:
    """**Doc-19:232-233 AC6 binding statement awareness.** Per
    doc-19:232-233 *"Human-facing dashboard/Slack output explains top
    findings without hiding evidence quality or omitted details."*
    the typed surface MUST expose:

    * :attr:`GovernanceSnapshot.evidence_quality` -- the Slice 13a
      :data:`EvidenceQuality` Literal field.
    * :attr:`GovernanceSnapshot.omitted_counts` -- the
      ``dict[str, int]`` field.
    """

    assert "evidence_quality" in GovernanceSnapshot.model_fields
    assert "omitted_counts" in GovernanceSnapshot.model_fields

    # evidence_quality is REQUIRED (no default; the human-facing
    # surface cannot omit the evidence-quality citation).
    assert GovernanceSnapshot.model_fields["evidence_quality"].is_required()


def test_doc_19_234_ac7_read_only_bounded_read_guarantees_surface_present() -> None:
    """**Doc-19:234 AC7 binding statement awareness.** Per doc-19:234
    *"Reporting honors Slice 10 read-only and bounded-read guarantees."*
    the typed surface MUST be read-only (no mutation methods; this is
    covered by the separate ``test_no_mutation_methods_on_any_basemodel``
    test) + must expose the bounded-read fields:

    * :attr:`GovernanceSnapshot.max_response_bytes` -- the byte cap.
    * :attr:`GovernanceSnapshot.next_cursor` -- the pagination cursor.
    * :attr:`GovernanceAgentContext.max_prompt_chars` -- the prompt
      cap.
    """

    assert "max_response_bytes" in GovernanceSnapshot.model_fields
    assert "next_cursor" in GovernanceSnapshot.model_fields
    assert "max_prompt_chars" in GovernanceAgentContext.model_fields


def test_doc_19_235_ac8_implementation_log_anchors_visible_in_plan_vs_actual_reports_surface_present() -> None:
    """**Doc-19:235 AC8 binding statement awareness.** Per doc-19:235
    *"Implementation-log anchors are visible in plan-vs-actual
    reports."* the typed surface MUST expose the
    :attr:`GovernanceSnapshot.top_findings` field whose Slice 16 typed
    REUSE preserves the
    :attr:`GovernanceFinding.implementation_log_anchors` surface.
    """

    # top_findings is typed as list[GovernanceFinding].
    annotation = GovernanceSnapshot.model_fields["top_findings"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is GovernanceFinding

    # The Slice 16 GovernanceFinding shape has the
    # implementation_log_anchors field (the typed REUSE preserves it).
    assert "implementation_log_anchors" in GovernanceFinding.model_fields


# ── doc-19:256-303 Slice 13A consumption awareness ─────────────────────────


def test_doc_19_256_303_no_local_completeness_state_redefinition() -> None:
    """Per doc-19:256-303 Slice 13A Shared Completeness Model
    Dependency the Slice 19 module MUST NOT redefine the shared
    Slice 13A typed shapes locally (the module imports them by
    identity from
    :mod:`iriai_build_v2.workflows.develop.governance.models`).
    """

    import iriai_build_v2.execution_control.governance_agent as mod

    src = open(mod.__file__).read()
    forbidden_redefinitions = (
        "class CompletenessState",
        "class EvidenceCompleteness",
        "class AuthoritativePromptContextRouting",
        "class AuthoritativeGateCompanionRecord",
        "class AuthoritativeGateProofRow",
        "class AuthoritativeSnapshotListFieldCompleteness",
        "class AuthoritativeSnapshotClassifierRouting",
        "class ExactEvidenceManifest",
        "class AuthoritativeContextRef",
        "class EvidencePageRef",
        # Slice 16 / 17 / 18 redefinitions also forbidden (per the
        # no-second-source-of-truth discipline).
        "class GovernanceFinding",
        "class GovernanceMetricValue",
        "class GovernancePolicyRecommendation",
        "class PolicyRecommendationDecision",
        "class CounterfactualResult",
        "class CounterfactualScenario",
        "class ReplayCorpus",
        # The Slice 21-conditional ContextLayerPackageSummary is also
        # NOT redefined locally (it lands at the future Slice 21
        # integration sub-slice).
        "class ContextLayerPackageSummary",
        # CompletenessState + EvidenceQuality are typed as Literals
        # in the Slice 13a models module; the Slice 19 module MUST NOT
        # redefine the Literals locally.
        "EvidenceQuality = Literal",
        "CompletenessState = Literal",
    )
    for forbidden in forbidden_redefinitions:
        assert forbidden not in src, (
            f"governance_agent.py must not redefine the Slice 13A / 13a / "
            f"16 / 17 / 18 / 21 shared shape {forbidden!r}; consume the "
            f"shared shape via direct import instead."
        )


def test_doc_19_185_186_governance_snapshot_stale_blocker_id_surface_present() -> None:
    """**Doc-19:186-187 edge-case awareness.** Per doc-19:186-187
    *"Governance snapshot stale: report stale status and do not
    present new recommendations as current."* the typed surface MUST
    expose the :attr:`blocked_by` field on
    :class:`GovernanceSnapshot` (the per-blocker-id string list that
    the future Slice 19 2nd sub-slice snapshot API populates with
    blocker-id strings like ``"stale_evidence:8ac124d6"``).
    """

    assert "blocked_by" in GovernanceSnapshot.model_fields
    annotation = GovernanceSnapshot.model_fields["blocked_by"].annotation
    assert get_origin(annotation) is list
    assert get_args(annotation)[0] is str

    # And the typed surface accepts the typical stale-evidence blocker
    # id string at construction.
    s = _snapshot(blocked_by=["stale_evidence:8ac124d6"])
    assert s.blocked_by == ["stale_evidence:8ac124d6"]


def test_doc_19_172_173_etag_seed_is_snapshot_digest_surface_present() -> None:
    """**Doc-19:172-173 dashboard ETag seed contract awareness.** Per
    doc-19:172-173 *"...The ETag seed is `snapshot_digest`."* the
    typed surface MUST expose the :attr:`snapshot_digest` string field
    on :class:`GovernanceSnapshot` (the future Slice 19 3rd sub-slice
    dashboard view reads this for ETag generation).
    """

    assert "snapshot_digest" in GovernanceSnapshot.model_fields
    annotation = GovernanceSnapshot.model_fields["snapshot_digest"].annotation
    assert annotation is str


def test_doc_19_201_202_slack_dedupe_key_is_snapshot_digest_surface_present() -> None:
    """**Doc-19:201-202 Slack dedupe contract awareness.** Per
    doc-19:201-202 *"Slack digest dedupes repeated identical
    governance snapshots by `snapshot_digest`..."* the typed surface
    MUST expose the deterministic
    :func:`compute_governance_snapshot_digest` helper (the future
    Slice 19 4th sub-slice Slack rendering reads this for dedupe).
    """

    # The deterministic helper is callable + produces stable digests
    # for the same logical inputs.
    kwargs = _digest_kwargs()
    k1 = compute_governance_snapshot_digest(**kwargs)  # type: ignore[arg-type]
    k2 = compute_governance_snapshot_digest(**kwargs)  # type: ignore[arg-type]
    assert k1 == k2  # Stable Slack-dedupe key for identical snapshots.
