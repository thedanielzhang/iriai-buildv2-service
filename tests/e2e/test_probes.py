"""Unit tests for generic per-surface readiness probes."""

from __future__ import annotations

import http.server
import socket
import threading

import pytest

from iriai_build_v2.workflows.develop.e2e.adapters import (
    Surface,
    probe_exit_zero,
    probe_file_exists,
    probe_http_get,
    probe_surface,
)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.asyncio
async def test_http_get_pass_against_live_server():
    port = _free_port()
    server = http.server.HTTPServer(
        ("127.0.0.1", port), http.server.BaseHTTPRequestHandler
    )

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    server.RequestHandlerClass = H
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        ok, detail = await probe_http_get(
            f"http://127.0.0.1:{port}/", timeout_s=5, interval_s=0.2
        )
        assert ok, detail
        assert "200" in detail
    finally:
        server.shutdown()


@pytest.mark.asyncio
async def test_http_get_fail_on_dead_port():
    port = _free_port()  # nothing listening
    ok, detail = await probe_http_get(
        f"http://127.0.0.1:{port}/", timeout_s=2, interval_s=0.3
    )
    assert not ok
    assert "timeout" in detail


@pytest.mark.asyncio
async def test_exit_zero(tmp_path):
    ok, _ = await probe_exit_zero(["true"])
    assert ok
    bad, _ = await probe_exit_zero(["false"])
    assert not bad


@pytest.mark.asyncio
async def test_file_exists(tmp_path):
    f = tmp_path / "ready.flag"
    ok_missing, _ = await probe_file_exists(str(f), timeout_s=1, interval_s=0.2)
    assert not ok_missing
    f.write_text("x")
    ok, _ = await probe_file_exists(str(f), timeout_s=1, interval_s=0.2)
    assert ok


@pytest.mark.asyncio
async def test_probe_surface_not_applicable_for_library():
    surface = Surface(name="lib", probe_kind="none", probe_target="")
    bs = await probe_surface(surface, timeout_s=1)
    assert bs.status == "not_applicable"
