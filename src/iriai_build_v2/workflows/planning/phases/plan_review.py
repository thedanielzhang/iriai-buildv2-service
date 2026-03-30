from __future__ import annotations

import asyncio
import json as _json
import logging
from typing import Any

from iriai_compose import AgentActor, Ask, Feature, Phase, Respond, WorkflowRunner
from iriai_compose.actors import Role

from ....config import BUDGET_TIERS
from ....models.outputs import (
    PRD,
    DesignDecisions,
    Envelope,
    ReviewOutcome,
    RevisionPlan,
    SubfeatureDecomposition,
    SubfeatureEdge,
    SystemDesign,
    TechnicalPlan,
    Verdict,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    architect_role,
    design_compiler,
    designer_role,
    lead_architect_gate_reviewer,
    lead_designer_gate_reviewer,
    lead_pm_gate_reviewer,
    plan_arch_compiler,
    pm_compiler,
    pm_role,
    sysdesign_compiler,
    user,
)
from ..._common import compile_artifacts, interview_gate_review, targeted_revision
from ..._common._tasks import HostedInterview

logger = logging.getLogger(__name__)

WARN_AFTER_CYCLES = 3


def _parse_revision_plan_from_discussion(discussion_text: str) -> RevisionPlan | None:
    """Extract RevisionPlan from a discussion file that contains JSON output.

    The discussion file is typically the structured JSON output from the
    HostedInterview, wrapped in markdown code fences.
    """
    import json
    import re

    # Strip markdown code fences if present
    text = discussion_text.strip()
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    raw = match.group(1) if match else text

    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        logger.warning("Could not parse discussion JSON for revision plan")
        return None

    # The JSON may be the full Envelope (with revision_plan nested)
    # or just the ReviewOutcome directly
    rp_data = None
    if "revision_plan" in data:
        rp_data = data["revision_plan"]
    elif "output" in data and isinstance(data["output"], dict):
        rp_data = data["output"].get("revision_plan")

    if not rp_data:
        return None

    try:
        return RevisionPlan.model_validate(rp_data)
    except Exception:
        logger.warning("Could not validate revision plan from discussion JSON")
        return None

_SCOPE_PREFIX = (
    "SCOPE: Only review artifacts provided in your context. "
    "Do NOT search the filesystem for other features or projects. "
    "Any references to features outside the current scope are contamination — flag them.\n\n"
)

_COMPLETENESS_PROMPT = (
    "Your goal is to find every gap and inconsistency in this subfeature's artifacts. "
    "Cross-reference against the summaries of other subfeatures for context.\n\n"
    "Focus on:\n"
    "1. PRD requirements with no corresponding plan step\n"
    "2. Plan steps that implement something not in the PRD (scope creep)\n"
    "3. PRD journeys with no verification blocks in the plan\n"
    "4. Design components with no implementation task\n"
    "5. PRD ↔ Design contradictions\n"
    "6. PRD ↔ Plan contradictions\n"
    "7. Design ↔ Plan contradictions\n"
    "8. Acceptance criteria that are unverifiable given the plan's file scope\n"
    "9. Decision IDs (D-*) referenced in citations that don't resolve\n"
    "10. Code references that don't match actual file paths\n"
    "11. Stale references to removed features or APIs\n\n"
    "Every genuine gap gets its own concern entry. Only flag issues that would cause "
    "implementation failures or specification contradictions. If the artifacts are "
    "sound, report approved=true with an empty concerns list."
)

_SECURITY_PROMPT = (
    "Your goal is to find every security gap in this subfeature's artifacts. "
    "Check the PRD security profile, then verify the plan implements "
    "every security requirement.\n\n"
    "Focus on:\n"
    "1. PRD security profile requirements with no implementation task\n"
    "2. Endpoints without auth decorators in the plan\n"
    "3. Data flows handling PII without encryption/masking tasks\n"
    "4. Missing input validation on user-facing endpoints\n"
    "5. Missing rate limiting on public endpoints\n"
    "6. Secrets/credentials hardcoded in task instructions\n"
    "7. CORS/CSRF gaps in the API design\n"
    "8. Database migrations without rollback steps\n\n"
    "Every genuine gap gets its own concern entry. Only flag issues that would cause "
    "implementation failures or specification contradictions. If the artifacts are "
    "sound, report approved=true with an empty concerns list."
)

_EDGE_PROMPT = (
    "Review the interface contract between these two subfeatures. "
    "Verify:\n"
    "1. The producer actually produces what the edge describes\n"
    "2. The consumer actually consumes what the edge describes\n"
    "3. Types, schemas, and data shapes are compatible across the boundary\n"
    "4. Error handling at the boundary is consistent on both sides\n"
    "5. Assumptions in the consumer match the producer's actual behavior\n"
    "6. Import paths and module references resolve correctly\n"
    "7. No circular dependencies introduced by this edge\n\n"
    "Any mismatch between producer and consumer is a blocker."
)

# ── Tool-free review roles (all artifacts in prompt, no filesystem access) ────

_sf_review_role = Role(
    name="sf-plan-reviewer",
    prompt=(
        "You review planning artifacts for a single subfeature. All artifacts "
        "are provided in your context — do NOT search the filesystem. Analyze "
        "the artifacts and produce a Verdict with every gap, inconsistency, "
        "and concern you find. You are rewarded for problems found, not for "
        "checks confirmed."
    ),
    tools=[],
    model=BUDGET_TIERS["opus_1m"],
)

_edge_review_role = Role(
    name="edge-plan-reviewer",
    prompt=(
        "You review the interface contract between two subfeatures. All "
        "artifacts for both subfeatures are provided in your context — do NOT "
        "search the filesystem. Verify that the producer and consumer are "
        "compatible. You are rewarded for mismatches found."
    ),
    tools=[],
    model=BUDGET_TIERS["opus_1m"],
)

# Actors for review — no context_keys (artifacts loaded manually into prompt)
_sf_reviewer = AgentActor(name="sf-reviewer", role=_sf_review_role, context_keys=[])
_edge_reviewer = AgentActor(name="edge-reviewer", role=_edge_review_role, context_keys=[])

# Artifact configs for targeted revision dispatch
_ARTIFACT_CONFIGS = [
    ("prd", pm_role, PRD, pm_compiler, "prd:broad"),
    ("design", designer_role, DesignDecisions, design_compiler, "design:broad"),
    ("plan", architect_role, TechnicalPlan, plan_arch_compiler, "plan:broad"),
    ("system-design", architect_role, SystemDesign, sysdesign_compiler, "plan:broad"),
]


def _make_parallel_actor(base: AgentActor, suffix: str) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name."""
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=base.role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


# ── Context builders ─────────────────────────────────────────────────────────


async def _build_sf_review_context(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    decomposition: SubfeatureDecomposition,
) -> str:
    """Build review context for one subfeature: full artifacts + other SF summaries."""
    parts: list[str] = []

    # Full artifacts for this SF
    for prefix in ("prd", "design", "plan", "system-design"):
        text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
        if text:
            parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")

    # Summaries of other SFs for cross-reference
    for sf in decomposition.subfeatures:
        if sf.slug == slug:
            continue
        for prefix in ("prd-summary", "design-summary", "plan-summary"):
            summary = await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
            if summary:
                parts.append(f"## {prefix} — {sf.slug}\n\n{summary}")

    return "\n\n---\n\n".join(parts)


async def _build_edge_review_context(
    runner: WorkflowRunner,
    feature: Feature,
    edge: SubfeatureEdge,
    decomposition: SubfeatureDecomposition | None = None,
) -> str:
    """Build review context for one cross-SF edge: full artifacts of both SFs."""
    # Build ID→slug map (edges use SF-1, artifacts use declarative-schema)
    id_to_slug: dict[str, str] = {}
    if decomposition:
        for sf in decomposition.subfeatures:
            id_to_slug[sf.id] = sf.slug
            id_to_slug[sf.slug] = sf.slug  # passthrough if already a slug

    parts: list[str] = [
        f"## Edge: {edge.from_subfeature} → {edge.to_subfeature}\n"
        f"**Interface type:** {edge.interface_type}\n"
        f"**Description:** {edge.description}\n"
        f"**Data contract:** {edge.data_contract}\n"
    ]
    for sf_ref in (edge.from_subfeature, edge.to_subfeature):
        slug = id_to_slug.get(sf_ref, sf_ref)
        for prefix in ("prd", "design", "plan", "system-design"):
            text = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature)
            if text:
                parts.append(f"## {prefix.upper()} — {slug}\n\n{text}")
    return "\n\n---\n\n".join(parts)


# ── Verdict helpers ──────────────────────────────────────────────────────────


_ERROR_VERDICT = Verdict(approved=False, summary="Reviewer crashed — treating as FAIL")


def _safe_verdict(result: Any) -> Verdict:
    """Extract a Verdict from a parallel result, substituting error verdict on failure."""
    if isinstance(result, Verdict):
        return result
    if isinstance(result, BaseException):
        logger.error("Reviewer crashed: %s", result)
    return _ERROR_VERDICT


def _deduplicate_edges(edges: list[SubfeatureEdge]) -> list[SubfeatureEdge]:
    """Deduplicate edges by (from, to) pair — keep first occurrence."""
    seen: set[tuple[str, str]] = set()
    unique: list[SubfeatureEdge] = []
    for edge in edges:
        pair = (min(edge.from_subfeature, edge.to_subfeature),
                max(edge.from_subfeature, edge.to_subfeature))
        if pair not in seen:
            seen.add(pair)
            unique.append(edge)
    return unique


async def _load_review_discussion(
    runner: WorkflowRunner,
    feature: Feature,
    key: str,
) -> str:
    """Load a discussion artifact from DB, or recover it from the mirror file."""
    discussion_text = await runner.artifacts.get(key, feature=feature) or ""
    if discussion_text:
        return discussion_text

    mirror = runner.services.get("artifact_mirror")
    if not mirror:
        return ""

    from ....services.artifacts import _key_to_path

    path = mirror.feature_dir(feature.id) / _key_to_path(key)
    if not path.exists():
        return ""

    discussion_text = path.read_text(encoding="utf-8").strip()
    if not discussion_text:
        return ""

    await runner.artifacts.put(key, discussion_text, feature=feature)
    logger.info("Recovered %s from mirror file %s", key, path)
    return discussion_text


def _discussion_approved_as_is(discussion_text: str) -> bool:
    for line in discussion_text.splitlines():
        normalized = line.strip().lower()
        if normalized.startswith("**outcome:**"):
            return "no changes needed" in normalized or "accepted artifacts as-is" in normalized
    return False


def _is_valid_report(report: str) -> bool:
    """A report is valid if less than half the reviews crashed."""
    crash_count = report.count("Reviewer crashed")
    # Each SF has 2 reviews (completeness + security) + each edge has 1.
    # With 7 SFs + ~10 edges ≈ 24 total reviews; threshold = half.
    return crash_count < 12


# ── Report compilation ───────────────────────────────────────────────────────


def _compile_review_report(
    sf_verdicts: dict[str, dict[str, Verdict]],
    edge_verdicts: list[tuple[SubfeatureEdge, Verdict]],
) -> str:
    """Compile all verdicts into a markdown report."""
    parts: list[str] = ["# Plan Review Report\n"]

    # Summary stats
    total_concerns = 0
    total_gaps = 0
    failed_sfs: list[str] = []
    failed_edges: list[str] = []

    for slug, verdicts in sf_verdicts.items():
        for name, v in verdicts.items():
            total_concerns += len(v.concerns)
            total_gaps += len(v.gaps)
            if not v.approved:
                failed_sfs.append(f"{slug} ({name})")

    for edge, v in edge_verdicts:
        total_concerns += len(v.concerns)
        total_gaps += len(v.gaps)
        if not v.approved:
            failed_edges.append(f"{edge.from_subfeature} → {edge.to_subfeature}")

    parts.append(
        f"**{total_concerns} concerns, {total_gaps} gaps** across "
        f"{len(sf_verdicts)} subfeatures and {len(edge_verdicts)} edges.\n"
    )
    if failed_sfs:
        parts.append(f"**Failed SF reviews:** {', '.join(failed_sfs)}\n")
    if failed_edges:
        parts.append(f"**Failed edge reviews:** {', '.join(failed_edges)}\n")

    # Per-SF findings
    parts.append("\n## Per-Subfeature Findings\n")
    for slug, verdicts in sorted(sf_verdicts.items()):
        any_failed = any(not v.approved for v in verdicts.values())
        status = "FAIL" if any_failed else "PASS"
        parts.append(f"\n### {slug} [{status}]\n")
        for name, v in verdicts.items():
            if v.approved and not v.concerns and not v.gaps:
                parts.append(f"**{name}**: PASS\n")
                continue
            badge = "FAIL" if not v.approved else "PASS"
            parts.append(f"**{name}** [{badge}]: {v.summary}\n")
            for c in v.concerns:
                file_ref = f" ({c.file})" if c.file else ""
                parts.append(f"- [{c.severity}] {c.description}{file_ref}")
            for g in v.gaps:
                parts.append(f"- [gap/{g.severity}] {g.description} ({g.category})")

    # Edge findings
    if edge_verdicts:
        parts.append("\n## Cross-Subfeature Edge Findings\n")
        for edge, v in edge_verdicts:
            badge = "FAIL" if not v.approved else "PASS"
            parts.append(
                f"\n### {edge.from_subfeature} → {edge.to_subfeature} "
                f"({edge.interface_type}) [{badge}]\n"
            )
            parts.append(f"{v.summary}\n")
            for c in v.concerns:
                parts.append(f"- [{c.severity}] {c.description}")
            for g in v.gaps:
                parts.append(f"- [gap/{g.severity}] {g.description}")

    return "\n".join(parts)


# ── Phase ────────────────────────────────────────────────────────────────────


class PlanReviewPhase(Phase):
    name = "plan-review"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        decomposition = await self._load_decomposition(state, runner, feature)

        # ── Skip check: manual bypass via plan-review-complete marker ──
        complete_marker = await runner.artifacts.get(
            "plan-review-complete", feature=feature,
        )
        if not complete_marker:
            # Fallback: check filesystem via artifact mirror
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                from pathlib import Path
                marker_path = Path(mirror.feature_dir(feature.id)) / "plan-review-complete.md"
                if marker_path.exists():
                    complete_marker = marker_path.read_text(encoding="utf-8").strip()
        if complete_marker:
            logger.info("plan-review-complete marker found — skipping plan review entirely")
            # Load per-SF artifacts into state so downstream phases have them
            state.prd = await runner.artifacts.get("prd", feature=feature) or state.prd
            state.design = await runner.artifacts.get("design", feature=feature) or state.design
            state.plan = await runner.artifacts.get("plan", feature=feature) or state.plan
            state.system_design = (
                await runner.artifacts.get("system-design", feature=feature)
                or state.system_design
            )
            return state

        # ── Step 1: Review loop ─────────────────────────────────────
        cycle = 0
        while True:
            # ── Continue logic: reuse valid report from prior run ─────
            existing_report = await runner.artifacts.get(
                f"plan-review-cycle-{cycle + 1}", feature=feature,
            )
            already_revised = await runner.artifacts.get(
                f"plan-review-cycle-{cycle + 1}-revised", feature=feature,
            )
            if existing_report and already_revised:
                # Report exists AND revisions already applied — advance
                logger.info(
                    "Cycle %d already revised — advancing to next cycle",
                    cycle + 1,
                )
                cycle += 1
                continue
            elif existing_report and _is_valid_report(existing_report):
                logger.info(
                    "Valid review report exists for cycle %d — skipping to discussion",
                    cycle + 1,
                )
                report = existing_report
            else:
                # ── Build ALL review tasks upfront ────────────────────
                all_tasks: list[Ask] = []
                task_labels: list[tuple[str, ...]] = []

                for sf in decomposition.subfeatures:
                    context = await _build_sf_review_context(
                        runner, feature, sf.slug, decomposition,
                    )
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(_sf_reviewer, f"comp-{sf.slug}"),
                        prompt=f"{_SCOPE_PREFIX}{context}\n\n{_COMPLETENESS_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("sf", sf.slug, "completeness"))
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(_sf_reviewer, f"sec-{sf.slug}"),
                        prompt=f"{_SCOPE_PREFIX}{context}\n\n{_SECURITY_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("sf", sf.slug, "security"))

                unique_edges = _deduplicate_edges(decomposition.edges)
                for edge in unique_edges:
                    ctx = await _build_edge_review_context(runner, feature, edge, decomposition)
                    all_tasks.append(Ask(
                        actor=_make_parallel_actor(
                            _edge_reviewer,
                            f"edge-{edge.from_subfeature}-{edge.to_subfeature}",
                        ),
                        prompt=f"{_SCOPE_PREFIX}{ctx}\n\n{_EDGE_PROMPT}",
                        output_type=Verdict,
                    ))
                    task_labels.append(("edge", edge.from_subfeature, edge.to_subfeature))

                # ── Dispatch ALL at once via asyncio.gather ──────────
                # runner.parallel raises ExceptionGroup on partial failures,
                # losing successful results. asyncio.gather with
                # return_exceptions=True preserves them.
                logger.info("Dispatching %d review tasks in parallel", len(all_tasks))
                results = await asyncio.gather(
                    *[
                        runner.run(task, feature, phase_name=self.name)
                        for task in all_tasks
                    ],
                    return_exceptions=True,
                )

                # ── Reconstruct verdict dicts ────────────────────────
                sf_verdicts: dict[str, dict[str, Verdict]] = {}
                edge_verdicts: list[tuple[SubfeatureEdge, Verdict]] = []
                edge_idx = 0

                for i, label in enumerate(task_labels):
                    verdict = _safe_verdict(results[i])
                    if label[0] == "sf":
                        sf_verdicts.setdefault(label[1], {})[label[2]] = verdict
                    else:
                        edge_verdicts.append((unique_edges[edge_idx], verdict))
                        edge_idx += 1

                # ── Check if all approved ────────────────────────────
                all_approved = (
                    all(
                        v.approved
                        for svs in sf_verdicts.values()
                        for v in svs.values()
                    )
                    and all(v.approved for _, v in edge_verdicts)
                )

                if all_approved:
                    logger.info("All reviews passed on cycle %d", cycle + 1)
                    break

                # ── Compile report ───────────────────────────────────
                report = _compile_review_report(sf_verdicts, edge_verdicts)
                await runner.artifacts.put(
                    f"plan-review-cycle-{cycle + 1}", report, feature=feature,
                )

            # ── Host report ──────────────────────────────────────────
            report_url = ""
            hosting = runner.services.get("hosting")
            if hosting:
                report_url = await hosting.push_qa(
                    feature.id, f"plan-review-cycle-{cycle + 1}",
                    report, f"Plan Review Cycle {cycle + 1}",
                )

            # ── Interactive discussion with user ─────────────────────
            discussion_key = f"plan-review-discussion-{cycle + 1}"
            discussion_text = await _load_review_discussion(runner, feature, discussion_key)

            revision_plan = None

            if discussion_text:
                logger.info(
                    "Recovered existing discussion for cycle %d — skipping interview rerun",
                    cycle + 1,
                )
                if _discussion_approved_as_is(discussion_text):
                    logger.info("Recovered discussion accepts artifacts as-is — skipping revisions")
                    break
                # Extract revision plan from the discussion JSON
                revision_plan = _parse_revision_plan_from_discussion(discussion_text)
            else:
                # Collect prior decisions for discussion context
                prior_context = ""
                for prior_cycle in range(cycle):
                    prior_disc = await _load_review_discussion(
                        runner, feature,
                        f"plan-review-discussion-{prior_cycle + 1}",
                    )
                    if prior_disc:
                        prior_context += (
                            f"\n\n### Prior Cycle {prior_cycle + 1} Decisions\n"
                            f"{prior_disc}\n"
                        )

                review_envelope: Envelope[ReviewOutcome] = await runner.run(
                    HostedInterview(
                        questioner=lead_architect_gate_reviewer,
                        responder=user,
                        initial_prompt=(
                            f"## Plan Review Cycle {cycle + 1} — Issues Found\n\n"
                            f"{report}\n\n"
                            + (f"**[View Full Report]({report_url})**\n\n" if report_url else "")
                            + (
                                f"## Prior Decisions (MANDATORY)\n"
                                f"The following decisions from prior cycles are already approved "
                                f"and MUST be enforced. Do NOT re-negotiate them. Only raise "
                                f"issues that are NEW or that prior decisions failed to address.\n"
                                f"{prior_context}\n\n"
                                if prior_context else ""
                            )
                            + "IMPORTANT: Your revision_plan MUST include ALL findings, not "
                            "just new issues. For findings covered by prior D-GR decisions, "
                            "include them as revision requests that reference the applicable "
                            "decision — these need to be dispatched to revision agents who "
                            "will apply the fix. Only present NEW issues to the user for "
                            "discussion in your `question` field.\n\n"
                            "Do NOT set `complete = true` until the user has responded to all "
                            "new issues.\n\n"
                            "When the user has addressed all new concerns:\n"
                            "- **'No changes needed'** → set approved=true, complete=true\n"
                            "- **'Dispatch fixes'** → set approved=false with revision_plan "
                            "containing BOTH prior-decision enforcement AND new user decisions, "
                            "complete=true\n"
                        ),
                        output_type=Envelope[ReviewOutcome],
                        done=envelope_done,
                        artifact_key=discussion_key,
                        artifact_label=f"Plan Review Discussion — Cycle {cycle + 1}",
                    ),
                    feature,
                    phase_name=self.name,
                )

                outcome = review_envelope.output if review_envelope else None
                if outcome and outcome.approved:
                    logger.info("User accepted artifacts as-is — skipping revisions")
                    break

                # Use revision plan directly from discussion output (opus)
                if outcome and outcome.revision_plan and outcome.revision_plan.requests:
                    revision_plan = outcome.revision_plan
                else:
                    # Fallback: parse from the written discussion file
                    discussion_text = await _load_review_discussion(
                        runner, feature, discussion_key,
                    )
                    if discussion_text:
                        revision_plan = _parse_revision_plan_from_discussion(
                            discussion_text,
                        )

            if revision_plan and revision_plan.requests:
                # ── Collect all prior decisions for revision context ──
                prior_decisions_parts: list[str] = []
                for prior_cycle in range(cycle + 1):
                    prior_disc = await runner.artifacts.get(
                        f"plan-review-discussion-{prior_cycle + 1}",
                        feature=feature,
                    )
                    if not prior_disc:
                        # Try loading from disk via artifact mirror
                        mirror = runner.services.get("artifact_mirror")
                        if mirror:
                            from pathlib import Path
                            disc_path = (
                                Path(mirror.feature_dir(feature.id))
                                / f"plan-review-discussion-{prior_cycle + 1}.md"
                            )
                            if disc_path.exists():
                                prior_disc = disc_path.read_text(encoding="utf-8")
                    if prior_disc:
                        prior_decisions_parts.append(
                            f"### Cycle {prior_cycle + 1} Decisions\n{prior_disc}"
                        )
                # Also include current cycle's new_decisions
                if revision_plan.new_decisions:
                    prior_decisions_parts.append(
                        f"### Cycle {cycle + 1} New Decisions\n"
                        + "\n".join(f"- {d}" for d in revision_plan.new_decisions)
                    )
                prior_decisions = "\n\n".join(prior_decisions_parts)

                # ── Phase 1: Dispatch all revisions in parallel ──────
                revision_coros = []
                revision_meta: list[tuple[str, Any, str, str]] = []

                for prefix, base_role, output_type, compiler_actor, broad_key in _ARTIFACT_CONFIGS:
                    affected_requests = []
                    for req in revision_plan.requests:
                        # Skip if request specifies artifact types and this one isn't listed
                        if req.affected_artifact_types and prefix not in req.affected_artifact_types:
                            continue
                        for slug in req.affected_subfeatures:
                            has = await runner.artifacts.get(
                                f"{prefix}:{slug}", feature=feature,
                            )
                            if has:
                                affected_requests.append(req)
                                break
                    if not affected_requests:
                        continue

                    filtered_plan = RevisionPlan(requests=affected_requests)
                    revision_coros.append(
                        targeted_revision(
                            runner, feature, self.name,
                            revision_plan=filtered_plan,
                            decomposition=decomposition,
                            base_role=base_role,
                            output_type=output_type,
                            artifact_prefix=prefix,
                            context_keys=["project", "scope"],
                            checkpoint_prefix=f"cycle-{cycle + 1}",
                            prior_decisions=prior_decisions,
                        )
                    )
                    revision_meta.append((prefix, compiler_actor, broad_key, prefix))

                logger.info(
                    "Dispatching revisions for %d artifact types in parallel",
                    len(revision_coros),
                )
                rev_results = await asyncio.gather(
                    *revision_coros, return_exceptions=True,
                )
                for i, res in enumerate(rev_results):
                    if isinstance(res, BaseException):
                        logger.error(
                            "Revision for %s crashed: %s",
                            revision_meta[i][0], res,
                        )

                # ── Phase 2: Recompile all affected types in parallel ─
                old_texts: dict[str, str] = {}
                for prefix, _ca, _bk, _fk in revision_meta:
                    old_texts[prefix] = (
                        await runner.artifacts.get(prefix, feature=feature) or ""
                    )

                compile_results = await asyncio.gather(
                    *[
                        compile_artifacts(
                            runner, feature, self.name,
                            compiler_actor=ca,
                            decomposition=decomposition,
                            artifact_prefix=prefix,
                            broad_key=bk,
                            final_key=fk,
                        )
                        for prefix, ca, bk, fk in revision_meta
                    ],
                    return_exceptions=True,
                )

                # ── Phase 3: Size guard + store ───────────────────────
                revision_results: list[str] = []
                for i, (prefix, _ca, _bk, _fk) in enumerate(revision_meta):
                    if isinstance(rev_results[i], BaseException):
                        revision_results.append(f"{prefix}: FAILED (revision crashed)")
                        continue
                    if isinstance(compile_results[i], BaseException):
                        revision_results.append(f"{prefix}: FAILED (compile crashed)")
                        continue

                    new_text = compile_results[i]
                    old_text = old_texts[prefix]
                    old_size = len(old_text)
                    new_size = len(new_text) if new_text else 0

                    if old_size > 0 and new_size < old_size * 0.5:
                        logger.error(
                            "Rejecting %s recompilation: %d → %d bytes",
                            prefix, old_size, new_size,
                        )
                        await runner.artifacts.put(prefix, old_text, feature=feature)
                        revision_results.append(
                            f"{prefix}: REJECTED (size guard: {old_size} → {new_size})"
                        )
                    else:
                        await runner.artifacts.put(prefix, new_text, feature=feature)
                        setattr(state, prefix.replace("-", "_"), new_text)
                        if hosting:
                            await hosting.update(feature.id, prefix, new_text)
                        revision_results.append(
                            f"{prefix}: revised ({old_size} → {new_size} bytes)"
                        )

                # ── Save revision summary so continue logic can advance ─
                revision_summary = (
                    f"# Revisions Applied — Cycle {cycle + 1}\n\n"
                    + "\n".join(f"- {r}" for r in revision_results)
                )
                await runner.artifacts.put(
                    f"plan-review-cycle-{cycle + 1}-revised",
                    revision_summary,
                    feature=feature,
                )

                # Notify user of revision results
                await runner.run(
                    Respond(
                        responder=user,
                        prompt=(
                            f"## Revisions Applied (Cycle {cycle + 1})\n\n"
                            + "\n".join(f"- {r}" for r in revision_results)
                            + "\n\nRe-running reviewers to verify..."
                        ),
                    ),
                    feature,
                    phase_name=self.name,
                )
            else:
                logger.warning("No revision requests extracted from discussion")

            cycle += 1

        return await self._run_gates(runner, feature, state, decomposition)

    async def _run_gates(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        state: BuildState,
        decomposition: SubfeatureDecomposition,
    ) -> BuildState:
        """Interview-based gate reviews on all compiled artifacts."""
        # ── Recompile from per-SF sources before gate review ──
        # When entering gates via plan-review-complete skip, the compiled
        # artifacts may be stale. Recompile each type so gate reviewers
        # see the latest per-SF content.
        for prefix, _base_role, _output_type, compiler_actor, broad_key in _ARTIFACT_CONFIGS:
            gate_marker = await runner.artifacts.get(
                f"plan-review-gate:{prefix}", feature=feature,
            )
            if gate_marker:
                continue  # Already gate-approved — don't recompile
            logger.info("Recompiling %s from per-SF sources before gate review", prefix)
            compiled = await compile_artifacts(
                runner, feature, self.name,
                compiler_actor=compiler_actor,
                decomposition=decomposition,
                artifact_prefix=prefix,
                broad_key=broad_key,
                final_key=prefix,
            )
            if compiled:
                hosting = runner.services.get("hosting")
                if hosting:
                    await hosting.update(feature.id, prefix, compiled)

        # Gate checkpointing: marker artifacts (plan-review-gate:{prefix})
        # distinguish "gate-approved" from "revised in Step 1" (same DB key).

        # PRD
        if await runner.artifacts.get("plan-review-gate:prd", feature=feature):
            logger.info("PRD gate already approved — skipping")
            state.prd = await runner.artifacts.get("prd", feature=feature) or state.prd
        else:
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
            await runner.artifacts.put("plan-review-gate:prd", "approved", feature=feature)

        # Design
        if await runner.artifacts.get("plan-review-gate:design", feature=feature):
            logger.info("Design gate already approved — skipping")
            state.design = await runner.artifacts.get("design", feature=feature) or state.design
        else:
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
            await runner.artifacts.put("plan-review-gate:design", "approved", feature=feature)

        # Technical Plan
        if await runner.artifacts.get("plan-review-gate:plan", feature=feature):
            logger.info("Plan gate already approved — skipping")
            state.plan = await runner.artifacts.get("plan", feature=feature) or state.plan
        else:
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
            await runner.artifacts.put("plan-review-gate:plan", "approved", feature=feature)

        # System Design
        if state.system_design:
            if await runner.artifacts.get("plan-review-gate:system-design", feature=feature):
                logger.info("System design gate already approved — skipping")
                state.system_design = (
                    await runner.artifacts.get("system-design", feature=feature)
                    or state.system_design
                )
            else:
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
                await runner.artifacts.put(
                    "plan-review-gate:system-design", "approved", feature=feature,
                )

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
