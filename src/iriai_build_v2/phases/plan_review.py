from __future__ import annotations

from iriai_compose import Ask, Feature, Gate, Phase, Respond, WorkflowRunner, to_str

from ..models.outputs import Verdict
from ..models.state import BuildState
from ..roles import plan_compiler, user


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # Parallel: plan-compiler + security review run concurrently
        # Using two Ask tasks with the same plan_compiler actor but different prompts
        results = await runner.parallel(
            [
                Ask(
                    actor=plan_compiler,
                    prompt=(
                        "Review the technical plan for completeness and correctness. "
                        "Verify all PRD requirements are addressed and implementation "
                        "steps are actionable."
                    ),
                    output_type=Verdict,
                ),
                Ask(
                    actor=plan_compiler,
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

        review_summary = (
            f"## Completeness Review\n"
            f"{to_str(completeness_verdict)}\n\n"
            f"## Security Review\n"
            f"{to_str(security_verdict)}"
        )

        # Respond: human provides free-form notes
        user_notes = await runner.run(
            Respond(
                responder=user,
                prompt=f"Review verdicts:\n\n{review_summary}\n\nAny notes or concerns?",
            ),
            feature,
            phase_name=self.name,
        )
        await runner.artifacts.put("user-notes", str(user_notes), feature=feature)
        state.user_notes = str(user_notes)

        # Gate: final approval before implementation
        approved = await runner.run(
            Gate(
                approver=user,
                prompt="All reviews complete. Proceed to implementation?",
            ),
            feature,
            phase_name=self.name,
        )
        if approved is not True:
            raise RuntimeError("Plan review not approved. Aborting workflow.")

        return state
