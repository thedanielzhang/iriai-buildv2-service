"""P4c: run_full_pass compose branch — dispatch, preflight mutex skip, green on
boot+tests pass, boot-fail blocks green, provision-failure teardown. No docker:
the adapter/substrate/preflight are stubbed; the studio path is never touched.
"""

from __future__ import annotations

import pytest

from iriai_build_v2.workflows.develop.e2e import pass_ as pass_mod
from iriai_build_v2.workflows.develop.e2e import runner_loop as rl
from iriai_build_v2.workflows.develop.e2e.adapters import Instance
from iriai_build_v2.workflows.develop.e2e.checkpoint import (
    RepoCheckpoint,
    SealedCheckpoint,
)
from iriai_build_v2.workflows.develop.e2e.models import (
    BootSmoke,
    E2EVerdictRecord,
    ProjectProfile,
)
from iriai_build_v2.workflows.develop.e2e.pass_ import run_full_pass


def _checkpoint() -> SealedCheckpoint:
    return SealedCheckpoint(
        feature_id="f",
        group_idx=5,
        repos=[RepoCheckpoint(repo_id="r", repo_path="/x/kaya-main",
                              result_commit="abc123")],
    )


def _profile() -> ProjectProfile:
    return ProjectProfile(
        project_kind="full_stack", adapter_id="compose", repo_path="kaya-main",
        compose_project_prefix="e2e", compose_file="docker-compose.yaml",
        compose_profiles=["spend-client"],
    )


class FakeRegistry:
    def __init__(self, profile):
        self._profile = profile
        self.verdicts: list = []
        self.green = None
        self.status = None
        self.raw: dict = {}

    async def get_profile(self):
        return self._profile

    async def put_verdict(self, v):
        self.verdicts.append(v)

    async def get_raw(self, key):
        return self.raw.get(key)

    async def put_raw(self, key, val):
        self.raw[key] = val

    async def put_green_pointer(self, gp):
        self.green = gp

    async def put_status(self, s):
        self.status = s


class _Checkout:
    def __init__(self, d):
        self.checkout_dir = d


class FakeSubstrate:
    constructed = 0

    def __init__(self, **_kw):
        FakeSubstrate.constructed += 1
        self.run_id = "rid8"
        self.torn = False

    async def clone_checkpoint(self, sources, commits):
        return {key: _Checkout(f"/tmp/{key}") for key in commits}

    async def teardown(self):
        self.torn = True


class FakeComposeAdapter:
    adapter_id = "compose"

    def __init__(self, smokes, verdicts, *, provision_exc=None):
        self.smokes = smokes
        self.verdicts = verdicts
        self.provision_exc = provision_exc
        self.ran = False
        self.torn = False

    async def provision(self, profile, checkout, *, runner=None, feature=None,
                        substrate=None, run_id=None, project_slug=""):
        if self.provision_exc:
            raise self.provision_exc
        inst = Instance(profile=profile, checkout_dir=checkout, surfaces=[])
        inst.substrate = substrate
        return inst

    async def smoke(self, instance, profile):
        return self.smokes

    async def run(self, instance, specs, *, runner=None, feature=None,
                  source_commit=""):
        self.ran = True
        return self.verdicts

    async def teardown(self, instance):
        self.torn = True


def _wire(monkeypatch, adapter, *, preflight_ok=True):
    monkeypatch.setattr(pass_mod, "CloneSubstrate", FakeSubstrate)
    monkeypatch.setattr(pass_mod, "get_adapter", lambda _id: adapter)
    monkeypatch.setattr(
        rl, "compose_preflight",
        lambda **k: rl.ComposePreflight(
            ok=preflight_ok, free_disk_gb=100.0,
            reason="" if preflight_ok else "single-stack mutex: e2e_x up"),
    )
    FakeSubstrate.constructed = 0


@pytest.mark.asyncio
async def test_run_full_pass_dispatches_to_compose_branch(monkeypatch):
    called = {}

    async def fake_compose_pass(checkpoint, **kw):
        called["yes"] = kw.get("profile")
        return pass_mod.PassSummary(group_idx=checkpoint.group_idx)

    monkeypatch.setattr(pass_mod, "_run_compose_pass", fake_compose_pass)
    out = await run_full_pass(_checkpoint(), feature_id="f", registry=None,
                              live_dsn="x", profile=_profile())
    assert called["yes"].adapter_id == "compose"
    assert out.group_idx == 5


@pytest.mark.asyncio
async def test_compose_preflight_refuse_is_honest_skip(monkeypatch):
    adapter = FakeComposeAdapter(smokes=[], verdicts=[])
    _wire(monkeypatch, adapter, preflight_ok=False)
    reg = FakeRegistry(_profile())
    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_profile())
    assert out.boot_smoke == "fail"
    assert out.green is False
    assert "preflight refused" in out.detail
    # Nothing was stood up: no substrate, no green pointer.
    assert FakeSubstrate.constructed == 0
    assert reg.green is None


@pytest.mark.asyncio
async def test_compose_happy_path_sets_green(monkeypatch):
    smokes = [
        BootSmoke(status="pass", surface="frontend", probe_kind="http_get"),
        BootSmoke(status="pass", surface="db", probe_kind="tcp_connect"),
    ]
    verdicts = [E2EVerdictRecord(spec_id="api:t1", status="pass")]
    adapter = FakeComposeAdapter(smokes=smokes, verdicts=verdicts)
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(_profile())

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_profile())

    assert out.boot_smoke == "pass"
    assert out.green is True
    assert reg.green is not None
    assert adapter.ran is True
    assert [v.spec_id for v in reg.verdicts] == ["api:t1"]
    assert adapter.torn is True  # teardown ran


@pytest.mark.asyncio
async def test_compose_boot_fail_blocks_green_and_skips_tests(monkeypatch):
    smokes = [
        BootSmoke(status="pass", surface="frontend", probe_kind="http_get"),
        BootSmoke(status="fail", surface="db", probe_kind="tcp_connect",
                  detail="connection refused"),
    ]
    adapter = FakeComposeAdapter(smokes=smokes, verdicts=[])
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(_profile())

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_profile())

    assert out.boot_smoke == "fail"
    assert out.green is False
    assert reg.green is None
    assert adapter.ran is False  # no host tests on a dead stack
    assert adapter.torn is True


@pytest.mark.asyncio
async def test_compose_provision_failure_tears_down(monkeypatch):
    adapter = FakeComposeAdapter(
        smokes=[], verdicts=[], provision_exc=RuntimeError("compose up failed"))
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(_profile())

    # Capture the substrate instance to assert it was torn down on the failure path.
    created: list = []
    orig_init = FakeSubstrate.__init__

    def capturing_init(self, **kw):
        orig_init(self, **kw)
        created.append(self)

    monkeypatch.setattr(FakeSubstrate, "__init__", capturing_init)

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_profile())

    assert out.boot_smoke == "fail"
    assert out.green is False
    assert "provision failed" in out.detail
    assert created and created[0].torn is True  # substrate down -v + rmtree ran
