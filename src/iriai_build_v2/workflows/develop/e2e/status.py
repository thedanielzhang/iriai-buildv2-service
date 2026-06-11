"""Operator visibility: e2e-status rollup, material-change Slack card, paging.

Writes the durable ``e2e-status`` artifact and posts a Slack Block Kit card ONLY
on material change (digest dedupe, mirroring the supervisor digest pattern) so it
never spams. CRITICAL events (boot-smoke failure / ``critical``-flagged
regression) write an ``e2e-blocker`` artifact and PAGE — a high-priority card that
is NOT subject to the material-change dedupe, so a real blocker can't be swallowed.

The poster is pluggable: a captured poster for standalone proof (no live-channel
noise), or a real ``SlackAdapter.post_blocks`` poster in production. The
``ControlPlaneSnapshot`` e2e section is intentionally NOT wired here — that edits
``execution/snapshots.py`` which is a gated cutover step, out of scope for A–D.
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

from .models import E2EGreenPointer, E2EStatus, E2EVerdictRecord
from .registry import BLOCKER_KEY, STATUS_KEY

CARD_DIGEST_KEY = "e2e-status-card-digest"

Poster = Callable[[list[dict], str], Awaitable[None]]


class CapturingPoster:
    """Default poster for proof: records cards instead of hitting live Slack."""

    def __init__(self) -> None:
        self.cards: list[tuple[list[dict], str]] = []

    async def __call__(self, blocks: list[dict], text: str) -> None:
        self.cards.append((blocks, text))


def _agg_boot_smoke(smokes: list[Any]) -> str:
    statuses = [getattr(s, "status", "not_applicable") for s in smokes]
    if not statuses:
        return "not_applicable"
    if any(s == "fail" for s in statuses):
        return "fail"
    if any(s == "pass" for s in statuses):
        return "pass"
    return "not_applicable"


def build_status(
    *,
    checkpoint: Any,
    smokes: list[Any],
    verdicts: list[E2EVerdictRecord],
    green_pointer: E2EGreenPointer | None = None,
    preview_url: str = "",
    browser_lanes: str = "",
) -> E2EStatus:
    commits = checkpoint.result_commits() if checkpoint else {}
    passed = sum(1 for v in verdicts if v.status == "pass" and v.failure_class != "flaky")
    flaky = sum(1 for v in verdicts if v.failure_class == "flaky")
    failed = sum(
        1 for v in verdicts if v.status == "fail" and v.failure_class == "regression"
    )
    open_regressions = [
        v.spec_id for v in verdicts
        if v.status == "fail" and v.failure_class == "regression"
    ]
    return E2EStatus(
        latest_checkpoint=(f"group {checkpoint.group_idx}" if checkpoint else ""),
        latest_checkpoint_commit=(next(iter(commits.values()), "") if commits else ""),
        latest_green_checkpoint=(
            f"group {green_pointer.group_idx}" if green_pointer else ""
        ),
        boot_smoke=_agg_boot_smoke(smokes),
        passed=passed,
        failed=failed,
        flaky=flaky,
        open_regressions=open_regressions,
        preview_url=preview_url,
        browser_lanes=browser_lanes,
    )


def material_digest(status: E2EStatus) -> str:
    fields = [
        status.latest_checkpoint_commit, status.boot_smoke, status.passed,
        status.failed, status.flaky, sorted(status.open_regressions),
        status.latest_green_checkpoint,
    ]
    # Item-11 G4: include browser_lanes ONLY when non-empty so the studio card
    # digest (and its dedupe history) is byte-for-byte unchanged.
    if getattr(status, "browser_lanes", ""):
        fields.append(status.browser_lanes)
    payload = "|".join(str(x) for x in fields)
    return hashlib.sha256(payload.encode()).hexdigest()


def status_blocks(status: E2EStatus) -> list[dict]:
    fields = [
        {"type": "mrkdwn", "text": f"*Latest:* {status.latest_checkpoint}"},
        {"type": "mrkdwn", "text": f"*Green:* {status.latest_green_checkpoint or '—'}"},
        {"type": "mrkdwn", "text": f"*Boot-smoke:* {status.boot_smoke}"},
        {"type": "mrkdwn",
         "text": f"*Pass/Fail/Flaky:* {status.passed}/{status.failed}/{status.flaky}"},
        {"type": "mrkdwn",
         "text": f"*Open regressions:* {len(status.open_regressions)}"},
        {"type": "mrkdwn", "text": f"*Preview:* {status.preview_url or '—'}"},
    ]
    # Item-11 G4: shown ONLY when set, so the studio card layout is unchanged.
    if getattr(status, "browser_lanes", ""):
        fields.append(
            {"type": "mrkdwn", "text": f"*Browser lanes:* {status.browser_lanes}"})
    return [
        {"type": "header",
         "text": {"type": "plain_text", "text": "e2e status"}},
        {"type": "section", "fields": fields},
    ]


def blocker_blocks(*, title: str, detail: str, checkpoint_label: str) -> list[dict]:
    return [
        {"type": "header",
         "text": {"type": "plain_text", "text": f":rotating_light: e2e BLOCKER — {title}"}},
        {"type": "section", "text": {"type": "mrkdwn",
         "text": f"*Checkpoint:* {checkpoint_label}\n{detail}"}},
    ]


async def emit_status(
    registry: Any, status: E2EStatus, *, poster: Poster, force: bool = False
) -> bool:
    """Write e2e-status; post a card only on material change. Returns posted?."""
    await registry.put_status(status)
    digest = material_digest(status)
    last = await registry.get_raw(CARD_DIGEST_KEY)
    last_digest = last if isinstance(last, str) else (last or {}).get("digest") if last else None
    if not force and last_digest == digest:
        return False
    await poster(status_blocks(status), f"e2e status: {status.latest_checkpoint}")
    await registry.put_raw(CARD_DIGEST_KEY, {"digest": digest})
    return True


async def page_critical(
    registry: Any,
    *,
    poster: Poster,
    checkpoint_label: str,
    critical_regressions: list[E2EVerdictRecord] | None = None,
    boot_smoke_failures: list[Any] | None = None,
) -> int:
    """Write e2e-blocker + send NON-deduped page card(s). Returns count paged."""
    critical_regressions = critical_regressions or []
    boot_smoke_failures = boot_smoke_failures or []
    paged = 0
    blockers: list[dict] = []
    for v in critical_regressions:
        title = f"critical regression {v.spec_id}"
        detail = v.summary or "critical-flagged regression"
        await poster(blocker_blocks(title=title, detail=detail,
                                    checkpoint_label=checkpoint_label),
                     f"e2e BLOCKER: {title}")
        blockers.append({"kind": "critical_regression", "spec_id": v.spec_id,
                         "summary": v.summary})
        paged += 1
    for bs in boot_smoke_failures:
        title = f"boot-smoke failure ({getattr(bs, 'surface', '')})"
        detail = getattr(bs, "detail", "") or "boot-smoke failed"
        await poster(blocker_blocks(title=title, detail=detail,
                                    checkpoint_label=checkpoint_label),
                     f"e2e BLOCKER: {title}")
        blockers.append({"kind": "boot_smoke", "surface": getattr(bs, "surface", ""),
                         "detail": detail})
        paged += 1
    if blockers:
        await registry.put_raw(BLOCKER_KEY,
                               {"checkpoint": checkpoint_label, "blockers": blockers})
    return paged


def green_pointer_for(
    checkpoint: Any, *, boot_smoke: str, open_critical_regressions: int
) -> E2EGreenPointer | None:
    """Green = boot-smoke pass + no open CRITICAL regressions (matches alert tier).

    NOT "zero failures ever" — deferred non-critical items must not make
    latest-green perpetually empty.
    """
    if boot_smoke == "pass" and open_critical_regressions == 0 and checkpoint:
        return E2EGreenPointer(
            group_idx=checkpoint.group_idx,
            result_commits=checkpoint.result_commits(),
        )
    return None
