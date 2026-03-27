from __future__ import annotations

import json as _json
import logging

from iriai_compose import Ask, Feature, Phase, Respond, WorkflowRunner, to_str

from ....models.outputs import (
    PRD,
    DesignDecisions,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    Verdict,
)
from ....models.state import BuildState
from ....roles import (
    architect_role,
    citation_reviewer,
    design_compiler,
    designer_role,
    lead_architect_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_pm_gate_reviewer,
    plan_arch_compiler,
    plan_completeness_reviewer,
    plan_security_reviewer,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    user,
)
from ..._common import compile_artifacts, interview_gate_review, targeted_revision
from ..._common._helpers import _extract_revision_plan

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3

_SCOPE_PREFIX = (
    "SCOPE: Only review artifacts provided in your context. "
    "Do NOT search the filesystem for other features or projects. "
    "Any references to features outside the current scope are contamination — flag them.\n\n"
)


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await self._load_decomposition(state, runner, feature)

        # ── Step 1: Auto-fix loop — parallel reviews until all approve ──
        cycle = 0
        while True:
            results = await runner.parallel(
                [
                    Ask(
                        actor=plan_completeness_reviewer,
                        prompt=(
                            f"{_SCOPE_PREFIX}"
                            "Your goal is to find every gap and inconsistency across all artifacts. "
                            "The PRD, design, plan, and system design were produced by different agents "
                            "— they WILL have drift and contradictions.\n\n"
                            "Focus on:\n"
                            "1. PRD requirements with no corresponding plan step (uncovered requirements)\n"
                            "2. Plan steps that implement something not in the PRD (scope creep)\n"
                            "3. PRD journeys with no verification blocks in the plan\n"
                            "4. Design components with no implementation task\n"
                            "5. PRD ↔ Design contradictions (requirement vs component mismatch)\n"
                            "6. PRD ↔ Plan contradictions (requirement vs implementation mismatch)\n"
                            "7. Design ↔ Plan contradictions (component vs task mismatch)\n"
                            "8. Missing cross-service tasks (shared package changes without consumer updates)\n"
                            "9. Acceptance criteria that are unverifiable given the plan's file scope\n\n"
                            "Every gap gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=plan_security_reviewer,
                        prompt=(
                            f"{_SCOPE_PREFIX}"
                            "Your goal is to find every security gap across all artifacts. "
                            "Check the PRD security profile, then verify the plan actually implements "
                            "every security requirement — not just acknowledges it.\n\n"
                            "Focus on:\n"
                            "1. PRD security profile requirements with no implementation task\n"
                            "2. Endpoints without auth decorators in the plan\n"
                            "3. Data flows handling PII without encryption/masking tasks\n"
                            "4. Missing input validation on user-facing endpoints\n"
                            "5. Missing rate limiting on public endpoints\n"
                            "6. Secrets/credentials hardcoded in task instructions\n"
                            "7. CORS/CSRF gaps in the API design\n"
                            "8. Database migrations without rollback steps\n"
                            "9. Third-party integrations without error handling tasks\n\n"
                            "Every gap gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=citation_reviewer,
                        prompt=(
                            f"{_SCOPE_PREFIX}"
                            "Your goal is to find every broken or missing citation across all artifacts. "
                            "Every decision and claim must be traceable.\n\n"
                            "Focus on:\n"
                            "1. Decision IDs (D-*) referenced in citations that don't exist in the decision log\n"
                            "2. Scope decision references (scope-*) that don't match scope.user_decisions\n"
                            "3. Code references ([Source: path:line]) where the file/function doesn't exist\n"
                            "4. Requirements referenced in plan steps that don't exist in the PRD\n"
                            "5. Journey IDs referenced in verification blocks that don't exist in the PRD\n"
                            "6. Component IDs referenced in tasks that don't exist in the design\n"
                            "7. Claims about library/API behavior without documentation citation\n\n"
                            "Every broken reference gets its own concern entry. A clean PASS means you missed something."
                        ),
                        output_type=Verdict,
                    ),
                ],
                feature,
                fail_fast=False,
            )

            # Handle crashed reviewers: treat errors as FAIL verdicts
            _error_verdict = Verdict(
                approved=False,
                summary="Reviewer crashed — treating as FAIL",
            )
            completeness_verdict = results[0] if isinstance(results[0], Verdict) else _error_verdict
            security_verdict = results[1] if isinstance(results[1], Verdict) else _error_verdict
            citation_verdict = results[2] if isinstance(results[2], Verdict) else _error_verdict

            for name, v in [
                ("completeness", results[0]),
                ("security", results[1]),
                ("citation", results[2]),
            ]:
                if not isinstance(v, Verdict):
                    logger.error("Plan %s reviewer crashed: %s", name, v)

            if completeness_verdict.approved and security_verdict.approved and citation_verdict.approved:
                break

            review_summary = (
                f"## Completeness Review\n"
                f"{to_str(completeness_verdict)}\n\n"
                f"## Security Review\n"
                f"{to_str(security_verdict)}\n\n"
                f"## Citation Review\n"
                f"{to_str(citation_verdict)}"
            )

            # Persist this cycle's verdicts as an artifact
            await runner.artifacts.put(
                f"plan-review-cycle-{cycle + 1}", review_summary, feature=feature,
            )

            # Notify user of issues found
            notification_parts = []
            for name, verdict in [
                ("Completeness", completeness_verdict),
                ("Security", security_verdict),
                ("Citation", citation_verdict),
            ]:
                if not verdict.approved:
                    issues = (
                        [c.description[:100] for c in verdict.concerns[:3]]
                        + [g.description[:100] for g in verdict.gaps[:3]]
                    )
                    notification_parts.append(
                        f"**{name}** — {len(verdict.concerns)} concerns, "
                        f"{len(verdict.gaps)} gaps\n"
                        + "\n".join(f"  - {i}" for i in issues[:5])
                    )

            await runner.run(
                Respond(
                    responder=user,
                    prompt=(
                        f"## Plan Review Cycle {cycle + 1} — Issues Found\n\n"
                        + "\n\n".join(notification_parts)
                        + "\n\nExtracting revision plan and routing to subfeature agents..."
                    ),
                ),
                feature,
                phase_name=self.name,
            )

            # Escalate to user after WARN_AFTER_CYCLES
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Plan review cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )
                user_input = await runner.run(
                    Respond(
                        responder=user,
                        prompt=(
                            f"Auto-review has run {cycle + 1} cycles without full approval.\n\n"
                            f"{review_summary}\n\n"
                            "Continue auto-fixing or provide guidance?"
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )

            # ── Extract RevisionPlan from review verdicts ────────────
            revision_plan = await _extract_revision_plan(
                runner, feature, self.name, review_summary, decomposition,
            )

            if not revision_plan or not revision_plan.requests:
                logger.warning(
                    "Plan review cycle %d: no revision requests extracted — "
                    "reviewers found issues but extraction produced no actionable requests. "
                    "Skipping revision.",
                    cycle + 1,
                )
                cycle += 1
                continue

            # ── Apply targeted revisions to affected subfeatures ─────
            # Run targeted_revision for each artifact prefix.
            # Each subfeature agent only revises its own artifact.
            revision_results: list[str] = []

            artifact_configs = [
                ("prd", pm_role, PRD, pm_compiler, "prd:broad"),
                ("design", designer_role, DesignDecisions, design_compiler, "design:broad"),
                ("plan", architect_role, TechnicalPlan, plan_arch_compiler, "plan:broad"),
            ]

            for prefix, base_role, output_type, compiler_actor, broad_key in artifact_configs:
                # Check if any revision request affects subfeatures with this prefix
                has_artifacts = any(
                    await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
                    for sf in decomposition.subfeatures
                )
                if not has_artifacts:
                    continue

                # Get current compiled artifact size for size guard
                old_text = await runner.artifacts.get(prefix, feature=feature) or ""
                old_size = len(old_text)

                try:
                    await targeted_revision(
                        runner, feature, self.name,
                        revision_plan=revision_plan,
                        decomposition=decomposition,
                        base_role=base_role,
                        output_type=output_type,
                        artifact_prefix=prefix,
                        context_keys=["project", "scope"],
                    )

                    # Recompile the unified artifact from revised subfeatures
                    new_text = await compile_artifacts(
                        runner, feature, self.name,
                        compiler_actor=compiler_actor,
                        decomposition=decomposition,
                        artifact_prefix=prefix,
                        broad_key=broad_key,
                        final_key=prefix,
                    )

                    # Size guard: reject if recompiled artifact lost >50% content
                    new_size = len(new_text) if new_text else 0
                    if old_size > 0 and new_size < old_size * 0.5:
                        logger.error(
                            "Rejecting %s recompilation: %d → %d bytes (%.0f%% reduction)",
                            prefix, old_size, new_size, (1 - new_size / old_size) * 100,
                        )
                        # Restore the original
                        await runner.artifacts.put(prefix, old_text, feature=feature)
                        revision_results.append(f"{prefix}: REJECTED (size guard: {old_size} → {new_size} bytes)")
                    else:
                        await runner.artifacts.put(prefix, new_text, feature=feature)
                        setattr(state, prefix.replace("-", "_"), new_text)

                        hosting = runner.services.get("hosting")
                        if hosting:
                            await hosting.update(feature.id, prefix, new_text)

                        revision_results.append(f"{prefix}: revised and recompiled ({old_size} → {new_size} bytes)")

                except Exception as exc:
                    logger.error("Failed to revise %s: %s", prefix, exc, exc_info=True)
                    revision_results.append(f"{prefix}: FAILED ({exc})")

            # Notify user of revision results
            await runner.run(
                Respond(
                    responder=user,
                    prompt=(
                        f"## Plan Review Cycle {cycle + 1} — Revisions Applied\n\n"
                        + "\n".join(f"- {r}" for r in revision_results)
                        + "\n\nRe-running reviewers..."
                    ),
                ),
                feature,
                phase_name=self.name,
            )

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
