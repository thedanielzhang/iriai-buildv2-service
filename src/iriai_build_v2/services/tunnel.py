"""Artifact hosting server + optional Cloudflare tunnel.

Manages two subprocesses:
1. ``iriai-feedback serve <dir> --port 9000`` — always started
2. ``cloudflared tunnel --url http://localhost:9000`` — best-effort

The serve process is essential (artifacts won't render without it).
The tunnel is optional — if cloudflared fails, artifacts are served
via local URLs only.
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


async def _kill_process(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except (asyncio.TimeoutError, ProcessLookupError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


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


class CloudflaredUrlTunnel:
    """Manages a best-effort cloudflared tunnel for an arbitrary local URL."""

    def __init__(self) -> None:
        self._cf_process: asyncio.subprocess.Process | None = None
        self._public_url: str | None = None
        self._target_url: str | None = None

    async def start(self, target_url: str) -> str | None:
        if self._cf_process and self._cf_process.returncode is None:
            return self._public_url
        if not _CLOUDFLARED:
            logger.warning("cloudflared not installed — %s remains local only", target_url)
            return None

        self._target_url = target_url
        try:
            self._cf_process = await asyncio.create_subprocess_exec(
                _CLOUDFLARED,
                "tunnel",
                "--url",
                target_url,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            url = await _read_tunnel_url(self._cf_process)
            if url:
                self._public_url = url
                logger.info("Tunnel started: %s → %s", target_url, url)
                return url

            if self._cf_process.stderr:
                try:
                    remaining = await asyncio.wait_for(self._cf_process.stderr.read(), timeout=2.0)
                    if remaining:
                        logger.warning(
                            "cloudflared stderr for %s: %s",
                            target_url,
                            remaining.decode(errors="replace")[:500],
                        )
                except (asyncio.TimeoutError, Exception):
                    pass
            await _kill_process(self._cf_process)
            self._cf_process = None
            logger.warning("cloudflared tunnel failed for %s", target_url)
        except Exception:
            logger.warning("cloudflared tunnel failed for %s", target_url, exc_info=True)
            self._cf_process = None
        return None

    @property
    def public_url(self) -> str | None:
        return self._public_url

    @property
    def active(self) -> bool:
        return self._cf_process is not None and self._cf_process.returncode is None

    async def stop(self) -> None:
        if self._cf_process:
            await _kill_process(self._cf_process)
        self._cf_process = None
        self._public_url = None
        self._target_url = None


class CloudflareTunnel:
    """Manages iriai-feedback serve (always) + cloudflared tunnel (best-effort)."""

    def __init__(self) -> None:
        self._serve_process: asyncio.subprocess.Process | None = None
        self._url_tunnel = CloudflaredUrlTunnel()

    async def start(self, artifact_dir: Path) -> str | None:
        """Start iriai-feedback serve + attempt cloudflared tunnel.

        Always starts the serve process. Tunnel is best-effort — if
        cloudflared fails, artifacts are served locally on port 9000.

        Returns public tunnel URL if available, else None.
        Raises RuntimeError only if the serve process fails to start.
        """
        if not _FEEDBACK_CLI:
            raise RuntimeError(
                "iriai-feedback is not installed. "
                "Install it with: npm install -g iriai-feedback"
            )

        # 1. Start iriai-feedback serve (essential)
        self._serve_process = await asyncio.create_subprocess_exec(
            _FEEDBACK_CLI,
            "serve",
            str(artifact_dir),
            "--port",
            str(_SERVE_PORT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
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

        # 2. Attempt cloudflared tunnel (best-effort)
        return await self._url_tunnel.start(f"http://localhost:{_SERVE_PORT}")

    @property
    def public_url(self) -> str | None:
        """The base tunnel URL, or None if tunnel isn't active."""
        return self._url_tunnel.public_url

    def local_url(self, feature_id: str, key: str) -> str:
        """Deterministic local URL for an artifact."""
        return f"http://localhost:{_SERVE_PORT}/features/{feature_id}/{key}"

    def artifact_url(self, feature_id: str, key: str) -> str:
        """Public URL if tunnel is active, else local URL."""
        base = self.public_url or f"http://localhost:{_SERVE_PORT}"
        return f"{base}/features/{feature_id}/{key}"

    async def stop_all(self) -> None:
        """Terminate both subprocesses."""
        if self._url_tunnel.active or self._url_tunnel.public_url:
            await self._url_tunnel.stop()
            logger.info("cloudflared stopped")
        if self._serve_process:
            await _kill_process(self._serve_process)
            logger.info("iriai-feedback stopped")
        self._serve_process = None
