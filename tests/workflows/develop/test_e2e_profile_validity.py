"""is_structurally_valid — compose-lane validity arm (readiness item 8 / P6).

EMPIRICAL HAZARD (develop-readiness research §5.5): a hand-authored pure-compose
ProjectProfile (kaya) has no ``start_cmd``/``ready_probe_kind`` — the compose
path probes per-service ``service_probe_targets`` instead (pass_.py compose
branch + adapters/compose.build_surfaces). Without a compose arm the cached
profile is treated as "structurally incomplete" forever (profile.py cache gate)
and agentic inference re-runs over the hand-authored artifact.

The fixture below mirrors the staged kaya profile
(/tmp/kaya_prestage/kaya-projectprofile/kaya-project-profile.json); kaya tokens
here are test-fixture data, not workflow code.
"""

from __future__ import annotations

from iriai_build_v2.workflows.develop.e2e.adapters.compose import build_surfaces
from iriai_build_v2.workflows.develop.e2e.models import ProjectProfile
from iriai_build_v2.workflows.develop.e2e.profile import is_structurally_valid

_KAYA_SERVICES = [
    "spend-client", "spend-visibility", "knowledge-service", "data-service",
    "ai-service", "invoice-service", "supply-chain", "db", "redis",
    "falkordb", "pdfviewer-server", "word-processor-server", "n8n",
]
_KAYA_PROBES = [
    "http://127.0.0.1:3000/", "http://127.0.0.1:8000/",
    "http://127.0.0.1:8040/", "http://127.0.0.1:8060/health",
    "http://127.0.0.1:8070/live", "http://127.0.0.1:8080/live",
    "http://127.0.0.1:8090/live", "127.0.0.1:5432", "127.0.0.1:6379",
    "127.0.0.1:6380", "http://127.0.0.1:6001/", "http://127.0.0.1:6002/",
    "http://127.0.0.1:5678/",
]
_KAYA_PORT_KEYS = [
    "SPEND_CLIENT_PORT", "SPEND_VISIBILITY_APP_PORT",
    "KNOWLEDGE_SERVICE_APP_PORT", "DATA_SERVICE_APP_PORT",
    "AI_SERVICE_APP_PORT", "INVOICE_SERVICE_APP_PORT",
    "SUPPLY_CHAIN_APP_PORT", "DB_PORT", "REDIS_PORT", "FALKORDB_PORT",
    "PDFVIEWER_PORT", "WORD_PROCESSOR_PORT", "N8N_PORT",
]


def _pure_compose_profile(**overrides) -> ProjectProfile:
    """A kaya-shaped pure-compose profile: NO start_cmd, NO ready_probe_kind."""
    base = dict(
        project_kind="full_stack",
        repo_path="kaya-main",
        adapter_id="compose",
        compose_file="common/docker/docker-compose.yaml",
        compose_profiles=["spend-client"],
        compose_project_prefix="kaya-e2e",
        compose_port_strategy="fixed",
        secret_rel_dst="common/docker/.env.local",
        compose_named_volume_targets=[
            "/var/lib/postgresql/data", "/data", "/var/lib/falkordb/data",
        ],
        service_names=list(_KAYA_SERVICES),
        service_languages=["typescript"] + ["python"] * 6 + ["infra"] * 6,
        service_test_cmds=[],
        service_probe_targets=list(_KAYA_PROBES),
        service_port_keys=list(_KAYA_PORT_KEYS),
        package_roots=[".", "ai-service", "data-service", "invoice-service",
                       "knowledge-service", "spend-visibility", "supply-chain"],
        package_managers=["pnpm"] + ["pip"] * 6,
    )
    base.update(overrides)
    return ProjectProfile(**base)


def test_pure_compose_profile_is_valid() -> None:
    """The hazard fix: a probed compose profile must pass the cache gate."""
    assert is_structurally_valid(_pure_compose_profile()) is True


def test_pure_compose_profile_is_aligned() -> None:
    assert _pure_compose_profile().alignment_errors() == []


def test_compose_profile_without_probe_targets_rejected() -> None:
    """A compose profile that can't be probed is still structurally incomplete."""
    profile = _pure_compose_profile(
        service_probe_targets=[], service_port_keys=[], service_names=[],
        service_languages=[],
    )
    assert is_structurally_valid(profile) is False


def test_compose_profile_with_only_empty_probe_targets_rejected() -> None:
    profile = _pure_compose_profile(
        service_probe_targets=[""] * len(_KAYA_SERVICES)
    )
    assert is_structurally_valid(profile) is False


def test_compose_profile_without_compose_file_rejected() -> None:
    assert is_structurally_valid(_pure_compose_profile(compose_file="")) is False


def test_electron_arm_unchanged() -> None:
    """Studio-shaped profiles keep today's exact semantics."""
    studio = ProjectProfile(
        project_kind="electron", repo_path="iriai-studio", adapter_id="browser",
        native_test_cmd="npx playwright test",
        ready_probe_kind="http_get",
        ready_probe_target="http://127.0.0.1:8787/healthz",
    )
    assert is_structurally_valid(studio) is True
    # missing ready_probe_kind still rejects a non-compose runnable kind
    assert is_structurally_valid(
        studio.model_copy(update={"ready_probe_kind": ""})
    ) is False
    # missing both start_cmd and native_test_cmd still rejects
    assert is_structurally_valid(
        studio.model_copy(update={"native_test_cmd": ""})
    ) is False


def test_library_arm_unchanged() -> None:
    profile = ProjectProfile(project_kind="library", adapter_id="cli")
    assert is_structurally_valid(profile) is True


def test_unknown_kind_and_missing_adapter_still_rejected() -> None:
    assert is_structurally_valid(
        _pure_compose_profile(project_kind="nonsense")
    ) is False
    assert is_structurally_valid(_pure_compose_profile(adapter_id="")) is False


def test_kaya_probe_surfaces_inferred_correctly() -> None:
    """build_surfaces must derive http_get for URLs and tcp_connect for host:port."""
    surfaces = build_surfaces(_pure_compose_profile())
    by_name = {s.name: s for s in surfaces}
    assert len(surfaces) == 13
    assert by_name["spend-client"].probe_kind == "http_get"
    assert by_name["data-service"].probe_target.endswith("/health")
    assert by_name["db"].probe_kind == "tcp_connect"
    assert by_name["falkordb"].probe_target == "127.0.0.1:6380"
