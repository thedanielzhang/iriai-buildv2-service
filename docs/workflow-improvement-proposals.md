# Workflow Improvement Proposals — post-incident synthesis (2026-06-10)

**Provenance**: distilled from the Kaya 5b280bb4 run (2026-06-09/10): 13+ plan-review
cycles, 8 process crashes (all guard-caught, zero silent corruption), 10 orchestrator
fixes shipped mid-run, one measured spec-inflation audit, one full seal impasse +
recovery. Each proposal carries its evidence. Written by the analysis session for
the driver to implement IN PARALLEL with driving the current feature.

**How to use this doc (driver)**: treat each item like a ledger entry with a fix
workflow (driver runbook §7): additive/flag-gated, tests, commit+push, load at a
safe boundary. The TIMING column is binding — "mid-feature-safe" items may ship
now at any restart boundary; "post-seal" items must not touch the running
feature's active phases; "next-feature" items are architecture for the next
planning run, not code for this one. Driving the current feature ALWAYS
outranks this backlog; pick items up in idle stretches (waves running, compiles
grinding) the way the seal-stretch fixes were done.

**Unifying principle** (the shape every fix should take): every guard that can
say "no" gets a paired, MECHANICAL recovery path — detection without designed-in
re-dispatch is what converted loud failures into driver heroics (marker surgery,
manual restarts, direct-update lanes) all run long. Wait-for-quota, no-skip
resume, and the freshness gate already follow this shape; P-1 is the largest
remaining violation.

---

## P-1. Gate-rejection → corrective-dispatch wiring  [BLOCKING-class · post-seal for this feature · HIGH effort]

**Evidence**: the seal impasse. resume31 (22:03) and resume32 (22:36, 2026-06-09)
both died on `_assert_gate_requests_are_converging` with identical digest —
seal-gate rejections (GF-148..153) had NO mechanism to become dispatched work.
Recovery required the operator-sanctioned direct-update lane + an 85-item manual
work-list extraction.

**Design sketch**: when `_run_gates` collects `{"approved": false}` verdicts,
convert the structured findings (they already carry GF-ids, targets, demanded
changes — see the gate-review-ledger rows) into a revision-request set and feed
the EXISTING plan-review revision dispatch (`targeted_revision` machinery), then
re-run gates. Bound it: max 2 gate→dispatch→gate rounds before escalating to the
operator (preserving the convergence guard as the backstop, not the only stop).
Flag-gate (`IRIAI_GATE_REJECTION_DISPATCH`, default ON for new features).

**Files**: `workflows/planning/phases/plan_review.py` (`_run_gates`, ~:1845-1911;
the convergence assert ~:1332), `workflows/_common/_helpers.py`
(`_assert_gate_requests_are_converging` ~:1929). The gate-review-ledger artifact
format is the input contract.

**Verify**: unit tests simulating a rejected gate with structured findings →
assert a revision wave dispatches with those findings → second rejection with
unchanged digest still raises. Timing: do NOT load mid-seal for the current
feature (its seal should pass on the direct-updated corpus); ship for
task-planning-onward gates and the next feature.

## P-2. True-convergence invariant for the review loop  [BLOCKING-class · mid-feature-safe · LOW effort]

**Evidence**: cycle-13 "converged" because gate-ledger dedup suppressed findings
as already-resolved while 13 of the cycle-12 revisions had never dispatched
(revision-done stuck at 20/33) — convergence-by-suppression led directly to a
false seal attempt.

**Design sketch**: the review loop may not declare convergence (nor enter
`_run_gates`) while the current/previous cycle's dispatched-revision count
exceeds its revision-done count. Pure-code check against the existing
`revision-done:cycle-N:*` markers vs the dispatch records. Fail-loud with the
counts in the message.

**Files**: `plan_review.py` convergence/loop-exit decision point; markers
already exist. ~30 lines + tests.

## P-3. Driver lock (implement what the runbook specifies)  [coordination · mid-feature-safe · LOW effort]

**Evidence**: the 2026-06-09 dual-driver afternoon (two sessions concurrently
authorized; survived only by ad-hoc answer-file checks) + a stale queued wakeup
re-entering the driving role post-stand-down.

**Design sketch**: exactly runbook §2.1/§3 — `<workspace>/.iriai/driver.lock`
holding `{session_id, taken_at, heartbeat}`; driver tooling refreshes on action;
every wake checks ownership first. This is convention + a small helper script
(or just disciplined Bash in the driver prompt) — no orchestrator code required.
Optionally: the resume CLI warns at startup if the lock is missing/stale.

## P-4. Per-role/per-cycle usage telemetry  [cost · mid-feature-safe · MEDIUM effort]

**Evidence**: every model-strategy decision in this run (economy mode tiering,
Fable gates, the discovery that the `--agent-runtime claude` workaround was
draining the operator's own account) was made by inference from session counts
and job-manifest archaeology, hours after the fact.

**Design sketch**: pool job manifests/results already carry usage blocks
(`modelUsage` in the CLI result JSON — see `claude_pool.py` ~:1147). Aggregate
on job completion into a durable per-(role, model, cycle/phase) ledger (DB table
or JSONL) + a `iriai-build-v2 usage report --feature <id>` CLI. Alert hook:
log loudly when a single invocation's account differs from the pool accounts
(the self-starvation detector).

## P-5. Compile parallelism  [latency · post-seal · MEDIUM effort — design doc EXISTS]

**Evidence**: the seal recompile ran strictly serially (chunk timestamps
2026-06-10 08:21→09:31+), a 1-3h tax paid at every recompile, with 3 pool
members idle. `docs/compile-parallelism-resumability-design.md` already
sketches the approach.

**Design sketch**: per the existing doc — per-family chunk fan-out (chunks
within a family are independent; final bundles remain sequential after their
family). The compile-piece checkpoint/sentinel machinery already makes chunks
idempotent and resumable; the change is dispatching them concurrently
(bounded by pool size) instead of awaiting serially. All existing guards run
unchanged per chunk. Do NOT ship while the current feature's seal stretch is
active; first exercise on a post-seal recompile or the next feature.

## P-6. Dispatcher↔runner protocol-version handshake  [robustness · mid-feature-safe · LOW effort]

**Evidence**: the B-6 impasse — dispatcher shipped new-format manifests, stale
runners rejected them with a misleading error ("requires runtime workspace
binding"), costing an RCA cycle + the `--claude` workaround chain. Self-bounce
(d7a2be5) now keeps runners current, but a mismatch can still occur in the
self-bounce stagger window or if self-bounce is opted out.

**Design sketch**: a `protocol_version` int in the job manifest; the runner
compares against its own and fails the job with an explicit
"runner protocol N < manifest M — runner restart needed" (a distinct,
greppable failure_type the failure router can classify as
infrastructure-retryable). ~20 lines both sides.

## P-7. Test-plan baseline-freshness invariant  [churn reduction · mid-feature-safe · LOW effort]

**Evidence**: stale test plans were the single most re-flagged artifact class
across cycles 9-13 (encoding forbidden behaviors, covering obsolete REQ ranges)
— ultimately requiring operator-approved full regens of 5 of 7.

**Design sketch**: a pure-code end-of-cycle check: each test plan's REQ-id
range ⊇ its sibling PRD's REQ-id range, and zero citations of
superseded-decision ids (the alias-resolution grammar from e96ba2b can
validate). Violation = a finding auto-filed into the next review cycle (not a
crash) so the lockstep cascade picks it up while the lag is one cycle, not five.

## P-8. Generation-layer collapse  [architecture · NEXT-FEATURE ONLY · the big one]

**Evidence (measured, 2026-06-09 audit)**: ~63% of the 361-entry decision ledger
was self-inflicted (39% reconciling the workflow's own 7 parallel doc sets, 14%
churn/duplication, 10% process-meta); ~33% of generated requirement count and
>50% of requirement text had no source basis; one enum (`SharedLinkStatus`)
consumed 15 decisions across 4 phases unwinding a single early wrong call the
source had answered correctly.

**Design sketch (for the next feature's planning configuration)**:
1. ONE strong-model author per bounded context (Fable-class models hold a full
   subfeature corpus coherently — the per-SF parallel author split is solving a
   context limit that no longer exists at this corpus size).
2. Every cross-cutting contract (auth plane, carrier DTOs, read paths, audit
   spine) authored ONCE in an owned contracts document; sibling docs REFERENCE
   by id, never restate. Reviews flag restatement as a defect.
3. Workflow invariants (preservation rules, re-dispatch governance, GF-style
   bars) live in a governance artifact, NEVER minted as product requirements
   or success metrics (the REQ-20/35/36 class).
4. Review-loop budget: expect 3-5 cycles with the P-2 invariant + tripwire
   convergence checks; treat cycle 6+ as a design smell, not normal cost.
Expected effect: the next feature's planning at roughly a third of this one's
cycle count and decision mass, with identical fidelity bars (guards unchanged).

## P-9. Operator-action ergonomics for develop  [ergonomics · ship before develop's first CHK · LOW effort]

**Evidence**: develop will surface ~6 migration-apply checkpoints
(CHK-S1-CORE, CHK-1, CHK-PRIMARY, S3a/S3b tables) as operator asks; the
escalation-channel pattern (OPERATOR-ACTIONS.md) worked well but entries were
hand-authored.

**Design sketch**: when a CHK gate blocks on an unapplied migration, the
workflow (or driver tooling) auto-generates the OPERATOR-ACTIONS entry with the
exact authored-SQL path, the apply command, and the existence-probe
verification query (the plans already specify the probes). Batch hint included
(which other pending CHKs could ride the same session).

---

## Suggested sequencing for the current-feature driver

Idle-stretch order: **P-2 (kills the false-convergence class now, 30 lines) →
P-3 (lock; pure convention) → P-6 (handshake) → P-9 (before develop's first
CHK) → P-4 (telemetry; informs develop's economy tuning) → P-1 + P-5
(post-seal) → P-7 (any cycle boundary) → P-8 (next feature's planning config —
a prompt/configuration change, not code).**

Every item: ledger-file it on pickup, demote only with the runbook's
clean-evidence rule. Nothing here outranks the feature.

---
# Post-incident additions (2026-06-10 evening, analysis session + operator review)
# P-10/P-11/P-12 are NEXT-FEATURE items, peers of P-8; each carries FIDELITY GUARDS
# from an adversarial pass (operator-requested) — the guards are part of the
# proposal, not optional hardening.
#
# FRAMING (operator clarification, binding on all three): the planning phase is
# NOT a verification pass over pre-made decisions. It is an active DECISION-
# MAKING venue — the corpus seeds it, but planning interrogates deeper than
# corpus generation and is EXPECTED to produce new decisions, made by the
# DRIVER agent (escalating to the operator per its standing rules) and recorded
# as first-class decision-ledger entries with citations (the DEC-PR pattern).
# The driver may also directly edit artifacts via the sanctioned direct-update
# lane as part of executing its decisions. What these proposals eliminate is
# RE-CONFIRMATION CEREMONY around already-made decisions and reader-less
# formats — never the making of new ones.

## P-10. Artifact layer modernization  [architecture · NEXT-FEATURE · MEDIUM effort]
**Evidence**: S6 system-design single-line minified HTML caused repeated
find_replace target misses (3 agent retries -> forced FULL_DOCUMENT fallback,
2026-06-10 17:36-17:54); browser-review 404 flagged by reviewers in 3 cycles
(deferred twice = review-attention waste); the hosting/exhibit layer's only
consumer (brainstorm-era human review) no longer exists — fable reviewers read
disk artifacts via context packages every time.
**Design**: markdown-primary for ALL artifacts (no HTML storage format); stable
unique heading IDs as patch anchors; contracts/enums in fenced blocks; anchored
patches (heading-ID + position) replace literal find_replace; human-readable
surfaces only as GENERATED decision briefs at actual decision points (corpus
handoff, DAG gate, seal, develop boundary) — the prestage answer-key kits are
the prototype.
**FIDELITY GUARDS**: briefs are navigational never authoritative — verbatim
critical items + exact ids + file pointers, with completeness reconciliation
(every open finding/decision id present or generation fails; never-truncate-
decisions applies). Anchor-uniqueness validated at authoring; post-apply
verification greps mandatory. HTML->markdown migration is a one-time audited
conversion under the existing completeness/SF-marker guards (minified HTML
carries normative content, e.g. service-map auth strings).

## P-11. Escalation-by-exception gating  [process · NEXT-FEATURE · MEDIUM effort]
**Evidence**: the best run moments were cycle-11/12 reviewers self-separating
"governed by DEC-PR1..10, re-dispatching" from "I need your call on these N new
clusters" (genuinely novel decisions); the worst gate ceremony was formulaic
re-presentation answered by directive riders every cycle. Operator clarification
(2026-06-10): planning interrogates deeper than corpus generation — the
interview's discovery + driver-escalation functions are REAL value; the
per-cycle approval ceremony is not.
**Design**: the decision ledger is planning's LIVING OUTPUT, not a fixed input.
Agents proceed autonomously on items the ledger already governs (citing the
governing id); everything else flows to the DRIVER as the standing decision-
maker via interview/query turns — genuinely novel decisions, ledger additions,
and explicit escalations needing human-grade judgment. New decisions during
planning are expected and first-class (driver decides, records the DEC entry
with citations, dispatches, and may execute via the direct-update lane);
the driver escalates to the operator per its standing rules. Verdict-based
(structured findings) turns replace prose discussion envelopes only for the
re-confirmation ceremony — never for decision-carrying turns.
**FIDELITY GUARDS** (this proposal has the highest misclassification risk —
the convergence-by-suppression incident class): every auto-dispatched item MUST
cite its specific governing decision id — no citable DEC = forced escalation;
anything ADDING to the ledger always escalates; ambiguity fails OPEN to asking;
seal verification samples auto-dispatched items against their cited decisions;
the driver->operator escalation channel is unchanged.

## P-12. Token-efficiency program  [cost · NEXT-FEATURE · MEDIUM effort]
**Evidence**: design gate = 5 full re-reviews of a 533KB artifact to confirm
single fixes; ~33% requirement count / >50% requirement text without source
basis, heavily cross-SF contract restatement (P-8 audit) — corpus inflation
compounds through every package/compile/review; 3 full recompiles of an 800KB+
corpus in one day. Stacked estimate: next feature's planning at ~1/3 the token
spend with equal-or-better fidelity (the cuts are re-reading and re-stating,
not thinking).
**Design**: (1) delta-reviews — review the diff + invariants per cycle;
(2) contracts authored once, referenced by id everywhere (with P-8);
(3) pointer-based selective-read context as the universal default (never inline
full artifacts); (4) verdict-based gate turns (with P-11).
**FIDELITY GUARDS**: delta scope = diff + CITATION BLAST-RADIUS (all sections
referencing changed ids); terminal/pre-seal reviews ALWAYS full-document;
invariant lens battery stays full-document every cycle; reviews keyed to
full-document digests (B-9 discipline) so delta-approved never masquerades as
fully-reviewed — this is the assertion-strength rule's structural enforcement.
Reference-not-restate: the contracts doc ships in EVERY context package +
fail-closed reference-integrity check at compile (e96ba2b alias machinery
generalized). Pointer-context: verdicts must cite line evidence from pointed
files; a verdict citing none of its pointed material is re-asked.
**META-GUARD (all three)**: the seal-stage verification battery is never the
optimization target — efficiency changes ship flag-gated; guards run unchanged.

## P-13. Pre-phase class audits, two modes  [process · STANDING · evidence: 2026-06-10]
**Evidence**: four task-planning blocks were present in code simultaneously
but execution surfaced one per ~80-min cycle (discovery gated by the
execution path); a 3-agent, 13-minute read-audit briefed with the
incident-class taxonomy found the 4th block plus 21 adjacent findings
(6 will-block, incl. one silent-corruption class) before the run did.
**P-13a — WORKFLOW MODE**: full parallel class-audit of unexercised code
paths against the target project's reality. TRIGGER: first run on a new
project; new or significantly refactored subsystem; any phase that has
never executed in the current configuration. Brief auditors with the
accumulated incident-class taxonomy + the project's specifics.
**P-13b — FEATURE MODE (every feature, cheap)**: offline dry-runs of every
fail-closed validator against the actual corpus BEFORE the run reaches
it; content-class pre-sweeps before each gate battery; answer-key
prestaging for operator decision points. DESTINY: per the healing
doctrine ratchet (runbook §16), P-13b checks graduate into built-in
workflow phase-entry steps as they prove deterministic.
**Trigger heuristic**: audit CODE when code meets a new reality; audit
CONTENT when content is new. A fresh feature on a proven workflow is
only the second.
