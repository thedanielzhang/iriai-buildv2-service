"""Findings -> enhancement backlog.

Only a genuine ``regression`` becomes a finding (``intended_change``/``flaky``
resolve as provenance updates/quarantine). Non-critical regressions are appended
to the durable ``enhancement-backlog`` artifact — the SAME format the end-of-DAG
``_run_enhancement_group`` consumes — deduped by description and tagged with
severity + failed AC-ids. CRITICAL regressions (and boot-smoke failures) do NOT
go to the default backlog; they page the operator via ``status.py``.

During STANDALONE PROOF this writes to a SCRATCH feature (separate DB), never the
live ``8ac124d6`` backlog. The decoupled append/dedupe here mirrors
``implementation._append_enhancements`` so the items are pickup-compatible.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from iriai_build_v2.models.outputs import EnhancementBacklog, EnhancementItem

from .models import E2ESpecRecord, E2EVerdictRecord
from .registry import ENHANCEMENT_BACKLOG_KEY

_WORD = re.compile(r"[a-z0-9]+")


def _text_overlap(a: str, b: str) -> float:
    """Word-level Jaccard overlap (mirrors implementation._text_overlap)."""
    wa = set(_WORD.findall(a.lower()))
    wb = set(_WORD.findall(b.lower()))
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _is_dupe(desc: str, existing: list[str], *, threshold: float = 0.5) -> bool:
    if desc in existing:
        return True
    return any(_text_overlap(desc, e) > threshold for e in existing)


@dataclass
class BridgeResult:
    appended: list[EnhancementItem] = field(default_factory=list)
    deduped: list[str] = field(default_factory=list)
    critical: list[E2EVerdictRecord] = field(default_factory=list)
    backlog_size: int = 0


class E2EBacklogCorruptError(RuntimeError):
    """A corrupt ``enhancement-backlog`` row was found by the e2e bridge.

    Raised (item-4, IRIAI_LEDGER_FAIL_LOUD) AFTER the corrupt row has been
    quarantined under ``enhancement-backlog-quarantine`` — the e2e pass fails
    loudly and non-destructively instead of dying on an anonymous
    ValidationError. The develop-side reader of the same key quiesces through
    its own typed path; both readers preserve the corrupt row for repair.
    """


def _ledger_fail_loud_enabled() -> bool:
    """Item-4 flag (IRIAI_LEDGER_FAIL_LOUD, default OFF = today's raw raise)."""
    import os

    return os.environ.get("IRIAI_LEDGER_FAIL_LOUD", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


async def _load_backlog_or_quarantine(registry: Any, raw: Any) -> EnhancementBacklog:
    """Parse the backlog row; on corruption (flag ON) quarantine + typed error.

    Flag OFF preserves today's behavior exactly: the parse error propagates
    raw (non-destructive crash). Flag ON: the corrupt row is copied to
    ``enhancement-backlog-quarantine`` (never overwritten in place) and a
    typed ``E2EBacklogCorruptError`` names the key and the repair path.
    """
    try:
        return _load_backlog(raw)
    except Exception as exc:  # noqa: BLE001 — both branches re-raise
        if not _ledger_fail_loud_enabled():
            raise
        raw_text = raw if isinstance(raw, str) else repr(raw)
        try:
            await registry.put_raw(
                f"{ENHANCEMENT_BACKLOG_KEY}-quarantine",
                {
                    "source_key": ENHANCEMENT_BACKLOG_KEY,
                    "parse_error": str(exc),
                    "row_length": len(raw_text),
                    "raw": raw_text,
                },
            )
        except Exception as quarantine_exc:  # noqa: BLE001
            raise E2EBacklogCorruptError(
                f"corrupt '{ENHANCEMENT_BACKLOG_KEY}' row ({exc}); quarantine "
                f"write ALSO failed ({quarantine_exc}) — repair the row "
                "manually before re-running the e2e pass"
            ) from exc
        raise E2EBacklogCorruptError(
            f"corrupt '{ENHANCEMENT_BACKLOG_KEY}' row ({exc}); the row was "
            f"quarantined at '{ENHANCEMENT_BACKLOG_KEY}-quarantine' and NOT "
            "overwritten — repair or delete it, then re-run the e2e pass"
        ) from exc


def _finding_description(
    verdict: E2EVerdictRecord, spec: E2ESpecRecord | None, checkpoint_label: str
) -> str:
    ac_ids = (spec.linked_ac_ids if spec else None) or verdict.changed_ac_ids or []
    ac_part = f"[{','.join(ac_ids)}] " if ac_ids else ""
    return (
        f"[e2e regression @ {checkpoint_label}] {ac_part}{verdict.summary}".strip()
    )


async def bridge_findings(
    registry: Any,
    verdicts: list[E2EVerdictRecord],
    specs_by_id: dict[str, E2ESpecRecord],
    *,
    checkpoint_label: str,
    severity: str = "minor",
) -> BridgeResult:
    """Append non-critical regressions to the backlog (deduped). Critical ones
    are returned for the operator page (handled by status.py), NOT backlogged."""
    raw = await registry.get_raw(ENHANCEMENT_BACKLOG_KEY)
    backlog = await _load_backlog_or_quarantine(registry, raw)
    existing = [it.description for it in backlog.items]
    result = BridgeResult()

    for v in verdicts:
        if v.status != "fail" or v.failure_class != "regression":
            continue  # only genuine regressions are findings
        spec = specs_by_id.get(v.spec_id)
        if v.critical:
            result.critical.append(v)
            continue
        desc = _finding_description(v, spec, checkpoint_label)
        if _is_dupe(desc, existing):
            result.deduped.append(desc)
            continue
        item = EnhancementItem(
            source="e2e_regression",
            severity=severity,
            description=desc,
            file=(spec.spec_path if spec else ""),
            category="e2e",
            task_context=checkpoint_label,
        )
        backlog.items.append(item)
        existing.append(desc)
        result.appended.append(item)

    if result.appended:
        await registry.put_raw(ENHANCEMENT_BACKLOG_KEY, backlog)
    result.backlog_size = len(backlog.items)
    return result


@dataclass
class LaneBuildFailure:
    lane: str
    error: str


async def bridge_build_failures(
    registry: Any,
    failures: list[LaneBuildFailure],
    *,
    checkpoint_label: str,
    severity: str = "major",
    source: str = "e2e_preview_build",
    category: str = "build",
    file: str = "",
) -> BridgeResult:
    """Append boot-smoke/preview-build failures to the backlog (deduped).

    A lane whose globalSetup production build (or webServer) fails to come up is
    a boot-smoke regression — exactly the mid-stream build break this subsystem
    exists to surface. Unlike a test-level regression it cannot be authored from
    an ``E2EVerdictRecord`` (zero specs ran), so this is a separate, narrow path.
    Boot-smoke failures ALSO page via ``status.page_critical``; this records the
    durable, end-of-DAG-pickup-compatible backlog item with the failing
    ``file`` + the exact build error. Findings sharing a root cause collapse via
    the same Jaccard dedupe as ``bridge_findings``.
    """
    raw = await registry.get_raw(ENHANCEMENT_BACKLOG_KEY)
    backlog = await _load_backlog_or_quarantine(registry, raw)
    existing = [it.description for it in backlog.items]
    result = BridgeResult()
    for f in failures:
        desc = (
            f"[e2e preview-build @ {checkpoint_label}] [build] {f.lane}: "
            f"{f.error}"
        ).strip()
        if _is_dupe(desc, existing):
            result.deduped.append(desc)
            continue
        item = EnhancementItem(
            source=source, severity=severity, description=desc, file=file,
            category=category, task_context=checkpoint_label,
        )
        backlog.items.append(item)
        existing.append(desc)
        result.appended.append(item)
    if result.appended:
        await registry.put_raw(ENHANCEMENT_BACKLOG_KEY, backlog)
    result.backlog_size = len(backlog.items)
    return result


def _load_backlog(raw: Any) -> EnhancementBacklog:
    if raw is None:
        return EnhancementBacklog(items=[])
    if isinstance(raw, str):
        return EnhancementBacklog.model_validate_json(raw)
    return EnhancementBacklog.model_validate(raw)
