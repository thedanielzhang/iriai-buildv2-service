"""Parallel per-task dispatch prompt assembly (IRIAI_PROMPT_ASSEMBLY_CONCURRENCY, W-O).

Pins the W-O change: the blocking sections of
``_ImplementationPromptBuilder.build_prompt_context`` (sandbox prompt
side-file writes + the ``.iriai-context`` scan/sha material) run on worker
threads bounded by an optional per-loop semaphore, so a wave of N tasks
assembles prompts concurrently (wave prompt startup = max(single task), not
sum). Covered:

- flag reader: unset/blank/invalid/non-positive -> None (no extra bound;
  effective concurrency = wave width), positive int -> that bound;
- a wave of N fake tasks assembles concurrently by default
  (max in-flight > 1) and serially with the env set to 1 (max in-flight == 1,
  deterministic — the semaphore guarantees it);
- per-task error isolation: one task's assembly failure surfaces as that
  task's exception only; siblings complete with correct prompts and persisted
  ``dag-dispatch-prompt:*`` artifacts, and the failing task persists nothing
  (exactly the serial path's typed-failure semantics at the dispatcher);
- prompts + bundle identity (prompt_sha256 / context_sha256) are
  byte-identical between serial (env=1) and parallel (default) assembly for
  the same inputs, including the sandbox context-dir scan path.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import pytest

from iriai_build_v2.models.outputs import ImplementationTask
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module

_ENV = implementation_module._PROMPT_ASSEMBLY_CONCURRENCY_ENV


class _Artifacts:
    """In-memory artifact store (test_task_amendments.py conventions)."""

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


def _task(task_id: str) -> ImplementationTask:
    return ImplementationTask(
        id=task_id, name=f"Task {task_id}", description=f"Do {task_id}."
    )


def _builder(runner, feature, task_id: str, *, inline_prompt: str | None = None):
    return implementation_module._ImplementationPromptBuilder(
        runner=runner,
        feature=feature,
        task=_task(task_id),
        repo_prefix="",
        task_contract=None,
        handover_context="",
        inline_prompt=inline_prompt if inline_prompt is not None else f"Implement {task_id}.",
        log_label=f"W-O {task_id}",
    )


def _request():
    return SimpleNamespace(group_idx=7, request_digest="e" * 64)


class _InFlightRecorder:
    """Thread-safe max-in-flight tracker for the offloaded assembly section."""

    def __init__(self, *, hold_s: float = 0.15, fail_task_ids: frozenset[str] = frozenset()):
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0
        self.calls: list[str] = []
        self._hold_s = hold_s
        self._fail_task_ids = fail_task_ids

    def __call__(self, task, **kwargs):
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            self.calls.append(task.id)
        try:
            time.sleep(self._hold_s)
            if task.id in self._fail_task_ids:
                raise RuntimeError(f"assembly exploded for {task.id}")
            return f"PROMPT::{task.id}::{kwargs.get('inline_prompt', '')}"
        finally:
            with self._lock:
                self._in_flight -= 1


# ── Flag reader ───────────────────────────────────────────────────────────────


def test_limit_unset_means_wave_width(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    assert implementation_module._prompt_assembly_concurrency_limit() is None


@pytest.mark.parametrize("raw", ["", "   ", "abc", "0", "-2"])
def test_limit_blank_invalid_nonpositive_means_wave_width(monkeypatch, raw):
    monkeypatch.setenv(_ENV, raw)
    assert implementation_module._prompt_assembly_concurrency_limit() is None


@pytest.mark.parametrize(("raw", "expected"), [("1", 1), ("3", 3), (" 8 ", 8)])
def test_limit_positive_int(monkeypatch, raw, expected):
    monkeypatch.setenv(_ENV, raw)
    assert implementation_module._prompt_assembly_concurrency_limit() == expected


def test_gate_is_noop_when_unbounded(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)

    async def scenario():
        gate = implementation_module._prompt_assembly_gate()
        assert not isinstance(gate, asyncio.Semaphore)
        async with gate:
            pass

    asyncio.run(scenario())


def test_gate_reuses_per_loop_semaphore(monkeypatch):
    monkeypatch.setenv(_ENV, "2")

    async def scenario():
        first = implementation_module._prompt_assembly_gate()
        second = implementation_module._prompt_assembly_gate()
        assert isinstance(first, asyncio.Semaphore)
        assert first is second

    asyncio.run(scenario())


# ── Wave concurrency ─────────────────────────────────────────────────────────


async def _assemble_wave(recorder, *, n: int = 4, return_exceptions: bool = False):
    monkey_target = "_build_task_prompt_with_optional_sandbox_context"
    original = getattr(implementation_module, monkey_target)
    setattr(implementation_module, monkey_target, recorder)
    try:
        runner = _runner()
        feature = _feature("wo-wave")
        builders = [_builder(runner, feature, f"T-{i}") for i in range(n)]
        results = await asyncio.gather(
            *[b.build_prompt_context(_request()) for b in builders],
            return_exceptions=return_exceptions,
        )
        return results, runner
    finally:
        setattr(implementation_module, monkey_target, original)


@pytest.mark.asyncio
async def test_wave_assembles_concurrently_by_default(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    recorder = _InFlightRecorder()

    results, _runner_ns = await _assemble_wave(recorder, n=4)

    assert recorder.max_in_flight > 1
    assert sorted(recorder.calls) == ["T-0", "T-1", "T-2", "T-3"]
    for i, result in enumerate(results):
        assert result.prompt.startswith(f"PROMPT::T-{i}::")
        assert result.bundle.prompt_ref > 0


@pytest.mark.asyncio
async def test_wave_env_one_is_serial(monkeypatch):
    monkeypatch.setenv(_ENV, "1")
    recorder = _InFlightRecorder(hold_s=0.05)

    results, _runner_ns = await _assemble_wave(recorder, n=4)

    # Deterministic: the semaphore guarantees at most one assembly in flight.
    assert recorder.max_in_flight == 1
    assert len(results) == 4


@pytest.mark.asyncio
async def test_wave_env_two_bounds_concurrency(monkeypatch):
    monkeypatch.setenv(_ENV, "2")
    recorder = _InFlightRecorder(hold_s=0.05)

    await _assemble_wave(recorder, n=5)

    # Deterministic upper bound; the >1 lower bound is timing-assisted.
    assert recorder.max_in_flight <= 2


# ── Per-task error isolation ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_failing_assembly_does_not_sink_siblings(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    recorder = _InFlightRecorder(hold_s=0.05, fail_task_ids=frozenset({"T-2"}))

    results, runner = await _assemble_wave(recorder, n=4, return_exceptions=True)

    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(failures) == 1
    assert isinstance(failures[0], RuntimeError)
    assert "T-2" in str(failures[0])
    # Siblings completed with their own prompts + persisted dispatch-prompt
    # artifacts; the failing task persisted nothing (it failed before
    # materialization, exactly like the serial path).
    survivors = [r for r in results if not isinstance(r, BaseException)]
    assert {r.prompt.split("::")[1] for r in survivors} == {"T-0", "T-1", "T-3"}
    persisted = [k for k in runner.artifacts.store if k.startswith("dag-dispatch-prompt:")]
    assert len(persisted) == 3
    assert not any(":T-2:" in key for key in persisted)


@pytest.mark.asyncio
async def test_failure_releases_bounded_gate(monkeypatch):
    monkeypatch.setenv(_ENV, "1")
    recorder = _InFlightRecorder(hold_s=0.01, fail_task_ids=frozenset({"T-0"}))

    results, _runner_ns = await _assemble_wave(recorder, n=3, return_exceptions=True)

    # T-0 failed while HOLDING the size-1 gate; siblings still completed, so
    # the gate was released (a poisoned gate would deadlock + timeout).
    assert sum(isinstance(r, BaseException) for r in results) == 1
    assert sum(not isinstance(r, BaseException) for r in results) == 2


# ── Serial vs parallel byte-identity ─────────────────────────────────────────

_AMENDMENT = (
    "### Release-target reuse (DEC-WO-1)\n"
    "Reuse the existing write chokepoint; do NOT mint a new write path.\n"
)


def _binding_for(tmp_path):
    return SimpleNamespace(
        env={"IRIAI_SANDBOX_ROOT": str(tmp_path)},
        manifest_path="",
        cwd=str(tmp_path),
        workspace_override="",
    )


async def _assemble_all(task_ids, tmp_path):
    """Real assembly path (no monkeypatching) against a shared fake store."""
    runner = _runner()
    feature = _feature("wo-identity")
    # Give one task an amendment so the async store read participates.
    runner.artifacts.store[
        implementation_module._task_amendments_artifact_key(task_ids[0])
    ] = _AMENDMENT
    binding = _binding_for(tmp_path)
    builders = [_builder(runner, feature, tid) for tid in task_ids]
    results = await asyncio.gather(
        *[b.build_prompt_context(_request(), binding) for b in builders]
    )
    return {
        tid: (r.prompt, r.bundle.prompt_sha256, r.bundle.context_sha256, tuple(r.bundle.context_file_paths))
        for tid, r in zip(task_ids, results)
    }


@pytest.mark.asyncio
async def test_prompts_byte_identical_serial_vs_parallel(monkeypatch, tmp_path):
    task_ids = ["T-A", "T-B", "T-C"]
    # Pre-place per-task context files so the offloaded context-dir scan has
    # real material to hash.
    for tid in task_ids:
        segment = implementation_module._prompt_context_segment_for_task(tid)
        context_dir = tmp_path / ".iriai-context" / segment
        context_dir.mkdir(parents=True)
        (context_dir / "refs.md").write_text(f"# refs for {tid}\n", encoding="utf-8")

    monkeypatch.delenv(_ENV, raising=False)
    parallel = await _assemble_all(task_ids, tmp_path)

    monkeypatch.setenv(_ENV, "1")
    serial = await _assemble_all(task_ids, tmp_path)

    assert parallel == serial
    # Sanity: the scan actually saw the context material and the amendment
    # reached the first task's prompt.
    for tid in task_ids:
        assert parallel[tid][3], f"context scan saw no files for {tid}"
    assert "DEC-WO-1" in parallel["T-A"][0]
    assert "DEC-WO-1" not in parallel["T-B"][0]
