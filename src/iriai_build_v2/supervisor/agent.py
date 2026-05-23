from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from .models import (
    ActionLevel,
    ArtifactEvidenceSummary,
    EvidencePacket,
    FailureClass,
    SupervisorAssessment,
    SupervisorEvidenceBundle,
    SupervisorInvestigationRequest,
    SupervisorSeedPacket,
)
from .tools import SupervisorEvidenceToolbox, artifact_summary

logger = logging.getLogger(__name__)

_SUPERVISOR_ROLE_PROMPT = """\
You are the iriai-build-v2 workflow supervisor.

You own the current status/root-cause assessment. The deterministic classifier
and Slack bridge may wake you up, but they are not the authority for your
operator-facing answer.

You have a read-only `supervisor-evidence` MCP server. Use it to investigate:
- get_current_snapshot: call this first for status/current-health questions.
- list_artifact_index: browse compact artifact pointers by prefix/key.
- get_artifact_detail/get_artifact_chunk: inspect exact cited artifacts.
- list_events, get_bridge_status/get_bridge_logs, probe_worktree, readonly_sql:
  gather timeline, process, worktree, or bounded feature-scoped SQL evidence.

Rules:
- MCP-only: do not run Bash, shell commands, local file reads, ripgrep, Python
  scripts, or repository inspection. Your only evidence source is the
  `supervisor-evidence` MCP server plus the operator question. If MCP evidence
  is insufficient or unavailable, return a degraded assessment instead of using
  shell/file tools.
- Ground every important claim in citations returned by the evidence tools.
- Separate facts from inference.
- Say the current action or next step.
- Do not produce generic encouragement or template language.
- For failure/root-cause/revision/update questions, do not answer with only
  live workflow liveness or cursor freshness. Include the concrete latest
  material failure reason, the most recent repair/update action, and whether
  that failure is still current or historical.
- For current status/health questions, lead with human-readable workflow state:
  what group/retry is doing, the specific error/contract/test/hook issue being
  addressed, what the most recent fix changed, and what is currently running.
  Artifact ids, event ids, and cursors are citations; do not make them the main
  content of the answer.
- If a failure is historical, still name the specific failure and what evidence
  superseded it. "Historical commit failures predate the cursor" is not enough.
- If the operator asks what failed, inspect current snapshot plus current-group
  verify/RCA/fix/authority artifacts before answering.
- For current status/health questions:
  1. Call `get_current_snapshot`.
  2. Use the snapshot's `latest_material_artifacts` and
     `recommended_detail_artifact_ids` as your starting index.
  3. Fetch at most three artifact details unless the user explicitly asks for a
     deep timeline/history.
  4. Do not call `readonly_sql` unless the user explicitly asks for database,
     SQL, schema, or historical forensic detail, or the snapshot/artifact tools
     return contradictory evidence that cannot be resolved otherwise.
  5. Answer from the best cited evidence available instead of doing open-ended
     exploration.
- Background digests should name the specific material change since the prior
  digest, or stay quiet.
- Do not claim you took an action unless tool evidence/action records say so.
- Keep the answer short enough for Slack.
- Return JSON only.
- Return:
  {"type":"assessment","assessment":{"status":"...","message":"...","facts":[],"inferences":[],"citations":[],"confidence":0.0,"recommended_action":"observe","proposed_action":null}}
- recommended_action MUST be exactly one of: "observe", "digest", "recommend",
  "act_guarded", or "stop/escalate". Put domain-specific next steps such as
  commit hygiene, stale metadata repair, or product repair in status/message,
  not in recommended_action.
- Use proposed_action only for guarded mutations or dry runs: "restart_bridge" or
  "supervisor_maintainer_dry_run". Leave it null for normal status/product repair.
- Evidence is append-only. Treat rows chronologically by id/created_at.
- Use `get_current_snapshot` as authoritative for live "current status/health"
  questions. Use historical artifacts for context, but do not let old rows
  override the current workflow snapshot.
- If the operator asks about a historical group, answer that history and include
  a short header noting the live group when it differs.
- Do not call the workflow currently blocked unless the latest material same-group
  evidence is itself a blocker.
"""
_SUPERVISOR_SESSION_METADATA = {
    "max_session_chars": 200_000,
    "keep_recent_messages": 12,
    "forbid_command_execution": True,
    "auto_approve_mcp_tools": False,
    "disable_shell_tools": True,
}
_SUPERVISOR_PROMPT_CHAR_LIMIT = 850_000
_SUPERVISOR_COMPACT_PROMPT_CHAR_LIMIT = 650_000
_DETAIL_PROMPT_BUDGET = 220_000
_COMPACT_DETAIL_PROMPT_BUDGET = 60_000
_MAX_SUMMARIES = 260
_COMPACT_MAX_SUMMARIES = 120
_DETAIL_EXCERPT_CHARS = 8_000


def _supervisor_role_metadata(
    *,
    feature_id: str | None,
    toolbox: SupervisorEvidenceToolbox | None,
) -> dict[str, Any]:
    from ..config import mcp_servers_for

    metadata: dict[str, Any] = dict(_SUPERVISOR_SESSION_METADATA)
    if _supervisor_codex_read_only_enabled():
        metadata["codex_read_only_shell"] = True
    mcp_servers = mcp_servers_for("supervisor-evidence")
    if mcp_servers:
        server = dict(mcp_servers["supervisor-evidence"])
        env = dict(server.get("env") or {})
        if feature_id:
            env["IRIAI_SUPERVISOR_FEATURE_ID"] = feature_id
        if toolbox is not None:
            dashboard_url = getattr(toolbox, "dashboard_url", None)
            worktree_roots = getattr(toolbox, "worktree_roots", None) or []
            forbidden_paths = getattr(toolbox, "forbidden_paths", None) or []
            if dashboard_url:
                env["IRIAI_DASHBOARD_BASE_URL"] = dashboard_url
            if worktree_roots:
                env["IRIAI_SUPERVISOR_WORKTREE_ROOTS"] = os.pathsep.join(
                    str(root) for root in worktree_roots
                )
            if forbidden_paths:
                env["IRIAI_SUPERVISOR_FORBIDDEN_PATHS"] = os.pathsep.join(
                    str(path) for path in forbidden_paths
                )
        server["env"] = env
        metadata["mcp_servers"] = {"supervisor-evidence": server}
    return metadata


def _supervisor_session_key(
    feature_id: str | None,
    *,
    session_epoch: str | None = None,
    session_scope: str | None = None,
) -> str:
    parts = ["workflow-supervisor"]
    epoch = _session_key_part(session_epoch)
    scope = _session_key_part(session_scope)
    if epoch:
        parts.append(epoch)
    if scope:
        parts.append(scope)
    if feature_id:
        parts.append(feature_id)
    return ":".join(parts)


def _session_key_part(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-") or "session"


def _runtime_is_shell_capable_codex(runtime: Any) -> bool:
    runtime_type = type(runtime)
    return (
        runtime_type.__name__ == "CodexAgentRuntime"
        and runtime_type.__module__.startswith("iriai_build_v2.runtimes")
    )


def _supervisor_codex_read_only_enabled() -> bool:
    return str(os.environ.get("IRIAI_SUPERVISOR_CODEX_READ_ONLY") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _host_preload_request(packet: EvidencePacket) -> SupervisorInvestigationRequest:
    """Build a bounded host-side evidence request for read-only Codex supervisor turns."""

    group_idx = packet.group_idx
    facts = dict(packet.facts or {})
    current = facts.get("current_workflow") if isinstance(facts.get("current_workflow"), dict) else {}
    latest_event_id = _safe_int(current.get("latest_event_id") or facts.get("next_event_cursor"))
    latest_artifact_id = _safe_int(
        current.get("latest_artifact_id")
        or facts.get("next_artifact_cursor")
        or facts.get("next_cursor")
    )
    artifact_ids = _artifact_ids_from_values(
        facts.get("failed_raw_artifacts"),
        facts.get("successful_verify_artifacts"),
        facts.get("checkpoint_artifacts"),
        facts.get("material_artifacts"),
        packet.citations,
    )
    if latest_artifact_id is not None:
        artifact_ids.append(latest_artifact_id)
    artifact_ids = _dedupe_ints(artifact_ids)[:12]
    prefixes: list[str] = []
    if group_idx is not None:
        prefixes.extend(
            [
                f"dag-verify:g{group_idx}:",
                f"dag-fix:g{group_idx}:",
                f"dag-verify-rca:g{group_idx}:",
                f"dag-repair-preflight:g{group_idx}:",
                f"dag-repair-expanded-verify:g{group_idx}:",
                f"dag-repair-lens:g{group_idx}:",
                f"dag-authority-gate:g{group_idx}:",
                f"dag-direct-repair-route:g{group_idx}:",
                f"dag-commit-failure:g{group_idx}:",
                f"dag-group:{group_idx}",
            ]
        )
    return SupervisorInvestigationRequest(
        reason="host-preloaded-current-supervisor-evidence",
        artifact_prefixes=prefixes,
        artifact_ids=artifact_ids,
        artifact_after_id=max(0, latest_artifact_id - 500) if latest_artifact_id is not None else None,
        event_after_id=max(0, latest_event_id - 80) if latest_event_id is not None else None,
        event_limit=80,
        include_bridge=True,
        include_worktrees=False,
    )


def _artifact_ids_from_values(*values: Any) -> list[int]:
    ids: list[int] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, int):
            ids.append(value)
            continue
        if isinstance(value, str):
            ids.extend(int(match) for match in re.findall(r"\bid=(\d+)\b", value))
            continue
        if isinstance(value, dict):
            ids.extend(_artifact_ids_from_values(*value.values()))
            continue
        if isinstance(value, (list, tuple, set)):
            ids.extend(_artifact_ids_from_values(*value))
    return ids


def _safe_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _dedupe_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


class SupervisorAgent:
    """Compose operator-facing supervisor messages from evidence packets.

    A real agent runtime can be provided for Slack-facing prose. The deterministic
    formatter remains the fallback for tests, runtime outages, and read-only dry
    runs where we still need reliable status text.
    """

    async def compose_message(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
        runtime: Any | None = None,
        feature_id: str | None = None,
        toolbox: SupervisorEvidenceToolbox | None = None,
        initial_bundles: list[SupervisorEvidenceBundle] | None = None,
        assessment_sink: Any | None = None,
        timeout_seconds: float | None = 90.0,
        session_epoch: str | None = None,
        session_scope: str | None = None,
    ) -> str:
        assessment, bundles, fallback = await self.assess(
            packet,
            question=question,
            runtime=runtime,
            feature_id=feature_id,
            toolbox=toolbox,
            initial_bundles=initial_bundles,
            timeout_seconds=timeout_seconds,
            session_epoch=session_epoch,
            session_scope=session_scope,
        )
        if assessment_sink is not None:
            await assessment_sink(assessment, bundles, fallback)
        return assessment.message

    async def assess(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
        runtime: Any | None = None,
        feature_id: str | None = None,
        toolbox: SupervisorEvidenceToolbox | None = None,
        initial_bundles: list[SupervisorEvidenceBundle] | None = None,
        timeout_seconds: float | None = 90.0,
        max_rounds: int = 3,
        session_epoch: str | None = None,
        session_scope: str | None = None,
    ) -> tuple[SupervisorAssessment, list[SupervisorEvidenceBundle], bool]:
        effective_feature_id = feature_id or packet.feature_id
        seed = SupervisorSeedPacket(
            feature_id=effective_feature_id,
            packet=packet,
        )
        if runtime is None:
            return (
                self.fallback_assessment(
                    packet,
                    question=question,
                    fallback_reason="runtime_absent",
                ),
                [],
                True,
            )

        runtime_is_codex = _runtime_is_shell_capable_codex(runtime)
        if runtime_is_codex and not _supervisor_codex_read_only_enabled():
            return (
                self.fallback_assessment(
                    packet,
                    question=question,
                    fallback_reason="evidence_only_no_shell_runtime",
                ),
                list(initial_bundles or []),
                True,
            )

        bundles: list[SupervisorEvidenceBundle] = list(initial_bundles or [])
        codex_read_only_mode = runtime_is_codex and _supervisor_codex_read_only_enabled()
        if codex_read_only_mode and toolbox is not None and not bundles:
            try:
                bundles.append(await toolbox.gather(_host_preload_request(packet)))
            except Exception:
                logger.debug("Failed to preload supervisor host evidence", exc_info=True)
        round_count = 0
        prompt_meta = _PromptMetadata()
        try:
            from iriai_compose import Role

            from ..config import BUDGET_TIERS

            role_prompt = _SUPERVISOR_ROLE_PROMPT
            if runtime_is_codex and _supervisor_codex_read_only_enabled():
                role_prompt = (
                    f"{role_prompt}\n\n"
                    "Runtime safety mode: Codex supervisor investigation is enabled "
                    "inside a local read-only sandbox. Use the `supervisor-evidence` "
                    "MCP server for evidence. Do not run Bash, shell commands, local "
                    "scripts, package installs, network calls, process control, "
                    "workflow mutation, git mutation, file writes, or edits."
                )

            role = Role(
                name="workflow-supervisor",
                prompt=role_prompt,
                tools=[],
                model=BUDGET_TIERS["haiku"],
                effort="low",
                metadata=_supervisor_role_metadata(
                    feature_id=effective_feature_id,
                    toolbox=toolbox,
                ),
            )
            session_key = _supervisor_session_key(
                effective_feature_id,
                session_epoch=session_epoch,
                session_scope=session_scope,
            )
            for _round in range(max_rounds):
                prompt, prompt_meta = _agent_prompt_with_metadata(
                    seed,
                    question,
                    bundles,
                    include_seed_evidence=codex_read_only_mode,
                )
                if len(prompt) > _SUPERVISOR_PROMPT_CHAR_LIMIT:
                    return (
                        self.fallback_assessment(
                            packet,
                            question=question,
                            fallback_reason="input_overflow",
                            prompt_meta=prompt_meta,
                            round_count=round_count,
                        ),
                        bundles,
                        True,
                    )
                round_count += 1
                invocation = runtime.invoke(role, prompt, session_key=session_key)
                if timeout_seconds is None or timeout_seconds <= 0:
                    response = await invocation
                else:
                    response = await asyncio.wait_for(
                        invocation,
                        timeout=timeout_seconds,
                    )
                parsed = _parse_agent_response(str(response or ""))
                if isinstance(parsed, SupervisorAssessment):
                    assessment = _guard_assessment_against_seed(packet, parsed)
                    return (
                        _with_prompt_metadata(
                            assessment,
                            prompt_meta=prompt_meta,
                            round_count=round_count,
                            evidence_mode=prompt_meta.evidence_mode,
                            tool_names_used=prompt_meta.tool_names_used,
                        ),
                        bundles,
                        False,
                    )
                if not parsed:
                    text = str(response or "").strip()
                    if text:
                        if _looks_like_json(text):
                            return (
                                self.fallback_assessment(
                                    packet,
                                    question=question,
                                    fallback_reason="parse_error",
                                    prompt_meta=prompt_meta,
                                    round_count=round_count,
                                ),
                                bundles,
                                True,
                            )
                        return (
                            _with_prompt_metadata(
                                _plain_text_assessment(packet, text),
                                prompt_meta=prompt_meta,
                                round_count=round_count,
                                evidence_mode=prompt_meta.evidence_mode,
                                tool_names_used=prompt_meta.tool_names_used,
                            ),
                            bundles,
                            False,
                        )
                    break
                if toolbox is None:
                    break
                new_bundles = await toolbox.gather_many(parsed[:3])
                bundles.extend(new_bundles)
        except asyncio.TimeoutError:
            logger.warning("Supervisor agent message composition timed out")
            return (
                self.fallback_assessment(
                    packet,
                    question=question,
                    fallback_reason="timeout",
                    prompt_meta=prompt_meta,
                    round_count=round_count,
                ),
                bundles,
                True,
            )
        except Exception as exc:
            logger.warning("Supervisor agent message composition failed: %s", exc)
            fallback_reason = _fallback_reason_from_exception(exc)
            return (
                self.fallback_assessment(
                    packet,
                    question=question,
                    fallback_reason=fallback_reason,
                    prompt_meta=prompt_meta,
                    round_count=round_count,
                ),
                bundles,
                True,
            )
        return (
            self.fallback_assessment(
                packet,
                question=question,
                fallback_reason="parse_error",
                prompt_meta=prompt_meta,
                round_count=round_count,
            ),
            bundles,
            True,
        )

    def fallback_assessment(
        self,
        packet: EvidencePacket,
        *,
        question: str | None = None,
        fallback_reason: str = "runtime_error",
        prompt_meta: "_PromptMetadata | None" = None,
        round_count: int = 0,
    ) -> SupervisorAssessment:
        citations = ", ".join(packet.citations[:4]) or "current evidence window"
        group = f"G{packet.group_idx}" if packet.group_idx is not None else "current group"
        retry = f" retry-{packet.retry}" if packet.retry is not None else ""
        prefix = (
            f"Supervisor degraded while answering: {question.strip()} "
            if question
            else "Supervisor degraded: "
        )
        message = (
            f"{prefix}agent investigation fell back because `{fallback_reason}`. "
            f"Do not treat this as an agent-authored assessment. Deterministic seed: "
            f"{group}{retry} classified as `{packet.classification.value}` "
            f"with {packet.confidence:.0%} confidence. Seed fact: {citations}. "
            f"Seed inference: {packet.inference}. Seed action: `{packet.recommended_action.value}`."
        )
        prompt_meta = prompt_meta or _PromptMetadata()
        evidence_mode = (
            f"{prompt_meta.evidence_mode}:degraded"
            if prompt_meta.prompt_chars
            else "fallback"
        )
        return SupervisorAssessment(
            status="supervisor_degraded",
            message=message,
            facts=[citations],
            inferences=[packet.inference],
            citations=packet.citations,
            confidence=packet.confidence,
            recommended_action=packet.recommended_action,
            fallback_reason=fallback_reason,
            prompt_chars=prompt_meta.prompt_chars,
            round_count=round_count,
            evidence_artifact_count=prompt_meta.evidence_artifact_count,
            evidence_summary_count=prompt_meta.evidence_summary_count,
            omitted_detail_refs=prompt_meta.omitted_detail_refs,
            evidence_mode=evidence_mode,
            tool_names_used=prompt_meta.tool_names_used,
        )

    def answer_status(self, packet: EvidencePacket, *, question: str | None = None) -> str:
        return self.fallback_assessment(packet, question=question).message


def _agent_prompt(
    seed: SupervisorSeedPacket,
    question: str | None,
    bundles: list[SupervisorEvidenceBundle],
) -> str:
    return _agent_prompt_with_metadata(seed, question, bundles)[0]


def _agent_prompt_with_metadata(
    seed: SupervisorSeedPacket,
    question: str | None,
    bundles: list[SupervisorEvidenceBundle],
    *,
    include_seed_evidence: bool = False,
) -> tuple[str, "_PromptMetadata"]:
    prompt_meta = _PromptMetadata(
        evidence_mode="mcp",
        tool_names_used=["supervisor-evidence"],
    )
    detail_guidance = ""
    if _question_requires_failure_detail(question):
        detail_guidance = (
            "## Required Detail For This Question\n"
            "The operator is asking for status/health/failure/root-cause/revision/update "
            "detail. Your final assessment must be human-readable and include: "
            "(1) live state, (2) the concrete latest material failure or issue being "
            "addressed, including paths/tests/hook output when present, (3) the most "
            "recent repair/update action and whether it superseded the failure, "
            "(4) what is actively running or waiting now, and (5) the next step. "
            "Use artifact/event ids only as citations, not as the main answer. Use "
            "the supervisor-evidence MCP tools or the host-preloaded evidence below "
            "to retrieve enough detail before returning an assessment.\n\n"
        )
    seed_evidence = ""
    if include_seed_evidence:
        prompt_meta.evidence_mode = "mcp+host_seed"
        seed_evidence = (
            "## Host Deterministic Seed JSON\n"
            "This bounded host evidence is safe to use if MCP tool calls are "
            "unavailable or cancelled. Do not answer that no evidence is available "
            "when this seed or host-preloaded evidence is sufficient for a current "
            "status digest.\n"
            f"{seed.model_dump_json()}\n\n"
        )
    legacy_evidence = ""
    if bundles:
        evidence_payload, legacy_meta = _evidence_prompt_payload(
            bundles,
            detail_budget=_COMPACT_DETAIL_PROMPT_BUDGET,
            max_summaries=_COMPACT_MAX_SUMMARIES,
        )
        prompt_meta.evidence_mode = (
            "mcp+host_preloaded" if include_seed_evidence else "mcp+legacy_request_evidence"
        )
        prompt_meta.evidence_artifact_count = legacy_meta.evidence_artifact_count
        prompt_meta.evidence_summary_count = legacy_meta.evidence_summary_count
        prompt_meta.omitted_detail_refs = legacy_meta.omitted_detail_refs
        evidence_title = (
            "Host Preloaded Evidence JSON"
            if include_seed_evidence
            else "Legacy Fallback Evidence JSON"
        )
        evidence_note = (
            "This bounded evidence was loaded by the supervisor host before the "
            "agent turn. Use it as current evidence; call MCP only if you need more "
            "detail and the tool call is available."
            if include_seed_evidence
            else "This exists only because the host serviced a legacy request_evidence "
            "response. Prefer MCP tools for any additional detail."
        )
        legacy_evidence = (
            f"## {evidence_title}\n"
            f"{evidence_note}\n"
            f"{json.dumps(evidence_payload, sort_keys=True, indent=2)}\n\n"
        )
    prompt = (
        "## Operator Question\n"
        f"{question or 'Provide a current workflow health/status digest.'}\n\n"
        f"{detail_guidance}"
        "## Supervisor Context\n"
        f"feature_id: {seed.feature_id}\n"
        f"current_time_utc: {seed.created_at.isoformat()}\n"
        "Use the `supervisor-evidence` MCP tools for evidence. For current status, "
        "call `get_current_snapshot` first, then inspect artifact/event indexes and "
        "exact artifact details/chunks as needed. If MCP tool calls are unavailable "
        "or cancelled, answer from the host deterministic seed and host-preloaded "
        "evidence instead of reporting that no evidence exists.\n\n"
        f"{seed_evidence}"
        f"{legacy_evidence}"
        "Return the final assessment JSON only after you have gathered enough evidence."
    )
    prompt_meta.prompt_chars = len(prompt)
    return prompt, prompt_meta


@dataclass
class _PromptMetadata:
    prompt_chars: int = 0
    evidence_artifact_count: int = 0
    evidence_summary_count: int = 0
    omitted_detail_refs: list[str] = field(default_factory=list)
    evidence_mode: str = "mcp"
    tool_names_used: list[str] = field(default_factory=lambda: ["supervisor-evidence"])


def _evidence_prompt_payload(
    bundles: list[SupervisorEvidenceBundle],
    *,
    detail_budget: int,
    max_summaries: int,
) -> tuple[list[dict[str, Any]], _PromptMetadata]:
    remaining_detail = detail_budget
    prompt_bundles: list[dict[str, Any]] = []
    meta = _PromptMetadata()
    for bundle in bundles:
        summaries = list(bundle.artifact_summaries)
        summaries.extend(artifact_summary(artifact) for artifact in bundle.artifacts)
        summaries = _dedupe_summaries(summaries)
        meta.evidence_artifact_count += len(bundle.artifacts)
        meta.evidence_summary_count += len(summaries)
        omitted = list(bundle.omitted_detail_refs)
        if len(summaries) > max_summaries:
            omitted.extend(summary.citation for summary in summaries[:-max_summaries])
            summaries = summaries[-max_summaries:]

        details: list[dict[str, Any]] = []
        for artifact in bundle.artifacts:
            text = _artifact_text(artifact.value)
            citation = artifact.citation
            if len(text) <= remaining_detail:
                details.append(
                    {
                        "id": artifact.id,
                        "key": artifact.key,
                        "citation": citation,
                        "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                        "sha256": artifact.sha256,
                        "value": artifact.value,
                    }
                )
                remaining_detail -= len(text)
                continue
            excerpt = text[:_DETAIL_EXCERPT_CHARS]
            chunk_count = max(1, (len(text) + 19_999) // 20_000)
            chunk_refs = [
                f"{artifact.id}:{idx}"
                for idx in range(chunk_count)
                if artifact.id is not None
            ][:25]
            details.append(
                {
                    "id": artifact.id,
                    "key": artifact.key,
                    "citation": citation,
                    "created_at": artifact.created_at.isoformat() if artifact.created_at else None,
                    "sha256": artifact.sha256,
                    "total_chars": len(text),
                    "excerpt": excerpt,
                    "truncated": True,
                    "chunk_refs": chunk_refs,
                }
            )
            omitted.append(citation)

        chunk_payload = [chunk.model_dump(mode="json") for chunk in bundle.artifact_chunks]
        prompt_bundles.append(
            {
                "request": bundle.request.model_dump(mode="json"),
                "artifact_index": [_summary_index_entry(summary) for summary in summaries],
                "artifact_details": details,
                "artifact_chunks": chunk_payload,
                "omitted_detail_refs": _dedupe_strings(omitted),
                "events": [event.model_dump(mode="json") for event in bundle.events],
                "bridge": _bridge_prompt_payload(bundle.bridge),
                "worktrees": [
                    _worktree_prompt_payload(worktree) for worktree in bundle.worktrees
                ],
                "sql_results": bundle.sql_results,
                "rejected_sql": bundle.rejected_sql,
                "errors": bundle.errors,
            }
        )
        meta.omitted_detail_refs.extend(omitted)
    meta.omitted_detail_refs = _dedupe_strings(meta.omitted_detail_refs)
    return prompt_bundles, meta


def _summary_index_entry(summary: ArtifactEvidenceSummary) -> dict[str, Any]:
    """Compact artifact pointer for broad evidence scans.

    Keep this deliberately index-shaped. Exact artifact ids/chunks are the
    retrieval path for raw summaries and full detail.
    """

    return {
        "id": summary.id,
        "key": summary.key,
        "citation": summary.citation,
        "created_at": summary.created_at.isoformat() if summary.created_at else None,
        "size_chars": summary.size_chars,
        "sha256": summary.sha256,
        "status": summary.status,
        "approved": summary.approved,
        "route": summary.route,
        "reason": summary.reason,
        "summary_preview": _shorten(summary.summary, 180),
        "concern_count": len(summary.concerns),
        "gap_count": len(summary.gaps),
        "path_count": len(summary.path_snippets),
        "path_samples": summary.path_snippets[:3],
        "chunk_refs": summary.chunk_refs[:5],
        "detail_available": summary.detail_available,
    }


def _bridge_prompt_payload(bridge: Any | None) -> dict[str, Any] | None:
    if bridge is None:
        return None
    return {
        "dashboard_url": bridge.dashboard_url,
        "ok": bridge.ok,
        "process_state": bridge.process_state,
        "status": bridge.status,
        "log_cursor": bridge.log_cursor,
        "recent_log_lines": list(bridge.log_lines[-40:]),
        "recent_errors": list(bridge.errors[-20:]),
    }


def _worktree_prompt_payload(worktree: Any) -> dict[str, Any]:
    return {
        "root": worktree.root,
        "ok": worktree.ok,
        "branch": worktree.branch,
        "dirty_count": len(worktree.dirty_paths),
        "embedded_git_count": len(worktree.embedded_git_paths),
        "gitlink_count": len(worktree.gitlinks),
        "forbidden_count": len(worktree.forbidden_paths),
        "pending_count": len(worktree.pending_paths),
        "proposed_count": len(worktree.proposed_paths),
        "unwritable_count": len(worktree.unwritable_paths),
        "dirty_samples": [item.model_dump(mode="json") for item in worktree.dirty_paths[:20]],
        "forbidden_samples": [
            item.model_dump(mode="json") for item in worktree.forbidden_paths[:20]
        ],
        "embedded_git_samples": worktree.embedded_git_paths[:20],
        "gitlink_samples": worktree.gitlinks[:20],
        "pending_samples": worktree.pending_paths[:20],
        "proposed_samples": worktree.proposed_paths[:20],
        "unwritable_samples": worktree.unwritable_paths[:20],
        "errors": worktree.errors[:10],
    }


_FAILURE_DETAIL_RE = re.compile(
    r"\b("
    r"status|current status|health|healthy|looking|running|progress|"
    r"failure|failed|fail|root cause|why|stuck|blocked|blocker|what happened|"
    r"what changed|recent update|most recent|revision|revisions|revise|cycle|"
    r"cycles|retry|retries|fix|fixed|repair|repaired"
    r")\b",
    re.IGNORECASE,
)


def _question_requires_failure_detail(question: str | None) -> bool:
    return bool(question and _FAILURE_DETAIL_RE.search(question))


def _parse_agent_response(
    text: str,
) -> SupervisorAssessment | list[SupervisorInvestigationRequest] | None:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "assessment" or "assessment" in payload:
        raw = payload.get("assessment", payload)
        try:
            return _assessment_from_payload(raw)
        except (TypeError, ValidationError):
            return None
    if payload.get("type") == "request_evidence" or "requests" in payload:
        requests = payload.get("requests") or []
        if not isinstance(requests, list):
            return []
        parsed: list[SupervisorInvestigationRequest] = []
        for item in requests[:3]:
            try:
                parsed.append(SupervisorInvestigationRequest.model_validate(item))
            except ValidationError:
                continue
        return parsed
    return None


def _plain_text_assessment(packet: EvidencePacket, text: str) -> SupervisorAssessment:
    return SupervisorAssessment(
        status=packet.classification.value,
        message=text,
        facts=[],
        inferences=[packet.inference],
        citations=packet.citations,
        confidence=packet.confidence,
        recommended_action=packet.recommended_action,
    )


def _with_prompt_metadata(
    assessment: SupervisorAssessment,
    *,
    prompt_meta: _PromptMetadata,
    round_count: int,
    evidence_mode: str | None = None,
    tool_names_used: list[str] | None = None,
) -> SupervisorAssessment:
    return assessment.model_copy(
        update={
            "prompt_chars": prompt_meta.prompt_chars,
            "round_count": round_count,
            "evidence_artifact_count": prompt_meta.evidence_artifact_count,
            "evidence_summary_count": prompt_meta.evidence_summary_count,
            "omitted_detail_refs": prompt_meta.omitted_detail_refs,
            "evidence_mode": evidence_mode or prompt_meta.evidence_mode,
            "tool_names_used": tool_names_used or prompt_meta.tool_names_used,
        }
    )


def _guard_assessment_against_seed(
    packet: EvidencePacket,
    assessment: SupervisorAssessment,
) -> SupervisorAssessment:
    deterministic_classes = {
        FailureClass.DETERMINISTIC_UNBLOCK,
        FailureClass.SAFE_RESTART_CANDIDATE,
        FailureClass.STALE_CODEX_INVOCATION,
        FailureClass.WATCH_ONLY,
    }
    if (
        packet.classification in deterministic_classes
        and packet.recommended_action != ActionLevel.STOP_ESCALATE
        and assessment.recommended_action == ActionLevel.STOP_ESCALATE
    ):
        return assessment.model_copy(
            update={
                "recommended_action": packet.recommended_action,
                "proposed_action": None,
                "inferences": [
                    *list(assessment.inferences),
                    (
                        "Agent escalation was capped to the deterministic seed "
                        f"action {packet.recommended_action.value!r}; workflow-class "
                        "unblocks do not escalate without typed operator-required evidence."
                    ),
                ],
                "confidence": min(assessment.confidence, packet.confidence),
            }
        )
    if packet.classification != FailureClass.HEALTHY_PROGRESS:
        return assessment
    text = f"{assessment.status} {assessment.message}".lower()
    commit_blocker_claim = "commit" in text and "block" in text
    cites_commit_failure = any("dag-commit-failure:" in citation for citation in assessment.citations)
    seed_success = bool(packet.facts.get("successful_verify_artifacts"))
    if not (commit_blocker_claim and cites_commit_failure and seed_success):
        return assessment
    seed_citations = list(packet.citations)
    return SupervisorAssessment(
        status=FailureClass.HEALTHY_PROGRESS.value,
        message=(
            "Workflow appears healthy based on the latest seed evidence. Older "
            "commit-failure artifacts were present in the investigation, but they "
            "are superseded by newer same-group progress/success evidence; continue "
            "observing unless a new commit failure appears."
        ),
        facts=list(assessment.facts) + [
            "Seed packet reported successful verify/progress evidence after the cited commit failures."
        ],
        inferences=list(assessment.inferences) + [
            "Historical commit-failure rows do not make the current run blocked when newer same-group success evidence exists."
        ],
        citations=seed_citations or assessment.citations,
        confidence=min(assessment.confidence, packet.confidence),
        recommended_action=packet.recommended_action,
        proposed_action=None,
    )


_ACTION_VALUES = {action.value for action in ActionLevel}


def _assessment_from_payload(raw: Any) -> SupervisorAssessment:
    if not isinstance(raw, dict):
        raise TypeError("assessment payload must be an object")
    payload = dict(raw)
    action = str(payload.get("recommended_action") or ActionLevel.OBSERVE.value)
    if action not in _ACTION_VALUES:
        payload["recommended_action"] = _normalize_recommended_action(action).value
        inferences = list(payload.get("inferences") or [])
        inferences.append(
            f"Agent returned non-standard recommended_action {action!r}; "
            f"normalized to {payload['recommended_action']!r}."
        )
        payload["inferences"] = inferences
    if payload.get("proposed_action") not in {
        None,
        "",
        "restart_bridge",
        "supervisor_maintainer_dry_run",
    }:
        payload["proposed_action"] = None
    return SupervisorAssessment.model_validate(payload)


def _normalize_recommended_action(action: str) -> ActionLevel:
    lowered = action.lower().strip().replace("-", "_")
    if any(token in lowered for token in ("stop", "escalate", "blocked")):
        return ActionLevel.STOP_ESCALATE
    if any(token in lowered for token in ("restart", "guarded", "act")):
        return ActionLevel.ACT_GUARDED
    if any(token in lowered for token in ("digest", "progress", "healthy")):
        return ActionLevel.DIGEST
    if any(token in lowered for token in ("repair", "fix", "recommend", "hygiene")):
        return ActionLevel.RECOMMEND
    return ActionLevel.OBSERVE


def _fallback_reason_from_exception(exc: Exception) -> str:
    text = str(exc).lower()
    if "input exceeds" in text or "maximum length" in text or "context length" in text:
        return "input_overflow"
    return "runtime_error"


def _artifact_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True, default=str)


def _shorten(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 24)]}... [truncated]"


def _dedupe_summaries(
    summaries: list[ArtifactEvidenceSummary],
) -> list[ArtifactEvidenceSummary]:
    seen: set[tuple[int | None, str]] = set()
    result: list[ArtifactEvidenceSummary] = []
    for summary in summaries:
        token = (summary.id, summary.key)
        if token in seen:
            continue
        seen.add(token)
        result.append(summary)
    return sorted(result, key=lambda item: item.id or 0)


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _looks_like_json(text: str) -> bool:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    return stripped.startswith(("{", "["))
