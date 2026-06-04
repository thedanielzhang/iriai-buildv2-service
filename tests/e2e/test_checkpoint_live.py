"""Live read-only proof of AC1 against feature 8ac124d6.

Skips cleanly when the live DB / feature is unavailable, so it is safe in
``pytest tests/`` everywhere. When the DB is present it asserts the newest
sealed checkpoint (>= group 79) with valid per-repo commits and ZERO advisory
locks held by our backend.
"""

from __future__ import annotations

import re

import pytest

from iriai_build_v2.workflows.develop.e2e.checkpoint import (
    DEFAULT_DSN,
    get_latest_sealed_checkpoint,
    open_readonly_pool,
)

FEATURE = "8ac124d6"
KNOWN_G79 = {
    "iriai-studio": "0d480cd7f0ab17ca572385056d035d3953c4226a",
    "iriai-studio-backend": "de29d86a84e097d6c2c0364aa5bdc6a522727158",
}
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


@pytest.mark.asyncio
async def test_latest_sealed_checkpoint_live():
    try:
        pool = await open_readonly_pool(DEFAULT_DSN)
    except Exception as exc:  # noqa: BLE001 - environment without the live DB
        pytest.skip(f"live DB unavailable: {exc}")
    try:
        async with pool.acquire() as conn:
            tr = conn.transaction(isolation="repeatable_read", readonly=True)
            await tr.start()
            try:
                cp = await get_latest_sealed_checkpoint(conn, FEATURE)
                locks = await conn.fetch(
                    "SELECT 1 FROM pg_locks WHERE pid = pg_backend_pid() "
                    "AND locktype = 'advisory'"
                )
            finally:
                await tr.rollback()
    finally:
        await pool.close()

    if cp is None:
        pytest.skip("feature 8ac124d6 has no sealed checkpoint in this DB")

    # No feature advisory lock acquired by our read-only path.
    assert len(locks) == 0

    # Newest sealed group is >= the pinned baseline (workflow may have advanced).
    assert cp.group_idx >= 79
    commits = cp.result_commits()
    assert "iriai-studio" in commits
    for repo, sha in commits.items():
        assert _HEX40.match(sha), f"{repo} commit not a full sha: {sha}"

    # At the pinned baseline the commits are exactly known.
    if cp.group_idx == 79:
        assert commits["iriai-studio"] == KNOWN_G79["iriai-studio"]
        assert commits["iriai-studio-backend"] == KNOWN_G79["iriai-studio-backend"]
