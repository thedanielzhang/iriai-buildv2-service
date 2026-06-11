"""Per-task spec-amendment carrier injection (IRIAI_TASK_AMENDMENTS, P-14).

Covers the operator directive P-14:
- no `dag-task-amendments:{task_id}` artifact → helper returns "" (prompts
  are byte-identical);
- artifact present + flag ON (the DEFAULT — opposite of the flaky-ledger
  flag) → BINDING header + amendment markdown injected;
- artifact present + IRIAI_TASK_AMENDMENTS=0 → "" with a LOUD warning (an
  operator-authored amendment is being suppressed);
- artifact lacking a decision-ledger citation → warned about but STILL
  injected (operator content is never silently dropped);
- the section reaches the actual dispatch prompt built by
  `_ImplementationPromptBuilder.build_prompt_context` — the single prompt
  chokepoint every dispatcher-mediated implementation attempt flows
  through — and is read FRESH per build (no caching).
"""

import logging
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module

TASK_ID = "TASK-RCAN-01-BACKEND-SERVICES"
AMENDMENT_KEY = f"dag-task-amendments:{TASK_ID}"
AMENDMENT_BODY = (
    "### Release-target reuse (DEC-S32-RT-1)\n"
    "Reuse the existing fds-s32-write-chokepoint release target; do NOT "
    "mint a new write path for this task.\n"
)
AMENDMENT_BODY_NO_CITATION = (
    "Reuse the existing write chokepoint; do not mint a new write path.\n"
)
HEADER_MARKER = "Operator Spec Amendments for this task"
BINDING_MARKER = "BINDING — apply before the base spec where they conflict"


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


def _task():
    return ImplementationTask(id=TASK_ID, name="Backend services", description="Do it.")


# ── Flag gating (default ON — opposite of the flaky-ledger flag) ─────────────


def test_flag_defaults_on(monkeypatch):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    assert implementation_module._task_amendments_enabled() is True


def test_flag_explicit_off(monkeypatch):
    monkeypatch.setenv(implementation_module.TASK_AMENDMENTS_ENV, "0")
    assert implementation_module._task_amendments_enabled() is False


def test_flag_explicit_on(monkeypatch):
    monkeypatch.setenv(implementation_module.TASK_AMENDMENTS_ENV, "1")
    assert implementation_module._task_amendments_enabled() is True


def test_artifact_key_shape():
    assert implementation_module._task_amendments_artifact_key(TASK_ID) == AMENDMENT_KEY


# ── Section content ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_artifact_returns_empty(monkeypatch):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-absent")

    section = await implementation_module._task_amendments_section(
        runner, feature, TASK_ID
    )

    assert section == ""
    assert AMENDMENT_KEY in runner.artifacts.get_calls


@pytest.mark.asyncio
async def test_artifact_present_default_flag_injects_binding_section(monkeypatch):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-present")
    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY

    section = await implementation_module._task_amendments_section(
        runner, feature, TASK_ID
    )

    assert HEADER_MARKER in section
    assert BINDING_MARKER in section
    assert AMENDMENT_BODY.strip() in section


@pytest.mark.asyncio
async def test_flag_off_with_artifact_suppresses_with_loud_warning(monkeypatch, caplog):
    monkeypatch.setenv(implementation_module.TASK_AMENDMENTS_ENV, "0")
    runner = _runner()
    feature = _feature("amend-flag-off")
    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY

    with caplog.at_level(logging.WARNING):
        section = await implementation_module._task_amendments_section(
            runner, feature, TASK_ID
        )

    assert section == ""
    warnings = [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING and AMENDMENT_KEY in rec.getMessage()
    ]
    assert len(warnings) == 1
    assert "NOT being injected" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_flag_off_without_artifact_is_silent(monkeypatch, caplog):
    monkeypatch.setenv(implementation_module.TASK_AMENDMENTS_ENV, "0")
    runner = _runner()
    feature = _feature("amend-flag-off-bare")

    with caplog.at_level(logging.WARNING):
        section = await implementation_module._task_amendments_section(
            runner, feature, TASK_ID
        )

    assert section == ""
    assert not [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING and AMENDMENT_KEY in rec.getMessage()
    ]


@pytest.mark.asyncio
async def test_citation_missing_warns_but_still_injects(monkeypatch, caplog):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-no-citation")
    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY_NO_CITATION

    with caplog.at_level(logging.WARNING):
        section = await implementation_module._task_amendments_section(
            runner, feature, TASK_ID
        )

    # Injected anyway — operator content is never silently dropped.
    assert HEADER_MARKER in section
    assert AMENDMENT_BODY_NO_CITATION.strip() in section
    warnings = [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING
        and "decision-ledger" in rec.getMessage()
        and AMENDMENT_KEY in rec.getMessage()
    ]
    assert len(warnings) == 1


@pytest.mark.asyncio
async def test_citation_present_does_not_warn(monkeypatch, caplog):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-cited")
    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY  # carries DEC-S32-RT-1

    with caplog.at_level(logging.WARNING):
        section = await implementation_module._task_amendments_section(
            runner, feature, TASK_ID
        )

    assert HEADER_MARKER in section
    assert not [
        rec for rec in caplog.records
        if rec.levelno == logging.WARNING and "decision-ledger" in rec.getMessage()
    ]


def test_citation_regex_accepts_known_ledger_id_shapes():
    pattern = implementation_module._TASK_AMENDMENT_CITATION_RE
    assert pattern.search("per DEC-12")
    assert pattern.search("per D-3 ruling")
    assert pattern.search("per DD-7")
    assert not pattern.search("no citation here")


# ── Injection site: the dispatched/persisted task prompt ─────────────────────
#
# `_ImplementationPromptBuilder.build_prompt_context` is the single prompt
# chokepoint every dispatcher-mediated implementation attempt flows through
# (initial, retry, strict-resume re-dispatch, enhancement stage). Driving it
# directly mirrors test_implementation_workspace_authority_adapter.py's
# test_implementation_prompt_context_materializes_positive_prompt_ref.


def _prompt_builder(runner, feature, *, inline_prompt: str = "Do the task."):
    return implementation_module._ImplementationPromptBuilder(
        runner=runner,
        feature=feature,
        task=_task(),
        repo_prefix="",
        task_contract=None,
        handover_context="",
        inline_prompt=inline_prompt,
        log_label="Amendments",
    )


def _request():
    return SimpleNamespace(group_idx=3, request_digest="d" * 64)


@pytest.mark.asyncio
async def test_dispatch_prompt_carries_amendment_section(monkeypatch):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-dispatch")
    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY

    result = await _prompt_builder(runner, feature).build_prompt_context(_request())

    assert HEADER_MARKER in result.prompt
    assert BINDING_MARKER in result.prompt
    assert AMENDMENT_BODY.strip() in result.prompt
    # The amendment is part of the PERSISTED dispatch prompt, not a
    # post-materialization decoration.
    persisted = [
        value
        for key, value in runner.artifacts.store.items()
        if key.startswith("dag-dispatch-prompt:") and TASK_ID in key
    ]
    assert len(persisted) == 1
    assert AMENDMENT_BODY.strip() in persisted[0]
    assert HEADER_MARKER in persisted[0]


@pytest.mark.asyncio
async def test_dispatch_prompt_without_artifact_is_byte_identical(monkeypatch):
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    feature = _feature("amend-dispatch-bare")

    runner_bare = _runner()
    bare = await _prompt_builder(runner_bare, feature).build_prompt_context(_request())

    runner_flag_off = _runner()
    runner_flag_off.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY
    monkeypatch.setenv(implementation_module.TASK_AMENDMENTS_ENV, "0")
    suppressed = await _prompt_builder(runner_flag_off, feature).build_prompt_context(
        _request()
    )

    assert HEADER_MARKER not in bare.prompt
    assert bare.prompt == suppressed.prompt


@pytest.mark.asyncio
async def test_dispatch_prompt_reads_amendment_fresh_each_attempt(monkeypatch):
    """An amendment installed mid-run binds the NEXT attempt (no caching)."""
    monkeypatch.delenv(implementation_module.TASK_AMENDMENTS_ENV, raising=False)
    runner = _runner()
    feature = _feature("amend-mid-run")
    builder = _prompt_builder(runner, feature)

    first = await builder.build_prompt_context(_request())
    assert HEADER_MARKER not in first.prompt

    runner.artifacts.store[AMENDMENT_KEY] = AMENDMENT_BODY
    second = await builder.build_prompt_context(_request())
    assert HEADER_MARKER in second.prompt
    assert AMENDMENT_BODY.strip() in second.prompt
    assert runner.artifacts.get_calls.count(AMENDMENT_KEY) == 2
