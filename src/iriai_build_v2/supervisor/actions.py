from __future__ import annotations

import inspect
import os
import signal
import subprocess
import time
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
from .read_only import assert_no_control_plane_writer
from .stale_codex import process_is_feature_scoped

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
        execution_authority: Any | None = None,
    ) -> None:
        # ── Mechanical read-only enforcement (Slice 10c-1, doc 10 § "Read-Only
        #    And Audit Exception Policy") ──────────────────────────────────────
        #
        # In default v1 read-only mode the action policy MUST NOT hold an
        # execution-authority writer. ``execution_authority`` is the opt-in slot
        # for a writer handle a GUARDED-mode deployment might supply — but doc
        # 10 says even guarded bridge actions "cannot mutate typed execution
        # state or product files". So a control-plane writer is NEVER valid on
        # an ActionPolicy: ``assert_no_control_plane_writer`` fails closed at
        # construction if one is passed. The writer path is thus STRUCTURALLY
        # ABSENT, not gated by a runtime ``self.mode`` check.
        assert_no_control_plane_writer(
            execution_authority, role="ActionPolicy.execution_authority"
        )
        self.mode = mode
        self.restart = restart
        self.artifact_sink = artifact_sink
        self.feature = feature
        # Held only so the absence is explicit + auditable; it is always None
        # for a correctly-wired supervisor (the assert above guarantees a
        # non-None value is never an execution-authority writer).
        self.execution_authority = execution_authority

    async def guard_execution_write(
        self,
        packet: EvidencePacket,
        *,
        action: str,
        reason: str,
    ) -> SupervisorActionRecord:
        """Deny an execution-authority / product write and audit the block.

        doc 10 § "Read-Only And Audit Exception Policy": "Denied writes fail
        closed and produce a blocked action audit row rather than a best-effort
        mutation." Any supervisor code path that finds itself about to mutate
        execution/control-plane/product authority calls this instead of
        performing the write. It NEVER mutates — it records a
        :class:`SupervisorActionStatus.BLOCKED` audit row and returns it.

        This is the fail-closed handler for a caught
        :class:`~iriai_build_v2.supervisor.read_only.BlockedExecutionWrite`,
        and the deterministic deny path the doc-10 test
        "Read-only action policy blocks execution/control-plane mutation and
        writes a blocked audit record" exercises.
        """

        record = SupervisorActionRecord(
            feature_id=packet.feature_id,
            cursor=int(
                packet.facts.get("next_cursor")
                or packet.facts.get("cursor")
                or 0
            ),
            action=action,
            mode=self.mode,
            status=SupervisorActionStatus.BLOCKED,
            reason=(
                "Read-only supervisor denied an execution-authority write "
                f"(fail-closed, no mutation performed): {reason}"
            ),
            packet=packet,
        )
        await self._write(record, "blocked")
        return record

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

    async def maybe_kill_stale_codex(
        self,
        packet: EvidencePacket,
        *,
        evidence_token: str,
    ) -> SupervisorActionRecord:
        cursor = int(packet.facts.get("next_cursor") or packet.facts.get("cursor") or 0)
        stale = packet.facts.get("stale_codex_invocation") if packet.facts else None
        if packet.classification != FailureClass.STALE_CODEX_INVOCATION or not isinstance(stale, dict):
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=cursor,
                action="kill_stale_codex",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason="Kill is only allowed for stale_codex_invocation packets.",
                packet=packet,
            )
            await self._write(record, "blocked")
            return record
        if str(stale.get("evidence_token") or "") != evidence_token:
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=cursor,
                action="kill_stale_codex",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason="Evidence token does not match the selected stale Codex card.",
                packet=packet,
                before={"requested_token": evidence_token, "stale": stale},
            )
            await self._write(record, "blocked")
            return record
        if self.mode != SupervisorMode.GUARDED:
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=cursor,
                action="kill_stale_codex",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason="Read-only mode records stale Codex reset recommendations but does not kill processes.",
                packet=packet,
                before={"stale": stale},
            )
            await self._write(record, "blocked")
            return record

        validation = _validate_stale_codex_target(packet.feature_id, stale)
        if validation.get("error"):
            record = SupervisorActionRecord(
                feature_id=packet.feature_id,
                cursor=cursor,
                action="kill_stale_codex",
                mode=self.mode,
                status=SupervisorActionStatus.BLOCKED,
                reason=str(validation["error"]),
                packet=packet,
                before=validation,
            )
            await self._write(record, "blocked")
            return record

        planned = SupervisorActionRecord(
            feature_id=packet.feature_id,
            cursor=cursor,
            action="kill_stale_codex",
            mode=self.mode,
            status=SupervisorActionStatus.PLANNED,
            reason="Guarded stale Codex reset approved by exact process-tree policy.",
            packet=packet,
            before=validation,
        )
        await self._write(planned, "planned")
        try:
            after = _kill_process_tree([int(pid) for pid in validation["kill_pids"]])
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
                "after": after,
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
    if packet.recommended_action != ActionLevel.ACT_GUARDED:
        return "Packet is recommend-only; guarded restart requires an act_guarded packet."
    bridge_state = str(packet.facts.get("bridge_state") or "")
    bridge_dead = bridge_state in {"dead", "stopped", "crashed", "unreachable"}
    if active_invocation and not bridge_dead:
        return "Active invocation is present and bridge is not dead."
    return ""


def _validate_stale_codex_target(feature_id: str, stale: dict[str, Any]) -> dict[str, Any]:
    try:
        pid = int(stale.get("pid"))
    except (TypeError, ValueError):
        return {"error": "Stale Codex evidence did not include a valid parent pid.", "stale": stale}
    expected_children = [int(child) for child in stale.get("child_pids") or []]
    expected_command = str(stale.get("command") or "")
    probed = _probe_pid(pid)
    if probed is None:
        return {"error": f"PID {pid} is no longer running.", "stale": stale}
    command = str(probed.get("command") or "")
    if "codex exec" not in command:
        return {"error": f"PID {pid} is not a Codex exec process.", "probe": probed, "stale": stale}
    if "dashboard.py" in command or "interfaces.cli.app slack" in command:
        return {"error": "Refusing to kill dashboard or bridge process.", "probe": probed, "stale": stale}
    if not process_is_feature_scoped(
        feature_id=feature_id,
        command=command,
        trace_path="",
    ):
        return {
            "error": "Codex process is not scoped to the exact feature workspace.",
            "probe": probed,
            "stale": stale,
        }
    if (
        expected_command
        and _normalize_process_command(expected_command)
        != _normalize_process_command(command)
    ):
        return {
            "error": "Current process command no longer matches stored stale Codex evidence.",
            "probe": probed,
            "stale": stale,
        }
    expected_descendants = [
        int(descendant)
        for descendant in stale.get("descendant_pids", expected_children) or []
    ]
    expected_child_set = set(expected_children)
    expected_descendant_set = set(expected_descendants)
    child_pids = _child_pids(pid)
    descendant_pids = _descendant_pids(pid)
    current_child_set = set(child_pids)
    current_descendant_set = set(descendant_pids)
    missing_descendants = sorted(expected_descendant_set - current_descendant_set)
    extra_descendants = sorted(current_descendant_set - expected_descendant_set)
    missing_children = sorted(expected_child_set - current_child_set)
    extra_children = sorted(current_child_set - expected_child_set)
    if missing_descendants or extra_descendants or missing_children or extra_children:
        return {
            "error": "Current process tree no longer matches stored stale Codex evidence.",
            "probe": probed,
            "current_child_pids": child_pids,
            "current_descendant_pids": descendant_pids,
            "expected_child_pids": expected_children,
            "expected_descendant_pids": expected_descendants,
            "extra_child_pids": extra_children,
            "extra_descendant_pids": extra_descendants,
            "missing_child_pids": missing_children,
            "missing_descendant_pids": missing_descendants,
            "stale": stale,
        }
    kill_pids = [*reversed(descendant_pids), pid]
    return {
        "probe": probed,
        "current_child_pids": child_pids,
        "current_descendant_pids": descendant_pids,
        "kill_pids": kill_pids,
        "stale": stale,
    }


def _normalize_process_command(command: str) -> str:
    return " ".join(str(command or "").split())


def _probe_pid(pid: int) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "pid=,ppid=,%cpu=,%mem=,command="],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    line = (result.stdout or "").strip()
    if result.returncode != 0 or not line:
        return None
    parts = line.split(None, 4)
    if len(parts) < 5:
        return None
    try:
        return {
            "pid": int(parts[0]),
            "ppid": int(parts[1]),
            "cpu_percent": float(parts[2]),
            "mem_percent": float(parts[3]),
            "command": parts[4],
        }
    except ValueError:
        return None


def _child_pids(pid: int) -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-P", str(pid)],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2,
        )
    except Exception:
        return []
    children: list[int] = []
    for line in (result.stdout or "").splitlines():
        try:
            children.append(int(line.strip()))
        except ValueError:
            continue
    return sorted(children)


def _descendant_pids(pid: int) -> list[int]:
    descendants: list[int] = []
    queue = list(_child_pids(pid))
    seen: set[int] = set()
    while queue:
        child = queue.pop(0)
        if child in seen:
            continue
        seen.add(child)
        descendants.append(child)
        queue.extend(_child_pids(child))
    return descendants


def _kill_process_tree(pids: list[int]) -> dict[str, Any]:
    unique_pids = []
    for pid in pids:
        if pid not in unique_pids:
            unique_pids.append(pid)
    for pid in unique_pids:
        with _suppress_process_lookup():
            os.kill(pid, signal.SIGTERM)
    time.sleep(2)
    still_alive = [pid for pid in unique_pids if _pid_alive(pid)]
    for pid in still_alive:
        with _suppress_process_lookup():
            os.kill(pid, signal.SIGKILL)
    return {
        "terminated_pids": unique_pids,
        "sigkilled_pids": still_alive,
        "alive_after": [pid for pid in unique_pids if _pid_alive(pid)],
    }


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class _suppress_process_lookup:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, _exc, _tb):
        return exc_type is ProcessLookupError
