from __future__ import annotations

from iriai_compose import Feature, Gate, Phase, WorkflowRunner

from ....models.state import BugFixState
from ....roles import user


class ApprovalPhase(Phase):
    name = "approval"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        summary = (
            f"## Bug Report\n{state.bug_report}\n\n"
            f"## Fix Applied\n{state.fix}\n\n"
            f"## Verification\n{state.verification}\n\n"
            f"## Regression Results\n{state.regression}\n\n"
            "Approve the bug fix?"
        )

        approved = await runner.run(
            Gate(approver=user, prompt=summary),
            feature,
            phase_name=self.name,
        )

        if approved is True:
            state.metadata["approved"] = True
        else:
            state.metadata["approved"] = False
            state.metadata["rejection_feedback"] = (
                str(approved) if isinstance(approved, str) else ""
            )

        return state
