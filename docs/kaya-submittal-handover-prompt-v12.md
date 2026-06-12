# HANDOVER — SUCCESSOR DRIVER for Kaya Submittal 5b280bb4 (driver-v13… or v12 seat)
# Written 2026-06-12 ~16:2x PDT by driver-fable-20260612-1030 (driver-v11) at
# operator-ordered wind-down. SUPERSEDES v11 (whose §IN-FLIGHT is stale).
# ALSO READ the analyst-staged kickoff at <ws>/.iriai/driver-v12-kickoff.md.
# Trust NOTHING as current — derive live state first; the store wins.

Repo ~/src/iriai/iriai-build-v2 branch fix/cli-resume-command (tip at writing:
35e4d19 + this doc; ALL engine fixes pushed: 0f25d1c codex-config live reload,
1aa7f50 partial-triage marker, 347f97a IRIAI_MCP_DISABLE, cfd85b7 walk
exclusions). Workspace ~/src/kaya/kaya-main; FW = .iriai/features/
submittal-management-...-5b280bb4/repos, tip at writing 71ef5e8e3 (docs-removal;
porcelain 0). Store: psql postgresql://danielzhang@localhost:5431/iriai_build_v2.

## STATE (verify each)
- DAG COMPLETE 14/14, 53/53 (g13 sealed 13:24:26). POST-DAG battery NOT yet
  producing rows: develop41/42 both ground in the BUG-17 walk (fixed cfd85b7);
  develop43 (launched ~16:0x with the fix + IRIAI_MCP_DISABLE=context7 +
  IRIAI_DAG_PARTIAL_ENQUEUE_DRIVER_MARKER=1) DIES WITH MY SESSION — sweep +
  relaunch develop44 (v11 doc's recipe + BOTH new flags). Expect: a ~50-min
  SILENT slow pass (BUG-16 — artifact-lookback before any kill; >60 min
  zero-writes = wedge), then walk log lines, then battery rows; STRICT_VERDICT
  may emit operator queries — answer per runbook §5; evidence pack at
  .iriai/runtime/gate-battery-evidence-pack.md.
- FW commits this seat: 490496ea3, 678e1eb5c, f93e04c57, 7c2610666, 18399b7ba,
  a8e8173d0 (UI batch-1), abf1fb86d (batch-2: amber write plane FIXED + CMP-22
  + F16/F23 anatomy), 71ef5e8e3 (docs/ EXCLUDED — diff vs merge-base must stay
  EMPTY; advisory SQLs live in .iriai/migrations-advisory/).
- DEV DB (compose kayadb org_1, via `docker exec -i docker-db-1 psql -U
  admin_user -d kayadb`): lifecycle seed E2E4-S-001..006 APPLIED (data ceiling
  lifted) + TWO never-migrated schema pieces APPLIED at root (submittal_review_
  decision table = F33's cause; share_links.submittal_sent_package column =
  latent send-flow 500). 3rd model-vs-migration drift instance → the
  schema-existence-preflight ratchet is priority backlog.
- DEMO: :3001/:8001 from /tmp/e2e4-tree at abf1fb86d — SERVERS DIE WITH MY
  SESSION (maybe; nohup'd — verify). Revive via the two one-liners in the
  OPERATOR-ACTIONS 12:2x demo entry; REFRESH to FW tip first; run the
  authenticated probe (dashboard.spec grep "skeleton|row detail navigation",
  PLAYWRIGHT_BASE_URL=http://localhost:3001, creds ~/.iriai-secrets/kaya-e2e/,
  E2E_ORG_ID=1, traces ON) and log probe+SHA in the demo entry. KNOWN: the
  "distinct stage axes" test fails post-batch-2 (locator vs new columns —
  reconcile in the BP-F dashboard rebuild which removes the legend anyway).

## IN-FLIGHT CODEX JOBS AT MY DEATH (verify; prompts all saved — re-dispatch if reports absent)
/tmp/kaya-bugq-backend.{prompt,report} — F33 typed handling (table now exists!
  reconcile its fix with that) + F27 code-vs-seed verdict + F42 KPI count align.
/tmp/kaya-bugq-f5.{prompt,report} — strip KPI tile click/active/↗ per the F5
  ruling (keep error fix).
/tmp/kaya-riders-i.{prompt,report} — grouped rows (operator ruling 15:2x,
  D-U10-01 sanctioned; composes with batch-2's table anatomy).
APPLY DISCIPLINE: review diff → assemble union if overlapping (codex in
  /tmp/kaya-batchN clone; beware `git apply --3way` STAGING the index — verify
  with `git diff HEAD`, generate patches via `git add -A && git diff --cached
  --binary`, or copy files) → host-verify (tsc/eslint/py_compile/vitest via
  `pnpm install --offline --ignore-scripts --filter <pkg>` first) → ONE atomic
  FW commit → demo refresh+probe → FINDINGS.md closures w/ SHA.

## THE QUEUE AFTER THAT (operator-ruled order)
1. Bug batch above. 2. BP FLIGHTS: /tmp/kaya-build-specs/ 12 specs, INDEX.md
order (rename BP-F..BP-Q pending analyst); dispatch parallel codex clones, the
spec IS the brief; Figma crops cited from the MAIN checkout path
(kaya-main/docs/submittal-prd/tdd-html/assets/figma/screens/cropped/ — NOT in
the FW; docs/ is excluded). Two flights if review bandwidth binds; delta-QA
re-grades per refresh. 3. Battery → end-gate e2e (STRICT_GREEN; amber spec OFF
holdout — grants + contracts + seeds all live; FULL traces per the 12:0x P2).
4. Findings-closure SETTLE (checklist Rounds 1-7 + OPF + UI batches current as
of 16:1x). 5. PR: staged at .iriai/runtime/pr-staging/ (push + create BOTH
operator-gated; 23-commit base-divergence flag; docs/-empty hard check).

## CHANNELS + SEATS (sweep EVERY wake, before any work — SLO ≤15 min P0)
ANALYST-DIRECTIVES.md (successor analyst live since 14:50, sentinel armed;
flips current as of 16:2x) · operator-ui-findings/FINDINGS.md (POLISHER owns;
F4 verdict pending; F33-clear re-click requested 16:1x) · OPERATOR-ACTIONS.md
(PENDING: swap ask, launchctl runner [both non-blocking]; everything else
RESOLVED current). Monitors: arm /tmp/kaya_v11_arm_monitors.sh <PID> <LOG> +
channel/ui-findings/query watchers (all DIE WITH MY SESSION); alert logs
/tmp/kaya_v11_alerts.log + _channel_alerts.log.

## SESSION LEDGER (driver-v11, 10:3x→16:2x)
develop38→43; g11/g12/g13 sealed = DAG COMPLETE; 8 FW commits (riders +
batches + docs-removal); 4 engine fixes pushed; lifecycle seed + 3 schema
heals applied; fast-mode tripwire fired→reverted (effort=HIGH proven); 6
partials superseded; BUG-13..17 filed (13/17 fixed); standing demo + findings
loop + visual-QA + build-plan programs live; F1/F2 operator blockers closed
same-hour; 2 SLO misses owned (regime re-adopted).
