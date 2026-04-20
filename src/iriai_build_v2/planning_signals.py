from __future__ import annotations

from dataclasses import dataclass

BACKGROUND_RESPONSE = "__planning_finish_in_background__"


@dataclass(frozen=True)
class GateRejection:
    feedback: str = ""
