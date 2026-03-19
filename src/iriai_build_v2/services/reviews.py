"""Python wrapper for iriai-feedback review sessions.

Manages ephemeral review sessions (doc review, QA sessions, mockup review)
by spawning the ``iriai-feedback`` CLI as subprocesses.  Port allocation
follows the same 9001-9020 range as v3's review-sessions.js.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import signal
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import shutil

logger = logging.getLogger(__name__)

from ..config import IRIAI_ROOT

_HARDCODED_CLI = IRIAI_ROOT / "iriai-feedback" / "bin" / "iriai-feedback"
_RESOLVED_CLI = shutil.which("iriai-feedback")
FEEDBACK_CLI = Path(_RESOLVED_CLI) if _RESOLVED_CLI else _HARDCODED_CLI
PORT_RANGE = range(9001, 9021)
_SESSIONS_DIR = Path.home() / ".qa-feedback" / "sessions"

_FEEDBACK_SESSION_RE = re.compile(r"session (qs_[a-f0-9]{12})")


@dataclass
class ReviewSession:
    session_id: str
    url: str
    port: int
    kind: str  # "doc_review" | "qa_session" | "mockup_review"
    feedback_session_id: str | None = None
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

    @staticmethod
    async def _read_feedback_session_id(
        process: asyncio.subprocess.Process,
    ) -> str | None:
        """Read up to 2 lines from subprocess stdout to extract the qs_ ID."""
        if not process.stdout:
            return None
        try:
            for _ in range(2):
                line_bytes = await asyncio.wait_for(
                    process.stdout.readline(), timeout=5.0
                )
                if not line_bytes:
                    break
                match = _FEEDBACK_SESSION_RE.search(line_bytes.decode())
                if match:
                    return match.group(1)
        except (asyncio.TimeoutError, UnicodeDecodeError):
            pass
        return None

    @staticmethod
    def _list_session_ids() -> set[str]:
        """Snapshot existing qs_* session directory names from disk."""
        if not _SESSIONS_DIR.is_dir():
            return set()
        return {
            e.name for e in _SESSIONS_DIR.iterdir()
            if e.is_dir() and e.name.startswith("qs_")
        }

    @staticmethod
    def _find_new_session_on_disk(port: int, known: set[str]) -> str | None:
        """Find a new qs_* session on disk (not in *known*) matching *port*."""
        if not _SESSIONS_DIR.is_dir():
            return None
        for entry in _SESSIONS_DIR.iterdir():
            if not entry.is_dir() or entry.name in known or not entry.name.startswith("qs_"):
                continue
            session_file = entry / "session.json"
            try:
                data = json.loads(session_file.read_text())
                if data.get("qa_port") == port:
                    return entry.name
            except (json.JSONDecodeError, OSError):
                continue
        return None

    async def start_doc_review(
        self,
        doc_path: str | Path,
        *,
        title: str | None = None,
        base_path: str = "/doc-review",
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
            "--base-path",
            base_path,
        ]
        if title:
            args.extend(["--title", title])

        known_sessions = self._list_session_ids()

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        feedback_id = await self._read_feedback_session_id(process)
        if not feedback_id:
            # Stdout timed out — session file should be on disk by now
            feedback_id = self._find_new_session_on_disk(port, known_sessions)

        if feedback_id:
            logger.warning("[diag] session %s: feedback_session_id=%s", sid, feedback_id)
        else:
            logger.warning("[diag] session %s: FAILED to capture feedback_session_id", sid)

        session = ReviewSession(
            session_id=sid,
            url=f"http://localhost:{port}{base_path}",
            port=port,
            kind="doc_review",
            feedback_session_id=feedback_id,
            process=process,
        )
        self._sessions[sid] = session
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

        known_sessions = self._list_session_ids()

        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        feedback_id = await self._read_feedback_session_id(process)
        if not feedback_id:
            feedback_id = self._find_new_session_on_disk(port, known_sessions)

        if feedback_id:
            logger.warning("[diag] session %s: feedback_session_id=%s", sid, feedback_id)
        else:
            logger.warning("[diag] session %s: FAILED to capture feedback_session_id", sid)

        session = ReviewSession(
            session_id=sid,
            url=f"http://localhost:{port}",
            port=port,
            kind="qa_session",
            feedback_session_id=feedback_id,
            process=process,
        )
        self._sessions[sid] = session
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
        """Collect annotations from an active session via the CLI."""
        session = self._sessions.get(session_id)
        if not session:
            logger.warning("[diag] collect_feedback: no session for %r (known: %s)", session_id, list(self._sessions.keys()))
            raise KeyError(f"No session with id {session_id}")

        feedback_id = session.feedback_session_id
        if not feedback_id:
            logger.warning("[diag] collect_feedback: feedback_session_id is None for %s", session_id)
            return []

        logger.warning("[diag] collect_feedback: calling CLI with %s", feedback_id)
        proc = await asyncio.create_subprocess_exec(
            str(FEEDBACK_CLI),
            "feedback",
            feedback_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            logger.warning("[diag] collect_feedback: CLI exit %d, stderr=%s", proc.returncode, stderr.decode()[:300])
            return []

        try:
            result = json.loads(stdout.decode())
            logger.warning("[diag] collect_feedback: got %d annotations", len(result))
            return result
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("[diag] collect_feedback: JSON parse failed, stdout=%r", stdout[:200])
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
