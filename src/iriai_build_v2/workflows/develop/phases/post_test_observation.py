"""Post-test observation gate: collect user observations, categorize, and dispatch fixes."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from iriai_compose import Ask, Feature, Phase, WorkflowRunner, to_str

from ....models.outputs import (
    Envelope,
    HandoverDoc,
    ImplementationResult,
    Observation,
    ObservationReport,
    RootCauseAnalysis,
    Verdict,
    envelope_done,
)
from ....services.markdown import to_markdown
from ....models.state import BuildState
from ....roles import (
    implementer,
    observation_collector,
    root_cause_analyst,
    test_author,
    user,
    verifier,
)
from ..._common import Interview
from ..._common._helpers import _offload_if_large
from .implementation import (
    _commit_repos,
    _commit_repos_in_root,
    _get_feature_root,
    _make_parallel_actor,
)

logger = logging.getLogger(__name__)

# No iteration cap — broad observations like "all E2E tests green" need
# many RCA→impl→verify cycles.  Managed manually via Slack.
MAX_FIX_ITERATIONS = 50


# Live testing instructions for verify prompts — extracted from
# implementation.py:802-816 (post-DAG verifier gate).
_LIVE_VERIFY_INSTRUCTIONS = (
    "\n\n**Live Testing Required (for projects with a frontend/UI):**\n"
    "- Interact with the UI via real Playwright clicks and form fills "
    "— do NOT substitute API calls.\n"
    "- You MUST capture Playwright screenshots as evidence. "
    "Save to `screenshots/` using descriptive names.\n"
    "- A UI fix without screenshot evidence is NOT verified.\n"
    "- If the app cannot be started, report it as a blocker — "
    "do NOT fall back to static-only verification.\n\n"
    "**For backend/library fixes:**\n"
    "- Run the test suite and verify all tests pass.\n"
    "- Execute API endpoints or CLI commands and verify responses.\n\n"
    "**Test execution strategy:**\n"
    "- Run the FULL test suite in a single command to get a complete picture: "
    "`npx playwright test --reporter=list`. The liveness timeout is disabled "
    "for the verifier role, so long-running suites will not be killed.\n"
    "- If the full suite fails, re-run only the failing tests to capture "
    "detailed error output: `npx playwright test path/to/failing.spec.ts`.\n"
    "- Do NOT run tests file-by-file unless investigating a specific failure.\n"
    "- For pytest: `pytest tests/ -v` (full suite first, then specific files "
    "for failures).\n\n"
    "Every fix must produce evidence of working correctly."
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _render_observation_report(report: ObservationReport) -> str:
    """Render an ObservationReport as readable markdown."""
    if not report.observations:
        return "_No observations._"
    lines: list[str] = []
    for obs in report.observations:
        lines.append(f"### {obs.id}: {obs.title}")
        lines.append(f"- **Category:** {obs.category}")
        lines.append(f"- **Severity:** {obs.severity}")
        lines.append(f"- **Affected area:** {obs.affected_area}")
        lines.append(f"- **Description:** {obs.description}")
        if obs.steps_to_reproduce:
            lines.append("- **Steps to reproduce:**")
            for step in obs.steps_to_reproduce:
                lines.append(f"  1. {step}")
        if obs.expected_behavior:
            lines.append(f"- **Expected behavior:** {obs.expected_behavior}")
        if obs.decision:
            lines.append(f"- **User decision:** {obs.decision}")
        lines.append("")
    return "\n".join(lines)


def _build_observation_context(
    report: ObservationReport,
    cycle: int,
    clarification_decisions: list[str],
    prior_fix_summary: str,
    feature_root: Path | None,
) -> str:
    """Build observation context passed to downstream agents.

    Large sections are offloaded to files (never truncated) so agents
    can read them on demand without bloating the inline prompt.
    """
    ctx = (
        f"## Post-Test Observation Report\n\n"
        f"### All Observations (Cycle {cycle})\n\n"
        f"{_render_observation_report(report)}\n\n"
    )

    if clarification_decisions:
        ctx += (
            "\n\n## User Decisions (AUTHORITATIVE)\n"
            "The user made the following design decisions during observation review. "
            "These override any conflicting spec.\n\n"
            + "\n".join(f"- {d}" for d in clarification_decisions)
            + "\n"
        )

    if prior_fix_summary:
        # Offload large prior fix summaries to a file instead of inlining.
        # Never truncate — agents read the full content from the file.
        offloaded = _offload_if_large(
            prior_fix_summary, feature_root, "prior-fix-summary",
        )
        ctx += (
            f"\n\n## Prior Observation Fixes (DO NOT REPEAT)\n"
            f"{offloaded}\n"
        )

    ctx += (
        "\n\n## Observation Fix Rules\n"
        "You ARE allowed to write tests as part of this fix. "
        "The normal restriction against writing tests is lifted "
        "for observation-phase fixes.\n"
    )

    return _offload_if_large(ctx, feature_root, "observation-context")


_CATEGORY_PRIORITY = {
    "clarification": 0,
    "bug": 1,
    "requirement": 2,
    "missing_test": 3,
}


def _sort_by_priority(observations: list[Observation]) -> list[Observation]:
    """Sort observations by category priority."""
    return sorted(observations, key=lambda o: _CATEGORY_PRIORITY.get(o.category, 99))


def _build_fix_summary(results: list[dict]) -> str:
    """Format dispatch results into a readable summary."""
    lines: list[str] = []
    for r in results:
        obs = r["observation"]
        status = r.get("status", "unknown")
        summary = r.get("summary", "")
        lines.append(f"- **{obs.id}** [{obs.category}] {obs.title}: {status}")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines) if lines else "_No fixes applied._"


# ── Prompt builders ────────────────────────────────────────────────────────


def _build_rca_prompt(
    obs: Observation, observation_context: str, ws_hint: str, prior_context: str,
    *,
    lens: str | None = None,
) -> str:
    """Build RCA prompt — varies by observation category.

    When *lens* is set, an analytical framing is prepended so parallel
    RCA agents investigate from different perspectives (mirroring the
    dual-RCA pattern in diagnosis_fix.py).
    """
    header = (
        f"## Investigation: {obs.id}\n\n"
        f"**Title:** {obs.title}\n"
        f"**Category:** {obs.category}\n"
        f"**Severity:** {obs.severity}\n"
        f"**Description:** {obs.description}\n"
        f"**Affected Area:** {obs.affected_area}\n"
    )
    if obs.expected_behavior:
        header += f"**Expected Behavior:** {obs.expected_behavior}\n"
    if obs.steps_to_reproduce:
        header += "**Steps to Reproduce:**\n"
        for step in obs.steps_to_reproduce:
            header += f"  1. {step}\n"
    if obs.decision:
        header += f"**User Decision (AUTHORITATIVE):** {obs.decision}\n"

    # Dual-RCA analytical framing
    lens_framing = ""
    if lens == "symptoms":
        lens_framing = (
            "\n**Analytical lens: SYMPTOMS → ROOT CAUSE.**\n"
            "Trace backward from the user-visible symptom. Start with what "
            "the test/user sees, then follow the execution path through the "
            "code to find where the behavior diverges from expectations.\n\n"
        )
    elif lens == "architecture":
        lens_framing = (
            "\n**Analytical lens: ARCHITECTURE → ROOT CAUSE.**\n"
            "Trace forward from the system architecture. Start with the data "
            "model, serialization layer, and state management, then identify "
            "structural issues that could produce the observed symptom.\n\n"
        )

    instructions = {
        "bug": (
            "Investigate the root cause of this bug. Trace from the symptoms "
            "through the code. Identify the exact point of failure, affected "
            "files, and propose a conceptual fix approach — do NOT implement."
        ),
        "clarification": (
            "Analyze the impact of this design decision. Identify:\n"
            "1. Where the current behavior is implemented\n"
            "2. What code changes are needed to match the user's decision\n"
            "3. What side effects or regressions to watch for\n"
            "Propose a conceptual approach — do NOT implement."
        ),
        "requirement": (
            "Analyze what functionality is missing. Identify:\n"
            "1. What golden path or feature should exist\n"
            "2. What existing components can be extended\n"
            "3. What new code needs to be written\n"
            "4. The scope and complexity of the work\n"
            "Propose an implementation plan — do NOT implement."
        ),
        "missing_test": (
            "Investigate what test coverage is missing. Identify:\n"
            "1. Which requirements or acceptance criteria lack tests\n"
            "2. What kind of tests are needed (unit, integration, E2E)\n"
            "3. What existing test patterns to follow\n"
            "Propose a test strategy — do NOT write tests."
        ),
    }

    return (
        header + lens_framing + "\n"
        + f"{observation_context}\n\n"
        + instructions.get(obs.category, instructions["bug"])
        + ws_hint + prior_context
    )


_CONFIDENCE_RANK = {"high": 3, "medium": 2, "low": 1, "contradiction": 0}


def _merge_rca(
    rca_symptoms: RootCauseAnalysis,
    rca_architecture: RootCauseAnalysis,
) -> RootCauseAnalysis:
    """Merge two parallel RCA results into a single unified analysis.

    Strategy: take the hypothesis with higher confidence, merge
    affected_files from both, and combine proposed approaches.
    """
    sym_rank = _CONFIDENCE_RANK.get(rca_symptoms.confidence, 1)
    arch_rank = _CONFIDENCE_RANK.get(rca_architecture.confidence, 1)

    # Prefer higher confidence; on tie prefer symptoms (closer to failure).
    if arch_rank > sym_rank:
        primary, secondary = rca_architecture, rca_symptoms
    else:
        primary, secondary = rca_symptoms, rca_architecture

    # Merge affected files (deduplicated, preserving order)
    seen: set[str] = set()
    merged_files: list[str] = []
    for f in list(primary.affected_files) + list(secondary.affected_files):
        if f not in seen:
            seen.add(f)
            merged_files.append(f)

    # Merge evidence lists
    merged_evidence = list(primary.evidence) + [
        e for e in secondary.evidence if e not in primary.evidence
    ]

    return RootCauseAnalysis(
        hypothesis=(
            f"{primary.hypothesis}\n\n"
            f"**Alternative analysis:** {secondary.hypothesis}"
        ),
        evidence=merged_evidence,
        affected_files=merged_files,
        proposed_approach=(
            f"{primary.proposed_approach}\n\n"
            f"**Alternative approach:** {secondary.proposed_approach}"
        ),
        confidence=primary.confidence,
        alternative_hypotheses=(
            list(primary.alternative_hypotheses)
            + list(secondary.alternative_hypotheses)
            + [secondary.hypothesis]
        ),
        prior_attempt_analysis=primary.prior_attempt_analysis or secondary.prior_attempt_analysis,
    )


def _build_fix_prompt(
    obs: Observation, rca: RootCauseAnalysis,
    observation_context: str, prior_context: str,
) -> str:
    """Build fix/implementation prompt — varies by observation category."""
    header = (
        f"## Fix: {obs.id}\n\n"
        f"### Root Cause Analysis\n\n"
        f"**Hypothesis:** {rca.hypothesis}\n"
        f"**Affected Files:** {', '.join(rca.affected_files)}\n"
        f"**Proposed Approach:** {rca.proposed_approach}\n\n"
        f"### Observation\n\n{obs.description}\n"
    )
    if obs.expected_behavior:
        header += f"**Expected Behavior:** {obs.expected_behavior}\n"
    if obs.decision:
        header += f"\n### User Decision (AUTHORITATIVE)\n{obs.decision}\n"

    instructions = {
        "bug": (
            "\n\nApply the minimal fix based on the RCA. "
            "Fix only what the root cause analysis identified. "
            "Report all files modified."
        ),
        "clarification": (
            "\n\nImplement the design change to match the user's decision. "
            "The user's decision overrides any conflicting spec."
        ),
        "requirement": (
            "\n\nImplement the missing functionality. Follow the scope analysis "
            "and extend existing components where possible."
        ),
        "missing_test": (
            "\n\nWrite the missing tests based on the gap analysis. "
            "Use the project's existing test framework and patterns."
        ),
    }

    return (
        header
        + instructions.get(obs.category, instructions["bug"])
        + f"\n\n{observation_context}" + prior_context
    )


def _build_test_prompt(
    obs: Observation, impl_result: ImplementationResult,
    observation_context: str, prior_context: str,
) -> str:
    """Build test-writing prompt — only for requirement and missing_test."""
    return (
        f"## Tests for: {obs.id}\n\n"
        f"### What was implemented\n{impl_result.summary}\n\n"
        f"### Files created/modified\n"
        + "\n".join(f"- `{f}`" for f in (impl_result.files_created + impl_result.files_modified))
        + f"\n\n### Expected behavior\n{obs.expected_behavior}\n\n"
        f"{observation_context}\n\n"
        "Write tests covering happy path and key error cases. "
        "Use the project's existing test framework and patterns."
        + prior_context
    )


def _build_verify_prompt(
    obs: Observation, impl_result: ImplementationResult,
    test_result: ImplementationResult | None, observation_context: str,
) -> str:
    """Build verification prompt — includes live testing instructions."""
    prompt = (
        f"## Verification: {obs.id}\n\n"
        f"### Observation\n{obs.description}\n"
    )
    if obs.expected_behavior:
        prompt += f"**Expected Behavior:** {obs.expected_behavior}\n"
    if obs.decision:
        prompt += f"**User Decision:** {obs.decision}\n"
    prompt += (
        f"\n### Fix Applied\n{impl_result.summary}\n\n"
        f"### Files Modified\n"
        + "\n".join(f"- `{f}`" for f in (impl_result.files_created + impl_result.files_modified))
    )
    if test_result:
        prompt += (
            f"\n\n### Tests Written\n{test_result.summary}\n"
            f"### Test Files\n"
            + "\n".join(f"- `{f}`" for f in (test_result.files_created + test_result.files_modified))
        )
    prompt += (
        f"\n\n{observation_context}\n\n"
        "Verify that the fix resolves the observation. "
        "Also check for regressions against other observations."
        + _LIVE_VERIFY_INSTRUCTIONS
    )
    return prompt


# ── Unified dispatch pipeline ──────────────────────────────────────────────


async def _dispatch_observation(
    runner: WorkflowRunner,
    feature: Feature,
    obs: Observation,
    observation_context: str,
    phase_name: str,
    workspace_root: Path | None = None,
    rca_runtime: str | None = None,
    implement_runtime: str | None = None,
    test_runtime: str | None = None,
    verify_runtime: str | None = None,
    actor_factory: Callable[..., Any] | None = None,
) -> dict:
    """Unified pipeline for any observation category.

    Follows the bugfix workflow pattern (diagnosis_fix.py:25-177):
    outer retry loop with HandoverDoc accumulation.
    """
    handover = HandoverDoc()
    feature_root = workspace_root or _get_feature_root(runner, feature)
    actor_builder = actor_factory or _make_parallel_actor
    ws_hint = (
        f"\n\n### Workspace\nFeature repos at: `{feature_root}`\n"
        if feature_root else ""
    )
    last_verdict: Verdict | None = None
    last_verdict_key = ""

    for iteration in range(MAX_FIX_ITERATIONS):
        logger.info("%s [%s]: iteration %d/%d", obs.id, obs.category, iteration + 1, MAX_FIX_ITERATIONS)

        prior_context = ""
        if handover.failed_attempts:
            handover.compress()
            prior_context = (
                f"\n\n## Prior Fix Attempts (DO NOT REPEAT)\n\n"
                f"{to_markdown(handover)}\n\n"
                "The above attempts did NOT resolve the issue. "
                "Consider what they missed."
            )
            prior_context = _offload_if_large(
                prior_context, feature_root, f"prior-attempts-{obs.id}",
            )

        # Inject the verifier's full rejection assessment from the previous
        # iteration so the fixer knows exactly what failed and why, not just
        # the one-liner in HandoverDoc.
        if iteration > 0:
            prior_verdict_raw = await runner.artifacts.get(
                f"obs-verdict:{obs.id}:iter-{iteration}", feature=feature,
            )
            if prior_verdict_raw:
                prior_context += (
                    f"\n\n## Prior Verification Verdict (iteration {iteration})\n"
                    f"The verifier rejected the previous fix attempt with this assessment:\n\n"
                    f"{prior_verdict_raw}\n\n"
                    f"Address the verifier's specific concerns in your next attempt.\n"
                )

        # 1. Parallel dual-RCA (symptoms lens + architecture lens)
        # Mirrors the bugfix workflow's parallel RCA pattern from
        # diagnosis_fix.py — two independent analytical perspectives
        # produce better root causes.
        rca_symptoms, rca_architecture = await runner.parallel([
            Ask(
                actor=actor_builder(
                    root_cause_analyst,
                    f"obs-rca-symptoms-{obs.id}",
                    runtime=rca_runtime,
                    workspace_path=str(feature_root) if feature_root else None,
                ),
                prompt=_build_rca_prompt(obs, observation_context, ws_hint, prior_context,
                                         lens="symptoms"),
                output_type=RootCauseAnalysis,
            ),
            Ask(
                actor=actor_builder(
                    root_cause_analyst,
                    f"obs-rca-architecture-{obs.id}",
                    runtime=rca_runtime,
                    workspace_path=str(feature_root) if feature_root else None,
                ),
                prompt=_build_rca_prompt(obs, observation_context, ws_hint, prior_context,
                                         lens="architecture"),
                output_type=RootCauseAnalysis,
            ),
        ], feature)
        rca = _merge_rca(rca_symptoms, rca_architecture)

        # 2. Fix/Implement (no workspace_path — inherits worktree_root
        # = full feature root for cross-repo access)
        impl_result: ImplementationResult = await runner.run(
            Ask(
                actor=actor_builder(
                    implementer,
                    f"obs-impl-{obs.id}",
                    runtime=implement_runtime,
                    workspace_path=str(feature_root) if feature_root else None,
                ),
                prompt=_build_fix_prompt(obs, rca, observation_context, prior_context),
                output_type=ImplementationResult,
            ),
            feature,
            phase_name=phase_name,
        )
        if workspace_root is None:
            await _commit_repos(runner, feature, f"fix: {obs.id} (iter {iteration + 1})")
        else:
            await _commit_repos_in_root(feature_root, f"fix: {obs.id} (iter {iteration + 1})")

        # 3. Write tests (requirement + missing_test categories only)
        test_result: ImplementationResult | None = None
        if obs.category in ("requirement", "missing_test"):
            test_result = await runner.run(
                Ask(
                    actor=actor_builder(
                        test_author,
                        f"obs-test-{obs.id}",
                        runtime=test_runtime,
                        workspace_path=str(feature_root) if feature_root else None,
                    ),
                    prompt=_build_test_prompt(obs, impl_result, observation_context, prior_context),
                    output_type=ImplementationResult,
                ),
                feature,
                phase_name=phase_name,
            )
            if workspace_root is None:
                await _commit_repos(runner, feature, f"test: {obs.id} (iter {iteration + 1})")
            else:
                await _commit_repos_in_root(feature_root, f"test: {obs.id} (iter {iteration + 1})")

        # 4. Verify
        re_verdict: Verdict = await runner.run(
            Ask(
                actor=actor_builder(
                    verifier,
                    f"obs-verify-{obs.id}",
                    runtime=verify_runtime,
                    workspace_path=str(feature_root) if feature_root else None,
                ),
                prompt=_build_verify_prompt(obs, impl_result, test_result, observation_context),
                output_type=Verdict,
            ),
            feature,
            phase_name=phase_name,
        )
        verdict_key = f"obs-verdict:{obs.id}:iter-{iteration + 1}"
        await runner.artifacts.put(
            verdict_key,
            to_str(re_verdict), feature=feature,
        )
        last_verdict = re_verdict
        last_verdict_key = verdict_key

        if re_verdict.approved:
            logger.info("%s verified on iteration %d", obs.id, iteration + 1)
            return {
                "observation": obs,
                "status": "FIXED",
                "summary": impl_result.summary,
                "verdict": re_verdict.model_dump(mode="json"),
                "verdict_key": verdict_key,
            }

        handover.record_failure(
            task_id=f"{obs.id}-iter-{iteration + 1}",
            summary=impl_result.summary,
            failure_reason=f"Verification failed: {re_verdict.summary}",
        )
        logger.warning("%s not resolved after iteration %d, looping", obs.id, iteration + 1)

    logger.error("%s not resolved after %d iterations", obs.id, MAX_FIX_ITERATIONS)
    return {
        "observation": obs,
        "status": "UNRESOLVED",
        "summary": f"Failed after {MAX_FIX_ITERATIONS} iterations",
        "verdict": last_verdict.model_dump(mode="json") if last_verdict else None,
        "verdict_key": last_verdict_key,
    }


# ── Phase ──────────────────────────────────────────────────────────────────


class PostTestObservationPhase(Phase):
    """Collect user post-test observations and dispatch category-specific fix pipelines."""

    name = "post-test-observation"

    async def execute(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BuildState,
    ) -> BuildState:
        # Ensure worktree_root is set — may be missing on resume since
        # ImplementationPhase (which normally sets it) was skipped.
        # Mirrors implementation.py:235 via _get_feature_root which only
        # needs workspace_manager (always set by orchestrator.py:658).
        feature_root = _get_feature_root(runner, feature)
        if feature_root:
            runner.services["worktree_root"] = feature_root

        # ── Restore state from prior cycles on resume ────────────
        # Pattern: implementation.py:408-418 restores prior_attempts
        # from artifacts on resume.
        cycle_raw = await runner.artifacts.get(
            "observation-cycle-counter", feature=feature,
        )
        cycle = int(cycle_raw) if cycle_raw and cycle_raw.strip().isdigit() else 0
        if cycle > 0:
            logger.info("Resuming from cycle %d (cycles 1-%d already complete)", cycle + 1, cycle)

        prior_fix_summary = ""
        if cycle > 0:
            stored = await runner.artifacts.get("observations", feature=feature)
            if stored:
                prior_fix_summary = stored

        all_decisions: list[str] = []
        decisions_raw = await runner.artifacts.get(
            "observation-decisions", feature=feature,
        )
        if decisions_raw:
            import json as _json
            try:
                all_decisions = _json.loads(decisions_raw)
            except Exception:
                logger.debug("Could not parse observation-decisions, starting fresh")

        while True:
            cycle += 1

            # ── Stage 1: Collection Interview ────────────────────────
            # Resume checkpoint: if the structured checkpoint artifact
            # exists (from a prior run that crashed during dispatch),
            # skip the interview and recover the report.  Mirrors the
            # implementation phase's dag-gate:* checkpoint pattern
            # (implementation.py:540-542).
            checkpoint_key = f"observations-checkpoint:{cycle}"
            checkpoint = await runner.artifacts.get(checkpoint_key, feature=feature)
            report = None
            if checkpoint:
                try:
                    report = ObservationReport.model_validate_json(checkpoint)
                    logger.info(
                        "Cycle %d: recovered %d observations from checkpoint",
                        cycle, len(report.observations),
                    )
                except Exception:
                    logger.debug("Checkpoint %s not parseable, re-interviewing", checkpoint_key)

            if report is None:
                report = await self._collect_observations(
                    runner, feature, state,
                    cycle=cycle,
                    prior_fix_summary=prior_fix_summary,
                )

            if not report or not report.observations:
                logger.info("No observations in cycle %d — phase complete", cycle)
                break

            logger.info(
                "Cycle %d: %d observations collected",
                cycle, len(report.observations),
            )

            # Persist observation report BEFORE dispatching so that a
            # bridge restart mid-dispatch doesn't lose what the user reported.
            report_text = _render_observation_report(report)
            report_json = report.model_dump_json(indent=2)
            await runner.artifacts.put(
                f"observations:cycle-{cycle}", report_text, feature=feature,
            )
            await runner.artifacts.put(
                f"observations-checkpoint:{cycle}", report_json, feature=feature,
            )
            state.observations = report_text
            await runner.artifacts.put("observations", report_text, feature=feature)

            # Extract clarification decisions
            cycle_decisions = [
                f"D-OBS-{obs.id}: {obs.title} — {obs.decision}"
                for obs in report.observations
                if obs.category == "clarification" and obs.decision
            ]
            all_decisions.extend(cycle_decisions)

            # Build observation context
            observation_context = _build_observation_context(
                report, cycle, all_decisions, prior_fix_summary,
                feature_root,
            )

            # Store decisions as artifacts
            for obs in report.observations:
                if obs.category == "clarification" and obs.decision:
                    await runner.artifacts.put(
                        f"D-OBS-{obs.id}",
                        f"# {obs.title}\n\n{obs.decision}",
                        feature=feature,
                    )

            # ── Stage 2: Dispatch with cycle-level re-dispatch ───────
            # Dispatch in priority order.  Re-dispatch any observations
            # that are not FIXED/UNRESOLVED/SKIPPED until all reach a
            # terminal status or cycle-level retries are exhausted.
            remaining = _sort_by_priority(list(report.observations))
            all_results: dict[str, dict] = {}

            for dispatch_round in range(MAX_FIX_ITERATIONS):
                if not remaining:
                    break

                logger.info(
                    "Cycle %d dispatch round %d: %d observations",
                    cycle, dispatch_round + 1, len(remaining),
                )

                for obs in remaining:
                    try:
                        result = await _dispatch_observation(
                            runner, feature, obs, observation_context, self.name,
                        )
                    except Exception as exc:
                        err_msg = str(exc).lower()
                        if "prompt too long" in err_msg or "input too long" in err_msg:
                            logger.error(
                                "Observation %s: prompt exceeds context window — BLOCKED",
                                obs.id,
                            )
                            result = {
                                "observation": obs,
                                "status": "BLOCKED",
                                "summary": f"Prompt too large for model context window: {exc}",
                            }
                        else:
                            logger.exception("Failed to dispatch %s", obs.id)
                            result = {
                                "observation": obs,
                                "status": "ERROR",
                                "summary": f"Dispatch failed: {exc}",
                            }
                    all_results[obs.id] = result

                # Check what's still unresolved — ERROR and BLOCKED are
                # terminal (re-dispatch would hit the same failure).
                remaining = _sort_by_priority([
                    r["observation"] for r in all_results.values()
                    if r["status"] not in ("FIXED", "UNRESOLVED", "SKIPPED", "ERROR", "BLOCKED")
                ])

                if remaining:
                    logger.info(
                        "Cycle %d round %d: %d still unresolved",
                        cycle, dispatch_round + 1, len(remaining),
                    )

            flat_results = list(all_results.values())

            # Build summary for next cycle and persist immediately so
            # rebuild_state can recover if the phase crashes mid-cycle.
            prior_fix_summary = _build_fix_summary(flat_results)
            state.observations = prior_fix_summary
            await runner.artifacts.put("observations", state.observations, feature=feature)

            # Clear the interview checkpoint now that dispatch is complete.
            await runner.artifacts.put(checkpoint_key, "", feature=feature)

            # Persist cycle counter + decisions so resume skips completed
            # cycles and restores clarification decisions.
            import json as _json
            await runner.artifacts.put(
                "observation-cycle-counter", str(cycle), feature=feature,
            )
            await runner.artifacts.put(
                "observation-decisions", _json.dumps(all_decisions), feature=feature,
            )

            # Append to cumulative history so the next cycle's interviewer
            # knows what was observed and fixed in ALL prior cycles.
            cycle_history = (
                f"## Cycle {cycle}\n\n"
                f"### Observations Reported\n{report_text}\n\n"
                f"### Fix Results\n{prior_fix_summary}\n\n"
            )
            existing_history = (
                await runner.artifacts.get("observation-history", feature=feature) or ""
            )
            await runner.artifacts.put(
                "observation-history",
                existing_history + cycle_history,
                feature=feature,
            )

            logger.info("Cycle %d complete: %s", cycle, prior_fix_summary[:200])

        return state

    async def _collect_observations(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BuildState,
        *,
        cycle: int,
        prior_fix_summary: str,
    ) -> ObservationReport | None:
        """Run Interview with observation_collector to gather observations."""
        feature_root = _get_feature_root(runner, feature)

        context_pointers = ""
        if feature_root:
            context_dir = feature_root / ".iriai-context" / "observation"
            context_dir.mkdir(parents=True, exist_ok=True)

            context_pointers = (
                "### Context\n"
                "You have Read, Glob, and Grep tools to explore the codebase. "
                "Use them to investigate each observation the user reports.\n"
            )

            if cycle > 1:
                history = await runner.artifacts.get(
                    "observation-history", feature=feature,
                )
                if history:
                    (context_dir / "prior-cycles.md").write_text(
                        history, encoding="utf-8",
                    )
                    context_pointers += (
                        "\n### Prior Cycles\n"
                        "Read `.iriai-context/observation/prior-cycles.md` for "
                        "ALL prior observation cycles and fix results. Check fix "
                        "statuses — ATTEMPTED/PARTIAL/UNRESOLVED means the issue "
                        "was NOT fully resolved.\n"
                    )

            context_pointers += "\n"

        if cycle == 1:
            initial_prompt = (
                f"## Post-Test Observation Review\n\n"
                f"Feature: {feature.name}\n\n"
                f"{context_pointers}"
                "I'll help you document what you found during testing. "
                "Tell me about the first thing you noticed — a bug, "
                "a missing feature, or something that should work differently.\n\n"
                "If everything looks good and there's nothing to report, "
                "just say 'done' or 'nothing to report'."
            )
        else:
            initial_prompt = (
                f"## Post-Test Observation Review — Cycle {cycle}\n\n"
                f"### Fixes Applied in Previous Cycle\n\n{prior_fix_summary}\n\n"
                f"{context_pointers}"
                "The prior-cycles.md file contains ALL observations and fixes "
                "from earlier cycles. Some prior fixes may NOT have fully "
                "resolved their issues — check the fix results for statuses "
                "like ATTEMPTED, PARTIAL, or UNRESOLVED.\n\n"
                "Report anything that still needs attention — whether it's a "
                "new issue or a prior issue that wasn't fully resolved.\n\n"
                "Say 'done' if everything looks good."
            )

        # Use plain Interview (not HostedInterview) so the agent populates
        # envelope.output with structured ObservationReport.
        # Pattern: bug_intake.py:26-40.
        envelope = await runner.run(
            Interview(
                questioner=observation_collector,
                responder=user,
                initial_prompt=initial_prompt,
                output_type=Envelope[ObservationReport],
                done=envelope_done,
            ),
            feature,
            phase_name=self.name,
        )

        if isinstance(envelope, Envelope) and envelope.output:
            return envelope.output
        return None
