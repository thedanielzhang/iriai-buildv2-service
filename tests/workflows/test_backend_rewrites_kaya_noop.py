"""P5: _BACKEND_PATH_REWRITES is iriai-studio-specific and a verified NO-OP on a
second product's (kaya) paths — the studio retired-prefix migration can never
match kaya backend paths, so no profile-threading is needed for kaya correctness
(and the studio rewrites still fire — AC-K-11 regression)."""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows._common._dag_paths import canonicalize_dag_path


@pytest.mark.parametrize("path", [
    "spend-client/src/app/api.py",
    "supply-chain/src-py/handlers/foo.py",
    "amber-service/src/index.ts",
    "common/docker/docker-compose.yaml",
])
def test_kaya_paths_pass_through_unchanged(path):
    canonical, rule = canonicalize_dag_path(path)
    assert canonical == path  # no studio prefix matches a kaya path
    assert rule is None


def test_studio_retired_prefix_still_rewrites():
    # AC-K-11: the studio canonicalization is unchanged.
    canonical, rule = canonicalize_dag_path("src/iriai_studio_backend/server.py")
    assert canonical == "iriai-studio-backend/iriai_studio_backend/server.py"
    assert rule == "bare-src"
