from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Protocol

from .models import (
    ActionLevel,
    EvidencePacket,
    FailureClass,
    SupervisorActionRecord,
    SupervisorActionStatus,
    SupervisorMode,
    action_key,
)

RestartCallable = Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]]


class ArtifactSink(Protocol):
    async def put(self, key: str, value: Any, *, feature: Any) -> None: ...


class ActionPolicy:
    def __init__(
        self,
        *,
        mode: SupervisorMode = SupervisorMode.READ_ONLY,
        restart: RestartCallable | None = None,
        artifact_sink: ArtifactSink | None = None,
        feature: Any | None = None,
    ) -> None:
        self.mode = mode
        self.restart = restart
        self.artifact_sink = artifact_sink
        self.feature = feature

    async def maybe_restart(
        self,
        packet: EvidencePacket,
        *,
        active_invocation: bool = False,
    ) -> SupervisorActionRecord:
        reason = _restart_reason(packet, active_invocation)
        if reason:
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0),
                action="restart_bridge",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason=reason,
                packet=packet,
            )
            await self._write(record, "blocked")
            return record
        if self.mode != SupervisorMode.GUARDED:
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0),
                action="restart_bridge",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason="Read-only mode records recommendations but does not mutate bridge state.",
                packet=packet,
            )
            await self._write(record, "blocked")
            return record
        if self.restart is None:
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0),
                action="restart_bridge",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason="No restart client/callable was provided.",
                packet=packet,
            )
            await self._write(record, "blocked")
            return record
        planned = SupervisorActionRecord(
            feature_id=packet.feature_id,
            cursor=int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0),
            action="restart_bridge",
            mode=self.mode,
            status=SupervisorActionStatus.PLANNED,
            reason="Safe-boundary bridge restart approved by guarded policy.",
            packet=packet,
        )
        await self._write(planned, "planned")
        try:
            response = self.restart()
            after = await response if inspect.isawaitable(response) else response
        except Exception as exc:
            failed = planned.model_copy(
                update={
                    "status": SupervisorActionStatus.FAILED,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            await self._write(failed, "failed")
            return failed
        completed = planned.model_copy(
            update={
                "status": SupervisorActionStatus.COMPLETED,
                "after": after if isinstance(after, dict) else {"result": after},
            }
        )
        await self._write(completed, "completed")
        return completed

    async def _write(self, record: SupervisorActionRecord, suffix: str) -> None:
        if self.artifact_sink is None or self.feature is None:
            return
        await self.artifact_sink.put(
            action_key(record.feature_id, record.cursor, record.action, suffix),
            record.model_dump_json(),
            feature=self.feature,
        )


def _restart_reason(packet: EvidencePacket, active_invocation: bool) -> str:
    if packet.classification != FailureClass.SAFE_RESTART_CANDIDATE:
        return f"Restart is only allowed for {FailureClass.SAFE_RESTART_CANDIDATE.value} packets."
    if packet.recommended_action not in {ActionLevel.ACT_GUARDED, ActionLevel.RECOMMEND}:
        return "Packet does not recommend restart action."
    bridge_state = str(packet.facts.get("bridge_state") or "")
    bridge_dead = bridge_state in {"dead", "stopped", "crashed", "unreachable"}
    if active_invocation and not bridge_dead:
        return "Active invocation is present and bridge is not dead."
    return ""
