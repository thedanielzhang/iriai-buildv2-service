from __future__ import annotations

import json as _json
import logging
from typing import Any

from iriai_compose import AgentActor, Ask, Feature, Phase, Respond, WorkflowRunner, to_str

from ....models.outputs import (
    PRD,
    DesignDecisions,
    Envelope,
    ReviewOutcome,
    SubfeatureDecomposition,
    SubfeatureEdge,
    SystemDesign,
    TechnicalPlan,
    Verdict,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    architect_role,
    citation_reviewer_role,
    design_compiler,
    designer_role,
    lead_architect_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_pm_gate_reviewer,
    plan_arch_compiler,
    plan_compiler_role,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    user,
)
from ..._common import compile_artifacts, interview_gate_review, targeted_revision
from ..._common._helpers import _extract_revision_plan
from ..._common._tasks import HostedInterview

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3

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
    "8. Acceptance criteria that are unverifiable given the plan's file scope\n\n"
    "Every gap gets its own concern entry. A clean PASS means you missed something."
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
    "Every gap gets its own concern entry. A clean PASS means you missed something."
)

_CITATION_PROMPT = (
    "Your goal is to find every broken or missing citation in this subfeature's artifacts. "
    "Every decision and claim must be traceable.\n\n"
    "Focus on:\n"
    "1. Decision IDs (D-*) that don't exist in the decision log\n"
    "2. Code references ([Source: path:line]) where the file/function doesn't exist\n"
    "3. Requirements referenced in plan steps that don't exist in the PRD\n"
    "4. Journey IDs referenced in verification blocks that don't exist in the PRD\n"
    "5. Component IDs referenced in tasks that don't exist in the design\n\n"
    "Every broken reference gets its own concern entry. A clean PASS means you missed something."
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

# Actors for review — no context_keys (artifacts loaded manually into prompt)
_sf_reviewer = AgentActor(name="sf-reviewer", role=plan_compiler_role, context_keys=[])
_citation_sf_reviewer = AgentActor(name="sf-citation", role=citation_reviewer_role, context_keys=[])
_edge_reviewer = AgentActor(name="edge-reviewer", role=plan_compiler_role, context_keys=[])

# Artifact configs for targeted revision dispatch
_ARTIFACT_CONFIGS = [
    ("prd", pm_role, PRD, pm_compiler, "prd:broad"),
    ("design", designer_role, DesignDecisions, design_compiler, "design:broad"),
    ("plan", architect_role, TechnicalPlan, plan_arch_compiler, "plan:broad"),
]


def _make_parallel_actor(base: AgentActor, suffix: str) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name."""
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=base.role,
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

    # Full artifacts for this SF
    for prefix in ("prd", "design", "plan", "system-design"):
        text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
        if text:
            parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")

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
) -> str:
    """Build review context for one cross-SF edge: full artifacts of both SFs."""
    parts: list[str] = [
        f"## Edge: {edge.from_subfeature} → {edge.to_subfeature}\n"
        f"**Interface type:** {edge.interface_type}\n"
        f"**Description:** {edge.description}\n"
        f"**Data contract:** {edge.data_contract}\n"
    ]
    for slug in (edge.from_subfeature, edge.to_subfeature):
        for prefix in ("prd", "design", "plan", "system-design"):
            text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
            if text:
                parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")
    return "\n\n---\n\n".join(parts)


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


def _batch(items: list, size: int) -> list[list]:
    """Split a list into batches of the given size."""
    return [items[i:i + size] for i in range(0, len(items), size)]


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


def _summarize_verdicts(
    sf_verdicts: dict[str, dict[str, Verdict]],
    edge_verdicts: list[tuple[SubfeatureEdge, Verdict]],
) -> str:
    """Short summary for Slack notification."""
    failed_items: list[str] = []
    for slug, verdicts in sf_verdicts.items():
        for name, v in verdicts.items():
            if not v.approved:
                n = len(v.concerns) + len(v.gaps)
                failed_items.append(f"**{slug}/{name}**: {n} issues")
    for edge, v in edge_verdicts:
        if not v.approved:
            n = len(v.concerns) + len(v.gaps)
            failed_items.append(
                f"**{edge.from_subfeature} → {edge.to_subfeature}**: {n} issues"
            )
    if not failed_items:
        return "All reviews passed."
    return "\n".join(f"- {item}" for item in failed_items)


# ── Phase ────────────────────────────────────────────────────────────────────


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await self._load_decomposition(state, runner, feature)

        # ── Step 1: Two-pass review loop ─────────────────────────────
        cycle = 0
        while True:
            # ── Pass 1: Per-subfeature review ────────────────────────
            sf_verdicts: dict[str, dict[str, Verdict]] = {}

            for sf in decomposition.subfeatures:
                logger.info("Reviewing subfeature %s (%d/%d)",
                            sf.slug, len(sf_verdicts) + 1, len(decomposition.subfeatures))

                context = await _build_sf_review_context(runner, feature, sf.slug, decomposition)

                try:
                    results = await runner.parallel(
                        [
                            Ask(
                                actor=_make_parallel_actor(_sf_reviewer, f"comp-{sf.slug}"),
                                prompt=f"{_SCOPE_PREFIX}{context}\n\n{_COMPLETENESS_PROMPT}",
                                output_type=Verdict,
                            ),
                            Ask(
                                actor=_make_parallel_actor(_sf_reviewer, f"sec-{sf.slug}"),
                                prompt=f"{_SCOPE_PREFIX}{context}\n\n{_SECURITY_PROMPT}",
                                output_type=Verdict,
                            ),
                            Ask(
                                actor=_make_parallel_actor(_citation_sf_reviewer, f"cit-{sf.slug}"),
                                prompt=f"{_SCOPE_PREFIX}{context}\n\n{_CITATION_PROMPT}",
                                output_type=Verdict,
                            ),
                        ],
                        feature,
                        fail_fast=False,
                    )
                except ExceptionGroup as eg:
                    # fail_fast=False still raises ExceptionGroup in iriai-compose
                    logger.error("SF review %s had crashes: %s", sf.slug, eg)
                    results = [_ERROR_VERDICT, _ERROR_VERDICT, _ERROR_VERDICT]

                sf_verdicts[sf.slug] = {
                    "completeness": _safe_verdict(results[0]),
                    "security": _safe_verdict(results[1]),
                    "citation": _safe_verdict(results[2]),
                }

            # ── Pass 2: Pairwise edge review ─────────────────────────
            unique_edges = _deduplicate_edges(decomposition.edges)
            edge_verdicts: list[tuple[SubfeatureEdge, Verdict]] = []

            for edge_batch in _batch(unique_edges, 3):
                logger.info("Reviewing edge batch: %s",
                            [f"{e.from_subfeature}→{e.to_subfeature}" for e in edge_batch])

                # Build contexts before dispatching (can't await in list comp for parallel)
                edge_contexts = []
                for e in edge_batch:
                    ctx = await _build_edge_review_context(runner, feature, e)
                    edge_contexts.append(ctx)

                try:
                    results = await runner.parallel(
                        [
                            Ask(
                                actor=_make_parallel_actor(
                                    _edge_reviewer,
                                    f"edge-{e.from_subfeature}-{e.to_subfeature}",
                                ),
                                prompt=f"{_SCOPE_PREFIX}{ctx}\n\n{_EDGE_PROMPT}",
                                output_type=Verdict,
                            )
                            for e, ctx in zip(edge_batch, edge_contexts)
                        ],
                        feature,
                        fail_fast=False,
                    )
                except ExceptionGroup as eg:
                    logger.error("Edge review batch had crashes: %s", eg)
                    results = [_ERROR_VERDICT] * len(edge_batch)

                for e, r in zip(edge_batch, results):
                    edge_verdicts.append((e, _safe_verdict(r)))

            # ── Check if all approved ────────────────────────────────
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
                break

            # ── Compile report, host it ──────────────────────────────
            report = _compile_review_report(sf_verdicts, edge_verdicts)
            await runner.artifacts.put(
                f"plan-review-cycle-{cycle + 1}", report, feature=feature,
            )

            report_url = ""
            hosting = runner.services.get("hosting")
            if hosting:
                report_url = await hosting.push_qa(
                    feature.id, f"plan-review-cycle-{cycle + 1}",
                    report, f"Plan Review Cycle {cycle + 1}",
                )

            # ── Interactive discussion with user ─────────────────────
            review_envelope: Envelope[ReviewOutcome] = await runner.run(
                HostedInterview(
                    questioner=lead_architect_gate_reviewer,
                    responder=user,
                    initial_prompt=(
                        f"## Plan Review Cycle {cycle + 1} — Issues Found\n\n"
                        f"{_summarize_verdicts(sf_verdicts, edge_verdicts)}\n\n"
                        + (f"**[View Full Report]({report_url})**\n\n" if report_url else "")
                        + "I've identified the issues above across all subfeatures and "
                        "cross-subfeature edges. Let's discuss them and decide how to proceed.\n\n"
                        "For each issue, I need your decision:\n"
                        "- Which subfeature should change?\n"
                        "- What should the fix look like?\n"
                        "- Or should we skip this issue?\n\n"
                        "When we've resolved all issues, you have two choices:\n"
                        "- **'No changes needed'** → all artifacts are acceptable as-is, "
                        "skip to gate reviews\n"
                        "- **'Dispatch fixes'** → I'll extract your decisions into revision "
                        "requests and send them to the subfeature agents\n\n"
                        "Set approved=true ONLY for 'no changes needed'. "
                        "Set approved=false with revision_plan when you want fixes dispatched."
                    ),
                    output_type=Envelope[ReviewOutcome],
                    done=envelope_done,
                    artifact_key=f"plan-review-discussion-{cycle + 1}",
                    artifact_label=f"Plan Review Discussion — Cycle {cycle + 1}",
                ),
                feature,
                phase_name=self.name,
            )

            # Check if user said "no changes needed"
            outcome = review_envelope.output if review_envelope else None
            if outcome and outcome.approved:
                logger.info("User accepted artifacts as-is — skipping revisions")
                break

            # ── Extract revision plan from discussion ────────────────
            discussion_text = await runner.artifacts.get(
                f"plan-review-discussion-{cycle + 1}", feature=feature,
            ) or ""

            revision_plan = await _extract_revision_plan(
                runner, feature, self.name,
                f"{report}\n\n## User Discussion\n\n{discussion_text}",
                decomposition,
            )

            if revision_plan and revision_plan.requests:
                revision_results: list[str] = []

                for prefix, base_role, output_type, compiler_actor, broad_key in _ARTIFACT_CONFIGS:
                    # Check if any request affects SFs that have this artifact type
                    affected_requests = []
                    for req in revision_plan.requests:
                        for slug in req.affected_subfeatures:
                            has = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
                            if has:
                                affected_requests.append(req)
                                break
                    if not affected_requests:
                        continue

                    from ....models.outputs import RevisionPlan as RP
                    filtered_plan = RP(requests=affected_requests)

                    old_text = await runner.artifacts.get(prefix, feature=feature) or ""
                    old_size = len(old_text)

                    try:
                        await targeted_revision(
                            runner, feature, self.name,
                            revision_plan=filtered_plan,
                            decomposition=decomposition,
                            base_role=base_role,
                            output_type=output_type,
                            artifact_prefix=prefix,
                            context_keys=["project", "scope"],
                        )
                        new_text = await compile_artifacts(
                            runner, feature, self.name,
                            compiler_actor=compiler_actor,
                            decomposition=decomposition,
                            artifact_prefix=prefix,
                            broad_key=broad_key,
                            final_key=prefix,
                        )
                        new_size = len(new_text) if new_text else 0

                        # Size guard
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
                    except Exception as exc:
                        logger.error("Failed to revise %s: %s", prefix, exc, exc_info=True)
                        revision_results.append(f"{prefix}: FAILED ({exc})")

                # Notify user of revision results
                await runner.run(
                    Respond(
                        responder=user,
                        prompt=(
                            f"## Revisions Applied (Cycle {cycle + 1})\n\n"
                            + "\n".join(f"- {r}" for r in revision_results)
                            + "\n\nRe-running reviewers to verify..."
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )
            else:
                logger.warning("No revision requests extracted from discussion")

            cycle += 1

        # ── Step 2: Interview-based gate reviews on all artifacts ──

        # PRD
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
        )
        state.prd = prd_text

        # Design
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
        )
        state.design = design_text

        # Technical Plan
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
        )
        state.plan = plan_text

        # System Design
        if state.system_design:
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
            )
            state.system_design = sd_text

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
