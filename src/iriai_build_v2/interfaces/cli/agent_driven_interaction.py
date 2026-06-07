from __future__ import annotations

"""Agent-driven CLI interaction runtime.

Routes the workflow's human/InteractionActor turns to an EXTERNAL driving
agent via a file-based query channel under
``<workspace_root>/.iriai/operator-queries/``, instead of prompting a
terminal or auto-approving.
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from iriai_compose.runner import InteractionRuntime
from iriai_compose.tasks import Ask

from ...planning_signals import GateRejection

try:
    from iriai_compose.prompts import Confirm
except Exception:  # pragma: no cover - older iriai_compose versions
    class Confirm:  # type: ignore[no-redef]
        pass

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S = 1800.0
_POLL_INTERVAL_S = 3.0


def _answer_schema(kind: str, options: list[str]) -> dict[str, Any]:
    if kind == "choose":
        return {"choice": "<one of options>"}
    if kind == "approve":
        return {"decision": "approve|reject", "feedback": "<optional>"}
    return {"text": "str"}


class AgentDrivenInteractionRuntime(InteractionRuntime):
    name = "terminal"

    def __init__(
        self,
        *,
        workspace_root: Path,
        thread_label: str = "",
        poll_interval_s: float = _POLL_INTERVAL_S,
    ) -> None:
        self._workspace_root = Path(workspace_root)
        self._thread_label = thread_label
        self._poll_interval_s = poll_interval_s
        self._dir = self._workspace_root / ".iriai" / "operator-queries"
        self._dir.mkdir(parents=True, exist_ok=True)

    def make_thread_runtime(
        self,
        *,
        feature_id: str = "",
        channel: str = "",
        thread_ts: str = "",
        persist_turns: bool = False,
        agent_runtime: Any = None,
        label: str | None = None,
        thread_id: str | None = None,
    ) -> AgentDrivenInteractionRuntime:
        del feature_id, channel, thread_ts, persist_turns, agent_runtime
        thread_label = label or thread_id or self._thread_label
        return AgentDrivenInteractionRuntime(
            workspace_root=self._workspace_root,
            thread_label=thread_label,
            poll_interval_s=self._poll_interval_s,
        )

    def _normalize_kind(self, task: Ask, kind: str | None) -> str:
        if kind in ("respond", "choose", "approve"):
            return kind
        if isinstance(getattr(task, "input", None), Confirm):
            return "approve"
        return "respond"

    async def ask(self, task: Ask, **kwargs: Any) -> str | bool | GateRejection:
        kind = self._normalize_kind(task, kwargs.get("kind"))
        options = list(getattr(getattr(task, "input", None), "options", []) or [])
        if not options:
            options = list(kwargs.get("options", []) or [])
        query_id = uuid.uuid4().hex
        question = getattr(task, "prompt", str(task))
        query = {
            "id": query_id,
            "kind": kind,
            "question": question,
            "options": options,
            "phase_name": kwargs.get("phase_name"),
            "feature_id": kwargs.get("feature_id"),
            "thread_label": self._thread_label,
            "answer_schema": _answer_schema(kind, options),
        }
        self._write_query(query_id, query)
        logger.info(
            "operator-query %s (%s) pending: %s", query_id, kind, str(question)[:200]
        )
        return await self._poll(query_id, kind, options)

    def _write_query(self, query_id: str, query: dict[str, Any]) -> None:
        path = self._dir / f"{query_id}.query.json"
        tmp = self._dir / f"{query_id}.query.json.tmp"
        tmp.write_text(json.dumps(query, indent=2))
        os.replace(tmp, path)

    async def _poll(
        self, query_id: str, kind: str, options: list[str]
    ) -> str | bool | GateRejection:
        timeout_s = float(
            os.environ.get("IRIAI_OPERATOR_QUERY_TIMEOUT_S", _DEFAULT_TIMEOUT_S)
        )
        answer_path = self._dir / f"{query_id}.answer.json"
        query_path = self._dir / f"{query_id}.query.json"
        elapsed = 0.0
        while True:
            if answer_path.exists():
                answer = json.loads(answer_path.read_text())
                if kind == "choose":
                    choice = answer.get("choice")
                    if options and choice not in options:
                        logger.info(
                            "operator-query %s choice %r not in options %s; waiting",
                            query_id,
                            choice,
                            options,
                        )
                        answer_path.unlink(missing_ok=True)
                        await asyncio.sleep(self._poll_interval_s)
                        elapsed += self._poll_interval_s
                        continue
                self._archive(query_path, answer_path, query_id)
                if kind == "approve":
                    if answer.get("decision") == "approve":
                        return True
                    return GateRejection(
                        feedback=answer.get("feedback") or "rejected by operator"
                    )
                if kind == "choose":
                    return answer.get("choice")
                return answer.get("text")
            if elapsed >= timeout_s:
                raise RuntimeError(
                    f"operator query {query_id} ({kind}) timed out after {timeout_s}s "
                    "with no answer from the driving agent — re-run with the driver "
                    "loop active"
                )
            await asyncio.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s

    def _archive(self, query_path: Path, answer_path: Path, query_id: str) -> None:
        processed = self._dir / "processed"
        processed.mkdir(parents=True, exist_ok=True)
        for src in (query_path, answer_path):
            if src.exists():
                os.replace(src, processed / src.name)

    async def notify(
        self,
        *,
        feature_id: str,
        phase_name: str,
        message: str,
        delivery_id: str | None = None,
    ) -> None:
        del feature_id, delivery_id
        logger.info("operator-notify [%s] %s", phase_name or "notification", message)
