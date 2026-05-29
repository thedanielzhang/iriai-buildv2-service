from __future__ import annotations

import asyncio
import sys

import pytest

from iriai_build_v2.tasks.playwright import PlaywrightService


@pytest.mark.asyncio
async def test_ensure_browsers_uses_active_python_interpreter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class _Proc:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def _fake_create_subprocess_exec(*args, **kwargs):
        del kwargs
        calls.append(args)
        return _Proc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    await PlaywrightService(browser="chromium").ensure_browsers()

    assert calls
    assert calls[0][:3] == (sys.executable, "-m", "playwright")
