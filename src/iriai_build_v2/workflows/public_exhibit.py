from __future__ import annotations

import hashlib
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

from iriai_compose import AgentActor, Ask, Feature, WorkflowRunner
from iriai_compose.actors import Role
from pydantic import BaseModel, Field

from ..config import BUDGET_TIERS
from ..public_dashboard import enqueue_public_display_jobs

logger = logging.getLogger(__name__)

PUBLIC_DISPLAY_BASE_JOBS = (
    "public-summary",
    "public-artifact-gallery",
    "public-workstream-summary",
)
PUBLIC_DISPLAY_DAG_JOBS = (
    "public-summary",
    "public-dag-narrative",
    "public-artifact-gallery",
    "public-workstream-summary",
)
PUBLIC_DISPLAY_GROUP_JOBS = (
    "public-summary",
    "public-milestone-feed",
    "public-agent-round-summary",
    "public-current-implementation",
    "public-walkthrough-report",
)

_FORBIDDEN_PUBLIC_PATTERNS = [
    re.compile(r"/Users/[^\s`\"')]+"),
    re.compile(r"/private/(?:var|tmp)/[^\s`\"')]+"),
    re.compile(r"/var/folders/[^\s`\"')]+"),
    re.compile(r"(?<![\w.-])\.iriai(?:/|\b)"),
    re.compile(r"(?<![\w.-])\.iriai-context(?:/|\b)"),
    re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
]


class PublicSummaryDraft(BaseModel):
    title: str = ""
    tagline: str = ""
    description: str = ""
    phase_label: str = ""
    status_label: str = ""
    progress_narrative: str = ""
    current_focus: str = ""
    next_checkpoint: str = ""
    health: str = "running"


class PublicMilestoneDraft(BaseModel):
    title: str = ""
    summary: str = ""
    kind: str = "milestone"


class PublicAgentRoundSummaryDraft(BaseModel):
    summary: str = ""
    active_roles: list[str] = Field(default_factory=list)
    notable_handoffs: list[str] = Field(default_factory=list)


class PublicDagNarrativeDraft(BaseModel):
    narrative: str = ""


class PublicArtifactGalleryCardDraft(BaseModel):
    key: str
    title: str = ""
    family: str = ""
    summary: str = ""
    status: str = "available"


class PublicWorkstreamCardDraft(BaseModel):
    id: str
    summary: str = ""
    status: str = ""


class PublicNarrativeBundle(BaseModel):
    public_summary: PublicSummaryDraft = Field(default_factory=PublicSummaryDraft)
    milestone: PublicMilestoneDraft = Field(default_factory=PublicMilestoneDraft)
    agent_round_summary: PublicAgentRoundSummaryDraft = Field(default_factory=PublicAgentRoundSummaryDraft)
    dag_narrative: PublicDagNarrativeDraft = Field(default_factory=PublicDagNarrativeDraft)
    artifact_gallery_cards: list[PublicArtifactGalleryCardDraft] = Field(default_factory=list)
    workstream_cards: list[PublicWorkstreamCardDraft] = Field(default_factory=list)
    workstream_summary: str = ""


_public_exhibit_role = Role(
    name="public-exhibit-narrator",
    prompt=(
        "You are a public-facing narrator for an autonomous multi-agent software delivery workflow. "
        "Write concise, polished, non-hypey exhibit copy that helps an outside visitor understand "
        "what is being built, what just happened, what agents are doing, and what artifact evidence exists. "
        "Use only the provided source excerpts. Do not invent progress. Do not include local filesystem paths, "
        ".iriai internals, secrets, raw logs, or private account details. Keep the copy suitable for a public product demo."
    ),
    tools=[],
    model=BUDGET_TIERS["haiku"],
)

_public_exhibit_actor = AgentActor(
    name="public-exhibit-narrator",
    role=_public_exhibit_role,
    context_keys=[],
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _is_public_safe(value: Any) -> bool:
    text = _text(value)
    if len(text) > 20_000:
        return False
    return not any(pattern.search(text) for pattern in _FORBIDDEN_PUBLIC_PATTERNS)


def _assert_public_safe(payload: dict[str, Any]) -> None:
    raw = json.dumps(payload, default=str)
    if not _is_public_safe(raw):
        raise ValueError("public narrative failed deterministic public-safety scan")


def _feature_name(feature: Feature) -> str:
    return str(getattr(feature, "name", "") or feature.id)


def _summary_has_content(raw: str | None) -> bool:
    if not raw:
        return False
    try:
        parsed = json.loads(raw)
    except Exception:
        return False
    if isinstance(parsed, dict) and isinstance(parsed.get("content"), dict):
        parsed = parsed["content"]
    if not isinstance(parsed, dict):
        return False
    return bool(
        str(parsed.get("title") or "").strip()
        and str(parsed.get("description") or "").strip()
        and str(parsed.get("current_focus") or "").strip()
    )


def _fallback_public_summary(
    *,
    feature: Feature,
    reason: str,
    source_snapshot: dict[str, str],
) -> PublicSummaryDraft:
    source_keys = set(source_snapshot)
    if "dag" in source_keys:
        phase_label = "Implementation"
        status_label = "DAG execution in progress"
        progress = "The bridge has planned this feature as an implementation DAG and is checkpointing batches through verification."
        current_focus = "Executing and verifying implementation batches."
        next_checkpoint = "The next DAG batch that passes final verification."
    elif source_keys.intersection({"decomposition", "plan", "plan:broad"}):
        phase_label = "Task planning"
        status_label = "Execution plan being shaped"
        progress = "The bridge has planning artifacts available and is turning them into executable workstreams and task slices."
        current_focus = "Preparing the implementation plan and artifact trail."
        next_checkpoint = "Root DAG approval."
    elif source_keys.intersection({"prd", "prd:broad", "design", "design:broad"}):
        phase_label = "Planning"
        status_label = "Product and design artifacts available"
        progress = "The bridge is building a public narrative from approved product, design, and planning artifacts."
        current_focus = "Synthesizing the current public workflow story."
        next_checkpoint = "The next approved planning artifact."
    else:
        phase_label = "Starting"
        status_label = "Workflow initializing"
        progress = "The bridge is preparing a public summary as canonical workflow artifacts become available."
        current_focus = "Collecting the first public-safe workflow artifacts."
        next_checkpoint = "The first approved source artifact."

    return PublicSummaryDraft(
        title=_feature_name(feature),
        tagline="A live multi-agent software delivery exhibit.",
        description=(
            "This page presents public-safe progress from the bridge: what is being built, "
            "what agents are working on, and which artifacts prove the workflow state."
        ),
        phase_label=phase_label,
        status_label=status_label,
        progress_narrative=progress,
        current_focus=current_focus,
        next_checkpoint=next_checkpoint,
        health="running",
    )


def _ensure_summary_defaults(
    summary: PublicSummaryDraft,
    *,
    feature: Feature,
    reason: str,
    source_snapshot: dict[str, str],
) -> PublicSummaryDraft:
    fallback = _fallback_public_summary(
        feature=feature,
        reason=reason,
        source_snapshot=source_snapshot,
    )
    return PublicSummaryDraft(
        title=summary.title or fallback.title,
        tagline=summary.tagline or fallback.tagline,
        description=summary.description or fallback.description,
        phase_label=summary.phase_label or fallback.phase_label,
        status_label=summary.status_label or fallback.status_label,
        progress_narrative=summary.progress_narrative or fallback.progress_narrative,
        current_focus=summary.current_focus or fallback.current_focus,
        next_checkpoint=summary.next_checkpoint or fallback.next_checkpoint,
        health=summary.health or fallback.health,
    )


def _wrap_public_artifact(
    *,
    content: Any,
    reason: str,
    source_digests: dict[str, str],
    source_artifact_keys: list[str],
) -> dict[str, Any]:
    payload = {
        "content": content,
        "provenance": {
            "source_artifact_keys": source_artifact_keys,
            "source_digests": source_digests,
            "generated_at": _utc_now(),
            "generation_reason": reason,
            "runtime": "bridge",
            "model": _public_exhibit_role.model,
        },
        "public_safety": {
            "status": "passed",
            "checked_at": _utc_now(),
            "rules": ["no-local-paths", "no-iriai-internals", "no-secrets", "max-string-size"],
        },
        "canonical": False,
    }
    _assert_public_safe(payload)
    return payload


async def _write_public_summary(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    summary: PublicSummaryDraft,
    reason: str,
    source_digests: dict[str, str],
    source_artifact_keys: list[str],
) -> None:
    await runner.artifacts.put(
        "public-summary",
        json.dumps(_wrap_public_artifact(
            content=summary.model_dump(),
            reason=reason,
            source_digests=source_digests,
            source_artifact_keys=source_artifact_keys,
        ), indent=2),
        feature=feature,
    )


async def _load_source_snapshot(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    group_idx: int | None,
) -> tuple[dict[str, str], dict[str, str]]:
    keys = [
        "project",
        "scope",
        "prd",
        "prd:broad",
        "design",
        "design:broad",
        "plan",
        "plan:broad",
        "system-design",
        "system-design:broad",
        "test-plan",
        "test-plan:broad",
        "decomposition",
        "dag:strategy",
        "dag",
        "implementation",
        "handover",
    ]
    if group_idx is not None:
        keys.extend([
            f"dag-group:{group_idx}",
            f"dag-verify:g{group_idx}:initial",
            f"dag-fix:g{group_idx}:retry-0",
        ])

    snapshot: dict[str, str] = {}
    digests: dict[str, str] = {}
    for key in keys:
        try:
            value = await runner.artifacts.get(key, feature=feature)
        except Exception:
            value = None
        if not value:
            continue
        text = _text(value)
        snapshot[key] = text[:12_000]
        digests[key] = _digest(text)
    return snapshot, digests


def _build_prompt(
    *,
    feature: Feature,
    reason: str,
    group_idx: int | None,
    source_snapshot: dict[str, str],
) -> str:
    source_sections = []
    for key, value in source_snapshot.items():
        source_sections.append(f"## Source artifact: {key}\n\n```text\n{value}\n```")
    group_line = f"DAG group: {group_idx}" if group_idx is not None else "DAG group: not applicable"
    return (
        f"Feature id: {feature.id}\n"
        f"Feature name: {getattr(feature, 'name', feature.id)}\n"
        f"Generation reason: {reason}\n"
        f"{group_line}\n\n"
        "Create a public exhibit narrative bundle. Keep each field concise and public-safe. "
        "The dashboard will combine this copy with deterministic progress numbers, so do not include raw counts "
        "unless the source explicitly provides them. If a field is not supported by the evidence, leave it empty.\n\n"
        + "\n\n".join(source_sections)
    )


async def refresh_public_exhibit_narratives(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    reason: str,
    group_idx: int | None = None,
    summary_required: bool = False,
) -> None:
    """Best-effort bridge-side generation of public presentation artifacts.

    These artifacts are presentation cache only. Failure is intentionally
    non-blocking so public exhibit generation cannot destabilize the core
    workflow.
    """
    if os.environ.get("IRIAI_PUBLIC_EXHIBIT_NARRATIVES", "1") == "0":
        return

    source_snapshot: dict[str, str] = {}
    source_digests: dict[str, str] = {}
    try:
        source_snapshot, source_digests = await _load_source_snapshot(
            runner,
            feature,
            group_idx=group_idx,
        )
        if not source_snapshot:
            if summary_required:
                await _write_public_summary(
                    runner,
                    feature,
                    summary=_fallback_public_summary(
                        feature=feature,
                        reason=reason,
                        source_snapshot={},
                    ),
                    reason=reason,
                    source_digests={},
                    source_artifact_keys=[],
                )
            return
        timeout_seconds = int(os.environ.get("IRIAI_PUBLIC_EXHIBIT_TIMEOUT_SECONDS", "180"))
        bundle = await asyncio.wait_for(
            runner.resolve(
                Ask(
                    actor=_public_exhibit_actor,
                    prompt=_build_prompt(
                        feature=feature,
                        reason=reason,
                        group_idx=group_idx,
                        source_snapshot=source_snapshot,
                    ),
                    output_type=PublicNarrativeBundle,
                ),
                feature,
                phase_name="public_exhibit",
            ),
            timeout=timeout_seconds,
        )
        if not isinstance(bundle, PublicNarrativeBundle):
            bundle = PublicNarrativeBundle.model_validate(bundle)

        bundle.public_summary = _ensure_summary_defaults(
            bundle.public_summary,
            feature=feature,
            reason=reason,
            source_snapshot=source_snapshot,
        )

        source_keys = list(source_snapshot)
        await _write_public_summary(
            runner,
            feature,
            summary=bundle.public_summary,
            reason=reason,
            source_digests=source_digests,
            source_artifact_keys=source_keys,
        )

        if bundle.dag_narrative.narrative:
            await runner.artifacts.put(
                "public-dag-narrative",
                json.dumps(_wrap_public_artifact(
                    content=bundle.dag_narrative.model_dump(),
                    reason=reason,
                    source_digests=source_digests,
                    source_artifact_keys=source_keys,
                ), indent=2),
                feature=feature,
            )

        if bundle.agent_round_summary.summary and group_idx is not None:
            await runner.artifacts.put(
                f"public-agent-round-summary:g{group_idx}",
                json.dumps(_wrap_public_artifact(
                    content=bundle.agent_round_summary.model_dump(),
                    reason=reason,
                    source_digests=source_digests,
                    source_artifact_keys=source_keys,
                ), indent=2),
                feature=feature,
            )

        if bundle.artifact_gallery_cards:
            await runner.artifacts.put(
                "public-artifact-gallery",
                json.dumps(_wrap_public_artifact(
                    content={"cards": [card.model_dump() for card in bundle.artifact_gallery_cards]},
                    reason=reason,
                    source_digests=source_digests,
                    source_artifact_keys=source_keys,
                ), indent=2),
                feature=feature,
            )

        if bundle.workstream_summary or bundle.workstream_cards:
            await runner.artifacts.put(
                "public-workstream-summary",
                json.dumps(_wrap_public_artifact(
                    content={
                        "summary": bundle.workstream_summary,
                        "workstreams": [card.model_dump() for card in bundle.workstream_cards],
                    },
                    reason=reason,
                    source_digests=source_digests,
                    source_artifact_keys=source_keys,
                ), indent=2),
                feature=feature,
            )

        if bundle.milestone.title or bundle.milestone.summary:
            existing = await runner.artifacts.get("public-milestone-feed", feature=feature)
            milestones: list[dict[str, Any]] = []
            if existing:
                try:
                    parsed = json.loads(existing)
                    content = parsed.get("content") if isinstance(parsed, dict) else parsed
                    if isinstance(content, dict) and isinstance(content.get("milestones"), list):
                        milestones = [item for item in content["milestones"] if isinstance(item, dict)]
                except Exception:
                    milestones = []
            next_milestone = bundle.milestone.model_dump()
            next_milestone["created_at"] = _utc_now()
            next_milestone["source"] = reason
            milestones.insert(0, next_milestone)
            await runner.artifacts.put(
                "public-milestone-feed",
                json.dumps(_wrap_public_artifact(
                    content={"milestones": milestones[:50]},
                    reason=reason,
                    source_digests=source_digests,
                    source_artifact_keys=source_keys,
                ), indent=2),
                feature=feature,
            )
    except Exception as exc:
        logger.warning("Public exhibit narrative refresh failed", exc_info=True)
        if summary_required:
            try:
                existing = await runner.artifacts.get("public-summary", feature=feature)
            except Exception:
                existing = None
            if not _summary_has_content(existing):
                try:
                    await _write_public_summary(
                        runner,
                        feature,
                        summary=_fallback_public_summary(
                            feature=feature,
                            reason=reason,
                            source_snapshot=source_snapshot,
                        ),
                        reason=f"{reason}-fallback",
                        source_digests=source_digests,
                        source_artifact_keys=list(source_snapshot),
                    )
                except Exception:
                    logger.warning("Failed to persist fallback public summary", exc_info=True)
        key = f"public-summary-error:{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        try:
            await runner.artifacts.put(
                key,
                json.dumps({
                    "reason": reason,
                    "group_idx": group_idx,
                    "error": str(exc),
                    "created_at": _utc_now(),
                }, indent=2),
                feature=feature,
            )
        except Exception:
            logger.warning("Failed to persist public exhibit error artifact", exc_info=True)


async def ensure_public_summary_narrative(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    reason: str = "missing-public-summary",
) -> None:
    """Ensure the public exhibit has at least one summary artifact.

    This is intentionally bridge-side and best-effort; the dashboard still
    remains deterministic and never asks a model on page load.
    """
    if os.environ.get("IRIAI_PUBLIC_EXHIBIT_NARRATIVES", "1") == "0":
        return
    try:
        existing = await runner.artifacts.get("public-summary", feature=feature)
    except Exception:
        existing = None
    if _summary_has_content(existing) and _is_public_safe(existing):
        return
    await refresh_public_exhibit_narratives(
        runner,
        feature,
        reason=reason,
        summary_required=True,
    )


async def ensure_public_summary_fallback(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    reason: str = "missing-public-summary",
) -> None:
    """Persist a deterministic public summary if none exists.

    Unlike ``ensure_public_summary_narrative`` this helper never invokes an
    agent. It gives the public dashboard a safe, immediate baseline while the
    richer bridge-authored display jobs run asynchronously through the outbox.
    """
    try:
        existing = await runner.artifacts.get("public-summary", feature=feature)
    except Exception:
        existing = None
    if _summary_has_content(existing) and _is_public_safe(existing):
        return

    source_snapshot, source_digests = await _load_source_snapshot(
        runner,
        feature,
        group_idx=None,
    )
    await _write_public_summary(
        runner,
        feature,
        summary=_fallback_public_summary(
            feature=feature,
            reason=reason,
            source_snapshot=source_snapshot,
        ),
        reason=reason,
        source_digests=source_digests,
        source_artifact_keys=list(source_snapshot),
    )


async def enqueue_public_exhibit_refresh(
    runner: WorkflowRunner,
    feature: Feature,
    *,
    reason: str,
    group_idx: int | None = None,
    source_artifact_keys: list[str] | tuple[str, ...] | None = None,
    job_types: list[str] | tuple[str, ...] | None = None,
    priority: int = 100,
) -> list[str]:
    """Queue non-blocking public display generation work for the dashboard.

    Workflow-owned runtimes still generate the AI-authored summaries; this
    helper only records durable work items for an async worker/relay so feature
    progress never waits on public-presentation copy.
    """
    snapshot, digests = await _load_source_snapshot(
        runner,
        feature,
        group_idx=group_idx,
    )
    if source_artifact_keys is None:
        source_artifact_keys = tuple(snapshot)
    if job_types is None:
        if group_idx is not None:
            job_types = PUBLIC_DISPLAY_GROUP_JOBS
        elif "dag" in reason:
            job_types = PUBLIC_DISPLAY_DAG_JOBS
        else:
            job_types = PUBLIC_DISPLAY_BASE_JOBS
    return await enqueue_public_display_jobs(
        runner,
        feature,
        reason=reason,
        job_types=job_types,
        group_idx=group_idx,
        source_artifact_keys=tuple(source_artifact_keys),
        source_digests={key: digests[key] for key in source_artifact_keys if key in digests},
        payload={
            "reason": reason,
            "group_idx": group_idx,
            "source_snapshot_keys": list(snapshot),
        },
        priority=priority,
    )
