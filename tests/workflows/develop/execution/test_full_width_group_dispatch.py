"""Full-width group dispatch invariants (operator directive 2026-06-11).

Within each develop-phase CHK group, every pending task dispatches
CONCURRENTLY — the Phase-A audit found the dispatch path is ALREADY
full-width and these tests PIN that property so a future change cannot
silently introduce a serializer:

1. The group dispatch site (`implementation.py` "Dispatch all tasks in
   parallel" block) feeds ALL `pending_tasks` into a single unbounded
   `asyncio.gather` — no width cap, no semaphore, no sub-batching. Same
   for the enhancement-group dispatch. Pinned by source literal because
   the gather is inline in the phase orchestrator (not extractable
   without refactoring working code).
2. Parallel-actor names are unique under width N for every lane that can
   run N-wide (`_make_parallel_actor` + the real per-lane suffix
   schemes), so the iriai-compose "same AgentActor must not appear in
   multiple parallel tasks" guard can never trip on sibling tasks.
3. Dispatch identity (idempotency key / request digest / sandbox id) is
   unique per sibling task at width N, so N concurrent dispatch journal
   rows and N sandbox allocations never collide.
4. The planning-side wave normalizer keeps full width for independent
   tasks while serializing only TRUE dependency edges (the invariant
   that makes full-width wave dispatch safe at all).

Groups stay sequential; the merge-queue drain / group seal stays
order-sensitive under the feature advisory lock — those paths are
deliberately NOT touched or relaxed here.
"""

from __future__ import annotations

from pathlib import Path

from iriai_compose import AgentActor
from iriai_compose.actors import Role

from iriai_build_v2.models.outputs import (
    ImplementationDAG,
    ImplementationTask,
    TaskAcceptanceCriterion,
    TaskReference,
)
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    _make_parallel_actor,
    dispatch_idempotency_key,
    dispatch_request_digest,
)
from iriai_build_v2.workflows.planning.phases.task_planning import (
    TaskPlanningPhase,
)

_IMPLEMENTATION_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "iriai_build_v2"
    / "workflows"
    / "develop"
    / "phases"
    / "implementation.py"
)
_DISPATCHER_PATH = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "iriai_build_v2"
    / "workflows"
    / "develop"
    / "execution"
    / "dispatcher.py"
)

# The exact dispatch comprehension feeding the unbounded gather for the
# main DAG-group path and the enhancement-group path. If either literal
# changes, re-verify the replacement still dispatches EVERY pending task
# in the group concurrently (full DAG width), then update this pin.
_GROUP_DISPATCH_GATHER_LITERAL = (
    "gathered = await _asyncio.gather(\n"
    "                *[\n"
    "                    _run_task(stable_task_indices.get(t.id, i), t)\n"
    "                    for i, t in enumerate(pending_tasks)\n"
    "                ],\n"
    "            )"
)
_ENHANCEMENT_DISPATCH_GATHER_LITERAL = (
    "gathered = await _asyncio.gather(\n"
    "            *[\n"
    "                _run_enh_task(stable_task_indices.get(t.id, i), t)\n"
    "                for i, t in enumerate(pending_tasks)\n"
    "            ],\n"
    "        )"
)

# The real actor_suffix f-string literals used at the two width-N
# dispatch sites. The behavioral width-N tests below MIRROR these
# schemes; this pin guarantees the mirrors cannot silently drift from
# the production formats.
_IMPL_ACTOR_SUFFIX_LITERAL = 'actor_suffix=f"g{group_idx}-t{task_idx}-a{attempt}"'
_ENH_ACTOR_SUFFIX_LITERAL = 'actor_suffix=f"enh-t{task_idx}-a{attempt}"'
_REPAIR_FIX_SUFFIX_LITERAL = 'f"dag-g{group_idx}-r{retry}-fix-{gid}"'


def test_group_dispatch_gather_is_unbounded_full_width() -> None:
    """The DAG-group and enhancement-group dispatch sites feed every
    pending task into one unbounded `asyncio.gather` — no width cap, no
    semaphore, no slicing of `pending_tasks` into sub-batches. This is
    the load-bearing full-width property: a group is a dependency-free
    wave (see the normalizer test below), so all of it runs at once.
    """
    impl_source = _IMPLEMENTATION_PATH.read_text(encoding="utf-8")
    dispatcher_source = _DISPATCHER_PATH.read_text(encoding="utf-8")

    assert _GROUP_DISPATCH_GATHER_LITERAL in impl_source, (
        "The DAG-group dispatch gather changed shape. If the new shape "
        "caps width / batches pending_tasks, that breaks the operator "
        "full-width directive; otherwise update the pinned literal."
    )
    assert _ENHANCEMENT_DISPATCH_GATHER_LITERAL in impl_source, (
        "The enhancement-group dispatch gather changed shape. Re-verify "
        "full-width dispatch, then update the pinned literal."
    )
    # No concurrency primitive that could cap dispatch width exists in
    # either module today. Introducing one is the most likely vector for
    # an accidental serializer — force that change to confront this test.
    for label, source in (
        ("implementation.py", impl_source),
        ("execution/dispatcher.py", dispatcher_source),
    ):
        assert "Semaphore" not in source, (
            f"{label} gained a Semaphore. The develop-phase group "
            "dispatch must stay full-width (every pending task in the "
            "group dispatches concurrently); width caps require an "
            "explicit operator decision."
        )


def test_parallel_actor_suffix_schemes_pinned_in_source() -> None:
    """The width-N actor-suffix mirrors used below match production."""
    impl_source = _IMPLEMENTATION_PATH.read_text(encoding="utf-8")
    for literal in (
        _IMPL_ACTOR_SUFFIX_LITERAL,
        _ENH_ACTOR_SUFFIX_LITERAL,
        _REPAIR_FIX_SUFFIX_LITERAL,
    ):
        assert literal in impl_source, (
            f"actor_suffix scheme {literal!r} no longer in "
            "implementation.py — update the width-N uniqueness tests to "
            "mirror the new scheme."
        )


def _implementer_base() -> AgentActor:
    return AgentActor(
        name="implementer",
        role=Role(name="implementer", prompt="impl", metadata={}),
        context_keys=[],
        persistent=True,
    )


def test_parallel_actor_names_unique_at_width_n() -> None:
    """Width-10 group: every sibling task's parallel actor has a unique
    name under the REAL suffix schemes, so iriai-compose's parallel
    guard ("same AgentActor must not appear in multiple parallel tasks")
    can never trip on full-width sibling dispatch.
    """
    base = _implementer_base()
    width = 10
    group_idx, attempt, retry = 3, 0, 1

    # Main DAG-group dispatch: implementation.py actor_suffix
    # f"g{group_idx}-t{task_idx}-a{attempt}" (task_idx is the stable
    # per-group index, unique per task id).
    impl_names = [
        _make_parallel_actor(base, f"g{group_idx}-t{task_idx}-a{attempt}").name
        for task_idx in range(width)
    ]
    assert len(set(impl_names)) == width
    assert impl_names[0] == "implementer-g3-t0-a0"

    # Enhancement-group dispatch: actor_suffix f"enh-t{task_idx}-a{attempt}".
    enh_names = [
        _make_parallel_actor(base, f"enh-t{task_idx}-a{attempt}").name
        for task_idx in range(width)
    ]
    assert len(set(enh_names)) == width

    # Parallel-repair fix wave: f"dag-g{group_idx}-r{retry}-fix-{gid}"
    # (gid = bug-group id, unique per concurrent fix lane).
    repair_names = [
        _make_parallel_actor(
            base, f"dag-g{group_idx}-r{retry}-fix-BG-{lane}"
        ).name
        for lane in range(width)
    ]
    assert len(set(repair_names)) == width

    # Cross-lane: an implementation sibling, an enhancement sibling, and
    # a repair lane never collide with each other either.
    all_names = [*impl_names, *enh_names, *repair_names]
    assert len(set(all_names)) == len(all_names)

    # Retries of the SAME task get a fresh actor name (a{attempt}
    # participates), so a crash-retry never reuses a sibling's session.
    retry_name = _make_parallel_actor(base, f"g{group_idx}-t0-a1").name
    assert retry_name not in impl_names


def _sibling_dispatch_payload(task_idx: int, *, attempt: int = 0) -> dict:
    """A minimal dispatch-request mapping mirroring the per-task identity
    fields `_dispatcher_request_for_task` populates (implementation.py:
    task_id, per-task sandbox_id, retry, shared group/feature/dag)."""
    return {
        "feature_id": "feat-1",
        "dag_sha256": "d" * 64,
        "group_idx": 3,
        "task_id": f"TASK-{task_idx}",
        "retry": attempt,
        "actor_role": "implementer",
        "contract_ids": [f"contract-TASK-{task_idx}"],
        "sandbox_id": (
            f"dispatch-sandbox:feat-1:g3:t{task_idx}:a{attempt}:implementation"
        ),
        "workspace_snapshot_ids": ["snap-1"],
        "base_commit_by_repo": {"repo": "c" * 40},
        "runtime_policy_digest": "p" * 64,
        "prior_evidence_ids": [],
        "prompt_material_digest": "m" * 64,
        "output_schema_digest": "s" * 64,
        "retry_identity": {
            "retry": attempt,
            "dispatch_retry_id": (
                f"dispatch:feat-1:g3:t{task_idx}:TASK-{task_idx}"
                f":a{attempt}:implementation"
            ),
        },
    }


def test_dispatch_identity_unique_per_sibling_task_at_width_n() -> None:
    """Width-10 siblings produce 10 distinct idempotency keys, request
    digests, and sandbox ids — N concurrent journal rows and N sandbox
    allocations never collide, which is what makes the full-width gather
    safe against the dispatch journal and the sandbox allocator.
    """
    width = 10
    payloads = [_sibling_dispatch_payload(i) for i in range(width)]

    keys = {dispatch_idempotency_key(p) for p in payloads}
    digests = {dispatch_request_digest(p) for p in payloads}
    sandbox_ids = {p["sandbox_id"] for p in payloads}
    assert len(keys) == width
    assert len(digests) == width
    assert len(sandbox_ids) == width

    # A retry of the same task is a NEW identity (attempt participates),
    # while replaying the identical attempt is idempotent (same key).
    retry_payload = _sibling_dispatch_payload(0, attempt=1)
    assert dispatch_idempotency_key(retry_payload) not in keys
    assert dispatch_idempotency_key(
        _sibling_dispatch_payload(0)
    ) == dispatch_idempotency_key(_sibling_dispatch_payload(0))


def _wave_task(task_id: str, dependencies: list[str] | None = None) -> ImplementationTask:
    return ImplementationTask(
        id=task_id,
        name=f"Implement {task_id}",
        description=f"{task_id} work",
        subfeature_id="full-width",
        step_ids=["STEP-1"],
        requirement_ids=["REQ-full-width"],
        acceptance_criteria=[
            TaskAcceptanceCriterion(description=f"{task_id} acceptance"),
        ],
        reference_material=[
            TaskReference(source="Plan STEP-1", content=f"{task_id} ref"),
        ],
        verification_gates=["AC-full-width-1"],
        dependencies=dependencies or [],
    )


def test_wave_normalizer_serializes_only_true_edges_keeping_full_width() -> None:
    """An 8-task wave with one same-wave dependency chain (T-2 -> T-7):
    the normalizer moves ONLY the dependent task to a later wave; the
    seven independent tasks stay co-waved at full width. This is the
    planning-side invariant ("Implementation executes each wave in
    parallel" — task_planning._normalize_subfeature_execution_order)
    that makes the runtime's unbounded group gather dependency-safe.
    """
    task_ids = [f"T-{i}" for i in range(8)]
    tasks = [
        _wave_task(tid, dependencies=(["T-2"] if tid == "T-7" else []))
        for tid in task_ids
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[task_ids],  # planner emitted an invalid same-wave edge
        complete=True,
    )

    normalized, changed = TaskPlanningPhase._normalize_subfeature_execution_order(dag)

    assert changed is True
    assert normalized.execution_order == [
        ["T-0", "T-1", "T-2", "T-3", "T-4", "T-5", "T-6"],
        ["T-7"],
    ]

    # And a wave with NO intra-wave edges is left at full width untouched.
    independent = ImplementationDAG(
        tasks=[_wave_task(tid) for tid in task_ids],
        execution_order=[task_ids],
        complete=True,
    )
    normalized, changed = TaskPlanningPhase._normalize_subfeature_execution_order(
        independent
    )
    assert changed is False
    assert normalized.execution_order == [task_ids]
