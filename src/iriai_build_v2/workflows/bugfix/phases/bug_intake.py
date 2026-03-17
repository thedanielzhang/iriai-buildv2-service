from __future__ import annotations

import logging

from iriai_compose import Feature, Interview, Phase, WorkflowRunner

from ....models.outputs import BugReport, Envelope, envelope_done
from ....models.state import BugFixState
from ....roles import bug_interviewer, user
from ..._common import gate_and_revise

logger = logging.getLogger(__name__)


class BugIntakePhase(Phase):
    name = "bug-intake"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        # Skip interview if bug report was pre-loaded via --bug-report
        if state.bug_report:
            logger.info("Bug report pre-loaded, skipping interview")
            return state

        envelope: Envelope[BugReport] = await runner.run(
            Interview(
                questioner=bug_interviewer,
                responder=user,
                initial_prompt=(
                    f"I'm going to help you document this bug: {feature.name}\n\n"
                    "Let me ask some questions to build a complete bug report. "
                    "What exactly happened?"
                ),
                output_type=Envelope[BugReport],
                done=envelope_done,
            ),
            feature,
            phase_name=self.name,
        )

        bug_report = envelope.output
        assert bug_report is not None

        bug_report, bug_report_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=bug_report, actor=bug_interviewer, output_type=BugReport,
            approver=user, label="Bug Report",
        )

        await runner.artifacts.put("bug_report", bug_report_text, feature=feature)
        state.bug_report = bug_report_text
        return state
