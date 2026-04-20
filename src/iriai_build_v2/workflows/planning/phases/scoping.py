from __future__ import annotations

import json
import logging
from pathlib import Path

from iriai_compose import Ask, Feature, Phase, WorkflowRunner

from ....models.outputs import ProjectContext, RepoSpec, ScopeOutput, Envelope, envelope_done
from ....models.state import BuildState
from ....roles import scoper, user
from ....services.markdown import to_markdown
from .._control import load_planning_control
from .._decisions import refresh_decision_ledger
from ..._common import HostedInterview, gate_and_revise

logger = logging.getLogger(__name__)

_SCOPE_APPROVED_KEY = "scope:approved"
_SCOPE_DRAFT_KEY = "scope:draft"


async def _load_structured_scope_draft(
    runner: WorkflowRunner,
    feature: Feature,
) -> ScopeOutput | None:
    raw = await runner.artifacts.get(_SCOPE_DRAFT_KEY, feature=feature)
    if not raw:
        return None
    try:
        return ScopeOutput.model_validate(json.loads(raw))
    except Exception:
        logger.warning("Failed to parse stored structured scope draft", exc_info=True)
        return None


async def _recover_scope_model(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    approved_scope_text: str,
) -> ScopeOutput | None:
    recovered = await _load_structured_scope_draft(runner, feature)
    if recovered is not None:
        return recovered

    logger.info("Recovering structured scope model from approved scope text")
    recovered = await runner.run(
        Ask(
            actor=scoper,
            prompt=(
                "Convert the following approved feature scope document into a "
                "structured ScopeOutput object. Preserve repo actions, names, "
                "paths, GitHub URLs, constraints, out-of-scope items, and user "
                "decisions exactly as written. Do not ask follow-up questions.\n\n"
                f"{approved_scope_text}"
            ),
            output_type=ScopeOutput,
        ),
        feature,
        phase_name=ScopingPhase.name,
    )
    await runner.artifacts.put(
        _SCOPE_DRAFT_KEY,
        recovered.model_dump_json(indent=2),
        feature=feature,
    )
    return recovered


async def _load_project_context(
    runner: WorkflowRunner,
    feature: Feature,
) -> ProjectContext | None:
    raw = await runner.artifacts.get("project", feature=feature)
    if not raw:
        return None
    try:
        return ProjectContext.model_validate_json(raw)
    except Exception:
        logger.info("Project artifact is missing or legacy; rebuilding project context")
        return None


def _action_rank(action: str) -> int:
    return {
        "read_only": 0,
        "extend": 1,
        "new": 2,
    }.get(action, 0)


def _find_git_repo_root(workspace_root: Path | None, local_path: str) -> tuple[str, str] | None:
    if workspace_root is None or not local_path:
        return None
    candidate = Path(local_path)
    if not candidate.is_absolute():
        candidate = workspace_root / local_path
    if not candidate.exists():
        return None

    search = candidate if candidate.is_dir() else candidate.parent
    while True:
        if (search / ".git").exists():
            root_path = search
            break
        if search == workspace_root or search.parent == search:
            return None
        search = search.parent

    try:
        relative = root_path.relative_to(workspace_root)
        normalized = str(relative)
    except ValueError:
        normalized = str(root_path)

    original = str(Path(local_path))
    if normalized == original:
        return None
    return normalized, str(candidate)


def _normalize_scope_output(
    scope: ScopeOutput,
    *,
    workspace_root: Path | None,
) -> ScopeOutput:
    merged: list[RepoSpec] = []
    index_by_key: dict[tuple[str, str, str], int] = {}

    for spec in scope.repos:
        normalized_spec = spec.model_copy(deep=True)
        normalized_name = normalized_spec.name
        normalized_local_path = normalized_spec.local_path
        original_local_path = normalized_spec.local_path
        extra_relevance = ""

        repo_root = _find_git_repo_root(workspace_root, normalized_spec.local_path)
        if repo_root is not None:
            normalized_local_path, absolute_path = repo_root
            normalized_name = Path(normalized_local_path).name
            try:
                subpath = str(Path(absolute_path).relative_to(workspace_root / normalized_local_path))
            except Exception:
                subpath = original_local_path
            if subpath and subpath not in (".", normalized_local_path):
                extra_relevance = (
                    f"Relevant subpath: `{subpath}` inside `{normalized_name}`. "
                    f"{normalized_spec.relevance}".strip()
                )

        normalized_spec = normalized_spec.model_copy(
            update={
                "name": normalized_name,
                "local_path": normalized_local_path,
                "relevance": extra_relevance or normalized_spec.relevance,
            }
        )

        key = (
            normalized_spec.name,
            normalized_spec.local_path,
            normalized_spec.github_url,
        )
        existing_idx = index_by_key.get(key)
        if existing_idx is None:
            index_by_key[key] = len(merged)
            merged.append(normalized_spec)
            continue

        existing = merged[existing_idx]
        merged_relevance = existing.relevance
        if normalized_spec.relevance and normalized_spec.relevance not in merged_relevance:
            merged_relevance = (
                f"{merged_relevance}\n\n{normalized_spec.relevance}".strip()
                if merged_relevance
                else normalized_spec.relevance
            )
        merged[existing_idx] = existing.model_copy(
            update={
                "action": existing.action
                if _action_rank(existing.action) >= _action_rank(normalized_spec.action)
                else normalized_spec.action,
                "relevance": merged_relevance,
            }
        )

    return scope.model_copy(update={"repos": merged})


async def _finalize_scoping_outputs(
    runner: WorkflowRunner,
    feature: Feature,
    state: BuildState,
    *,
    scope_model: ScopeOutput,
    ensure_project: bool,
) -> str:
    workspace_mgr = runner.services.get("workspace_manager")
    workspace_root = getattr(workspace_mgr, "_base", None)
    normalized_scope = _normalize_scope_output(scope_model, workspace_root=workspace_root)
    scope_text = to_markdown(normalized_scope)

    await runner.artifacts.put("scope", scope_text, feature=feature)
    await runner.artifacts.put(_SCOPE_DRAFT_KEY, normalized_scope.model_dump_json(indent=2), feature=feature)
    await runner.artifacts.put(_SCOPE_APPROVED_KEY, "approved", feature=feature)
    state.scope = scope_text

    hosting = runner.services.get("hosting")
    if hosting:
        await hosting.update(feature.id, "scope", scope_text)

    control = load_planning_control(state=state, feature=feature)
    await refresh_decision_ledger(
        runner,
        feature,
        ledger_key="decisions:broad",
        label="Broad Decision Ledger",
        source_phase="scoping",
        artifact_kind="scope",
        state=state,
        control=control,
        statements=normalized_scope.user_decisions,
    )

    if workspace_mgr and ensure_project:
        project_ctx = await workspace_mgr.setup_feature_workspace(feature, normalized_scope)
        await runner.artifacts.put(
            "project",
            project_ctx.model_dump_json(indent=2),
            feature=feature,
        )

    return scope_text


class ScopingPhase(Phase):
    name = "scoping"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        approved_marker = await runner.artifacts.get(_SCOPE_APPROVED_KEY, feature=feature)
        scope_text = await runner.artifacts.get("scope", feature=feature) or state.scope or ""
        structured_scope = await _load_structured_scope_draft(runner, feature)
        project_ctx = await _load_project_context(runner, feature)

        if approved_marker:
            scope_model = structured_scope
            if scope_model is None and scope_text:
                scope_model = await _recover_scope_model(
                    runner,
                    feature,
                    approved_scope_text=scope_text,
                )
            if scope_model is not None:
                await _finalize_scoping_outputs(
                    runner,
                    feature,
                    state,
                    scope_model=scope_model,
                    ensure_project=project_ctx is None,
                )
            else:
                state.scope = scope_text
            return state

        draft_scope_text = scope_text
        if draft_scope_text:
            logger.info("Resuming scoping from existing draft scope artifact")
            gate_input: ScopeOutput | str = structured_scope or draft_scope_text
            scope = structured_scope or draft_scope_text
        else:
            envelope: Envelope[ScopeOutput] = await runner.run(
                HostedInterview(
                    questioner=scoper,
                    responder=user,
                    initial_prompt=(
                        f"I'm going to scope this feature: {feature.name}\n\n"
                        "I'll ask a few focused questions about which repos are involved, "
                        "what type of change this is, and what's out of scope."
                    ),
                    output_type=Envelope[ScopeOutput],
                    done=envelope_done,
                    artifact_key="scope",
                    artifact_label="Feature Scope",
                ),
                feature,
                phase_name=self.name,
            )
            scope = envelope.output
            gate_input = scope
            await runner.artifacts.put(
                _SCOPE_DRAFT_KEY,
                scope.model_dump_json(indent=2),
                feature=feature,
            )

        scope, scope_text = await gate_and_revise(
            runner, feature, self.name,
            artifact=gate_input, actor=scoper, output_type=ScopeOutput,
            approver=user, label="Feature Scope",
            artifact_key="scope",
            hosted_revision=True,
            prefer_structured_output=True,
        )
        scope_model = (
            scope
            if isinstance(scope, ScopeOutput)
            else await _recover_scope_model(
                runner,
                feature,
                approved_scope_text=scope_text,
            )
        )
        if isinstance(scope_model, ScopeOutput):
            await _finalize_scoping_outputs(
                runner,
                feature,
                state,
                scope_model=scope_model,
                ensure_project=True,
            )
        else:
            await runner.artifacts.put("scope", scope_text, feature=feature)
            await runner.artifacts.put(_SCOPE_APPROVED_KEY, "approved", feature=feature)
            state.scope = scope_text

        return state
