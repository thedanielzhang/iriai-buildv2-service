"""P4b: compose adapter pure helpers (override gen, surfaces, secret resolution),
the JUnit-XML parser, verdict conversion, and the compose preflight + single-stack
mutex. No real docker."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iriai_build_v2.workflows.develop.e2e import runner_loop as rl
from iriai_build_v2.workflows.develop.e2e.adapters import compose as compose_mod
from iriai_build_v2.workflows.develop.e2e.adapters.compose import (
    ComposeAdapter,
    build_compose_override,
    build_surfaces,
    resolve_secret_source,
    run_to_verdicts,
)
from iriai_build_v2.workflows.develop.e2e.adapters.junit_report import parse_junit_xml


# --- override generation ------------------------------------------------------

_BASE = {
    "services": {
        "db": {
            "image": "postgres",
            "volumes": [
                "./postgres/data:/var/lib/postgresql/data",
                "./postgres/init_scripts:/docker-entrypoint-initdb.d:ro",
            ],
            "ports": ["5432:5432"],
        },
        "web": {"image": "web", "ports": ["3000:3000"]},
    }
}


def test_override_remaps_rw_relative_bind_to_named_volume_keeps_ro():
    ov = build_compose_override(_BASE, run_id="run1", port_strategy="fixed")
    db_vols = ov["services"]["db"]["volumes"]
    # rw data bind -> per-run named volume; :ro seed bind untouched.
    assert db_vols[0] == "e2e_run1_db_0:/var/lib/postgresql/data"
    assert db_vols[1] == "./postgres/init_scripts:/docker-entrypoint-initdb.d:ro"
    assert ov["volumes"] == {"e2e_run1_db_0": None}
    # fixed strategy: ports unchanged -> web has no override at all.
    assert "web" not in ov.get("services", {})


def test_override_bump_offsets_host_ports():
    ov = build_compose_override(_BASE, run_id="run1", port_strategy="bump")
    web_ports = ov["services"]["web"]["ports"]
    host, container = web_ports[0].split(":")
    assert container == "3000"
    assert int(host) != 3000  # bumped by the deterministic offset
    # deterministic: same run_id -> same offset.
    ov2 = build_compose_override(_BASE, run_id="run1", port_strategy="bump")
    assert ov2["services"]["web"]["ports"] == web_ports


def test_override_empty_when_nothing_to_change():
    base = {"services": {"web": {"image": "web"}}}
    assert build_compose_override(base, run_id="r", port_strategy="fixed") == {}


def test_override_custom_project_prefix():
    ov = build_compose_override(
        _BASE, run_id="r9", port_strategy="fixed", project_prefix="kaya"
    )
    assert "kaya_r9_db_0" in ov["volumes"]


# --- surfaces -----------------------------------------------------------------


def test_build_surfaces_infers_probe_kind_from_target():
    profile = SimpleNamespace(
        service_names=["frontend", "api", "db", "skipme"],
        service_probe_targets=[
            "http://127.0.0.1:3000/",
            "http://127.0.0.1:8000/health",
            "127.0.0.1:5432",
            "",  # empty -> skipped
        ],
    )
    surfaces = build_surfaces(profile)
    assert [(s.name, s.probe_kind) for s in surfaces] == [
        ("frontend", "http_get"),
        ("api", "http_get"),
        ("db", "tcp_connect"),
    ]


# --- secret resolution --------------------------------------------------------


def test_resolve_secret_source_order(tmp_path, monkeypatch):
    monkeypatch.delenv("IRIAI_E2E_SECRET_SRC", raising=False)
    prof_secret = tmp_path / "prof.env"
    prof_secret.write_text("x")
    env_secret = tmp_path / "env.env"
    env_secret.write_text("x")
    default_dir = tmp_path / "repo"
    monkeypatch.setattr(compose_mod, "_repo_root", lambda: default_dir)
    default_secret = default_dir / ".iriai-secrets" / "kaya" / ".env.local"
    default_secret.parent.mkdir(parents=True)
    default_secret.write_text("x")

    # 1) profile path wins.
    prof = SimpleNamespace(secret_source_path=str(prof_secret))
    assert resolve_secret_source(prof, "kaya") == prof_secret
    # 2) env var next.
    monkeypatch.setenv("IRIAI_E2E_SECRET_SRC", str(env_secret))
    assert resolve_secret_source(SimpleNamespace(secret_source_path=""), "kaya") == env_secret
    # 3) default last.
    monkeypatch.delenv("IRIAI_E2E_SECRET_SRC", raising=False)
    assert resolve_secret_source(SimpleNamespace(secret_source_path=""), "kaya") == default_secret


def test_resolve_secret_source_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("IRIAI_E2E_SECRET_SRC", raising=False)
    monkeypatch.setattr(compose_mod, "_repo_root", lambda: tmp_path / "nope")
    assert resolve_secret_source(SimpleNamespace(secret_source_path=""), "kaya") is None


# --- JUnit parser -------------------------------------------------------------


def test_parse_junit_counts_pass_fail_skip():
    xml = """<testsuites>
      <testsuite name="api" errors="0">
        <testcase classname="t.test_a" name="ok" time="0.5"/>
        <testcase classname="t.test_b" name="bad" time="0.1">
          <failure message="assert 1==2">trace here</failure>
        </testcase>
        <testcase classname="t.test_c" name="skip"><skipped message="wip"/></testcase>
      </testsuite>
    </testsuites>"""
    run = parse_junit_xml(xml)
    assert (run.passed, run.failed, run.skipped) == (1, 1, 1)
    assert run.started is True
    bad = [t for t in run.tests if t.status == "failed"][0]
    assert "assert 1==2" in bad.error
    assert bad.duration_ms == 100


def test_parse_junit_single_testsuite_root_and_error_node():
    xml = """<testsuite name="svc">
      <testcase classname="m" name="boom"><error message="import error"/></testcase>
    </testsuite>"""
    run = parse_junit_xml(xml)
    assert run.failed == 1 and run.passed == 0


def test_parse_junit_malformed_is_honest_failure():
    run = parse_junit_xml("<not valid xml")
    assert run.started is False
    assert run.global_errors and "parse error" in run.global_errors[0]


def test_parse_junit_suite_level_errors_surfaced():
    xml = '<testsuite name="s" errors="2"><testcase name="x"/></testsuite>'
    run = parse_junit_xml(xml)
    assert any("errors=2" in e for e in run.global_errors)


# --- verdict conversion -------------------------------------------------------


def test_run_to_verdicts_maps_statuses_and_adds_infra_on_no_tests():
    from iriai_build_v2.workflows.develop.e2e.adapters.playwright_report import (
        PwRunResult,
        PwTestResult,
    )

    run = PwRunResult(
        tests=[
            PwTestResult("t.ok", "t", "passed", False, 1, ""),
            PwTestResult("t.bad", "t", "failed", False, 1, "boom"),
            PwTestResult("t.sk", "t", "skipped", False, 0, ""),
        ],
        passed=1, failed=1, skipped=1, started=True,
    )
    verdicts = run_to_verdicts(run, suite="api", source_commit="abc")
    by_status = sorted(v.status for v in verdicts)
    assert by_status == ["fail", "pass", "skipped"]
    assert all(v.spec_id.startswith("api:") for v in verdicts)
    fail = [v for v in verdicts if v.status == "fail"][0]
    assert fail.failure_class == "regression"

    # global error + nothing started -> a synthetic infra error verdict.
    boot_fail = PwRunResult(global_errors=["webServer down"], started=False)
    verdicts2 = run_to_verdicts(boot_fail, suite="api")
    assert any(v.status == "error" and v.failure_class == "infra" for v in verdicts2)


# --- compose preflight + single-stack mutex -----------------------------------


def test_compose_preflight_ok_when_disk_high_and_no_e2e_project(monkeypatch):
    monkeypatch.setattr(rl, "_MIN_COMPOSE_DISK_GB", 1.0)
    pf = rl.compose_preflight(scratch_dir="/tmp", running_projects=["other-app"])
    assert pf.ok is True
    assert pf.running_projects == []


def test_compose_preflight_mutex_refuses_when_e2e_project_up(monkeypatch):
    monkeypatch.setattr(rl, "_MIN_COMPOSE_DISK_GB", 1.0)
    pf = rl.compose_preflight(
        project_prefix="e2e", scratch_dir="/tmp",
        running_projects=["e2e_run123", "kaya-dev"],
    )
    assert pf.ok is False
    assert pf.running_projects == ["e2e_run123"]
    assert "single-stack mutex" in pf.reason


def test_compose_preflight_refuses_on_low_disk(monkeypatch):
    monkeypatch.setattr(rl, "_MIN_COMPOSE_DISK_GB", 1_000_000.0)
    pf = rl.compose_preflight(scratch_dir="/tmp", running_projects=[])
    assert pf.ok is False
    assert "free_disk" in pf.reason


# --- adapter smoke ------------------------------------------------------------


@pytest.mark.asyncio
async def test_adapter_smoke_probes_each_surface(monkeypatch):
    from iriai_build_v2.workflows.develop.e2e.adapters import Instance, Surface
    from iriai_build_v2.workflows.develop.e2e.models import BootSmoke

    async def fake_probe(surface, *, timeout_s=60.0):
        return BootSmoke(status="pass", surface=surface.name,
                         probe_kind=surface.probe_kind, probe_target=surface.probe_target)

    monkeypatch.setattr(compose_mod, "probe_surface", fake_probe)
    profile = SimpleNamespace()
    inst = Instance(
        profile=profile,
        checkout_dir=".",
        surfaces=[
            Surface(name="web", probe_kind="http_get", probe_target="http://x/"),
            Surface(name="db", probe_kind="tcp_connect", probe_target="127.0.0.1:5432"),
        ],
    )
    out = await ComposeAdapter().smoke(inst, profile)
    assert [b.surface for b in out] == ["web", "db"]
    assert all(b.status == "pass" for b in out)
