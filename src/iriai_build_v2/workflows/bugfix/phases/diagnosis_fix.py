from __future__ import annotations

import logging

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import BugFixResult, HandoverDoc, ReproductionResult, RootCauseAnalysis, TaskOutcome
from ....models.state import BugFixState
from ....roles import bug_fixer, bug_reproducer, rca_architecture_analyst, rca_symptoms_analyst
from ....services.markdown import to_markdown

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 3


class DiagnosisAndFixPhase(Phase):
    name = "diagnosis-fix"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BugFixState
    ) -> BugFixState:
        handover = HandoverDoc()

        for iteration in range(MAX_ITERATIONS):
            logger.info("Diagnosis-fix iteration %d/%d", iteration + 1, MAX_ITERATIONS)

            # ── Step A: Root Cause Analysis (parallel) ────────────────────
            handover_context = ""
            if handover.failed_attempts:
                handover.compress()
                handover_context = (
                    f"\n\n## Prior Fix Attempts (DO NOT REPEAT)\n\n"
                    f"{to_markdown(handover)}\n\n"
                    "The above fixes did NOT resolve the bug. "
                    "Consider what they missed."
                )

            rca_a, rca_b = await runner.parallel(
                [
                    Ask(
                        actor=rca_symptoms_analyst,
                        prompt=(
                            "## Investigation Approach: Trace from Symptoms\n\n"
                            "Start from where the bug manifests (the user-facing symptom) "
                            "and trace backwards through the code. Follow the request path, "
                            "find where behavior diverges from expectation.\n\n"
                            f"## Bug Report\n{state.bug_report}\n\n"
                            f"## Reproduction Evidence\n{state.reproduction}"
                            f"{handover_context}"
                        ),
                        output_type=RootCauseAnalysis,
                    ),
                    Ask(
                        actor=rca_architecture_analyst,
                        prompt=(
                            "## Investigation Approach: Trace from Architecture\n\n"
                            "Examine the affected area's data model, state management, "
                            "service boundaries, and deploy configuration. Look for race "
                            "conditions, missing validations, incorrect state transitions, "
                            "or config mismatches.\n\n"
                            f"## Bug Report\n{state.bug_report}\n\n"
                            f"## Reproduction Evidence\n{state.reproduction}"
                            f"{handover_context}"
                        ),
                        output_type=RootCauseAnalysis,
                    ),
                ],
                feature,
            )

            rca_a_text = to_str(rca_a)
            rca_b_text = to_str(rca_b)
            await runner.artifacts.put("root_cause_a", rca_a_text, feature=feature)
            await runner.artifacts.put("root_cause_b", rca_b_text, feature=feature)
            state.root_cause_a = rca_a_text
            state.root_cause_b = rca_b_text

            # ── Step B: Adjudication & Fix ────────────────────────────────
            fix_prompt = (
                "You have two independent root cause analyses for this bug. "
                "Assess which is correct (or synthesize a better explanation), "
                "then implement the minimal fix.\n\n"
                f"## Bug Report\n{state.bug_report}\n\n"
                f"## Reproduction Evidence\n{state.reproduction}\n\n"
                f"## Analyst A — Trace from Symptoms\n{rca_a_text}\n\n"
                f"## Analyst B — Trace from Architecture\n{rca_b_text}"
            )
            if handover.failed_attempts:
                fix_prompt += f"\n\n## Prior Fix Attempts (DO NOT REPEAT)\n\n{to_markdown(handover)}"

            fix_result: BugFixResult = await runner.run(
                Ask(
                    actor=bug_fixer,
                    prompt=fix_prompt,
                    output_type=BugFixResult,
                ),
                feature,
                phase_name=self.name,
            )

            fix_text = to_str(fix_result)
            await runner.artifacts.put("fix", fix_text, feature=feature)
            state.fix = fix_text

            # ── Step C: Push & Verify ─────────────────────────────────────
            await runner.run(
                Ask(
                    actor=bug_fixer,
                    prompt=(
                        "Commit and push the fix to the current branch so the "
                        "preview environment rebuilds. Use a descriptive commit "
                        "message referencing the bug."
                    ),
                ),
                feature,
                phase_name=self.name,
            )

            from ....tasks.preview import LaunchPreviewServerTask

            try:
                info = await runner.run(
                    LaunchPreviewServerTask(project=state.project, force=True),
                    feature,
                    phase_name=self.name,
                )
                preview_url = next(iter(info.urls.values()), state.preview_url)
                state.preview_url = preview_url
                await runner.artifacts.put("preview_url", preview_url, feature=feature)
            except Exception as exc:
                logger.warning("Preview redeploy failed, using existing URL: %s", exc)

            verification: ReproductionResult = await runner.run(
                Ask(
                    actor=bug_reproducer,
                    prompt=(
                        "Verify that the bug is NOW FIXED. Re-run the reproduction "
                        "steps and confirm the bug NO LONGER occurs.\n\n"
                        f"## Preview URL\n{state.preview_url}\n\n"
                        f"## Bug Report\n{state.bug_report}\n\n"
                        "Use all available channels (browser, API, DB, deploy status) "
                        "to verify the fix. Report reproduced=False if the bug is fixed."
                    ),
                    output_type=ReproductionResult,
                ),
                feature,
                phase_name=self.name,
            )

            verification_text = to_str(verification)
            await runner.artifacts.put("verification", verification_text, feature=feature)
            state.verification = verification_text

            # ── Loop control ──────────────────────────────────────────────
            if not verification.reproduced:
                logger.info("Bug verified as fixed on iteration %d", iteration + 1)
                handover.completed.append(TaskOutcome(
                    task_id=f"fix-iter-{iteration + 1}",
                    status="completed",
                    summary=fix_result.summary,
                    files_changed=fix_result.files_created + fix_result.files_modified,
                ))
                state.handover = to_str(handover)
                await runner.artifacts.put("handover", state.handover, feature=feature)
                return state

            # Bug still present — record failure in handover
            handover.record_failure(
                task_id=f"fix-iter-{iteration + 1}",
                summary=fix_result.summary,
                failure_reason=(
                    f"Bug still reproducible. Verification: {verification_text[:500]}"
                ),
            )
            state.handover = to_str(handover)
            await runner.artifacts.put("handover", state.handover, feature=feature)

            logger.warning(
                "Bug still present after iteration %d, looping back",
                iteration + 1,
            )

        logger.error(
            "Bug not fixed after %d iterations. Passing to approval gate.",
            MAX_ITERATIONS,
        )
        return state
