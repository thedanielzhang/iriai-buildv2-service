"""Unit tests for get_latest_sealed_checkpoint over a fake connection."""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.e2e.checkpoint import (
    get_latest_sealed_checkpoint,
)


class FakeConn:
    def __init__(self, sealed_group, lanes):
        self._sealed_group = sealed_group
        self._lanes = lanes
        self.queries: list[str] = []

    async def fetch(self, query, *args):
        self.queries.append(query)
        if "max(group_idx)" in query:
            return [{"g": self._sealed_group}]
        # lanes query — emulate WHERE group_idx == args[1]
        group_idx = args[1]
        return [r for r in self._lanes if r["group_idx"] == group_idx]


def _lane(item_id, group_idx, repo_id, commit, repo_path):
    return {
        "queue_item_id": item_id,
        "dag_sha256": "dag123",
        "group_idx": group_idx,
        "repo_id": repo_id,
        "result_commit": commit,
        "repo_path": repo_path,
    }


@pytest.mark.asyncio
async def test_multi_repo_picks_newest_lane_per_repo():
    lanes = [
        _lane(10, 79, "studioId", "studioA", "/x/repos/iriai-studio"),
        # backend has two done lanes; newest (id 19) wins
        _lane(17, 79, "beId", "be_old", "/x/repos/iriai-studio-backend"),
        _lane(19, 79, "beId", "be_new", "/x/repos/iriai-studio-backend"),
    ]
    conn = FakeConn(sealed_group=79, lanes=lanes)
    cp = await get_latest_sealed_checkpoint(conn, "8ac124d6")
    assert cp is not None
    assert cp.group_idx == 79
    assert cp.dag_sha256 == "dag123"
    commits = cp.result_commits()
    assert commits == {
        "iriai-studio": "studioA",
        "iriai-studio-backend": "be_new",
    }
    # all done lanes recorded
    assert set(cp.done_queue_item_ids) == {"10", "17", "19"}


@pytest.mark.asyncio
async def test_returns_none_when_no_sealed_group():
    conn = FakeConn(sealed_group=None, lanes=[])
    assert await get_latest_sealed_checkpoint(conn, "feat") is None


@pytest.mark.asyncio
async def test_max_group_idx_is_passed_through():
    lanes = [_lane(5, 70, "r", "c70", "/x/repos/r")]
    conn = FakeConn(sealed_group=70, lanes=lanes)
    cp = await get_latest_sealed_checkpoint(conn, "feat", max_group_idx=70)
    assert cp is not None and cp.group_idx == 70
    # the cap was forwarded as the 2nd bind param of the group query
    assert conn.queries[0].count("$2") >= 1


@pytest.mark.asyncio
async def test_repo_key_falls_back_to_repo_id_when_no_path():
    lanes = [_lane(1, 1, "opaquehash", "c", "")]
    conn = FakeConn(sealed_group=1, lanes=lanes)
    cp = await get_latest_sealed_checkpoint(conn, "feat")
    assert cp.result_commits() == {"opaquehash": "c"}
