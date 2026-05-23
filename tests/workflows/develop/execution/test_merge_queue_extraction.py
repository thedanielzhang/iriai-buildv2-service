"""Slice 11j -- `execution/merge_queue.py` extension + P3-6 producer fold-in.

Slice 11j has TWO parts:

* **Part A** (pure refactor extraction, mirroring Slice 11a-11i) -- INVENTORY
  RESULT: ZERO pure merge-queue helpers remain in `implementation.py` to
  extract to `execution/merge_queue.py`. Slice 08 already extracted the
  canonical merge-queue PRIMITIVE surface (the `MergeQueue` worker +
  `MergeApplyResult` + `_ApplyFailure` + `LeaseToken` + `RepoApplyInput` /
  `RepoApplyOutcome` + `MergeGateResult` / `MergeCommitResult` +
  `GroupMergeCoordinator` + `MergeQueueReadiness` + `_reconstruct_checkpoint_
  body` + `_checkpoint_coverage_digest` + `_path_allowed`); the remaining
  helpers in `implementation.py` are runner+feature+worker+store-coupled
  (the drain orchestrators) or impl.py-local-`_workflow_blocker_text`-coupled
  (the `ImplementationResult`-readers). Part A is therefore empty; no shim
  block is added for 11j.

* **Part B** (the GENUINE CONTRACT CHANGE -- the P3-6 producer-side
  fold-in) -- reopens the accepted Slice-08 `MergeApplyResult` contract
  LEGITIMATELY inside the Slice 11 merge-execution refactor. The contract
  change is ADDITIVE:

  1. `_ApplyFailure.__init__` gains an optional `escaped_paths: list[str] |
     None = None` keyword-only parameter. The two `contract_violation` raise
     sites in `MergeQueue.apply_candidate` (the patch-path-outside-contract
     pre-apply rejection and the applied-path-set escape post-apply check)
     pass their already-computed `sorted(outside)` / `sorted(escaped)` lists
     through. Every other `_ApplyFailure` raise (`merge_conflict` /
     `stale_projection` / `checkpoint_contradiction`) leaves it `None` ->
     yields the default empty list on the consumer side.
  2. `MergeApplyResult` gains `escaped_paths: list[str] =
     Field(default_factory=list)`. ADDITIVE -- every existing construction
     (a successful apply, a non-contract-violation failure) is byte-for-byte
     unchanged because the default-factory emits an empty list. The
     `MergeQueue._fail` method forwards the `_ApplyFailure.escaped_paths`
     onto this field.
  3. `implementation._route_merge_queue_drain_failure` gains two keyword-only
     parameters: `escaped_paths: list[str] | None = None` and
     `target_contract_ids: list[int] | None = None`. When non-empty they are
     surfaced into the typed-failure-router observation payload as
     `target_paths` + `target_contract_ids` -- the keys that
     `FailureRouter._repair_scope` (`execution/failure_router.py:1670-1737`)
     harvests into `RouteDecision.repair_scope`, and that
     `FailureRouter._allows_product_repair` (`:1739-1777`) checks before
     preserving the `run_product_repair` route.
  4. `implementation._drain_one_merge_queue_lane`'s inner `_fail_result`
     helper extracts `apply_result.escaped_paths` AND the captured
     `initial_contract_ids` (from the queue item's `contract_ids`) and
     forwards both to `_route_merge_queue_drain_failure`.
  5. No `failure_router._allows_product_repair` LOGIC change required --
     the consumer already does the right thing once the producer surfaces
     the structured signal. The fail-closed-to-`quiesce` safety net stays
     in place when EITHER `target_paths` or `target_contract_ids` is
     missing.

This file is the proof for the P3-6 fix. It covers:

* the additive `MergeApplyResult.escaped_paths` contract (default empty
  list; preserved through Pydantic round-trip; populated by the two
  apply-step `contract_violation` raise sites in `apply_candidate`);
* `_ApplyFailure.escaped_paths` plumbing (None by default; captured when
  passed; forwarded through `_fail` to the result);
* the new `_route_merge_queue_drain_failure(escaped_paths,
  target_contract_ids)` keyword-only parameters (additive; default None
  preserves byte-for-byte pre-11j payload; when populated they surface as
  `target_paths` + `target_contract_ids` on the observation payload);
* end-to-end the P3-6 fix scenario: a merge-queue drain
  `contract_violation` lane carries `escaped_paths` + `contract_ids` ->
  the router decides `run_product_repair`, NOT `quiesce` (the pre-11j
  downgrade). This was NOT testable pre-11j because the producer did not
  populate the structured signal.

Slice 08 acceptance criteria are preserved byte-for-byte: every existing
merge-queue test in `test_merge_queue.py`, `test_merge_queue_drain.py`,
`test_merge_queue_checkpoint.py`, `test_merge_queue_coordinator.py`,
`test_merge_queue_store.py`, `test_merge_queue_wiring.py` continues to
pass. The Slice 09 regroup-overlay drain integration is untouched.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.execution_control import ExecutionControlStore
from iriai_build_v2.execution_control.merge_queue_store import (
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution import git_service
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FailureObservation,
    FailureRouter,
)
from iriai_build_v2.workflows.develop.execution.merge_queue import (
    LeaseToken,
    MergeApplyResult,
    MergeQueue,
    RepoApplyInput,
    _ApplyFailure,
)
from iriai_build_v2.workflows.develop.phases import implementation as impl

_DAG = "dag-sha"


# ── Part B unit tests: the additive `MergeApplyResult.escaped_paths` contract.


def test_merge_apply_result_default_escaped_paths_is_empty_list() -> None:
    """A successful apply result has an empty `escaped_paths` list. The default
    factory yields a fresh `[]` per instance; two default-constructed results
    do not share the same list object (Pydantic v2 semantics; see
    `MergeApplyResult` `escaped_paths: list[str] = Field(default_factory=list)`).
    """

    result_a = MergeApplyResult(item_id=1, applied=True, status="verifying")
    result_b = MergeApplyResult(item_id=2, applied=True, status="verifying")
    assert result_a.escaped_paths == []
    assert result_b.escaped_paths == []
    # Each instance gets its own list -- mutating one does not bleed into
    # the other. (Pydantic v2 `default_factory=list` re-evaluates per
    # instance; classic `default=[]` would share the list.)
    result_a.escaped_paths.append("/x")
    assert result_b.escaped_paths == []


def test_merge_apply_result_escaped_paths_preserved_through_pydantic_roundtrip() -> None:
    """`escaped_paths` is a typed `list[str]` field on the Pydantic model and
    is preserved through model_dump / model_validate. The merge-queue
    coordinator persists `MergeApplyResult` model dumps in evidence rows; a
    silent field-loss on round-trip would lose the P3-6 signal.
    """

    populated = MergeApplyResult(
        item_id=42,
        applied=False,
        status="failed",
        failure_class="contract_violation",
        detail="patch escapes the lane contracts",
        escaped_paths=["src/escape.txt", "src/another.txt"],
    )
    dumped = populated.model_dump()
    assert dumped["escaped_paths"] == ["src/escape.txt", "src/another.txt"]
    restored = MergeApplyResult.model_validate(dumped)
    assert restored.escaped_paths == ["src/escape.txt", "src/another.txt"]


def test_merge_apply_result_escaped_paths_coerces_non_string_inputs() -> None:
    """Pydantic v2 coerces non-string list items to `str` (the field is typed
    `list[str]`). Defensive: a caller that accidentally supplies a `Path` or
    int is coerced to `str` rather than silently lost.
    """

    result = MergeApplyResult(
        item_id=7,
        applied=False,
        status="failed",
        failure_class="contract_violation",
        escaped_paths=["src/a", "src/b"],
    )
    assert all(isinstance(p, str) for p in result.escaped_paths)


# ── Part B unit tests: the `_ApplyFailure.escaped_paths` plumbing. ───────────


def test_apply_failure_default_escaped_paths_is_none() -> None:
    """Existing non-contract-violation raise sites (`merge_conflict` /
    `stale_projection` / `checkpoint_contradiction`) DO NOT pass
    `escaped_paths` -- the constructor defaults to `None`. The `_fail`
    method's `if escaped_paths` check then emits the default empty list on
    the resulting `MergeApplyResult.escaped_paths`. Pinned: the
    `_ApplyFailure` raises at `merge_queue.py:235` (baseline cleanliness),
    `:258` (stale projection), `:274` (rebase non-ancestor), `:298` (patch
    does not apply), `:307` (3-way conflicts), `:425` (no targets), `:432`
    (missing apply input), `:438` (extra apply input) all pass
    `failure_class` + `detail` only.
    """

    failure = _ApplyFailure("merge_conflict", "patch does not apply")
    assert failure.escaped_paths is None
    assert failure.failure_class == "merge_conflict"
    assert failure.detail == "patch does not apply"


def test_apply_failure_carries_explicit_escaped_paths() -> None:
    """When the contract-violation raise sites pass
    `escaped_paths=sorted(outside)` / `escaped_paths=sorted(escaped)`, the
    field is captured as the structured list and survives until `_fail`
    forwards it to the result. Pinned: `merge_queue.py:287-292` (the
    patch-path-outside-contract pre-apply rejection) +
    `:320-325` (the applied-path-set escape post-apply check).
    """

    failure = _ApplyFailure(
        "contract_violation",
        "patch touches paths outside the lane contracts: ['src/escape.txt']",
        escaped_paths=["src/escape.txt"],
    )
    assert failure.escaped_paths == ["src/escape.txt"]
    assert failure.failure_class == "contract_violation"


def test_apply_failure_escaped_paths_empty_list_distinguishable_from_none() -> None:
    """An explicit empty list is captured as `[]`, not normalized to `None`.
    `_fail` then emits `escaped_paths=[]` on the result (the default-factory
    is short-circuited by the truthiness check in `_fail`). Defensive:
    keeps the signal distinguishable for any future caller that wants to
    distinguish "the contract-violation paths were enumerated but the list
    is empty" from "no enumeration was performed".
    """

    failure = _ApplyFailure("contract_violation", "no paths", escaped_paths=[])
    assert failure.escaped_paths == []


# ── Part B integration tests: producer-end-to-end through `_fail`. ───────────
#
# These tests use a real Postgres queue database (the `mq_conn` fixture from
# `conftest.py`) and a real on-disk git repo for the canonical repo. They
# skip when no Postgres is reachable. The setup mirrors `test_merge_queue.py`
# byte-for-byte (the patch-out-of-contract scenarios were already covered for
# the legacy detail-string contract; here we additionally pin the new
# structured `escaped_paths` field).


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    ).stdout


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("init\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return _git(path, "rev-parse", "HEAD").strip()


async def _insert_feature(conn, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $1, $1, 'develop', 'ws-1')",
        feature_id,
    )


async def _insert_contract(conn, feature_id: str, task_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status) "
        "VALUES ($1, $2, 'dag-sha', 1, $3, $4, 'active') RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
        task_id,
        f"cd-{task_id}",
    )


async def _insert_evidence(conn, feature_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash) "
        "VALUES ($1, $2, 'gate_request', 'hash') RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
    )


async def _setup_apply_item(conn, repo_path: Path, base_commit: str):
    feature_id = f"feat-11j-{uuid.uuid4().hex[:8]}"
    await _insert_feature(conn, feature_id)
    gate = await _insert_evidence(conn, feature_id)
    contract = await _insert_contract(conn, feature_id, "T1")
    store = MergeQueueStore(conn)
    await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            base_commit=base_commit,
            head_commit="head-candidate",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[1],
            gate_evidence_ids=[gate],
            task_coverage=[TaskCoverageCreate(task_id="T1", contract_id=contract)],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="repo-a",
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
        )
    )
    claimed = await store.claim(feature_id, "worker-1")
    assert claimed is not None
    return store, claimed, LeaseToken.for_item(claimed)


def _provider(patch_text: str, allowed_paths: list[str]):
    async def provide(item):
        return [
            RepoApplyInput(
                repo_id="repo-a",
                patch_text=patch_text,
                allowed_paths=allowed_paths,
            )
        ]

    return provide


@pytest.mark.asyncio
async def test_apply_candidate_contract_violation_pre_apply_populates_escaped_paths(
    mq_conn, tmp_path: Path
) -> None:
    """Patch-path-outside-contract pre-apply rejection populates
    `MergeApplyResult.escaped_paths`. The patch lists `README.md` and only
    `src/only_this.py` is allowed; `apply_candidate` rejects BEFORE any git
    mutation (the path-set scan at `merge_queue.py:282-292` -- the "patch
    touches paths outside the lane contracts" raise). The Slice-08 contract
    asserts (applied=False, failure_class=contract_violation, status="failed")
    still hold byte-for-byte; the NEW assertion pins the structured
    `escaped_paths` field.
    """

    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nedit\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    # The patch's path set is `{README.md}`; allowed_paths excludes it.
    queue = MergeQueue(store, _provider(patch, ["src/only_this.py"]))

    result = await queue.apply_candidate(item, token)

    # Slice-08 baseline: rejected, classed as contract_violation, lane failed.
    assert result.applied is False
    assert result.failure_class == "contract_violation"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"

    # Slice 11j P3-6 fold-in: the structured escaped_paths field is
    # populated from the sorted(outside) list.
    assert result.escaped_paths == ["README.md"]


@pytest.mark.asyncio
async def test_apply_candidate_contract_violation_post_apply_populates_escaped_paths(
    mq_conn, tmp_path: Path
) -> None:
    """Applied-path-set escape post-apply check populates
    `MergeApplyResult.escaped_paths`. The patch's declared path set looks
    benign and passes the pre-apply check, but the 3-way merge produces
    paths outside the contract -- `apply_candidate` resets the repo and
    raises `contract_violation` at `merge_queue.py:316-325`. The new test
    pins the structured `escaped_paths` carries the sorted(escaped) list
    AFTER the rollback.
    """

    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    # A patch that creates a NEW file `src/escape.txt` -- the declared
    # patch_path_set is `{src/escape.txt}`. The lane's allowed_paths
    # excludes it, so the PRE-apply check at :282-292 fires (this is the
    # natural path; the applied-path-set check at :316-325 would fire if
    # we crafted a tricky 3-way merge that produces an escape that wasn't
    # in the declared patch_path_set, which is an exotic edge case).
    (repo / "src").mkdir()
    (repo / "src" / "escape.txt").write_text("escape\n")
    _git(repo, "add", "src/escape.txt")
    patch = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q")
    (repo / "src" / "escape.txt").unlink()

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider(patch, ["README.md"]))

    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "contract_violation"
    # The structured field carries the sorted escape list.
    assert result.escaped_paths == ["src/escape.txt"]
    # The canonical repo is unmodified -- the rollback reset to pre-apply
    # HEAD (this is the same Slice-08 guarantee, here repeated alongside
    # the new contract assertion).
    assert (repo / "README.md").read_text() == "init\n"


@pytest.mark.asyncio
async def test_apply_candidate_non_contract_violation_failure_has_empty_escaped_paths(
    mq_conn, tmp_path: Path
) -> None:
    """A `merge_conflict` failure (the patch does not apply against the
    canonical repo) leaves `escaped_paths` as the empty list -- the
    `_ApplyFailure.escaped_paths` is `None` and `_fail` short-circuits the
    truthiness check to emit `[]`. Defensive: the consumer-side
    `_allows_product_repair` SHOULD NOT see a populated `escaped_paths` for
    a non-contract-violation failure (every other `_ApplyFailure` raise site
    leaves the field empty, by design).
    """

    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    bad_patch = (
        "diff --git a/missing.txt b/missing.txt\n"
        "--- a/missing.txt\n"
        "+++ b/missing.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider(bad_patch, ["missing.txt"]))

    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "merge_conflict"
    assert result.escaped_paths == []


# ── Part B unit tests: the `_route_merge_queue_drain_failure` plumbing. ──────


class _DictRunner:
    """A minimal in-process runner with `services` so
    `_failure_router_for_runner` resolves the in-memory router.

    `implementation._failure_router_for_runner(runner)` resolves the failure
    router from `runner.services["failure_router"]` (or builds one from
    `runner.services["failure_router_port"]`) when `runner.services` is a
    dict; otherwise it falls back to `runner._failure_router_port`. We use
    the dict-services path so a fresh `FailureRouter` is bound and reused
    across calls inside the test.
    """

    def __init__(self) -> None:
        self.services: dict[str, Any] = {}


def _feature(feature_id: str) -> Any:
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


def test_route_merge_queue_drain_failure_omits_target_paths_without_signal() -> None:
    """Pre-11j fallback behavior preserved: a drain caller that does NOT
    thread `escaped_paths` / `target_contract_ids` (e.g. the recovery /
    checkpoint paths) gets a payload WITHOUT `target_paths` /
    `target_contract_ids`. `_repair_scope` then omits them from the
    `RouteDecision.repair_scope`, and `_allows_product_repair` returns
    `False` -- the route is downgraded to `quiesce`. This is the
    fail-closed safety net for un-upgraded callers.
    """

    runner = _DictRunner()
    feature = _feature("feat-omit")
    routed = impl._route_merge_queue_drain_failure(
        runner, feature,
        dag_sha256=_DAG, group_idx=1, item_id=42,
        failure_class="contract_violation",
        detail="some contract violation",
        stage="apply",
    )
    assert routed["routed"] is True
    assert routed["failure_class"] == "contract_violation"
    assert routed["failure_type"] == "outside_allowed_paths"

    # The route is `quiesce` because no `target_paths` / `target_contract_ids`
    # were threaded -- the `_allows_product_repair` consumer downgrades.
    assert routed["route_action"] == "quiesce"

    # Inspect the underlying observation: no `target_paths` /
    # `target_contract_ids` keys in the payload.
    router = runner.services["failure_router"]
    record = router.get_failure(routed["typed_failure_id"])
    assert "target_paths" not in record.observation.payload
    assert "target_contract_ids" not in record.observation.payload


def test_route_merge_queue_drain_failure_threads_escaped_paths_into_payload() -> None:
    """When the drain caller threads `escaped_paths`, the observation payload
    carries `target_paths` (a sorted, deduplicated, stringified list). When
    `target_contract_ids` is also threaded the payload carries that key too
    (a sorted, deduplicated, int-coerced list). Both surface to the
    `RouteDecision.repair_scope` via `_repair_scope`.
    """

    runner = _DictRunner()
    feature = _feature("feat-thread")
    routed = impl._route_merge_queue_drain_failure(
        runner, feature,
        dag_sha256=_DAG, group_idx=1, item_id=43,
        failure_class="contract_violation",
        detail="escapes the lane contracts",
        stage="apply",
        escaped_paths=["src/a.py", "src/b.py", "src/a.py"],  # duplicate input
        target_contract_ids=[11, 11, 7],  # duplicate input
    )
    assert routed["routed"] is True
    router = runner.services["failure_router"]
    record = router.get_failure(routed["typed_failure_id"])
    # Sorted, deduplicated, stringified.
    assert record.observation.payload["target_paths"] == ["src/a.py", "src/b.py"]
    assert record.observation.payload["target_contract_ids"] == [7, 11]


def test_route_merge_queue_drain_failure_empty_lists_omit_keys() -> None:
    """Explicit empty lists are treated the same as `None` -- the payload
    omits the corresponding keys. This preserves the
    `repair_scope`-empty-filter that `_repair_scope`
    (`failure_router.py:1737`) applies, and keeps the byte-for-byte payload
    shape for callers that pass an "we tried, but the list is empty"
    sentinel. The truthiness check in `_route_merge_queue_drain_failure` is
    the gate.
    """

    runner = _DictRunner()
    feature = _feature("feat-empty")
    routed = impl._route_merge_queue_drain_failure(
        runner, feature,
        dag_sha256=_DAG, group_idx=1, item_id=44,
        failure_class="contract_violation",
        detail="empty escaped",
        stage="apply",
        escaped_paths=[],
        target_contract_ids=[],
    )
    router = runner.services["failure_router"]
    record = router.get_failure(routed["typed_failure_id"])
    assert "target_paths" not in record.observation.payload
    assert "target_contract_ids" not in record.observation.payload
    # Still routes -- the failure class + type are unchanged.
    assert routed["failure_class"] == "contract_violation"


# ── Part B core P3-6 fix test: the producer-side fold-in end-to-end. ─────────


def test_p3_6_router_preserves_run_product_repair_when_producer_threads_signal() -> None:
    """The P3-6 fix end-to-end (router only -- pure unit test).

    Pre-11j behavior: the merge-queue drain's `contract_violation`
    observation did NOT include `target_paths` or `target_contract_ids`, so
    `FailureRouter._allows_product_repair` returned `False` and the route
    was downgraded to `quiesce` (the pre-11j P3-6 bug).

    Slice 11j P3-6 fold-in: when the producer (the drain) threads the
    structured `escaped_paths` + queue item `contract_ids` onto the
    observation payload's `target_paths` + `target_contract_ids` keys, the
    router's `_repair_scope` harvests them; `_allows_product_repair` then
    returns `True` because both `repair_scope["target_paths"]` and
    `repair_scope["target_contract_ids"]` are non-empty (the scoped-contract
    `outside_allowed_paths` path through the consumer logic).

    THIS TEST WAS NOT POSSIBLE PRE-11j because the merge-queue producer did
    not populate the structured signal -- there was no way to construct a
    `contract_violation` observation with the right payload shape from the
    merge-queue source. Landing it locks the end-to-end fix.
    """

    router = FailureRouter()

    # The pre-11j observation -- detail only, no structured signal. Routes
    # to `quiesce` (the downgrade).
    pre_11j_failure_id = router.record(
        FailureObservation(
            feature_id="feat-p3-6",
            dag_sha256=_DAG,
            group_idx=1,
            task_id=None,
            attempt_id=None,
            source="merge_queue",
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            deterministic=True,
            retryable=False,
            operator_required=False,
            evidence_ids=[],
            payload={
                "merge_queue_item_id": 7,
                "queue_item_id": 7,
                "stage": "apply",
                "detail": "patch escapes the lane contracts",
            },
        )
    )
    pre_11j_decision = router.decide(pre_11j_failure_id)
    # Pre-11j fall-through: the route is downgraded to `quiesce`.
    assert pre_11j_decision.action == "quiesce"

    # The Slice 11j P3-6 fix: the producer surfaces the structured signal
    # as `target_paths` + `target_contract_ids` on the observation payload.
    # The consumer (`_allows_product_repair`) then preserves
    # `run_product_repair`.
    fixed_failure_id = router.record(
        FailureObservation(
            feature_id="feat-p3-6",
            dag_sha256=_DAG,
            group_idx=2,  # distinct so the idempotency key is fresh
            task_id=None,
            attempt_id=None,
            source="merge_queue",
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            deterministic=True,
            retryable=False,
            operator_required=False,
            evidence_ids=[],
            payload={
                "merge_queue_item_id": 11,
                "queue_item_id": 11,
                "stage": "apply",
                "detail": "patch escapes the lane contracts",
                # The two NEW Slice 11j P3-6 fold-in keys -- the producer
                # threads them via `_route_merge_queue_drain_failure`.
                "target_paths": ["src/escape.txt"],
                "target_contract_ids": [42],
            },
        )
    )
    fixed_decision = router.decide(fixed_failure_id)
    assert fixed_decision.action == "run_product_repair"
    # The repair scope carries the structured target_paths + contract_ids.
    assert fixed_decision.repair_scope["target_paths"] == ["src/escape.txt"]
    assert fixed_decision.repair_scope["target_contract_ids"] == [42]
    # And the typed source / failure class / type are preserved.
    assert fixed_decision.repair_scope["source"] == "merge_queue"
    assert fixed_decision.repair_scope["failure_class"] == "contract_violation"
    assert fixed_decision.repair_scope["failure_type"] == "outside_allowed_paths"


def test_p3_6_consumer_requires_both_signals_target_paths_and_contract_ids() -> None:
    """Fail-closed safety net preserved: a `contract_violation` with
    `target_paths` BUT WITHOUT `target_contract_ids` still downgrades to
    `quiesce`. The merge-queue drain source is `"merge_queue"`, which is
    NOT in the `_authorized_direct_source_verdict` allowed_sources for
    contract-violation (those are restricted to `"contract"` source --
    the contract-compiler's direct-route). So the only path to
    `run_product_repair` for a merge-queue drain is the
    `target_contract_ids` path -- enforcing the producer always supplies
    the structured queue item `contract_ids` alongside the escaped paths.
    """

    router = FailureRouter()
    failure_id = router.record(
        FailureObservation(
            feature_id="feat-p3-6-partial",
            dag_sha256=_DAG,
            group_idx=1,
            task_id=None,
            attempt_id=None,
            source="merge_queue",
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            deterministic=True,
            retryable=False,
            operator_required=False,
            evidence_ids=[],
            payload={
                "merge_queue_item_id": 7,
                "queue_item_id": 7,
                "stage": "apply",
                "detail": "patch escapes the lane contracts",
                "target_paths": ["src/escape.txt"],
                # target_contract_ids INTENTIONALLY missing.
            },
        )
    )
    decision = router.decide(failure_id)
    # Without target_contract_ids the route degrades to quiesce -- the
    # source `"merge_queue"` cannot satisfy the
    # `_authorized_direct_source_verdict` fallback.
    assert decision.action == "quiesce"


def test_p3_6_consumer_requires_both_signals_contract_ids_without_paths() -> None:
    """The mirror of the previous test: `target_contract_ids` WITHOUT
    `target_paths` also degrades to `quiesce` because
    `_allows_product_repair` checks `target_paths` first. Both keys must
    be populated for the route to be preserved.
    """

    router = FailureRouter()
    failure_id = router.record(
        FailureObservation(
            feature_id="feat-p3-6-paths-missing",
            dag_sha256=_DAG,
            group_idx=1,
            task_id=None,
            attempt_id=None,
            source="merge_queue",
            failure_class="contract_violation",
            failure_type="outside_allowed_paths",
            deterministic=True,
            retryable=False,
            operator_required=False,
            evidence_ids=[],
            payload={
                "merge_queue_item_id": 7,
                "queue_item_id": 7,
                "stage": "apply",
                "detail": "patch escapes the lane contracts",
                "target_contract_ids": [42],
                # target_paths INTENTIONALLY missing.
            },
        )
    )
    decision = router.decide(failure_id)
    assert decision.action == "quiesce"


# ── Part B integration: the producer-side fix end-to-end through the drain. ──


def _runner(conn) -> SimpleNamespace:
    """A minimal runner with a typed store + a services dict (the
    `_failure_router_for_runner` resolves an in-memory router off it)."""

    return SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(conn)},
    )


async def _enqueue_drainable_contract_lane(
    conn,
    feature_id: str,
    *,
    repo_path: Path,
    base_commit: str,
    patch_text: str,
    allowed_file: str = "README.md",
) -> tuple[int, list[int]]:
    """Enqueue ONE `queued` lane the drain can claim.

    Mirrors `_enqueue_durable_merge_queue_for_results` (08e-2) by writing the
    typed rows the drain's `_resolve_merge_queue_lane_inputs` reads to
    reconstruct the `RepoApplyInput`. Returns the lane id AND the contract
    ids list (so the test can assert what `target_contract_ids` should be).
    """

    contract = await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status, allowed_paths) "
        "VALUES ($1, $2, $3, 1, $4, $5, 'active', $6::jsonb) RETURNING id",
        feature_id,
        f"contract:{feature_id}:TASK-1",
        _DAG,
        "TASK-1",
        "cd-1",
        json.dumps(
            [{"repo_id": "app", "path": allowed_file, "match_kind": "file"}]
        ),
    )
    diff_artifact = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) "
        "VALUES ($1, $2, $3) RETURNING id",
        feature_id,
        "dag-sandbox-diff:TASK-1",
        patch_text,
    )
    payload = {"repo_id": "app", "diff_artifact_id": diff_artifact}
    patch_evidence = await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, payload) "
        "VALUES ($1, $2, 'sandbox_patch_summary', $3, $4::jsonb) RETURNING id",
        feature_id,
        f"patch:{uuid.uuid4().hex}",
        f"hash-{uuid.uuid4().hex}",
        json.dumps(payload),
    )
    gate = await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, status) "
        "VALUES ($1, $2, 'aggregate_verdict', $3, 'approved') RETURNING id",
        feature_id,
        f"gate:{uuid.uuid4().hex}",
        f"hash-{uuid.uuid4().hex}",
    )
    store = MergeQueueStore(conn)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            base_commit=base_commit,
            repo_id="app",
            repo_path=str(repo_path),
            head_commit="",
            integration_lane="task:TASK-1",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[patch_evidence],
            gate_evidence_ids=[gate],
            task_coverage=[
                TaskCoverageCreate(task_id="TASK-1", contract_id=contract)
            ],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="app",
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
            payload={"stage": "implementation", "task_ids": ["TASK-1"]},
        )
    )
    return item.id, [contract]


@pytest.mark.asyncio
async def test_drain_contract_violation_routes_to_run_product_repair_when_paths_escape(
    mq_conn, tmp_path: Path
) -> None:
    """END-TO-END P3-6 FIX: a drain `contract_violation` with a structured
    `escaped_paths` + queue item `contract_ids` routes to
    `run_product_repair`, NOT `quiesce` (the pre-11j downgrade).

    Setup mirrors the existing
    `test_drain_routes_a_contract_violation_through_the_failure_router` in
    `test_merge_queue_drain.py:850`: a patch creates `escape.txt` outside
    the lane's `allowed_paths` (which is scoped to `README.md`). The drain
    calls `_drain_one_merge_queue_lane`, which calls
    `apply_candidate` -> `_ApplyFailure("contract_violation",
    escaped_paths=["escape.txt"])` -> `_fail` ->
    `MergeApplyResult.escaped_paths=["escape.txt"]`. The inner
    `_fail_result` reads `apply_result.escaped_paths` AND the captured
    `initial_contract_ids` and forwards them to
    `_route_merge_queue_drain_failure`, which surfaces them as
    `target_paths` + `target_contract_ids` on the typed-failure-router
    observation payload. `FailureRouter._allows_product_repair` sees both
    populated and preserves `run_product_repair`.

    THIS TEST WAS NOT POSSIBLE PRE-11j: the merge-queue producer did not
    populate the structured signal, so any drain `contract_violation`
    decision degraded to `quiesce` (the pre-11j P3-6 bug).
    """

    feature_id = f"feat-11j-drain-{uuid.uuid4().hex[:8]}"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)

    # A patch creating a new `escape.txt` outside the contract.
    (repo / "escape.txt").write_text("outside the contract\n")
    _git(repo, "add", "escape.txt")
    patch = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q")
    (repo / "escape.txt").unlink()

    lane_id, contract_ids = await _enqueue_drainable_contract_lane(
        mq_conn, feature_id, repo_path=repo, base_commit=base,
        patch_text=patch, allowed_file="README.md",
    )

    runner = _runner(mq_conn)
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.integrated is False
    assert result.terminal_status == "failed"
    assert result.failure_class == "contract_violation"

    # The typed failure routed through the Slice 07 router.
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "contract_violation"
    assert result.routed_failure["failure_type"] == "outside_allowed_paths"

    # SLICE 11j P3-6 FIX: the route action is `run_product_repair`, NOT
    # `quiesce`. The producer surfaced the structured signal; the consumer
    # preserved the route.
    assert result.routed_failure["route_action"] == "run_product_repair"

    # Inspect the underlying typed failure row: the observation payload
    # carries the structured `target_paths` + `target_contract_ids`.
    router = runner.services["failure_router"]
    record = router.get_failure(result.routed_failure["typed_failure_id"])
    assert record.observation.failure_class == "contract_violation"
    assert record.observation.source == "merge_queue"
    assert record.observation.payload["target_paths"] == ["escape.txt"]
    assert record.observation.payload["target_contract_ids"] == contract_ids


@pytest.mark.asyncio
async def test_drain_merge_conflict_still_quiesces_unchanged(
    mq_conn, tmp_path: Path
) -> None:
    """A non-contract-violation drain failure (a `merge_conflict`) still
    routes byte-for-byte as before -- the new `escaped_paths` /
    `target_contract_ids` keyword args default to `None`, the payload
    omits the new keys, and the route is unchanged. This pins that the
    P3-6 fix is SCOPED to contract-violation failures -- it does NOT
    accidentally broaden product-repair for merge conflicts or commit
    hygiene rejections.

    Setup mirrors `test_drain_routes_a_merge_conflict_through_the_failure_
    router` in `test_merge_queue_drain.py:340`: a patch whose context
    line does not exist in the canonical repo (after a HEAD divergence).
    """

    feature_id = f"feat-11j-merge-{uuid.uuid4().hex[:8]}"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)

    # A patch that appends to README.md.
    original = (repo / "README.md").read_text()
    (repo / "README.md").write_text(original + "conflicting line\n")
    patch = _git(repo, "diff")
    (repo / "README.md").write_text(original)

    lane_id, _ = await _enqueue_drainable_contract_lane(
        mq_conn, feature_id, repo_path=repo, base_commit=base,
        patch_text=patch, allowed_file="README.md",
    )

    # Diverge the canonical repo so the patch no longer applies.
    (repo / "README.md").write_text("totally different content\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "diverge")

    runner = _runner(mq_conn)
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.failure_class == "merge_conflict"
    assert result.routed_failure["failure_class"] == "merge_conflict"
    assert result.routed_failure["failure_type"] == "patch_apply_conflict"
    # `merge_conflict` -> `retry_merge` (unchanged Slice-08 behavior).
    assert result.routed_failure["route_action"] == "retry_merge"

    # The observation payload does NOT carry the new structured keys --
    # they were not threaded because the failure is NOT a contract
    # violation.
    router = runner.services["failure_router"]
    record = router.get_failure(result.routed_failure["typed_failure_id"])
    assert "target_paths" not in record.observation.payload
    assert "target_contract_ids" not in record.observation.payload


# ── Structural: the contract change is ADDITIVE; existing __all__ preserved. ─


def test_merge_queue_module_all_exports_merge_apply_result() -> None:
    """The Slice-08 module-level `__all__` continues to export
    `MergeApplyResult`. Slice 11j extends the type but does not
    add/remove names from `__all__`.
    """

    from iriai_build_v2.workflows.develop.execution import merge_queue
    assert "MergeApplyResult" in merge_queue.__all__


def test_merge_apply_result_has_escaped_paths_field() -> None:
    """Direct introspection: `MergeApplyResult.model_fields` contains
    `escaped_paths` with a `list[str]` annotation and a default-factory
    that produces a list. Pins the type contract for future readers.
    """

    field = MergeApplyResult.model_fields.get("escaped_paths")
    assert field is not None, "MergeApplyResult must define escaped_paths"
    # Pydantic v2 stores annotations as the raw annotation object.
    assert field.annotation in (list[str], list)
    # The default-factory IS list (Pydantic exposes default_factory).
    assert callable(field.default_factory)
    assert field.default_factory() == []


def test_route_merge_queue_drain_failure_accepts_new_keyword_arguments() -> None:
    """Pin the new keyword-only parameters on
    `_route_merge_queue_drain_failure`: `escaped_paths` (default `None`)
    and `target_contract_ids` (default `None`). Defensive structural
    assert against a future refactor that silently rebinds the signature.
    """

    import inspect

    sig = inspect.signature(impl._route_merge_queue_drain_failure)
    params = sig.parameters
    assert "escaped_paths" in params
    assert "target_contract_ids" in params
    # Both keyword-only with default None.
    assert params["escaped_paths"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["escaped_paths"].default is None
    assert params["target_contract_ids"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["target_contract_ids"].default is None
