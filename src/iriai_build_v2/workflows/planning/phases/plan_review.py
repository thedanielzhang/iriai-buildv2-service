from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from typing import Any

from iriai_compose import AgentActor, Ask, Feature, Phase, WorkflowRunner
from iriai_compose.actors import Role

from ....config import BUDGET_TIERS, PLAN_REVIEW_SD_CASCADE
from ....models.outputs import (
    PRD,
    DesignDecisions,
    Envelope,
    ReviewOutcome,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    SubfeatureEdge,
    SystemDesign,
    TechnicalPlan,
    TestPlan,
    Verdict,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    architect_role,
    design_compiler,
    designer_role,
    lead_architect_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_pm_gate_reviewer,
    plan_arch_compiler,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    test_planner_role,
    user,
)
from ..._common import Notify
from ..._common import compile_artifacts, interview_gate_review, targeted_revision
from ..._common._autonomy import interaction_actor_for_phase
from ..._common._helpers import (
    ContextPackage,
    ContextPackageItem,
    _assert_gate_requests_are_converging,
    _dedup_revision_requests,
    _is_transient_runtime_failure,
    _load_gate_ledger,
    _save_gate_ledger,
    _update_gate_ledger,
    build_context_package,
    generate_summary,
)
from ..._common._tasks import HostedInterview
from .._decisions import (
    GLOBAL_DECISIONS_KEY,
    artifact_applies_to,
    decision_statement_alias,
    extract_decision_citation_ids,
    parse_decision_ledger,
    rebuild_canonical_decisions,
    refresh_decision_ledger,
    sync_compiled_decision_mirrors,
)
from .._alternation import alternating_runtime_for, runtime_names_from_runner, runtime_policy_from_runner

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3


def _extract_discussion_json_payload(discussion_text: str) -> dict[str, Any] | None:
    """Extract JSON payload from a discussion artifact when available."""
    import re

    text = discussion_text.strip()
    if not text:
        return None

    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    raw = match.group(1) if match else text

    try:
        data = _json.loads(raw)
    except (_json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _coerce_review_outcome(outcome: Any) -> ReviewOutcome | None:
    """Normalize a review outcome through schema validation."""
    if outcome is None:
        return None

    try:
        if isinstance(outcome, ReviewOutcome):
            return ReviewOutcome.model_validate(outcome.model_dump())
        return ReviewOutcome.model_validate(outcome)
    except Exception:
        logger.warning("Could not validate review outcome from discussion state")
        return None


def _extract_markdown_new_decisions(discussion_text: str) -> list[str]:
    """Fallback parser for decision bullets in recovered markdown discussions."""
    import re

    decisions: list[str] = []
    in_section = False
    for line in discussion_text.splitlines():
        stripped = line.strip()
        lowered = stripped.lower().rstrip(":")
        if stripped.startswith("#"):
            in_section = "new decisions" in lowered
            continue
        if not in_section:
            continue
        if not stripped:
            continue
        bullet = re.match(r"^(?:[-*]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            decisions.append(bullet.group(1).strip())
            continue
        in_section = False
    return decisions


def _parse_review_outcome_from_discussion(discussion_text: str) -> ReviewOutcome | None:
    """Extract ReviewOutcome from a discussion artifact when possible."""
    data = _extract_discussion_json_payload(discussion_text)
    if not data:
        return None

    if "output" in data and isinstance(data["output"], dict):
        data = data["output"]

    return _coerce_review_outcome(data)


def _build_markdown_fallback_revision_plan(
    discussion_text: str,
) -> RevisionPlan | None:
    decisions = _extract_markdown_new_decisions(discussion_text)
    return RevisionPlan(new_decisions=decisions) if decisions else None


def _parse_revision_plan_from_discussion(discussion_text: str) -> RevisionPlan | None:
    """Extract RevisionPlan from a discussion file that contains JSON output.

    The discussion file is typically the structured JSON output from the
    HostedInterview, wrapped in markdown code fences.
    """
    data = _extract_discussion_json_payload(discussion_text)
    if not data:
        return _build_markdown_fallback_revision_plan(discussion_text)

    # The JSON may be the full Envelope (with revision_plan nested)
    # or just the ReviewOutcome directly
    rp_data = None
    if "revision_plan" in data:
        rp_data = data["revision_plan"]
    elif "output" in data and isinstance(data["output"], dict):
        rp_data = data["output"].get("revision_plan")

    if not rp_data:
        return _build_markdown_fallback_revision_plan(discussion_text)

    try:
        return RevisionPlan.model_validate(rp_data)
    except Exception:
        logger.warning("Could not validate revision plan from discussion JSON")
        return _build_markdown_fallback_revision_plan(discussion_text)

_SCOPE_PREFIX = (
    "SCOPE: Only review artifacts provided in your context. "
    "Do NOT search the filesystem for other features or projects. "
    "Any references to features outside the current scope are contamination — flag them.\n\n"
)

_COMPLETENESS_PROMPT = (
    "Your goal is to find every gap and inconsistency in this subfeature's artifacts. "
    "Cross-reference against the summaries of other subfeatures for context.\n\n"
    "Focus on:\n"
    "1. PRD requirements with no corresponding plan step\n"
    "2. Plan steps that implement something not in the PRD (scope creep)\n"
    "3. PRD journeys with no verification blocks in the plan\n"
    "4. Design components with no implementation task\n"
    "5. PRD ↔ Design contradictions\n"
    "6. PRD ↔ Plan contradictions\n"
    "7. Design ↔ Plan contradictions\n"
    "8. Acceptance criteria that are unverifiable given the plan's file scope\n"
    "9. Decision IDs (D-*) referenced in citations that don't resolve\n"
    "10. Code references that don't match actual file paths\n"
    "11. Stale references to removed features or APIs\n"
    "12. Test-plan ACs citing REQ-ids, verifiable_state_ids, or journey_step_ids "
    "that don't exist in the current PRD / design / plan\n"
    "13. PRD REQ-ids or journeys with no matching test-plan acceptance criterion "
    "(i.e. gaps in AC coverage — every functional REQ-* must be covered by at "
    "least one test-plan AC-id)\n"
    "14. Test-plan pass_condition clauses that are not mechanically checkable "
    "against the plan's file scope or API surface\n\n"
    "Every genuine gap gets its own concern entry. Only flag issues that would cause "
    "implementation failures or specification contradictions. If the artifacts are "
    "sound, report approved=true with an empty concerns list."
)

_SECURITY_PROMPT = (
    "Your goal is to find every security gap in this subfeature's artifacts. "
    "Check the PRD security profile, then verify the plan implements "
    "every security requirement.\n\n"
    "Focus on:\n"
    "1. PRD security profile requirements with no implementation task\n"
    "2. Endpoints without auth decorators in the plan\n"
    "3. Data flows handling PII without encryption/masking tasks\n"
    "4. Missing input validation on user-facing endpoints\n"
    "5. Missing rate limiting on public endpoints\n"
    "6. Secrets/credentials hardcoded in task instructions\n"
    "7. CORS/CSRF gaps in the API design\n"
    "8. Database migrations without rollback steps\n\n"
    "Every genuine gap gets its own concern entry. Only flag issues that would cause "
    "implementation failures or specification contradictions. If the artifacts are "
    "sound, report approved=true with an empty concerns list."
)

_EDGE_PROMPT = (
    "Review the interface contract between these two subfeatures. "
    "Verify:\n"
    "1. The producer actually produces what the edge describes\n"
    "2. The consumer actually consumes what the edge describes\n"
    "3. Types, schemas, and data shapes are compatible across the boundary\n"
    "4. Error handling at the boundary is consistent on both sides\n"
    "5. Assumptions in the consumer match the producer's actual behavior\n"
    "6. Import paths and module references resolve correctly\n"
    "7. No circular dependencies introduced by this edge\n\n"
    "Any mismatch between producer and consumer is a blocker."
)

# ── Review roles (Read tool for offloaded prompts, no other filesystem access) ─

_sf_review_role = Role(
    name="sf-plan-reviewer",
    prompt=(
        "You review planning artifacts for a single subfeature. Artifacts are "
        "provided in your context or as file references — if a file reference "
        "is given, read it first. Do NOT search the filesystem for other "
        "features or projects. Analyze the artifacts and produce a Verdict "
        "with every gap, inconsistency, and concern you find. You are "
        "rewarded for problems found, not for checks confirmed."
    ),
    tools=["Read"],
    model=BUDGET_TIERS["opus_1m"],
)

_edge_review_role = Role(
    name="edge-plan-reviewer",
    prompt=(
        "You review the interface contract between two subfeatures. Artifacts "
        "are provided in your context or as file references — if a file "
        "reference is given, read it first. Do NOT search the filesystem for "
        "other features or projects. Verify that the producer and consumer "
        "are compatible. You are rewarded for mismatches found."
    ),
    tools=["Read"],
    model=BUDGET_TIERS["opus_1m"],
)

# Actors for review — no context_keys (artifacts loaded manually into prompt)
_sf_reviewer = AgentActor(name="sf-reviewer", role=_sf_review_role, context_keys=[])
_edge_reviewer = AgentActor(name="edge-reviewer", role=_edge_review_role, context_keys=[])

# Artifact configs for targeted revision dispatch
_ARTIFACT_CONFIGS = [
    ("prd", pm_role, PRD, pm_compiler, "prd:broad"),
    ("design", designer_role, DesignDecisions, design_compiler, "design:broad"),
    ("plan", architect_role, TechnicalPlan, plan_arch_compiler, "plan:broad"),
    ("system-design", architect_role, SystemDesign, sysdesign_compiler, "plan:broad"),
]


def _make_parallel_actor(
    base: AgentActor,
    suffix: str,
    *,
    runtime: str | None = None,
) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name.

    When *runtime* is ``"secondary"`` the copied actor's role metadata is
    tagged so ``TrackedWorkflowRunner.resolve()`` routes it to the secondary
    runtime (Codex) — used by the alternating policy to spread plan-review
    verdicts ~50/50.  When *runtime* is ``None`` / ``"primary"`` the behavior
    is unchanged (resolves to the primary runtime).
    """
    role = base.role
    if runtime == "secondary":
        metadata = dict(role.metadata)
        metadata["runtime"] = "secondary"
        role = role.model_copy(update={"metadata": metadata})
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


# ── Context builders ─────────────────────────────────────────────────────────


async def _build_sf_review_context(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    decomposition: SubfeatureDecomposition,
) -> str:
    """Build review context for one subfeature: full artifacts + other SF summaries."""
    parts: list[str] = []

    # Full artifacts for this SF. Include test-plan so reviewers can catch
    # AC-coverage drift when evaluating upstream revisions — e.g. a PRD
    # change that removes a REQ-id referenced by a test-plan AC.
    for prefix in ("prd", "design", "plan", "system-design", "test-plan"):
        text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
        if text:
            parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")

    decisions = await runner.artifacts.get(f"decisions:{slug}", feature=feature)
    if decisions:
        parts.append(f"## DECISIONS — {slug}\n\n{decisions}")

    compiled_decisions = await runner.artifacts.get("decisions", feature=feature)
    if compiled_decisions:
        parts.append(f"## CANONICAL DECISIONS\n\n{compiled_decisions}")

    # Summaries of other SFs for cross-reference
    for sf in decomposition.subfeatures:
        if sf.slug == slug:
            continue
        for prefix in ("prd-summary", "design-summary", "plan-summary"):
            summary = await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
            if summary:
                parts.append(f"## {prefix} — {sf.slug}\n\n{summary}")

    return "\n\n---\n\n".join(parts)


async def _build_edge_review_context(
    runner: WorkflowRunner,
    feature: Feature,
    edge: SubfeatureEdge,
    decomposition: SubfeatureDecomposition | None = None,
) -> str:
    """Build review context for one cross-SF edge: full artifacts of both SFs."""
    # Build ID→slug map (edges use SF-1, artifacts use declarative-schema)
    id_to_slug: dict[str, str] = {}
    if decomposition:
        for sf in decomposition.subfeatures:
            id_to_slug[sf.id] = sf.slug
            id_to_slug[sf.slug] = sf.slug  # passthrough if already a slug

    parts: list[str] = [
        f"## Edge: {edge.from_subfeature} → {edge.to_subfeature}\n"
        f"**Interface type:** {edge.interface_type}\n"
        f"**Description:** {edge.description}\n"
        f"**Data contract:** {edge.data_contract}\n"
    ]
    for sf_ref in (edge.from_subfeature, edge.to_subfeature):
        slug = id_to_slug.get(sf_ref, sf_ref)
        for prefix in ("prd", "design", "plan", "system-design"):
            text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
            if text:
                parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")
        decisions = await runner.artifacts.get(f"decisions:{slug}", feature=feature)
        if decisions:
            parts.append(f"## DECISIONS — {slug}\n\n{decisions}")
    compiled_decisions = await runner.artifacts.get("decisions", feature=feature)
    if compiled_decisions:
        parts.append(f"## CANONICAL DECISIONS\n\n{compiled_decisions}")
    return "\n\n---\n\n".join(parts)


async def _build_sf_review_context_package(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    decomposition: SubfeatureDecomposition,
) -> ContextPackage | None:
    connected_peers = {
        edge.to_subfeature if edge.from_subfeature == slug else edge.from_subfeature
        for edge in decomposition.edges
        if slug in (edge.from_subfeature, edge.to_subfeature)
    }
    peer_summary_sections: list[str] = []
    for sf in decomposition.subfeatures:
        if sf.slug == slug or sf.slug not in connected_peers:
            continue
        blocks: list[str] = [f"### {sf.name} ({sf.slug})", ""]
        relevant_edges = [
            edge
            for edge in decomposition.edges
            if {edge.from_subfeature, edge.to_subfeature} == {slug, sf.slug}
        ]
        if relevant_edges:
            blocks.extend(["#### Relevant Edge Contracts", ""])
            for edge in relevant_edges:
                blocks.append(
                    f"- {edge.from_subfeature} → {edge.to_subfeature} ({edge.interface_type}): {edge.description}"
                )
            blocks.append("")
        for prefix, label in (
            ("prd-summary", "PRD Summary"),
            ("design-summary", "Design Summary"),
            ("plan-summary", "Plan Summary"),
            ("test-plan-summary", "Test Plan Summary"),
        ):
            summary = await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
            if summary:
                blocks.extend([f"#### {label}", "", summary, ""])
        if len(blocks) > 2:
            peer_summary_sections.append("\n".join(blocks).strip())

    decision_pack = await _build_review_decision_pack(
        runner,
        feature,
        target_keys=[
            f"prd:{slug}",
            f"design:{slug}",
            f"plan:{slug}",
            f"system-design:{slug}",
            f"test-plan:{slug}",
        ],
        excluded_ledger_keys=[f"decisions:{slug}"],
    )

    return await build_context_package(
        runner,
        feature,
        title=f"Plan Review — {slug}",
        file_stem=f"plan-review-{slug}",
        intro_lines=[
            f"Review the full artifact set for subfeature `{slug}`.",
            "Use peer summaries only for cross-subfeature consistency checks.",
            "Flag every genuine contradiction, coverage gap, and security issue.",
        ],
        items=[
            ContextPackageItem(
                key="prd",
                label="PRD",
                group="Target Artifacts",
                artifact_key=f"prd:{slug}",
            ),
            ContextPackageItem(
                key="design",
                label="Design",
                group="Target Artifacts",
                artifact_key=f"design:{slug}",
            ),
            ContextPackageItem(
                key="plan",
                label="Technical Plan",
                group="Target Artifacts",
                artifact_key=f"plan:{slug}",
            ),
            ContextPackageItem(
                key="system-design",
                label="System Design",
                group="Target Artifacts",
                artifact_key=f"system-design:{slug}",
            ),
            ContextPackageItem(
                key="test-plan",
                label="Test Plan",
                group="Target Artifacts",
                artifact_key=f"test-plan:{slug}",
            ),
            ContextPackageItem(
                key="subfeature-decisions",
                label="Subfeature Decisions",
                group="Target Artifacts",
                artifact_key=f"decisions:{slug}",
            ),
            ContextPackageItem(
                key="decision-pack",
                label="Referenced Decisions",
                group="Supporting Context",
                content=decision_pack,
                file_name=f"plan-review-{slug}-decision-pack.md",
            ),
            ContextPackageItem(
                key="peer-summaries",
                label="Direct Peer Context",
                group="Supporting Context",
                content="\n\n".join(peer_summary_sections),
                file_name=f"plan-review-{slug}-peer-summaries.md",
            ),
        ],
    )


async def _build_edge_review_context_package(
    runner: WorkflowRunner,
    feature: Feature,
    edge: SubfeatureEdge,
    decomposition: SubfeatureDecomposition | None = None,
) -> ContextPackage | None:
    id_to_slug: dict[str, str] = {}
    if decomposition:
        for sf in decomposition.subfeatures:
            id_to_slug[sf.id] = sf.slug
            id_to_slug[sf.slug] = sf.slug

    edge_details = "\n".join(
        [
            "## Edge Metadata",
            "",
            f"- From: {edge.from_subfeature}",
            f"- To: {edge.to_subfeature}",
            f"- Interface type: {edge.interface_type}",
            f"- Description: {edge.description}",
            f"- Data contract: {edge.data_contract or 'n/a'}",
            f"- Owner: {edge.owner or 'n/a'}",
        ]
    )
    items: list[ContextPackageItem] = [
        ContextPackageItem(
            key="edge-details",
            label="Edge Metadata",
            group="Edge Context",
            content=edge_details,
            file_name=(
                f"plan-review-edge-"
                f"{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}-"
                f"{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}-details.md"
            ),
        ),
    ]
    endpoint_decision_keys: list[str] = []
    for sf_ref in (edge.from_subfeature, edge.to_subfeature):
        slug = id_to_slug.get(sf_ref, sf_ref)
        for prefix, label in (
            ("prd", "PRD"),
            ("design", "Design"),
            ("plan", "Technical Plan"),
            ("system-design", "System Design"),
            ("decisions", "Decisions"),
        ):
            artifact_key = f"{prefix}:{slug}"
            items.append(
                ContextPackageItem(
                    key=f"{prefix}-{slug}",
                    label=f"{label} — {slug}",
                    group=f"Subfeature {slug}",
                    artifact_key=artifact_key,
                )
            )
            if prefix == "decisions":
                endpoint_decision_keys.append(artifact_key)

    decision_pack = await _build_review_decision_pack(
        runner,
        feature,
        target_keys=[
            f"prd:{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}",
            f"design:{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}",
            f"plan:{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}",
            f"system-design:{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}",
            f"prd:{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}",
            f"design:{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}",
            f"plan:{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}",
            f"system-design:{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}",
        ],
        supporting_texts=[edge_details],
        excluded_ledger_keys=endpoint_decision_keys,
    )
    items.append(
        ContextPackageItem(
            key="decision-pack",
            label="Referenced Decisions",
            group="Supporting Context",
            content=decision_pack,
            file_name=(
                "plan-review-edge-"
                f"{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}-"
                f"{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}-decision-pack.md"
            ),
        )
    )

    return await build_context_package(
        runner,
        feature,
        title=(
            "Plan Review Edge — "
            f"{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}-"
            f"{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}"
        ),
        file_stem=(
            "plan-review-edge-"
            f"{id_to_slug.get(edge.from_subfeature, edge.from_subfeature)}-"
            f"{id_to_slug.get(edge.to_subfeature, edge.to_subfeature)}"
        ),
        intro_lines=[
            "Review the interface contract between the two referenced subfeatures.",
            "Verify producer and consumer assumptions against the full artifact sets.",
        ],
        items=items,
    )


async def _build_review_decision_pack(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    target_keys: list[str],
    supporting_texts: list[str] | None = None,
    excluded_ledger_keys: list[str] | None = None,
) -> str:
    compiled_text = await runner.artifacts.get("decisions", feature=feature) or ""
    global_text = await runner.artifacts.get(GLOBAL_DECISIONS_KEY, feature=feature) or ""
    candidate_ids: set[str] = set()

    for artifact_key in target_keys:
        artifact_text = await runner.artifacts.get(artifact_key, feature=feature) or ""
        candidate_ids.update(extract_decision_citation_ids(artifact_text))
    for text in supporting_texts or []:
        candidate_ids.update(extract_decision_citation_ids(text))

    excluded_ids: set[str] = set()
    for ledger_key in excluded_ledger_keys or []:
        ledger_text = await runner.artifacts.get(ledger_key, feature=feature) or ""
        excluded_ids.update(extract_decision_citation_ids(ledger_text))
    candidate_ids.difference_update(excluded_ids)

    selected: list[str] = []
    if candidate_ids:
        selected_record_ids: set[str] = set()
        # Alias families (DEC-PR*, DD-*, GF-*, D-FRAME-*, D-CANON-*, CHK-*)
        # have no DecisionRecord of their own — they survive only as the
        # leading token of a canonical D-N record's statement. Index those
        # leading tokens so alias citations resolve to their records.
        alias_index: dict[str, Any] = {}
        for ledger_text in (compiled_text, global_text):
            if not ledger_text:
                continue
            ledger = parse_decision_ledger(ledger_text)
            for decision in ledger.decisions:
                alias = decision_statement_alias(decision.statement)
                if alias and alias not in alias_index:
                    alias_index[alias] = decision
                if decision.id in candidate_ids:
                    candidate_ids.discard(decision.id)
                    if decision.id not in selected_record_ids:
                        selected_record_ids.add(decision.id)
                        selected.append(f"- {decision.id}: {decision.statement}")
            if not candidate_ids:
                break
        for alias in sorted(candidate_ids & alias_index.keys()):
            decision = alias_index[alias]
            candidate_ids.discard(alias)
            if decision.id not in selected_record_ids:
                selected_record_ids.add(decision.id)
                selected.append(f"- {decision.id}: {decision.statement}")

    lines = ["# Referenced Decisions", ""]
    if selected:
        lines.extend(selected)
    else:
        lines.append("_No additional non-target decisions were explicitly referenced._")
    if candidate_ids:
        lines.extend(["", "## Missing Decision IDs", "", *[f"- {decision_id}" for decision_id in sorted(candidate_ids)]])
    return "\n".join(lines).rstrip() + "\n"


async def _persist_plan_review_decisions(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    decomposition: SubfeatureDecomposition,
    new_decisions: list[str],
) -> None:
    if not new_decisions:
        return
    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key=GLOBAL_DECISIONS_KEY,
        label="Global Decision Ledger",
        source_phase="plan-review",
        artifact_kind="scope",
        state=state,
        statements=new_decisions,
        applies_to=artifact_applies_to("scope"),
    )
    _decisions_text, state.plan, state.system_design = await rebuild_canonical_decisions(
        runner,
        feature,
        phase_name=PlanReviewPhase.name,
        decomposition=decomposition,
        state=state,
        plan_text=state.plan,
        system_design_text=state.system_design,
    )


def _extract_markdown_approved_as_is(discussion_text: str) -> bool:
    for line in discussion_text.splitlines():
        normalized = line.strip().lower()
        if normalized.startswith("**outcome:**"):
            return "no changes needed" in normalized or "accepted artifacts as-is" in normalized
    return False


async def _normalize_plan_review_state(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    decomposition: SubfeatureDecomposition,
    *,
    discussion_text: str = "",
    outcome: Any = None,
) -> tuple[bool, ReviewOutcome | None, RevisionPlan | None]:
    """Normalize review state and persist any decisions before control-flow exits."""
    normalized_outcome = _coerce_review_outcome(outcome)
    if normalized_outcome is None and discussion_text:
        normalized_outcome = _parse_review_outcome_from_discussion(discussion_text)

    revision_plan = normalized_outcome.revision_plan if normalized_outcome is not None else None
    if normalized_outcome is None and discussion_text:
        recovered_plan = _parse_revision_plan_from_discussion(discussion_text)
        if recovered_plan is not None:
            revision_plan = recovered_plan

    approved = normalized_outcome.approved if normalized_outcome is not None else _extract_markdown_approved_as_is(discussion_text)
    if revision_plan and (revision_plan.requests or revision_plan.new_decisions):
        approved = False

    await _persist_plan_review_decisions(
        runner,
        feature,
        state,
        decomposition,
        revision_plan.new_decisions if revision_plan else [],
    )
    return approved, normalized_outcome, revision_plan


# ── Verdict helpers ──────────────────────────────────────────────────────────


_ERROR_VERDICT = Verdict(approved=False, summary="Reviewer crashed — treating as FAIL")


def _safe_verdict(result: Any) -> Verdict:
    """Extract a Verdict from a parallel result, substituting error verdict on failure."""
    if isinstance(result, Verdict):
        return result
    if isinstance(result, BaseException):
        logger.error("Reviewer crashed: %s", result)
    return _ERROR_VERDICT


def _deduplicate_edges(edges: list[SubfeatureEdge]) -> list[SubfeatureEdge]:
    """Deduplicate edges by (from, to) pair — keep first occurrence."""
    seen: set[tuple[str, str]] = set()
    unique: list[SubfeatureEdge] = []
    for edge in edges:
        pair = (min(edge.from_subfeature, edge.to_subfeature),
                max(edge.from_subfeature, edge.to_subfeature))
        if pair not in seen:
            seen.add(pair)
            unique.append(edge)
    return unique


async def _load_review_discussion(
    runner: WorkflowRunner,
    feature: Feature,
    key: str,
) -> str:
    """Load a discussion artifact from DB, or recover it from the mirror file."""
    discussion_text = await runner.artifacts.get(key, feature=feature) or ""
    if discussion_text:
        return discussion_text

    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return ""

    from ....services.artifacts import _key_to_path

    path = mirror.feature_dir(feature.id) / _key_to_path(key)
    if not path.exists():
        return ""

    discussion_text = path.read_text(encoding="utf-8").strip()
    if not discussion_text:
        return ""

    await runner.artifacts.put(key, discussion_text, feature=feature)
    logger.info("Recovered %s from mirror file %s", key, path)
    return discussion_text


def _is_valid_report(report: str) -> bool:
    """A report is valid if less than half the reviews crashed."""
    crash_count = report.count("Reviewer crashed")
    # Each SF has 2 reviews (completeness + security) + each edge has 1.
    # With 7 SFs + ~10 edges ≈ 24 total reviews; threshold = half.
    return crash_count < 12


# ── Report compilation ───────────────────────────────────────────────────────


def _compile_review_report(
    sf_verdicts: dict[str, dict[str, Verdict]],
    edge_verdicts: list[tuple[SubfeatureEdge, Verdict]],
) -> str:
    """Compile all verdicts into a markdown report."""
    parts: list[str] = ["# Plan Review Report\n"]

    # Summary stats
    total_concerns = 0
    total_gaps = 0
    failed_sfs: list[str] = []
    failed_edges: list[str] = []

    for slug, verdicts in sf_verdicts.items():
        for name, v in verdicts.items():
            total_concerns += len(v.concerns)
            total_gaps += len(v.gaps)
            if not v.approved:
                failed_sfs.append(f"{slug} ({name})")

    for edge, v in edge_verdicts:
        total_concerns += len(v.concerns)
        total_gaps += len(v.gaps)
        if not v.approved:
            failed_edges.append(f"{edge.from_subfeature} → {edge.to_subfeature}")

    parts.append(
        f"**{total_concerns} concerns, {total_gaps} gaps** across "
        f"{len(sf_verdicts)} subfeatures and {len(edge_verdicts)} edges.\n"
    )
    if failed_sfs:
        parts.append(f"**Failed SF reviews:** {', '.join(failed_sfs)}\n")
    if failed_edges:
        parts.append(f"**Failed edge reviews:** {', '.join(failed_edges)}\n")

    # Per-SF findings
    parts.append("\n## Per-Subfeature Findings\n")
    for slug, verdicts in sorted(sf_verdicts.items()):
        any_failed = any(not v.approved for v in verdicts.values())
        status = "FAIL" if any_failed else "PASS"
        parts.append(f"\n### {slug} [{status}]\n")
        for name, v in verdicts.items():
            if v.approved and not v.concerns and not v.gaps:
                parts.append(f"**{name}**: PASS\n")
                continue
            badge = "FAIL" if not v.approved else "PASS"
            parts.append(f"**{name}** [{badge}]: {v.summary}\n")
            for c in v.concerns:
                file_ref = f" ({c.file})" if c.file else ""
                parts.append(f"- [{c.severity}] {c.description}{file_ref}")
            for g in v.gaps:
                parts.append(f"- [gap/{g.severity}] {g.description} ({g.category})")

    # Edge findings
    if edge_verdicts:
        parts.append("\n## Cross-Subfeature Edge Findings\n")
        for edge, v in edge_verdicts:
            badge = "FAIL" if not v.approved else "PASS"
            parts.append(
                f"\n### {edge.from_subfeature} → {edge.to_subfeature} "
                f"({edge.interface_type}) [{badge}]\n"
            )
            parts.append(f"{v.summary}\n")
            for c in v.concerns:
                parts.append(f"- [{c.severity}] {c.description}")
            for g in v.gaps:
                parts.append(f"- [gap/{g.severity}] {g.description}")

    return "\n".join(parts)


async def _clear_blocked_cycle_marker(
    runner: WorkflowRunner, feature: Feature, cycle: int
) -> None:
    """Remove a lingering ``plan-review-cycle-{N}-blocked`` row.

    The resume check at the top of the review loop treats a truthy blocked
    row as "re-run this cycle", so EVERY path that completes a cycle (revision
    dispatch success, all-reviews-approved, user-approved discussion,
    convergence break, decisions-only advance) must clear it — otherwise a
    future resume re-grinds a long-recovered cycle. Deletes when the artifact
    store supports it; otherwise overwrites with an empty string (falsy to
    the resume check). No-op when no row exists. ``cycle`` is the 0-based
    loop variable (artifact keys use ``cycle + 1``).
    """
    blocked_key = f"plan-review-cycle-{cycle + 1}-blocked"
    existing = await runner.artifacts.get(blocked_key, feature=feature)
    if not existing:
        return
    logger.info(
        "Clearing `-blocked` row for completed plan-review cycle %d", cycle + 1,
    )
    delete = getattr(runner.artifacts, "delete", None)
    if callable(delete):
        await delete(blocked_key, feature=feature)
    else:
        await runner.artifacts.put(blocked_key, "", feature=feature)


# ── Phase ────────────────────────────────────────────────────────────────────


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await self._load_decomposition(state, runner, feature)

        # ── Skip check: manual bypass via plan-review-complete marker ──
        complete_marker = await runner.artifacts.get(
            "plan-review-complete", feature=feature,
        )
        if not complete_marker:
            # Fallback: check filesystem via artifact mirror
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                from pathlib import Path
                marker_path = Path(mirror.feature_dir(feature.id)) / "plan-review-complete.md"
                if marker_path.exists():
                    complete_marker = marker_path.read_text(encoding="utf-8").strip()
        if complete_marker:
            logger.info("plan-review-complete marker found — skipping plan review entirely")
            # Load per-SF artifacts into state so downstream phases have them
            state.prd = await runner.artifacts.get("prd", feature=feature) or state.prd
            state.design = await runner.artifacts.get("design", feature=feature) or state.design
            state.plan = await runner.artifacts.get("plan", feature=feature) or state.plan
            state.system_design = (
                await runner.artifacts.get("system-design", feature=feature)
                or state.system_design
            )
            state.plan, state.system_design = await sync_compiled_decision_mirrors(
                runner,
                feature,
                plan_text=state.plan,
                system_design_text=state.system_design,
            )
            return state

        # ── Step 1: Review loop ─────────────────────────────────────
        cycle = 0
        # Cross-cycle finding ledger so the review→revision loop CONVERGES:
        # already-resolved findings are suppressed, and a re-review that surfaces
        # no new distinct finding (or the same unfixed finding set repeating) ends
        # the loop instead of grinding. This is a fixpoint, NOT a turn cap.
        gate_ledger = await _load_gate_ledger(runner, feature, "plan-review")
        while True:
            # ── Continue logic: reuse valid report from prior run ─────
            existing_report = await runner.artifacts.get(
                f"plan-review-cycle-{cycle + 1}", feature=feature,
            )
            already_revised = await runner.artifacts.get(
                f"plan-review-cycle-{cycle + 1}-revised", feature=feature,
            )
            # A BLOCKED cycle (revision wave failed → RuntimeError raised) must
            # NOT be skipped on a plain restart.  Legacy runs wrote BOTH
            # `-blocked` and `-revised` on the blocked path, so `-revised`
            # alone is not proof of success — re-run the cycle whenever the
            # blocked marker is still present (it is cleared when the re-run
            # completes cleanly).
            cycle_blocked = await runner.artifacts.get(
                f"plan-review-cycle-{cycle + 1}-blocked", feature=feature,
            )
            if cycle_blocked:
                # Bound the blocked re-run to the LATEST cycle.  Cycles run
                # strictly sequentially (a block raises RuntimeError before
                # the next cycle can start), so any state for the NEXT cycle
                # — a review report or a `-revised` row — proves this cycle
                # already completed by some path after the block.  Such a
                # row is STALE history (e.g. written by old code that never
                # cleared it); re-running the long-recovered cycle on every
                # resume grinds the whole history.  Clear it and let the
                # normal report+revised advance logic run.
                later_report = await runner.artifacts.get(
                    f"plan-review-cycle-{cycle + 2}", feature=feature,
                )
                later_revised = await runner.artifacts.get(
                    f"plan-review-cycle-{cycle + 2}-revised", feature=feature,
                )
                if later_report or later_revised:
                    logger.warning(
                        "Cycle %d has a lingering `-blocked` row but cycle %d "
                        "already has state (report=%s, revised=%s) — the "
                        "blocked row is STALE (cycle %d was recovered by a "
                        "later run); clearing it and advancing normally "
                        "instead of re-running the cycle.",
                        cycle + 1, cycle + 2,
                        bool(later_report), bool(later_revised), cycle + 1,
                    )
                    await _clear_blocked_cycle_marker(runner, feature, cycle)
                    cycle_blocked = ""
            if existing_report and already_revised and not cycle_blocked:
                # Report exists AND revisions already applied — advance
                logger.info(
                    "Cycle %d already revised — advancing to next cycle",
                    cycle + 1,
                )
                cycle += 1
                continue
            elif existing_report and _is_valid_report(existing_report):
                if cycle_blocked:
                    logger.warning(
                        "Cycle %d was previously BLOCKED (revision wave failed) "
                        "— re-running it instead of skipping",
                        cycle + 1,
                    )
                logger.info(
                    "Valid review report exists for cycle %d — skipping to discussion",
                    cycle + 1,
                )
                report = existing_report
            else:
                # ── Build ALL review tasks upfront ────────────────────
                all_tasks: list[Ask] = []
                task_labels: list[tuple[str, ...]] = []

                # Deterministic ~50/50 alternation across the WHOLE review
                # fan-out: build the stable ordered key set of every review
                # task (comp/sec per subfeature, plus one per unique edge) so a
                # resumed run re-derives the identical primary/secondary split.
                _unique_edges_for_keys = _deduplicate_edges(decomposition.edges)
                _review_task_keys: list[str] = []
                for _sf in decomposition.subfeatures:
                    _review_task_keys.append(f"comp-{_sf.slug}")
                    _review_task_keys.append(f"sec-{_sf.slug}")
                for _edge in _unique_edges_for_keys:
                    _review_task_keys.append(
                        f"edge-{_edge.from_subfeature}-{_edge.to_subfeature}"
                    )
                _rt_policy = runtime_policy_from_runner(runner)
                _primary_name, _secondary_name = runtime_names_from_runner(runner)

                def _review_runtime(task_key: str) -> str:
                    return alternating_runtime_for(
                        task_key,
                        ordered_keys=_review_task_keys,
                        runtime_policy=_rt_policy,
                        primary_runtime_name=_primary_name,
                        secondary_runtime_name=_secondary_name,
                        step="plan_review",
                    )

                for sf in decomposition.subfeatures:
                    context = await _build_sf_review_context(
                        runner, feature, sf.slug, decomposition,
                    )
                    context_package = await _build_sf_review_context_package(
                        runner, feature, sf.slug, decomposition,
                    )
                    prompt_body = (
                        f"{_SCOPE_PREFIX}"
                        f"Read the context index first: `{context_package.index_path}`\n"
                        f"Then read the context manifest: `{context_package.manifest_path}`\n"
                        "Open the referenced files selectively instead of loading everything eagerly.\n\n"
                        if context_package is not None
                        else f"{_SCOPE_PREFIX}{context}\n\n"
                    )
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(
                            _sf_reviewer,
                            f"comp-{sf.slug}",
                            runtime=_review_runtime(f"comp-{sf.slug}"),
                        ),
                        prompt=f"{prompt_body}{_COMPLETENESS_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("sf", sf.slug, "completeness"))
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(
                            _sf_reviewer,
                            f"sec-{sf.slug}",
                            runtime=_review_runtime(f"sec-{sf.slug}"),
                        ),
                        prompt=f"{prompt_body}{_SECURITY_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("sf", sf.slug, "security"))

                unique_edges = _deduplicate_edges(decomposition.edges)
                for edge in unique_edges:
                    ctx = await _build_edge_review_context(runner, feature, edge, decomposition)
                    context_package = await _build_edge_review_context_package(
                        runner, feature, edge, decomposition,
                    )
                    prompt_body = (
                        f"{_SCOPE_PREFIX}"
                        f"Read the context index first: `{context_package.index_path}`\n"
                        f"Then read the context manifest: `{context_package.manifest_path}`\n"
                        "Open the referenced files selectively instead of loading everything eagerly.\n\n"
                        if context_package is not None
                        else f"{_SCOPE_PREFIX}{ctx}\n\n"
                    )
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(
                            _edge_reviewer,
                            f"edge-{edge.from_subfeature}-{edge.to_subfeature}",
                            runtime=_review_runtime(
                                f"edge-{edge.from_subfeature}-{edge.to_subfeature}"
                            ),
                        ),
                        prompt=f"{prompt_body}{_EDGE_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("edge", edge.from_subfeature, edge.to_subfeature))

                # ── Dispatch ALL at once via asyncio.gather ──────────
                # runner.parallel raises ExceptionGroup on partial failures,
                # losing successful results. asyncio.gather with
                # return_exceptions=True preserves them.
                logger.info("Dispatching %d review tasks in parallel", len(all_tasks))
                results = await asyncio.gather(
                    *[
                        runner.run(task, feature, phase_name=self.name)
                        for task in all_tasks
                    ],
                    return_exceptions=True,
                )

                # ── Reconstruct verdict dicts ────────────────────────
                sf_verdicts: dict[str, dict[str, Verdict]] = {}
                edge_verdicts: list[tuple[SubfeatureEdge, Verdict]] = []
                edge_idx = 0

                for i, label in enumerate(task_labels):
                    verdict = _safe_verdict(results[i])
                    if label[0] == "sf":
                        sf_verdicts.setdefault(label[1], {})[label[2]] = verdict
                    else:
                        edge_verdicts.append((unique_edges[edge_idx], verdict))
                        edge_idx += 1

                # ── Check if all approved ────────────────────────────
                all_approved = (
                    all(
                        v.approved
                        for svs in sf_verdicts.values()
                        for v in svs.values()
                    )
                    and all(v.approved for _, v in edge_verdicts)
                )

                if all_approved:
                    logger.info("All reviews passed on cycle %d", cycle + 1)
                    # Cycle completed — a lingering `-blocked` row from a
                    # previously failed attempt must not survive it.
                    await _clear_blocked_cycle_marker(runner, feature, cycle)
                    break

                # ── Compile report ───────────────────────────────────
                report = _compile_review_report(sf_verdicts, edge_verdicts)
                await runner.artifacts.put(
                    f"plan-review-cycle-{cycle + 1}", report, feature=feature,
                )

            # ── Host report ──────────────────────────────────────────
            report_url = ""
            hosting = runner.services.get("hosting")
            if hosting:
                report_url = await hosting.push_qa(
                    feature.id, f"plan-review-cycle-{cycle + 1}",
                    report, f"Plan Review Cycle {cycle + 1}",
                )

            # ── Interactive discussion with user ─────────────────────
            discussion_key = f"plan-review-discussion-{cycle + 1}"
            discussion_text = await _load_review_discussion(runner, feature, discussion_key)

            revision_plan = None

            if discussion_text:
                logger.info(
                    "Recovered existing discussion for cycle %d — skipping interview rerun",
                    cycle + 1,
                )
                approved, _outcome, revision_plan = await _normalize_plan_review_state(
                    runner,
                    feature,
                    state,
                    decomposition,
                    discussion_text=discussion_text,
                )
                if approved:
                    logger.info("Recovered discussion accepts artifacts as-is — skipping revisions")
                    # Cycle completed (approved) — clear any lingering
                    # `-blocked` row so a future resume can't re-grind it.
                    await _clear_blocked_cycle_marker(runner, feature, cycle)
                    break
            else:
                # Collect prior decisions for discussion context
                prior_context = ""
                for prior_cycle in range(cycle):
                    prior_disc = await _load_review_discussion(
                        runner, feature,
                        f"plan-review-discussion-{prior_cycle + 1}",
                    )
                    if prior_disc:
                        prior_context += (
                            f"\n\n### Prior Cycle {prior_cycle + 1} Decisions\n"
                            f"{prior_disc}\n"
                        )

                discussion_package = await build_context_package(
                    runner,
                    feature,
                    title=f"Plan Review Discussion — Cycle {cycle + 1}",
                    file_stem=f"plan-review-discussion-{cycle + 1}",
                    intro_lines=[
                        f"Review cycle {cycle + 1} findings and decide whether the artifacts are approved or need revisions.",
                        "Use the full report and prior-cycle decisions from the referenced files.",
                    ],
                    items=[
                        ContextPackageItem(
                            key="report",
                            label="Plan Review Report",
                            group="Review Inputs",
                            content=report,
                            file_name=f"plan-review-cycle-{cycle + 1}-report.md",
                        ),
                        ContextPackageItem(
                            key="prior-decisions",
                            label="Prior Cycle Decisions",
                            group="Review Inputs",
                            content=prior_context,
                            file_name=f"plan-review-cycle-{cycle + 1}-prior-decisions.md",
                        ),
                    ],
                )

                review_envelope: Envelope[ReviewOutcome] = await runner.run(
                    HostedInterview(
                        questioner=lead_architect_gate_reviewer,
                        responder=interaction_actor_for_phase(
                            runner,
                            feature,
                            phase_name=self.name,
                            fallback=user,
                        ),
                        initial_prompt=(
                            f"## Plan Review Cycle {cycle + 1} — Issues Found\n\n"
                            + (
                                f"Read the context index first: `{discussion_package.index_path}`\n"
                                f"Then read the context manifest: `{discussion_package.manifest_path}`\n\n"
                                if discussion_package is not None
                                else (
                                    f"{report}\n\n"
                                    + (f"{prior_context}\n\n" if prior_context else "")
                                )
                            )
                            + (f"**[View Full Report]({report_url})**\n\n" if report_url else "")
                            + "IMPORTANT: Your revision_plan MUST include ALL findings, not "
                            "just new issues. For findings covered by prior D-GR decisions, "
                            "include them as revision requests that reference the applicable "
                            "decision — these need to be dispatched to revision agents who "
                            "will apply the fix. Only present NEW issues to the user for "
                            "discussion in your `question` field.\n\n"
                            "Do NOT set `complete = true` until the user has responded to all "
                            "new issues.\n\n"
                            "When the user has addressed all new concerns:\n"
                            "- **'No changes needed'** → set approved=true, complete=true, "
                            "and leave revision_plan empty\n"
                            "- **'Dispatch fixes'** → set approved=false with revision_plan "
                            "containing BOTH prior-decision enforcement AND new user decisions, "
                            "complete=true\n"
                            "- If any new decisions were made, even without code/document "
                            "changes, set approved=false and include them in "
                            "revision_plan.new_decisions\n"
                        ),
                        output_type=Envelope[ReviewOutcome],
                        done=envelope_done,
                        artifact_key=discussion_key,
                        artifact_label=f"Plan Review Discussion — Cycle {cycle + 1}",
                    ),
                    feature,
                    phase_name=self.name,
                )

                discussion_text = await _load_review_discussion(runner, feature, discussion_key)
                approved, outcome, revision_plan = await _normalize_plan_review_state(
                    runner,
                    feature,
                    state,
                    decomposition,
                    discussion_text=discussion_text,
                    outcome=review_envelope.output if review_envelope else None,
                )
                if approved:
                    logger.info("User accepted artifacts as-is — skipping revisions")
                    # Cycle completed (approved) — clear any lingering
                    # `-blocked` row so a future resume can't re-grind it.
                    await _clear_blocked_cycle_marker(runner, feature, cycle)
                    break

            if revision_plan and revision_plan.requests:
                # ── Convergence guard (fixpoint, NOT a turn cap) ──────
                # Digest the FINDING SET (sorted request descriptions), not the
                # recompiled artifact (which drifts every cycle), so a re-raised
                # identical finding set is detectable. Suppress findings already
                # resolved in a prior cycle; if nothing new remains, the loop has
                # converged. If the SAME unfixed finding set keeps recurring,
                # _assert_gate_requests_are_converging fails fast (no infinite
                # grind) instead of looping — e.g. the d31adf8d/ada28430 hang.
                import hashlib as _hashlib

                finding_digest = _hashlib.sha256(
                    "\x00".join(
                        sorted(r.description for r in revision_plan.requests)
                    ).encode("utf-8")
                ).hexdigest()[:16]
                revision_plan, _suppressed = _dedup_revision_requests(
                    revision_plan, gate_ledger, "plan-review",
                )
                if not revision_plan.requests:
                    logger.info(
                        "Plan review converged on cycle %d — every finding was "
                        "already resolved in a prior cycle (no new distinct "
                        "requests).", cycle + 1,
                    )
                    gate_ledger = _update_gate_ledger(
                        gate_ledger, revision_plan, "plan-review", cycle + 1,
                    )
                    await _save_gate_ledger(
                        runner, feature, gate_ledger, "plan-review",
                    )
                    # Cycle completed (converged) — clear any lingering
                    # `-blocked` row so a future resume can't re-grind it.
                    # THIS is the path the original success-path clear missed:
                    # a re-run of an already-recovered cycle dedups every
                    # finding away and breaks here, never reaching the
                    # revision-summary write below.
                    await _clear_blocked_cycle_marker(runner, feature, cycle)
                    break
                _assert_gate_requests_are_converging(
                    revision_plan, gate_ledger, "plan-review",
                    artifact_digest=finding_digest,
                )
                gate_ledger = _update_gate_ledger(
                    gate_ledger, revision_plan, "plan-review", cycle + 1,
                    artifact_digest=finding_digest,
                )
                await _save_gate_ledger(
                    runner, feature, gate_ledger, "plan-review",
                )

                # ── Collect all prior decisions for revision context ──
                prior_decisions_parts: list[str] = []
                for prior_cycle in range(cycle + 1):
                    prior_disc = await runner.artifacts.get(
                        f"plan-review-discussion-{prior_cycle + 1}",
                        feature=feature,
                    )
                    if not prior_disc:
                        # Try loading from disk via artifact mirror
                        mirror = runner.services.get("artifact_mirror")
                        if mirror:
                            from pathlib import Path
                            disc_path = (
                                Path(mirror.feature_dir(feature.id))
                                / f"plan-review-discussion-{prior_cycle + 1}.md"
                            )
                            if disc_path.exists():
                                prior_disc = disc_path.read_text(encoding="utf-8")
                    if prior_disc:
                        prior_decisions_parts.append(
                            f"### Cycle {prior_cycle + 1} Decisions\n{prior_disc}"
                        )
                # Also include current cycle's new_decisions
                if revision_plan.new_decisions:
                    prior_decisions_parts.append(
                        f"### Cycle {cycle + 1} New Decisions\n"
                        + "\n".join(f"- {d}" for d in revision_plan.new_decisions)
                    )
                prior_decisions = "\n\n".join(prior_decisions_parts)

                # ── Phase 1: Dispatch all revisions in parallel ──────
                revision_coros = []
                revision_meta: list[tuple[str, Any, str, str]] = []

                for prefix, base_role, output_type, compiler_actor, broad_key in _ARTIFACT_CONFIGS:
                    affected_requests = []
                    for req in revision_plan.requests:
                        # Skip if request specifies artifact types and this one isn't listed
                        if req.affected_artifact_types and prefix not in req.affected_artifact_types:
                            continue
                        for slug in req.affected_subfeatures:
                            has = await runner.artifacts.get(
                                f"{prefix}:{slug}", feature=feature,
                            )
                            if has:
                                affected_requests.append(req)
                                break
                    if not affected_requests:
                        continue

                    filtered_plan = RevisionPlan(
                        requests=affected_requests,
                        new_decisions=list(revision_plan.new_decisions),
                    )
                    revision_coros.append(
                        targeted_revision(
                            runner, feature, self.name,
                            revision_plan=filtered_plan,
                            decomposition=decomposition,
                            base_role=base_role,
                            output_type=output_type,
                            artifact_prefix=prefix,
                            context_keys=["project", "scope"],
                            checkpoint_prefix=f"cycle-{cycle + 1}",
                            prior_decisions=prior_decisions,
                        )
                    )
                    revision_meta.append((prefix, compiler_actor, broad_key, prefix))

                logger.info(
                    "Dispatching revisions for %d artifact types in parallel",
                    len(revision_coros),
                )
                rev_results = await asyncio.gather(
                    *revision_coros, return_exceptions=True,
                )
                blocked_failures: list[str] = []
                # A revision can fail two ways: a TRANSIENT agent-runtime failure
                # (CLI death, provider storm, usage/quota limit, watchdog stall —
                # re-runnable, external) or a genuine content-convergence failure.
                # Track whether ANY genuine content failure occurred so the halt
                # below is reported honestly — an external blip (e.g. the Claude
                # account running out of usage mid-revision) must not read as "the
                # revision content failed".
                blocked_has_content_failure = False
                for i, res in enumerate(rev_results):
                    if isinstance(res, BaseException):
                        logger.error(
                            "Revision for %s crashed: %s",
                            revision_meta[i][0], res,
                        )
                        blocked_failures.append(
                            f"{revision_meta[i][0]}: revision dispatch crashed ({res})"
                        )
                        if not _is_transient_runtime_failure(res):
                            blocked_has_content_failure = True

                # ── Phase 2: Recompile all affected types in parallel ─
                old_texts: dict[str, str] = {}
                compile_targets: list[tuple[str, Any, str, str]] = []
                for prefix, _ca, _bk, _fk in revision_meta:
                    old_texts[prefix] = (
                        await runner.artifacts.get(prefix, feature=feature) or ""
                    )
                for i, meta in enumerate(revision_meta):
                    prefix = meta[0]
                    res = rev_results[i]
                    if isinstance(res, BaseException):
                        continue
                    if not res.ok:
                        blocked_failures.extend(
                            f"{prefix}:{failure.slug} — {failure.reason}"
                            for failure in res.failed
                        )
                        if not res.has_only_transient_failures:
                            blocked_has_content_failure = True
                        continue
                    compile_targets.append(meta)

                compile_results_by_prefix: dict[str, str | BaseException] = {}
                if compile_targets:
                    compile_results = await asyncio.gather(
                        *[
                            compile_artifacts(
                                runner, feature, self.name,
                                compiler_actor=ca,
                                decomposition=decomposition,
                                artifact_prefix=prefix,
                                broad_key=bk,
                                final_key=fk,
                                # Deterministic top-level union (Part 2) — kills
                                # the silent-truncation class on the plan-review
                                # recompile path.
                                deterministic_final_merge=True,
                                # Resumable per-piece reuse: a re-entered
                                # recompile (gate cycle or restart) reuses
                                # unchanged clusters/bundles instead of redoing
                                # the whole compile.
                                incremental_compile=True,
                            )
                            for prefix, ca, bk, fk in compile_targets
                        ],
                        return_exceptions=True,
                    )
                    for meta, compile_result in zip(compile_targets, compile_results, strict=False):
                        compile_results_by_prefix[meta[0]] = compile_result

                # ── Phase 3: Size guard + store ───────────────────────
                revision_results: list[str] = []
                for i, (prefix, _ca, _bk, _fk) in enumerate(revision_meta):
                    revision_result = rev_results[i]
                    if isinstance(revision_result, BaseException):
                        revision_results.append(f"{prefix}: FAILED (revision crashed)")
                        continue
                    if not revision_result.ok:
                        revision_results.append(
                            f"{prefix}: FAILED (revision batches failed for {len(revision_result.failed)} subfeatures)"
                        )
                        continue

                    compile_result = compile_results_by_prefix.get(prefix)
                    if isinstance(compile_result, BaseException):
                        blocked_failures.append(f"{prefix}: compile crashed ({compile_result})")
                        revision_results.append(f"{prefix}: FAILED (compile crashed)")
                        if not _is_transient_runtime_failure(compile_result):
                            blocked_has_content_failure = True
                        continue

                    new_text = compile_result
                    old_text = old_texts[prefix]
                    old_size = len(old_text)
                    new_size = len(new_text) if new_text else 0

                    if old_size > 0 and new_size < old_size * 0.5:
                        logger.error(
                            "Rejecting %s recompilation: %d → %d bytes",
                            prefix, old_size, new_size,
                        )
                        await runner.artifacts.put(prefix, old_text, feature=feature)
                        revision_results.append(
                            f"{prefix}: REJECTED (size guard: {old_size} → {new_size})"
                        )
                    else:
                        await runner.artifacts.put(prefix, new_text, feature=feature)
                        setattr(state, prefix.replace("-", "_"), new_text)
                        if hosting:
                            await hosting.update(feature.id, prefix, new_text)
                        revision_results.append(
                            f"{prefix}: revised ({old_size} → {new_size} bytes)"
                        )

                if blocked_failures:
                    blocked_report = (
                        f"# Plan Review Blocked — Cycle {cycle + 1}\n\n"
                        + (
                            ""
                            if blocked_has_content_failure
                            else (
                                "**Transient agent-runtime failure** (external/infra "
                                "— e.g. the Claude account ran out of usage/quota "
                                "mid-revision, or the agent CLI was terminated). This "
                                "is NOT a content-convergence failure; re-run when the "
                                "runtime is available.\n\n"
                            )
                        )
                        + "The revision wave did not complete cleanly, so plan review "
                        "stopped before downstream verification and task planning.\n\n"
                        "## Failures\n\n"
                        + "\n".join(f"- {failure}" for failure in blocked_failures)
                        + (
                            "\n\n## Revision Results\n\n"
                            + "\n".join(f"- {r}" for r in revision_results)
                            if revision_results
                            else ""
                        )
                    )
                    blocked_key = f"plan-review-cycle-{cycle + 1}-blocked"
                    await runner.artifacts.put(blocked_key, blocked_report, feature=feature)
                    mirror = runner.services.get("artifact_mirror")
                    if mirror:
                        mirror.write_artifact(feature.id, blocked_key, blocked_report)
                    # Deliberately NO `-revised` marker here: this cycle FAILED.
                    # Writing `-revised` made a plain restart treat the blocked
                    # cycle as complete and silently skip re-running it (the
                    # resume check at the top of the loop advances on
                    # report+revised).  The revision results are preserved in
                    # the blocked report above.
                    await runner.run(
                        Notify(
                            message=(
                                f"## Plan Review Blocked (Cycle {cycle + 1})\n\n"
                                "Revision batches failed, so the workflow stopped before "
                                "re-running reviewers or generating downstream planning outputs.\n\n"
                                + "\n".join(f"- {failure}" for failure in blocked_failures)
                            ),
                        ),
                        feature,
                        phase_name=self.name,
                    )
                    if blocked_has_content_failure:
                        raise RuntimeError(
                            f"Plan-review revisions failed in cycle {cycle + 1}. "
                            f"See `{blocked_key}`."
                        )
                    raise RuntimeError(
                        f"Plan-review halted in cycle {cycle + 1} by a transient "
                        f"agent-runtime failure (external/infra — e.g. the Claude "
                        f"account ran out of usage/quota mid-revision, or the agent "
                        f"CLI was terminated), NOT a content-convergence failure. "
                        f"Re-run when the runtime is available. See `{blocked_key}`."
                    )

                # ── Cascade test-plan revisions (per-SF only, no compile) ─
                # Any PRD/design/plan/system-design revision can invalidate
                # the AC-ids that cite them. test-plan is not in
                # _ARTIFACT_CONFIGS because it has no compiled top-level
                # artifact — handle it as a terminal cascade here. Gate on
                # revision_plan.requests (not revision_meta) so test-plan-only
                # revisions still dispatch when main artifacts weren't touched.
                if revision_plan.requests:
                    test_plan_requests: list[RevisionRequest] = []
                    affected_slugs_set: set[str] = set()
                    for req in revision_plan.requests:
                        affected_with_tp = [
                            slug
                            for slug in req.affected_subfeatures
                            if await runner.artifacts.get(
                                f"test-plan:{slug}", feature=feature,
                            )
                        ]
                        if affected_with_tp:
                            test_plan_requests.append(
                                RevisionRequest(
                                    description=req.description,
                                    reasoning=req.reasoning,
                                    affected_subfeatures=affected_with_tp,
                                    # Forward the AC-coverage signal so the
                                    # test-planner adds ACs for new/changed REQs
                                    # in the SAME cycle. Dropping these caused a
                                    # producer-consumer lag loop (test-plan
                                    # trailed one cycle and re-flagged stale).
                                    affected_requirement_ids=list(
                                        req.affected_requirement_ids
                                    ),
                                    severity=req.severity,
                                    cross_subfeature=req.cross_subfeature,
                                )
                            )
                            affected_slugs_set.update(affected_with_tp)
                    if test_plan_requests:
                        try:
                            test_plan_result = await targeted_revision(
                                runner, feature, self.name,
                                revision_plan=RevisionPlan(
                                    requests=test_plan_requests,
                                    new_decisions=list(revision_plan.new_decisions),
                                ),
                                decomposition=decomposition,
                                base_role=test_planner_role,
                                output_type=TestPlan,
                                artifact_prefix="test-plan",
                                context_keys=["project", "scope"],
                                checkpoint_prefix=f"cycle-{cycle + 1}",
                                prior_decisions=prior_decisions,
                            )
                            if not test_plan_result.ok:
                                blocked_failures.extend(
                                    f"test-plan:{failure.slug} — {failure.reason}"
                                    for failure in test_plan_result.failed
                                )
                                if not test_plan_result.has_only_transient_failures:
                                    blocked_has_content_failure = True
                                revision_results.append(
                                    f"test-plan: FAILED (revision batches failed for {len(test_plan_result.failed)} subfeatures)"
                                )
                                raise RuntimeError("test-plan targeted revision failed")
                            # Refresh decision ledger and regenerate summaries
                            # for every affected SF — mirrors the per-SF
                            # post-cascade work in subfeature.py so downstream
                            # consumers see current decisions + summaries.
                            for slug in sorted(affected_slugs_set):
                                revised_text = await runner.artifacts.get(
                                    f"test-plan:{slug}", feature=feature,
                                ) or ""
                                await refresh_decision_ledger(
                                    runner,
                                    feature,
                                    ledger_key=f"decisions:{slug}",
                                    label=f"Decision Ledger — {slug}",
                                    source_phase="plan-review-test-planning",
                                    artifact_kind="test-plan",
                                    state=state,
                                    control=None,
                                    subfeature_slug=slug,
                                    source_texts=[revised_text],
                                    summary_key=f"decisions-summary:{slug}",
                                )
                                await generate_summary(
                                    runner, feature, "test-plan", slug,
                                )
                            revision_results.append(
                                f"test-plan: revised ({len(test_plan_requests)} requests, {len(affected_slugs_set)} SFs)"
                            )
                        except Exception as exc:
                            logger.error("test-plan cascade revision crashed: %s", exc)
                            revision_results.append(
                                "test-plan: FAILED (cascade revision crashed)"
                            )
                            if not blocked_failures:
                                blocked_failures.append(
                                    f"test-plan: cascade revision crashed ({exc})"
                                )
                                if not _is_transient_runtime_failure(exc):
                                    blocked_has_content_failure = True

                # ── Cascade system-design revisions (per-SF, opt-in) ──────
                # Mirrors the test-plan cascade but for per-SF
                # `system-design:{slug}` artifacts, which otherwise trail one
                # cycle behind PRD/design/plan revisions and re-flag stale in
                # the next cycle. Gated behind IRIAI_PLAN_REVIEW_SD_CASCADE
                # (default OFF) — when the flag is off this whole block is a
                # strict no-op and plan-review behavior is byte-identical to
                # today. Targeted-only: routes through `targeted_revision`,
                # never adds or alters FULL_DOCUMENT-regen behavior.
                if PLAN_REVIEW_SD_CASCADE and revision_plan.requests:
                    sd_requests: list[RevisionRequest] = []
                    sd_affected_slugs_set: set[str] = set()
                    for req in revision_plan.requests:
                        affected_with_sd = [
                            slug
                            for slug in req.affected_subfeatures
                            if await runner.artifacts.get(
                                f"system-design:{slug}", feature=feature,
                            )
                        ]
                        if affected_with_sd:
                            sd_requests.append(
                                RevisionRequest(
                                    description=req.description,
                                    reasoning=req.reasoning,
                                    affected_subfeatures=affected_with_sd,
                                    affected_requirement_ids=list(
                                        req.affected_requirement_ids
                                    ),
                                    severity=req.severity,
                                    cross_subfeature=req.cross_subfeature,
                                )
                            )
                            sd_affected_slugs_set.update(affected_with_sd)
                    if sd_requests:
                        try:
                            sd_result = await targeted_revision(
                                runner, feature, self.name,
                                revision_plan=RevisionPlan(
                                    requests=sd_requests,
                                    new_decisions=list(revision_plan.new_decisions),
                                ),
                                decomposition=decomposition,
                                base_role=architect_role,
                                output_type=SystemDesign,
                                artifact_prefix="system-design",
                                context_keys=["project", "scope", "prd", "design"],
                                checkpoint_prefix=f"cycle-{cycle + 1}",
                                prior_decisions=prior_decisions,
                            )
                            if not sd_result.ok:
                                blocked_failures.extend(
                                    f"system-design:{failure.slug} — {failure.reason}"
                                    for failure in sd_result.failed
                                )
                                if not sd_result.has_only_transient_failures:
                                    blocked_has_content_failure = True
                                revision_results.append(
                                    f"system-design: FAILED (revision batches failed for {len(sd_result.failed)} subfeatures)"
                                )
                                raise RuntimeError(
                                    "system-design targeted revision failed"
                                )
                            for slug in sorted(sd_affected_slugs_set):
                                revised_text = await runner.artifacts.get(
                                    f"system-design:{slug}", feature=feature,
                                ) or ""
                                await refresh_decision_ledger(
                                    runner,
                                    feature,
                                    ledger_key=f"decisions:{slug}",
                                    label=f"Decision Ledger — {slug}",
                                    source_phase="plan-review-system-design",
                                    artifact_kind="system-design",
                                    state=state,
                                    control=None,
                                    subfeature_slug=slug,
                                    source_texts=[revised_text],
                                    summary_key=f"decisions-summary:{slug}",
                                )
                                await generate_summary(
                                    runner, feature, "system-design", slug,
                                )
                            revision_results.append(
                                f"system-design: revised ({len(sd_requests)} requests, {len(sd_affected_slugs_set)} SFs)"
                            )
                        except Exception as exc:
                            logger.error(
                                "system-design cascade revision crashed: %s", exc
                            )
                            revision_results.append(
                                "system-design: FAILED (cascade revision crashed)"
                            )
                            if not blocked_failures:
                                blocked_failures.append(
                                    f"system-design: cascade revision crashed ({exc})"
                                )
                                if not _is_transient_runtime_failure(exc):
                                    blocked_has_content_failure = True

                if blocked_failures:
                    blocked_report = (
                        f"# Plan Review Blocked — Cycle {cycle + 1}\n\n"
                        + (
                            ""
                            if blocked_has_content_failure
                            else (
                                "**Transient agent-runtime failure** (external/infra "
                                "— e.g. the Claude account ran out of usage/quota "
                                "mid-revision, or the agent CLI was terminated). This "
                                "is NOT a content-convergence failure; re-run when the "
                                "runtime is available.\n\n"
                            )
                        )
                        + "The revision wave did not complete cleanly, so plan review "
                        "stopped before downstream verification and task planning.\n\n"
                        "## Failures\n\n"
                        + "\n".join(f"- {failure}" for failure in blocked_failures)
                        + (
                            "\n\n## Revision Results\n\n"
                            + "\n".join(f"- {r}" for r in revision_results)
                            if revision_results
                            else ""
                        )
                    )
                    blocked_key = f"plan-review-cycle-{cycle + 1}-blocked"
                    await runner.artifacts.put(blocked_key, blocked_report, feature=feature)
                    mirror = runner.services.get("artifact_mirror")
                    if mirror:
                        mirror.write_artifact(feature.id, blocked_key, blocked_report)
                    # Deliberately NO `-revised` marker here: this cycle FAILED
                    # (see the matching comment on the first blocked block).
                    await runner.run(
                        Notify(
                            message=(
                                f"## Plan Review Blocked (Cycle {cycle + 1})\n\n"
                                "Revision batches failed, so the workflow stopped before "
                                "re-running reviewers or generating downstream planning outputs.\n\n"
                                + "\n".join(f"- {failure}" for failure in blocked_failures)
                            ),
                        ),
                        feature,
                        phase_name=self.name,
                    )
                    if blocked_has_content_failure:
                        raise RuntimeError(
                            f"Plan-review revisions failed in cycle {cycle + 1}. "
                            f"See `plan-review-cycle-{cycle + 1}-blocked`."
                        )
                    raise RuntimeError(
                        f"Plan-review halted in cycle {cycle + 1} by a transient "
                        f"agent-runtime failure (external/infra — e.g. the Claude "
                        f"account ran out of usage/quota mid-revision, or the agent "
                        f"CLI was terminated), NOT a content-convergence failure. "
                        f"Re-run when the runtime is available. See "
                        f"`plan-review-cycle-{cycle + 1}-blocked`."
                    )

                # ── Save revision summary so continue logic can advance ─
                revision_summary = (
                    f"# Revisions Applied — Cycle {cycle + 1}\n\n"
                    + "\n".join(f"- {r}" for r in revision_results)
                )
                await runner.artifacts.put(
                    f"plan-review-cycle-{cycle + 1}-revised",
                    revision_summary,
                    feature=feature,
                )
                # Clear any stale `-blocked` marker from a previously failed
                # attempt at this cycle — the resume check treats a lingering
                # blocked marker as "re-run this cycle", which would loop
                # forever once the re-run has actually succeeded.
                await _clear_blocked_cycle_marker(runner, feature, cycle)

                # Notify user of revision results
                await runner.run(
                    Notify(
                        message=(
                            f"## Revisions Applied (Cycle {cycle + 1})\n\n"
                            + "\n".join(f"- {r}" for r in revision_results)
                            + "\n\nRe-running reviewers to verify..."
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )
            else:
                if revision_plan and revision_plan.new_decisions:
                    logger.info("Persisted plan-review decisions with no revision dispatch required")
                else:
                    logger.warning("No revision requests extracted from discussion")
                # Cycle completed (no revision dispatch needed) — clear any
                # lingering `-blocked` row so a future resume can't re-grind it.
                await _clear_blocked_cycle_marker(runner, feature, cycle)

            cycle += 1

        return await self._run_gates(runner, feature, state, decomposition)

    async def _run_gates(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BuildState,
        decomposition: SubfeatureDecomposition,
    ) -> BuildState:
        """Interview-based gate reviews on all compiled artifacts."""
        # ── Recompile from per-SF sources before gate review ──
        # When entering gates via plan-review-complete skip, the compiled
        # artifacts may be stale. Recompile each type so gate reviewers
        # see the latest per-SF content.
        for prefix, _base_role, _output_type, compiler_actor, broad_key in _ARTIFACT_CONFIGS:
            gate_marker = await runner.artifacts.get(
                f"plan-review-gate:{prefix}", feature=feature,
            )
            if gate_marker:
                continue  # Already gate-approved — don't recompile
            logger.info("Recompiling %s from per-SF sources before gate review", prefix)
            compiled = await compile_artifacts(
                runner, feature, self.name,
                compiler_actor=compiler_actor,
                decomposition=decomposition,
                artifact_prefix=prefix,
                broad_key=broad_key,
                final_key=prefix,
                # Deterministic top-level union (Part 2) — recompile-before-gate.
                deterministic_final_merge=True,
                # Resumable per-piece reuse: a re-entered recompile (gate cycle
                # or restart) reuses unchanged clusters/bundles instead of
                # re-doing the whole compile.
                incremental_compile=True,
            )
            if compiled:
                hosting = runner.services.get("hosting")
                if hosting:
                    await hosting.update(feature.id, prefix, compiled)

        # Gate checkpointing: marker artifacts (plan-review-gate:{prefix})
        # distinguish "gate-approved" from "revised in Step 1" (same DB key).

        # PRD
        if await runner.artifacts.get("plan-review-gate:prd", feature=feature):
            logger.info("PRD gate already approved — skipping")
            state.prd = await runner.artifacts.get("prd", feature=feature) or state.prd
        else:
            prd_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=lead_pm_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="prd",
                compiled_key="prd",
                base_role=pm_role,
                output_type=PRD,
                compiler_actor=pm_compiler,
                broad_key="prd:broad",
                deterministic_final_merge=True,
            )
            state.prd = prd_text
            await runner.artifacts.put("plan-review-gate:prd", "approved", feature=feature)

        # Design
        if await runner.artifacts.get("plan-review-gate:design", feature=feature):
            logger.info("Design gate already approved — skipping")
            state.design = await runner.artifacts.get("design", feature=feature) or state.design
        else:
            design_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=lead_designer_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="design",
                compiled_key="design",
                base_role=designer_role,
                output_type=DesignDecisions,
                compiler_actor=design_compiler,
                broad_key="design:broad",
                context_keys=["project", "scope", "prd"],
                deterministic_final_merge=True,
            )
            state.design = design_text
            await runner.artifacts.put("plan-review-gate:design", "approved", feature=feature)

        # Technical Plan
        if await runner.artifacts.get("plan-review-gate:plan", feature=feature):
            logger.info("Plan gate already approved — skipping")
            state.plan = await runner.artifacts.get("plan", feature=feature) or state.plan
        else:
            plan_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=lead_architect_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="plan",
                compiled_key="plan",
                base_role=architect_role,
                output_type=TechnicalPlan,
                compiler_actor=plan_arch_compiler,
                broad_key="plan:broad",
                context_keys=["project", "scope", "prd", "design"],
                deterministic_final_merge=True,
            )
            state.plan = plan_text
            await runner.artifacts.put("plan-review-gate:plan", "approved", feature=feature)

        # System Design
        if state.system_design:
            if await runner.artifacts.get("plan-review-gate:system-design", feature=feature):
                logger.info("System design gate already approved — skipping")
                state.system_design = (
                    await runner.artifacts.get("system-design", feature=feature)
                    or state.system_design
                )
            else:
                sd_text = await interview_gate_review(
                    runner, feature, self.name,
                    lead_actor=lead_architect_gate_reviewer,
                    decomposition=decomposition,
                    artifact_prefix="system-design",
                    compiled_key="system-design",
                    base_role=architect_role,
                    output_type=SystemDesign,
                    compiler_actor=sysdesign_compiler,
                    broad_key="plan:broad",
                    context_keys=["project", "scope", "prd", "design"],
                    deterministic_final_merge=True,
                )
                state.system_design = sd_text
                await runner.artifacts.put(
                    "plan-review-gate:system-design", "approved", feature=feature,
                )

        state.plan, state.system_design = await sync_compiled_decision_mirrors(
            runner,
            feature,
            plan_text=state.plan,
            system_design_text=state.system_design,
        )
        return state

    @staticmethod
    async def _load_decomposition(
        state: BuildState, runner: WorkflowRunner, feature: Feature
    ) -> SubfeatureDecomposition:
        """Load decomposition from state or artifact store."""
        decomp_text = state.decomposition
        if not decomp_text:
            decomp_text = await runner.artifacts.get("decomposition", feature=feature) or ""
        if decomp_text:
            try:
                return SubfeatureDecomposition.model_validate(_json.loads(decomp_text))
            except Exception:
                pass
        return SubfeatureDecomposition()
