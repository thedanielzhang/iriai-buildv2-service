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
    last_sources: dict | None = None

    def __init__(self, **_kw):
        FakeSubstrate.constructed += 1
        self.run_id = "rid8"
        self.torn = False

    async def clone_checkpoint(self, sources, commits):
        FakeSubstrate.last_sources = dict(sources)
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
async def test_compose_preflight_refuse_raises_typed_and_is_loud(monkeypatch):
    # Item-11 G2/G3: refusal raises E2EPassRefused (so callers hold the cursor)
    # + writes a durable e2e-blocker row + pages, instead of a silent summary.
    from iriai_build_v2.workflows.develop.e2e.registry import BLOCKER_KEY
    from iriai_build_v2.workflows.develop.e2e.status import CapturingPoster

    adapter = FakeComposeAdapter(smokes=[], verdicts=[])
    _wire(monkeypatch, adapter, preflight_ok=False)
    reg = FakeRegistry(_profile())
    poster = CapturingPoster()
    with pytest.raises(pass_mod.E2EPassRefused) as ei:
        await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                            live_dsn="x", profile=_profile(), poster=poster)
    assert "preflight refused" in str(ei.value)
    # Nothing was stood up: no substrate, no green pointer.
    assert FakeSubstrate.constructed == 0
    assert reg.green is None
    # Durable visibility: blocker row + page card + status row.
    blocker = reg.raw[BLOCKER_KEY]
    assert blocker["checkpoint"] == "group 5"
    assert blocker["blockers"][0]["surface"] == "compose-preflight"
    assert any("BLOCKER" in text for _blocks, text in poster.cards)
    assert reg.status is not None


@pytest.mark.asyncio
async def test_compose_preflight_refuse_page_dedupes_on_retry(monkeypatch):
    # The 10s poll loop retries the SAME checkpoint: the second refusal for the
    # same (checkpoint, reason) must NOT page again (no page-spam).
    adapter = FakeComposeAdapter(smokes=[], verdicts=[])
    _wire(monkeypatch, adapter, preflight_ok=False)
    from iriai_build_v2.workflows.develop.e2e.status import CapturingPoster

    reg = FakeRegistry(_profile())
    poster = CapturingPoster()
    for _ in range(2):
        with pytest.raises(pass_mod.E2EPassRefused):
            await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                                live_dsn="x", profile=_profile(), poster=poster)
    blocker_cards = [t for _b, t in poster.cards if "BLOCKER" in t]
    assert len(blocker_cards) == 1  # paged once, deduped on retry


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
    # Sources come from the checkpoint's actual repo_path, NOT the studio template.
    assert FakeSubstrate.last_sources == {"kaya-main": "/x/kaya-main"}


@pytest.mark.asyncio
async def test_compose_multisegment_repo_path_does_not_crash(monkeypatch):
    # profile.repo_path is a multi-segment path; result_commits()/clone keys are
    # basenames, so the basename match + fallback must avoid a KeyError.
    smokes = [BootSmoke(status="pass", surface="frontend", probe_kind="http_get")]
    adapter = FakeComposeAdapter(smokes=smokes, verdicts=[])
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(_profile())
    profile = ProjectProfile(
        project_kind="full_stack", adapter_id="compose",
        repo_path="services/spend-client",  # multi-segment -> basename "spend-client"
        compose_project_prefix="e2e", compose_file="docker-compose.yaml")

    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=profile)

    # checkpoint repo is "kaya-main"; basename "spend-client" not in checkouts ->
    # defensive fallback to the first cloned repo, no crash.
    assert out.boot_smoke == "pass"


@pytest.mark.asyncio
async def test_compose_empty_surfaces_fails_and_pages(monkeypatch):
    adapter = FakeComposeAdapter(smokes=[], verdicts=[])  # no surfaces came up
    _wire(monkeypatch, adapter)
    reg = FakeRegistry(_profile())
    out = await run_full_pass(_checkpoint(), feature_id="f", registry=reg,
                              live_dsn="x", profile=_profile())
    assert out.boot_smoke == "fail"
    assert out.green is False
    assert adapter.ran is False  # no host tests when nothing came up


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
