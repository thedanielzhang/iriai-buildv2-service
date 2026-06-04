"""Pydantic models for the async e2e-testing subsystem.

Flat-structured-output rule: control/identity fields are flat primitives
(``str``, ``bool``, ``int``, ``list[str]``, ``dict[str, str]``). Bulky fixtures
and evidence (screenshots, raw reports) live in files referenced by path, never
inlined into structured output an agent must populate.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field

# --------------------------------------------------------------------------- #
# Enumerated string domains (kept as plain str for flat structured output)
# --------------------------------------------------------------------------- #

PROJECT_KINDS = ("api", "full_stack", "cli", "electron", "library")
READY_PROBE_KINDS = ("http_get", "log_line", "exit_zero", "file_exists", "tcp_connect")
VERDICT_STATUSES = ("pass", "fail", "error", "skipped")
# No `drift` class: locator breaks are plain failures re-authored under citation.
FAILURE_CLASSES = ("regression", "intended_change", "flaky", "infra")
SMOKE_STATUSES = ("pass", "fail", "not_applicable")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectProfile(BaseModel):
    """How to install, build, run, probe and natively test a project.

    Inferred by ``project_profile_inferrer`` from the project's OWN manifests
    (package.json scripts, pyproject, Dockerfile, Makefile, playwright.config.*).
    Never invents build logic. Artifact key: ``project-profile``.
    """

    project_kind: str = ""  # one of PROJECT_KINDS
    repo_path: str = ""  # primary runnable repo (relative to the checkout root)
    # Companion repos for multi-repo features (e.g. a backend behind a frontend).
    extra_repo_paths: list[str] = Field(default_factory=list)
    install_cmd: str = ""
    build_cmd: str = ""
    start_cmd: str = ""  # full-app (operator) launch / server start
    teardown_cmd: str = ""
    ready_probe_kind: str = ""  # one of READY_PROBE_KINDS
    ready_probe_target: str = ""  # url path, log substring, or file path
    base_url_template: str = ""  # e.g. "http://127.0.0.1:{port}"
    native_test_cmd: str = ""  # base, e.g. "npx playwright test"
    # Discovered native test configs (e.g. playwright.config.{badge,chat,lifecycle}.ts);
    # the browser adapter runs `{native_test_cmd} --config=<cfg>` per entry.
    native_test_configs: list[str] = Field(default_factory=list)
    # Names ONLY — never secret values.
    env_keys: list[str] = Field(default_factory=list)
    seed_cmd: str = ""  # deterministic fixtures/DB seed; "" = no-op
    adapter_id: str = ""  # browser | http_service | cli | compose
    inference_confidence: float = 0.0
    notes: str = ""

    # --- multi-package provisioning (P1/P2). package_managers[i] is the manager
    #     for package_roots[i]: one of npm | pnpm | pip | poetry. Empty => the
    #     legacy single-root npm path (iriai-studio default, unchanged). ---
    package_roots: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)

    # --- per-service description for a multi-service monorepo (index-aligned). ---
    service_names: list[str] = Field(default_factory=list)
    service_languages: list[str] = Field(default_factory=list)
    service_test_cmds: list[str] = Field(default_factory=list)

    # --- commit-hygiene strategy (P3). Empty => the studio eslint/gulp rule_grant
    #     default. "restage_autofix" = re-stage a formatter's own edits (black). ---
    commit_hygiene_strategy: str = ""  # "" | rule_grant | restage_autofix
    commit_hygiene_parser: str = ""  # "" => eslint_gulp

    # --- authenticated-e2e indirection (P4). KEY NAMES ONLY — never secret values;
    #     resolved at run time from the injected env/.env file. ---
    e2e_test_account_user_key: str = ""
    e2e_test_account_pass_key: str = ""

    # --- docker-compose boot (P4). Empty => non-compose single-surface (studio).
    #     compose_port_strategy: "" / "fixed" => use the product's OWN fixed ports
    #     (kaya needs specific ports; a single-stack mutex serialises passes);
    #     "bump" => offset host ports per run (only for products without fixed-port
    #     requirements). ---
    compose_file: str = ""
    compose_override_file: str = ""
    compose_profiles: list[str] = Field(default_factory=list)
    compose_project_prefix: str = ""
    secret_source_path: str = ""  # orchestrator-side secret store path (never the product repo)
    secret_rel_dst: str = ""  # where to inject the secret inside the clone
    service_probe_targets: list[str] = Field(default_factory=list)  # index-aligned w/ service_names
    service_port_keys: list[str] = Field(default_factory=list)  # env keys (in .env.instance) for host ports
    compose_port_strategy: str = ""  # "" | fixed | bump
    # Container TARGET paths whose host bind is replaced by a per-run NAMED volume
    # (clean per-run state + `down -v` teardown). ONLY these are remapped — config/
    # seed/source binds (init_scripts, *.conf, source mounts) are left intact. e.g.
    # kaya: ["/var/lib/postgresql/data", "/data", "/var/lib/falkordb/data"].
    compose_named_volume_targets: list[str] = Field(default_factory=list)

    def alignment_errors(self) -> list[str]:
        """Non-raising check that index-aligned parallel lists agree in length.

        Returns a list of human-readable mismatches (empty == aligned). Used by
        the inferrer refine loop to ask for a corrected profile rather than
        failing structured-output parsing. Empty lists (the studio default) are
        always aligned.
        """
        errors: list[str] = []
        groups = {
            "package_roots/package_managers": (
                len(self.package_roots),
                len(self.package_managers),
            ),
            "service_names/service_languages/service_test_cmds": (
                len(self.service_names),
                len(self.service_languages),
                len(self.service_test_cmds),
            ),
            "service_names/service_probe_targets/service_port_keys": (
                len(self.service_names),
                len(self.service_probe_targets),
                len(self.service_port_keys),
            ),
        }
        for label, lengths in groups.items():
            nonzero = {n for n in lengths if n}
            if len(nonzero) > 1:
                errors.append(f"{label} lengths disagree: {lengths}")
        return errors


class BootSmoke(BaseModel):
    """Result of a per-surface readiness probe."""

    status: str = "not_applicable"  # one of SMOKE_STATUSES
    surface: str = ""  # web | api | worker | electron | <name>
    detail: str = ""  # precise blocker text on failure (never a false green)
    probe_kind: str = ""
    probe_target: str = ""


class E2ESpecRecord(BaseModel):
    """A durable, provenance-tracked e2e spec bound to acceptance criteria.

    Artifact key: ``e2e-spec:{spec_id}``.
    """

    spec_id: str = ""
    scenario_id: str = ""
    title: str = ""
    adapter_id: str = ""
    priority: str = ""
    # Set + justified by spec_author: real unmocked deps OR downstream prereq.
    critical: bool = False
    critical_justification: str = ""
    linked_ac_ids: list[str] = Field(default_factory=list)
    spec_path: str = ""  # native spec file in the isolated checkout
    # AC-id -> assertion-scoped digest WE compute over only the semantic fields
    # (pass_condition + linked_verifiable_state_id + linked_journey_step_id),
    # NOT the whole-AC content_digest (which flips on cosmetic wording edits).
    author_assertion_digests: dict[str, str] = Field(default_factory=dict)
    author_commit: str = ""  # commit the spec was authored green against
    test_plan_digest: str = ""
    source_commit: str = ""


class E2EVerdictRecord(BaseModel):
    """Outcome of a deterministic replay of one spec at a commit.

    Artifact key: ``e2e-verdict:{spec_id}:{commit}``.
    """

    spec_id: str = ""
    source_commit: str = ""
    status: str = ""  # one of VERDICT_STATUSES
    failure_class: str = ""  # one of FAILURE_CLASSES (only on fail)
    summary: str = ""
    changed_ac_ids: list[str] = Field(default_factory=list)
    critical: bool = False
    evidence_path: str = ""  # screenshots / raw report on disk
    citation: str = ""  # cited AC/requirement change authorizing relaxation


class E2ETrackCursor(BaseModel):
    """Self-coalescing cursor: the last checkpoint the track fully processed.

    Artifact key: ``e2e-track-cursor``.
    """

    last_processed_commit: str = ""
    group_idx: int = -1
    updated_at: str = Field(default_factory=_utcnow_iso)


class E2EStatus(BaseModel):
    """Operator rollup. Artifact key: ``e2e-status`` + control-plane section."""

    latest_checkpoint: str = ""  # "group {idx}" label
    latest_checkpoint_commit: str = ""
    latest_green_checkpoint: str = ""
    boot_smoke: str = "not_applicable"  # one of SMOKE_STATUSES
    passed: int = 0
    failed: int = 0
    flaky: int = 0
    open_regressions: list[str] = Field(default_factory=list)  # spec_ids
    preview_url: str = ""
    updated_at: str = Field(default_factory=_utcnow_iso)


class E2EGreenPointer(BaseModel):
    """Newest checkpoint certified green. Artifact key: ``e2e-green-checkpoint``.

    Green = boot-smoke pass + no open ``critical`` regressions (matches the
    alert tier) — NOT "zero failures ever". Written atomically only after a full
    pass. Resolves ``--checkpoint latest-green``.
    """

    group_idx: int = -1
    result_commits: dict[str, str] = Field(default_factory=dict)  # repo -> commit
    certified_at: str = Field(default_factory=_utcnow_iso)
