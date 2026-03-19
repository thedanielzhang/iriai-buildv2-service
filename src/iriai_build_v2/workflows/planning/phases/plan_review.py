from __future__ import annotations

import logging

from iriai_compose import Ask, Feature, Phase, Respond, WorkflowRunner, to_str

from ....models.outputs import PRD, DesignDecisions, SystemDesign, TechnicalPlan, Verdict
from ....models.state import BuildState
from ....roles import (
    architect,
    designer,
    plan_completeness_reviewer,
    plan_security_reviewer,
    pm,
    user,
)
from ..._common import gate_and_revise

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Auto-fix loop — parallel reviews until both approve ──
        cycle = 0
        while True:
            results = await runner.parallel(
                [
                    Ask(
                        actor=plan_completeness_reviewer,
                        prompt=(
                            "Review the technical plan for completeness and correctness. "
                            "Verify all PRD requirements are addressed and implementation "
                            "steps are actionable."
                        ),
                        output_type=Verdict,
                    ),
                    Ask(
                        actor=plan_security_reviewer,
                        prompt=(
                            "Review the technical plan for security concerns. Check for "
                            "potential vulnerabilities, insecure patterns, and missing "
                            "security considerations."
                        ),
                        output_type=Verdict,
                    ),
                ],
                feature,
            )

            completeness_verdict, security_verdict = results

            if completeness_verdict.approved and security_verdict.approved:
                break

            review_summary = (
                f"## Completeness Review\n"
                f"{to_str(completeness_verdict)}\n\n"
                f"## Security Review\n"
                f"{to_str(security_verdict)}"
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
                            "Continue auto-fixing or provide guidance for the architect?"
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )
                review_summary += f"\n\n## User Guidance\n{user_input}"

            # Route feedback to architect for revision
            feedback_parts = []
            if not completeness_verdict.approved:
                feedback_parts.append(f"Completeness issues:\n{to_str(completeness_verdict)}")
            if not security_verdict.approved:
                feedback_parts.append(f"Security issues:\n{to_str(security_verdict)}")
            feedback = "\n\n".join(feedback_parts)

            revised_plan: TechnicalPlan = await runner.run(
                Ask(
                    actor=architect,
                    prompt=f"Fix these review issues in the technical plan:\n\n{feedback}",
                    output_type=TechnicalPlan,
                ),
                feature,
                phase_name=self.name,
            )
            plan_text = to_str(revised_plan)
            await runner.artifacts.put("plan", plan_text, feature=feature)
            state.plan = plan_text

            hosting = runner.services.get("hosting")
            if hosting:
                await hosting.update(feature.id, "plan", plan_text)

            cycle += 1

        # ── Step 2: User gates on all three artifacts ──
        hosting = runner.services.get("hosting")

        prd_url = hosting.get_url("prd") if hosting else None
        design_url = hosting.get_url("design") if hosting else None
        plan_url = hosting.get_url("plan") if hosting else None

        # PRD
        prd_label = f"PRD\nReview in browser: {prd_url}" if prd_url else "PRD"
        prd, prd_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=state.prd, actor=pm, output_type=PRD,
            approver=user, label=prd_label,
            artifact_key="prd",
        )
        await runner.artifacts.put("prd", prd_text, feature=feature)
        state.prd = prd_text

        # Design
        design_label = f"Design decisions\nReview in browser: {design_url}" if design_url else "Design decisions"
        design, design_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=state.design, actor=designer, output_type=DesignDecisions,
            approver=user, label=design_label,
            artifact_key="design",
        )
        await runner.artifacts.put("design", design_text, feature=feature)
        state.design = design_text

        # Plan
        plan_label = f"Technical plan\nReview in browser: {plan_url}" if plan_url else "Technical plan"
        plan, plan_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=state.plan, actor=architect, output_type=TechnicalPlan,
            approver=user, label=plan_label,
            artifact_key="plan",
        )
        await runner.artifacts.put("plan", plan_text, feature=feature)
        state.plan = plan_text

        # System Design
        sd_url = hosting.get_url("system-design") if hosting else None
        if state.system_design:
            sd_label = (
                f"System Design\nReview in browser: {sd_url}"
                if sd_url
                else "System Design"
            )
            sd, sd_text = await gate_and_revise(
                runner, feature, self.name,
                artifact=state.system_design, actor=architect, output_type=SystemDesign,
                approver=user, label=sd_label,
                artifact_key="system-design",
            )
            await runner.artifacts.put("system-design", sd_text, feature=feature)
            state.system_design = sd_text

        return state
