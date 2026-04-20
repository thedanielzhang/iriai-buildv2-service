from __future__ import annotations

import asyncio
import json as _json
import logging
import re
from typing import Any

from iriai_compose import AgentActor, Ask, Feature, Phase, WorkflowRunner
from iriai_compose.actors import Role

from ....models.outputs import (
    ImplementationDAG,
    ImplementationTask,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    TestPlan,
    WorkstreamDecomposition,
)
from ....models.state import BuildState
from ....roles import (
    InterviewActor,
    dag_compiler,
    planning_lead_role,
)
from ..._common import (
    compile_artifacts,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
)
from ..._common._helpers import targeted_revision

logger = logging.getLogger(__name__)


# Matches AC-id tokens like "AC-1", "AC-auth-flow-3". Accepts alphanumeric
# slug segments and optional trailing numeric suffix. Non-greedy on the slug
# to avoid over-matching into adjacent prose.
_AC_ID_PATTERN = re.compile(r"\bAC-[A-Za-z0-9][A-Za-z0-9-]*\b")

# Matches a top-level "## Acceptance Criteria" (or "## Acceptance Criteria ...")
# heading. Used to scope AC-id extraction to the section where criteria are
# *defined* — the test plan's other sections (test_scenarios,
# verification_checklist, edge_cases) frequently cite AC-ids in prose,
# which would otherwise cause false negatives in coverage checks when a
# typo AC-id happens to appear in narrative.
_AC_SECTION_HEADING = re.compile(r"(?m)^##\s+Acceptance Criteria\b.*$")
_NEXT_H2_HEADING = re.compile(r"(?m)^##\s+\S")


def _extract_ac_ids(test_plan_text: str) -> set[str]:
    """Extract AC-id tokens from a test-plan artifact (markdown or JSON).

    Tries structured parse first. Falls back to regex scoped to the
    ``## Acceptance Criteria`` section when present, or the whole document
    when no such section is found. Returns AC-ids actually *defined* by the
    test plan — used to validate task ``verification_gates`` against real
    IDs and catch agent typos.
    """
    if not test_plan_text:
        return set()
    # Try structured parse (the agent may have written JSON).
    try:
        data = _json.loads(test_plan_text)
        tp = TestPlan.model_validate(data)
        return {ac.id for ac in tp.acceptance_criteria if ac.id}
    except Exception:
        pass
    # Scope the regex to the Acceptance Criteria section when we can find
    # it — AC-ids cited in scenarios / checklists / prose are references,
    # not definitions. If the heading is absent (agent wrote free-form
    # markdown without sections), fall back to whole-document regex.
    section_start = _AC_SECTION_HEADING.search(test_plan_text)
    if section_start is None:
        return set(_AC_ID_PATTERN.findall(test_plan_text))
    section_body_start = section_start.end()
    next_heading = _NEXT_H2_HEADING.search(test_plan_text, section_body_start)
    section_body_end = next_heading.start() if next_heading else len(test_plan_text)
    section_text = test_plan_text[section_body_start:section_body_end]
    return set(_AC_ID_PATTERN.findall(section_text))


async def _validate_verification_gates_coverage(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    sf_tasks: list[ImplementationTask],
) -> None:
    """Post-decomposition lint: cross-check ``verification_gates`` against
    real AC-ids in the subfeature's test plan. Logs warnings for typos
    (gates referencing unknown AC-ids) and coverage gaps (AC-ids the test
    plan defines but no task cites). Non-fatal — this is diagnostic.
    """
    tp_text = await runner.artifacts.get(f"test-plan:{slug}", feature=feature)
    if not tp_text:
        return  # No test plan for this SF — nothing to validate against.
    real_ac_ids = _extract_ac_ids(tp_text)
    if not real_ac_ids:
        logger.warning(
            "Test plan for %s has no extractable AC-ids; verification_gates cannot be validated",
            slug,
        )
        return
    cited_ac_ids: set[str] = set()
    for task in sf_tasks:
        for gate in task.verification_gates:
            if not gate:
                continue
            cited_ac_ids.add(gate)
            if gate not in real_ac_ids:
                logger.warning(
                    "Task %s (%s) cites verification_gate %r not found in test-plan:%s — "
                    "possible typo or stale reference",
                    task.id, slug, gate, slug,
                )
    uncovered = real_ac_ids - cited_ac_ids
    if uncovered:
        logger.warning(
            "Test plan for %s defines %d AC-ids not cited by any task's verification_gates: %s",
            slug, len(uncovered), sorted(uncovered),
        )

# ── Actors ──────────────────────────────────────────────────────────────────

_workstream_planner = AgentActor(
    name="workstream-planner",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)

_sf_task_planner_gate_reviewer = InterviewActor(
    name="sf-task-planner-gate-reviewer",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)

_sf_task_planner_reviewer = InterviewActor(
    name="sf-task-planner-reviewer",
    role=planning_lead_role,
    context_keys=["project", "scope", "decomposition"],
)


# ── Phase ────────────────────────────────────────────────────────────────────


class TaskPlanningPhase(Phase):
    name = "task-planning"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        approved_dag = await runner.artifacts.get("dag", feature=feature)
        if approved_dag:
            logger.info("Gate-approved DAG exists — skipping")
            state.dag = approved_dag
            return state

        compiled_dag = await get_existing_artifact(runner, feature, "dag")
        if compiled_dag:
            logger.info("Compiled DAG exists but not gate-reviewed — running gate review")
            decomposition = await self._load_decomposition(runner, feature, state)
            final_text = await interview_gate_review(
                runner, feature, self.name,
                lead_actor=_sf_task_planner_gate_reviewer,
                decomposition=decomposition,
                artifact_prefix="dag",
                compiled_key="dag",
                base_role=planning_lead_role,
                output_type=ImplementationDAG,
                compiler_actor=dag_compiler,
                broad_key="dag:strategy",
                context_keys=["project", "scope", "decomposition"],
            )
            # DB write now happens inside interview_gate_review() on approval.
            state.dag = final_text
            return state

        decomposition = await self._load_decomposition(runner, feature, state)

        # ── Step 2: Workstream Decomposition (one-shot Ask) ──
        ws_decomp = await self._get_or_create_workstreams(
            runner, feature, decomposition,
        )

        # ── Step 3: Parallel Workstream Task Decomposition ──
        sf_upstream = await self._load_sf_upstream(runner, feature, decomposition)

        for round_ids in ws_decomp.execution_order:
            round_workstreams = [
                ws for ws in ws_decomp.workstreams if ws.id in round_ids
            ]
            logger.info(
                "Dispatching %d workstreams in parallel (round: %s)",
                len(round_workstreams),
                round_ids,
            )
            results = await asyncio.gather(
                *[
                    self._decompose_workstream(
                        runner, feature, decomposition, ws, sf_upstream,
                    )
                    for ws in round_workstreams
                ],
                return_exceptions=True,
            )
            for i, res in enumerate(results):
                if isinstance(res, BaseException):
                    logger.error(
                        "Workstream %s decomposition crashed: %s",
                        round_workstreams[i].id, res,
                    )

        # ── Step 4: DAG Integration Review ──
        ordered_decomp = self._order_decomposition(decomposition, ws_decomp)
        review = await integration_review(
            runner, feature, self.name,
            lead_actor=_sf_task_planner_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            review_key_suffix="dag",
        )

        if review.needs_revision:
            if not review.revision_instructions:
                logger.error(
                    "TaskPlanningPhase: integration review needs_revision=True but "
                    "revision_instructions is empty — skipping revision"
                )
            else:
                logger.info(
                    "DAG integration review needs revision — dispatching %d patches",
                    len(review.revision_instructions),
                )
                plan = RevisionPlan(requests=[
                    RevisionRequest(
                        description=instruction,
                        reasoning="DAG integration review finding",
                        affected_subfeatures=[sf_slug],
                    )
                    for sf_slug, instruction in review.revision_instructions.items()
                ])
                await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=ordered_decomp,
                    base_role=planning_lead_role,
                    output_type=ImplementationDAG,
                    artifact_prefix="dag",
                    context_keys=["project", "scope"],
                )

        # ── Step 5: DAG Compilation ──
        dag_text = await compile_artifacts(
            runner, feature, self.name,
            compiler_actor=dag_compiler,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            broad_key="dag:strategy",
            final_key="dag",
        )

        # ── Step 6: Interview-Based Gate Review ──
        final_text = await interview_gate_review(
            runner, feature, self.name,
            lead_actor=_sf_task_planner_gate_reviewer,
            decomposition=ordered_decomp,
            artifact_prefix="dag",
            compiled_key="dag",
            base_role=planning_lead_role,
            output_type=ImplementationDAG,
            compiler_actor=dag_compiler,
            broad_key="dag:strategy",
            context_keys=["project", "scope", "decomposition"],
        )

        # DB write now happens inside interview_gate_review() on approval.
        state.dag = final_text
        return state

    # ── Step 2 helper ────────────────────────────────────────────────────

    async def _get_or_create_workstreams(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
    ) -> WorkstreamDecomposition:
        """Load existing workstream decomposition or create via one-shot Ask."""
        ws_text = await get_existing_artifact(runner, feature, "dag:strategy")
        if ws_text:
            try:
                ws = WorkstreamDecomposition.model_validate(_json.loads(ws_text))
                logger.info("Loaded existing workstream decomposition")
                return ws
            except Exception:
                logger.warning("Failed to parse existing workstream decomposition — regenerating")

        plan_text = await runner.artifacts.get("plan", feature=feature) or ""
        decisions_text = await runner.artifacts.get("decisions", feature=feature) or ""
        prd_summaries = await self._load_prd_summaries(runner, feature, decomposition)
        decomp_json = _json.dumps(
            [{"id": sf.id, "slug": sf.slug, "name": sf.name, "description": sf.description}
             for sf in decomposition.subfeatures],
            indent=2,
        )

        ws_decomp: WorkstreamDecomposition = await runner.run(
            Ask(
                actor=_workstream_planner,
                prompt=(
                    "Decompose the following feature into parallel workstreams.\n\n"
                    "Each workstream is a group of subfeatures that can be planned "
                    "together because they share domain context or have tight dependencies.\n\n"
                    "Produce execution rounds — workstreams in the same round "
                    "can run in parallel. A workstream enters a round only when "
                    "all its depends_on workstreams are in earlier rounds.\n\n"
                    "Be aggressive about parallelization — only serialize workstreams "
                    "that have true data dependencies.\n\n"
                    f"## Technical Plan\n\n{plan_text}\n\n"
                    f"## Decision Ledger\n\n{decisions_text}\n\n"
                    f"## PRD Summaries\n\n{prd_summaries}\n\n"
                    f"## Subfeature Decomposition\n\n{decomp_json}"
                ),
                output_type=WorkstreamDecomposition,
            ),
            feature,
            phase_name=self.name,
        )
        await runner.artifacts.put(
            "dag:strategy",
            ws_decomp.model_dump_json(indent=2),
            feature=feature,
        )
        # Mirror to disk
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            from ....services.artifacts import _key_to_path
            from pathlib import Path

            path = Path(mirror.feature_dir(feature.id)) / _key_to_path("dag:strategy")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(ws_decomp.model_dump_json(indent=2), encoding="utf-8")

        logger.info(
            "Workstream decomposition: %d workstreams, %d rounds",
            len(ws_decomp.workstreams),
            len(ws_decomp.execution_order),
        )
        return ws_decomp

    # ── Step 3 helper ────────────────────────────────────────────────────

    async def _decompose_workstream(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,  # Workstream model instance
        sf_upstream: dict[str, dict[str, str]],
    ) -> None:
        """Decompose one workstream's subfeatures into tasks via a single Ask."""
        from ....services.artifacts import _key_to_path
        from pathlib import Path

        # Resume check: all SFs in this workstream already have dag artifacts?
        pending_slugs = []
        for slug in workstream.subfeature_slugs:
            existing = await runner.artifacts.get(f"dag:{slug}", feature=feature)
            if not existing:
                pending_slugs.append(slug)
        if not pending_slugs:
            logger.info("Workstream %s: all SFs already decomposed — skipping", workstream.id)
            return

        # Build focused context: full plans + summaries of other artifacts
        context_parts: list[str] = []
        decisions_text = await runner.artifacts.get("decisions", feature=feature) or ""
        if decisions_text:
            context_parts.append(f"## Decision Ledger\n\n{decisions_text}")
        for slug in workstream.subfeature_slugs:
            sf_arts = sf_upstream.get(slug, {})
            # Full plan (primary source for task decomposition)
            plan = sf_arts.get("plan", "")
            if plan:
                context_parts.append(f"## Plan: {slug}\n\n{plan}")
            # Other artifacts: include full upstream context so task
            # decomposition never loses detail from large specs.
            for prefix in ("prd", "design", "system-design", "test-plan"):
                text = sf_arts.get(prefix, "")
                if not text:
                    continue
                context_parts.append(f"## {prefix.upper()}: {slug}\n\n{text}")
            sf_decisions = await runner.artifacts.get(f"decisions:{slug}", feature=feature) or ""
            if sf_decisions:
                context_parts.append(f"## DECISIONS: {slug}\n\n{sf_decisions}")

        ws_context = "\n\n---\n\n".join(context_parts)

        actor = AgentActor(
            name=f"dag-ws-{workstream.id}",
            role=planning_lead_role,
            context_keys=["project", "scope"],
        )

        logger.info(
            "Decomposing workstream %s (%d SFs, %d pending)",
            workstream.id,
            len(workstream.subfeature_slugs),
            len(pending_slugs),
        )

        dag: ImplementationDAG = await runner.run(
            Ask(
                actor=actor,
                prompt=(
                    f"You are decomposing workstream '{workstream.name}' into "
                    f"implementation tasks.\n\n"
                    f"Subfeatures in this workstream: {workstream.subfeature_slugs}\n\n"
                    "Break each subfeature's technical plan into parallelizable "
                    "implementation tasks. Each task needs:\n"
                    "- file_scope (path + create/modify/read_only)\n"
                    "- requirement_ids (REQ-* from PRD)\n"
                    "- step_ids (STEP-* from plan)\n"
                    "- acceptance_criteria\n"
                    "- counterexamples\n"
                    "- reference_material (self-contained excerpts from upstream artifacts — "
                    "include `test-plan:{slug}#AC-id` citations when the task maps to specific "
                    "acceptance criteria from the TEST-PLAN section)\n"
                    "- subfeature_id (set to the subfeature SLUG for every task, "
                    "e.g., 'declarative-schema', 'dag-loader-runner', NOT 'SF-1' or 'SF-2')\n\n"
                    "When a TEST-PLAN section is present for a subfeature, use its acceptance "
                    "criteria as the source of truth for task-level verification_gates and "
                    "acceptance_criteria. Cite AC-ids directly so downstream implementers and "
                    "gates can march through them mechanically.\n\n"
                    "Be aggressive about parallelization. Only create dependencies "
                    "when a task truly cannot start until another completes.\n\n"
                    f"{ws_context}"
                ),
                output_type=ImplementationDAG,
            ),
            feature,
            phase_name=self.name,
        )

        # Store per-SF artifacts (split by subfeature_id)
        sf_id_to_slug = {sf.id: sf.slug for sf in decomposition.subfeatures}
        sf_slug_set = set(workstream.subfeature_slugs)

        for slug in workstream.subfeature_slugs:
            # Match tasks by subfeature_id — try multiple variants since
            # agents may use SF ID, slug, name, or abbreviations
            sf = next((s for s in decomposition.subfeatures if s.slug == slug), None)
            sf_ids = {slug, slug.lower()}
            if sf:
                sf_ids.add(sf.id)
                sf_ids.add(sf.id.lower())
                sf_ids.add(sf.name)
                sf_ids.add(sf.name.lower())
                # Also match partial: "SF-1", "sf1", "sf-1"
                sf_ids.add(sf.id.replace("-", ""))

            sf_tasks = [t for t in dag.tasks if t.subfeature_id in sf_ids]

            # Fallback: fuzzy match — agent may have used "SF-1: Declarative Schema" format
            if not sf_tasks:
                sf_tasks = [
                    t for t in dag.tasks
                    if t.subfeature_id and (
                        slug in t.subfeature_id.lower()
                        or (sf and sf.id.lower() in t.subfeature_id.lower())
                    )
                ]

            if not sf_tasks:
                logger.warning(
                    "Workstream %s: no tasks with subfeature_id matching %s "
                    "(tried: %s). Agent subfeature_ids: %s",
                    workstream.id, slug, sf_ids,
                    {t.subfeature_id for t in dag.tasks},
                )
                continue

            sf_task_ids = {t.id for t in sf_tasks}
            sf_dag = ImplementationDAG(
                tasks=sf_tasks,
                execution_order=[
                    [tid for tid in round_ids if tid in sf_task_ids]
                    for round_ids in dag.execution_order
                ],
                requirement_coverage={
                    k: [tid for tid in v if tid in sf_task_ids]
                    for k, v in dag.requirement_coverage.items()
                    if any(tid in sf_task_ids for tid in v)
                },
                complete=True,
            )
            sf_dag_json = sf_dag.model_dump_json(indent=2)

            await runner.artifacts.put(f"dag:{slug}", sf_dag_json, feature=feature)

            mirror = runner.services.get("artifact_mirror")
            if mirror:
                path = Path(mirror.feature_dir(feature.id)) / _key_to_path(f"dag:{slug}")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(sf_dag_json, encoding="utf-8")

            # Post-decomposition lint: verify ``verification_gates`` reference
            # real AC-ids. Non-fatal — logs warnings so agent typos don't
            # silently propagate to impl-phase gates.
            await _validate_verification_gates_coverage(
                runner, feature, slug, sf_tasks,
            )

            logger.info(
                "Workstream %s: stored %d tasks for %s",
                workstream.id, len(sf_tasks), slug,
            )

    # ── Shared helpers ───────────────────────────────────────────────────

    @staticmethod
    def _order_decomposition(
        decomposition: SubfeatureDecomposition,
        ws_decomp: WorkstreamDecomposition,
    ) -> SubfeatureDecomposition:
        """Reorder decomposition subfeatures based on workstream execution order."""
        ordered_slugs: list[str] = []
        for round_ids in ws_decomp.execution_order:
            for ws in ws_decomp.workstreams:
                if ws.id in round_ids:
                    ordered_slugs.extend(ws.subfeature_slugs)

        sf_by_slug = {sf.slug: sf for sf in decomposition.subfeatures}
        ordered_sfs = [sf_by_slug[slug] for slug in ordered_slugs if slug in sf_by_slug]
        remaining = [sf for sf in decomposition.subfeatures if sf.slug not in set(ordered_slugs)]

        return SubfeatureDecomposition(
            subfeatures=ordered_sfs + remaining,
            edges=decomposition.edges,
            decomposition_rationale=decomposition.decomposition_rationale,
            complete=decomposition.complete,
        )

    @staticmethod
    async def _load_decomposition(
        runner: WorkflowRunner, feature: Feature, state: BuildState
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

    @staticmethod
    async def _load_sf_upstream(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> dict[str, dict[str, str]]:
        """Preload per-SF upstream artifacts (prd, design, plan, system-design, test-plan).

        ``test-plan`` is optional — features planned before the test_planning
        step existed will return empty text; the ``if text:`` guard below
        drops missing artifacts silently.
        """
        result: dict[str, dict[str, str]] = {}
        for sf in decomposition.subfeatures:
            sf_artifacts: dict[str, str] = {}
            for prefix in ("prd", "design", "plan", "system-design", "test-plan"):
                text = await runner.artifacts.get(f"{prefix}:{sf.slug}", feature=feature)
                if text:
                    sf_artifacts[prefix] = text
            result[sf.slug] = sf_artifacts
        return result

    @staticmethod
    async def _load_prd_summaries(
        runner: WorkflowRunner, feature: Feature, decomposition: SubfeatureDecomposition,
    ) -> str:
        """Load PRD summaries for all subfeatures."""
        parts: list[str] = []
        for sf in decomposition.subfeatures:
            summary = await runner.artifacts.get(f"prd-summary:{sf.slug}", feature=feature)
            if summary:
                parts.append(f"### {sf.name} ({sf.slug})\n\n{summary}")
        return "\n\n".join(parts)
