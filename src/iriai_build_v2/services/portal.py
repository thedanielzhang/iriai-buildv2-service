"""Python wrapper for the Node.js artifact portal.

Starts the artifact-portal server as a subprocess, configured to read from
v2's filesystem artifact mirror instead of v3's SQLite database.
"""

from __future__ import annotations

import asyncio
import os
import signal
from pathlib import Path
from typing import Any

from ..config import IRIAI_ROOT

PORTAL_SCRIPT = IRIAI_ROOT / "iriai-build" / "v3" / "artifact-portal.js"
DEFAULT_PORT = 8900


class ArtifactPortal:
    """Manages the artifact portal Node.js subprocess."""

    def __init__(
        self,
        artifacts_dir: str | Path,
        *,
        port: int = DEFAULT_PORT,
    ) -> None:
        self._artifacts_dir = Path(artifacts_dir)
        self._port = port
        self._process: asyncio.subprocess.Process | None = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}"

    def get_feature_url(self, feature_id: str) -> str:
        return f"{self.url}/feature/{feature_id}"

    async def start(self) -> None:
        """Start the artifact portal subprocess."""
        if self._process is not None:
            return

        if not PORTAL_SCRIPT.exists():
            raise FileNotFoundError(
                f"Artifact portal not found at {PORTAL_SCRIPT}. "
                f"Ensure iriai-build v3 is available at {IRIAI_ROOT}/iriai-build/v3/"
            )

        env = {
            **os.environ,
            "PORTAL_PORT": str(self._port),
            "ARTIFACTS_DIR": str(self._artifacts_dir),
            # Tell portal to use filesystem mode (manifest.json) instead of SQLite
            "PORTAL_DATA_SOURCE": "filesystem",
        }

        self._process = await asyncio.create_subprocess_exec(
            "node",
            str(PORTAL_SCRIPT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait briefly for the server to bind
        await asyncio.sleep(1)

        if self._process.returncode is not None:
            stderr = b""
            if self._process.stderr:
                stderr = await self._process.stderr.read()
            raise RuntimeError(
                f"Artifact portal exited immediately: {stderr.decode()}"
            )

    async def stop(self) -> None:
        """Stop the artifact portal subprocess."""
        if self._process is None:
            return

        try:
            self._process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(self._process.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            self._process.kill()
        finally:
            self._process = None

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def __aenter__(self) -> ArtifactPortal:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.stop()
