from __future__ import annotations

import asyncio as _asyncio
import itertools
import json
import logging
import shutil
from pathlib import Path

from iriai_compose import AgentActor, Ask, Feature, Gate, Phase, Respond, WorkflowRunner, to_str
from iriai_compose.actors import Role

from ....config import BUDGET_TIERS
from ....models.outputs import (
    BugFixAttempt,
    BugGroup,
    BugTriage,
    EnhancementBacklog,
    EnhancementItem,
    Envelope,
    FindingLedger,
    FindingRecord,
    HandoverDoc,
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    ReviewOutcome,
    RootCauseAnalysis,
    Verdict,
    envelope_done,
)
from ....models.state import BuildState
from ....roles import (
    implementer,
    integration_tester,
    lead_architect_gate_reviewer,
    qa_engineer,
    regression_tester,
    reviewer,
    root_cause_analyst,
    security_auditor,
    test_author,
    user,
    verifier,
)
from ....services.markdown import to_markdown
from ..._common._tasks import HostedInterview

logger = logging.getLogger(__name__)

VERIFY_RETRIES = 2
WARN_AFTER_CYCLES = 3
MAX_FIX_ATTEMPTS = 7
BLOCKING_SEVERITIES = frozenset({"blocker", "major"})

# ── Inline triage role (lightweight, no tools) ───────────────────────────────

_triage_role = Role(
    name="bug-triager",
    prompt=(
        "You triage bug reports from code review verdicts. Group ALL "
        "issues by their likely root cause. Issues that probably stem from "
        "the same underlying problem (same file, same data flow, same "
        "missing check) go in the same group. Every issue must be assigned "
        "to a group — do not skip or defer any."
    ),
    tools=[],
    model=BUDGET_TIERS["opus"],
)


# ── Worktree management ─────────────────────────────────────────────────────


def _discover_repo(file_path: str, workspace_root: Path) -> Path | None:
    """Find an EXISTING repo by walking the path for .git directories."""
    parts = Path(file_path).parts
    for depth in range(1, len(parts)):
        candidate = workspace_root / Path(*parts[:depth])
        if (candidate / ".git").exists():
            return Path(*parts[:depth])
    return None


def _infer_new_repo_from_tasks(
    tasks: list[ImplementationTask],
) -> dict[str, list[str]]:
    """For tasks whose file paths don't match existing repos, infer new repo
    boundaries from the longest common path prefix per subfeature.

    Returns ``{ws_rel_repo_path: [task_ids]}``.
    """
    sf_paths: dict[str, list[str]] = {}
    for task in tasks:
        sf = task.subfeature_id or "unknown"
        for fs in task.file_scope:
            sf_paths.setdefault(sf, []).append(fs.path)

    new_repos: dict[str, list[str]] = {}
    for sf, paths in sf_paths.items():
        if not paths:
            continue
        split = [p.split("/") for p in paths]
        common: list[str] = []
        for parts in zip(*split):
            if len(set(parts)) == 1:
                common.append(parts[0])
            else:
                break
        if common:
            repo_path = "/".join(common)
            task_ids = [t.id for t in tasks if t.subfeature_id == sf]
            new_repos[repo_path] = task_ids

    return new_repos


async def _ensure_task_worktrees(
    runner: WorkflowRunner,
    feature: Feature,
    tasks: list[ImplementationTask],
) -> None:
    """Ensure worktrees exist for all repos referenced by a group of tasks.

    - Existing repos: discovered by walking ``.git`` directories.
    - New repos: inferred from the longest common path prefix per subfeature,
      then scaffolded inside the feature sandbox.
    - Read-only repos: cloned into the feature sandbox so writes cannot
      escape through symlink resolution.
    - All repo copies mirror workspace-relative paths under
      ``.iriai/features/{slug}/repos/`` so DAG file paths resolve.
    """
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return

    workspace_root: Path = workspace_mgr._base
    feature_root = workspace_root / ".iriai" / "features" / feature.slug / "repos"
    feature_root.mkdir(parents=True, exist_ok=True)

    repos_needed: dict[str, str] = {}  # ws_rel_path → action

    for task in tasks:
        # 1. Explicit repo_path from task planner
        if task.repo_path:
            action = "read_only"
            for fs in task.file_scope:
                if fs.action in ("create", "modify"):
                    action = "extend"
                    break
            repos_needed.setdefault(task.repo_path, action)
            continue

        # 2. Discover existing repos from file_scope
        for fs in task.file_scope:
            repo_path = _discover_repo(fs.path, workspace_root)
            if repo_path:
                action = "read_only" if fs.action == "read_only" else "extend"
                repos_needed.setdefault(str(repo_path), action)

    # 3. Infer new repos from common-prefix for unresolved writable paths
    unresolved = [
        t for t in tasks
        if not t.repo_path and any(
            _discover_repo(fs.path, workspace_root) is None
            and fs.action in ("create", "modify")
            for fs in t.file_scope
        )
    ]
    if unresolved:
        new_repos = _infer_new_repo_from_tasks(unresolved)
        for repo_path in new_repos:
            repos_needed.setdefault(repo_path, "new")

    # 4. Create feature-local repo copies
    for ws_rel_path, action in repos_needed.items():
        worktree_dest = feature_root / ws_rel_path
        if _is_isolated_repo_copy(worktree_dest):
            continue
        if worktree_dest.exists():
            _remove_repo_path(worktree_dest)

        source_path = workspace_root / ws_rel_path

        if action == "new":
            logger.info("Scaffolding new feature-local repo at %s", worktree_dest)
            worktree_dest.parent.mkdir(parents=True, exist_ok=True)
            await _scaffold_repo(worktree_dest)
            continue

        if not (source_path / ".git").exists():
            logger.info("Scaffolding feature-local repo at %s", worktree_dest)
            worktree_dest.parent.mkdir(parents=True, exist_ok=True)
            await _scaffold_repo(worktree_dest)
            continue

        branch = None if action == "read_only" else f"feature/{feature.slug}"
        await _clone_repo(source_path, worktree_dest, branch=branch)
        logger.info("Cloned %s → %s (branch: %s)", ws_rel_path, worktree_dest, branch or "default")

    # Set the worktree root as a service so ALL agents in this phase
    # automatically get cwd=repos/ via TrackedWorkflowRunner.resolve().
    # Implementers/fixers can still override to a specific repo via
    # workspace_override in metadata for more precision.
    #
    # Filesystem isolation is enforced by ClaudeAgentOptions.sandbox
    # (OS-level Seatbelt/bubblewrap), not by soft instructions.
    runner.services["worktree_root"] = feature_root


def _is_isolated_repo_copy(path: Path) -> bool:
    """Return true when *path* is a standalone git clone, not a linked path."""
    return path.exists() and not path.is_symlink() and (path / ".git").is_dir()


def _remove_repo_path(path: Path) -> None:
    """Remove an existing feature repo path so it can be recreated safely."""
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if path.exists():
        shutil.rmtree(path)


async def _clone_repo(source_path: Path, dest: Path, *, branch: str | None) -> None:
    """Clone a repo into the feature sandbox without mutating the source repo."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    await _run_git(
        dest.parent,
        "clone",
        "--no-local",
        str(source_path),
        str(dest),
    )
    if branch:
        await _run_git(dest, "checkout", "-B", branch)


def _write_sandbox_settings(feature_root: Path) -> None:
    """Write .claude/settings.json to each repo worktree to sandbox writes.

    Claude Code follows the .git worktree link and can discover the main
    repo. The sandbox filesystem restrictions prevent writes outside the
    worktree directory.
    """
    import json as _json

    settings = {
        "permissions": {
            "allow": [
                "Read(**)",
                "Edit(**)",
                "Write(**)",
                "Glob(**)",
                "Grep(**)",
                "Bash(git *)",
                "Bash(python *)",
                "Bash(pip *)",
                "Bash(npm *)",
                "Bash(npx *)",
                "Bash(node *)",
                "Bash(ls *)",
                "Bash(mkdir *)",
                "Bash(cat *)",
                "Bash(cd *)",
            ],
            "deny": [],
        },
    }

    for worktree_dir in feature_root.rglob(".git"):
        repo_dir = worktree_dir.parent
        if repo_dir == feature_root:
            continue
        # Only handle worktree .git files (not real .git directories)
        if not worktree_dir.is_file():
            continue

        claude_dir = repo_dir / ".claude"
        claude_dir.mkdir(exist_ok=True)

        settings_path = claude_dir / "settings.json"
        if settings_path.exists():
            continue  # Don't overwrite existing settings

        settings_path.write_text(_json.dumps(settings, indent=2), encoding="utf-8")

        # Also write a CLAUDE.md with explicit workspace boundaries
        claude_md = repo_dir / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(
                "# Workspace Boundaries\n\n"
                "You are working in a git worktree. "
                "ALL file operations must stay within this directory.\n\n"
                "- Do NOT write to any path outside this directory\n"
                "- Do NOT navigate to parent directories to find other repos\n"
                "- Do NOT use absolute paths\n"
                "- All file paths in your task are relative to THIS directory\n",
                encoding="utf-8",
            )

        logger.info("Sandbox settings written to %s", repo_dir)


async def _scaffold_repo(path: Path) -> None:
    """Initialize a new git repo with minimal files."""
    path.mkdir(parents=True, exist_ok=True)
    readme = path / "README.md"
    readme.write_text(f"# {path.name}\n", encoding="utf-8")

    gitignore = path / ".gitignore"
    gitignore.write_text(
        "__pycache__/\n*.pyc\nnode_modules/\n.env\ndist/\nbuild/\n",
        encoding="utf-8",
    )

    await _run_git(path, "init", "-b", "main")
    await _run_git(path, "add", "-A")
    await _run_git(path, "commit", "-m", "chore: scaffold")


async def _run_git(cwd: Path, *args: str) -> str:
    """Run a git command asynchronously."""
    proc = await _asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        stdout=_asyncio.subprocess.PIPE,
        stderr=_asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): "
            f"{stderr.decode().strip()}"
        )
    return stdout.decode().strip()


# ── Parallel actor helpers ──────────────────────────────────────────────────


def _make_parallel_actor(
    base: AgentActor,
    suffix: str,
    *,
    runtime: str | None = None,
    workspace_path: str | None = None,
) -> AgentActor:
    """Create a parallel-safe copy of an AgentActor with a unique name.

    When *runtime* is set (``"primary"`` or ``"secondary"``), the actor's
    role metadata is updated so ``TrackedWorkflowRunner.resolve()`` routes
    it to the correct runtime for adversarial multi-model execution.

    When *workspace_path* is set, it overrides the agent's ``cwd`` so
    it operates within a specific repo worktree (not the main workspace).
    """
    metadata = dict(base.role.metadata)
    if runtime:
        metadata["runtime"] = runtime
    if workspace_path:
        metadata["workspace_override"] = workspace_path
    role = base.role.model_copy(update={"metadata": metadata})
    return AgentActor(
        name=f"{base.name}-{suffix}",
        role=role,
        context_keys=base.context_keys,
        persistent=base.persistent,
    )


class ImplementationPhase(Phase):
    name = "implementation"

    async def execute(
        self, runner: WorkflowRunner, feature: Feature, state: BuildState
    ) -> BuildState:
        dag_json = await runner.artifacts.get("dag", feature=feature)
        dag = ImplementationDAG.model_validate_json(dag_json)

        prior_attempts: list[BugFixAttempt] = []
        bug_counter = itertools.count(1)
        cycle = 0

        while True:
            if cycle >= WARN_AFTER_CYCLES:
                logger.warning(
                    "Implementation cycle %d (exceeded %d without approval)",
                    cycle + 1,
                    WARN_AFTER_CYCLES,
                )

            # ── Step 1: Implementation ───────────────────────────────────
            impl_text, dag_failure, handover = await _implement_dag(runner, feature, dag)

            await runner.artifacts.put("implementation", impl_text, feature=feature)
            await runner.artifacts.put("handover", to_str(handover), feature=feature)
            state.implementation = impl_text
            state.handover = to_str(handover)

            # If the DAG stopped early on a verify failure, go through RCA
            if dag_failure:
                attempts = await _diagnose_and_fix(
                    runner, feature, dag_failure, "verify",
                    qa_engineer, implementer, prior_attempts, bug_counter,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "verify") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "DAG verification", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # Compress handover before passing to review/QA gates
            handover.compress()
            handover_context = to_markdown(handover)

            # ── Adversarial runtime routing for post-DAG gates ──────────
            # The last implementation group used impl_runtime based on its
            # index parity.  Post-DAG gates (review, security, QA,
            # integration, verifier) use the opposite runtime so a
            # different model audits the work.  Fixes go back to the
            # implementation runtime.
            last_group_idx = len(dag.execution_order) - 1
            gate_runtime = "secondary" if last_group_idx % 2 == 0 else "primary"
            fix_runtime = "primary" if last_group_idx % 2 == 0 else "secondary"
            logger.info(
                "Post-DAG gates: gate_runtime=%s, fix_runtime=%s (last_group=%d)",
                gate_runtime, fix_runtime, last_group_idx,
            )

            # ── Step 2: Code Review (static) ─────────────────────────────
            if await runner.artifacts.get("dag-gate:code-review", feature=feature):
                logger.info("Code review gate already passed — skipping")
                review_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                review_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            reviewer, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Review the implementation for code quality, adherence to "
                            "the technical plan, design decisions, and system design. "
                            "Cross-check against the full upstream artifacts in your context."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "review-verdict", to_str(review_verdict), feature=feature
                )

            # Ledger dedup + severity partition
            if isinstance(review_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                review_verdict, _suppressed = _dedup_findings(review_verdict, ledger, "code_reviewer")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from code_reviewer", len(_suppressed))
                review_verdict, _enhancements = _partition_verdict(review_verdict, "code_reviewer", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, review_verdict, "code_reviewer", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(review_verdict):
                await runner.artifacts.put(
                    "dag-gate:code-review", "approved", feature=feature
                )

            if not _is_approved(review_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, review_verdict, "code_reviewer",
                    _make_parallel_actor(reviewer, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "cr-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "code_reviewer") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Code Review", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 3: Security Audit (static) ──────────────────────────
            if await runner.artifacts.get("dag-gate:security", feature=feature):
                logger.info("Security gate already passed — skipping")
                security_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                security_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            security_auditor, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Audit the implementation for security vulnerabilities. "
                            "Check OWASP Top 10, auth on every endpoint, secrets in "
                            "code, input validation, and data exposure. Cross-check "
                            "against the security profile in the PRD."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "security-verdict", to_str(security_verdict), feature=feature
                )

            if isinstance(security_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                security_verdict, _suppressed = _dedup_findings(security_verdict, ledger, "security_auditor")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from security_auditor", len(_suppressed))
                security_verdict, _enhancements = _partition_verdict(security_verdict, "security_auditor", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, security_verdict, "security_auditor", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(security_verdict):
                await runner.artifacts.put(
                    "dag-gate:security", "approved", feature=feature
                )

            if not _is_approved(security_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, security_verdict, "security_auditor",
                    _make_parallel_actor(security_auditor, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "sec-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "security_auditor") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Security Audit", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 4: Test Authoring ────────────────────────────────────
            test_checkpoint = await runner.artifacts.get(
                "dag-gate:test-authoring", feature=feature,
            )
            if test_checkpoint:
                logger.info("Test authoring gate already passed — skipping")
                test_result = ImplementationResult.model_validate_json(test_checkpoint)
            else:
                test_result = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            test_author, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Write tests for this implementation. For each acceptance "
                            "criterion in the PRD, write at least one test. For each "
                            "counterexample, write a test that verifies the wrong thing "
                            "does NOT happen. Use the project's existing test framework "
                            "and patterns. Write both unit tests and integration/E2E tests "
                            "where appropriate.\n\n"
                            "For web/full-stack projects, write Playwright E2E tests that "
                            "test user journeys via real UI interactions."
                        ),
                        output_type=ImplementationResult,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put("test-authoring", to_str(test_result), feature=feature)
                await runner.artifacts.put(
                    "dag-gate:test-authoring",
                    test_result.model_dump_json(),
                    feature=feature,
                )
                await _commit_repos(runner, feature, "test: add tests")

            # ── Step 5: Full QA (dynamic) ─────────────────────────────────
            if await runner.artifacts.get("dag-gate:qa", feature=feature):
                logger.info("QA gate already passed — skipping")
                qa_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                qa_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            qa_engineer, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Test the full implementation. Run the test suite, check "
                            "for runtime errors, and verify the acceptance criteria "
                            "from the PRD and design specs are met. Cross-check "
                            "implementation against the full upstream artifacts "
                            "in your context."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put("qa-verdict", to_str(qa_verdict), feature=feature)

            if isinstance(qa_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                qa_verdict, _suppressed = _dedup_findings(qa_verdict, ledger, "qa_engineer")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from qa_engineer", len(_suppressed))
                qa_verdict, _enhancements = _partition_verdict(qa_verdict, "qa_engineer", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, qa_verdict, "qa_engineer", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(qa_verdict):
                await runner.artifacts.put("dag-gate:qa", "approved", feature=feature)

            if not _is_approved(qa_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, qa_verdict, "qa_engineer",
                    _make_parallel_actor(qa_engineer, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "qa-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "qa_engineer") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "QA", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 6: Integration Test (dynamic) ────────────────────────
            if await runner.artifacts.get("dag-gate:integration", feature=feature):
                logger.info("Integration gate already passed — skipping")
                integration_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                integration_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            integration_tester, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Execute ALL user journeys from the PRD against the "
                            "implementation. Use Playwright for UI journeys, Bash "
                            "for API/CLI journeys. Every journey step must produce "
                            "evidence. Check happy paths, error cases, and boundary "
                            "conditions."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "integration-verdict", to_str(integration_verdict), feature=feature
                )

            if isinstance(integration_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                integration_verdict, _suppressed = _dedup_findings(integration_verdict, ledger, "integration_tester")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from integration_tester", len(_suppressed))
                integration_verdict, _enhancements = _partition_verdict(integration_verdict, "integration_tester", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, integration_verdict, "integration_tester", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(integration_verdict):
                await runner.artifacts.put(
                    "dag-gate:integration", "approved", feature=feature
                )

            if not _is_approved(integration_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, integration_verdict, "integration_tester",
                    _make_parallel_actor(integration_tester, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "int-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "integration_tester") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Integration Test", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Step 7: Verifier — confirm all journeys work ─────────────
            if await runner.artifacts.get("dag-gate:verifier", feature=feature):
                logger.info("Verifier gate already passed — skipping")
                verifier_verdict = Verdict(approved=True, summary="Previously approved")
            else:
                verifier_verdict = await runner.run(
                    Ask(
                        actor=_make_parallel_actor(
                            verifier, "gate", runtime=gate_runtime,
                        ),
                        prompt=(
                            f"## Implementation Handover\n\n{handover_context}\n\n"
                            "Verify that ALL user journeys from the PRD work end-to-end.\n\n"
                            "**For projects with a frontend/UI:**\n"
                            "- Interact with the UI via real Playwright clicks and form fills "
                            "— do not substitute API calls.\n"
                            "- You MUST capture Playwright screenshots for every journey step. "
                            "Save screenshots to a `screenshots/` directory in the project root "
                            "using descriptive names: `{journey_id}_{step}.png` "
                            "(e.g., `J1_create_workflow.png`, `J2_add_node.png`).\n"
                            "- Use `page.screenshot(path='screenshots/...')` after each step.\n"
                            "- A UI journey without screenshot evidence is NOT verified.\n\n"
                            "**For pure backend/library projects:**\n"
                            "- Run the test suite and verify all tests pass.\n"
                            "- Execute API endpoints or CLI commands and verify responses.\n"
                            "- Capture terminal output as evidence where appropriate.\n\n"
                            "Every journey must produce evidence of working correctly."
                        ),
                        output_type=Verdict,
                    ),
                    feature,
                    phase_name=self.name,
                )
                await runner.artifacts.put(
                    "verifier-verdict", to_str(verifier_verdict), feature=feature
                )

            if isinstance(verifier_verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                verifier_verdict, _suppressed = _dedup_findings(verifier_verdict, ledger, "verifier")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from verifier", len(_suppressed))
                verifier_verdict, _enhancements = _partition_verdict(verifier_verdict, "verifier", "post-dag-gate")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, verifier_verdict, "verifier", cycle)
                await _save_ledger(runner, feature, ledger)

            if _is_approved(verifier_verdict):
                await runner.artifacts.put(
                    "dag-gate:verifier", "approved", feature=feature
                )

            if not _is_approved(verifier_verdict):
                attempts = await _diagnose_and_fix(
                    runner, feature, verifier_verdict, "verifier",
                    _make_parallel_actor(verifier, "recheck", runtime=gate_runtime),
                    _make_parallel_actor(implementer, "vfy-fix", runtime=fix_runtime),
                    prior_attempts, bug_counter,
                    handover_context=handover_context,
                )
                prior_attempts.extend(attempts)
                await _store_attempts(runner, feature, prior_attempts)
                failed = [a for a in attempts if a.re_verify_result != "PASS"]
                if failed and _count_source_attempts(prior_attempts, "verifier") >= MAX_FIX_ATTEMPTS:
                    await _escalate_to_user(
                        runner, feature, self.name,
                        "Verifier", failed[0], prior_attempts,
                    )
                cycle += 1
                continue

            # ── Push clones back to source repos ───────────────────────
            await _push_clones_to_source(runner, feature)

            # ── Step 8: Implementation Report ────────────────────────────
            from ....services.implementation_report import (
                render_implementation_report,
                validate_report,
            )

            # Collect artifact URLs from hosting service
            artifact_urls = _collect_artifact_urls(runner)

            # Collect any Playwright screenshots from the workspace
            screenshot_paths = _collect_screenshots(feature, runner)

            all_verdicts = {
                "qa": qa_verdict,
                "integration": integration_verdict,
                "code_review": review_verdict,
                "security": security_verdict,
                "verifier": verifier_verdict,
            }

            report_html = render_implementation_report(
                feature_name=feature.name,
                handover=handover,
                verdicts=all_verdicts,
                bug_fix_attempts=prior_attempts,
                test_result=test_result,
                artifact_urls=artifact_urls,
                screenshot_paths=screenshot_paths,
            )

            # Validate the report
            validation_errors = validate_report(report_html, handover, all_verdicts)
            if validation_errors:
                logger.warning(
                    "Report validation: %d issues: %s",
                    len(validation_errors),
                    "; ".join(validation_errors[:5]),
                )

            # Host the report
            report_url = ""
            hosting = runner.services.get("hosting")
            if hosting:
                report_url = await hosting.push_qa(
                    feature.id, "implementation-report",
                    report_html, "Implementation Report",
                )
                logger.info("Implementation report hosted at %s", report_url)

            # Store as artifact
            await runner.artifacts.put(
                "implementation-report", report_html, feature=feature
            )

            # Host enhancement backlog as separate artifact
            backlog_url = ""
            backlog_json = await runner.artifacts.get(
                "enhancement-backlog", feature=feature,
            )
            if backlog_json:
                try:
                    backlog = EnhancementBacklog.model_validate_json(backlog_json)
                except Exception:
                    backlog = EnhancementBacklog()
                if backlog.items:
                    backlog_html = _render_enhancement_backlog_html(
                        backlog, feature.name,
                    )
                    if hosting:
                        backlog_url = await hosting.push_qa(
                            feature.id, "enhancement-backlog",
                            backlog_html, "Enhancement Backlog",
                        )
                    await runner.artifacts.put(
                        "enhancement-backlog-report", backlog_html,
                        feature=feature,
                    )

            # Notify user via Slack with report link
            notification = "All quality gates passed. Implementation complete."
            if report_url:
                notification = (
                    f"All quality gates passed. Implementation complete.\n\n"
                    f"**[View Implementation Report]({report_url})**\n\n"
                    f"The report contains journey evidence, gate verdicts, "
                    f"bug fix history, and artifact references."
                )
            if backlog_url:
                notification += (
                    f"\n\n**[View Enhancement Backlog]({backlog_url})** "
                    f"({len(backlog.items)} items deferred)"
                )
            await runner.run(
                Respond(
                    responder=user,
                    prompt=notification,
                ),
                feature,
                phase_name=self.name,
            )

            return state


async def _push_clones_to_source(
    runner: WorkflowRunner, feature: Feature,
) -> None:
    """Push commits from all cloned repos back to their source repos.

    Each clone has ``origin`` pointing to the source repo on disk.
    We push the feature branch so the source repo has all the changes.
    """
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return

    feature_root = Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
    if not feature_root.exists():
        return

    for git_dir in feature_root.rglob(".git"):
        if not git_dir.is_dir():
            continue  # Skip worktree .git files (shouldn't exist with clones)
        repo_dir = git_dir.parent
        if repo_dir == feature_root:
            continue

        try:
            branch = await _run_git(repo_dir, "branch", "--show-current")
            if not branch:
                continue
            # Check if there are commits to push
            status = await _run_git(repo_dir, "status", "--porcelain")
            if status:
                # Uncommitted changes — commit them first
                await _run_git(repo_dir, "add", "-A")
                await _run_git(repo_dir, "commit", "-m", "feat: final uncommitted changes")

            await _run_git(repo_dir, "push", "origin", branch)
            rel = repo_dir.relative_to(feature_root)
            logger.info("Pushed %s (branch: %s) to source", rel, branch)
        except Exception as e:
            rel = repo_dir.relative_to(feature_root)
            logger.warning("Failed to push %s: %s", rel, e)


# ── DAG execution ────────────────────────────────────────────────────────────


def _build_task_prompt(task: ImplementationTask, *, repo_prefix: str = "") -> str:
    """Construct a rich prompt from an ImplementationTask's structured fields.

    When *repo_prefix* is set, file_scope paths are stripped of the prefix
    so they're relative to the repo root (matching the agent's cwd).
    """
    parts: list[str] = [
        f"# {task.name}\n\n"
        f"**Task ID:** `{task.id}` — use this exact value for `task_id` in your output.\n\n"
        f"{task.description}"
    ]

    # ── Workspace directive ──────────────────────────────────────────
    if repo_prefix:
        parts.append(
            "## Working Directory\n"
            "All file paths below are relative to your current working directory.\n"
            "Do NOT use absolute paths. Do NOT navigate outside your working directory.\n"
            "Your cwd is the root of the repository you're working in."
        )

    # ── File Scope ────────────────────────────────────────────────────
    if task.file_scope:
        lines = []
        for fs in task.file_scope:
            path = fs.path
            if repo_prefix and path.startswith(repo_prefix):
                path = path[len(repo_prefix):]
                if path.startswith("/"):
                    path = path[1:]
            lines.append(f"- [{fs.action.upper()}] `{path}`")
        parts.append("## File Scope\n" + "\n".join(lines))
    elif task.files:
        lines = []
        for f in task.files:
            path = f
            if repo_prefix and path.startswith(repo_prefix):
                path = path[len(repo_prefix):]
                if path.startswith("/"):
                    path = path[1:]
            lines.append(f"- `{path}`")
        parts.append("## File Scope\n" + "\n".join(lines))

    # ── Acceptance Criteria ───────────────────────────────────────────
    if task.acceptance_criteria:
        ac_lines: list[str] = []
        for ac in task.acceptance_criteria:
            ac_lines.append(f"- {ac.description}")
            if ac.not_criteria:
                ac_lines.append(f"  - **NOT:** {ac.not_criteria}")
        parts.append("## Acceptance Criteria\n" + "\n".join(ac_lines))

    # ── Counterexamples ──────────────────────────────────────────────
    if task.counterexamples:
        parts.append(
            "## Counterexamples (Do NOT)\n"
            + "\n".join(f"- {ce}" for ce in task.counterexamples)
        )

    # ── Security Concerns ────────────────────────────────────────────
    if task.security_concerns:
        parts.append(
            "## Security Concerns\n"
            + "\n".join(f"- {sc}" for sc in task.security_concerns)
        )

    # ── data-testid Assignments ──────────────────────────────────────
    if task.testid_assignments:
        parts.append(
            "## data-testid Assignments\n"
            + "\n".join(f"- `{tid}`" for tid in task.testid_assignments)
        )

    # ── Reference Material ──────────────────────────────────────────
    if task.reference_material:
        ref_lines = []
        for ref in task.reference_material:
            ref_lines.append(f"### {ref.source}\n{ref.content}")
        parts.append("## Reference Material\n\n" + "\n\n".join(ref_lines))

    # ── Traceability ─────────────────────────────────────────────────
    trace_lines: list[str] = []
    if task.requirement_ids:
        trace_lines.append(f"Requirements: {', '.join(task.requirement_ids)}")
    if task.step_ids:
        trace_lines.append(f"Plan steps: {', '.join(task.step_ids)}")
    if task.journey_ids:
        trace_lines.append(f"Journeys: {', '.join(task.journey_ids)}")
    if trace_lines:
        parts.append("## Traceability\n" + "\n".join(trace_lines))

    return "\n\n".join(parts)


async def _implement_dag(
    runner: WorkflowRunner, feature: Feature, dag: ImplementationDAG
) -> tuple[str, str, HandoverDoc]:
    """Execute the full DAG with per-group verification, checkpointing, and
    handover tracking.

    **Checkpointing:**
    - ``dag-task:{task_id}`` — per-task result (survives mid-group crash)
    - ``dag-group:{group_idx}`` — group completion marker with commit hash
    - On resume, completed groups and tasks are skipped.

    Returns ``(impl_text, failure, handover)``.  *failure* is empty when every
    group passed verification.
    """
    import json as _json

    tasks_by_id = {t.id: t for t in dag.tasks}
    all_results: list[object] = []
    handover = HandoverDoc()

    # ── Resume: reconstruct state from checkpointed groups ──────────
    start_group = 0
    for g_idx in range(len(dag.execution_order)):
        checkpoint_json = await runner.artifacts.get(
            f"dag-group:{g_idx}", feature=feature,
        )
        if not checkpoint_json:
            break
        try:
            data = _json.loads(checkpoint_json)
        except (ValueError, TypeError):
            break
        for r_data in data.get("results", []):
            try:
                result = ImplementationResult.model_validate(r_data)
                all_results.append(result)
                handover.record_success(result)
            except Exception:
                pass
        start_group = g_idx + 1
        logger.info(
            "Group %d already complete (commit %s) — skipping",
            g_idx, data.get("commit_hash", "?"),
        )

    # ── Execute remaining groups ────────────────────────────────────
    for group_idx, group in enumerate(dag.execution_order):
        if group_idx < start_group:
            continue

        group_tasks = [tasks_by_id[tid] for tid in group]

        # Ensure worktrees exist for all repos this group touches
        await _ensure_task_worktrees(runner, feature, group_tasks)

        # Adversarial runtime alternation
        impl_runtime = "primary" if group_idx % 2 == 0 else "secondary"
        review_runtime = "secondary" if group_idx % 2 == 0 else "primary"
        logger.info(
            "Group %d: implement=%s, review=%s",
            group_idx, impl_runtime, review_runtime,
        )

        # Build prompts with handover context from prior groups
        handover_context = ""
        if handover.completed or handover.failed_attempts:
            handover.compress()
            handover_context = f"\n\n## Handover — Prior Work\n\n{to_markdown(handover)}"

        # ── Per-task resume: check which tasks already completed ─────
        pending_tasks: list[ImplementationTask] = []
        completed_results: list[ImplementationResult] = []
        for tid in group:
            task_marker = await runner.artifacts.get(
                f"dag-task:{tid}", feature=feature,
            )
            if task_marker:
                try:
                    completed_results.append(
                        ImplementationResult.model_validate_json(task_marker),
                    )
                    logger.info("Task %s already complete — skipping", tid)
                    continue
                except Exception:
                    pass
            pending_tasks.append(tasks_by_id[tid])

        # ── Resolve worktree paths for each task ────────────────────
        workspace_mgr = runner.services.get("workspace_manager")
        feature_root = (
            Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
            if workspace_mgr
            else None
        )

        # ── Dispatch pending tasks with retry on crash ──────────────
        TASK_MAX_RETRIES = 5
        TASK_WARN_AT = 3  # Send Slack notification at this attempt
        new_results: list[object] = []
        if pending_tasks:

            async def _run_task(task_idx: int, t: ImplementationTask) -> ImplementationResult:
                """Run a single implementation task with retry on crash."""
                repo_prefix = t.repo_path
                ws_path = None
                if feature_root and repo_prefix:
                    worktree = feature_root / repo_prefix
                    if worktree.exists():
                        ws_path = str(worktree)

                for attempt in range(TASK_MAX_RETRIES + 1):
                    try:
                        result = await runner.run(
                            Ask(
                                actor=_make_parallel_actor(
                                    implementer, f"g{group_idx}-t{task_idx}-a{attempt}",
                                    runtime=impl_runtime,
                                    workspace_path=ws_path,
                                ),
                                prompt=_build_task_prompt(
                                    t,
                                    repo_prefix=f"{repo_prefix}/" if repo_prefix else "",
                                ) + handover_context,
                                output_type=ImplementationResult,
                            ),
                            feature,
                            phase_name="implementation",
                        )
                        # Force correct task_id
                        if isinstance(result, ImplementationResult):
                            if result.task_id != t.id:
                                logger.warning(
                                    "Task reported task_id=%r, expected %r — correcting",
                                    result.task_id, t.id,
                                )
                                result.task_id = t.id
                        return result
                    except Exception as e:
                        logger.warning(
                            "Task %s crashed (attempt %d/%d): %s",
                            t.id, attempt + 1, TASK_MAX_RETRIES + 1, e,
                        )
                        if attempt + 1 == TASK_WARN_AT:
                            # Notify user via Slack that a task is struggling
                            try:
                                await runner.run(
                                    Respond(
                                        responder=user,
                                        prompt=(
                                            f"⚠️ Task `{t.id}` ({t.name}) has crashed "
                                            f"{TASK_WARN_AT} times in group {group_idx}.\n"
                                            f"Last error: `{str(e)[:200]}`\n"
                                            f"Retrying ({TASK_MAX_RETRIES - attempt} attempts left)..."
                                        ),
                                    ),
                                    feature,
                                    phase_name="implementation",
                                )
                            except Exception:
                                pass  # Don't let notification failure block retries
                        if attempt >= TASK_MAX_RETRIES:
                            logger.error(
                                "Task %s failed after %d attempts: %s",
                                t.id, TASK_MAX_RETRIES + 1, e,
                            )
                            return ImplementationResult(
                                task_id=t.id,
                                summary=f"FAILED after {TASK_MAX_RETRIES + 1} attempts: {e}",
                            )
                # Unreachable but satisfies type checker
                return ImplementationResult(task_id=t.id, summary="FAILED")

            # Dispatch all tasks in parallel with individual error handling
            gathered = await _asyncio.gather(
                *[_run_task(i, t) for i, t in enumerate(pending_tasks)],
            )
            new_results = list(gathered)

            # Save per-task markers
            for r in new_results:
                if isinstance(r, ImplementationResult) and r.task_id:
                    await runner.artifacts.put(
                        f"dag-task:{r.task_id}",
                        r.model_dump_json(),
                        feature=feature,
                    )

            # Commit after implementation so work is never left uncommitted
            task_ids = [r.task_id for r in new_results if isinstance(r, ImplementationResult) and r.task_id]
            await _commit_repos(
                runner, feature,
                f"feat: group {group_idx} impl — {', '.join(task_ids[:3])}"
                + (f" (+{len(task_ids) - 3} more)" if len(task_ids) > 3 else ""),
            )

        results = list(completed_results) + list(new_results)
        all_results.extend(new_results)  # Don't double-count resumed results

        # ── Verify: confirm claimed work + basic correctness ─────────
        group_files = _collect_files(results)
        verdict = await _verify(
            runner, feature, results, group_files, group_tasks,
            runtime=review_runtime,
        )
        await runner.artifacts.put(
            f"dag-verify:g{group_idx}:initial",
            to_str(verdict),
            feature=feature,
        )

        # Ledger dedup + severity partition for initial verify
        if isinstance(verdict, Verdict):
            ledger = await _load_ledger(runner, feature)
            verdict, _suppressed = _dedup_findings(verdict, ledger, "verify")
            if _suppressed:
                logger.info("Suppressed %d duplicate findings from verify (group %d)", len(_suppressed), group_idx)
            verdict, _enhancements = _partition_verdict(verdict, "verify", f"group-{group_idx}")
            await _append_enhancements(runner, feature, _enhancements)
            ledger = _update_ledger(ledger, verdict, "verify", 0)
            await _save_ledger(runner, feature, ledger)

        for retry in range(VERIFY_RETRIES):
            if _is_approved(verdict):
                break

            feedback = _format_feedback("Verify", verdict)

            # Determine the primary repo worktree for the fix agent
            # Use the most common repo_path across this group's tasks
            fix_ws_path = None
            if feature_root:
                repo_counts: dict[str, int] = {}
                for t in group_tasks:
                    if t.repo_path:
                        repo_counts[t.repo_path] = repo_counts.get(t.repo_path, 0) + 1
                if repo_counts:
                    primary_repo = max(repo_counts, key=repo_counts.get)
                    worktree = feature_root / primary_repo
                    if worktree.exists():
                        fix_ws_path = str(worktree)

            fix_actor = _make_parallel_actor(
                implementer, f"g{group_idx}-fix-{retry}",
                runtime=impl_runtime,
                workspace_path=fix_ws_path,
            )
            fix_result = await runner.run(
                Ask(
                    actor=fix_actor,
                    prompt=(
                        f"Verification failed (attempt {retry + 1}/{VERIFY_RETRIES}). "
                        f"Read the issues below carefully, then fix them.\n\n"
                        f"{feedback}\n\n"
                        "## Instructions\n"
                        "1. Read each affected file listed above\n"
                        "2. Identify the root cause of each issue\n"
                        "3. Apply targeted fixes — do NOT rewrite files unnecessarily\n"
                        "4. Verify your fix addresses the specific concern/gap described"
                    ),
                    output_type=ImplementationResult,
                ),
                feature,
                phase_name="implementation",
            )
            all_results.append(fix_result)
            if isinstance(fix_result, ImplementationResult):
                await runner.artifacts.put(
                    f"dag-fix:g{group_idx}:retry-{retry}",
                    fix_result.model_dump_json(),
                    feature=feature,
                )
            await _commit_repos(
                runner, feature,
                f"fix: group {group_idx} verify retry {retry + 1}",
            )
            group_files = list(set(group_files + _collect_files([fix_result])))
            verdict = await _verify(
                runner, feature, [*results, fix_result], group_files, group_tasks,
                runtime=review_runtime,
            )
            await runner.artifacts.put(
                f"dag-verify:g{group_idx}:retry-{retry}",
                to_str(verdict),
                feature=feature,
            )

            # Ledger dedup + severity partition for re-verify
            if isinstance(verdict, Verdict):
                ledger = await _load_ledger(runner, feature)
                verdict, _suppressed = _dedup_findings(verdict, ledger, "verify")
                if _suppressed:
                    logger.info("Suppressed %d duplicate findings from verify retry (group %d)", len(_suppressed), group_idx)
                verdict, _enhancements = _partition_verdict(verdict, "verify", f"group-{group_idx}-retry-{retry}")
                await _append_enhancements(runner, feature, _enhancements)
                ledger = _update_ledger(ledger, verdict, "verify", 0)
                await _save_ledger(runner, feature, ledger)

        # ── Record outcomes + checkpoint ─────────────────────────────
        if _is_approved(verdict):
            for r in results:
                if isinstance(r, ImplementationResult):
                    handover.record_success(r)

            # Git commit the group's changes
            commit_hash = await _commit_group(runner, feature, group_idx, group_tasks)

            # Persist group checkpoint
            checkpoint = {
                "group_idx": group_idx,
                "task_ids": [t.id for t in group_tasks],
                "results": [
                    r.model_dump()
                    for r in results
                    if isinstance(r, ImplementationResult)
                ],
                "verdict": "approved",
                "commit_hash": commit_hash,
            }
            await runner.artifacts.put(
                f"dag-group:{group_idx}",
                _json.dumps(checkpoint),
                feature=feature,
            )
            logger.info(
                "Group %d checkpointed (commit %s)", group_idx, commit_hash,
            )
        else:
            # Group failed — record and stop
            for r in results:
                if isinstance(r, ImplementationResult):
                    handover.record_failure(
                        r.task_id, r.summary, _format_feedback("Verify", verdict),
                    )
            remaining = dag.execution_order[group_idx + 1 :]
            remaining_names = [
                tasks_by_id[tid].name for g in remaining for tid in g
            ]
            failure = _format_feedback("Verify", verdict)
            if remaining_names:
                failure += (
                    "\n\nThe DAG was halted. Unexecuted tasks: "
                    + ", ".join(remaining_names)
                )
            impl_text = "\n\n".join(to_str(r) for r in all_results)
            return impl_text, failure, handover

    return "\n\n".join(to_str(r) for r in all_results), "", handover


async def _commit_repos(
    runner: WorkflowRunner,
    feature: Feature,
    msg: str,
) -> str:
    """Commit uncommitted changes in all feature repo clones.

    The repos root (``repos/``) is not a git repo itself — each
    subdirectory is a separate clone. We find repos with uncommitted
    changes and commit in each one.

    Returns a comma-separated list of commit hashes (one per repo).
    """
    repos_root = _get_feature_root(runner, feature)
    if not repos_root:
        logger.warning("_commit_repos: no feature workspace found — skipping")
        return ""

    hashes: list[str] = []

    async def _commit_in_repo(repo_path: Path) -> str | None:
        try:
            proc = await _asyncio.create_subprocess_exec(
                "git", "status", "--porcelain",
                cwd=str(repo_path),
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if not stdout.decode().strip():
                return None  # No changes

            await _run_git(repo_path, "add", "--all", ".")
            await _run_git(repo_path, "commit", "-m", msg)
            commit_hash = await _run_git(repo_path, "rev-parse", "HEAD")
            logger.info("Committed in %s: %s", repo_path.name, commit_hash[:8])
            return commit_hash
        except Exception as e:
            logger.warning("Failed to commit in %s: %s", repo_path, e)
            return None

    # Walk repos root for git repos (may be nested: repos/tools/compose/backend/)
    for dirpath in repos_root.rglob(".git"):
        repo_dir = dirpath.parent
        if repo_dir == repos_root:
            continue
        h = await _commit_in_repo(repo_dir)
        if h:
            hashes.append(h)

    return ",".join(hashes) if hashes else ""


async def _commit_group(
    runner: WorkflowRunner,
    feature: Feature,
    group_idx: int,
    group_tasks: list[ImplementationTask],
) -> str:
    """Commit after a group's verification passes."""
    task_names = [t.name for t in group_tasks[:3]]
    msg = f"feat: group {group_idx} — {', '.join(task_names)}"
    if len(group_tasks) > 3:
        msg += f" (+{len(group_tasks) - 3} more)"
    return await _commit_repos(runner, feature, msg)


async def _verify(
    runner: WorkflowRunner,
    feature: Feature,
    results: list[object],
    files: list[str],
    tasks: list[ImplementationTask] | None = None,
    *,
    runtime: str | None = None,
) -> Verdict:
    """Verify a group's implementation: claimed work exists + basic tests.

    When *runtime* is set, the verifier is routed to that runtime for
    adversarial multi-model review.
    """
    results_summary = "\n\n".join(to_str(r) for r in results)
    file_list = ", ".join(files) if files else "recently changed files"

    # Collect reference material from the tasks being verified so the
    # verifier can check implementation against upstream specs.
    ref_context = ""
    if tasks:
        ref_parts = []
        for t in tasks:
            if t.reference_material:
                for ref in t.reference_material:
                    ref_parts.append(f"**{ref.source}** (task {t.id}):\n{ref.content}")
        if ref_parts:
            ref_context = (
                "\n\n## Upstream Specs (verify implementation against these)\n\n"
                + "\n\n---\n\n".join(ref_parts)
            )

    verifier = _make_parallel_actor(qa_engineer, "verify", runtime=runtime)

    return await runner.run(
        Ask(
            actor=verifier,
            prompt=(
                f"Verify this implementation group:\n\n{results_summary}\n\n"
                "For each result, confirm:\n"
                f"1. All claimed files exist on disk: {file_list}\n"
                "2. Files listed as modified were actually changed\n"
                "3. The changes align with the described summary\n"
                "4. The code compiles, imports correctly, and passes "
                "any existing tests for these files\n"
                "5. Implementation matches the upstream specs in Reference Material"
                f"{ref_context}\n\n"
                "This is a per-group verification, not a full QA pass."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )


# ── RCA → Fix → Re-verify pipeline ──────────────────────────────────────────


def _format_indexed_issues(verdict: Verdict) -> str:
    """Format verdict concerns and gaps with indices for the triage agent."""
    lines: list[str] = []
    for i, c in enumerate(verdict.concerns):
        file_hint = f" (file: {c.file})" if c.file else ""
        lines.append(f"[C{i}] ({c.severity}) {c.description}{file_hint}")
    for i, g in enumerate(verdict.gaps):
        lines.append(f"[G{i}] ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines)


def _extract_group_issues(verdict: Verdict, group: object) -> str:
    """Extract the specific issues for a bug group from the verdict."""
    lines: list[str] = []
    for idx in getattr(group, "issue_indices", []):
        if idx < len(verdict.concerns):
            c = verdict.concerns[idx]
            file_hint = f" (file: {c.file})" if c.file else ""
            lines.append(f"- ({c.severity}) {c.description}{file_hint}")
    for idx in getattr(group, "gap_indices", []):
        if idx < len(verdict.gaps):
            g = verdict.gaps[idx]
            lines.append(f"- ({g.severity}) {g.description} (category: {g.category})")
    return "\n".join(lines) if lines else to_str(verdict)


def _compute_fix_schedule(
    rcas: list[tuple[str, RootCauseAnalysis]],
) -> list[list[str]]:
    """Compute parallel-safe fix rounds using greedy graph coloring.

    Groups whose ``affected_files`` don't overlap can fix in the same round.
    Groups with overlapping files are placed in separate sequential rounds.
    """
    file_sets: dict[str, set[str]] = {
        gid: set(rca.affected_files) for gid, rca in rcas
    }
    remaining = set(file_sets.keys())
    schedule: list[list[str]] = []

    while remaining:
        round_ids: list[str] = []
        round_files: set[str] = set()
        for gid in sorted(remaining):
            if not file_sets[gid] & round_files:
                round_ids.append(gid)
                round_files |= file_sets[gid]
        schedule.append(round_ids)
        remaining -= set(round_ids)

    return schedule


def _format_prior_attempts(prior_attempts: list[BugFixAttempt]) -> str:
    """Format prior attempts as context for RCA/fix agents."""
    if not prior_attempts:
        return ""
    prior_lines = []
    for a in prior_attempts:
        prior_lines.append(
            f"### Attempt {a.attempt_number} ({a.bug_id})\n"
            f"- **Source:** {a.source_verdict}\n"
            f"- **Group:** {a.group_id or 'single'}\n"
            f"- **Description:** {a.description}\n"
            f"- **Root Cause:** {a.root_cause}\n"
            f"- **Fix Applied:** {a.fix_applied}\n"
            f"- **Files Modified:** {', '.join(a.files_modified)}\n"
            f"- **Result:** {a.re_verify_result}"
        )
    return (
        "\n\n## Prior Fix Attempts (DO NOT REPEAT these approaches)\n\n"
        + "\n\n".join(prior_lines)
    )


def _get_feature_root(runner: WorkflowRunner, feature: Feature) -> Path | None:
    """Resolve the feature worktree root path."""
    workspace_mgr = runner.services.get("workspace_manager")
    if not workspace_mgr:
        return None
    root = Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
    return root if root.exists() else None


def _resolve_fix_workspace(
    feature_root: Path | None,
    affected_files: list[str],
) -> str | None:
    """Find the worktree path for a fix agent based on affected files."""
    if not feature_root or not affected_files:
        return None
    # Find the repo from the first affected file
    for f in affected_files:
        parts = Path(f).parts
        for depth in range(1, min(len(parts), 5)):
            candidate = feature_root / Path(*parts[:depth])
            if (candidate / ".git").exists():
                return str(candidate)
    return None


async def _diagnose_and_fix(
    runner: WorkflowRunner,
    feature: Feature,
    verdict: object,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_attempts: list[BugFixAttempt],
    bug_counter: itertools.count,  # type: ignore[type-arg]
    handover_context: str = "",
) -> list[BugFixAttempt]:
    """Structured failure handling: triage → parallel RCA → fix → re-verify.

    For string verdicts or single-issue verdicts, takes the single-bug path.
    For multi-issue Verdicts, triages by root cause and dispatches in parallel
    where file scopes don't overlap.

    Returns a list of BugFixAttempt records (one per bug group).
    """
    verdict_text = to_str(verdict)
    prior_context = _format_prior_attempts(prior_attempts)
    attempt_number = sum(1 for a in prior_attempts if a.source_verdict == source) + 1

    # Resolve workspace path for RCA git access
    feature_root = _get_feature_root(runner, feature)
    workspace_hint = (
        f"\n\n### Workspace\nFeature repos at: `{feature_root}`\n"
        if feature_root else ""
    )

    # ── Short-circuit: string verdict or ≤1 issue ────────────────────
    use_single_path = True
    if isinstance(verdict, Verdict):
        total_issues = len(verdict.concerns) + len(verdict.gaps)
        if total_issues > 1:
            use_single_path = False

    if use_single_path:
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
        )
        return [attempt]

    # ── Multi-issue path: triage → parallel RCA → fix → re-verify ────
    assert isinstance(verdict, Verdict)

    # 1. Triage: group issues by root cause
    indexed_issues = _format_indexed_issues(verdict)
    triage: BugTriage = await runner.run(
        Ask(
            actor=AgentActor(name="bug-triager", role=_triage_role),
            prompt=(
                f"## Verdict from: {source}\n\n"
                f"### Summary\n{verdict.summary}\n\n"
                f"### Issues (reference by index)\n{indexed_issues}\n\n"
                "Group ALL issues by likely root cause. Every index must appear "
                "in exactly one group. Use issue_indices for [C*] entries and "
                "gap_indices for [G*] entries."
            ),
            output_type=BugTriage,
        ),
        feature,
        phase_name="implementation",
    )

    await runner.artifacts.put(
        f"bug-triage:{source}:attempt-{attempt_number}",
        to_str(triage),
        feature=feature,
    )

    if not triage.groups:
        # Fallback: triage produced no groups — treat as single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
        )
        return [attempt]

    logger.info(
        "Triage produced %d bug groups from %d issues (source: %s)",
        len(triage.groups), len(verdict.concerns) + len(verdict.gaps), source,
    )

    # 2. Parallel RCA: one per group (read-only, always safe in parallel)
    rca_tasks = [
        Ask(
            actor=_make_parallel_actor(root_cause_analyst, f"rca-{group.group_id}"),
            prompt=(
                f"## Bug Group: {group.group_id}\n\n"
                f"### Likely Root Cause (from triage)\n{group.likely_root_cause}\n\n"
                f"### Issues in this group\n{_extract_group_issues(verdict, group)}\n\n"
                f"### Full Verdict Summary\n{verdict.summary}\n\n"
                "Investigate the root cause of these specific issues. Read the "
                "relevant code, trace the data flow, and identify the exact "
                "point of failure. Propose a conceptual fix approach — do NOT "
                "implement anything."
                f"{prior_context}{workspace_hint}"
            ),
            output_type=RootCauseAnalysis,
        )
        for group in triage.groups
    ]

    if len(rca_tasks) == 1:
        rca_results = [await runner.run(rca_tasks[0], feature, phase_name="implementation")]
    else:
        rca_results = await runner.parallel(rca_tasks, feature)

    # Build group_id → RCA mapping
    group_rcas: list[tuple[str, RootCauseAnalysis]] = []
    for group, rca_result in zip(triage.groups, rca_results):
        if isinstance(rca_result, RootCauseAnalysis):
            group_rcas.append((group.group_id, rca_result))
            await runner.artifacts.put(
                f"bug-rca:{source}:{group.group_id}:attempt-{attempt_number}",
                to_str(rca_result),
                feature=feature,
            )

    if not group_rcas:
        # All RCAs failed — fallback to single bug
        attempt = await _single_rca_fix_verify(
            runner, feature, verdict_text, source,
            original_reviewer, fixer, prior_context,
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            attempt_number=attempt_number,
            handover_context=handover_context,
        )
        return [attempt]

    # Build lookup dicts early (needed for contradiction handling)
    group_by_id = {g.group_id: g for g in triage.groups}

    # ── Contradiction handling ──────────────────────────────────────
    contradiction_groups = [
        (gid, rca) for gid, rca in group_rcas
        if rca.confidence == "contradiction"
    ]
    fixable_groups = [
        (gid, rca) for gid, rca in group_rcas
        if rca.confidence != "contradiction"
    ]

    contradiction_results: list[BugFixAttempt] = []
    if contradiction_groups:
        logger.warning(
            "%d of %d bug groups are spec contradictions — escalating",
            len(contradiction_groups), len(group_rcas),
        )
        for gid, rca in contradiction_groups:
            group = group_by_id[gid]
            resolution = await _escalate_contradiction(
                runner, feature, "implementation", source, group, rca,
            )
            # User resolved it — add to fixable with their direction
            resolved_rca = rca.model_copy(update={
                "proposed_approach": resolution,
                "confidence": "high",
            })
            fixable_groups.append((gid, resolved_rca))
            contradiction_results.append(BugFixAttempt(
                bug_id=f"{source.upper()}-CONTRADICTION-{gid}",
                group_id=gid,
                source_verdict=source,
                description=rca.hypothesis,
                root_cause=rca.contradiction_detail or rca.hypothesis,
                fix_applied=f"User decision: {resolution[:200]}",
                re_verify_result="RESOLVED",
                attempt_number=attempt_number,
            ))

    if not fixable_groups:
        return contradiction_results

    group_rcas = fixable_groups

    # 3. File-overlap scheduling
    schedule = _compute_fix_schedule(group_rcas)
    logger.info(
        "Fix schedule: %d rounds for %d groups",
        len(schedule), len(group_rcas),
    )

    # Build lookup dicts
    rca_by_group = dict(group_rcas)

    # 3b. Store verbose dispatch artifact
    dispatch_record = {
        "source": source,
        "attempt_number": attempt_number,
        "total_issues": len(verdict.concerns) + len(verdict.gaps),
        "groups": [
            {
                "group_id": g.group_id,
                "likely_root_cause": g.likely_root_cause,
                "severity": g.severity,
                "affected_files_hint": g.affected_files_hint,
                "issue_count": len(g.issue_indices) + len(g.gap_indices),
                "rca": {
                    "hypothesis": rca_by_group[g.group_id].hypothesis,
                    "evidence": rca_by_group[g.group_id].evidence,
                    "affected_files": rca_by_group[g.group_id].affected_files,
                    "proposed_approach": rca_by_group[g.group_id].proposed_approach,
                    "confidence": rca_by_group[g.group_id].confidence,
                } if g.group_id in rca_by_group else None,
            }
            for g in triage.groups
        ],
        "schedule": [
            {"round": i, "group_ids": ids}
            for i, ids in enumerate(schedule)
        ],
        "total_rounds": len(schedule),
    }
    await runner.artifacts.put(
        f"bug-dispatch:{source}:attempt-{attempt_number}",
        json.dumps(dispatch_record),
        feature=feature,
    )

    # 4. Fix dispatch: parallel within each round, sequential between rounds
    feature_root = _get_feature_root(runner, feature)
    fix_results: dict[str, ImplementationResult] = {}

    for round_idx, round_ids in enumerate(schedule):
        fix_tasks = []
        for gid in round_ids:
            rca = rca_by_group[gid]
            ws_path = _resolve_fix_workspace(feature_root, rca.affected_files)
            fix_tasks.append(Ask(
                actor=_make_parallel_actor(
                    fixer, f"fix-{gid}",
                    workspace_path=ws_path,
                ),
                prompt=(
                    f"## Bug Fix: group {gid}\n\n"
                    f"### Root Cause Analysis\n\n"
                    f"**Hypothesis:** {rca.hypothesis}\n\n"
                    f"**Evidence:**\n"
                    + "\n".join(f"- {e}" for e in rca.evidence)
                    + f"\n\n**Affected Files:**\n"
                    + "\n".join(f"- `{f}`" for f in rca.affected_files)
                    + f"\n\n**Proposed Approach:** {rca.proposed_approach}\n\n"
                    f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                    "## Instructions\n"
                    "1. Read each affected file listed above\n"
                    "2. Apply the fix described in the RCA — be precise\n"
                    "3. Fix only what the root cause analysis identified\n"
                    "4. Report all files modified"
                    f"{prior_context}"
                ),
                output_type=ImplementationResult,
            ))

        if len(fix_tasks) == 1:
            results = [await runner.run(fix_tasks[0], feature, phase_name="implementation")]
        else:
            results = await runner.parallel(fix_tasks, feature)

        for gid, result in zip(round_ids, results):
            if isinstance(result, ImplementationResult):
                fix_results[gid] = result

        # Commit fixes from this round before re-verification
        fixed_ids = [gid for gid in round_ids if gid in fix_results]
        if fixed_ids:
            await _commit_repos(
                runner, feature,
                f"fix: round {round_idx} — {', '.join(fixed_ids)}",
            )

    # 5. Parallel re-verify: one per group (read-only, always safe)
    verify_tasks = [
        Ask(
            actor=_make_parallel_actor(original_reviewer, f"reverify-{gid}"),
            prompt=(
                f"## Re-verification: group {gid}\n\n"
                f"A fix was applied for the following issues.\n\n"
                f"### Issues\n{_extract_group_issues(verdict, group_by_id[gid])}\n\n"
                f"### Root Cause\n{rca_by_group[gid].hypothesis}\n\n"
                f"### Fix Applied\n{fix_results[gid].summary}\n\n"
                f"### Files Modified\n"
                + "\n".join(
                    f"- `{f}`"
                    for f in (fix_results[gid].files_created + fix_results[gid].files_modified)
                )
                + "\n\nRe-verify that the issues in this group are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code."
            ),
            output_type=Verdict,
        )
        for gid in fix_results
    ]

    if len(verify_tasks) == 1:
        verify_results = [await runner.run(verify_tasks[0], feature, phase_name="implementation")]
    else:
        verify_results = await runner.parallel(verify_tasks, feature)

    # Persist per-group re-verify verdicts
    for gid, rv in zip(fix_results.keys(), verify_results):
        await runner.artifacts.put(
            f"bug-reverify:{source}:{gid}:attempt-{attempt_number}",
            to_str(rv),
            feature=feature,
        )

    # 6. Regression test on all modified files from passed groups
    passed_gids = [
        gid for gid, rv in zip(fix_results.keys(), verify_results) if _is_approved(rv)
    ]
    regression_failed_gids: set[str] = set()
    if passed_gids:
        all_modified = []
        for gid in passed_gids:
            fix = fix_results[gid]
            all_modified.extend(fix.files_created + fix.files_modified)
        all_modified = sorted(set(all_modified))
        if all_modified:
            regression_verdict = await _run_regression(
                runner, feature, all_modified, handover_context=handover_context,
            )
            if regression_verdict is not None:
                await runner.artifacts.put(
                    f"bug-regression:{source}:attempt-{attempt_number}",
                    to_str(regression_verdict),
                    feature=feature,
                )
                if not _is_approved(regression_verdict):
                    logger.warning("Regression found after multi-group fixes")
                    # Mark all passed groups as failed due to regression
                    regression_failed_gids = set(passed_gids)

    # 7. Collect BugFixAttempt records
    attempts: list[BugFixAttempt] = []
    for gid, re_verdict in zip(fix_results.keys(), verify_results):
        group = group_by_id[gid]
        fix = fix_results[gid]
        passed = _is_approved(re_verdict) and gid not in regression_failed_gids

        description = group.likely_root_cause
        if passed:
            logger.info("Bug group %s fixed: %s", gid, description[:80])
        else:
            logger.warning("Bug group %s re-verify FAILED: %s", gid, description[:80])

        attempts.append(BugFixAttempt(
            bug_id=f"{source.upper().replace(' ', '-')}-FAIL-{next(bug_counter)}",
            group_id=gid,
            source_verdict=source,
            description=description,
            root_cause=rca_by_group[gid].hypothesis,
            fix_applied=fix.summary,
            files_modified=fix.files_created + fix.files_modified,
            re_verify_result="PASS" if passed else "FAIL",
            attempt_number=attempt_number,
        ))

    return contradiction_results + attempts


async def _single_rca_fix_verify(
    runner: WorkflowRunner,
    feature: Feature,
    verdict_text: str,
    source: str,
    original_reviewer: AgentActor,
    fixer: AgentActor,
    prior_context: str,
    bug_id: str,
    attempt_number: int,
    handover_context: str = "",
) -> BugFixAttempt:
    """Single-bug RCA → fix → re-verify (no triage needed)."""
    # 1. Root Cause Analysis
    rca: RootCauseAnalysis = await runner.run(
        Ask(
            actor=root_cause_analyst,
            prompt=(
                f"## Bug Report: {bug_id}\n\n"
                f"### Failure Source: {source}\n\n"
                f"### Verdict\n\n{verdict_text}\n\n"
                "Investigate the root cause of this failure. Read the relevant "
                "code, trace the data flow, and identify the exact point of failure. "
                "Propose a conceptual fix approach — do NOT implement anything."
                f"{prior_context}"
            ),
            output_type=RootCauseAnalysis,
        ),
        feature,
        phase_name="implementation",
    )
    await runner.artifacts.put(
        f"bug-rca:{source}:{bug_id}",
        to_str(rca),
        feature=feature,
    )

    # 2. Fix via implementer (with workspace_path for correct cwd)
    feature_root = _get_feature_root(runner, feature)
    ws_path = _resolve_fix_workspace(feature_root, rca.affected_files)

    fix_actor = _make_parallel_actor(
        fixer, f"fix-{bug_id}",
        workspace_path=ws_path,
    )
    fix_result: ImplementationResult = await runner.run(
        Ask(
            actor=fix_actor,
            prompt=(
                f"## Bug Fix: {bug_id}\n\n"
                f"### Root Cause Analysis\n\n"
                f"**Hypothesis:** {rca.hypothesis}\n\n"
                f"**Evidence:**\n"
                + "\n".join(f"- {e}" for e in rca.evidence)
                + f"\n\n**Affected Files:**\n"
                + "\n".join(f"- `{f}`" for f in rca.affected_files)
                + f"\n\n**Proposed Approach:** {rca.proposed_approach}\n\n"
                f"### Original Verdict\n\n{verdict_text}\n\n"
                "## Instructions\n"
                "1. Read each affected file listed above\n"
                "2. Apply the fix described in the RCA — be precise\n"
                "3. Fix only what the root cause analysis identified\n"
                "4. Report all files modified"
                f"{prior_context}"
            ),
            output_type=ImplementationResult,
        ),
        feature,
        phase_name="implementation",
    )

    # Commit fix before re-verification
    await _commit_repos(runner, feature, f"fix: {bug_id}")

    # 3. Re-verify with the SAME reviewer that found the bug
    re_verdict: Verdict = await runner.run(
        Ask(
            actor=original_reviewer,
            prompt=(
                f"## Re-verification: {bug_id}\n\n"
                f"A fix was applied for the following failure.\n\n"
                f"### Original Verdict\n\n{verdict_text}\n\n"
                f"### Root Cause\n\n{rca.hypothesis}\n\n"
                f"### Fix Applied\n\n{fix_result.summary}\n\n"
                f"### Files Modified\n\n"
                + "\n".join(f"- `{f}`" for f in (fix_result.files_created + fix_result.files_modified))
                + "\n\nRe-verify that the original issues are resolved. "
                "Check that the fix does not introduce new problems. "
                "The verdict must be based on the CURRENT state of the code."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )

    await runner.artifacts.put(
        f"bug-reverify:{source}:{bug_id}",
        to_str(re_verdict),
        feature=feature,
    )

    # 4. Regression test on modified files
    passed = _is_approved(re_verdict)
    if passed:
        modified = fix_result.files_created + fix_result.files_modified
        regression_verdict = await _run_regression(
            runner, feature, modified, handover_context=handover_context,
        )
        if regression_verdict is not None:
            await runner.artifacts.put(
                f"bug-regression:{source}:{bug_id}",
                to_str(regression_verdict),
                feature=feature,
            )
            if not _is_approved(regression_verdict):
                logger.warning("Regression found after fix %s", bug_id)
                passed = False

    return BugFixAttempt(
        bug_id=bug_id,
        source_verdict=source,
        description=verdict_text[:200],
        root_cause=rca.hypothesis,
        fix_applied=fix_result.summary,
        files_modified=fix_result.files_created + fix_result.files_modified,
        re_verify_result="PASS" if passed else "FAIL",
        attempt_number=attempt_number,
    )


# ── Escalation and persistence ───────────────────────────────────────────────


async def _escalate_to_user(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    stage: str,
    failed_attempt: BugFixAttempt,
    all_attempts: list[BugFixAttempt],
) -> None:
    """Escalate to the user after MAX_FIX_ATTEMPTS failures from one source."""
    source_attempts = [a for a in all_attempts if a.source_verdict == failed_attempt.source_verdict]
    attempts_text = "\n".join(
        f"- **{a.bug_id}** (group {a.group_id or 'single'}, attempt {a.attempt_number}): "
        f"{a.description[:80]} → root cause: {a.root_cause[:80]} → {a.re_verify_result}"
        for a in source_attempts
    )
    logger.warning(
        "Escalating to user after %d failed attempts at %s stage",
        len(source_attempts), stage,
    )
    await runner.run(
        Gate(
            approver=user,
            prompt=(
                f"## Escalation: {stage} — {len(source_attempts)} fix attempts failed\n\n"
                f"Bug fixes from **{stage}** have failed {len(source_attempts)} times "
                f"(limit: {MAX_FIX_ATTEMPTS}).\n\n"
                f"### Latest Attempt\n"
                f"- **Bug:** {failed_attempt.bug_id} (group {failed_attempt.group_id or 'single'})\n"
                f"- **Root Cause:** {failed_attempt.root_cause}\n"
                f"- **Fix Applied:** {failed_attempt.fix_applied}\n"
                f"- **Files:** {', '.join(failed_attempt.files_modified)}\n"
                f"- **Result:** {failed_attempt.re_verify_result}\n\n"
                f"### All {stage} Attempts\n{attempts_text}\n\n"
                "Review the situation and approve to continue with "
                "the next implementation cycle, or provide guidance."
            ),
        ),
        feature,
        phase_name=phase_name,
    )


async def _escalate_contradiction(
    runner: WorkflowRunner,
    feature: Feature,
    phase_name: str,
    source: str,
    group: BugGroup,
    rca: RootCauseAnalysis,
) -> str:
    """Interview the user about a spec contradiction. Blocks until resolved."""
    result = await runner.run(
        HostedInterview(
            questioner=lead_architect_gate_reviewer,
            responder=user,
            initial_prompt=(
                f"## Specification Contradiction Detected\n\n"
                f"**Source:** {source} verification\n"
                f"**Bug Group:** {group.group_id} — {group.likely_root_cause}\n\n"
                f"### The Contradiction\n{rca.contradiction_detail}\n\n"
                f"### Evidence\n"
                + "\n".join(f"- {e}" for e in rca.evidence)
                + "\n\n"
                f"### Best-Guess Resolution\n{rca.proposed_approach}\n"
                f"*(Based on D-GR-1: most recent authoritative source)*\n\n"
                f"Please confirm the best-guess direction, override with the "
                f"other source, or provide a new decision."
            ),
            output_type=Envelope[ReviewOutcome],
            done=envelope_done,
            artifact_key=f"contradiction:{source}:{group.group_id}",
            artifact_label=f"Contradiction — {group.group_id}",
        ),
        feature,
        phase_name=phase_name,
    )
    if result and result.output:
        outcome = result.output
        if outcome.approved:
            return rca.proposed_approach  # user confirmed best-guess
        # User overrode — extract direction from revision_plan
        if outcome.revision_plan and outcome.revision_plan.requests:
            return outcome.revision_plan.requests[0].description
        return rca.proposed_approach  # fallback
    # Also check the written artifact for the user's response
    discussion = await runner.artifacts.get(
        f"contradiction:{source}:{group.group_id}", feature=feature,
    )
    if discussion:
        return discussion[:500]  # use the discussion text as direction
    return rca.proposed_approach


async def _store_attempts(
    runner: WorkflowRunner,
    feature: Feature,
    attempts: list[BugFixAttempt],
) -> None:
    """Persist bug fix attempts as an artifact for audit trail."""
    text = "\n\n".join(to_str(a) for a in attempts)
    await runner.artifacts.put("bug-fix-attempts", text, feature=feature)


async def _run_regression(
    runner: WorkflowRunner,
    feature: Feature,
    modified_files: list[str],
    handover_context: str = "",
) -> Verdict | None:
    """Run regression tests on files modified by bug fixes.

    Returns None if no files to test, otherwise a Verdict.
    When *handover_context* is provided, also runs an integration-style
    regression on user journeys touching the modified files.
    """
    if not modified_files:
        return None

    file_list = "\n".join(f"- `{f}`" for f in sorted(set(modified_files)))
    regression_verdict: Verdict = await runner.run(
        Ask(
            actor=regression_tester,
            prompt=(
                f"## Regression Check After Bug Fixes\n\n"
                f"The following files were modified during bug fix cycles:\n"
                f"{file_list}\n\n"
                "Run existing tests covering these files. Then probe the "
                "changed surfaces for regressions the test suite doesn't cover. "
                "Focus on downstream consumers and integration points."
            ),
            output_type=Verdict,
        ),
        feature,
        phase_name="implementation",
    )

    if not _is_approved(regression_verdict):
        return regression_verdict

    # ── Integration regression: re-run affected user journeys ─────────
    if handover_context:
        integration_verdict: Verdict = await runner.run(
            Ask(
                actor=integration_tester,
                prompt=(
                    f"## Integration Regression Check\n\n"
                    f"The following files were modified during bug fix cycles:\n"
                    f"{file_list}\n\n"
                    f"## Implementation Handover\n\n{handover_context}\n\n"
                    "Re-execute ONLY the user journeys from the PRD that touch "
                    "the modified files listed above. Use Playwright for UI "
                    "journeys, Bash for API/CLI journeys. This is a targeted "
                    "regression check — verify that existing journeys still "
                    "work correctly after the bug fix changes."
                ),
                output_type=Verdict,
            ),
            feature,
            phase_name="implementation",
        )
        if not _is_approved(integration_verdict):
            return integration_verdict

    return regression_verdict


# ── Helpers ──────────────────────────────────────────────────────────────────


def _count_source_attempts(prior: list[BugFixAttempt], source: str) -> int:
    """Count total failed fix attempts from a given source verdict."""
    return sum(1 for a in prior if a.source_verdict == source and a.re_verify_result != "PASS")


def _collect_files(results: list[object]) -> list[str]:
    """Extract file paths from implementation results."""
    files: list[str] = []
    for r in results:
        if isinstance(r, ImplementationResult):
            files.extend(r.files_created)
            files.extend(r.files_modified)
    return files


def _is_approved(verdict: object) -> bool:
    """Approve if no blocker/major findings exist, regardless of agent opinion."""
    if not isinstance(verdict, Verdict):
        return False
    for c in verdict.concerns:
        if c.severity in BLOCKING_SEVERITIES:
            return False
    for g in verdict.gaps:
        if g.severity in BLOCKING_SEVERITIES:
            return False
    for ch in verdict.checks:
        if ch.result == "FAIL":
            return False
    return True


# ── Finding ledger ──────────────────────────────────────────────────────────


async def _load_ledger(
    runner: WorkflowRunner, feature: Feature,
) -> FindingLedger:
    """Load the finding ledger from the artifact store."""
    raw = await runner.artifacts.get("finding-ledger", feature=feature)
    if raw:
        try:
            return FindingLedger.model_validate_json(raw)
        except Exception:
            logger.warning("Failed to parse finding ledger — starting fresh")
    return FindingLedger()


async def _save_ledger(
    runner: WorkflowRunner, feature: Feature, ledger: FindingLedger,
) -> None:
    """Save the finding ledger to the artifact store."""
    await runner.artifacts.put(
        "finding-ledger", ledger.model_dump_json(), feature=feature,
    )


def _text_overlap(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    words_a = set(a.lower().split())
    words_b = set(b.lower().split())
    if not words_a or not words_b:
        return 0.0
    return len(words_a & words_b) / len(words_a | words_b)


def _dedup_findings(
    verdict: Verdict, ledger: FindingLedger, source: str,
) -> tuple[Verdict, list[FindingRecord]]:
    """Remove findings that match resolved ledger entries (unchanged files).

    Returns (filtered_verdict, list_of_suppressed_records).
    """
    resolved = [
        f for f in ledger.findings
        if f.status == "resolved" and f.source == source
    ]
    if not resolved:
        return verdict, []

    new_concerns = []
    suppressed: list[FindingRecord] = []
    for c in verdict.concerns:
        is_dup = False
        for r in resolved:
            if _text_overlap(c.description, r.description) > 0.5:
                # Same finding — only suppress if the file hasn't changed
                if c.file and c.file == r.file:
                    is_dup = True
                    suppressed.append(r)
                    break
        if not is_dup:
            new_concerns.append(c)

    new_gaps = []
    for g in verdict.gaps:
        is_dup = False
        for r in resolved:
            if _text_overlap(g.description, r.description) > 0.5:
                is_dup = True
                suppressed.append(r)
                break
        if not is_dup:
            new_gaps.append(g)

    filtered = verdict.model_copy(update={
        "concerns": new_concerns,
        "gaps": new_gaps,
    })
    return filtered, suppressed


def _update_ledger(
    ledger: FindingLedger, verdict: Verdict, source: str, cycle: int,
) -> FindingLedger:
    """Add new findings from a verdict, mark resolved ones.

    Findings from the same source that appeared in prior cycles but are
    absent from the current verdict are marked ``resolved``.
    """
    # Collect current verdict descriptions for comparison
    current_descs = {c.description for c in verdict.concerns}
    current_descs |= {g.description for g in verdict.gaps}

    # Mark previously-open findings from this source as resolved
    # if they no longer appear in the current verdict
    for f in ledger.findings:
        if f.source == source and f.status == "open":
            if not any(
                _text_overlap(f.description, d) > 0.5 for d in current_descs
            ):
                f.status = "resolved"
                f.cycle_resolved = cycle

    existing_descs = {f.description for f in ledger.findings}
    next_id = len(ledger.findings) + 1

    # Add new findings
    for c in verdict.concerns:
        if c.description not in existing_descs:
            ledger.findings.append(FindingRecord(
                id=f"F-{next_id:03d}",
                source=source,
                description=c.description,
                file=c.file,
                line=c.line,
                severity=c.severity,
                status="open",
                cycle_introduced=cycle,
            ))
            next_id += 1

    for g in verdict.gaps:
        if g.description not in existing_descs:
            ledger.findings.append(FindingRecord(
                id=f"F-{next_id:03d}",
                source=source,
                description=g.description,
                severity=g.severity,
                category=g.category,
                status="open",
                cycle_introduced=cycle,
            ))
            next_id += 1

    ledger.cycle = cycle
    return ledger


# ── Enhancement backlog ─────────────────────────────────────────────────────


def _partition_verdict(
    verdict: Verdict, source: str, task_context: str = "",
) -> tuple[Verdict, list[EnhancementItem]]:
    """Split a verdict into blocking-only and non-blocking enhancement items."""
    blocking_concerns = [
        c for c in verdict.concerns if c.severity in BLOCKING_SEVERITIES
    ]
    non_blocking_concerns = [
        c for c in verdict.concerns if c.severity not in BLOCKING_SEVERITIES
    ]
    blocking_gaps = [
        g for g in verdict.gaps if g.severity in BLOCKING_SEVERITIES
    ]
    non_blocking_gaps = [
        g for g in verdict.gaps if g.severity not in BLOCKING_SEVERITIES
    ]

    blocking_verdict = verdict.model_copy(update={
        "concerns": blocking_concerns,
        "gaps": blocking_gaps,
    })

    enhancements: list[EnhancementItem] = []
    for c in non_blocking_concerns:
        enhancements.append(EnhancementItem(
            source=source, severity=c.severity,
            description=c.description, file=c.file, line=c.line,
            task_context=task_context,
        ))
    for g in non_blocking_gaps:
        enhancements.append(EnhancementItem(
            source=source, severity=g.severity,
            description=g.description, category=g.category,
            task_context=task_context,
        ))
    for s in verdict.suggestions:
        enhancements.append(EnhancementItem(
            source=source, severity="nit",
            description=s, task_context=task_context,
        ))

    return blocking_verdict, enhancements


async def _append_enhancements(
    runner: WorkflowRunner, feature: Feature,
    items: list[EnhancementItem],
) -> None:
    """Append non-blocking findings to the feature's enhancement backlog."""
    if not items:
        return
    raw = await runner.artifacts.get("enhancement-backlog", feature=feature)
    if raw:
        try:
            backlog = EnhancementBacklog.model_validate_json(raw)
        except Exception:
            backlog = EnhancementBacklog()
    else:
        backlog = EnhancementBacklog()

    backlog.items.extend(items)
    await runner.artifacts.put(
        "enhancement-backlog", backlog.model_dump_json(), feature=feature,
    )
    logger.info(
        "Enhancement backlog: +%d items (total: %d)",
        len(items), len(backlog.items),
    )


def _render_enhancement_backlog_html(
    backlog: EnhancementBacklog, feature_name: str,
) -> str:
    """Render the enhancement backlog as a standalone HTML page."""
    from html import escape

    # Group by source
    by_source: dict[str, list[EnhancementItem]] = {}
    for item in backlog.items:
        by_source.setdefault(item.source, []).append(item)

    rows = []
    for source, items in sorted(by_source.items()):
        for item in items:
            sev_class = "minor" if item.severity == "minor" else "nit"
            file_ref = f"<code>{escape(item.file)}</code>" if item.file else ""
            rows.append(
                f"<tr>"
                f"<td>{escape(source)}</td>"
                f'<td><span class="sev-{sev_class}">{escape(item.severity)}</span></td>'
                f"<td>{escape(item.description)}</td>"
                f"<td>{file_ref}</td>"
                f"</tr>"
            )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Enhancement Backlog — {escape(feature_name)}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 2rem; color: #1a1a2e; }}
h1 {{ font-size: 1.5rem; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; font-size: 0.875rem; }}
th {{ background: #f5f5f5; }}
.sev-minor {{ background: #fef3c7; color: #92400e; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; }}
.sev-nit {{ background: #e0e7ff; color: #3730a3; padding: 2px 6px; border-radius: 3px; font-size: 0.75rem; }}
code {{ background: #f3f4f6; padding: 1px 4px; border-radius: 2px; font-size: 0.8rem; }}
</style></head><body>
<h1>Enhancement Backlog — {escape(feature_name)}</h1>
<p>{len(backlog.items)} non-blocking findings deferred from implementation verification.</p>
<table>
<thead><tr><th>Source</th><th>Severity</th><th>Description</th><th>File</th></tr></thead>
<tbody>{"".join(rows)}</tbody>
</table>
</body></html>"""


def _format_feedback(source: str, verdict: object) -> str:
    """Format a Verdict into human-readable markdown for fix agents."""
    if not isinstance(verdict, Verdict):
        return f"## {source} Feedback\n\n{to_str(verdict)}"

    parts = [f"## {source} Feedback\n"]
    parts.append(f"**Status:** {'APPROVED' if verdict.approved else 'FAILED'}")
    parts.append(f"**Summary:** {verdict.summary}\n")

    if verdict.concerns:
        parts.append("### Issues Found\n")
        for i, c in enumerate(verdict.concerns, 1):
            file_ref = f" in `{c.file}`" if c.file else ""
            line_ref = f" (line {c.line})" if c.line else ""
            parts.append(f"{i}. **[{c.severity}]** {c.description}{file_ref}{line_ref}")
        parts.append("")

    if verdict.gaps:
        parts.append("### Gaps\n")
        for i, g in enumerate(verdict.gaps, 1):
            ref = f" (ref: {g.plan_reference})" if g.plan_reference else ""
            parts.append(f"{i}. **[{g.severity}/{g.category}]** {g.description}{ref}")
        parts.append("")

    if verdict.checks:
        failed_checks = [c for c in verdict.checks if c.result == "FAIL"]
        if failed_checks:
            parts.append("### Failed Checks\n")
            for c in failed_checks:
                detail = f": {c.detail}" if c.detail else ""
                parts.append(f"- **FAIL** {c.criterion}{detail}")
            parts.append("")

    # Collect all affected files for easy reference
    affected_files = sorted({c.file for c in verdict.concerns if c.file})
    if affected_files:
        parts.append("### Affected Files\n")
        for f in affected_files:
            parts.append(f"- `{f}`")
        parts.append("")

    return "\n".join(parts)


def _collect_artifact_urls(runner: WorkflowRunner) -> dict[str, str]:
    """Collect hosted artifact URLs from the hosting service."""
    hosting = runner.services.get("hosting")
    if not hosting:
        return {}
    urls: dict[str, str] = {}
    for key in ("prd", "design", "plan", "system-design", "mockup"):
        url = hosting.get_url(key)
        if url:
            urls[key] = url
    return urls


def _collect_screenshots(feature: Feature, runner: WorkflowRunner | None = None) -> list[str]:
    """Collect Playwright screenshot paths from the feature worktree.

    Searches the feature's worktree repos (not the main workspace) for
    screenshots in common Playwright output locations.
    """
    import glob

    # Primary: search the feature's worktree directory
    search_roots: list[str] = []

    workspace_mgr = runner.services.get("workspace_manager") if runner else None
    if workspace_mgr:
        feature_root = Path(workspace_mgr._base) / ".iriai" / "features" / feature.slug / "repos"
        if feature_root.exists():
            search_roots.append(str(feature_root))

    # Fallback: try workspace_path on feature (for CLI mode)
    if not search_roots:
        workspace = getattr(feature, "workspace_path", "") or ""
        if workspace:
            search_roots.append(workspace)

    if not search_roots:
        return []

    patterns_per_root = [
        "**/screenshots/*.png",
        "**/test-results/**/*.png",
        "**/playwright-report/**/*.png",
        "**/*.screenshot.png",
    ]
    paths: list[str] = []
    for root in search_roots:
        for pattern in patterns_per_root:
            paths.extend(glob.glob(f"{root}/{pattern}", recursive=True))
    return sorted(set(paths))
