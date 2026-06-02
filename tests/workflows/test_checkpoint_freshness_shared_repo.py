from __future__ import annotations

"""Regression coverage for the resume freshness gate scope gap (feature
8ac124d6, DAG groups 79/80): a sealed *queue-only* group must NOT be re-staled
just because a LATER group left a SHARED authorized repo dirty.

`_dag_group_checkpoint_is_fresh` used to run `_feature_repos_clean_for_checkpoint_
resume` as a short-circuit BEFORE delegating to the queue-checkpoint freshness
(`_dag_group_queue_checkpoint_is_fresh`, which accepts when the sealed group's
commits remain reachable from current heads). When a subsequent group's
failed/interrupted lane dirtied a repo the sealed group also owns, the clean
check returned False and the reachability fallback never ran, so the sealed
group was re-run on every resume (a hard block: its `done` lanes refuse
re-enqueue). The clean check now gates only the legacy commit-proof path; the
queue-only path accepts on the reachability proof alone.
"""

from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.develop.phases.implementation as impl


class _Artifacts:
    def __init__(self, values=None):
        self._values = values or {}

    async def get(self, key, *, feature=None):
        return self._values.get(key)


def _runner(artifact_values=None):
    return SimpleNamespace(artifacts=_Artifacts(artifact_values))


def _feature():
    return SimpleNamespace(id="8ac124d6")


def _checkpoint():
    return {
        "group_idx": 79,
        "verdict": "approved",
        "task_ids": ["t1", "t2"],
        "results": [],
        "commit_hash": "c1,c2",
    }


def _stub_common(monkeypatch, *, clean: bool, queue_fresh: bool):
    monkeypatch.setattr(impl, "_get_feature_root", lambda runner, feature: "/tmp/root")

    async def _auth_repos(*a, **k):
        return {"iriai-studio", "iriai-studio-backend"}

    async def _auth_sources(*a, **k):
        return ["/tmp/root/iriai-studio", "/tmp/root/iriai-studio-backend"]

    async def _queue_fresh(*a, **k):
        return queue_fresh

    monkeypatch.setattr(impl, "_checkpoint_authorized_repos", _auth_repos)
    monkeypatch.setattr(impl, "_checkpoint_authorized_repo_sources", _auth_sources)
    monkeypatch.setattr(impl, "_checkpoint_results_match_tasks", lambda checkpoint, task_ids: True)
    monkeypatch.setattr(impl, "_current_feature_repo_heads", lambda *a, **k: "head-a,head-b")
    monkeypatch.setattr(impl, "_feature_repos_clean_for_checkpoint_resume", lambda *a, **k: clean)
    monkeypatch.setattr(impl, "_dag_group_queue_checkpoint_is_fresh", _queue_fresh)


@pytest.mark.asyncio
async def test_queue_only_seal_is_fresh_despite_dirty_shared_repo(monkeypatch):
    # A later group dirtied a shared authorized repo (clean check would fail), but
    # the queue-only seal's commits remain reachable from current heads → FRESH.
    _stub_common(monkeypatch, clean=False, queue_fresh=True)
    fresh = await impl._dag_group_checkpoint_is_fresh(
        _runner(artifact_values={}),  # no legacy proofs → queue-only seal path
        _feature(),
        group_idx=79,
        group_task_ids=["t1", "t2"],
        dag_sha256="dag",
        checkpoint=_checkpoint(),
    )
    assert fresh is True


@pytest.mark.asyncio
async def test_queue_only_seal_stale_when_commits_unreachable(monkeypatch):
    # Same dirty-repo condition, but the queue freshness (reachability) fails —
    # e.g. the seal's commits were reset away — so it is genuinely stale.
    _stub_common(monkeypatch, clean=False, queue_fresh=False)
    fresh = await impl._dag_group_checkpoint_is_fresh(
        _runner(artifact_values={}),
        _feature(),
        group_idx=79,
        group_task_ids=["t1", "t2"],
        dag_sha256="dag",
        checkpoint=_checkpoint(),
    )
    assert fresh is False


@pytest.mark.asyncio
async def test_legacy_proof_seal_still_requires_clean_repos(monkeypatch):
    # A legacy commit-proof seal (proof artifacts present) still fails closed when
    # the authorized repos are dirty — the clean-check gate is preserved there.
    _stub_common(monkeypatch, clean=False, queue_fresh=True)
    fresh = await impl._dag_group_checkpoint_is_fresh(
        _runner(
            artifact_values={
                "dag-group-commit-proof:79": '{"artifact_schema": "dag-group-commit-proof-v1"}'
            }
        ),
        _feature(),
        group_idx=79,
        group_task_ids=["t1", "t2"],
        dag_sha256="dag",
        checkpoint=_checkpoint(),
    )
    assert fresh is False
