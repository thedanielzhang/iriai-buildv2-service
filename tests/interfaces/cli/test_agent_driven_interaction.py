from __future__ import annotations

import asyncio
import json

import pytest
from iriai_compose.prompts import Select
from iriai_compose.tasks import Ask

from iriai_build_v2.interfaces.cli.agent_driven_interaction import (
    AgentDrivenInteractionRuntime,
)
from iriai_build_v2.planning_signals import GateRejection
from iriai_build_v2.roles import user


def _runtime(tmp_path):
    return AgentDrivenInteractionRuntime(workspace_root=tmp_path, poll_interval_s=0.02)


async def _wait_for_query(rt) -> tuple[str, dict]:
    for _ in range(500):
        files = list(rt._dir.glob("*.query.json"))
        if files:
            data = json.loads(files[0].read_text())
            return data["id"], data
        await asyncio.sleep(0.01)
    raise AssertionError("query file never appeared")


def _write_answer(rt, query_id: str, answer: dict) -> None:
    (rt._dir / f"{query_id}.answer.json").write_text(json.dumps(answer))


@pytest.mark.asyncio
async def test_respond_returns_driver_answer(tmp_path):
    rt = _runtime(tmp_path)
    ask_task = asyncio.create_task(
        rt.ask(
            Ask(actor=user, prompt="What is the CMIC field?"),
            kind="respond",
            feature_id="f1",
            phase_name="scope",
        )
    )
    query_id, query = await _wait_for_query(rt)
    assert query["kind"] == "respond"
    _write_answer(rt, query_id, {"text": "Use a new project CMIC field"})

    result = await ask_task
    assert result == "Use a new project CMIC field"
    assert not (rt._dir / f"{query_id}.query.json").exists()
    assert (rt._dir / "processed" / f"{query_id}.query.json").exists()
    assert (rt._dir / "processed" / f"{query_id}.answer.json").exists()


@pytest.mark.asyncio
async def test_choose_validates_option(tmp_path):
    rt = _runtime(tmp_path)
    ask_task = asyncio.create_task(
        rt.ask(
            Ask(actor=user, prompt="Pick", input=Select(options=["A", "B"])),
            kind="choose",
            feature_id="f1",
            phase_name="scope",
        )
    )
    query_id, query = await _wait_for_query(rt)
    assert query["options"] == ["A", "B"]
    _write_answer(rt, query_id, {"choice": "B"})

    result = await ask_task
    assert result == "B"


@pytest.mark.asyncio
async def test_approve_reject_returns_gate_rejection(tmp_path):
    rt = _runtime(tmp_path)
    ask_task = asyncio.create_task(
        rt.ask(
            Ask(actor=user, prompt="Approve?"),
            kind="approve",
            feature_id="f1",
            phase_name="gate",
        )
    )
    query_id, _ = await _wait_for_query(rt)
    _write_answer(rt, query_id, {"decision": "reject", "feedback": "fix DD-07"})

    result = await ask_task
    assert isinstance(result, GateRejection)
    assert result.feedback == "fix DD-07"


@pytest.mark.asyncio
async def test_approve_returns_true(tmp_path):
    rt = _runtime(tmp_path)
    ask_task = asyncio.create_task(
        rt.ask(
            Ask(actor=user, prompt="Approve?"),
            kind="approve",
            feature_id="f1",
            phase_name="gate",
        )
    )
    query_id, _ = await _wait_for_query(rt)
    _write_answer(rt, query_id, {"decision": "approve"})

    result = await ask_task
    assert result is True


@pytest.mark.asyncio
async def test_timeout_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("IRIAI_OPERATOR_QUERY_TIMEOUT_S", "1")
    rt = _runtime(tmp_path)
    with pytest.raises(RuntimeError, match="timed out"):
        await rt.ask(
            Ask(actor=user, prompt="Slow?"),
            kind="respond",
            feature_id="f1",
            phase_name="scope",
        )
