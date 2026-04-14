from __future__ import annotations

import sys
from pathlib import Path


def _prefer_canonical_iriai_compose() -> None:
    """Prefer the sibling iriai-compose checkout over editable lane paths.

    Bugflow lanes can temporarily repoint the editable ``iriai-compose`` install
    into a lane worktree, which makes bridge restarts import stale framework
    code. During local development we always want the canonical sibling repo:

        <workspace>/iriai-build-v2
        <workspace>/iriai-compose
    """

    canonical = Path(__file__).resolve().parents[3] / "iriai-compose"
    if not canonical.exists():
        return

    canonical_str = str(canonical)
    if canonical_str in sys.path:
        sys.path.remove(canonical_str)
    sys.path.insert(0, canonical_str)


_prefer_canonical_iriai_compose()
