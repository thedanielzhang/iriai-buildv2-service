# Plan Review Discussion — Cycle 6

**Date:** 2026-03-29
**Reviewer:** Lead Architect
**Scope:** All 7 subfeatures + 10 cross-subfeature edges
**Finding Count:** 304 concerns, 150 gaps
**Result:** 8 decision themes resolved → revision plan dispatched

---

## Decision Log

### D-GR-34: Full SF-2 Plan Rewrite Against Canonical SF-1 PRD

**Context:** The SF-2 (DAG Loader & Runner) plan was built against stale SF-1 plan/design artifacts rather than the canonical SF-1 PRD. This is the exact "stale artifact survival" scenario the D-GR-22 revision was created to prevent. ~95% of the plan is incompatible with the canonical PRD.

**Concrete drift found:**
- Plan's BranchNode uses `switch_function` + `merge_function` — PRD explicitly rejects both
- Plan's BranchNode has no `condition_type`, `condition`, or `paths` — the PRD's only valid routing fields (note: these are further revised by D-GR-35 below)
- Plan's WorkflowConfig includes `stores` and `plugin_instances` at root — PRD forbids
- Plan uses actor type `interaction` instead of `human`
- Plan's PortDefinition lacks `schema_def` — making XOR enforcement impossible
- Plan adds `inputs`, `outputs`, `context_keys`, `context_text` to WorkflowConfig root — 4 unauthorized fields
- `validate()` function completely missing — required by REQ-12
- Expression sandbox uses bare `exec()` instead of AST allowlist + timeout per REQ-16/REQ-21
- ExecutionResult missing `history` and `phase_metrics` fields required by REQ-15
- No stale-field rejection for any of the 14 items in REQ-14

**Decision:** Full SF-2 plan rewrite against the canonical SF-1 PRD, including:
- Rebase all schema entities to PRD contract
- Implement `validate()` as a standalone structural validation function
- Implement expression sandbox per REQ-16/REQ-21 (AST allowlist, timeout, size limits)
- Align ExecutionResult with PRD (add `history`, `phase_metrics`)
- Add stale-field rejection for all 14 items in REQ-14
- BranchNode model per D-GR-35 below

**Affected subfeatures:** SF-2 (primary), SF-3/SF-4/SF-5/SF-6 (downstream consumers)

---

### D-GR-35: BranchNode — D-GR-12 Per-Port Model Is Authoritative

**Context:** Three competing BranchNode models existed across artifacts:
1. SF-1 PRD: `condition_type`/`condition`/`paths` with exclusive single-path routing
2. SF-1 Plan + SF-2 Plan: `switch_function`/`merge_function`
3. SF-6 Design D-GR-12: per-port conditions with non-exclusive fan-out + optional `merge_function` for gather

**Decision:** D-GR-12 per-port model is the single authority. BranchNode semantics:
- **Gather:** Multiple inputs merge via optional `merge_function`
- **Fan-out:** Non-exclusive — each output port carries its own condition; multiple ports can fire if their conditions are met
- **Per-port conditions are expressions only** — no `output_field` mode per port
- **`output_field` is fully removed** from the BranchNode schema everywhere
- **`switch_function` remains rejected** — not a valid field
- **`merge_function` is valid** for multi-input gather

**Artifacts requiring revision:**
- SF-1 PRD: Replace `condition_type`/`condition`/`paths` exclusive-routing model with per-port output conditions and optional `merge_function`
- SF-2 PRD: Update branch execution to evaluate each output port condition independently (non-exclusive). Remove stale-field rejection for `merge_function` (now valid). Keep rejection of `switch_function`.
- SF-6 System Design D-SF6-9: Revise — currently says "exactly one path fires" and "per-port branch predicates are forbidden," which contradicts D-GR-12
- SF-1 Plan: Remove `switch_function`/`merge_function` node-level fields, add per-port condition model (covered by D-GR-34 rewrite)
- SF-2 Plan: Align to per-port model (covered by D-GR-34 rewrite)
- SF-6 Plan: Remove `SwitchFunctionEditor.tsx` creation (still rejected). Keep `OutputPathsEditor`, `PortConditionRow`, `MergeFunctionEditor`.

**Affected subfeatures:** SF-1, SF-2, SF-4, SF-6 (directly); SF-3, SF-5, SF-7 (indirectly)

---

### D-GR-36: Repo Topology — `tools/compose/` Is Canonical

**Context:** Repo path was inconsistent:
- SF-5 PRD REQ-1, decision D-A3: `tools/compose/backend`, `tools/compose/frontend`
- SF-5 Plan summary: `platform/compose/backend`, `platform/compose/frontend`
- SF-6 all artifacts: `tools/compose/`
- SF-7 Plan summary: `platform/compose/`

**Decision:** `tools/compose/` is canonical per SF-5 PRD and D-A3. Tools hub stays at `platform/toolshub/frontend/`. SF-5 plan summary and SF-7 plan summary revised from `platform/compose/` to `tools/compose/`.

**Affected subfeatures:** SF-5, SF-7 (plan summaries revised)

---

### D-GR-37: Reference Checking — Mutation Hooks + Materialized Index

**Context:** Two mutually exclusive approaches existed:
- Option A (PRD design): SF-5 fires post-commit mutation hooks → SF-7 subscribes, parses YAML, maintains `workflow_entity_refs` rows → delete preflight queries index (O(1))
- Option B (SF-7 design D-SF7-2): Parse all user workflows' YAML on every delete attempt (N×parse)

**Decision:** Option A — Mutation hooks + materialized `workflow_entity_refs`. This is the PRD-designed approach (SF-5 REQ-18, D-SF5-R3).

**Rationale:** O(1) delete preflight lookups, scales with workflow count. Stale-index risk manageable with reconciliation job (added to SF-7 plan).

**Revision:** SF-7 design decision D-SF7-2 revised from "YAML parsing on delete (no materialized ref table)" to "subscribe to SF-5 mutation hooks and maintain `workflow_entity_refs` index." SF-7 plan adds a reconciliation job step.

**Affected subfeatures:** SF-7 (design + plan revised)

---

### D-GR-38: Schema Failure — Strictly Blocking, No View-Only Fallback

**Context:**
- PRD REQ-13 / AC-10: "blocks editing when schema endpoint is unavailable"
- Design CMP-69: "No view-only fallback, no partial initialization"
- SF-6 Plan STEP-15: "on schema failure, keep the canvas view-only" — direct contradiction

**Decision:** Strictly blocking confirmed. When `/api/schema/workflow` is unavailable, the editor shows a blocking error panel (retry + back to workflows) with zero canvas, palette, or inspector rendering. No view-only mode.

**Revision:** SF-6 Plan STEP-15 revised to implement blocking error panel instead of view-only mode.

**Affected subfeatures:** SF-6 (plan revised)

---

### D-GR-39: SF-4 Plan — Complete Cycle 5 Approved Revisions

**Context:** The SF-4 plan was identified in Cycle 5 as needing revision to match the R13 PRD narrowing (D-GR-32), but the revisions were never applied. Every Cycle 6 finding was already a Cycle 5 finding.

**Prior decisions:** D-GR-18 (Cycle 3: CLI flag is `--declarative`), D-GR-32 (Cycle 5: SF-4 narrowed to R13's 4 requirements, stale --yaml/seed_loader/plugin HTTP scope removed).

**Decision:** Complete the Cycle 5 approved SF-4 plan revisions:
- Rebase requirement traceability to REQ-34/40/53/54
- Add REQ-54 coverage (artifact-hygiene verification step)
- Add journey verification blocks for J-1 through J-4
- Fix step ordering (STEP-6 template creation before STEP-3 template consumption)
- Align mock class names to SF-3 current exports (`MockAgentRuntime`, `MockInteractionRuntime`, `MockPluginRuntime`)
- CLI flag → `--declarative` per D-GR-18/D-GR-32
- Align `nodes_executed` type to `list[tuple[str, str]]` per SF-2/SF-3
- Remove stale scope (seed_loader, HTTP plugin surfaces, `--yaml` bridge)

**Affected subfeatures:** SF-4 (plan revised)

---

### D-GR-40: Missing Components Added to Plans

**Context:** Multiple design-defined components with CMP IDs, file paths, and verifiable states had zero plan coverage.

**Decision:** All missing components added to their respective plan steps.

**SF-5 additions:**
- Tools hub SPA step (`platform/toolshub/frontend/`) covering REQ-17
- `EditorSchemaBootstrapGate` (CMP-18) added to plan scope
- `YAMLContractErrorPanel` (CMP-19) added to plan scope
- React SPA entry points (`App.tsx`, `main.tsx`, `index.html`) added to STEP-41
- Alembic config files (`alembic.ini`, `env.py`) added to plan
- React Query (`@tanstack/react-query`) added to dependencies
- Pydantic schema files for roles/schemas/templates/errors added to STEP-40
- Restore endpoint for soft-deleted entities added

**SF-3 additions:**
- `MockPluginRuntime` (CMP-3) — plan step added for `mock_plugin.py`
- `respond_sequence()`, `respond_with()`, `raise_error()`, `then_crash()`, `with_cost()` terminal methods — implementation instructions added
- `assert_loop_iterations()` — plan step added

**SF-6 additions:**
- `SchemaBootstrapGate` (CMP-69) — plan file added
- `MergeFunctionEditor` (CMP-65) — plan file added (needed per D-GR-35)
- `PortConditionRow` (CMP-66) — plan file added (needed per D-GR-35)

**SF-7 additions:**
- `LibraryCollectionPage` (CMP-134) — plan file added
- `ActorSlotsEditor` (CMP-137) — plan file added
- `ResourceStateCard` (CMP-138) — plan file added
- 9 visual primitives added to SF-7 scope: AskNodePrimitive, BranchNodePrimitive, PluginNodePrimitive, NodePortDot, EdgeTypeLabel, ActorSlot, PhaseBadge, InlineCodeEditor, TaskTemplateThumbnail
- `actor_slots` storage model: JSONB column is canonical (matches PRD and plan). System design revised from separate table to JSONB column.

**Affected subfeatures:** SF-3, SF-5, SF-6, SF-7 (plans revised)

---

### D-GR-41: All 10 Edge Data Contracts Rewritten

**Context:** All 10 cross-subfeature edge contracts from the original decomposition are stale. Key issues:
- SF-1 → SF-2: 5 phantom exports, wrong module path, 10+ missing actual exports
- SF-2 → SF-3: `run()` signature completely wrong, field name disagreements
- SF-1 → SF-4: Missing `context_keys`, ambiguous AskNode fields
- SF-5 → SF-6, SF-5 → SF-7, SF-7 → SF-6, SF-6 → SF-7: Zero verifiable artifacts

**Decision:** All 10 edge data contracts rewritten after plan revisions from D-GR-34 through D-GR-40 are applied. Each contract must specify:
- Exact module/import paths
- Exact class/function names with full signatures
- Exact field names and types
- API endpoint paths and response shapes (for HTTP edges)

**Affected subfeatures:** All (edge contracts are cross-cutting)

---

## Additional Issues Noted for Revision Agents

The following lower-priority issues from the review should be addressed during the revisions above:

### SF-1 (Declarative Schema)
- Plan uses four separate mode config fields; Design D-SF1-38 mandates single discriminated-union `mode_config` → align plan to Design
- Plan adds `PluginNode.instance_ref` referencing removed `plugin_instances` → remove
- Plan carries three stale store-related error codes → remove
- Plan AskNode field names (`task`, `context_text`) contradict Design (`prompt`, `output_type`) → align to Design
- System Design has workflow-level input/output entities contradicting closed root set → remove from system design
- Design synthetic root phase normalization needs PRD backing or removal → add to PRD if retained
- Plan journey verifications missing for J-6 and J-7 → add
- Expression sandbox constants (AST allowlist, timeout, etc.) need to be importable for SF-2 consumption
- YAML document size limit and alias expansion protection needed
- Max recursion depth on PhaseDefinition.children needed
- No max_length constraints on free-text Pydantic string fields → add

### SF-3 (Testing Framework)
- D-SF3-16 must be fully REMOVED (not rewritten with same ID) per REQ-4/AC-5
- ContextVar type/name mismatch between plan (`current_node_var: ContextVar[str]` default `""`) and system design (`_current_node: ContextVar[str | None]` default `None`) → resolve
- RuntimeConfig `agent_runtime` (singular) vs SF-2's `agent_runtimes` (plural) → align after SF-2 rewrite
- Plan STEP-2 only implements 3-priority resolution; system design specifies 4-strategy model → add role+prompt strategy
- MockCall fields mismatch between PRD and plan → align
- Design CMP-1/CMP-2 location listed as `runner.py` but plan creates at `testing/mock_runtime.py` → fix design locations

### SF-5 (Composer App Foundation)
- Mutation hook event types inconsistent: PRD (created/updated/soft_deleted/restored) vs system design (created/imported/version_saved/deleted) → align to PRD
- PRD REQ-18 requires hooks on all 4 entity types; system design only defines WorkflowMutationHook for workflows → extend to all 4
- Starter template approach contradicts: plan (DB rows, user_id='__system__') vs system design (filesystem assets) → pick one
- Import endpoint path: plan says `POST /api/workflows/{id}/import` vs system design `POST /api/workflows/import` → align
- Import error status code: system design 400 vs plan 422 → standardize
- PUT /api/workflows/{id} allows silent YAML mutation bypassing audit trail → require version creation on content change
- YAML deserialization safety: enforce `yaml.safe_load()`, add document size limits
- `__system__` sentinel user_id needs guard against JWT impersonation
- Input validation for entity name fields needed
- CORS implementation file needed
- Security headers (CSP, X-Frame-Options, etc.) needed

### SF-6 (Workflow Editor)
- Plan journey IDs (J-16/J-17/J-18 etc.) reference non-existent PRD journeys → remap to J-1 through J-6
- Plan references phantom REQ-15 → remove, map steps to actual REQ-1 through REQ-14
- Palette position: PRD says left, system design says right → align to PRD (left)
- Plan testid_registry includes stale `branch-inspector-{id}-switch-editor` → remove
- YAML import needs bomb/entity-expansion protection and file size limits
- No plan step for synthetic root phase normalization verification → add
- Auto-save needs 401/429 error handling

### SF-7 (Libraries & Registries)
- Task template API path: plan `/api/task-templates/` vs system design `/api/templates/` → standardize
- Tool source enum: plan `builtin|custom` vs system design `mcp|custom_function` → align
- Tool soft-delete: plan `is_deleted BOOL` vs PRD/system design `deleted_at TIMESTAMP` → use `deleted_at`
- Design component filenames contradict plan filenames (RoleEditorForm vs RoleEditorView) → align
- System design CP-5 (tool delete) skips preflight GET → add preflight step
- Tool name mutability: plan says immutable, system design allows rename → resolve
- Payload size limit: PRD 256KB vs plan 512KB → align to PRD 256KB
- Rate limiting needed for all new SF-7 endpoints
- `workflow_entity_refs` reconciliation job needed (per D-GR-37)
- `check-name` endpoints in system design but absent from plan → add to plan

---

## Summary

8 decisions made (D-GR-34 through D-GR-41). Revision plan dispatched to affected subfeature agents.