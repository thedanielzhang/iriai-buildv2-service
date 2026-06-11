"""Orchestrate the project_profile_inferrer agent + cache the profile artifact.

Standalone, read-only against a checkout: the inferrer inspects the project's
own manifests and emits a ``ProjectProfile``. We cache it under the
``project-profile`` artifact and refine once if the first pass is structurally
incomplete.
"""

from __future__ import annotations

from typing import Any

from iriai_compose import Ask

from iriai_build_v2.roles import project_profile_inferrer

from .models import PROJECT_KINDS, ProjectProfile


def _build_prompt(checkout_dirs: dict[str, str]) -> str:
    lines = [
        "Infer a ProjectProfile for the following checkout(s). Inspect each "
        "repo's OWN manifests (package.json scripts, pyproject, Dockerfile, "
        "Makefile, playwright.config.*). Read-only — do not install or build.",
        "",
        "Checkouts:",
    ]
    for key, path in checkout_dirs.items():
        lines.append(f"  - {key}: {path}")
    lines += [
        "",
        "If there are multiple repos, pick the primary RUNNABLE one as "
        "`repo_path` and list the rest in `extra_repo_paths`. For an Electron / "
        "VS Code fork, set project_kind=electron and capture the Playwright "
        "`webServer` harness configs in `native_test_configs` (from the "
        "`test:e2e:*` scripts) with `native_test_cmd='npx playwright test'`. "
        "Derive a valid `ready_probe` from a webServer/health route.",
        "Emit exactly one ProjectProfile.",
    ]
    return "\n".join(lines)


def is_structurally_valid(profile: ProjectProfile) -> bool:
    """Minimal sanity gate (NOT a green-wash): the profile must be actionable."""
    if profile.project_kind not in PROJECT_KINDS:
        return False
    if not profile.adapter_id:
        return False
    if profile.project_kind == "library":
        return True  # no runnable surface required
    if profile.adapter_id == "compose":
        # Compose-lane validity arm (readiness item 8 / P6): a compose-stack
        # product is brought up via its OWN compose file and probed through the
        # per-service ``service_probe_targets`` (adapters/compose.build_surfaces)
        # — it never has a single ``start_cmd``/``ready_probe_kind``. Without
        # this arm a hand-authored compose profile is rejected as "structurally
        # incomplete" forever (perpetual cache-miss → agentic inference re-runs
        # over the reviewed artifact). Additive: non-compose arms are unchanged.
        return bool(
            profile.compose_file
            and any(t for t in profile.service_probe_targets)
        )
    # any runnable kind needs a way to come up + be probed
    if not (profile.start_cmd or profile.native_test_cmd):
        return False
    if not profile.ready_probe_kind:
        return False
    return True


async def infer_profile(
    runner: Any,
    feature: Any,
    checkout_dirs: dict[str, str],
    *,
    registry: Any | None = None,
    use_cache: bool = True,
    refine: bool = True,
) -> ProjectProfile:
    """Run the inferrer (cache-first); refine once if structurally incomplete."""
    if use_cache and registry is not None:
        cached = await registry.get_profile()
        if cached is not None and is_structurally_valid(cached):
            return cached

    prompt = _build_prompt(checkout_dirs)
    profile: ProjectProfile = await runner.run(
        Ask(actor=project_profile_inferrer, prompt=prompt, output_type=ProjectProfile),
        feature,
    )

    issues: list[str] = []
    if not is_structurally_valid(profile):
        issues.append(
            "structurally incomplete: ensure project_kind is one of "
            f"{PROJECT_KINDS}, adapter_id is set, and (for a runnable kind) "
            "start_cmd or native_test_cmd plus a ready_probe_kind are present"
        )
    issues.extend(profile.alignment_errors())
    if refine and issues:
        refine_prompt = (
            prompt
            + "\n\nYour previous profile had these issues:\n- "
            + "\n- ".join(issues)
            + "\n\nRe-emit a COMPLETE ProjectProfile grounded in the manifests, "
            "keeping every index-aligned list group the SAME length."
        )
        profile = await runner.run(
            Ask(
                actor=project_profile_inferrer,
                prompt=refine_prompt,
                output_type=ProjectProfile,
            ),
            feature,
        )

    if registry is not None:
        await registry.put_profile(profile)
    return profile
