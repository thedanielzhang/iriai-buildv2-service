from __future__ import annotations

import logging

from iriai_compose import Ask, Feature, Gate, Phase, WorkflowRunner, to_str

from ..models.outputs import ImplementationDAG, ImplementationResult, Verdict
from ..models.state import BuildState
from ..roles import implementer, qa_engineer, reviewer, user

logger = logging.getLogger(__name__)

VERIFY_RETRIES = 2
WARN_AFTER_CYCLES = 3


class ImplementationPhase(Phase):
    name = "implementation"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        dag_json = await runner.artifacts.get("dag", feature=feature)
        dag = ImplementationDAG.model_validate_json(dag_json)

        feedback = ""
        cycle = 0

        while True:
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Implementation cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )

            # ── Step 1: Implementation ───────────────────────────────────
            if cycle == 0:
                impl_text, dag_failure = await _implement_dag(runner, feature, dag)
            else:
                impl_text = await _fix(runner, feature, feedback)
                dag_failure = ""

            await runner.artifacts.put("implementation", impl_text, feature=feature)
            state.implementation = impl_text

            # If the DAG stopped early on a verify failure, skip the
            # expensive QA/Review steps — we already know what's wrong.
            if dag_failure:
                feedback = dag_failure
                cycle += 1
                continue

            # ── Step 2: Full QA ──────────────────────────────────────────
            qa_verdict: Verdict = await runner.run(
                Ask(
                    actor=qa_engineer,
                    prompt=(
                        "Test the full implementation. Run the test suite, check "
                        "for runtime errors, and verify the acceptance criteria "
                        "from the PRD are met."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put("qa-verdict", to_str(qa_verdict), feature=feature)

            if not _is_approved(qa_verdict):
                feedback = _format_feedback("QA", qa_verdict)
                cycle += 1
                continue

            # ── Step 3: Code Review ──────────────────────────────────────
            review_verdict: Verdict = await runner.run(
                Ask(
                    actor=reviewer,
                    prompt=(
                        "Review the implementation for code quality, adherence to "
                        "the technical plan, security issues, and potential bugs."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put(
                "review-verdict", to_str(review_verdict), feature=feature
            )

            if not _is_approved(review_verdict):
                feedback = _format_feedback("Code Review", review_verdict)
                cycle += 1
                continue

            # ── Step 4: User Approval ────────────────────────────────────
            summary = (
                f"## QA Verdict\n{to_str(qa_verdict)}\n\n"
                f"## Code Review\n{to_str(review_verdict)}"
            )
            approved = await runner.run(
                Gate(
                    approver=user,
                    prompt=f"Implementation complete.\n\n{summary}\n\nApprove?",
                ),
                feature,
                phase_name=self.name,
            )
            if approved is True:
                return state

            feedback = _format_feedback(
                "User",
                str(approved) if isinstance(approved, str) else "Please revise.",
            )
            cycle += 1


# ── DAG execution ────────────────────────────────────────────────────────────


async def _implement_dag(
    runner: WorkflowRunner, feature: Feature, dag: ImplementationDAG
) -> tuple[str, str]:
    """Execute the full DAG with per-group verification.

    Returns ``(impl_text, failure)``.  *failure* is empty when every group
    passed verification.  If a group fails verification after retries the
    DAG stops early and *failure* describes the problem plus which tasks
    were never executed.
    """
    tasks_by_id = {t.id: t for t in dag.tasks}
    all_results: list[object] = []

    for group_idx, group in enumerate(dag.execution_order):
        group_tasks = [tasks_by_id[tid] for tid in group]

        # ── Implement group tasks in parallel ────────────────────────
        results = await runner.parallel(
            [
                Ask(
                    actor=implementer,
                    prompt=(
                        f"Implement task '{t.name}':\n{t.description}\n"
                        f"Files: {', '.join(t.files) if t.files else 'determine as needed'}"
                    ),
                    output_type=ImplementationResult,
                )
                for t in group_tasks
            ],
            feature,
        )
        all_results.extend(results)

        # ── Verify: confirm claimed work + basic correctness ─────────
        group_files = _collect_files(results)
        verdict = await _verify(runner, feature, results, group_files)

        for _ in range(VERIFY_RETRIES):
            if _is_approved(verdict):
                break
            fix_result = await runner.run(
                Ask(
                    actor=implementer,
                    prompt=(
                        "Verification failed. Fix these issues:\n\n"
                        f"{_format_feedback('Verify', verdict)}"
                    ),
                    output_type=ImplementationResult,
                ),
                feature,
                phase_name="implementation",
            )
            all_results.append(fix_result)
            group_files = list(set(group_files + _collect_files([fix_result])))
            verdict = await _verify(runner, feature, [*results, fix_result], group_files)

        if not _is_approved(verdict):
            # Group is still broken — stop the DAG.
            remaining = dag.execution_order[group_idx + 1 :]
            remaining_names = [
                tasks_by_id[tid].name for g in remaining for tid in g
            ]
            failure = _format_feedback("Verify", verdict)
            if remaining_names:
                failure += (
                    "\n\nThe DAG was halted. Unexecuted tasks: "
                    + ", ".join(remaining_names)
                )
            impl_text = "\n\n".join(to_str(r) for r in all_results)
            return impl_text, failure

    return "\n\n".join(to_str(r) for r in all_results), ""


async def _verify(
    runner: WorkflowRunner,
    feature: Feature,
    results: list[object],
    files: list[str],
) -> Verdict:
    """Verify a group's implementation: claimed work exists + basic tests."""
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = ", ".join(files) if files else "recently changed files"
    return await runner.run(
        Ask(
            actor=qa_engineer,
            prompt=(
                f"Verify this implementation group:\n\n{results_summary}\n\n"
                "For each result, confirm:\n"
                f"1. All claimed files exist on disk: {file_list}\n"
                "2. Files listed as modified were actually changed\n"
                "3. The changes align with the described summary\n"
                "4. The code compiles, imports correctly, and passes "
                "any existing tests for these files\n\n"
                "This is a per-group verification, not a full QA pass."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )


# ── Helpers ──────────────────────────────────────────────────────────────────


async def _fix(runner: WorkflowRunner, feature: Feature, feedback: str) -> str:
    """Ask the implementer to fix issues from the previous cycle.

    Feedback may include incomplete DAG information — the implementer
    should address all issues and complete any unexecuted tasks.
    """
    result: ImplementationResult = await runner.run(
        Ask(
            actor=implementer,
            prompt=(
                "The implementation needs fixes. Address all issues below, "
                "including completing any unexecuted tasks:\n\n"
                f"{feedback}\n\n"
                "Make the necessary changes and report what you fixed."
            ),
            output_type=ImplementationResult,
        ),
        feature,
        phase_name="implementation",
    )
    return to_str(result)


def _collect_files(results: list[object]) -> list[str]:
    """Extract file paths from implementation results."""
    files: list[str] = []
    for r in results:
        if isinstance(r, ImplementationResult):
            files.extend(r.files_created)
            files.extend(r.files_modified)
    return files


def _is_approved(verdict: object) -> bool:
    return isinstance(verdict, Verdict) and verdict.approved


def _format_feedback(source: str, verdict: object) -> str:
    return f"## {source} Feedback\n\n{to_str(verdict)}"
