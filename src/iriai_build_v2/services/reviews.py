"""Python wrapper for iriai-feedback review sessions.

Manages ephemeral review sessions (doc review, QA sessions, mockup review)
by spawning the ``iriai-feedback`` CLI as subprocesses.  Port allocation
follows the same 9001-9020 range as v3's review-sessions.js.
"""

from __future__ import annotations

import asyncio
import json
import signal
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import IRIAI_ROOT

FEEDBACK_CLI = IRIAI_ROOT / "iriai-feedback" / "bin" / "iriai-feedback"
PORT_RANGE = range(9001, 9021)


@dataclass
class ReviewSession:
    session_id: str
    url: str
    port: int
    kind: str  # "doc_review" | "qa_session" | "mockup_review"
    process: asyncio.subprocess.Process | None = field(
        default=None, repr=False
    )


class ReviewSessionManager:
    """Manages ephemeral review sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, ReviewSession] = {}
        self._next_id = 1

    def _allocate_port(self) -> int:
        """Find the first available port in the range."""
        for port in PORT_RANGE:
            if any(s.port == port for s in self._sessions.values()):
                continue
            if not _port_in_use(port):
                return port
        raise RuntimeError(
            f"No available ports in range {PORT_RANGE.start}-{PORT_RANGE.stop - 1}"
        )

    def _make_id(self, prefix: str) -> str:
        sid = f"{prefix}-{self._next_id}"
        self._next_id += 1
        return sid

    async def start_doc_review(
        self,
        doc_path: str | Path,
        *,
        title: str | None = None,
    ) -> ReviewSession:
        """Start a document review session for a markdown/HTML file."""
        port = self._allocate_port()
        sid = self._make_id("doc")

        args = [
            str(FEEDBACK_CLI),
            "review",
            str(doc_path),
            "--port",
            str(port),
        ]
        if title:
            args.extend(["--title", title])

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        session = ReviewSession(
            session_id=sid,
            url=f"http://localhost:{port}",
            port=port,
            kind="doc_review",
            process=process,
        )
        self._sessions[sid] = session

        # Brief wait for server to bind
        await asyncio.sleep(0.5)
        return session

    async def start_qa_session(
        self,
        target_url: str,
        *,
        context: str | None = None,
    ) -> ReviewSession:
        """Start a QA session proxying a target URL."""
        port = self._allocate_port()
        sid = self._make_id("qa")

        args = [
            str(FEEDBACK_CLI),
            "start",
            target_url,
            "--port",
            str(port),
        ]
        if context:
            args.extend(["--context", context])

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        session = ReviewSession(
            session_id=sid,
            url=f"http://localhost:{port}",
            port=port,
            kind="qa_session",
            process=process,
        )
        self._sessions[sid] = session

        await asyncio.sleep(0.5)
        return session

    async def start_mockup_review(
        self,
        html_path: str | Path,
        *,
        title: str | None = None,
    ) -> ReviewSession:
        """Start a mockup review session for an HTML file."""
        # Mockup review uses the doc review command with HTML
        return await self.start_doc_review(html_path, title=title)

    async def collect_feedback(self, session_id: str) -> list[dict[str, Any]]:
        """Collect annotations from an active session via the MCP."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"No session with id {session_id}")

        # Use iriai-feedback's HTTP API to get annotations
        proc = await asyncio.create_subprocess_exec(
            str(FEEDBACK_CLI),
            "feedback",
            session_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode != 0:
            return []

        try:
            return json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []

    async def stop_session(self, session_id: str) -> None:
        """Stop a review session."""
        session = self._sessions.pop(session_id, None)
        if session and session.process:
            try:
                session.process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(session.process.wait(), timeout=5)
            except (asyncio.TimeoutError, ProcessLookupError):
                session.process.kill()

    async def stop_all(self) -> None:
        """Stop all active review sessions."""
        for sid in list(self._sessions):
            await self.stop_session(sid)

    def list_sessions(self) -> list[ReviewSession]:
        return list(self._sessions.values())


def _port_in_use(port: int) -> bool:
    """Check if a TCP port is in use via probe."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.1)
        return s.connect_ex(("127.0.0.1", port)) == 0
