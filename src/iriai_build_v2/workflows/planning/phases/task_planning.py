from __future__ import annotations

import asyncio
import hashlib
import json as _json
import logging
import re
from dataclasses import dataclass, field
from typing import Any
from pathlib import Path

from pydantic import BaseModel, Field as PydanticField
from iriai_compose import AgentActor, Ask, Feature, Phase, WorkflowRunner
from iriai_compose.actors import Role

from ....models.outputs import (
    DesignDecisions,
    DecisionLedger,
    DecisionRecord,
    ImplementationDAG,
    ImplementationTask,
    PRD,
    RevisionPlan,
    RevisionRequest,
    SubfeatureDecomposition,
    SystemDesign,
    TechnicalPlan,
    TestPlan,
    WorkstreamDecomposition,
)
from ....models.state import BuildState
from ....services.markdown import to_markdown
from ....roles import (
    InterviewActor,
    dag_compiler,
    planning_lead_role,
)
from .._decisions import GLOBAL_DECISIONS_KEY, _decision_sort_key, parse_decision_ledger
from ..._common import (
    compile_artifacts,
    get_existing_artifact,
    integration_review,
    interview_gate_review,
    Notify,
)
from ..._common._autonomy import autonomous_remainder_enabled
from ..._common._helpers import (
    _clear_agent_session,
    _is_model_boundary_failure,
    ContextPackage,
    ContextPackageItem,
    build_context_package,
    targeted_revision,
)

logger = logging.getLogger(__name__)


# Matches AC-id tokens like "AC-1", "AC-auth-flow-3". Accepts alphanumeric
# slug segments and optional trailing numeric suffix. Non-greedy on the slug
# to avoid over-matching into adjacent prose.
_AC_ID_PATTERN = re.compile(r"\bAC-[A-Za-z0-9][A-Za-z0-9-]*\b")
_AC_DEFINITION_PATTERN = re.compile(
    r"(?m)^\s*(?:[-*]|\d+[.)])\s*(?:\[[ xX]\]\s*)?(?:\*\*)?"
    r"(AC-[A-Za-z0-9][A-Za-z0-9-]*)(?:\*\*)?\b"
)

# Matches a top-level "## Acceptance Criteria" (or "## Acceptance Criteria ...")
# heading. Used to scope AC-id extraction to the section where criteria are
# *defined* — the test plan's other sections (test_scenarios,
# verification_checklist, edge_cases) frequently cite AC-ids in prose,
# which would otherwise cause false negatives in coverage checks when a
# typo AC-id happens to appear in narrative.
_AC_SECTION_HEADING = re.compile(r"(?m)^##\s+Acceptance Criteria\b.*$")
_NEXT_H2_HEADING = re.compile(r"(?m)^##\s+\S")
_DECISION_ID_PATTERN = re.compile(r"\bD-\d+\b")
_STEP_HEADING_PATTERN = re.compile(r"(?m)^###\s+(STEP-[A-Za-z0-9-]+)\s*:?\s*(.*)$")
_REQ_ID_PATTERN = re.compile(r"\bREQ-[A-Za-z0-9][A-Za-z0-9-]*\b")
_JOURNEY_ID_PATTERN = re.compile(r"\bJ-[A-Za-z0-9][A-Za-z0-9-]*\b")
_STEP_ID_PATTERN = re.compile(r"\bSTEP-[A-Za-z0-9][A-Za-z0-9-]*\b")
_TRACE_TOKEN_PATTERN = re.compile(r"`([^`]+)`|\b([A-Za-z][A-Za-z0-9_]*(?:Service|API|Entity|Repository|Workspace|Shell|Runtime)?)\b")
_SLICE_SOURCE_BUDGET = 35_000
_SLICE_MAX_STEPS = 4
_SLICE_MANIFEST_DERIVATION_VERSION = 1
_SLICE_RETRY_MODES: tuple[tuple[str, bool], ...] = (
    ("all-workstream-peers", False),
    ("direct-peers-only", True),
    ("target-only", True),
)


@dataclass(slots=True)
class VerificationCoverageResult:
    slug: str
    unknown_gate_refs: list[str] = field(default_factory=list)
    uncovered_ac_ids: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unknown_gate_refs and not self.uncovered_ac_ids

    def failure_messages(self) -> list[str]:
        messages = list(self.unknown_gate_refs)
        if self.uncovered_ac_ids:
            messages.append(
                f"test-plan:{self.slug} defines acceptance criteria that no task cites: "
                f"{', '.join(sorted(self.uncovered_ac_ids))}"
            )
        return messages


@dataclass(slots=True)
class TaskPlanningFailure:
    workstream_id: str
    slug: str
    reason: str
    invocation_key: str = ""
    context_paths: list[str] = field(default_factory=list)

    def render(self) -> str:
        details: list[str] = []
        if self.invocation_key:
            details.append(f"invocation={self.invocation_key}")
        if self.context_paths:
            details.append(
                "context="
                + ", ".join(f"`{path}`" for path in self.context_paths)
            )
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"{self.workstream_id}/{self.slug}: {self.reason}{suffix}"


@dataclass(slots=True)
class SlicePlanResult:
    slice_id: str
    dag: ImplementationDAG | None = None
    error: str = ""
    retryable: bool = False
    attempt: SlicePlanningAttempt | None = None
    context_package: ContextPackage | None = None


class TaskPlanningSlice(BaseModel):
    slice_id: str
    title: str = ""
    step_ids: list[str] = PydanticField(default_factory=list)
    requirement_ids: list[str] = PydanticField(default_factory=list)
    journey_ids: list[str] = PydanticField(default_factory=list)
    acceptance_criterion_ids: list[str] = PydanticField(default_factory=list)
    strict_acceptance_criteria: bool = False
    step_titles: list[str] = PydanticField(default_factory=list)
    source_budget_chars: int = _SLICE_SOURCE_BUDGET
    mandatory_source_chars: int = 0


class SlicePlanningStatus(BaseModel):
    slice_id: str
    status: str = "pending"
    retry_mode: str = ""
    context_paths: list[str] = PydanticField(default_factory=list)
    last_error: str = ""
    fragment_key: str = ""


class SlicePlanningAttempt(BaseModel):
    slice_id: str
    mode: str
    attempt: int = 1
    status: str = "failed"
    actor_name: str = ""
    context_paths: list[str] = PydanticField(default_factory=list)
    attempt_key: str = ""
    error: str = ""


class TaskPlanningSliceManifest(BaseModel):
    slug: str
    slices: list[TaskPlanningSlice] = PydanticField(default_factory=list)
    statuses: list[SlicePlanningStatus] = PydanticField(default_factory=list)
    attempts: list[SlicePlanningAttempt] = PydanticField(default_factory=list)
    derivation_version: int = _SLICE_MANIFEST_DERIVATION_VERSION
    plan_digest: str = ""
    test_plan_digest: str = ""
    complete: bool = False


async def _preferred_decision_ledger_key(
    runner: WorkflowRunner,
    feature: Feature,
) -> str:
    """Pick the smallest canonical decision ledger that still preserves intent.

    ``decisions`` can become extremely large on mature features. Task planning
    only needs the feature-wide canonical direction, not the entire compiled
    ledger history, so prefer the broad/global ledgers when available and fall
    back to the full compiled ledger only as a last resort.
    """
    for key in ("decisions:broad", "decisions:global", "decisions"):
        text = await runner.artifacts.get(key, feature=feature)
        if text:
            return key
    return "decisions"


def _extract_decision_ids(text: str) -> set[str]:
    if not text:
        return set()
    return set(_DECISION_ID_PATTERN.findall(text))


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

    def _extract_defined_ids(text: str) -> set[str]:
        return {match.group(1) for match in _AC_DEFINITION_PATTERN.finditer(text)}

    # Scope the regex to the Acceptance Criteria section when we can find
    # it — AC-ids cited in scenarios / checklists / prose are references,
    # not definitions. If the heading is absent (agent wrote free-form
    # markdown without sections), fall back to whole-document regex.
    section_start = _AC_SECTION_HEADING.search(test_plan_text)
    if section_start is None:
        return _extract_defined_ids(test_plan_text) or set(_AC_ID_PATTERN.findall(test_plan_text))
    section_body_start = section_start.end()
    next_heading = _NEXT_H2_HEADING.search(test_plan_text, section_body_start)
    section_body_end = next_heading.start() if next_heading else len(test_plan_text)
    section_text = test_plan_text[section_body_start:section_body_end]
    return _extract_defined_ids(section_text) or set(_AC_ID_PATTERN.findall(section_text))


async def _validate_verification_gates_coverage(
    runner: WorkflowRunner,
    feature: Feature,
    slug: str,
    sf_tasks: list[ImplementationTask],
) -> VerificationCoverageResult:
    """Post-decomposition lint: cross-check ``verification_gates`` against
    real AC-ids in the subfeature's test plan.

    Returns a structured result so task planning can fail closed on
    verification coverage drift instead of only logging diagnostics.
    """
    result = VerificationCoverageResult(slug=slug)
    tp_text = await runner.artifacts.get(f"test-plan:{slug}", feature=feature)
    if not tp_text:
        return result  # No test plan for this SF — nothing to validate against.
    real_ac_ids = _extract_ac_ids(tp_text)
    if not real_ac_ids:
        logger.warning(
            "Test plan for %s has no extractable AC-ids; verification_gates cannot be validated",
            slug,
        )
        return result
    cited_ac_ids: set[str] = set()
    for task in sf_tasks:
        for gate in task.verification_gates:
            if not gate:
                continue
            cited_ac_ids.add(gate)
            if gate not in real_ac_ids:
                message = (
                    f"Task {task.id} ({slug}) cites verification_gate {gate!r} "
                    f"not found in test-plan:{slug}"
                )
                result.unknown_gate_refs.append(message)
                logger.warning("%s — blocking DAG persistence", message)
    uncovered = real_ac_ids - cited_ac_ids
    if uncovered:
        result.uncovered_ac_ids = sorted(uncovered)
        logger.warning(
            "Test plan for %s defines %d AC-ids not cited by any task's verification_gates: %s "
            "— blocking DAG persistence",
            slug, len(uncovered), result.uncovered_ac_ids,
        )
    return result

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

    @staticmethod
    def _context_paths(*packages: ContextPackage | None) -> list[str]:
        paths: list[str] = []
        for package in packages:
            if package is None:
                continue
            for path in (package.index_path, package.manifest_path):
                if path and path not in paths:
                    paths.append(path)
        return paths

    @staticmethod
    async def _clear_stale_blocked_artifact(
        runner: WorkflowRunner,
        feature: Feature,
    ) -> None:
        delete = getattr(runner.artifacts, "delete", None)
        if callable(delete):
            await delete("task-planning-blocked", feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror is not None:
            mirror.delete_artifact(feature.id, "task-planning-blocked")

    @staticmethod
    async def _put_artifact(
        runner: WorkflowRunner,
        feature: Feature,
        key: str,
        text: str,
    ) -> None:
        await runner.artifacts.put(key, text, feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror:
            mirror.write_artifact(feature.id, key, text)

    @classmethod
    async def _load_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> TaskPlanningSliceManifest | None:
        manifest_text = await runner.artifacts.get(f"dag-slices:{slug}", feature=feature)
        if not manifest_text:
            return None
        try:
            return TaskPlanningSliceManifest.model_validate_json(manifest_text)
        except Exception:
            logger.warning("Failed to parse existing slice manifest for %s", slug)
            return None

    @classmethod
    async def _save_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
    ) -> None:
        await cls._put_artifact(
            runner,
            feature,
            f"dag-slices:{manifest.slug}",
            manifest.model_dump_json(indent=2),
        )

    @staticmethod
    def _slice_status_map(
        manifest: TaskPlanningSliceManifest,
    ) -> dict[str, SlicePlanningStatus]:
        return {status.slice_id: status for status in manifest.statuses}

    @staticmethod
    def _content_digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    @classmethod
    async def _delete_artifact_key(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        key: str,
    ) -> None:
        delete = getattr(runner.artifacts, "delete", None)
        if callable(delete):
            await delete(key, feature=feature)
        mirror = runner.services.get("artifact_mirror")
        if mirror is not None:
            mirror.delete_artifact(feature.id, key)

    @classmethod
    async def _clear_slice_manifest_artifacts(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
    ) -> None:
        fragment_keys = {
            cls._slice_fragment_key(manifest.slug, slice_info.slice_id)
            for slice_info in manifest.slices
        }
        fragment_keys.update(
            status.fragment_key
            for status in manifest.statuses
            if status.fragment_key
        )
        for fragment_key in sorted(fragment_keys):
            await cls._delete_artifact_key(runner, feature, fragment_key)
        for attempt in manifest.attempts:
            if attempt.attempt_key:
                await cls._delete_artifact_key(runner, feature, attempt.attempt_key)

    @classmethod
    def _ensure_slice_status(
        cls,
        manifest: TaskPlanningSliceManifest,
        slice_id: str,
    ) -> SlicePlanningStatus:
        status_map = cls._slice_status_map(manifest)
        status = status_map.get(slice_id)
        if status is None:
            status = SlicePlanningStatus(slice_id=slice_id)
            manifest.statuses.append(status)
        return status

    @staticmethod
    def _normalize_artifact_markdown(text: str, artifact_key: str) -> str:
        if not text:
            return ""
        try:
            payload = _json.loads(text)
        except Exception:
            return text
        try:
            if artifact_key.startswith("prd"):
                return to_markdown(PRD.model_validate(payload))
            if artifact_key.startswith("design"):
                return to_markdown(DesignDecisions.model_validate(payload))
            if artifact_key.startswith("plan"):
                return to_markdown(TechnicalPlan.model_validate(payload))
            if artifact_key.startswith("system-design"):
                return to_markdown(SystemDesign.model_validate(payload))
            if artifact_key.startswith("test-plan"):
                return to_markdown(TestPlan.model_validate(payload))
        except Exception:
            logger.debug("Failed to normalize %s as structured markdown", artifact_key, exc_info=True)
        return text

    @staticmethod
    def _markdown_sections(text: str) -> list[tuple[str, str]]:
        if not text.strip():
            return []
        matches = list(re.finditer(r"(?m)^#{1,6}\s+.+$", text))
        if not matches:
            return [("", text.strip())]
        sections: list[tuple[str, str]] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
            heading = match.group(0).strip()
            section_text = text[start:end].strip()
            sections.append((heading, section_text))
        return sections

    @classmethod
    def _extract_matching_sections(
        cls,
        text: str,
        tokens: set[str],
        *,
        fallback_headings: tuple[str, ...] = (),
        max_chars: int = 14_000,
    ) -> str:
        markdown = text.strip()
        if not markdown:
            return ""
        lowered_tokens = {token.lower() for token in tokens if token}
        sections = cls._markdown_sections(markdown)
        selected: list[str] = []
        for heading, section_text in sections:
            haystack = section_text.lower()
            if any(token in haystack for token in lowered_tokens):
                selected.append(section_text)
                continue
            if fallback_headings and any(heading.startswith(prefix) for prefix in fallback_headings):
                selected.append(section_text)
        if not selected:
            selected = [section_text for _heading, section_text in sections[:2]]
        excerpt = "\n\n".join(selected).strip()
        if len(excerpt) <= max_chars:
            return excerpt
        return excerpt[:max_chars].rstrip() + "\n\n...[truncated]\n"

    @staticmethod
    def _extract_trace_tokens(*texts: str) -> set[str]:
        tokens: set[str] = set()
        for text in texts:
            for match in _TRACE_TOKEN_PATTERN.finditer(text):
                token = match.group(1) or match.group(2) or ""
                token = token.strip()
                if len(token) >= 3:
                    tokens.add(token)
        return tokens

    @classmethod
    def _parse_technical_plan(
        cls,
        plan_text: str,
    ) -> TechnicalPlan | None:
        if not plan_text:
            return None
        try:
            return TechnicalPlan.model_validate(_json.loads(plan_text))
        except Exception:
            return None

    @classmethod
    def _parse_test_plan(
        cls,
        test_plan_text: str,
    ) -> TestPlan | None:
        if not test_plan_text:
            return None
        try:
            return TestPlan.model_validate(_json.loads(test_plan_text))
        except Exception:
            return None

    @classmethod
    def _derive_slices_from_markdown_plan(
        cls,
        plan_markdown: str,
        test_plan: TestPlan | None,
        fallback_ac_ids: list[str] | None = None,
    ) -> list[TaskPlanningSlice]:
        all_ac_ids = sorted(
            criterion.id for criterion in (test_plan.acceptance_criteria if test_plan else []) if criterion.id
        ) or sorted(fallback_ac_ids or [])
        matches = list(_STEP_HEADING_PATTERN.finditer(plan_markdown))
        if not matches:
            return [
                TaskPlanningSlice(
                    slice_id="slice-1",
                    title="Whole subfeature",
                    acceptance_criterion_ids=all_ac_ids,
                    strict_acceptance_criteria=False,
                    mandatory_source_chars=len(plan_markdown),
                )
            ]

        raw_slices: list[TaskPlanningSlice] = []
        for idx, match in enumerate(matches):
            start = match.start()
            end = matches[idx + 1].start() if idx + 1 < len(matches) else len(plan_markdown)
            section_text = plan_markdown[start:end]
            step_id = match.group(1).strip()
            title = match.group(2).strip() or step_id
            requirement_ids = sorted(set(_REQ_ID_PATTERN.findall(section_text)))
            journey_ids = sorted(set(_JOURNEY_ID_PATTERN.findall(section_text)))
            strict_ac_ids: list[str] = []
            strict = False
            if test_plan and test_plan.acceptance_criteria:
                strict_ac_ids = sorted(
                    criterion.id
                    for criterion in test_plan.acceptance_criteria
                    if criterion.id and (
                        criterion.linked_requirement in requirement_ids
                        or any(journey_id in criterion.linked_journey_step_id for journey_id in journey_ids)
                    )
                )
                strict = bool(strict_ac_ids)
            raw_slices.append(
                TaskPlanningSlice(
                    slice_id=f"slice-{len(raw_slices) + 1}",
                    title=title,
                    step_ids=[step_id],
                    requirement_ids=requirement_ids,
                    journey_ids=journey_ids,
                    acceptance_criterion_ids=strict_ac_ids or all_ac_ids,
                    strict_acceptance_criteria=strict,
                    step_titles=[title],
                    mandatory_source_chars=len(section_text),
                )
            )

        merged: list[TaskPlanningSlice] = []
        current: TaskPlanningSlice | None = None
        for slice_info in raw_slices:
            if current is None:
                current = slice_info
                continue
            combined_chars = current.mandatory_source_chars + slice_info.mandatory_source_chars
            combined_steps = len(current.step_ids) + len(slice_info.step_ids)
            if combined_chars <= _SLICE_SOURCE_BUDGET and combined_steps <= _SLICE_MAX_STEPS:
                current = TaskPlanningSlice(
                    slice_id=current.slice_id,
                    title=current.title,
                    step_ids=current.step_ids + slice_info.step_ids,
                    requirement_ids=sorted(set(current.requirement_ids + slice_info.requirement_ids)),
                    journey_ids=sorted(set(current.journey_ids + slice_info.journey_ids)),
                    acceptance_criterion_ids=sorted(
                        set(current.acceptance_criterion_ids + slice_info.acceptance_criterion_ids)
                    ),
                    strict_acceptance_criteria=current.strict_acceptance_criteria and slice_info.strict_acceptance_criteria,
                    step_titles=current.step_titles + slice_info.step_titles,
                    mandatory_source_chars=combined_chars,
                )
                continue
            merged.append(current)
            current = slice_info.model_copy(
                update={"slice_id": f"slice-{len(merged) + 1}"}
            )
        if current is not None:
            current = current.model_copy(
                update={"slice_id": f"slice-{len(merged) + 1}"}
            )
            merged.append(current)
        return merged

    @staticmethod
    def _normalize_subfeature_execution_order(
        dag: ImplementationDAG,
    ) -> tuple[ImplementationDAG, bool]:
        """Return a dependency-safe execution order for a subfeature DAG.

        The planner can return waves that contain tasks whose dependencies are
        scheduled in the same wave. Implementation executes each wave in
        parallel, so same-wave dependency edges are semantically invalid even if
        the model output itself parses cleanly.
        """
        tasks_by_id: dict[str, ImplementationTask] = {}
        task_order: dict[str, int] = {}
        for idx, task in enumerate(dag.tasks):
            if task.id in tasks_by_id:
                raise ValueError(f"duplicate task id in DAG: {task.id}")
            tasks_by_id[task.id] = task
            task_order[task.id] = idx

        wave_index: dict[str, int] = {}
        wave_position: dict[str, int] = {}
        for group_idx, group in enumerate(dag.execution_order):
            for pos_idx, task_id in enumerate(group):
                if task_id not in tasks_by_id:
                    raise ValueError(
                        f"execution_order references unknown task id: {task_id}"
                    )
                if task_id in wave_index:
                    raise ValueError(
                        f"execution_order references task id more than once: {task_id}"
                    )
                wave_index[task_id] = group_idx
                wave_position[task_id] = pos_idx

        fallback_group = len(dag.execution_order)
        missing_task_ids = [task.id for task in dag.tasks if task.id not in wave_index]
        for offset, task_id in enumerate(missing_task_ids):
            # Keep omitted tasks conservative: append them in their own trailing
            # waves instead of coalescing them into one new parallel batch.
            wave_index[task_id] = fallback_group + offset
            wave_position[task_id] = 0

        def _sort_key(task_id: str) -> tuple[int, int, int, str]:
            return (
                wave_index[task_id],
                wave_position[task_id],
                task_order[task_id],
                task_id,
            )

        indegree: dict[str, int] = {task.id: 0 for task in dag.tasks}
        dependents: dict[str, list[str]] = {task.id: [] for task in dag.tasks}
        for task in dag.tasks:
            seen_dependencies: set[str] = set()
            for dep in task.dependencies:
                if dep in seen_dependencies:
                    continue
                seen_dependencies.add(dep)
                if dep not in tasks_by_id:
                    raise ValueError(
                        f"task {task.id} depends on unknown task id: {dep}"
                    )
                indegree[task.id] += 1
                dependents[dep].append(task.id)

        ready = sorted(
            [task_id for task_id, count in indegree.items() if count == 0],
            key=_sort_key,
        )
        topo_order: list[str] = []

        while ready:
            task_id = ready.pop(0)
            topo_order.append(task_id)
            for dependent_id in dependents[task_id]:
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)
            ready.sort(key=_sort_key)

        if len(topo_order) != len(dag.tasks):
            remaining = sorted(
                [task_id for task_id, count in indegree.items() if count > 0],
                key=_sort_key,
            )
            raise ValueError(
                "DAG contains cyclic or unsatisfied dependencies: "
                + ", ".join(remaining)
            )

        assigned_wave: dict[str, int] = {}
        for task_id in topo_order:
            task = tasks_by_id[task_id]
            required_wave = wave_index[task_id]
            for dep in dict.fromkeys(task.dependencies):
                required_wave = max(required_wave, assigned_wave[dep] + 1)
            assigned_wave[task_id] = required_wave

        compact_map = {
            original_wave: compact_idx
            for compact_idx, original_wave in enumerate(sorted(set(assigned_wave.values())))
        }
        normalized_order: list[list[str]] = [[] for _ in compact_map]
        for task_id in sorted(tasks_by_id, key=_sort_key):
            normalized_order[compact_map[assigned_wave[task_id]]].append(task_id)

        if normalized_order == dag.execution_order:
            return dag, False

        return (
            ImplementationDAG(
                tasks=dag.tasks,
                num_teams=dag.num_teams,
                execution_order=normalized_order,
                requirement_coverage=dag.requirement_coverage,
                complete=dag.complete,
            ),
            True,
        )

    @classmethod
    async def _derive_slice_manifest(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        subfeature: Any,
    ) -> TaskPlanningSliceManifest:
        plan_text = await runner.artifacts.get(f"plan:{subfeature.slug}", feature=feature) or ""
        test_plan_text = await runner.artifacts.get(f"test-plan:{subfeature.slug}", feature=feature) or ""
        normalized_plan = cls._normalize_artifact_markdown(plan_text, f"plan:{subfeature.slug}")
        normalized_test_plan = cls._normalize_artifact_markdown(test_plan_text, "test-plan")
        plan_digest = cls._content_digest(normalized_plan)
        test_plan_digest = cls._content_digest(normalized_test_plan)

        existing = await cls._load_slice_manifest(runner, feature, subfeature.slug)
        if (
            existing is not None
            and existing.slices
            and existing.derivation_version == _SLICE_MANIFEST_DERIVATION_VERSION
            and existing.plan_digest == plan_digest
            and existing.test_plan_digest == test_plan_digest
        ):
            return existing
        if existing is not None and existing.slices:
            logger.info(
                "Invalidating stale slice manifest for %s (plan/test-plan changed)",
                subfeature.slug,
            )
            await cls._clear_slice_manifest_artifacts(runner, feature, existing)

        test_plan = cls._parse_test_plan(test_plan_text)
        slices = cls._derive_slices_from_markdown_plan(
            normalized_plan,
            test_plan,
            fallback_ac_ids=sorted(_extract_ac_ids(test_plan_text)),
        )
        manifest = TaskPlanningSliceManifest(
            slug=subfeature.slug,
            slices=slices,
            statuses=[SlicePlanningStatus(slice_id=slice_info.slice_id) for slice_info in slices],
            derivation_version=_SLICE_MANIFEST_DERIVATION_VERSION,
            plan_digest=plan_digest,
            test_plan_digest=test_plan_digest,
        )
        await cls._save_slice_manifest(runner, feature, manifest)
        return manifest

    @classmethod
    def _test_plan_excerpt_for_slice(
        cls,
        test_plan_text: str,
        slice_info: TaskPlanningSlice,
    ) -> str:
        markdown = cls._normalize_artifact_markdown(test_plan_text, "test-plan")
        test_plan = cls._parse_test_plan(test_plan_text)
        if test_plan and test_plan.acceptance_criteria:
            selected_criteria = [
                criterion
                for criterion in test_plan.acceptance_criteria
                if not slice_info.acceptance_criterion_ids or criterion.id in slice_info.acceptance_criterion_ids
            ]
            selected_scenarios = [
                scenario
                for scenario in test_plan.test_scenarios
                if not slice_info.acceptance_criterion_ids
                or any(ac_id in slice_info.acceptance_criterion_ids for ac_id in scenario.linked_acceptance)
            ]
            checklist = [
                item
                for item in test_plan.verification_checklist
                if not slice_info.acceptance_criterion_ids
                or any(ac_id in item for ac_id in slice_info.acceptance_criterion_ids)
            ]
            edge_cases = [
                item
                for item in test_plan.edge_cases
                if not slice_info.acceptance_criterion_ids
                or any(ac_id in item for ac_id in slice_info.acceptance_criterion_ids)
            ]
            filtered = TestPlan(
                overview=test_plan.overview,
                acceptance_criteria=selected_criteria,
                test_scenarios=selected_scenarios,
                verification_checklist=checklist,
                edge_cases=edge_cases,
                mocking_strategy=test_plan.mocking_strategy,
                test_environment=test_plan.test_environment,
                decisions=test_plan.decisions,
                complete=test_plan.complete,
            )
            rendered = to_markdown(filtered).strip()
            if rendered:
                return rendered
        return cls._extract_matching_sections(
            markdown,
            set(slice_info.acceptance_criterion_ids + slice_info.step_ids + slice_info.requirement_ids + slice_info.journey_ids),
            fallback_headings=("## Acceptance Criteria", "## Test Scenarios", "## Verification Checklist", "## Edge Cases"),
        )

    @classmethod
    def _target_slice_bundle(
        cls,
        slug: str,
        slice_info: TaskPlanningSlice,
        target_texts: dict[str, str],
    ) -> dict[str, str]:
        plan_text = cls._normalize_artifact_markdown(target_texts.get("plan", ""), f"plan:{slug}")
        prd_text = cls._normalize_artifact_markdown(target_texts.get("prd", ""), f"prd:{slug}")
        design_text = cls._normalize_artifact_markdown(target_texts.get("design", ""), f"design:{slug}")
        system_design_text = cls._normalize_artifact_markdown(
            target_texts.get("system-design", ""),
            f"system-design:{slug}",
        )
        test_plan_text = target_texts.get("test-plan", "")

        base_tokens = set(
            slice_info.step_ids
            + slice_info.requirement_ids
            + slice_info.journey_ids
            + slice_info.acceptance_criterion_ids
        )
        plan_excerpt = cls._extract_matching_sections(
            plan_text,
            base_tokens or set(slice_info.step_titles),
            fallback_headings=("## Implementation Steps", "## Journey Verifications", "## Architectural Risks"),
        )
        trace_tokens = base_tokens | cls._extract_trace_tokens(plan_excerpt)
        return {
            "plan": plan_excerpt,
            "prd": cls._extract_matching_sections(
                prd_text,
                trace_tokens,
                fallback_headings=("## Requirements", "## Acceptance Criteria", "## User Journeys"),
            ),
            "design": cls._extract_matching_sections(
                design_text,
                trace_tokens,
                fallback_headings=("## Journey UX Annotations", "## Design System", "## Verifiable States", "## Interaction Patterns", "## Accessibility"),
            ),
            "system-design": cls._extract_matching_sections(
                system_design_text,
                trace_tokens,
                fallback_headings=("## Services", "## Entities", "## API", "## Architecture Decisions", "## Risks"),
            ),
            "test-plan": cls._test_plan_excerpt_for_slice(test_plan_text, slice_info),
            "subfeature-decisions": target_texts.get("decisions", ""),
        }

    @classmethod
    def _feature_constraint_bundle(
        cls,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        broad_artifacts: dict[str, str],
        *,
        mode_label: str,
    ) -> dict[str, str]:
        edge_context = cls._edge_context_for_slug(
            decomposition,
            subfeature.slug,
            allowed_peers=set(workstream.subfeature_slugs) - {subfeature.slug},
        )
        target_tokens = set(
            slice_info.step_ids
            + slice_info.requirement_ids
            + slice_info.journey_ids
            + slice_info.step_titles
        )
        return {
            "metadata": "\n".join(
                [
                    "## Target Subfeature Metadata",
                    "",
                    f"- ID: {subfeature.id}",
                    f"- Slug: {subfeature.slug}",
                    f"- Name: {subfeature.name}",
                    f"- Slice: {slice_info.slice_id} — {slice_info.title or 'Task Planning Slice'}",
                    f"- Workstream: {workstream.id} — {workstream.name}",
                    f"- Peer context mode: {mode_label}",
                    f"- Workstream depends on: {', '.join(workstream.depends_on or ['none'])}",
                ]
            ),
            "decomposition": to_markdown(decomposition).strip(),
            "edges": edge_context,
            "broad-decisions": cls._normalize_artifact_markdown(
                broad_artifacts.get("decisions:broad", ""),
                "decisions:broad",
            ),
            "broad-prd": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("prd:broad", ""), "prd:broad"),
                target_tokens,
                fallback_headings=("## Requirements", "## User Journeys"),
            ),
            "broad-design": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("design:broad", ""), "design:broad"),
                target_tokens,
                fallback_headings=("## Design System", "## Verifiable States", "## Interaction Patterns"),
            ),
            "broad-plan": cls._extract_matching_sections(
                cls._normalize_artifact_markdown(broad_artifacts.get("plan:broad", ""), "plan:broad"),
                target_tokens,
                fallback_headings=("## Implementation Steps", "## Architectural Risks"),
            ),
        }

    @classmethod
    async def _load_target_texts(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        sf_upstream: dict[str, dict[str, str]],
    ) -> dict[str, str]:
        target_texts = dict(sf_upstream.get(slug, {}))
        for prefix in ("plan", "prd", "design", "system-design", "test-plan", "decisions"):
            if prefix in target_texts:
                continue
            target_texts[prefix] = await runner.artifacts.get(f"{prefix}:{slug}", feature=feature) or ""
        return target_texts

    @classmethod
    async def _persist_slice_attempt(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        manifest: TaskPlanningSliceManifest,
        attempt: SlicePlanningAttempt,
    ) -> None:
        manifest.attempts.append(attempt)
        await cls._put_artifact(
            runner,
            feature,
            attempt.attempt_key,
            "\n".join(
                [
                    f"# Slice Attempt — {attempt.slice_id}",
                    "",
                    f"- Mode: `{attempt.mode}`",
                    f"- Attempt: {attempt.attempt}",
                    f"- Status: {attempt.status}",
                    f"- Actor: `{attempt.actor_name}`" if attempt.actor_name else "",
                    f"- Error: {attempt.error}" if attempt.error else "",
                    "",
                    "## Context Paths",
                    "",
                    *[f"- `{path}`" for path in attempt.context_paths],
                ]
            ).strip() + "\n",
        )
        await cls._save_slice_manifest(runner, feature, manifest)

    @staticmethod
    def _slice_fragment_key(slug: str, slice_id: str) -> str:
        return f"dag-fragment:{slug}:{slice_id}"

    @staticmethod
    def _slice_attempt_key(slug: str, slice_id: str, mode_label: str, attempt: int) -> str:
        return f"dag-fragment-attempt:{slug}:{slice_id}:{mode_label}:{attempt}"

    @classmethod
    def _slice_traceability_errors(
        cls,
        slice_info: TaskPlanningSlice,
        tasks: list[ImplementationTask],
    ) -> list[str]:
        errors: list[str] = []
        for task in tasks:
            if not task.step_ids:
                errors.append(f"{task.id} is missing step_ids")
            if not task.requirement_ids:
                errors.append(f"{task.id} is missing requirement_ids")
            if not task.reference_material:
                errors.append(f"{task.id} is missing reference_material")
            if not task.acceptance_criteria:
                errors.append(f"{task.id} is missing task-level acceptance_criteria")
            if slice_info.step_ids and not set(task.step_ids) & set(slice_info.step_ids):
                errors.append(
                    f"{task.id} references step_ids {task.step_ids} outside slice {slice_info.step_ids}"
                )
        return errors

    @classmethod
    async def _validate_slice_fragment(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        slice_info: TaskPlanningSlice,
        dag: ImplementationDAG,
    ) -> tuple[ImplementationDAG | None, str | None, bool]:
        tasks = dag.tasks
        if not tasks:
            return None, "agent returned no tasks for this slice", True
        try:
            normalized, normalized_flag = cls._normalize_subfeature_execution_order(dag)
        except ValueError as exc:
            return None, str(exc), True
        if normalized_flag:
            logger.warning("Slice %s/%s: normalized execution order before persistence", slug, slice_info.slice_id)
        errors = cls._slice_traceability_errors(slice_info, normalized.tasks)
        if errors:
            return None, "; ".join(errors), True
        if slice_info.strict_acceptance_criteria and slice_info.acceptance_criterion_ids:
            cited = {
                gate
                for task in normalized.tasks
                for gate in task.verification_gates
                if gate
            }
            unknown = sorted(cited - set(slice_info.acceptance_criterion_ids))
            missing = sorted(set(slice_info.acceptance_criterion_ids) - cited)
            messages: list[str] = []
            if unknown:
                messages.append(
                    "fragment cites acceptance criteria outside slice scope: " + ", ".join(unknown)
                )
            if missing:
                messages.append(
                    "fragment leaves slice acceptance criteria uncovered: " + ", ".join(missing)
                )
            if messages:
                return None, "; ".join(messages), True
        return normalized, None, False

    @classmethod
    async def _merge_slice_fragments(
        cls,
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
        manifest: TaskPlanningSliceManifest,
    ) -> ImplementationDAG:
        fragment_dags: list[ImplementationDAG] = []
        for slice_info in manifest.slices:
            fragment_key = cls._slice_fragment_key(slug, slice_info.slice_id)
            fragment_text = await runner.artifacts.get(fragment_key, feature=feature)
            if not fragment_text:
                raise RuntimeError(f"missing fragment {fragment_key}")
            fragment_dags.append(ImplementationDAG.model_validate_json(fragment_text))

        tasks: list[ImplementationTask] = []
        requirement_coverage: dict[str, list[str]] = {}
        seen_task_ids: set[str] = set()
        for fragment in fragment_dags:
            for task in fragment.tasks:
                if task.id in seen_task_ids:
                    raise RuntimeError(f"duplicate task id across slices: {task.id}")
                seen_task_ids.add(task.id)
                tasks.append(task)
            for requirement_id, task_ids in fragment.requirement_coverage.items():
                bucket = requirement_coverage.setdefault(requirement_id, [])
                for task_id in task_ids:
                    if task_id not in bucket:
                        bucket.append(task_id)

        merged = ImplementationDAG(
            tasks=tasks,
            num_teams=max((fragment.num_teams for fragment in fragment_dags), default=0),
            execution_order=[task_ids for fragment in fragment_dags for task_ids in fragment.execution_order],
            requirement_coverage=requirement_coverage,
            complete=True,
        )
        normalized, _normalized_flag = cls._normalize_subfeature_execution_order(merged)
        return normalized

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        # ── Step 1: Resume check ──
        approved_dag = await runner.artifacts.get("dag", feature=feature)
        if approved_dag:
            logger.info("Gate-approved DAG exists — skipping")
            state.dag = approved_dag
            return state

        await self._clear_stale_blocked_artifact(runner, feature)

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
        blocked_failures: list[str] = []

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
                workstream_id = round_workstreams[i].id
                if isinstance(res, BaseException):
                    failure = f"{workstream_id}: decomposition crashed ({res})"
                    logger.error(
                        "Workstream %s decomposition crashed: %s",
                        workstream_id, res,
                    )
                    blocked_failures.append(failure)
                    continue
                blocked_failures.extend(failure.render() for failure in res)
            if blocked_failures:
                break

        if blocked_failures:
            blocked_report = (
                "# Task Planning Blocked\n\n"
                "Task planning stopped before integration review and DAG compilation "
                "because one or more subfeature DAG decompositions failed validation "
                "or exhausted the retry budget.\n\n"
                "## Failures\n\n"
                + "\n".join(f"- {failure}" for failure in blocked_failures)
            )
            blocked_key = "task-planning-blocked"
            await runner.artifacts.put(blocked_key, blocked_report, feature=feature)
            mirror = runner.services.get("artifact_mirror")
            if mirror:
                mirror.write_artifact(feature.id, blocked_key, blocked_report)
            await runner.run(
                Notify(
                    message=(
                        "## Task Planning Blocked\n\n"
                        "Task planning stopped before integration review and DAG "
                        "compilation because one or more subfeature DAGs were invalid "
                        "or could not be generated within the model budget.\n\n"
                        + "\n".join(f"- {failure}" for failure in blocked_failures)
                    ),
                ),
                feature,
                phase_name=self.name,
            )
            raise RuntimeError(f"Task planning blocked. See `{blocked_key}`.")

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
                revision_result = await targeted_revision(
                    runner, feature, self.name,
                    revision_plan=plan,
                    decomposition=ordered_decomp,
                    base_role=planning_lead_role,
                    output_type=ImplementationDAG,
                    artifact_prefix="dag",
                    context_keys=["project", "scope"],
                )
                if not revision_result.ok:
                    failure_text = "; ".join(
                        f"{failure.artifact_prefix}:{failure.slug} — {failure.reason}"
                        for failure in revision_result.failed
                    )
                    raise RuntimeError(
                        "Task planning integration review targeted revision failed: "
                        + failure_text
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
        decision_ledger_key = await _preferred_decision_ledger_key(runner, feature)
        decisions_text = await runner.artifacts.get(decision_ledger_key, feature=feature) or ""
        prd_summaries = await self._load_prd_summaries(runner, feature, decomposition)
        decomp_json = _json.dumps(
            [{"id": sf.id, "slug": sf.slug, "name": sf.name, "description": sf.description}
             for sf in decomposition.subfeatures],
            indent=2,
        )
        context_package = await build_context_package(
            runner,
            feature,
            title="Workstream Planner",
            file_stem="workstream-planner",
            intro_lines=[
                "Decompose the feature into parallel workstreams.",
                "Use the technical plan, decision ledger, PRD summaries, and subfeature decomposition from the referenced files.",
                "Be aggressive about parallelization and only serialize true data dependencies.",
            ],
            items=[
                ContextPackageItem(
                    key="plan",
                    label="Technical Plan",
                    group="Canonical Sources",
                    artifact_key="plan",
                ),
                ContextPackageItem(
                    key="decisions",
                    label="Decision Ledger",
                    group="Canonical Sources",
                    artifact_key=decision_ledger_key,
                ),
                ContextPackageItem(
                    key="prd-summaries",
                    label="PRD Summaries",
                    group="Supporting Context",
                    content=prd_summaries,
                    file_name="workstream-planner-prd-summaries.md",
                ),
                ContextPackageItem(
                    key="subfeature-decomposition",
                    label="Subfeature Decomposition",
                    group="Supporting Context",
                    content=decomp_json,
                    file_name="workstream-planner-subfeature-decomposition.json",
                ),
            ],
        )

        ws_decomp: WorkstreamDecomposition = await runner.run(
            Ask(
                actor=_workstream_planner,
                prompt=(
                    "Decompose this feature into parallel workstreams.\n\n"
                    "Each workstream is a group of subfeatures that can be planned together "
                    "because they share domain context or have tight dependencies.\n\n"
                    "Produce execution rounds — workstreams in the same round can run in "
                    "parallel. A workstream enters a round only when all its depends_on "
                    "workstreams are in earlier rounds.\n\n"
                    + (
                        f"Read the context index first: `{context_package.index_path}`\n"
                        f"Then read the context manifest: `{context_package.manifest_path}`\n"
                        "Open the referenced files selectively instead of loading everything eagerly.\n"
                        if context_package is not None
                        else (
                            f"## Technical Plan\n\n{plan_text}\n\n"
                            f"## Decision Ledger\n\n{decisions_text}\n\n"
                            f"## PRD Summaries\n\n{prd_summaries}\n\n"
                            f"## Subfeature Decomposition\n\n{decomp_json}"
                        )
                    )
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
    ) -> list[TaskPlanningFailure]:
        """Decompose one workstream into per-subfeature DAGs."""
        pending_slugs = []
        for slug in workstream.subfeature_slugs:
            existing = await runner.artifacts.get(f"dag:{slug}", feature=feature)
            if not existing:
                pending_slugs.append(slug)
        if not pending_slugs:
            logger.info("Workstream %s: all SFs already decomposed — skipping", workstream.id)
            return []

        logger.info(
            "Decomposing workstream %s (%d SFs, %d pending)",
            workstream.id,
            len(workstream.subfeature_slugs),
            len(pending_slugs),
        )

        failures: list[TaskPlanningFailure] = []
        for slug in pending_slugs:
            failure = await self._decompose_subfeature(
                runner,
                feature,
                decomposition,
                workstream,
                slug,
                sf_upstream,
            )
            if failure is not None:
                failures.append(failure)
                break
        return failures

    async def _plan_slice(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        sf_upstream: dict[str, dict[str, str]],
        *,
        mode_label: str,
        direct_peer_only: bool,
    ) -> SlicePlanResult:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        actor = AgentActor(
            name=f"dag-ws-{workstream.id}-{subfeature.slug}-{slice_info.slice_id}-{mode_stem}",
            role=planning_lead_role,
            context_keys=["project", "scope"],
        )
        prompt, context_package = await self._build_subfeature_task_prompt(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            sf_upstream,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            slice_info=slice_info,
        )
        attempt = SlicePlanningAttempt(
            slice_id=slice_info.slice_id,
            mode=mode_label,
            actor_name=actor.name,
            context_paths=self._context_paths(context_package),
            attempt_key=self._slice_attempt_key(subfeature.slug, slice_info.slice_id, mode_label, 1),
        )
        try:
            await _clear_agent_session(runner, actor, feature)
            dag: ImplementationDAG = await runner.run(
                Ask(
                    actor=actor,
                    prompt=prompt,
                    output_type=ImplementationDAG,
                ),
                feature,
                phase_name=self.name,
            )
        except Exception as exc:
            attempt.status = "failed"
            attempt.error = str(exc)
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=str(exc),
                retryable=_is_model_boundary_failure(exc),
                attempt=attempt,
                context_package=context_package,
            )

        candidate_tasks = self._extract_subfeature_tasks(dag, decomposition, subfeature.slug)
        slice_task_ids = set(slice_info.step_ids)
        sf_tasks = [
            task
            for task in candidate_tasks
            if not slice_task_ids or set(task.step_ids) & slice_task_ids
        ]
        if not sf_tasks:
            sf_tasks = candidate_tasks
        if not sf_tasks:
            attempt.status = "failed"
            attempt.error = (
                "agent returned no tasks tagged for this subfeature slice; "
                f"slice={slice_info.slice_id}"
            )
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=True,
                attempt=attempt,
                context_package=context_package,
            )

        slice_dag = self._build_subfeature_dag(dag, sf_tasks)
        validated_dag, validation_error, retryable = await self._validate_slice_fragment(
            runner,
            feature,
            subfeature.slug,
            slice_info,
            slice_dag,
        )
        if validated_dag is None:
            attempt.status = "failed"
            attempt.error = validation_error or "slice validation failed"
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=retryable,
                attempt=attempt,
                context_package=context_package,
            )

        attempt.status = "succeeded"
        return SlicePlanResult(
            slice_id=slice_info.slice_id,
            dag=validated_dag,
            attempt=attempt,
            context_package=context_package,
        )

    async def _repair_slice_fragment(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        slice_info: TaskPlanningSlice,
        sf_upstream: dict[str, dict[str, str]],
        current_fragment: ImplementationDAG,
        findings: list[str],
        *,
        mode_label: str,
        direct_peer_only: bool,
    ) -> SlicePlanResult:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        actor = AgentActor(
            name=f"dag-ws-{workstream.id}-{subfeature.slug}-{slice_info.slice_id}-repair-{mode_stem}",
            role=planning_lead_role,
            context_keys=["project", "scope"],
        )
        prompt, context_package = await self._build_subfeature_task_prompt(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            sf_upstream,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
            slice_info=slice_info,
            repair_fragment=current_fragment,
            repair_findings=findings,
        )
        attempt = SlicePlanningAttempt(
            slice_id=slice_info.slice_id,
            mode=f"repair-{mode_label}",
            actor_name=actor.name,
            context_paths=self._context_paths(context_package),
            attempt_key=self._slice_attempt_key(subfeature.slug, slice_info.slice_id, f"repair-{mode_label}", 1),
        )
        try:
            await _clear_agent_session(runner, actor, feature)
            dag: ImplementationDAG = await runner.run(
                Ask(
                    actor=actor,
                    prompt=prompt,
                    output_type=ImplementationDAG,
                ),
                feature,
                phase_name=self.name,
            )
        except Exception as exc:
            attempt.status = "failed"
            attempt.error = str(exc)
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=str(exc),
                retryable=_is_model_boundary_failure(exc),
                attempt=attempt,
                context_package=context_package,
            )

        validated_dag, validation_error, retryable = await self._validate_slice_fragment(
            runner,
            feature,
            subfeature.slug,
            slice_info,
            dag,
        )
        if validated_dag is None:
            attempt.status = "failed"
            attempt.error = validation_error or "slice repair validation failed"
            return SlicePlanResult(
                slice_id=slice_info.slice_id,
                error=attempt.error,
                retryable=retryable,
                attempt=attempt,
                context_package=context_package,
            )

        attempt.status = "succeeded"
        return SlicePlanResult(
            slice_id=slice_info.slice_id,
            dag=validated_dag,
            attempt=attempt,
            context_package=context_package,
        )

    async def _decompose_subfeature(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        sf_upstream: dict[str, dict[str, str]],
    ) -> TaskPlanningFailure | None:
        sf = next((item for item in decomposition.subfeatures if item.slug == slug), None)
        if sf is None:
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="subfeature is missing from decomposition",
            )
        manifest = await self._derive_slice_manifest(runner, feature, sf)

        for slice_info in manifest.slices:
            fragment_key = self._slice_fragment_key(slug, slice_info.slice_id)
            fragment_text = await runner.artifacts.get(fragment_key, feature=feature)
            status = self._ensure_slice_status(manifest, slice_info.slice_id)
            status.fragment_key = fragment_key
            if fragment_text:
                try:
                    persisted_fragment = ImplementationDAG.model_validate_json(fragment_text)
                    validated_fragment, validation_error, _retryable = await self._validate_slice_fragment(
                        runner,
                        feature,
                        slug,
                        slice_info,
                        persisted_fragment,
                    )
                    if validated_fragment is None:
                        await self._delete_artifact_key(runner, feature, fragment_key)
                        status.status = "pending"
                        status.last_error = (
                            f"existing fragment {fragment_key} failed validation: "
                            f"{validation_error or 'unknown validation error'}"
                        )
                        continue
                    persisted_json = validated_fragment.model_dump_json(indent=2)
                    if persisted_json != fragment_text:
                        await self._put_artifact(
                            runner,
                            feature,
                            fragment_key,
                            persisted_json,
                        )
                    status.status = "completed"
                    status.last_error = ""
                    continue
                except Exception:
                    await self._delete_artifact_key(runner, feature, fragment_key)
                    status.status = "pending"
                    status.last_error = f"existing fragment {fragment_key} is invalid"
            if status.status != "completed":
                status.status = "pending"
        await self._save_slice_manifest(runner, feature, manifest)

        for mode_label, direct_peer_only in _SLICE_RETRY_MODES:
            pending_slices = [
                slice_info
                for slice_info in manifest.slices
                if self._ensure_slice_status(manifest, slice_info.slice_id).status != "completed"
            ]
            if not pending_slices:
                break
            results = await asyncio.gather(
                *[
                    self._plan_slice(
                        runner,
                        feature,
                        decomposition,
                        workstream,
                        sf,
                        slice_info,
                        sf_upstream,
                        mode_label=mode_label,
                        direct_peer_only=direct_peer_only,
                    )
                    for slice_info in pending_slices
                ]
            )
            for result in results:
                status = self._ensure_slice_status(manifest, result.slice_id)
                if result.attempt is not None:
                    await self._persist_slice_attempt(runner, feature, manifest, result.attempt)
                if result.dag is not None:
                    fragment_key = self._slice_fragment_key(slug, result.slice_id)
                    await self._put_artifact(
                        runner,
                        feature,
                        fragment_key,
                        result.dag.model_dump_json(indent=2),
                    )
                    status.status = "completed"
                    status.retry_mode = mode_label
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = ""
                    status.fragment_key = fragment_key
                else:
                    status.status = "failed"
                    status.retry_mode = mode_label
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = result.error
            await self._save_slice_manifest(runner, feature, manifest)

        incomplete = [
            status for status in manifest.statuses
            if status.status != "completed"
        ]
        if incomplete:
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="; ".join(
                    f"{status.slice_id} failed in {status.retry_mode or 'unknown-mode'}: {status.last_error}"
                    for status in incomplete
                ),
                invocation_key=f"dag-ws-{workstream.id}-{slug}",
                context_paths=sorted({path for status in incomplete for path in status.context_paths}),
            )

        sf_dag = await self._merge_slice_fragments(runner, feature, slug, manifest)
        coverage = await _validate_verification_gates_coverage(
            runner,
            feature,
            slug,
            sf_dag.tasks,
        )
        if not coverage.ok:
            repaired = await self._attempt_coverage_repair(
                runner,
                feature,
                decomposition,
                workstream,
                sf,
                sf_upstream,
                manifest,
                coverage,
            )
            if repaired is not None:
                sf_dag = repaired
                coverage = await _validate_verification_gates_coverage(
                    runner,
                    feature,
                    slug,
                    sf_dag.tasks,
                )
        if not coverage.ok:
            bad_statuses = [
                self._ensure_slice_status(manifest, slice_info.slice_id)
                for slice_info in manifest.slices
                if set(slice_info.acceptance_criterion_ids) & set(coverage.uncovered_ac_ids)
            ]
            return TaskPlanningFailure(
                workstream_id=workstream.id,
                slug=slug,
                reason="; ".join(coverage.failure_messages()),
                invocation_key=f"dag-ws-{workstream.id}-{slug}",
                context_paths=sorted({path for status in bad_statuses for path in status.context_paths}),
            )

        manifest.complete = True
        await self._save_slice_manifest(runner, feature, manifest)
        await self._put_artifact(
            runner,
            feature,
            f"dag:{slug}",
            sf_dag.model_dump_json(indent=2),
        )
        logger.info(
            "Workstream %s: stored %d tasks across %d slices for %s",
            workstream.id,
            len(sf_dag.tasks),
            len(manifest.slices),
            slug,
        )
        return None

    async def _attempt_coverage_repair(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        manifest: TaskPlanningSliceManifest,
        coverage: VerificationCoverageResult,
    ) -> ImplementationDAG | None:
        if not autonomous_remainder_enabled(runner, feature, phase_name=self.name):
            return None

        logger.info(
            "Workstream %s/%s: validation drift detected — attempting autonomous slice repair",
            workstream.id,
            subfeature.slug,
        )
        task_to_slice: dict[str, str] = {}
        fragment_by_slice: dict[str, ImplementationDAG] = {}
        for slice_info in manifest.slices:
            fragment_text = await runner.artifacts.get(
                self._slice_fragment_key(subfeature.slug, slice_info.slice_id),
                feature=feature,
            )
            if not fragment_text:
                continue
            fragment = ImplementationDAG.model_validate_json(fragment_text)
            fragment_by_slice[slice_info.slice_id] = fragment
            for task in fragment.tasks:
                task_to_slice[task.id] = slice_info.slice_id

        affected_slice_ids: set[str] = set()
        uncovered = set(coverage.uncovered_ac_ids)
        if uncovered:
            for slice_info in manifest.slices:
                if uncovered & set(slice_info.acceptance_criterion_ids):
                    affected_slice_ids.add(slice_info.slice_id)

        for message in coverage.unknown_gate_refs:
            task_match = re.search(r"Task\s+([A-Za-z0-9._-]+)", message)
            if task_match and task_match.group(1) in task_to_slice:
                affected_slice_ids.add(task_to_slice[task_match.group(1)])

        if not affected_slice_ids:
            return None

        findings = coverage.failure_messages() or ["verification coverage drift"]
        remaining = [slice_info for slice_info in manifest.slices if slice_info.slice_id in affected_slice_ids]
        for mode_label, direct_peer_only in _SLICE_RETRY_MODES:
            if not remaining:
                break
            results = await asyncio.gather(
                *[
                    self._repair_slice_fragment(
                        runner,
                        feature,
                        decomposition,
                        workstream,
                        subfeature,
                        slice_info,
                        sf_upstream,
                        fragment_by_slice[slice_info.slice_id],
                        findings,
                        mode_label=mode_label,
                        direct_peer_only=direct_peer_only,
                    )
                    for slice_info in remaining
                    if slice_info.slice_id in fragment_by_slice
                ]
            )
            failed_slice_ids: set[str] = set()
            for result in results:
                status = self._ensure_slice_status(manifest, result.slice_id)
                if result.attempt is not None:
                    await self._persist_slice_attempt(runner, feature, manifest, result.attempt)
                if result.dag is not None:
                    fragment_key = self._slice_fragment_key(subfeature.slug, result.slice_id)
                    await self._put_artifact(
                        runner,
                        feature,
                        fragment_key,
                        result.dag.model_dump_json(indent=2),
                    )
                    fragment_by_slice[result.slice_id] = result.dag
                    status.status = "completed"
                    status.retry_mode = f"repair-{mode_label}"
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = ""
                else:
                    failed_slice_ids.add(result.slice_id)
                    status.status = "failed"
                    status.retry_mode = f"repair-{mode_label}"
                    status.context_paths = result.attempt.context_paths if result.attempt else []
                    status.last_error = result.error
            await self._save_slice_manifest(runner, feature, manifest)
            remaining = [slice_info for slice_info in remaining if slice_info.slice_id in failed_slice_ids]

        if remaining:
            return None
        return await self._merge_slice_fragments(runner, feature, subfeature.slug, manifest)

    async def _build_subfeature_task_prompt(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        *,
        direct_peer_only: bool,
        mode_label: str,
        slice_info: TaskPlanningSlice | None = None,
        repair_fragment: ImplementationDAG | None = None,
        repair_findings: list[str] | None = None,
    ) -> tuple[str, ContextPackage | None]:
        package = await self._build_subfeature_task_context_package(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=slice_info,
        )
        context = ""
        if package is None:
            context = await self._build_subfeature_task_context(
                runner,
                feature,
                decomposition,
                workstream,
                subfeature,
                sf_upstream,
                direct_peer_only=direct_peer_only,
                mode_label=mode_label,
                slice_info=slice_info,
            )
        slice_note = ""
        if slice_info is not None:
            slice_note = (
                f"Target only planning slice `{slice_info.slice_id}`.\n"
                f"Step IDs in scope: {', '.join(slice_info.step_ids) or 'none'}.\n"
                f"Requirement IDs in scope: {', '.join(slice_info.requirement_ids) or 'none'}.\n"
                f"Journey IDs in scope: {', '.join(slice_info.journey_ids) or 'none'}.\n"
                f"Acceptance criteria in scope: {', '.join(slice_info.acceptance_criterion_ids) or 'none'}.\n\n"
            )
        repair_note = ""
        if repair_fragment is not None:
            findings_text = "\n".join(f"- {finding}" for finding in (repair_findings or []))
            repair_note = (
                "You are repairing an existing slice fragment, not replanning from scratch.\n"
                "Keep task IDs, dependencies, file scope, and execution order stable unless a minimal change is required.\n"
                "Repair the fragment so every task has required traceability fields and slice coverage aligns with the canonical test plan.\n\n"
                "## Repair Findings\n"
                f"{findings_text or '- verification coverage drift'}\n\n"
                "## Current Slice Fragment\n"
                f"{repair_fragment.model_dump_json(indent=2)}\n\n"
            )
        prompt = (
            f"You are decomposing subfeature '{subfeature.name}' ({subfeature.slug}) within "
            f"workstream '{workstream.name}' into implementation tasks.\n\n"
            f"Workstream rationale: {workstream.rationale}\n"
            f"Peer context mode: {mode_label}\n"
            f"Workstream depends on: {workstream.depends_on or ['none']}\n\n"
            f"{slice_note}"
            f"{repair_note}"
            "Create tasks ONLY for the target subfeature. Every task.subfeature_id MUST be the "
            f"exact subfeature slug '{subfeature.slug}'. Do not emit tasks for peer subfeatures.\n\n"
            "Break the target subfeature's technical plan into parallelizable implementation tasks. "
            "Each task needs:\n"
            "- file_scope (path + create/modify/read_only)\n"
            "- requirement_ids (REQ-* from PRD)\n"
            "- step_ids (STEP-* from plan)\n"
            "- acceptance_criteria\n"
            "- counterexamples\n"
            "- verification_gates populated with exact AC-ids from the subfeature's TEST-PLAN\n"
            "- reference_material with self-contained excerpts from upstream artifacts\n"
            "- subfeature_id set exactly to the target slug\n\n"
            "When a TEST-PLAN section is present, treat its acceptance criteria as the source of "
            "truth for task-level verification_gates and acceptance_criteria. Use the exact AC-id "
            "strings from the test plan; mismatched or invented IDs will block publication.\n\n"
            "Every task must carry step_ids, requirement_ids, acceptance_criteria, reference_material, "
            "and verification_gates that are self-contained enough for downstream implementers and verifiers.\n\n"
            "execution_order must be topologically valid: if a task depends on another task, "
            "the dependent task must appear in a later execution_order wave.\n\n"
            "Be aggressive about parallelization, but only create task dependencies when one task "
            "truly cannot start until another finishes. Keep dependencies within the target "
            "subfeature task set.\n\n"
        )
        if package is not None:
            prompt += (
                f"Read the context index first: `{package.index_path}`\n"
                f"Then read the context manifest: `{package.manifest_path}`\n"
                "Open the referenced files selectively instead of loading everything eagerly."
            )
        else:
            prompt += context
        return prompt, package

    async def _build_subfeature_task_context_package(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        *,
        mode_label: str,
        direct_peer_only: bool,
        slice_info: TaskPlanningSlice | None = None,
    ) -> ContextPackage | None:
        mode_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", mode_label.strip()).strip("-") or "default"
        target_texts = await self._load_target_texts(runner, feature, subfeature.slug, {})
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        target_bundle = self._target_slice_bundle(subfeature.slug, active_slice, target_texts)
        broad_artifacts = {
            key: await runner.artifacts.get(key, feature=feature) or ""
            for key in ("prd:broad", "design:broad", "plan:broad", "decisions:broad")
        }
        feature_bundle = self._feature_constraint_bundle(
            decomposition,
            workstream,
            subfeature,
            active_slice,
            broad_artifacts,
            mode_label=mode_label,
        )
        decision_pack_text = await self._build_scoped_decision_pack(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=active_slice,
        )
        peer_context = await self._render_peer_context(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature.slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
        )
        return await build_context_package(
            runner,
            feature,
            title=f"Subfeature DAG Planner — {subfeature.slug}",
            file_stem=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}",
            intro_lines=[
                f"Plan implementation tasks only for subfeature `{subfeature.slug}`.",
                f"Target slice: `{active_slice.slice_id}` ({', '.join(active_slice.step_ids) or 'whole subfeature'}).",
                "Use slice-local target excerpts, the full target decision ledger, broad constraints, and peer summaries from the referenced files.",
            ],
            items=[
                ContextPackageItem(
                    key="metadata",
                    label="Target Metadata",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["metadata"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-metadata.md",
                ),
                ContextPackageItem(
                    key="decomposition",
                    label="Subfeature Decomposition",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["decomposition"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-decomposition.md",
                ),
                ContextPackageItem(
                    key="edges",
                    label="Interface Edges",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["edges"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-edges.md",
                ),
                ContextPackageItem(
                    key="broad-decisions",
                    label="Broad Decision Ledger",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["broad-decisions"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-decisions.md",
                ),
                ContextPackageItem(
                    key="broad-prd",
                    label="Broad PRD Excerpts",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["broad-prd"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-prd.md",
                ),
                ContextPackageItem(
                    key="broad-design",
                    label="Broad Design Excerpts",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["broad-design"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-design.md",
                ),
                ContextPackageItem(
                    key="broad-plan",
                    label="Broad Plan Excerpts",
                    group="Feature-wide Constraint Layer",
                    content=feature_bundle["broad-plan"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-broad-plan.md",
                ),
                ContextPackageItem(
                    key="plan",
                    label="Slice Technical Plan",
                    group="Target Slice Layer",
                    content=target_bundle["plan"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-plan.md",
                ),
                ContextPackageItem(
                    key="prd",
                    label="Slice PRD Excerpts",
                    group="Target Slice Layer",
                    content=target_bundle["prd"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-prd.md",
                ),
                ContextPackageItem(
                    key="design",
                    label="Slice Design Excerpts",
                    group="Target Slice Layer",
                    content=target_bundle["design"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-design.md",
                ),
                ContextPackageItem(
                    key="system-design",
                    label="Slice System Design Excerpts",
                    group="Target Slice Layer",
                    content=target_bundle["system-design"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-system-design.md",
                ),
                ContextPackageItem(
                    key="test-plan",
                    label="Slice Test Plan Excerpts",
                    group="Target Slice Layer",
                    content=target_bundle["test-plan"],
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-test-plan.md",
                ),
                ContextPackageItem(
                    key="subfeature-decisions",
                    label="Target Decision Ledger",
                    group="Target Slice Layer",
                    artifact_key=f"decisions:{subfeature.slug}",
                ),
                ContextPackageItem(
                    key="decision-pack",
                    label="Scoped Decision Pack",
                    group="Peer Contract Layer",
                    content=decision_pack_text,
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-decision-pack.md",
                ),
                ContextPackageItem(
                    key="peer-context",
                    label="Peer Summaries",
                    group="Peer Contract Layer",
                    content=peer_context,
                    file_name=f"dag-ws-{workstream.id}-{subfeature.slug}-{active_slice.slice_id}-{mode_stem}-peer-context.md",
                ),
            ],
        )

    async def _build_subfeature_task_context(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        sf_upstream: dict[str, dict[str, str]],
        *,
        direct_peer_only: bool,
        mode_label: str,
        slice_info: TaskPlanningSlice | None = None,
    ) -> str:
        sections: list[str] = []
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        target_texts = await self._load_target_texts(runner, feature, subfeature.slug, sf_upstream)
        target_bundle = self._target_slice_bundle(subfeature.slug, active_slice, target_texts)
        broad_artifacts = {
            key: await runner.artifacts.get(key, feature=feature) or ""
            for key in ("prd:broad", "design:broad", "plan:broad", "decisions:broad")
        }
        feature_bundle = self._feature_constraint_bundle(
            decomposition,
            workstream,
            subfeature,
            active_slice,
            broad_artifacts,
            mode_label=mode_label,
        )
        decisions_text = await self._build_scoped_decision_pack(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature,
            mode_label=mode_label,
            direct_peer_only=direct_peer_only,
            slice_info=active_slice,
        )
        if decisions_text:
            sections.append(decisions_text)

        sections.extend(
            section
            for section in (
                feature_bundle["metadata"],
                feature_bundle["edges"],
                feature_bundle["broad-decisions"],
                feature_bundle["broad-prd"],
                feature_bundle["broad-design"],
                feature_bundle["broad-plan"],
                target_bundle["plan"],
                target_bundle["prd"],
                target_bundle["design"],
                target_bundle["system-design"],
                target_bundle["test-plan"],
                target_bundle["subfeature-decisions"],
            )
            if section
        )

        peer_context = await self._render_peer_context(
            runner,
            feature,
            decomposition,
            workstream,
            subfeature.slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
        )
        if peer_context:
            sections.append(peer_context)

        return "\n\n---\n\n".join(section for section in sections if section)

    async def _render_peer_context(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        *,
        direct_peer_only: bool,
        mode_label: str,
    ) -> str:
        peer_slugs = self._peer_slugs_for_context(
            decomposition,
            workstream,
            slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
        )
        if not peer_slugs:
            return ""

        sections = ["## Workstream Peer Context", ""]
        for peer_slug in peer_slugs:
            peer = next((item for item in decomposition.subfeatures if item.slug == peer_slug), None)
            lines = [
                f"### {peer.name if peer else peer_slug} ({peer_slug})",
                "",
                f"Description: {peer.description if peer else ''}".strip(),
            ]
            edge_lines = self._edge_lines_between(decomposition, slug, peer_slug)
            if edge_lines:
                lines.extend(["", "Relevant edges:"])
                lines.extend(f"- {line}" for line in edge_lines)

            summary_blocks = await self._load_peer_summary_blocks(runner, feature, peer_slug)
            if summary_blocks:
                lines.extend(["", *summary_blocks])
            sections.append("\n".join(line for line in lines if line))

        return "\n\n".join(sections)

    @staticmethod
    async def _load_peer_summary_blocks(
        runner: WorkflowRunner,
        feature: Feature,
        slug: str,
    ) -> list[str]:
        blocks: list[str] = []
        summary_keys = [
            ("prd-summary", "PRD Summary"),
            ("design-summary", "Design Summary"),
            ("plan-summary", "Plan Summary"),
            ("test-plan-summary", "Test Plan Summary"),
            ("decisions-summary", "Decision Summary"),
        ]
        for key_prefix, label in summary_keys:
            text = await runner.artifacts.get(f"{key_prefix}:{slug}", feature=feature)
            if text:
                blocks.append(f"#### {label}\n\n{text}")
        return blocks

    @classmethod
    def _peer_slugs_for_context(
        cls,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        slug: str,
        *,
        direct_peer_only: bool,
        mode_label: str,
    ) -> list[str]:
        if mode_label == "target-only":
            return []
        peer_slugs = [peer for peer in workstream.subfeature_slugs if peer != slug]
        if direct_peer_only:
            connected = cls._connected_peer_slugs(decomposition, slug)
            peer_slugs = [peer for peer in peer_slugs if peer in connected]
        return peer_slugs

    async def _build_scoped_decision_pack(
        self,
        runner: WorkflowRunner,
        feature: Feature,
        decomposition: SubfeatureDecomposition,
        workstream: Any,
        subfeature: Any,
        *,
        mode_label: str,
        direct_peer_only: bool,
        slice_info: TaskPlanningSlice | None = None,
    ) -> str:
        compiled_text = await runner.artifacts.get("decisions", feature=feature) or ""
        broad_text = await runner.artifacts.get("decisions:broad", feature=feature) or ""
        global_text = await runner.artifacts.get(GLOBAL_DECISIONS_KEY, feature=feature) or ""
        target_decisions_key = f"decisions:{subfeature.slug}"
        target_text = await runner.artifacts.get(target_decisions_key, feature=feature) or ""

        compiled_ledger = parse_decision_ledger(compiled_text)
        broad_ledger = parse_decision_ledger(broad_text)
        global_ledger = parse_decision_ledger(global_text)
        target_ledger = parse_decision_ledger(target_text)
        target_texts = await self._load_target_texts(runner, feature, subfeature.slug, {})
        active_slice = slice_info or TaskPlanningSlice(slice_id="slice-1", title="Whole subfeature")
        target_bundle = self._target_slice_bundle(subfeature.slug, active_slice, target_texts)

        peer_slugs = self._peer_slugs_for_context(
            decomposition,
            workstream,
            subfeature.slug,
            direct_peer_only=direct_peer_only,
            mode_label=mode_label,
        )
        peer_ledgers: list[tuple[str, DecisionLedger]] = []
        peer_reference_sources: list[tuple[str, str]] = []
        for peer_slug in peer_slugs:
            peer_text = await runner.artifacts.get(f"decisions:{peer_slug}", feature=feature) or ""
            peer_ledgers.append((peer_slug, parse_decision_ledger(peer_text)))

            summary_key = f"decisions-summary:{peer_slug}"
            summary_text = await runner.artifacts.get(summary_key, feature=feature) or ""
            if summary_text:
                peer_reference_sources.append((summary_key, summary_text))
                continue
            if peer_text:
                peer_reference_sources.append((f"decisions:{peer_slug}", peer_text))

        candidate_ids: set[str] = {
            decision.id for decision in target_ledger.decisions
        }

        referenced_ids: set[str] = set()
        for text in target_bundle.values():
            referenced_ids.update(_extract_decision_ids(text))

        for _source_key, source_text in peer_reference_sources:
            referenced_ids.update(_extract_decision_ids(source_text))

        candidate_ids.update(referenced_ids)

        selected_by_id: dict[str, DecisionRecord] = {}
        for decision in compiled_ledger.decisions:
            if decision.id in candidate_ids:
                selected_by_id[decision.id] = decision.model_copy(deep=True)

        for ledger in (target_ledger, global_ledger, broad_ledger, *[ledger for _slug, ledger in peer_ledgers]):
            for decision in ledger.decisions:
                if decision.id in candidate_ids and decision.id not in selected_by_id:
                    selected_by_id[decision.id] = decision.model_copy(deep=True)

        missing_ids = sorted(candidate_ids - set(selected_by_id))
        scoped_ledger = DecisionLedger(
            title="Scoped Decision Pack",
            decisions=sorted(selected_by_id.values(), key=_decision_sort_key),
            complete=bool(selected_by_id),
        )

        included_sources: list[str] = []
        if broad_text and any(
            decision.id in selected_by_id for decision in broad_ledger.decisions
        ):
            included_sources.append("- Broad Decision Ledger: `decisions:broad`")
        if global_text and any(
            decision.id in selected_by_id for decision in global_ledger.decisions
        ):
            included_sources.append(f"- Global Decision Ledger: `{GLOBAL_DECISIONS_KEY}`")
        if target_text:
            included_sources.append(f"- Target Decision Ledger: `{target_decisions_key}`")
        if peer_reference_sources:
            included_sources.append(
                "- Peer Decision Citation Sources: "
                + ", ".join(f"`{source_key}`" for source_key, _text in peer_reference_sources)
            )
        if compiled_text:
            included_sources.append("- Full-record resolution source: `decisions`")

        lines = [
            "# Scoped Decision Pack",
            "",
            f"- Target subfeature: `{subfeature.slug}`",
            f"- Peer context mode: `{mode_label}`",
            f"- Workstream: `{workstream.id}`",
            "",
            "## Included Sources",
            "",
            *(included_sources or ["- _No decision sources available._"]),
            "",
            "## Explicitly Referenced Decision IDs",
            "",
        ]
        if referenced_ids:
            lines.extend(f"- `{decision_id}`" for decision_id in sorted(referenced_ids))
        else:
            lines.append("- _No explicit decision citations found in target or peer inputs._")

        if missing_ids:
            lines.extend(
                [
                    "",
                    "## Missing Referenced IDs",
                    "",
                    *[f"- `{decision_id}`" for decision_id in missing_ids],
                ]
            )

        lines.extend(
            [
                "",
                "## Decision Records",
                "",
                to_markdown(scoped_ledger).rstrip(),
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _connected_peer_slugs(
        decomposition: SubfeatureDecomposition,
        slug: str,
    ) -> set[str]:
        return {
            edge.to_subfeature if edge.from_subfeature == slug else edge.from_subfeature
            for edge in decomposition.edges
            if slug in (edge.from_subfeature, edge.to_subfeature)
        }

    @staticmethod
    def _edge_lines_between(
        decomposition: SubfeatureDecomposition,
        slug: str,
        peer_slug: str,
    ) -> list[str]:
        lines: list[str] = []
        for edge in decomposition.edges:
            participants = {edge.from_subfeature, edge.to_subfeature}
            if {slug, peer_slug} != participants:
                continue
            details = (
                f"{edge.from_subfeature} → {edge.to_subfeature} "
                f"({edge.interface_type}): {edge.description}"
            )
            if edge.data_contract:
                details += f" [contract: {edge.data_contract}]"
            if edge.owner:
                details += f" [owner: {edge.owner}]"
            lines.append(details)
        return lines

    @classmethod
    def _edge_context_for_slug(
        cls,
        decomposition: SubfeatureDecomposition,
        slug: str,
        *,
        allowed_peers: set[str] | None = None,
    ) -> str:
        lines: list[str] = []
        seen_peers: set[str] = set()
        for edge in decomposition.edges:
            if slug not in (edge.from_subfeature, edge.to_subfeature):
                continue
            peer_slug = edge.to_subfeature if edge.from_subfeature == slug else edge.from_subfeature
            if allowed_peers is not None and peer_slug not in allowed_peers:
                continue
            if peer_slug in seen_peers:
                continue
            seen_peers.add(peer_slug)
            lines.extend(f"- {line}" for line in cls._edge_lines_between(decomposition, slug, peer_slug))
        if not lines:
            return ""
        return "## Interface Edges\n\n" + "\n".join(lines)

    @staticmethod
    def _extract_subfeature_tasks(
        dag: ImplementationDAG,
        decomposition: SubfeatureDecomposition,
        slug: str,
    ) -> list[ImplementationTask]:
        sf = next((item for item in decomposition.subfeatures if item.slug == slug), None)
        sf_ids = {slug, slug.lower()}
        if sf is not None:
            sf_ids.update(
                {
                    sf.id,
                    sf.id.lower(),
                    sf.name,
                    sf.name.lower(),
                    sf.id.replace("-", ""),
                }
            )
        sf_tasks = [task for task in dag.tasks if task.subfeature_id in sf_ids]
        if sf_tasks:
            return sf_tasks
        return [
            task
            for task in dag.tasks
            if task.subfeature_id
            and (
                slug in task.subfeature_id.lower()
                or (sf is not None and sf.id.lower() in task.subfeature_id.lower())
            )
        ]

    @staticmethod
    def _build_subfeature_dag(
        dag: ImplementationDAG,
        sf_tasks: list[ImplementationTask],
    ) -> ImplementationDAG:
        sf_task_ids = {task.id for task in sf_tasks}
        return ImplementationDAG(
            tasks=sf_tasks,
            num_teams=dag.num_teams,
            execution_order=[
                [task_id for task_id in round_ids if task_id in sf_task_ids]
                for round_ids in dag.execution_order
                if any(task_id in sf_task_ids for task_id in round_ids)
            ],
            requirement_coverage={
                requirement_id: [
                    task_id for task_id in task_ids if task_id in sf_task_ids
                ]
                for requirement_id, task_ids in dag.requirement_coverage.items()
                if any(task_id in sf_task_ids for task_id in task_ids)
            },
            complete=True,
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
