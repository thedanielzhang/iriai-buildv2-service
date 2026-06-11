"""Known-flaky test ledger injection (IRIAI_KNOWN_FLAKY_LEDGER).

Covers the analyst timesink directive P1 item 1:
- flag OFF (default) → prompts are byte-identical (helper returns "");
- flag ON + `known-flaky-tests` artifact absent → no-op with a WARN log;
- flag ON + artifact present → ledger content + triage instruction injected
  into the verifier / regression-tester / integration-tester / smoke-tester
  (qa_engineer) prompts.
"""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import (
    ImplementationResult,
    ImplementationTask,
    Verdict,
)
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module

LEDGER_BODY = (
    "## Baseline flaky tests (kaya-main, live DB)\n"
    "- tests/api/test_share_links.py::test_concurrent_bid_status\n"
    "- tests/api/test_submittals.py::test_listing_order\n"
)
MARKER = "Known-Flaky Test Ledger (triage discipline)"


class _Artifacts:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.get_calls: list[str] = []

    async def get(self, key: str, *, feature):
        del feature
        self.get_calls.append(key)
        return self.store.get(key, "")

    async def put(self, key: str, value: str, *, feature):
        del feature
        self.store[key] = value


def _feature(feature_id: str):
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


def _runner():
    return SimpleNamespace(artifacts=_Artifacts(), services={})


@pytest.fixture(autouse=True)
def _clear_ledger_cache():
    implementation_module._KNOWN_FLAKY_LEDGER_CACHE.clear()
    implementation_module._KNOWN_FLAKY_LEDGER_WARNED.clear()
    yield
    implementation_module._KNOWN_FLAKY_LEDGER_CACHE.clear()
    implementation_module._KNOWN_FLAKY_LEDGER_WARNED.clear()


# ── Flag gating ──────────────────────────────────────────────────────────────


def test_flag_defaults_off(monkeypatch):
    monkeypatch.delenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, raising=False)
    assert implementation_module._known_flaky_ledger_enabled() is False


def test_flag_on(monkeypatch):
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    assert implementation_module._known_flaky_ledger_enabled() is True


def test_flag_explicit_off(monkeypatch):
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "0")
    assert implementation_module._known_flaky_ledger_enabled() is False


@pytest.mark.asyncio
async def test_flag_off_returns_empty_even_with_artifact(monkeypatch):
    monkeypatch.delenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, raising=False)
    runner = _runner()
    feature = _feature("flaky-flag-off")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    section = await implementation_module._known_flaky_ledger_section(runner, feature)

    assert section == ""
    # Flag off must not even read the artifact (zero behavior change).
    assert implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY not in runner.artifacts.get_calls


@pytest.mark.asyncio
async def test_flag_on_artifact_absent_is_noop_with_warn(monkeypatch, caplog):
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner = _runner()
    feature = _feature("flaky-absent")

    with caplog.at_level(logging.WARNING):
        section = await implementation_module._known_flaky_ledger_section(runner, feature)

    assert section == ""
    warnings = [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING
        and implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY in rec.getMessage()
    ]
    assert len(warnings) == 1

    # Warn fires only once per feature; a later call stays silent but is
    # NOT cached, so staging the artifact mid-run takes effect.
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert await implementation_module._known_flaky_ledger_section(runner, feature) == ""
    assert not [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING
        and implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY in rec.getMessage()
    ]

    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY
    late = await implementation_module._known_flaky_ledger_section(runner, feature)
    assert LEDGER_BODY.strip() in late


@pytest.mark.asyncio
async def test_flag_on_artifact_present_builds_triage_section(monkeypatch):
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner = _runner()
    feature = _feature("flaky-present")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    section = await implementation_module._known_flaky_ledger_section(runner, feature)

    assert MARKER in section
    assert LEDGER_BODY.strip() in section
    # Triage discipline content.
    assert "NOT an automatic blocker" in section
    assert "stash-diff" in section
    assert "ABSENT on clean HEAD" in section
    assert "NEVER name-match" in section
    assert "remain automatic blockers" in section
    assert "Ledger drift" in section
    assert "REPORTED" in section


@pytest.mark.asyncio
async def test_section_memoized_per_feature(monkeypatch):
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner = _runner()
    feature = _feature("flaky-memo")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    first = await implementation_module._known_flaky_ledger_section(runner, feature)
    second = await implementation_module._known_flaky_ledger_section(runner, feature)

    assert first == second
    assert runner.artifacts.get_calls.count(
        implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY
    ) == 1


# ── Prompt injection per role ────────────────────────────────────────────────


@asynccontextmanager
async def _fake_actor_context(runner, feature, **kwargs):
    # Bypass sandbox binding; yield the real base actor so Ask validation
    # passes (qa_engineer carries the smoke_tester role).
    del runner, feature
    yield kwargs["base_actor"]


async def _captured_verify_prompt(runner, feature, monkeypatch) -> str:
    """Run `_verify` (group verification — qa_engineer / smoke_tester role)
    with prompt capture."""
    prompts: list[str] = []

    async def _capture_run(task, feature_arg, phase_name=""):
        del feature_arg, phase_name
        prompts.append(task.prompt)
        return Verdict(approved=True, summary="ok")

    runner.run = _capture_run
    monkeypatch.setattr(
        implementation_module, "_diagnostic_actor_context", _fake_actor_context
    )
    verdict = await implementation_module._verify(
        runner,
        feature,
        [ImplementationResult(task_id="TASK-1", summary="done")],
        ["src/example.py"],
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
    )
    assert verdict.approved is True
    assert len(prompts) == 1
    return prompts[0]


@pytest.mark.asyncio
async def test_verify_prompt_flag_off_byte_identical(monkeypatch):
    feature = _feature("flaky-verify-prompt")

    monkeypatch.delenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, raising=False)
    runner_off = _runner()
    runner_off.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY
    prompt_off_with_artifact = await _captured_verify_prompt(runner_off, feature, monkeypatch)

    runner_off_bare = _runner()
    prompt_off_without_artifact = await _captured_verify_prompt(
        runner_off_bare, feature, monkeypatch
    )

    # Flag OFF: byte-identical regardless of artifact presence.
    assert prompt_off_with_artifact == prompt_off_without_artifact
    assert MARKER not in prompt_off_with_artifact

    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner_on = _runner()
    runner_on.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY
    prompt_on = await _captured_verify_prompt(runner_on, feature, monkeypatch)

    # Flag ON: exactly the ledger section appended — nothing else changes.
    section = await implementation_module._known_flaky_ledger_section(runner_on, feature)
    assert prompt_on == prompt_off_with_artifact + section
    assert MARKER in prompt_on
    assert LEDGER_BODY.strip() in prompt_on


@pytest.mark.asyncio
async def test_run_regression_injects_into_regression_and_integration_prompts(monkeypatch):
    """Covers the regression_tester and integration_tester role prompts."""
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner = _runner()
    feature = _feature("flaky-regression")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    prompts: list[str] = []

    async def _capture_run(task, feature_arg, phase_name=""):
        del feature_arg, phase_name
        prompts.append(task.prompt)
        return Verdict(approved=True, summary="ok")

    runner.run = _capture_run

    verdict = await implementation_module._run_regression(
        runner,
        feature,
        ["src/changed.py"],
        handover_context="handover notes",
        actor_factory=lambda actor, name, **kwargs: actor,
    )

    assert verdict is not None and verdict.approved is True
    assert len(prompts) == 2  # regression_tester + integration_tester
    for prompt in prompts:
        assert MARKER in prompt
        assert LEDGER_BODY.strip() in prompt


@pytest.mark.asyncio
async def test_run_regression_flag_off_has_no_marker(monkeypatch):
    monkeypatch.delenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, raising=False)
    runner = _runner()
    feature = _feature("flaky-regression-off")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    prompts: list[str] = []

    async def _capture_run(task, feature_arg, phase_name=""):
        del feature_arg, phase_name
        prompts.append(task.prompt)
        return Verdict(approved=True, summary="ok")

    runner.run = _capture_run

    await implementation_module._run_regression(
        runner,
        feature,
        ["src/changed.py"],
        handover_context="handover notes",
        actor_factory=lambda actor, name, **kwargs: actor,
    )

    assert len(prompts) == 2
    for prompt in prompts:
        assert MARKER not in prompt


@pytest.mark.asyncio
async def test_expanded_verify_lenses_inject_ledger(monkeypatch):
    """Covers the verifier and regression_tester lens prompts."""
    monkeypatch.setenv(implementation_module.KNOWN_FLAKY_LEDGER_ENV, "1")
    runner = _runner()
    feature = _feature("flaky-lenses")
    runner.artifacts.store[implementation_module.KNOWN_FLAKY_LEDGER_ARTIFACT_KEY] = LEDGER_BODY

    captured: list[tuple[str, str]] = []

    async def _capture_bound_ask(runner_arg, feature_arg, *, base_actor, suffix,
                                 runtime, prompt, output_type, phase_name,
                                 feature_root, lane_id, group_idx=0):
        del runner_arg, feature_arg, base_actor, runtime, output_type
        del phase_name, feature_root, group_idx
        captured.append((lane_id, prompt))
        return Verdict(approved=True, summary="lens ok")

    async def _noop_event(*args, **kwargs):
        del args, kwargs

    monkeypatch.setattr(
        implementation_module, "_run_bound_diagnostic_ask", _capture_bound_ask
    )
    monkeypatch.setattr(implementation_module, "_log_feature_event", _noop_event)

    base = Verdict(approved=False, summary="normal verifier failed")
    merged = await implementation_module._run_expanded_dag_verify_lenses(
        runner,
        feature,
        0,
        "initial",
        base,
        [ImplementationResult(task_id="TASK-1", summary="done")],
        ["src/example.py"],
        [ImplementationTask(id="TASK-1", name="Task", description="Task")],
        record_graph=False,
    )

    assert merged is not None
    lens_slugs = {spec.slug for spec in implementation_module._dag_verify_lens_specs()}
    assert len(captured) == len(lens_slugs)
    # verifier + regression_tester lenses (and all others) get the ledger.
    assert any("regression-downstream" in lane for lane, _ in captured)
    for _lane, prompt in captured:
        assert MARKER in prompt
        assert LEDGER_BODY.strip() in prompt
