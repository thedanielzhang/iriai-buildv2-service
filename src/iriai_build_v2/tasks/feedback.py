"""Feedback collection task — launches a feedback server and waits for human submission."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from iriai_compose import Task

if TYPE_CHECKING:
    from iriai_compose import Feature, WorkflowRunner

    from ..services.reviews import ReviewSessionManager


@dataclass
class SessionInfo:
    """Returned by FeedbackService.start_* methods."""

    session_id: str
    url: str
    port: int


class FeedbackService:
    """Manages feedback collection sessions via the qa-feedback MCP server."""

    def __init__(self, review_manager: ReviewSessionManager) -> None:
        self._manager = review_manager

    async def start_qa(
        self,
        target_url: str,
        *,
        port: int = 9000,
        context: dict[str, Any] | None = None,
    ) -> SessionInfo:
        """Start a QA feedback session. Returns session_id and qa_url."""
        import json as _json

        ctx_str = _json.dumps(context) if context else None
        session = await self._manager.start_qa_session(
            target_url, context=ctx_str,
        )
        return SessionInfo(
            session_id=session.session_id,
            url=session.url,
            port=session.port,
        )

    async def start_doc_review(
        self,
        doc_path: str,
        *,
        title: str | None = None,
        port: int = 9000,
        base_path: str = "/doc-review",
    ) -> SessionInfo:
        """Start a doc review session. Returns session_id and review_url."""
        session = await self._manager.start_doc_review(
            doc_path, title=title, base_path=base_path,
        )
        return SessionInfo(
            session_id=session.session_id,
            url=session.url,
            port=session.port,
        )

    async def wait_for_submission(
        self, session_id: str, *, timeout_ms: int = 1_800_000
    ) -> None:
        """Block until the session is submitted (polls via ReviewSessionManager)."""
        import asyncio

        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000.0
        poll_interval = 2.0  # seconds

        while True:
            feedback = await self._manager.collect_feedback(session_id)
            # collect_feedback returns annotations; check session status via
            # the underlying process — if the process has exited, the session
            # was submitted (or stopped).
            session = self._manager._sessions.get(session_id)
            if session and session.process and session.process.returncode is not None:
                # Process exited — session was submitted or stopped
                return

            now = asyncio.get_event_loop().time()
            if now >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for session {session_id} after {timeout_ms}ms"
                )
            await asyncio.sleep(min(poll_interval, deadline - now))

    async def get_annotations(self, session_id: str) -> list[dict[str, Any]]:
        """Retrieve all annotations for a session."""
        return await self._manager.collect_feedback(session_id)

    async def stop(self, session_id: str) -> None:
        """Stop the feedback server for a session."""
        await self._manager.stop_session(session_id)

    async def collect(
        self, session_id: str, *, timeout_ms: int = 1_800_000
    ) -> list[dict[str, Any]]:
        """Wait for submission, get annotations, stop server. Convenience method."""
        await self.wait_for_submission(session_id, timeout_ms=timeout_ms)
        annotations = await self.get_annotations(session_id)
        await self.stop(session_id)
        return annotations


class CollectFeedbackTask(Task):
    """Launch feedback tool, wait for human submission, return annotations."""

    target_url: str | None = None
    doc_path: str | None = None
    title: str | None = None
    port: int = 9000
    context: dict[str, Any] | None = None
    timeout_ms: int = 1_800_000

    async def execute(self, runner: WorkflowRunner, feature: Feature) -> list[dict[str, Any]]:
        service: FeedbackService = runner.services["feedback"]

        if self.target_url:
            info = await service.start_qa(
                self.target_url, port=self.port, context=self.context,
            )
        elif self.doc_path:
            info = await service.start_doc_review(
                self.doc_path, title=self.title, port=self.port,
            )
        else:
            raise ValueError("Either target_url or doc_path is required")

        try:
            return await service.collect(info.session_id, timeout_ms=self.timeout_ms)
        except Exception:
            await service.stop(info.session_id)
            raise
