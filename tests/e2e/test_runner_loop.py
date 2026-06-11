"""Unit tests for AsyncE2ETrack poller: coalescing, cursor, preflight gating."""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.e2e import runner_loop as rl
from iriai_build_v2.workflows.develop.e2e.checkpoint import (
    RepoCheckpoint,
    SealedCheckpoint,
)
from iriai_build_v2.workflows.develop.e2e.models import E2ETrackCursor
from iriai_build_v2.workflows.develop.e2e.runner_loop import AsyncE2ETrack, host_preflight


class FakeRegistry:
    def __init__(self):
        self.cursor: E2ETrackCursor | None = None

    async def get_cursor(self):
        return self.cursor

    async def put_cursor(self, c):
        self.cursor = c


def _cp(group, commit):
    return SealedCheckpoint(
        feature_id="f", group_idx=group,
        repos=[RepoCheckpoint(repo_id="r", repo_path="/x/iriai-studio",
                              result_commit=commit)],
    )


class ScriptedTrack(AsyncE2ETrack):
    scripted: list = []
    idx: int = 0

    async def latest_checkpoint(self):
        cp = self.scripted[min(self.idx, len(self.scripted) - 1)]
        self.idx += 1
        return cp


def test_host_preflight_returns_structure():
    pf = host_preflight()
    assert isinstance(pf.ok, bool)
    assert pf.load1 >= 0 and pf.free_disk_gb > 0


@pytest.mark.asyncio
async def test_poll_advances_then_skips_unchanged():
    reg = FakeRegistry()
    passes = []
    t = ScriptedTrack(feature_id="f", live_dsn="x", registry=reg,
                      pass_fn=lambda cp: passes.append(cp.group_idx) or _noop())
    t.scripted = [_cp(79, "c79"), _cp(79, "c79")]
    r1 = await t.poll_once()
    assert r1.advanced and r1.did_pass
    assert reg.cursor.group_idx == 79 and reg.cursor.last_processed_commit == "c79"
    r2 = await t.poll_once()
    assert not r2.advanced and r2.skipped_reason == "already processed"
    assert passes == [79]  # pass ran once


@pytest.mark.asyncio
async def test_poll_coalesces_to_latest():
    reg = FakeRegistry()
    reg.cursor = E2ETrackCursor(last_processed_commit="c79", group_idx=79)
    seen = []
    t = ScriptedTrack(feature_id="f", live_dsn="x", registry=reg,
                      pass_fn=lambda cp: seen.append(cp.group_idx) or _noop())
    t.scripted = [_cp(81, "c81")]  # DAG outran the track 79 -> 81
    r = await t.poll_once()
    assert r.advanced and r.coalesced_from == 79
    assert seen == [81]  # processed the LATEST, skipping 80
    assert reg.cursor.group_idx == 81


@pytest.mark.asyncio
async def test_preflight_abort_skips_pass(monkeypatch):
    reg = FakeRegistry()
    ran = []
    t = ScriptedTrack(feature_id="f", live_dsn="x", registry=reg,
                      pass_fn=lambda cp: ran.append(1) or _noop())
    t.scripted = [_cp(79, "c79")]
    monkeypatch.setattr(
        rl, "host_preflight",
        lambda **k: rl.Preflight(False, 99.0, 0.1, 5.0, "load 99 > 20"),
    )
    r = await t.poll_once()
    assert r.advanced and not r.did_pass
    assert "preflight abort" in r.skipped_reason
    assert ran == []  # heavy pass did NOT run


@pytest.mark.asyncio
async def test_refused_pass_holds_cursor_and_retries():
    # Item-11 G2: E2EPassRefused -> the cursor is NOT written; the next poll
    # retries the SAME checkpoint (no silent consumption of a sealed
    # checkpoint), and a later successful pass advances it normally.
    from iriai_build_v2.workflows.develop.e2e.pass_ import E2EPassRefused

    reg = FakeRegistry()
    attempts = []
    refuse = {"on": True}

    async def pass_fn(cp):
        attempts.append(cp.group_idx)
        if refuse["on"]:
            raise E2EPassRefused("compose preflight refused: single-stack mutex")

    t = ScriptedTrack(feature_id="f", live_dsn="x", registry=reg, pass_fn=pass_fn)
    t.scripted = [_cp(79, "c79"), _cp(79, "c79"), _cp(79, "c79")]

    r1 = await t.poll_once()
    assert r1.advanced and not r1.did_pass
    assert "pass refused (cursor held)" in r1.skipped_reason
    assert reg.cursor is None  # checkpoint NOT consumed

    r2 = await t.poll_once()  # still refused -> retried the SAME checkpoint
    assert "pass refused (cursor held)" in r2.skipped_reason
    assert reg.cursor is None
    assert attempts == [79, 79]

    refuse["on"] = False  # pressure cleared -> pass runs, cursor advances
    r3 = await t.poll_once()
    assert r3.did_pass
    assert reg.cursor.group_idx == 79 and reg.cursor.last_processed_commit == "c79"


async def _noop():
    return None
