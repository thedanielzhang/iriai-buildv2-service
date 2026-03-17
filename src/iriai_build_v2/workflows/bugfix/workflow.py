from __future__ import annotations

from iriai_compose import Phase, Workflow

from .phases import (
    ApprovalPhase,
    BaselinePhase,
    BugIntakePhase,
    BugReproductionPhase,
    CleanupPhase,
    DiagnosisAndFixPhase,
    EnvironmentSetupPhase,
    RegressionPhase,
)


class BugFixWorkflow(Workflow):
    name = "bugfix"

    def build_phases(self) -> list[type[Phase]]:
        return [
            BugIntakePhase,
            EnvironmentSetupPhase,
            BaselinePhase,
            BugReproductionPhase,
            DiagnosisAndFixPhase,
            RegressionPhase,
            ApprovalPhase,
            CleanupPhase,
        ]
