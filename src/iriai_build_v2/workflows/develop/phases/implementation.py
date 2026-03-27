from __future__ import annotations

import itertools
import logging

from iriai_compose import AgentActor, Ask, Feature, Gate, Phase, WorkflowRunner, to_str
from iriai_compose.actors import Role

from ....config import BUDGET_TIERS
from ....models.outputs import (
    BugFixAttempt,
    BugTriage,
    HandoverDoc,
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    RootCauseAnalysis,
    Verdict,
)
from ....models.state import BuildState
from ....roles import (
    implementer,
    integration_tester,
    qa_engineer,
    reviewer,
    root_cause_analyst,
    security_auditor,
    user,
    verifier,
)
from ....services.markdown import to_markdown

logger = logging.getLogger(__name__)

VERIFY_RETRIES = 2
WARN_AFTER_CYCLES = 3
MAX_FIX_ATTEMPTS = 7

# ── Inline triage role (lightweight, no tools) ───────────────────────────────

_triage_role = Role(
    name="bug-triager",
    prompt=(
        "You triage bug reports from code review verdicts. Group ALL "
        "issues by their likely root cause. Issues that probably stem from "
        "the same underlying problem (same file, same data flow, same "
        "missing check) go in the same group. Every issue must be assigned "
        "to a group — do not skip or defer any."
    ),
    tools=[],
    model=BUDGET_TIERS["opus"],
)


def _make_parallel_actor(base: AgentActor, suffix: str) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name."""
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=base.role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


class ImplementationPhase(Phase):
    name = "implementation"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        dag_json = await runner.artifacts.get("dag", feature=feature)
        dag = ImplementationDAG.model_validate_json(dag_json)

        prior_attempts: list[BugFixAttempt] = []
        bug_counter = itertools.count(1)
        cycle = 0

        while True:
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Implementation cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )

            # ── Step 1: Implementation ───────────────────────────────────
            impl_text, dag_failure, handover = await _implement_dag(runner, feature, dag)

            await runner.artifacts.put("implementation", impl_text, feature=feature)
            await runner.artifacts.put("handover", to_str(handover), feature=feature)
            state.implementation = impl_text
            state.handover = to_str(handover)

            # If the DAG stopped early on a verify failure, go through RCA
            if dag_failure:
                attempts = await _diagnose_and_fix(
                    runner, feature, dag_failure, "verify",
                    qa_engineer, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "verify") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "DAG verification", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # Compress handover before passing to QA/review
            handover.compress()
            handover_context = to_markdown(handover)

            # ── Step 2: Full QA ──────────────────────────────────────────
            qa_verdict: Verdict = await runner.run(
                Ask(
                    actor=qa_engineer,
                    prompt=(
                        f"## Implementation Handover\n\n{handover_context}\n\n"
                        "Test the full implementation. Run the test suite, check "
                        "for runtime errors, and verify the acceptance criteria "
                        "from the PRD and design specs are met. Cross-check "
                        "implementation against the full upstream artifacts "
                        "in your context."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put("qa-verdict", to_str(qa_verdict), feature=feature)

            if not _is_approved(qa_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, qa_verdict, "qa_engineer",
                    qa_engineer, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "qa_engineer") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "QA", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 3: Integration Test ─────────────────────────────────
            integration_verdict: Verdict = await runner.run(
                Ask(
                    actor=integration_tester,
                    prompt=(
                        f"## Implementation Handover\n\n{handover_context}\n\n"
                        "Execute ALL user journeys from the PRD against the "
                        "implementation. Use Playwright for UI journeys, Bash "
                        "for API/CLI journeys. Every journey step must produce "
                        "evidence. Check happy paths, error cases, and boundary "
                        "conditions."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put(
                "integration-verdict", to_str(integration_verdict), feature=feature
            )

            if not _is_approved(integration_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, integration_verdict, "integration_tester",
                    integration_tester, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "integration_tester") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Integration Test", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 4: Code Review ──────────────────────────────────────
            review_verdict: Verdict = await runner.run(
                Ask(
                    actor=reviewer,
                    prompt=(
                        f"## Implementation Handover\n\n{handover_context}\n\n"
                        "Review the implementation for code quality, adherence to "
                        "the technical plan, design decisions, and system design. "
                        "Cross-check against the full upstream artifacts in your context."
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
                attempts = await _diagnose_and_fix(
                    runner, feature, review_verdict, "code_reviewer",
                    reviewer, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "code_reviewer") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Code Review", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 5: Security Audit ───────────────────────────────────
            security_verdict: Verdict = await runner.run(
                Ask(
                    actor=security_auditor,
                    prompt=(
                        f"## Implementation Handover\n\n{handover_context}\n\n"
                        "Audit the implementation for security vulnerabilities. "
                        "Check OWASP Top 10, auth on every endpoint, secrets in "
                        "code, input validation, and data exposure. Cross-check "
                        "against the security profile in the PRD."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put(
                "security-verdict", to_str(security_verdict), feature=feature
            )

            if not _is_approved(security_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, security_verdict, "security_auditor",
                    security_auditor, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "security_auditor") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Security Audit", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 6: Verifier — confirm all journeys work ────────────
            verifier_verdict: Verdict = await runner.run(
                Ask(
                    actor=verifier,
                    prompt=(
                        f"## Implementation Handover\n\n{handover_context}\n\n"
                        "Verify that ALL user journeys from the PRD work end-to-end. "
                        "For web/full-stack projects, interact with the UI via real "
                        "Playwright clicks and form fills — do not substitute API calls. "
                        "Every journey must produce evidence of working correctly."
                    ),
                    output_type=Verdict,
                ),
                feature,
                phase_name=self.name,
            )
            await runner.artifacts.put(
                "verifier-verdict", to_str(verifier_verdict), feature=feature
            )

            if not _is_approved(verifier_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, verifier_verdict, "verifier",
                    verifier, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "verifier") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Verifier", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 7: User Approval ────────────────────────────────────
            attempts_summary = ""
            if prior_attempts:
                fixed = [a for a in prior_attempts if a.re_verify_result == "PASS"]
                failed_all = [a for a in prior_attempts if a.re_verify_result != "PASS"]
                attempts_summary = (
                    f"\n\n## Bug Fix Attempts ({len(fixed)} fixed, {len(failed_all)} failed)\n\n"
                    + "\n".join(
                        f"- **{a.bug_id}** ({a.source_verdict}, group {a.group_id or 'single'}): "
                        f"{a.description[:80]} → {a.re_verify_result}"
                        for a in prior_attempts
                    )
                )

            summary = (
                f"## Implementation Handover\n\n{handover_context}\n\n"
                f"## QA Verdict\n{to_str(qa_verdict)}\n\n"
                f"## Integration Test\n{to_str(integration_verdict)}\n\n"
                f"## Code Review\n{to_str(review_verdict)}\n\n"
                f"## Security Audit\n{to_str(security_verdict)}\n\n"
                f"## Verifier\n{to_str(verifier_verdict)}"
                f"{attempts_summary}"
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

            # User rejection — go through RCA with user feedback
            user_feedback = str(approved) if isinstance(approved, str) else "Please revise."
            attempts = await _diagnose_and_fix(
                runner, feature, user_feedback, "user",
                qa_engineer, implementer, prior_attempts, bug_counter,
            )
            prior_attempts.extend(attempts)
            await _store_attempts(runner, feature, prior_attempts)
            cycle += 1


# ── DAG execution ────────────────────────────────────────────────────────────


def _build_task_prompt(task: ImplementationTask) -> str:
    """Construct a rich prompt from an ImplementationTask's structured fields."""
    parts: list[str] = [f"# {task.name}\n\n{task.description}"]

    # ── File Scope ────────────────────────────────────────────────────
    if task.file_scope:
        lines = [f"- [{fs.action.upper()}] `{fs.path}`" for fs in task.file_scope]
        parts.append("## File Scope\n" + "\n".join(lines))
    elif task.files:
        parts.append(
            "## File Scope\n"
            + "\n".join(f"- `{f}`" for f in task.files)
        )

    # ── Acceptance Criteria ───────────────────────────────────────────
    if task.acceptance_criteria:
        ac_lines: list[str] = []
        for ac in task.acceptance_criteria:
            ac_lines.append(f"- {ac.description}")
            if ac.not_criteria:
                ac_lines.append(f"  - **NOT:** {ac.not_criteria}")
        parts.append("## Acceptance Criteria\n" + "\n".join(ac_lines))

    # ── Counterexamples ──────────────────────────────────────────────
    if task.counterexamples:
        parts.append(
            "## Counterexamples (Do NOT)\n"
            + "\n".join(f"- {ce}" for ce in task.counterexamples)
        )

    # ── Security Concerns ────────────────────────────────────────────
    if task.security_concerns:
        parts.append(
            "## Security Concerns\n"
            + "\n".join(f"- {sc}" for sc in task.security_concerns)
        )

    # ── data-testid Assignments ──────────────────────────────────────
    if task.testid_assignments:
        parts.append(
            "## data-testid Assignments\n"
            + "\n".join(f"- `{tid}`" for tid in task.testid_assignments)
        )

    # ── Reference Material ──────────────────────────────────────────
    if task.reference_material:
        ref_lines = []
        for ref in task.reference_material:
            ref_lines.append(f"### {ref.source}\n{ref.content}")
        parts.append("## Reference Material\n\n" + "\n\n".join(ref_lines))

    # ── Traceability ─────────────────────────────────────────────────
    trace_lines: list[str] = []
    if task.requirement_ids:
        trace_lines.append(f"Requirements: {', '.join(task.requirement_ids)}")
    if task.step_ids:
        trace_lines.append(f"Plan steps: {', '.join(task.step_ids)}")
    if task.journey_ids:
        trace_lines.append(f"Journeys: {', '.join(task.journey_ids)}")
    if trace_lines:
        parts.append("## Traceability\n" + "\n".join(trace_lines))

    return "\n\n".join(parts)


async def _implement_dag(
    runner: WorkflowRunner, feature: Feature, dag: ImplementationDAG
) -> tuple[str, str, HandoverDoc]:
    """Execute the full DAG with per-group verification and handover tracking.

    Returns ``(impl_text, failure, handover)``.  *failure* is empty when every
    group passed verification.
    """
    tasks_by_id = {t.id: t for t in dag.tasks}
    all_results: list[object] = []
    handover = HandoverDoc()

    for group_idx, group in enumerate(dag.execution_order):
        group_tasks = [tasks_by_id[tid] for tid in group]

        # Build prompts with handover context from prior groups
        handover_context = ""
        if handover.completed or handover.failed_attempts:
            handover.compress()
            handover_context = f"\n\n## Handover — Prior Work\n\n{to_markdown(handover)}"

        # ── Implement group tasks in parallel ────────────────────────
        results = await runner.parallel(
            [
                Ask(
                    actor=_make_parallel_actor(implementer, f"g{group_idx}-t{task_idx}"),
                    prompt=_build_task_prompt(t) + handover_context,
                    output_type=ImplementationResult,
                )
                for task_idx, t in enumerate(group_tasks)
            ],
            feature,
        )
        all_results.extend(results)

        # ── Verify: confirm claimed work + basic correctness ─────────
        group_files = _collect_files(results)
        verdict = await _verify(runner, feature, results, group_files, group_tasks)

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
            verdict = await _verify(runner, feature, [*results, fix_result], group_files, group_tasks)

        # Record outcomes in handover
        if _is_approved(verdict):
            for r in results:
                if isinstance(r, ImplementationResult):
                    handover.record_success(r)
        else:
            # Group failed — record and stop
            for r in results:
                if isinstance(r, ImplementationResult):
                    handover.record_failure(
                        r.task_id, r.summary, _format_feedback("Verify", verdict),
                    )
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
            return impl_text, failure, handover

    return "\n\n".join(to_str(r) for r in all_results), "", handover


async def _verify(
    runner: WorkflowRunner,
    feature: Feature,
    results: list[object],
    files: list[str],
    tasks: list[ImplementationTask] | None = None,
) -> Verdict:
    """Verify a group's implementation: claimed work exists + basic tests."""
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = ", ".join(files) if files else "recently changed files"

    # Collect reference material from the tasks being verified so the
    # verifier can check implementation against upstream specs.
    ref_context = ""
    if tasks:
        ref_parts = []
        for t in tasks:
            if t.reference_material:
                for ref in t.reference_material:
                    ref_parts.append(f"**{ref.source}** (task {t.id}):\n{ref.content}")
        if ref_parts:
            ref_context = (
                "\n\n## Upstream Specs (verify implementation against these)\n\n"
                + "\n\n---\n\n".join(ref_parts)
            )

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
                "any existing tests for these files\n"
                "5. Implementation matches the upstream specs in Reference Material"
                f"{ref_context}\n\n"
                "This is a per-group verification, not a full QA pass."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )


# ── RCA → Fix → Re-verify pipeline ──────────────────────────────────────────


def _format_indexed_issues(verdict: Verdict) -> str:
    """Format verdict concerns and gaps with indices for the triage agent."""
    lines: list[str] = []
    for i, c in enumerate(verdict.concerns):
        file_hint = f" (file: {c.file})" if c.file else ""
        lines.append(f"[C{i}] ({c.severity}) {c.description}{file_hint}")
    for i, g in enumerate(verdict.gaps):
        lines.append(f"[G{i}] ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines)


def _extract_group_issues(verdict: Verdict, group: object) -> str:
    """Extract the specific issues for a bug group from the verdict."""
    lines: list[str] = []
    for idx in getattr(group, "issue_indices", []):
        if idx < len(verdict.concerns):
            c = verdict.concerns[idx]
            file_hint = f" (file: {c.file})" if c.file else ""
            lines.append(f"- ({c.severity}) {c.description}{file_hint}")
    for idx in getattr(group, "gap_indices", []):
        if idx < len(verdict.gaps):
            g = verdict.gaps[idx]
            lines.append(f"- ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines) if lines else to_str(verdict)


def _compute_fix_schedule(
    rcas: list[tuple[str, RootCauseAnalysis]],
) -> list[list[str]]:
    """Compute parallel-safe fix rounds using greedy graph coloring.

    Groups whose ``affected_files`` don't overlap can fix in the same round.
    Groups with overlapping files are placed in separate sequential rounds.
    """
    file_sets: dict[str, set[str]] = {
        gid: set(rca.affected_files) for gid, rca in rcas
    }
    remaining = set(file_sets.keys())
    schedule: list[list[str]] = []

    while remaining:
        round_ids: list[str] = []
        round_files: set[str] = set()
        for gid in sorted(remaining):
            if not file_sets[gid] & round_files:
                round_ids.append(gid)
                round_files |= file_sets[gid]
        schedule.append(round_ids)
        remaining -= set(round_ids)

    return schedule


def _format_prior_attempts(prior_attempts: list[BugFixAttempt]) -> str:
    """Format prior attempts as context for RCA/fix agents."""
    if not prior_attempts:
        return ""
    prior_lines = []
    for a in prior_attempts:
        prior_lines.append(
            f"### Attempt {a.attempt_number} ({a.bug_id})\n"
            f"- **Source:** {a.source_verdict}\n"
            f"- **Group:** {a.group_id or 'single'}\n"
            f"- **Description:** {a.description}\n"
            f"- **Root Cause:** {a.root_cause}\n"
            f"- **Fix Applied:** {a.fix_applied}\n"
            f"- **Files Modified:** {', '.join(a.files_modified)}\n"
            f"- **Result:** {a.re_verify_result}"
        )
    return (
        "\n\n## Prior Fix Attempts (DO NOT REPEAT these approaches)\n\n"
        + "\n\n".join(prior_lines)
    )


async def _diagnose_and_fix(
    runner: WorkflowRunner,
    feature: Feature,
    verdict: object,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_attempts: list[BugFixAttempt],
    bug_counter: itertools.count,  # type: ignore[type-arg]
) -> list[BugFixAttempt]:
    """Structured failure handling: triage → parallel RCA → fix → re-verify.

    For string verdicts or single-issue verdicts, takes the single-bug path.
    For multi-issue Verdicts, triages by root cause and dispatches in parallel
    where file scopes don't overlap.

    Returns a list of BugFixAttempt records (one per bug group).
    """
    verdict_text = to_str(verdict)
    prior_context = _format_prior_attempts(prior_attempts)
    attempt_number = sum(1 for a in prior_attempts if a.source_verdict == source) + 1

    # ── Short-circuit: string verdict or ≤1 issue ────────────────────
    use_single_path = True
    if isinstance(verdict, Verdict):
        total_issues = len(verdict.concerns) + len(verdict.gaps)
        if total_issues > 1:
            use_single_path = False

    if use_single_path:
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
        )
        return [attempt]

    # ── Multi-issue path: triage → parallel RCA → fix → re-verify ────
    assert isinstance(verdict, Verdict)

    # 1. Triage: group issues by root cause
    indexed_issues = _format_indexed_issues(verdict)
    triage: BugTriage = await runner.run(
        Ask(
            actor=AgentActor(name="bug-triager", role=_triage_role),
            prompt=(
                f"## Verdict from: {source}\n\n"
                f"### Summary\n{verdict.summary}\n\n"
                f"### Issues (reference by index)\n{indexed_issues}\n\n"
                "Group ALL issues by likely root cause. Every index must appear "
                "in exactly one group. Use issue_indices for [C*] entries and "
                "gap_indices for [G*] entries."
            ),
            output_type=BugTriage,
        ),
        feature,
        phase_name="implementation",
    )

    if not triage.groups:
        # Fallback: triage produced no groups — treat as single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
        )
        return [attempt]

    logger.info(
        "Triage produced %d bug groups from %d issues (source: %s)",
        len(triage.groups), len(verdict.concerns) + len(verdict.gaps), source,
    )

    # 2. Parallel RCA: one per group (read-only, always safe in parallel)
    rca_tasks = [
        Ask(
            actor=_make_parallel_actor(root_cause_analyst, f"rca-{group.group_id}"),
            prompt=(
                f"## Bug Group: {group.group_id}\n\n"
                f"### Likely Root Cause (from triage)\n{group.likely_root_cause}\n\n"
                f"### Issues in this group\n{_extract_group_issues(verdict, group)}\n\n"
                f"### Full Verdict Summary\n{verdict.summary}\n\n"
                "Investigate the root cause of these specific issues. Read the "
                "relevant code, trace the data flow, and identify the exact "
                "point of failure. Propose a conceptual fix approach — do NOT "
                "implement anything."
                f"{prior_context}"
            ),
            output_type=RootCauseAnalysis,
        )
        for group in triage.groups
    ]

    if len(rca_tasks) == 1:
        rca_results = [await runner.run(rca_tasks[0], feature, phase_name="implementation")]
    else:
        rca_results = await runner.parallel(rca_tasks, feature)

    # Build group_id → RCA mapping
    group_rcas: list[tuple[str, RootCauseAnalysis]] = []
    for group, rca_result in zip(triage.groups, rca_results):
        if isinstance(rca_result, RootCauseAnalysis):
            group_rcas.append((group.group_id, rca_result))

    if not group_rcas:
        # All RCAs failed — fallback to single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
        )
        return [attempt]

    # 3. File-overlap scheduling
    schedule = _compute_fix_schedule(group_rcas)
    logger.info(
        "Fix schedule: %d rounds for %d groups",
        len(schedule), len(group_rcas),
    )

    # Build lookup dicts
    rca_by_group = dict(group_rcas)
    group_by_id = {g.group_id: g for g in triage.groups}

    # 4. Fix dispatch: parallel within each round, sequential between rounds
    fix_results: dict[str, ImplementationResult] = {}

    for round_idx, round_ids in enumerate(schedule):
        fix_tasks = [
            Ask(
                actor=_make_parallel_actor(fixer, f"fix-{gid}"),
                prompt=(
                    f"## Bug Fix: group {gid}\n\n"
                    f"### Root Cause Analysis\n\n"
                    f"**Hypothesis:** {rca_by_group[gid].hypothesis}\n\n"
                    f"**Evidence:**\n"
                    + "\n".join(f"- {e}" for e in rca_by_group[gid].evidence)
                    + f"\n\n**Affected Files:** {', '.join(rca_by_group[gid].affected_files)}\n\n"
                    f"**Proposed Approach:** {rca_by_group[gid].proposed_approach}\n\n"
                    f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                    "Apply the fix described in the RCA. Be precise — fix only "
                    "what the root cause analysis identified. Report all files modified."
                    f"{prior_context}"
                ),
                output_type=ImplementationResult,
            )
            for gid in round_ids
        ]

        if len(fix_tasks) == 1:
            results = [await runner.run(fix_tasks[0], feature, phase_name="implementation")]
        else:
            results = await runner.parallel(fix_tasks, feature)

        for gid, result in zip(round_ids, results):
            if isinstance(result, ImplementationResult):
                fix_results[gid] = result

    # 5. Parallel re-verify: one per group (read-only, always safe)
    verify_tasks = [
        Ask(
            actor=_make_parallel_actor(original_reviewer, f"reverify-{gid}"),
            prompt=(
                f"## Re-verification: group {gid}\n\n"
                f"A fix was applied for the following issues.\n\n"
                f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                f"### Root Cause\n{rca_by_group[gid].hypothesis}\n\n"
                f"### Fix Applied\n{fix_results[gid].summary}\n\n"
                f"### Files Modified\n"
                + "\n".join(
                    f"- `{f}`"
                    for f in (fix_results[gid].files_created + fix_results[gid].files_modified)
                )
                + "\n\nRe-verify that the issues in this group are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code."
            ),
            output_type=Verdict,
        )
        for gid in fix_results
    ]

    if len(verify_tasks) == 1:
        verify_results = [await runner.run(verify_tasks[0], feature, phase_name="implementation")]
    else:
        verify_results = await runner.parallel(verify_tasks, feature)

    # 6. Collect BugFixAttempt records
    attempts: list[BugFixAttempt] = []
    for gid, re_verdict in zip(fix_results.keys(), verify_results):
        group = group_by_id[gid]
        fix = fix_results[gid]
        passed = _is_approved(re_verdict)

        description = group.likely_root_cause
        if passed:
            logger.info("Bug group %s fixed: %s", gid, description[:80])
        else:
            logger.warning("Bug group %s re-verify FAILED: %s", gid, description[:80])

        attempts.append(BugFixAttempt(
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            group_id=gid,
            source_verdict=source,
            description=description,
            root_cause=rca_by_group[gid].hypothesis,
            fix_applied=fix.summary,
            files_modified=fix.files_created + fix.files_modified,
            re_verify_result="PASS" if passed else "FAIL",
            attempt_number=attempt_number,
        ))

    return attempts


async def _single_rca_fix_verify(
    runner: WorkflowRunner,
    feature: Feature,
    verdict_text: str,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_context: str,
    bug_id: str,
    attempt_number: int,
) -> BugFixAttempt:
    """Single-bug RCA → fix → re-verify (no triage needed)."""
    # 1. Root Cause Analysis
    rca: RootCauseAnalysis = await runner.run(
        Ask(
            actor=root_cause_analyst,
            prompt=(
                f"## Bug Report: {bug_id}\n\n"
                f"### Failure Source: {source}\n\n"
                f"### Verdict\n\n{verdict_text}\n\n"
                "Investigate the root cause of this failure. Read the relevant "
                "code, trace the data flow, and identify the exact point of failure. "
                "Propose a conceptual fix approach — do NOT implement anything."
                f"{prior_context}"
            ),
            output_type=RootCauseAnalysis,
        ),
        feature,
        phase_name="implementation",
    )

    # 2. Fix via implementer
    fix_result: ImplementationResult = await runner.run(
        Ask(
            actor=fixer,
            prompt=(
                f"## Bug Fix: {bug_id}\n\n"
                f"### Root Cause Analysis\n\n"
                f"**Hypothesis:** {rca.hypothesis}\n\n"
                f"**Evidence:**\n"
                + "\n".join(f"- {e}" for e in rca.evidence)
                + f"\n\n**Affected Files:** {', '.join(rca.affected_files)}\n\n"
                f"**Proposed Approach:** {rca.proposed_approach}\n\n"
                f"### Original Verdict\n\n{verdict_text}\n\n"
                "Apply the fix described in the RCA. Be precise — fix only "
                "what the root cause analysis identified. Report all files modified."
                f"{prior_context}"
            ),
            output_type=ImplementationResult,
        ),
        feature,
        phase_name="implementation",
    )

    # 3. Re-verify with the SAME reviewer that found the bug
    re_verdict: Verdict = await runner.run(
        Ask(
            actor=original_reviewer,
            prompt=(
                f"## Re-verification: {bug_id}\n\n"
                f"A fix was applied for the following failure.\n\n"
                f"### Original Verdict\n\n{verdict_text}\n\n"
                f"### Root Cause\n\n{rca.hypothesis}\n\n"
                f"### Fix Applied\n\n{fix_result.summary}\n\n"
                f"### Files Modified\n\n"
                + "\n".join(f"- `{f}`" for f in (fix_result.files_created + fix_result.files_modified))
                + "\n\nRe-verify that the original issues are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )

    return BugFixAttempt(
        bug_id=bug_id,
        source_verdict=source,
        description=verdict_text[:200],
        root_cause=rca.hypothesis,
        fix_applied=fix_result.summary,
        files_modified=fix_result.files_created + fix_result.files_modified,
        re_verify_result="PASS" if _is_approved(re_verdict) else "FAIL",
        attempt_number=attempt_number,
    )


# ── Escalation and persistence ───────────────────────────────────────────────


async def _escalate_to_user(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    stage: str,
    failed_attempt: BugFixAttempt,
    all_attempts: list[BugFixAttempt],
) -> None:
    """Escalate to the user after MAX_FIX_ATTEMPTS failures from one source."""
    source_attempts = [a for a in all_attempts if a.source_verdict == failed_attempt.source_verdict]
    attempts_text = "\n".join(
        f"- **{a.bug_id}** (group {a.group_id or 'single'}, attempt {a.attempt_number}): "
        f"{a.description[:80]} → root cause: {a.root_cause[:80]} → {a.re_verify_result}"
        for a in source_attempts
    )
    logger.warning(
        "Escalating to user after %d failed attempts at %s stage",
        len(source_attempts), stage,
    )
    await runner.run(
        Gate(
            approver=user,
            prompt=(
                f"## Escalation: {stage} — {len(source_attempts)} fix attempts failed\n\n"
                f"Bug fixes from **{stage}** have failed {len(source_attempts)} times "
                f"(limit: {MAX_FIX_ATTEMPTS}).\n\n"
                f"### Latest Attempt\n"
                f"- **Bug:** {failed_attempt.bug_id} (group {failed_attempt.group_id or 'single'})\n"
                f"- **Root Cause:** {failed_attempt.root_cause}\n"
                f"- **Fix Applied:** {failed_attempt.fix_applied}\n"
                f"- **Files:** {', '.join(failed_attempt.files_modified)}\n"
                f"- **Result:** {failed_attempt.re_verify_result}\n\n"
                f"### All {stage} Attempts\n{attempts_text}\n\n"
                "Review the situation and approve to continue with "
                "the next implementation cycle, or provide guidance."
            ),
        ),
        feature,
        phase_name=phase_name,
    )


async def _store_attempts(
    runner: WorkflowRunner,
    feature: Feature,
    attempts: list[BugFixAttempt],
) -> None:
    """Persist bug fix attempts as an artifact for audit trail."""
    text = "\n\n".join(to_str(a) for a in attempts)
    await runner.artifacts.put("bug-fix-attempts", text, feature=feature)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _count_source_attempts(prior: list[BugFixAttempt], source: str) -> int:
    """Count total failed fix attempts from a given source verdict."""
    return sum(1 for a in prior if a.source_verdict == source and a.re_verify_result != "PASS")


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
