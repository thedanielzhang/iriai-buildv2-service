from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class AgentConcurrencyLimiter:
    """Process-local queue that caps active agent runtime invocations."""

    def __init__(self, max_active: int | None) -> None:
        if max_active is not None and max_active < 1:
            raise ValueError("agent concurrency max must be >= 1")
        self.max_active = max_active
        self._semaphore = asyncio.Semaphore(max_active) if max_active else None
        self._lock = asyncio.Lock()
        self._active = 0
        self._queued = 0

    @property
    def active_count(self) -> int:
        return self._active

    @property
    def queued_count(self) -> int:
        return self._queued

    @asynccontextmanager
    async def acquire(
        self,
        *,
        actor_name: str,
        feature_id: str,
        phase_name: str,
    ) -> AsyncIterator[None]:
        if self._semaphore is None:
            yield
            return

        started = time.monotonic()
        async with self._lock:
            self._queued += 1
            queued = self._queued
            active = self._active
        logger.info(
            "Agent concurrency queued actor=%s feature=%s phase=%s active=%d "
            "queued=%d max=%d",
            actor_name,
            feature_id,
            phase_name,
            active,
            queued,
            self.max_active,
        )

        acquired = False
        active_incremented = False
        try:
            await self._semaphore.acquire()
            acquired = True
            wait_ms = int((time.monotonic() - started) * 1000)
            async with self._lock:
                self._queued -= 1
                self._active += 1
                active_incremented = True
                queued = self._queued
                active = self._active
            logger.info(
                "Agent concurrency acquired actor=%s feature=%s phase=%s "
                "active=%d queued=%d max=%d wait_ms=%d",
                actor_name,
                feature_id,
                phase_name,
                active,
                queued,
                self.max_active,
                wait_ms,
            )
            yield
        except BaseException:
            if not acquired:
                async with self._lock:
                    self._queued -= 1
            raise
        finally:
            if acquired:
                if active_incremented:
                    async with self._lock:
                        self._active -= 1
                        active = self._active
                        queued = self._queued
                else:
                    active = self._active
                    queued = self._queued
                self._semaphore.release()
                logger.info(
                    "Agent concurrency released actor=%s feature=%s phase=%s "
                    "active=%d queued=%d max=%d",
                    actor_name,
                    feature_id,
                    phase_name,
                    active,
                    queued,
                    self.max_active,
                )
