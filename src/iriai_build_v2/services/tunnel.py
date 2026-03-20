"""Cloudflare Tunnel + iriai-feedback serve for artifact hosting.

Manages two subprocesses:
1. ``iriai-feedback serve <dir> --port 9000`` — multi-doc artifact server
2. ``cloudflared tunnel --url http://localhost:9000`` — public tunnel

Started once at bridge boot, shared across all workflows.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")
_CLOUDFLARED = shutil.which("cloudflared")
_FEEDBACK_CLI = shutil.which("iriai-feedback")
_SERVE_PORT = 9000


class CloudflareTunnel:
    """Manages a single iriai-feedback serve process + cloudflared tunnel."""

    def __init__(self) -> None:
        self._serve_process: asyncio.subprocess.Process | None = None
        self._cf_process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None

    async def start(self, artifact_dir: Path) -> str:
        """Start iriai-feedback serve + cloudflared. Returns public base URL.

        Raises RuntimeError if either process fails to start.
        """
        if not _FEEDBACK_CLI:
            raise RuntimeError(
                "iriai-feedback is not installed. "
                "Install it with: npm install -g iriai-feedback"
            )
        if not _CLOUDFLARED:
            raise RuntimeError(
                "cloudflared is not installed. "
                "Install it with: brew install cloudflared"
            )

        # 1. Start iriai-feedback serve
        self._serve_process = await asyncio.create_subprocess_exec(
            _FEEDBACK_CLI,
            "serve",
            str(artifact_dir),
            "--port",
            str(_SERVE_PORT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Give the HTTP server a moment to bind
        await asyncio.sleep(1.0)
        if self._serve_process.returncode is not None:
            stderr = ""
            if self._serve_process.stderr:
                try:
                    raw = await asyncio.wait_for(self._serve_process.stderr.read(), timeout=2.0)
                    stderr = raw.decode(errors="replace")[:500]
                except (asyncio.TimeoutError, Exception):
                    pass
            raise RuntimeError(
                f"iriai-feedback serve exited immediately (code {self._serve_process.returncode}). "
                f"stderr: {stderr}"
            )
        logger.info("iriai-feedback serve started on port %d", _SERVE_PORT)

        # 2. Start cloudflared tunnel
        self._cf_process = await asyncio.create_subprocess_exec(
            _CLOUDFLARED,
            "tunnel",
            "--url",
            f"http://localhost:{_SERVE_PORT}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        url = await self._read_tunnel_url(self._cf_process)
        if not url:
            if self._cf_process.stderr:
                try:
                    remaining = await asyncio.wait_for(self._cf_process.stderr.read(), timeout=2.0)
                    if remaining:
                        logger.warning("cloudflared stderr: %s", remaining.decode(errors="replace")[:500])
                except (asyncio.TimeoutError, Exception):
                    pass
            # Clean up serve process too
            await self._kill(self._cf_process)
            await self._kill(self._serve_process)
            raise RuntimeError(
                "Failed to start cloudflared tunnel. "
                "Check that cloudflared is working correctly."
            )

        self._public_url = url
        logger.info("Tunnel started: localhost:%d → %s", _SERVE_PORT, url)
        return url

    @property
    def public_url(self) -> str | None:
        """The base tunnel URL, or None if not started."""
        return self._public_url

    def local_url(self, feature_id: str, key: str) -> str:
        """Deterministic local URL for an artifact."""
        return f"http://localhost:{_SERVE_PORT}/features/{feature_id}/{key}"

    def artifact_url(self, feature_id: str, key: str) -> str:
        """Public URL if tunnel is active, else local URL."""
        base = self._public_url or f"http://localhost:{_SERVE_PORT}"
        return f"{base}/features/{feature_id}/{key}"

    async def stop_all(self) -> None:
        """Terminate both subprocesses."""
        for label, proc in [("cloudflared", self._cf_process), ("iriai-feedback", self._serve_process)]:
            if proc:
                await self._kill(proc)
                logger.info("%s stopped", label)
        self._cf_process = None
        self._serve_process = None
        self._public_url = None

    @staticmethod
    async def _kill(proc: asyncio.subprocess.Process) -> None:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            try:
                proc.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    async def _read_tunnel_url(
        process: asyncio.subprocess.Process,
    ) -> str | None:
        """Read cloudflared output to extract the public tunnel URL."""

        async def _scan_stream(
            stream: asyncio.StreamReader | None,
        ) -> str | None:
            if not stream:
                return None
            try:
                deadline = asyncio.get_event_loop().time() + 20.0
                while asyncio.get_event_loop().time() < deadline:
                    line_bytes = await asyncio.wait_for(
                        stream.readline(), timeout=20.0
                    )
                    if not line_bytes:
                        break
                    line = line_bytes.decode(errors="replace")
                    logger.debug("cloudflared: %s", line.rstrip())
                    match = _URL_RE.search(line)
                    if match:
                        return match.group(1)
            except asyncio.TimeoutError:
                pass
            return None

        url = await _scan_stream(process.stderr)
        if not url:
            url = await _scan_stream(process.stdout)
        return url
