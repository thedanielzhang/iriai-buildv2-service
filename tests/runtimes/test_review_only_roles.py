"""Gate/integration reviewer actors must dispatch as read-only pool jobs.

Regression tests for the plan-review SEAL gate crash:
    RuntimeError('Claude pool write-producing job requires runtime workspace binding')

The lead-*-gate-reviewer actors used to wrap the FULL generation roles
(lead_pm_role / lead_designer_role / lead_architect_role), whose tools include
Write/Bash. claude_pool._role_is_write_producing classified their jobs
write-producing, and the pool worker (claude_pool._validate_bound_job_manifest)
demands a runtime workspace binding for such jobs — a binding gate-review asks
never carry. The jobs only survived while the in-process codex member (which
skips the manifest validation) served them; once codex went usage-limited the
failover to a claude member crashed the run.

Fix: dedicated review-only Role variants (roles._review_only_role) with
Write/Edit/Bash stripped and the SAME Role.name, so economy-mode model
overrides (config.ECONOMY_MODEL_OVERRIDES, keyed by Role.name) still pin the
seal-gate reviewers to fable.
"""

from __future__ import annotations

import pytest

import iriai_build_v2.runtimes.claude as claude_runtime
from iriai_build_v2.config import BUDGET_TIERS, ECONOMY_MODEL_OVERRIDES
from iriai_build_v2.roles import (
    lead_architect_gate_reviewer,
    lead_architect_review_role,
    lead_architect_reviewer,
    lead_architect_role,
    lead_designer_gate_reviewer,
    lead_designer_review_role,
    lead_designer_reviewer,
    lead_designer_role,
    lead_pm_gate_reviewer,
    lead_pm_review_role,
    lead_pm_reviewer,
    lead_pm_role,
    lead_task_planner_gate_reviewer,
    lead_task_planner_review_role,
    lead_task_planner_reviewer,
    lead_task_planner_role,
    planning_lead_ask_role,
    planning_lead_review_role,
    planning_lead_role,
)
from iriai_build_v2.runtimes.claude import _resolve_model_and_effort
from iriai_build_v2.runtimes.claude_pool import (
    _manifest_role_is_write_producing,
    _role_is_write_producing,
)
from iriai_build_v2.workflows.planning.phases.task_planning import (
    _sf_task_planner_gate_reviewer,
    _sf_task_planner_reviewer,
    _slice_planner_actor,
    _workstream_planner,
)

REVIEWER_ACTORS = [
    lead_pm_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_architect_gate_reviewer,
    lead_pm_reviewer,
    lead_designer_reviewer,
    lead_architect_reviewer,
    lead_task_planner_reviewer,
    lead_task_planner_gate_reviewer,
    _sf_task_planner_gate_reviewer,
    _sf_task_planner_reviewer,
]

GATE_REVIEWER_ACTORS = [
    lead_pm_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_architect_gate_reviewer,
]


@pytest.mark.parametrize(
    "actor", REVIEWER_ACTORS, ids=lambda actor: actor.name,
)
def test_reviewer_actor_role_is_not_write_producing(actor) -> None:
    """Pool dispatch must never demand a workspace binding for reviewer asks."""
    assert not _role_is_write_producing(actor.role), (
        f"{actor.name} wraps a write-producing role "
        f"(tools={actor.role.tools}) — claude pool dispatch would require a "
        "runtime workspace binding that gate/integration review asks never "
        "carry, crashing the run when the job lands on a claude member"
    )


@pytest.mark.parametrize(
    "actor", REVIEWER_ACTORS, ids=lambda actor: actor.name,
)
def test_reviewer_actor_manifest_role_is_not_write_producing(actor) -> None:
    """Worker-side enforcement check (claude_pool._validate_bound_job_manifest)
    sees the role as a JSON manifest — assert the serialized shape too."""
    manifest_role = {
        "name": actor.role.name,
        "tools": [str(tool) for tool in (actor.role.tools or [])],
        "metadata": dict(actor.role.metadata or {}),
    }
    assert not _manifest_role_is_write_producing(manifest_role)


@pytest.mark.parametrize(
    "actor", REVIEWER_ACTORS, ids=lambda actor: actor.name,
)
def test_reviewer_actor_keeps_read_tools(actor) -> None:
    """Reviewers still need Read/Glob/Grep for context-package files."""
    for tool in ("Read", "Glob", "Grep"):
        assert tool in (actor.role.tools or []), (
            f"{actor.name} lost required read tool {tool}"
        )


@pytest.mark.parametrize(
    ("variant", "base"),
    [
        (lead_pm_review_role, lead_pm_role),
        (lead_designer_review_role, lead_designer_role),
        (lead_architect_review_role, lead_architect_role),
        (planning_lead_review_role, planning_lead_role),
        (lead_task_planner_review_role, lead_task_planner_role),
    ],
    ids=lambda role: role.name,
)
def test_review_variant_preserves_name_model_metadata(variant, base) -> None:
    """Economy overrides key on Role.name; session limits live in metadata.

    The variant must not mutate the shared base generation role.
    """
    assert variant is not base
    assert variant.name == base.name
    assert variant.model == base.model
    assert dict(variant.metadata or {}) == dict(base.metadata or {})
    # Base generation roles must REMAIN write-producing (they really write
    # per-SF artifacts) — the fix must not weaken them.
    assert _role_is_write_producing(base)


@pytest.mark.parametrize(
    "actor",
    GATE_REVIEWER_ACTORS + [_sf_task_planner_gate_reviewer, _sf_task_planner_reviewer],
    ids=lambda actor: actor.name,
)
def test_economy_mode_routes_gate_reviewers_to_fable(actor, monkeypatch) -> None:
    """Operator requirement: seal-gate reviewers stay on claude-fable-5 under
    IRIAI_ECONOMY_MODE=1. ECONOMY_MODE is read at config import time, so the
    runtime module global is patched the way a flagged process would see it."""
    monkeypatch.setattr(claude_runtime, "ECONOMY_MODE", True)
    model, _effort = _resolve_model_and_effort(actor.role)
    assert model == BUDGET_TIERS["fable"] == "claude-fable-5"


def test_gate_reviewer_role_names_are_in_economy_override_map() -> None:
    """Guard the Role.name → override keying: if a variant ever gets a new
    name without a matching ECONOMY_MODEL_OVERRIDES entry, economy mode would
    silently demote the seal gate off fable."""
    for actor in GATE_REVIEWER_ACTORS + [
        _sf_task_planner_gate_reviewer,
        _sf_task_planner_reviewer,
    ]:
        assert ECONOMY_MODEL_OVERRIDES.get(actor.role.name) == BUDGET_TIERS["fable"], (
            f"{actor.name}: Role.name {actor.role.name!r} is not mapped to "
            "fable in ECONOMY_MODEL_OVERRIDES"
        )


@pytest.mark.parametrize(
    "actor", GATE_REVIEWER_ACTORS, ids=lambda actor: actor.name,
)
def test_economy_mode_off_keeps_declared_model(actor, monkeypatch) -> None:
    """Flag OFF must stay byte-identical to the declared role model."""
    monkeypatch.setattr(claude_runtime, "ECONOMY_MODE", False)
    model, _effort = _resolve_model_and_effort(actor.role)
    assert model == actor.role.model


@pytest.mark.parametrize(
    "actor", REVIEWER_ACTORS, ids=lambda actor: actor.name,
)
def test_reviewer_prompt_carries_review_only_instructions(actor) -> None:
    """The base prompts (e.g. lead_pm prompt.md) instruct Write-tool artifact
    writes; the variant must override that so a write-less reviewer reliably
    falls back to structured-output verdicts instead of dead-ending."""
    assert "Review-Only Mode" in actor.role.prompt


# ── Ask-only planning actors (defect W-4) ────────────────────────────────────
#
# Regression tests for the dag-ws slice-planner crash:
#     RuntimeError('Claude pool write-producing job requires runtime workspace binding')
#
# The slice planners (and the workstream planner) are one-shot Asks that
# return ONLY structured output (ImplementationDAG / WorkstreamDecomposition)
# and never write workspace files, yet they wrapped the full planning_lead
# generation role whose tools include Write — so claude-pool dispatch
# classified the job write-producing and demanded a workspace binding that
# ask dispatches never inject. Fix follows the B-4 precedent: an ask-only
# role variant (roles._ask_only_role) with the SAME Role.name.

ASK_ONLY_PLANNING_ACTORS = [
    _slice_planner_actor("dag-ws-ws-foundation-sf-slug-slice-2-target-only"),
    _slice_planner_actor("dag-ws-ws-foundation-sf-slug-slice-2-repair-target-only"),
    _workstream_planner,
]


@pytest.mark.parametrize(
    "actor", ASK_ONLY_PLANNING_ACTORS, ids=lambda actor: actor.name,
)
def test_ask_only_planning_actor_role_is_not_write_producing(actor) -> None:
    """The exact predicate claude_pool dispatch uses must classify the
    slice-planner/workstream-planner jobs read-only."""
    assert not _role_is_write_producing(actor.role), (
        f"{actor.name} wraps a write-producing role (tools={actor.role.tools}) "
        "— claude pool dispatch would demand a runtime workspace binding the "
        "planning asks never carry"
    )


@pytest.mark.parametrize(
    "actor", ASK_ONLY_PLANNING_ACTORS, ids=lambda actor: actor.name,
)
def test_ask_only_planning_actor_manifest_role_is_not_write_producing(actor) -> None:
    manifest_role = {
        "name": actor.role.name,
        "tools": [str(tool) for tool in (actor.role.tools or [])],
        "metadata": dict(actor.role.metadata or {}),
    }
    assert not _manifest_role_is_write_producing(manifest_role)


@pytest.mark.parametrize(
    "actor", ASK_ONLY_PLANNING_ACTORS, ids=lambda actor: actor.name,
)
def test_ask_only_planning_actor_keeps_read_tools(actor) -> None:
    """Slice planners still Read the context-package files referenced in the
    prompt — only write-producing tools may be stripped."""
    for tool in ("Read", "Glob", "Grep"):
        assert tool in (actor.role.tools or []), (
            f"{actor.name} lost required read tool {tool}"
        )


def test_planning_lead_ask_role_preserves_name_model_metadata() -> None:
    """Economy overrides key on Role.name — the ask variant must keep it,
    and must not mutate the shared write-producing base role."""
    assert planning_lead_ask_role is not planning_lead_role
    assert planning_lead_ask_role.name == planning_lead_role.name
    assert planning_lead_ask_role.model == planning_lead_role.model
    assert dict(planning_lead_ask_role.metadata or {}) == dict(planning_lead_role.metadata or {})
    # The base generation role must REMAIN write-producing.
    assert _role_is_write_producing(planning_lead_role)


@pytest.mark.parametrize(
    "actor", ASK_ONLY_PLANNING_ACTORS, ids=lambda actor: actor.name,
)
def test_ask_only_planning_actor_prompt_carries_no_write_instructions(actor) -> None:
    """The planning_lead prompt instructs Write-tool artifact writes; the ask
    variant must override that so a write-less planner falls back to
    structured output instead of dead-ending."""
    assert "Structured-Output-Only Mode" in actor.role.prompt
