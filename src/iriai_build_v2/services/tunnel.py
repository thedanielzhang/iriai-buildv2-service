"""Cloudflare Tunnel service for exposing local ports to the internet.

Uses ``cloudflared tunnel --url`` (quick tunnels, no account needed) to create
ephemeral public URLs for local review sessions.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")
_CLOUDFLARED = shutil.which("cloudflared")


class CloudflareTunnel:
    """Manages cloudflared quick tunnels for localhost ports."""

    def __init__(self) -> None:
        self._tunnels: dict[int, tuple[str, asyncio.subprocess.Process]] = {}

    async def tunnel(self, port: int) -> str:
        """Start a cloudflared tunnel for the given port. Returns public URL.

        Reuses existing tunnel if one is already running for this port.
        Raises RuntimeError if cloudflared is not installed or tunnel fails.
        """
        if port in self._tunnels:
            return self._tunnels[port][0]

        if not _CLOUDFLARED:
            raise RuntimeError(
                "cloudflared is not installed. "
                "Install it with: brew install cloudflared"
            )

        process = await asyncio.create_subprocess_exec(
            _CLOUDFLARED,
            "tunnel",
            "--url",
            f"http://localhost:{port}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        url = await self._read_tunnel_url(process)
        if not url:
            # Log stderr for debugging
            if process.stderr:
                try:
                    remaining = await asyncio.wait_for(process.stderr.read(), timeout=2.0)
                    if remaining:
                        logger.warning("cloudflared stderr: %s", remaining.decode(errors="replace")[:500])
                except (asyncio.TimeoutError, Exception):
                    pass
            try:
                process.terminate()
            except ProcessLookupError:
                pass  # Already exited
            raise RuntimeError(
                f"Failed to start cloudflared tunnel for port {port}. "
                "Check that cloudflared is working correctly."
            )

        self._tunnels[port] = (url, process)
        logger.info("Tunnel started: localhost:%d → %s", port, url)
        return url

    async def stop_all(self) -> None:
        """Terminate all cloudflared processes."""
        for port, (url, proc) in self._tunnels.items():
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                proc.kill()
            logger.info("Tunnel stopped: port %d (%s)", port, url)
        self._tunnels.clear()

    @staticmethod
    async def _read_tunnel_url(
        process: asyncio.subprocess.Process,
    ) -> str | None:
        """Read cloudflared output to extract the public tunnel URL.

        cloudflared outputs the URL to stderr (or sometimes stdout) in a line
        like: ``... | https://abc123.trycloudflare.com``
        """

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

        # Try stderr first (most common), fall back to stdout
        url = await _scan_stream(process.stderr)
        if not url:
            url = await _scan_stream(process.stdout)
        return url
