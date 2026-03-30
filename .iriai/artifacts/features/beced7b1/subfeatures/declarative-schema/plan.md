### SF-1: Declarative Schema & Primitives

<!-- SF: declarative-schema -->



## Architecture

### Decision Log

| ID | Decision | Rationale | Citation |
|----|----------|-----------|----------|
| D-SF1-1 | **Canonical module path: `iriai_compose/schema/`** (no `declarative` intermediate). All downstream SFs import from `iriai_compose.schema`. The design doc's reference to `iriai_compose.declarative.schema` is superseded by this decision — `iriai_compose.schema` is the single authoritative import path for all schema models, validation, YAML I/O, and JSON Schema generation. | Cleaner import path; no other schema concept to disambiguate from. Confirmed as canonical per [C-2] integration review. | [decision: user Q2; decision: C-2 — canonical path confirmation] |
| D-SF1-2 | **Dual routing model**: (1) Per-port `condition` predicates on `PortDefinition.condition` — Ask/Plugin are mutually exclusive first-match, Branch (without `switch_function`) is non-exclusive all-match fan-out. (2) `switch_function` on BranchNode — exclusive programmatic routing where function returns output port name string [D-SF1-28]. The two modes are mutually exclusive on a given BranchNode: validation rejects `switch_function` + per-port conditions on the same node. | Unified routing model with two complementary strategies. Per-port conditions handle data-driven branching (Gate approved/rejected). `switch_function` handles programmatic exclusive routing (D-28). `merge_function` is orthogonal — it merges multi-input data before routing. | [decision: user — port routing model; decision: C-1 — switch_function for exclusive routing] |
| D-SF1-3 | Interview = Loop phase + Ask nodes (composed from primitives) | Keeps 3-node-type model pure; verbose but maximally composable | [decision: user Q3] |
| D-SF1-4 | Strict phase I/O boundary — first node input wired to `$input`, last node output wired to `$output` | External edges only touch phase ports; phase mode controls iteration on output | [decision: user Q4] |
| D-SF1-5 | Loop exit condition is Python expression on phase output, not BranchNode | Phase evaluates `exit_condition` against output; true = exit via `condition_met`, false = re-execute | [decision: user Q4 derivative] |
| D-SF1-6 | Schema version as string `"1.0"` | Standard practice; simple semver string | [research: JSON Schema $schema patterns] |
| D-SF1-7 | Pydantic v2 models with `model_json_schema()` for JSON Schema generation | Matches existing iriai-compose dependency (pydantic>=2.0) | [code: iriai-compose/pyproject.toml] |
| D-SF1-8 | YAML serialization via `pyyaml` (already transitive via pydantic) | No `ruamel.yaml` dependency needed for SF-1; round-trip preservation is SF-3's concern | [code: iriai-compose/pyproject.toml] |
| D-SF1-9 | Discriminated union on `type` field for nodes | Enables JSON Schema `oneOf` with discriminator for UI consumption | [code: iriai-compose/iriai_compose/tasks.py — Task is ABC] |
| D-SF1-10 | Single `PortDefinition` type for ALL ports — data inputs, data outputs, hooks | Ports are ports. The container field (inputs/outputs/hooks) determines role. Hooks are visually identical 12px circles [D-22]. No HookDefinition. | [decision: user feedback on plan] |
| D-SF1-11 | NodeBase and PhaseDefinition share identical default port signatures | Both default to `[PortDefinition(name="input")]` for inputs, `[PortDefinition(name="output")]` for outputs, and `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` for hooks. All three node types (Ask, Branch, Plugin) inherit these defaults from NodeBase and then specialize outputs via validators where needed. This ensures every element in the DAG has connectable ports from the moment it is created. | [decision: user feedback — consistency fix] |
| D-SF1-12 | AskNode: 1 fixed input, user-defined outputs (1+), mutually exclusive data-driven routing | Actor produces output, conditions on output ports evaluate against it. Replaces `options` field. Port names ARE the options. | [decision: user — entity hardening] |
| D-SF1-13 | BranchNode: user-defined inputs (1+) for gather/join, user-defined outputs (1+), dual routing via `switch_function` (exclusive) or per-port predicates (non-exclusive fan-out) [D-SF1-28] | Branch is the DAG coordination primitive — where workflows converge (gather multiple inputs) AND diverge (dispatch to parallel paths). Only node type with user-configurable inputs. With `switch_function`: exclusive routing (function returns port name). Without: non-exclusive fan-out (all matching conditions fire). Both input and output counts can be 1. `merge_function` is orthogonal to routing — merges multi-input data. | [decision: user — Branch as gather/dispatch; decision: C-1 — switch_function] |
| D-SF1-14 | PluginNode: 1 fixed input, user-defined outputs (0+), mutually exclusive | Same routing model as Ask for output port conditions, but outputs can be empty (0 ports = fire-and-forget side effect). Think of a plugin as an API call — it may or may not return data. | [decision: user — plugin fire-and-forget] |
| D-SF1-15 | Expression fields use `str` with documented evaluation contexts | Python expression strings are consistent across all evaluable fields (conditions, transforms, exit_condition, reducer, accumulator_init, merge_function, collection). Each documents available variables. `str` is sufficient — runtime sandboxing is SF-2's concern. Typed alternatives (AST, structured conditions) rejected as too restrictive for the patterns found in iriai-build-v2. | [decision: hardening pass — PhaseConfig typing analysis] |
| D-SF1-16 | `fresh_sessions: bool = False` on LoopConfig and FoldConfig for phase-iteration session management | Session clearing happens at loop iteration boundaries in iriai-build-v2 (`_clear_agent_session` called at start of each `while True` iteration in `interview_gate_review`). Session keys are actor-scoped (`{actor.name}:{feature.id}`), but clearing is triggered by phase iteration lifecycle. InteractionActor uses Pending objects with no session_key, so blanket clearing only affects AgentActor (safe). Same actor can participate in both persistent-session contexts (sequential phase) and fresh-session contexts (loop phase with `fresh_sessions: true`) without needing duplicate actor entries. | [code: iriai-build-v2/workflows/_common/_helpers.py — _clear_agent_session at loop iteration boundary; code: iriai-compose/iriai_compose/actors.py:30 — AgentActor.persistent; decision: user feedback — fresh_sessions is phase iteration concern] |
| D-SF1-17 | `instance_ref` on PluginNode as alternative to `plugin_ref + config` | PluginNode can reference either a plugin type (with inline config) OR a pre-configured instance from `workflow.plugin_instances`. Mutually exclusive — validator enforces exactly one. Matches iriai-build-v2 pattern where some plugins are configured per-project while others are inline. | [code: iriai-build-v2/workflows/bugfix/phases/env_setup.py — MCP plugin config] |
| D-SF1-18 | `output_type` and `output_schema` are mutually exclusive on NodeBase and PhaseDefinition; `input_type` and `input_schema` are mutually exclusive on NodeBase and PhaseDefinition | All four fields live on NodeBase (inherited by Ask, Branch, Plugin) and on PhaseDefinition. Each pair defines the element's I/O data structure — one by reference to `workflow.types`, the other inline. Having both in a pair is ambiguous. Validators enforce at most one per pair. Moving these from AskNode to NodeBase makes type declarations uniform: PluginNodes declare their output structure (e.g., `collect_files` returns a file list), BranchNodes declare their merged output shape, and ALL nodes can declare expected input structure for edge type-checking. | [code: iriai-compose/iriai_compose/tasks.py:54 — Ask.output_type is singular; decision: user — I/O type fields on all nodes and phases] |
| D-SF1-19 | PluginNode outputs 0+ (fire-and-forget allowed) | Plugins like `git_commit_push`, `preview_cleanup`, `doc_hosting.push` perform side effects without meaningful return data. Allowing `outputs: []` means no downstream edge is required. Other node types (Ask, Branch) retain 1+ outputs. | [decision: user — plugin I/O clarification] |
| D-SF1-20 | Async gather/barrier is runner responsibility, not schema | When BranchNode has multiple input ports, the runner (SF-2) waits for all connected input ports to receive data before firing the node. The schema declares the ports; the runner implements the barrier. This matches iriai-build-v2's `runner.parallel()` + `asyncio.gather()` pattern but at the DAG edge level. SF-1 makes no claims about execution order or async behavior. | [decision: user — async semantics boundary] |
| D-SF1-21 | Single `Edge` type for all connections — data edges AND hook edges | Edges are edges. The source port's container field (outputs vs hooks) determines whether the edge is a data edge or a hook edge, just as PortDefinition's container determines port role [D-SF1-10]. Hook edges are simply edges where the source resolves to a port in a node/phase's `hooks` list. Validation enforces `transform_fn=None` for hook-sourced edges. The UI renders hook-sourced edges as dashed purple [D-22] and data-sourced edges as solid with type labels — determined at render time by checking the source port's container, not by a schema-level type distinction. This eliminates `HookEdge` as a separate model and `hook_edges` as a separate list on `PhaseDefinition` and `WorkflowConfig`. All edges live in a single `edges` list. | [decision: user — merge HookEdge into Edge, matching PortDefinition unification] |
| D-SF1-22 | `input_type`/`input_schema` and `output_type`/`output_schema` on NodeBase and PhaseDefinition — not AskNode-specific | All nodes and phases declare their expected input and produced output data structures. `input_type: str` references `workflow.types`; `input_schema: dict` is inline JSON Schema. Same for `output_type`/`output_schema`. Each pair is mutually exclusive (validator enforced). This enables: (1) edge type-checking — source output type vs target input type, (2) self-documenting nodes at the schema level, (3) UI showing "expects: PRD" and "produces: TechnicalPlan" on any node, (4) PluginNodes declaring typed outputs (`collect_files` → file list), (5) BranchNodes declaring merged output shape. `PortDefinition.type_ref` remains for per-port granularity on multi-port nodes — port-level `type_ref` takes precedence over node-level type in type resolution. | [decision: user — I/O type fields on all nodes and phases] |
| D-SF1-23 | Store registry — `stores: dict[str, StoreDefinition]` on WorkflowConfig | Named stores declare the interface (keys, types, descriptions). Runner instantiates implementations. Three modes: typed keys (with type_ref), untyped keys (no type_ref, accepts Any), open stores (no keys dict, any key accepted). Dot notation for references: `"store_name.key_name"`. No implementation details (no Postgres/filesystem config). | [code: iriai-compose/storage.py:ArtifactStore; code: iriai-build-v2/storage/artifacts.py:PostgresArtifactStore; decision: user — store as schema entity] |
| D-SF1-24 | Context hierarchy — `context_keys` + `context_text` at workflow, phase, actor, node levels | Four-level context: WorkflowConfig (global), PhaseDefinition (phase-scoped), ActorDefinition (actor baseline), NodeBase (per-node). Runtime merges workflow → phase → actor → node (deduped). `context_keys: list[str]` for store refs via dot notation. `context_text: dict[str, str]` for inline named text snippets. Separate fields — not unified list. | [code: iriai-compose/runner.py:225-262 — resolve() merges actor + task keys; decision: user — 1B separate fields] |
| D-SF1-25 | Context store bindings on ActorDefinition — `context_store` + `handover_key` | `context_store: str \| None` declares which named store for context resolution. `handover_key: str \| None` is dot-notation store ref for actor's handover document. Store bindings only — context management strategy (compaction, summarization) is SF-2 runtime config. | [code: iriai-build-v2/_helpers.py — HandoverDoc at artifacts.put("handover",...); decision: user — 2B store bindings in schema] |
| D-SF1-26 | Dot notation for all store references — `"store_name.key_name"` | All store refs use dot notation: `artifact_key`, `context_keys`, `handover_key`. No separate `store` field on nodes — store target embedded in namespace prefix. No dot = references first declared store (implicit default). Validation parses and checks store existence. | [decision: user — dot notation, no separate store field] |
| D-SF1-27 | Artifact hosting as DAG topology — no schema config | Artifact hosting (HostedInterview pattern) represented as: (1) node writes to store via `artifact_key`, (2) `on_end` hook fires to `doc_hosting` PluginNode, (3) plugin reads from store and pushes to hosting. No `mirror_path` or implementation details on StoreDefinition. Runner handles store implementation. | [code: iriai-build-v2/_common/_tasks.py:HostedInterview; decision: user — no mirror_path] |
| D-SF1-28 | `switch_function: str \| None = None` on BranchNode for exclusive programmatic routing | When set, the Python body receives `data` (the node's merged/passthrough input) and returns an output port name string. Only that named port fires — exclusive routing. When `None`, routing falls back to per-port `condition` expressions with non-exclusive fan-out [D-SF1-2]. The two strategies are mutually exclusive on a given BranchNode: validation rejects a node that has both `switch_function` set AND `condition` on any output port (`invalid_switch_function_config`). `merge_function` is orthogonal — it merges multi-input data BEFORE routing regardless of which routing strategy is used. This restores D-28's "programmatic switch" concept while preserving the per-port condition model for data-driven branching patterns (Gate approved/rejected, Choose options). | [decision: C-1 — switch_function for exclusive routing; code: design-decisions.md D-28 — Branch as programmatic switch] |
| D-SF1-29 | `artifact_key` auto-write semantics — runner writes node output to store automatically | When a node has `artifact_key` set (e.g., `"artifacts.prd"`), the runner (SF-2) automatically writes the node's output to that store key after execution. Execution order: (1) node executes → produces output, (2) if `artifact_key` set, runner writes output to store at that key, (3) output port conditions/switch_function evaluate, (4) matching ports fire with the output data (optionally transformed via edge `transform_fn`). This replaces explicit `runner.artifacts.put(key, value, feature=feature)` calls from iriai-build-v2. Impact: SF-4 migration needs fewer PluginNodes — simple artifact storage is implicit via `artifact_key`, not requiring explicit store-write Plugin nodes. Only side-effect operations (hosting, MCP calls, git) still need Plugin nodes. | [decision: C-4 — artifact_key auto-write clarification; code: iriai-build-v2/_helpers.py — explicit runner.artifacts.put() calls] |
| D-SF1-30 | Workflow-level I/O definitions — `inputs` and `outputs` on WorkflowConfig | `inputs: list[WorkflowInputDefinition]` declares what the workflow expects as input (name, type_ref, required, default). `outputs: list[WorkflowOutputDefinition]` declares what the workflow produces (name, type_ref, description). SF-2 uses these for workflow-level I/O validation: verifying all required inputs are provided at `run()` time, and all declared outputs are produced by the time execution completes. `type_ref` references `workflow.types` keys. Validation checks type refs resolve. | [decision: H-2 — workflow I/O for SF-2 validation] |

### Entity Hardening: iriai-build-v2 Validation Results

This section documents the systematic validation of every schema entity against the iriai-build-v2 codebase (planning workflow: 6 phases ~50 nodes, develop workflow: 7 phases ~60 nodes, bugfix workflow: 8 phases ~35 nodes). Every imperative pattern was traced to its declarative equivalent.

#### Task Type Mapping (Existing → Declarative)

| Existing (iriai_compose) | Declarative | Validation Notes |
|--------------------------|-------------|------------------|
| `Ask` (actor, prompt, output_type) | `AskNode` (single output) | Direct 1:1 mapping. `Ask.actor` → `AskNode.actor` ref. `Ask.output_type: type[BaseModel]` → `AskNode.output_type: str` (name ref, inherited from NodeBase) or `AskNode.output_schema: dict` (inline, inherited from NodeBase). `Ask.prompt` → `AskNode.prompt`. Verified against 40+ Ask usages across all workflows. [code: iriai-compose/tasks.py:49-63] |
| `Interview` (questioner, responder, initial_prompt, done) | Loop phase { AskNode(questioner) → AskNode(responder) → $output } | Two actors mapped as two AskNodes inside a Loop phase. `done: Callable` → `LoopConfig.exit_condition` (Python expression on $output). iriai-build-v2 universally uses `envelope_done` which checks `data.complete` — maps to `exit_condition: "data.complete"`. First iteration uses `initial_prompt`; subsequent use response — requires prompt template with `{{ $input }}` variable. Verified against HostedInterview, broad_interview, per_subfeature_loop patterns. [code: iriai-build-v2/workflows/_common/_helpers.py:envelope_done] |
| `Gate` (approver, prompt) → `True \| False \| str` | AskNode with `approved`/`rejected` output ports with conditions | Gate returns True (approved), False (rejected), or str (feedback). Maps to AskNode with interaction actor. Output routing: `condition: "data is True"` on approved port, `condition: "data is not True"` on rejected port. The `kind="approve"` dispatch is a runtime concern — the runner recognizes InteractionActor and uses the approval protocol. Verified against gate_and_revise pattern (used 15+ times). [code: iriai-compose/tasks.py:104-116] |
| `Choose` (chooser, prompt, options) → `str` | AskNode with N output ports (one per option), conditions matching option string | Options become output port names. Conditions: `condition: "data == 'option_name'"`. The actor selects one option; the matching port fires. `kind="choose"` is runner-level dispatch. Verified against Choose usages in workflows. [code: iriai-compose/tasks.py:119-133] |
| `Respond` (responder, prompt) → `str` | AskNode with single output, no conditions | Simple pass-through. The responder produces free-form text. Maps directly. `kind="respond"` is runner-level. [code: iriai-compose/tasks.py:136-148] |
| `HostedInterview` (Interview + file artifacts + doc hosting) | Loop phase { AskNode + PluginNode(doc_hosting) } with on_start/on_end hooks | HostedInterview wraps Interview with: (1) on_start injects artifact paths → PluginNode on on_start hook, (2) on_done pushes to hosting → PluginNode on on_end hook, (3) file-aware done predicate → Plugin in exit_condition evaluation chain. The most complex mapping — requires 2 Ask nodes + 2 Plugin nodes + hook edges. Hook edges are standard `Edge` instances where the source references a hook port (e.g., `"node_id.on_start"`) [D-SF1-21]. | [code: iriai-build-v2/workflows/_common/_tasks.py:HostedInterview] |

#### Pattern Mapping: Complex Workflow Patterns

| iriai-build-v2 Pattern | Declarative Representation | Verified Against |
|------------------------|---------------------------|------------------|
| `per_subfeature_loop` (sequential iteration with tiered context) | Fold phase: `collection: "ctx['decomposition'].subfeatures"`, `accumulator_init: "{'artifacts': {}, 'summaries': {}}"`. Each iteration: tiered_context_builder Plugin → AskNode(interview) → AskNode(gate) → AskNode(gate_revise). Accumulator tracks completed artifacts/summaries for tiered context on next iteration. | PMPhase, DesignPhase, ArchitecturePhase, TaskPlanningPhase [code: _helpers.py:per_subfeature_loop] |
| `gate_and_revise` (approval loop) | Loop phase: AskNode(producer) → AskNode(approver with gate conditions) → Branch(approved/rejected). Exit: `exit_condition: "data.approved is True"`. On rejection: edge to revision AskNode → loop back. | Used in every artifact gating step (~15 occurrences) |
| `interview_gate_review` (compiled artifact review) | Loop phase with `fresh_sessions: true` in loop_config: AskNode(actor=`reviewer`) → AskNode(user gate). The `fresh_sessions: true` on the loop_config clears all agent actor sessions before each iteration, preventing auto-approval contamination across rejection→revision→re-review iterations. On rejection: extract RevisionPlan → Fold(targeted revisions) → compile Plugin → loop back. | PlanReviewPhase Step 2 [code: _helpers.py:interview_gate_review] |
| `runner.parallel([Ask, Ask, Ask])` (parallel reviews) | Map phase: `collection: "ctx['review_targets']"`. Each item = one reviewer Ask. All run concurrently. Results gathered at phase output. | PlanReviewPhase Step 1 (3 parallel reviewers), DiagnosisAndFixPhase (2 parallel RCA analysts) [code: plan_review.py:52, diagnosis_fix.py:39] |
| `compile_artifacts` (multi-artifact merge) | AskNode(compiler) with context_keys pointing to all per-subfeature artifacts + a file-writing Plugin. Or a dedicated compile Plugin that reads from artifact store and writes merged output. | Used after every per_subfeature_loop |
| `_build_subfeature_context` (tiered context) | `tiered_context_builder` Plugin: receives current slug, decomposition edges, completed artifacts/summaries. Returns formatted context string. Runs before each fold iteration's main Ask. | Every per_subfeature_loop iteration |
| DAG group execution (parallel tasks within sequential groups) | Nested: Fold(over groups) > Map(parallel tasks within group) > AskNode(implementer). Fold accumulates handover. Map fans out tasks. | ImplementationPhase [code: implementation.py:_implement_dag] |
| Retry with max iterations + handover | Loop phase: `max_iterations: 3`, `exit_condition: "not data.reproduced"`. Accumulator carries HandoverDoc for "do not repeat" context. `max_exceeded` port routes to approval gate. | DiagnosisAndFixPhase [code: diagnosis_fix.py:25-188] |

#### Issues Found and Resolved

| Issue | Resolution |
|-------|-----------|
| **Missing `fresh_sessions`**: iriai-build-v2's `_clear_agent_session()` clears session data between gate review iterations. Without this, agents auto-approve based on prior context. Session clearing operates at loop iteration boundaries (called at start of each `while True` iteration). | Added `fresh_sessions: bool = False` to LoopConfig and FoldConfig [D-SF1-16]. Session clearing is a phase iteration concern — same actor participates in both persistent-session and fresh-session contexts without duplication. |
| **Missing `instance_ref`**: PluginNode has `plugin_ref` (type reference) + `config` (inline), but no way to reference a pre-configured instance from `workflow.plugin_instances`. | Added `instance_ref: str \| None` to PluginNode, mutually exclusive with `plugin_ref` [D-SF1-17] |
| **`output_type` + `output_schema` ambiguity**: Both define output structure. Having both is undefined behavior. | Added model_validator enforcing mutual exclusion on NodeBase (inherited by all node types) and PhaseDefinition [D-SF1-18, D-SF1-22] |
| **`input_type` + `input_schema` missing**: No way to declare expected input data structure for edge type-checking, self-documentation, or UI display. | Added `input_type: str \| None` and `input_schema: dict \| None` to NodeBase and PhaseDefinition with mutual exclusion validator [D-SF1-22]. Enables edge type-checking (source output vs target input) and self-documenting nodes. |
| **`output_type`/`output_schema` only on AskNode**: PluginNodes produce typed outputs (e.g., `collect_files` returns a file list, `tiered_context_builder` returns formatted context) but had no way to declare this. BranchNodes with `merge_function` produce merged data with a specific shape. | Moved `output_type`/`output_schema` from AskNode to NodeBase [D-SF1-22]. All three node types and PhaseDefinition now declare their output structure uniformly. |
| **`ActorDefinition` incomplete validation**: Agent type requires `role`, interaction type requires `resolver`. No enforcement at schema level. | Added model_validator |
| **Phase `$input` semantics for Map/Fold undocumented**: Inside a Map phase, `$input` = current item. Inside a Fold, `$input` = `{item, accumulator}`. | Added Expression Evaluation Contexts section with per-mode documentation [D-SF1-15] |
| **No way to express "parallel safe" actor copies**: iriai-build-v2's `_make_parallel_actor()` creates unique-named copies for `runner.parallel()`. Map phases need the same. | Runner responsibility — Map phase runner auto-creates unique actor instances per iteration. Documented in interface contract. |
| **Async gather semantics undefined in schema**: BranchNode with multiple inputs needs barrier behavior, but schema is a static data model. | Runner responsibility (SF-2). When a BranchNode has N input ports, the runner awaits data on all N before firing. Schema declares port definitions only. [D-SF1-20] |
| **Resume/checkpoint pattern not in schema**: iriai-build-v2 checks artifact store before executing (skip if already done). | Runner responsibility — SF-2 runner checks `artifact_key` in store before executing node. Documented in interface contract. |
| **State model (BuildState/BugFixState) not represented**: Phases read/write typed state fields. | Artifact store IS the state. `context_keys` on actors/nodes replace state field reads. `artifact_key` on nodes replaces state field writes. |
| **`done: Callable` not directly representable**: Interview's `done` is `Callable[[Any], bool]`. | Replaced by `LoopConfig.exit_condition` (Python expression). Universal pattern is `envelope_done` → `"data.complete"`. |
| **HookEdge as separate type creates unnecessary type explosion**: PortDefinition was already unified [D-SF1-10] — hooks are just ports in the `hooks` container. Having a separate `HookEdge` type contradicted this unification. | Merged into single `Edge` type [D-SF1-21]. Hook edges are edges where the source port lives in a `hooks` list. Validation enforces `transform_fn=None` for hook-sourced edges. UI determines rendering style (dashed purple vs solid) by checking the source port's container at render time. |
| **Store not represented as entity**: `artifact_key` and `context_keys` referenced opaque strings with no schema-level store declaration. iriai-build-v2 uses `PostgresArtifactStore` + `PostgresSessionStore` — two distinct stores — but schema had no way to declare or differentiate them. | Added `stores: dict[str, StoreDefinition]` on WorkflowConfig [D-SF1-23]. Named stores declare interface (keys, types). Runner instantiates implementations. Dot notation for references [D-SF1-26]. Validated against 25+ artifact key patterns from iriai-build-v2. |
| **Context hierarchy missing phase/workflow levels**: Actor `context_keys` existed but phase-level and workflow-level context did not. Actors carried heavy key lists (e.g., `["project", "scope", "prd", "design", "plan", "system-design"]`) that should be phase-scoped. | Added `context_keys` + `context_text` at all 4 levels: WorkflowConfig, PhaseDefinition, ActorDefinition, NodeBase [D-SF1-24]. Runtime merges workflow → phase → actor → node. |
| **No inline text context mechanism**: iriai-build-v2 manually injects handover context, tiered subfeature context, and format instructions as literal strings in prompts. No schema-level field for inline text. | Added `context_text: dict[str, str]` at all 4 levels [D-SF1-24]. Named text snippets injected alongside resolved store keys. |
| **Actor context bindings missing**: No way to declare which store an actor reads context from, or where its handover doc lives. All done imperatively in Python. | Added `context_store` and `handover_key` to ActorDefinition [D-SF1-25]. Declarative store bindings — strategy deferred to SF-2. |
| **Artifact hosting modeled as store config**: Initial proposal had `mirror_path` on StoreDefinition. But hosting implementation (Postgres vs filesystem vs web) is a runner concern. | Removed `mirror_path`. Artifact hosting represented as DAG topology: node → on_end hook → doc_hosting plugin [D-SF1-27]. Already representable with existing primitives. |
| **Exclusive routing needed for programmatic switching**: D-28 in the design doc specifies "Branch = programmatic switch, returns path name string" but the original plan rejected `switch_function` in favor of per-port conditions only. Integration review [C-1] identified that both patterns are needed. | Added `switch_function: str \| None = None` to BranchNode [D-SF1-28]. Dual routing model: `switch_function` set → exclusive (returns port name), not set → per-port conditions (non-exclusive). Mutually exclusive on same node — validation enforces. `merge_function` orthogonal. |
| **Explicit artifact writes are verbose**: iriai-build-v2 patterns like `await runner.artifacts.put("prd", prd, feature=feature)` after every task execution are boilerplate. No schema-level way to declare "this node writes to store." | Clarified `artifact_key` auto-write semantics [D-SF1-29]. Runner auto-writes node output to store at `artifact_key` after execution, before routing. Fewer PluginNodes needed in SF-4 migration. |
| **Workflow-level I/O not declared**: No way for SF-2 to validate that a workflow receives its expected inputs or produces its expected outputs. | Added `inputs: list[WorkflowInputDefinition]` and `outputs: list[WorkflowOutputDefinition]` to WorkflowConfig [D-SF1-30]. SF-2 validates at run time. |

### Expression Evaluation Contexts [D-SF1-15]

All expression fields are Python `str` values evaluated at runtime by SF-2's runner. Each expression type documents the variables available in its evaluation scope:

| Expression Field | Location | Available Variables | Returns | Example |
|-----------------|----------|-------------------|---------|---------|
| `PortDefinition.condition` | Output ports on any node | `data` = node's output value | `bool` | `"data.verdict == 'approved'"`, `"data is True"` |
| `BranchNode.switch_function` | BranchNode body (exclusive routing) [D-SF1-28] | `data` = node's merged/passthrough input value | `str` — output port name | `"'approved' if data.verdict == 'approved' else 'rejected'"`, `"data.next_step"` |
| `BranchNode.merge_function` | BranchNode body | `inputs` = `dict[str, Any]` mapping port_name → received data | merged data `dict` | `"{'combined': list(inputs.values())}"` |
| `Edge.transform_fn` | Data edges (NOT hook edges) | `data` = source port's output value | transformed value | `"data['key']"`, `"{'summary': data.summary}"` |
| `LoopConfig.exit_condition` | Loop phase, after each iteration | `data` = phase's `$output` value | `bool` — True exits loop | `"data.complete"`, `"not data.reproduced"` |
| `MapConfig.collection` | Map phase, once before iterations | `ctx` = resolved context keys + phase input | `Iterable` | `"ctx['decomposition'].subfeatures"` |
| `FoldConfig.collection` | Fold phase, once before iterations | `ctx` = same as Map | `Iterable` | `"ctx['decomposition'].subfeatures"` |
| `FoldConfig.accumulator_init` | Fold phase, once before first iteration | (no variables) | `Any` — initial accumulator | `"{'artifacts': {}, 'summaries': {}}"` |
| `FoldConfig.reducer` | Fold phase, after each iteration | `accumulator` = current value, `result` = iteration $output | `Any` — new accumulator | `"{**accumulator, result['slug']: result['text']}"` |

**Why `str` is sufficient [D-SF1-15]:**

1. **Consistency**: All evaluable fields use the same pattern — Python expression strings. No mixed paradigm.
2. **Expressiveness**: iriai-build-v2's actual patterns require full Python expressiveness: `all(v.approved for v in data.values())`, `not data.reproduced`, dict comprehensions in reducers. Structured condition types (e.g., `{field: "verdict", op: "==", value: "approved"}`) cannot express these.
3. **Security boundary**: Expression sandboxing is SF-2's runner responsibility, not the schema's. The schema stores opaque strings — the runner evaluates them in a restricted context (no imports, no side effects, only the documented variables).
4. **UI generation**: SF-6 can provide expression builders for common patterns while preserving the string representation underneath.

**Phase `$input` semantics by mode:**

| Mode | `$input` contents | `$output` handling |
|------|-------------------|-------------------|
| Sequential | Whatever the external edge provides | Passes through to phase output port |
| Map | Current collection item (one per parallel execution) | All iteration outputs collected into list at phase output |
| Fold | `{"item": current_collection_item, "accumulator": current_accumulator_value}` | Passed to `reducer` expression; result becomes next accumulator |
| Loop | First iteration: external input. Subsequent: previous iteration's `$output` | Evaluated by `exit_condition`; if truthy → `condition_met` port, if falsy → feed back as next `$input` |

### Unified Port Model [D-SF1-10, D-SF1-11]

Everything is a `PortDefinition`. The field it lives in determines its role:

| Container field | Visual position | Port role | Edge syntax |
|----------------|-----------------|-----------|-------------|
| `inputs` | Left edge (blue) | Data input | `"node_id.port_name"` target |
| `outputs` | Right edge (green) | Data output | `"node_id.port_name"` source |
| `hooks` | Bottom (gray) | Lifecycle hook (fire-and-forget) | `"node_id.hook_name"` source |

All three use the same `PortDefinition(name, type_ref, description, condition)` type. There is no separate `HookDefinition`.

The `condition` field is only meaningful on output ports [D-SF1-2]. It holds a Python predicate string that receives `data` (the node's output) and returns a boolean.

**Output port routing behaviors [D-SF1-2, D-SF1-28]:**

1. **Single output port with no condition** — always fires (pass-through).
2. **Multiple output ports with conditions on Ask/Plugin** — mutually exclusive, first match wins.
3. **Multiple output ports on Branch with `switch_function`** — exclusive programmatic routing. Function receives `data`, returns port name string. Only that named port fires. [D-SF1-28]
4. **Multiple output ports on Branch with per-port conditions (no `switch_function`)** — non-exclusive, all matching conditions fire simultaneously.
5. **Validation constraint:** `switch_function` and per-port `condition` are mutually exclusive on a given BranchNode. A node with both produces `invalid_switch_function_config` error.

**Default ports (shared between NodeBase and PhaseDefinition) [D-SF1-11]:**
- `inputs`: `[PortDefinition(name="input")]`
- `outputs`: `[PortDefinition(name="output")]`
- `hooks`: `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]`

### I/O Type Model [D-SF1-22]

Nodes and phases declare their expected input and produced output data structures via four optional fields on NodeBase and PhaseDefinition:

| Field | Type | Default | Constraint | Purpose |
|-------|------|---------|------------|---------|
| `input_type` | `str \| None` | `None` | Mutual exclusion with `input_schema` | Reference to `workflow.types` key — expected input structure |
| `input_schema` | `dict \| None` | `None` | Mutual exclusion with `input_type` | Inline JSON Schema — expected input structure |
| `output_type` | `str \| None` | `None` | Mutual exclusion with `output_schema` | Reference to `workflow.types` key — produced output structure |
| `output_schema` | `dict \| None` | `None` | Mutual exclusion with `output_type` | Inline JSON Schema — produced output structure |

**Relationship with PortDefinition.type_ref:**

`PortDefinition.type_ref` provides per-port type annotation for multi-port nodes. Node-level `input_type`/`output_type` provides a simpler mechanism for the common single-port case. Type resolution priority:

1. **Port-level `type_ref` takes precedence** — most specific, always wins
2. **Node-level type applies to the default port** — if a node has `output_type: "PRD"` and a single output port with `type_ref: None`, the resolved type for that port is `"PRD"`
3. **Neither set** — no type constraint, any data accepted

This means simple single-port workflows use node-level types, while complex multi-port nodes use port-level `type_ref`. No conflict is possible because port-level always wins.

**Type resolution helper** (used by validation and downstream consumers):

```python
def resolve_port_type(element, port: PortDefinition, is_input: bool) -> str | None:
    """Resolve the effective type for a port, considering node/phase-level fallbacks."""
    # Port-level type_ref always takes precedence
    if port.type_ref is not None:
        return port.type_ref
    # Fall back to element-level type for single-port elements
    if is_input and len(element.inputs) == 1:
        return element.input_type  # str | None
    if not is_input and len(element.outputs) == 1:
        return element.output_type  # str | None
    return None
```

**Why all nodes and phases need I/O types [D-SF1-22]:**

| Node Type | Input type use case | Output type use case |
|-----------|--------------------|--------------------|
| AskNode | Declares expected input shape for edge type-checking (e.g., "expects SubfeatureDecomposition") | Tells runtime what structured output the agent should produce (existing `Ask.output_type` pattern) |
| BranchNode | Declares expected shape for each input port on gather nodes | Declares merged output shape from `merge_function` |
| PluginNode | Declares expected input shape (e.g., `tiered_context_builder` expects specific context dict) | Declares output shape (e.g., `collect_files` returns a file list) |
| PhaseDefinition | Declares what the phase expects via its `$input` boundary | Declares what the phase produces via its `$output` boundary |

### Store Model [D-SF1-23, D-SF1-26]

Workflows declare named stores that the runner instantiates. Schema declares the interface — runner provides the implementation (Postgres, filesystem, in-memory).

```yaml
stores:
  artifacts:
    description: "Primary artifact store"
    keys:
      prd: { type_ref: "PRD", description: "Product requirements" }
      design: { type_ref: "DesignDecisions" }
      handover: { description: "Cumulative handover doc" }
      # Keys without type_ref accept Any
  sessions:
    description: "Agent session persistence"
    # No keys dict = open store, any key accepted
```

**Three key typing modes:**

| Mode | YAML | Meaning |
|------|------|---------|
| **Typed** | `type_ref: "PRD"` | Value must conform to `workflow.types["PRD"]`. Validation checks writer `output_type` matches. |
| **Untyped** | Key declared, no `type_ref` | Key declared (self-documenting) but accepts `Any`. Generic output allowed. |
| **Open store** | `keys` omitted | Store accepts any key. No validation of key references. For dynamic patterns like `"{prefix}:{slug}"`. |

**Dot notation for all references [D-SF1-26]:**
- `artifact_key: "artifacts.prd"` — node writes output to `prd` key in `artifacts` store
- `context_keys: ["artifacts.prd", "artifacts.design"]` — node reads from `artifacts` store
- `handover_key: "artifacts.handover_pm"` — actor's handover doc location
- No dot = implicit first declared store

**Artifact hosting as DAG topology [D-SF1-27]:**
```yaml
# Node writes to store
- id: write_prd
  type: ask
  artifact_key: "artifacts.prd"
  hooks: [on_start, on_end]

# Hook edge triggers hosting plugin
edges:
  - source: "write_prd.on_end"
    target: "host_prd.input"

# Plugin reads from store and hosts
- id: host_prd
  type: plugin
  plugin_ref: doc_hosting
  config:
    store_key: "artifacts.prd"
```

No `mirror_path` or implementation details on stores — runner handles persistence strategy.

### Context Hierarchy Model [D-SF1-24, D-SF1-25]

Context is set at four levels, each with two fields:

| Level | `context_keys: list[str]` | `context_text: dict[str, str]` |
|-------|--------------------------|-------------------------------|
| **WorkflowConfig** | Global baseline — all nodes inherit | Global inline text snippets |
| **PhaseDefinition** | Phase-scoped — nodes in this phase inherit | Phase-scoped inline text |
| **ActorDefinition** | Actor baseline — every invocation of this actor | Actor-specific inline text |
| **NodeBase** | Per-node specific | Per-node inline text |

**Runtime merge order:** workflow → phase → actor → node (deduplicated, preserving order).

**`context_keys`** references store keys via dot notation. Resolved by runner's ContextProvider (`DefaultContextProvider.resolve()`). Each key resolved to formatted markdown section.

**`context_text`** provides inline named text snippets injected alongside resolved store keys. Not store references — literal strings embedded in YAML. Used for handover injection, format instructions, etc.

**Actor store bindings [D-SF1-25]:**
- `context_store: str | None` — which store for this actor's context resolution (default: first store)
- `handover_key: str | None` — dot-notation reference to handover doc in store (e.g., `"artifacts.handover_pm"`)
- Actual context management strategy (compaction, summarization, token budgets) is SF-2 runner config

### Unified Edge Model [D-SF1-21]

All connections between ports use a single `Edge` type. The source port's container determines whether an edge is a data edge or a hook edge:

| Source port container | Edge semantics | `transform_fn` | UI rendering | Example |
|----------------------|----------------|-----------------|--------------|---------|
| `outputs` | Data edge | Allowed (optional) | Solid line, type label at midpoint, ⚡ if transform | `source: "ask_1.output"` → `target: "branch_1.input"` |
| `hooks` | Hook edge (fire-and-forget) | Must be `None` (validated) | Dashed purple (#a78bfa), no type label | `source: "ask_1.on_end"` → `target: "plugin_1.input"` |

**Why a single type [D-SF1-21]:**

Just as `PortDefinition` is unified [D-SF1-10] (no `HookDefinition`), edges are unified (no `HookEdge`). The port's container field already carries the semantic distinction. A separate `HookEdge` type:
- Duplicates the `source`/`target`/`description` fields identically
- Only differs by *not having* `transform_fn` — a constraint easily enforced by validation
- Forces two separate `edges` lists on PhaseDefinition/WorkflowConfig, complicating edge traversal and DAG algorithms
- Contradicts the design principle that ports determine role, not the edge connecting them

**Validation rule:** If `edge.source` resolves to a port in the `hooks` container of any node or phase, then `edge.transform_fn` must be `None`. Violation produces `invalid_hook_edge_transform` error.

### Phase I/O Boundary Model [D-SF1-4]

```
External ──→ [Phase Input Port] ──→ $input ──→ FirstNode ──→ ... ──→ LastNode ──→ $output ──→ [Phase Output Port] ──→ External
                                                                                      │
                                                                                      ↓ (loop mode only)
                                                                              exit_condition(output)
                                                                              ├── True  → condition_met port → External
                                                                              └── False → feed back to $input (next iteration)
```

### Phase Iteration Session Model [D-SF1-16]

Session clearing is a phase iteration concern, not an actor property. This matches the runtime implementation where:
- Session key = `{actor.name}:{feature.id}` — actor-scoped [code: iriai-compose/runner.py:250-251]
- `_clear_agent_session()` is called at the start of each loop iteration in `interview_gate_review` [code: iriai-build-v2/_helpers.py:1333-1344]
- InteractionActor uses Pending objects with no session_key — clearing only affects AgentActor (safe for blanket boolean)
- Map phases already create isolated sessions per parallel execution via runner actor deduplication
- Sequential phases: sessions persist naturally across nodes (the default)

**Default:** Sessions persist for the duration of task execution. Same actor across multiple nodes shares a session (key = `{actor.name}:{feature.id}`).

**Loop/Fold with `fresh_sessions: true`:** Runner clears ALL agent actor sessions used within the phase before each iteration. InteractionActor unaffected (uses Pending, not sessions).

**Map:** Runner auto-creates unique actor instances per parallel execution. Sessions inherently isolated. No `fresh_sessions` field needed.

**Sequential:** Sessions persist naturally. No iteration boundaries.

**`persistent` (actor) vs `fresh_sessions` (phase) interaction:**

| `persistent` (actor) | `fresh_sessions` (phase) | Behavior |
|---|---|---|
| true (default) | false (default) | Session persists across workflow run. Full continuity. |
| true | true | Session persisted to store but cleared before each phase iteration. Audit trail maintained. |
| false | false | Ephemeral session, runtime manages lifecycle. |
| false | true | Ephemeral and cleared per iteration. |

**Example: Same actor, different session behavior by phase:**
```yaml
actors:
  reviewer:
    type: agent
    role: { name: reviewer, prompt: "Review...", model: claude-opus-4-20250514 }
    # No fresh_sessions here — same actor used everywhere

phases:
  - id: interview_phase
    mode: sequential
    # reviewer maintains session continuity here (default)

  - id: gate_review_loop
    mode: loop
    loop_config:
      exit_condition: "data.approved"
      fresh_sessions: true  # reviewer gets fresh session each iteration
```

---

## System Design

### Services

| ID | Name | Kind | Technology | Description | Journeys |
|----|------|------|------------|-------------|----------|
| SVC-1 | iriai-compose-schema | service | Python 3.11+ / Pydantic v2 | New `iriai_compose/schema/` subpackage (canonical import: `iriai_compose.schema` [C-2]). Pydantic v2 models defining the declarative workflow format with dual routing (per-port conditions + switch_function [D-SF1-28]), workflow-level I/O [D-SF1-30], structural validation (21 error codes [H-3]), YAML I/O, and JSON Schema generation. Pure data layer with zero runtime dependencies. | J-1, J-2, J-3, J-4, J-5, J-6 |
| SVC-2 | iriai-compose-runtime | service | Python 3.11+ | Existing `iriai_compose` package — `WorkflowRunner`, `AgentRuntime`, `InteractionRuntime`, `Task`, `Phase`, `Workflow`. SF-1 does NOT modify this; SF-2 bridges schema→runtime. | J-8 |
| SVC-3 | yaml-workflow-files | database | YAML on filesystem | `.yaml` files authored by developers or exported from Compose UI. | J-1, J-9, J-11, J-12, J-13 |
| SVC-4 | json-schema-artifact | database | JSON file | Static `workflow-schema.json` generated via `model_json_schema()`. | J-3 |
| SVC-5 | iriai-compose-testing | service | Python 3.11+ / pytest | SF-3 `iriai_compose.testing` subpackage. | J-7, J-8, J-9, J-10 |
| SVC-6 | iriai-build-v2-workflows | external | Python 3.11+ | Existing imperative workflows. Read-only reference for SF-4. | J-11, J-12, J-13, J-14 |
| SVC-7 | iriai-workflows-backend | service | Python / FastAPI / SQLite | SF-5/SF-6/SF-7 visual builder backend. | J-15, J-16 |
| SVC-8 | iriai-workflows-frontend | frontend | React / React Flow | SF-5/SF-6/SF-7 visual builder UI. | J-3, J-15, J-16 |

### Connections

| From | To | Label | Protocol | Journeys |
|------|----|-------|----------|----------|
| SVC-1 | SVC-3 | `dump_workflow()` serializes config to YAML | Python file I/O | J-1, J-11, J-12, J-13 |
| SVC-3 | SVC-1 | `load_workflow()` deserializes YAML to config | Python file I/O | J-1, J-2, J-7, J-8, J-9 |
| SVC-1 | SVC-4 | `generate_json_schema()` exports schema | Python file I/O | J-3 |
| SVC-1 | SVC-2 | Schema models imported by loader/runner (SF-2) | Python import | J-8 |
| SVC-1 | SVC-5 | Schema models + validation imported by testing (SF-3) | Python import | J-7, J-8, J-9, J-10 |
| SVC-6 | SVC-3 | SF-4 migration translates imperative → YAML | Python file I/O | J-11, J-12, J-13, J-14 |
| SVC-4 | SVC-8 | JSON Schema drives inspector field rendering | Static import / HTTP | J-3, J-16 |
| SVC-3 | SVC-7 | YAML import/export via API | HTTP file upload | J-15, J-16 |
| SVC-7 | SVC-1 | Backend uses `load_workflow()`/`validate_workflow()` | Python import | J-15 |

### API Endpoints

SF-1 is a Python library — it exposes no HTTP endpoints. Its "API" is the Python import surface:

| Method | Path | Service | Description | Request | Response | Auth |
|--------|------|---------|-------------|---------|----------|------|
| Python | `load_workflow(path)` | SVC-1 | Load YAML file → WorkflowConfig | `str \| Path` | `WorkflowConfig` | N/A |
| Python | `load_workflow_lenient(path)` | SVC-1 | Load YAML + structural validation | `str \| Path` | `(WorkflowConfig, list[ValidationError])` | N/A |
| Python | `dump_workflow(config, path?)` | SVC-1 | Serialize config to YAML string/file | `WorkflowConfig` | `str` | N/A |
| Python | `validate_workflow(config)` | SVC-1 | Full structural validation | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `validate_type_flow(config)` | SVC-1 | Edge type compatibility only | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `detect_cycles(config)` | SVC-1 | DFS cycle detection only | `WorkflowConfig` | `list[ValidationError]` | N/A |
| Python | `generate_json_schema(path?)` | SVC-1 | Export JSON Schema from models | `str \| Path \| None` | `dict` | N/A |
| CLI | `python -m iriai_compose.schema.json_schema [path]` | SVC-1 | CLI JSON Schema generator | argv path | JSON file | N/A |

### Call Paths

#### CP-1: Author YAML Workflow (J-1)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Developer | SVC-3 | Write YAML file | Author workflow definition in YAML | `.yaml` on filesystem |
| 2 | SVC-3 | SVC-1 | `load_workflow(path)` | Parse YAML → Pydantic models | `WorkflowConfig` or raises |
| 3 | SVC-1 | SVC-1 | `validate_workflow(config)` | Structural validation checks | `list[ValidationError]` (empty = valid) |

#### CP-2: Generate JSON Schema for UI (J-3)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Build script | SVC-1 | `generate_json_schema(path)` | Pydantic `model_json_schema()` | JSON Schema dict |
| 2 | SVC-1 | SVC-4 | Write JSON file | Serialize to `workflow-schema.json` | Static JSON artifact |
| 3 | SVC-4 | SVC-8 | Import at build time | UI bundles schema for inspector rendering | Inspector field definitions |

#### CP-3: Validate Workflow Structurally (J-5)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Caller | SVC-1 | `validate_workflow(config)` | Entry point | Aggregated error list |
| 2 | SVC-1 | SVC-1 | `_check_duplicate_ids()` | Scan all phases for ID collisions | `duplicate_node_id` / `duplicate_phase_id` errors |
| 3 | SVC-1 | SVC-1 | `_check_actor_refs()` | Verify node actor refs exist in `workflow.actors` | `invalid_actor_ref` errors |
| 4 | SVC-1 | SVC-1 | `_check_phase_configs()` | Verify mode-specific config presence | `invalid_phase_mode_config` errors |
| 5 | SVC-1 | SVC-1 | `_check_edges()` | Resolve `node_id.port_name` references, classify as data or hook by source port container | `dangling_edge` errors |
| 6 | SVC-1 | SVC-1 | `_check_hook_edge_constraints()` | Verify hook-sourced edges have `transform_fn=None` [D-SF1-21] | `invalid_hook_edge_transform` errors |
| 7 | SVC-1 | SVC-1 | `_check_phase_boundaries()` | Verify `$input`/`$output` wiring [D-SF1-4] | `phase_boundary_violation` errors |
| 8 | SVC-1 | SVC-1 | `_check_cycles()` | DFS within each phase's edge graph | `cycle_detected` errors |
| 9 | SVC-1 | SVC-1 | `_check_reachability()` | Find nodes with no incoming edges | `unreachable_node` errors |
| 10 | SVC-1 | SVC-1 | `_check_type_flow()` | Compare source output type ↔ target input type using `resolve_port_type()` [D-SF1-22] | `type_mismatch` errors |
| 11 | SVC-1 | SVC-1 | `_check_branch_configs()` | Verify Branch has ≥1 input + ≥1 output ports | `invalid_branch_config` errors |
| 12 | SVC-1 | SVC-1 | `_check_plugin_refs()` | Verify plugin_ref or instance_ref exists | `invalid_plugin_ref` errors |
| 13 | SVC-1 | SVC-1 | `_check_output_port_conditions()` | Warn on ambiguous multi-port conditions | `missing_output_condition` warnings |
| 14 | SVC-1 | SVC-1 | `_check_io_configs()` | Verify input_type/input_schema and output_type/output_schema mutual exclusion on all nodes and phases [D-SF1-22] | `invalid_io_config` errors |
| 15 | SVC-1 | SVC-1 | `_check_type_refs()` | Verify input_type/output_type reference valid keys in `workflow.types` | `invalid_type_ref` errors |
| 16 | SVC-1 | SVC-1 | `_check_store_refs()` | Verify all dot-notation references (`artifact_key`, `context_keys`, `handover_key`) have valid store name prefix [D-SF1-26] | `invalid_store_ref` errors |
| 17 | SVC-1 | SVC-1 | `_check_store_key_refs()` | For non-open stores, verify referenced keys exist in store definition [D-SF1-23] | `invalid_store_key_ref` errors |
| 18 | SVC-1 | SVC-1 | `_check_store_key_types()` | For typed store keys, verify node `output_type` matches store key `type_ref` when writing [D-SF1-23] | `store_type_mismatch` errors |
| 19 | SVC-1 | SVC-1 | `_check_switch_function_config()` | Verify BranchNodes with `switch_function` have no per-port `condition` on outputs [D-SF1-28] | `invalid_switch_function_config` errors |
| 20 | SVC-1 | SVC-1 | `_check_workflow_io_refs()` | Verify `type_ref` on WorkflowInputDefinition/WorkflowOutputDefinition resolves to `workflow.types` keys [D-SF1-30] | `invalid_workflow_io_ref` errors |
| 21 | SVC-1 | SVC-1 | `_check_required_fields()` | Catch required field violations not caught by Pydantic in lenient loading paths | `missing_required_field` errors |

#### CP-4: YAML Round-Trip (J-9)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | Test | SVC-3 | Read fixture | Load raw YAML from `tests/fixtures/` | YAML string |
| 2 | SVC-3 | SVC-1 | `load_workflow(path)` | Deserialize to typed objects | `WorkflowConfig` |
| 3 | SVC-1 | SVC-1 | `dump_workflow(config)` | Reserialize to YAML string | YAML string |
| 4 | Test | SVC-1 | `load_workflow()` on dumped string | Verify re-deserialization | Equivalent `WorkflowConfig` |

#### CP-5: Migrate Imperative Workflow (J-11, J-12, J-13)

| Seq | From | To | Action | Description | Returns |
|-----|------|----|--------|-------------|---------|
| 1 | SF-4 script | SVC-6 | Read Python classes | Analyze Phase/Task definitions | Class structure |
| 2 | SF-4 script | SVC-1 | Construct `WorkflowConfig` | Build declarative equivalent programmatically | `WorkflowConfig` instance |
| 3 | SVC-1 | SVC-1 | `validate_workflow(config)` | Verify structural validity | Errors → J-14 gap if any |
| 4 | SVC-1 | SVC-3 | `dump_workflow(config, path)` | Write YAML file | `.yaml` on filesystem |

### Entities

#### PortDefinition [D-SF1-10]

Single type for ALL ports — data inputs, data outputs, hooks. The container field determines the port's role.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Port identifier (e.g., `"input"`, `"output"`, `"on_start"`, `"approved"`) |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Type name for edge type-checking. Takes precedence over node-level `input_type`/`output_type` [D-SF1-22]. |
| `description` | `str \| None` | `None` | — | Human-readable description |
| `condition` | `str \| None` | `None` | Python expression string | Output port routing predicate. Receives `data` (node output). Returns `bool`. Only meaningful on output ports [D-SF1-2]. |

#### NodeBase [D-SF1-11, D-SF1-22]

Abstract base for all node types. Provides default port signatures shared with PhaseDefinition. Provides I/O type declarations shared by all node types.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required, unique within phase | Node identifier |
| `type` | `Literal["ask", "branch", "plugin"]` | — | required | Discriminator for union [D-SF1-9] |
| `summary` | `str \| None` | `None` | max ~120 chars | 1–2 line description on card face [D-32] |
| `context_keys` | `list[str]` | `[]` | — | Artifact store keys to inject into prompt context |
| `context_text` | `dict[str, str]` | `{}` | — | Inline text snippets for this node's context [D-SF1-24] |
| `artifact_key` | `str \| None` | `None` | — | Output artifact key. Also used as output port label [D-32]. |
| `input_type` | `str \| None` | `None` | mutual exclusion with `input_schema` [D-SF1-22] | Reference to `workflow.types` key — expected input structure |
| `input_schema` | `dict \| None` | `None` | mutual exclusion with `input_type` [D-SF1-22] | Inline JSON Schema — expected input structure |
| `output_type` | `str \| None` | `None` | mutual exclusion with `output_schema` [D-SF1-22] | Reference to `workflow.types` key — produced output structure |
| `output_schema` | `dict \| None` | `None` | mutual exclusion with `output_type` [D-SF1-22] | Inline JSON Schema — produced output structure |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Data input ports [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Data output ports [D-SF1-11] |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | — | Lifecycle hook ports [D-SF1-11] |
| `position` | `dict[str, float] \| None` | `None` | `{"x": float, "y": float}` | Canvas position (UI-only) |

**Validators:**
- `_check_input_config`: Raises if both `input_type` and `input_schema` are set. [D-SF1-22]
- `_check_output_config`: Raises if both `output_type` and `output_schema` are set. [D-SF1-18, D-SF1-22]

#### AskNode (extends NodeBase, type="ask") [D-SF1-12]

Agent invocation node. 1 fixed input, user-defined outputs (1+), mutually exclusive routing.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `actor` | `str` | — | required, must reference `workflow.actors` key | Actor to invoke |
| `prompt` | `str` | — | required | Prompt template. `{{ $input }}`, `{{ ctx.key }}` variables. |

**Validators:**
- `_fix_input_ports`: Always overrides inputs to single `[PortDefinition(name="input")]`. User cannot customize. [D-SF1-12]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema` with mutual exclusion validators. `output_type` tells the runtime what structured output the agent should produce (maps directly from existing `Ask.output_type: type[BaseModel]`).

**NOT fields:** No `fresh_session`, no `options`, no `kind`. Session clearing is on phase configs [D-SF1-16].

#### BranchNode (extends NodeBase, type="branch") [D-SF1-13, D-SF1-20, D-SF1-28]

DAG coordination primitive — gather (multiple inputs) and dispatch (multiple outputs). Supports two mutually exclusive routing strategies: `switch_function` (exclusive) or per-port `condition` (non-exclusive fan-out).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `switch_function` | `str \| None` | `None` | Python body string; mutually exclusive with per-port `condition` on outputs [D-SF1-28] | Exclusive routing function. Receives `data` (merged/passthrough input). Returns output port name string. Only that port fires. |
| `merge_function` | `str \| None` | `None` | Python expression | Merges multi-input data. Receives `inputs: dict[str, Any]`. Returns merged dict. Orthogonal to routing — runs before either routing strategy. [D-SF1-15] |

**Validators:**
- `_validate_branch_ports`: Enforces min 1 input port, min 1 output port. No upper bounds. Both can be exactly 1. [D-SF1-13]
- `_validate_switch_function_config`: If `switch_function` is set, no output port in `outputs` may have a non-None `condition`. Raises `ValueError: "switch_function and per-port conditions are mutually exclusive"`. [D-SF1-28]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema`. `output_type` declares the shape of data produced by `merge_function` (or the passthrough shape if no merge). `input_type` declares what each input port expects (useful for gather nodes receiving typed data).

**NOT fields:** No `actor` [D-28].

**Port routing [D-SF1-2, D-SF1-28]:**
- **With `switch_function`:** Exclusive — function returns port name string, only that port fires. If `merge_function` is also set: inputs merged first → `switch_function` receives merged data → exclusive routing.
- **Without `switch_function`:** Non-exclusive — all output ports whose `condition` evaluates truthy fire simultaneously. This is the default fan-out behavior.
- **Neither set:** All output ports fire (broadcast). Validation emits `missing_output_condition` warning if >1 output port.

#### PluginNode (extends NodeBase, type="plugin") [D-SF1-14, D-SF1-17, D-SF1-19]

Side-effect execution node. 1 fixed input, 0+ outputs (fire-and-forget allowed).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `plugin_ref` | `str \| None` | `None` | mutual exclusion with `instance_ref` [D-SF1-17] | Reference to `workflow.plugins` key |
| `instance_ref` | `str \| None` | `None` | mutual exclusion with `plugin_ref` [D-SF1-17] | Reference to `workflow.plugin_instances` key |
| `config` | `dict \| None` | `None` | only valid with `plugin_ref` | Inline config for plugin type |

**Validators:**
- `_fix_input_ports`: Always single `[PortDefinition(name="input")]`. [D-SF1-14]
- `_check_plugin_ref`: Exactly one of `plugin_ref` or `instance_ref` must be set. [D-SF1-17]

**Inherited from NodeBase [D-SF1-22]:** `input_type`, `input_schema`, `output_type`, `output_schema`. `output_type` declares the plugin's output structure (e.g., `collect_files` returns a file list). `input_type` declares what data the plugin expects.

**Outputs:** Allows `outputs: []` (empty list) for fire-and-forget plugins (e.g., `git_commit_push`). [D-SF1-19]

#### NodeDefinition (discriminated union) [D-SF1-9]

```python
NodeDefinition = Annotated[AskNode | BranchNode | PluginNode, Field(discriminator="type")]
```

#### Edge [D-SF1-15, D-SF1-21]

Single edge type for ALL connections — data edges and hook edges. The source port's container field determines semantics. When the source resolves to a port in a `hooks` list, the edge is a hook edge (fire-and-forget, no transform). When the source resolves to a port in an `outputs` list, the edge is a data edge (optional transform).

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `source` | `str` | — | required | `"node_id.port_name"` or `"$input"` (phase boundary). Port may be in `outputs` (data edge) or `hooks` (hook edge). |
| `target` | `str` | — | required | `"node_id.port_name"` or `"$output"` (phase boundary). Always resolves to an `inputs` port on the target. |
| `transform_fn` | `str \| None` | `None` | Must be `None` when source is a hook port [D-SF1-21] | Inline transform. Receives `data`. Returns transformed value. [D-21] |
| `description` | `str \| None` | `None` | — | Human-readable |

**Hook edge identification at runtime/render time [D-SF1-21]:**
1. Resolve `edge.source` to a port: parse `"node_id.port_name"` → find node → check if `port_name` is in `node.hooks`
2. If yes → hook edge. Render as dashed purple. No transform allowed.
3. If no → data edge. Render as solid with type label. Transform optional.

**Validation rule:** `_check_hook_edge_constraints()` iterates all edges, resolves source ports, and produces `invalid_hook_edge_transform` errors for any hook-sourced edge with non-None `transform_fn`.

#### SequentialConfig

No additional fields. Phase nodes execute in edge-determined order.

#### MapConfig

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `collection` | `str` | — | required, Python expression | Evaluates to `Iterable`. Receives `ctx` (context keys + phase input). [D-SF1-15] |
| `max_parallelism` | `int \| None` | `None` | positive int | Concurrency limit. `None` = unlimited. |

**Session behavior:** Runner auto-creates unique actor instances per parallel execution. Sessions inherently isolated. No `fresh_sessions` field. [D-SF1-16]

#### FoldConfig

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `collection` | `str` | — | required, Python expression | Evaluates to `Iterable`. Receives `ctx`. [D-SF1-15] |
| `accumulator_init` | `str` | — | required, Python expression | Initial accumulator value. No variables available. [D-SF1-15] |
| `reducer` | `str` | — | required, Python expression | Combines `accumulator` + `result` (iteration output). [D-SF1-15] |
| `fresh_sessions` | `bool` | `False` | — | When True, runner clears all agent actor sessions before each iteration. [D-SF1-16] |

#### LoopConfig [D-SF1-5]

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `exit_condition` | `str` | — | required, Python expression | Evaluates against `data` ($output). True → exit via `condition_met`. False → re-execute. [D-SF1-5, D-SF1-15] |
| `max_iterations` | `int \| None` | `None` | positive int | Enables `max_exceeded` exit port when set. |
| `fresh_sessions` | `bool` | `False` | — | When True, runner clears all agent actor sessions before each iteration. [D-SF1-16] |

#### PhaseDefinition [D-SF1-4, D-SF1-11, D-SF1-21, D-SF1-22]

Primary container for DAG execution. 4 modes with strict I/O boundary. Single `edges` list contains both data edges and hook edges [D-SF1-21]. I/O type declarations for phase-level type-checking [D-SF1-22].

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required, unique within workflow | Phase identifier |
| `mode` | `Literal["sequential", "map", "fold", "loop"]` | — | required | Execution mode |
| `sequential_config` | `SequentialConfig \| None` | `None` | required when mode="sequential" | Sequential mode config |
| `map_config` | `MapConfig \| None` | `None` | required when mode="map" | Map mode config |
| `fold_config` | `FoldConfig \| None` | `None` | required when mode="fold" | Fold mode config |
| `loop_config` | `LoopConfig \| None` | `None` | required when mode="loop" | Loop mode config |
| `nodes` | `list[NodeDefinition]` | `[]` | — | Internal nodes |
| `edges` | `list[Edge]` | `[]` | — | ALL internal edges — both data edges and hook edges [D-SF1-21] |
| `phases` | `list[PhaseDefinition]` | `[]` | — | Nested phases (recursive) |
| `input_type` | `str \| None` | `None` | mutual exclusion with `input_schema` [D-SF1-22] | Reference to `workflow.types` key — expected phase input structure |
| `input_schema` | `dict \| None` | `None` | mutual exclusion with `input_type` [D-SF1-22] | Inline JSON Schema — expected phase input structure |
| `output_type` | `str \| None` | `None` | mutual exclusion with `output_schema` [D-SF1-22] | Reference to `workflow.types` key — produced phase output structure |
| `output_schema` | `dict \| None` | `None` | mutual exclusion with `output_type` [D-SF1-22] | Inline JSON Schema — produced phase output structure |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Phase input ports [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Phase output ports [D-SF1-11] |
| `hooks` | `list[PortDefinition]` | `[PortDefinition(name="on_start"), PortDefinition(name="on_end")]` | — | Phase hook ports [D-SF1-11] |
| `summary` | `str \| None` | `None` | max ~120 chars | 1–2 line description [D-32] |
| `context_keys` | `list[str]` | `[]` | dot notation refs to stores | Phase-scoped context keys inherited by child nodes [D-SF1-24] |
| `context_text` | `dict[str, str]` | `{}` | — | Phase-scoped inline text snippets [D-SF1-24] |
| `position` | `dict[str, float] \| None` | `None` | `{"x": float, "y": float}` | Canvas position (UI-only) |

**Validators:**
- Mode requires corresponding config (e.g., `mode="fold"` requires `fold_config` non-None).
- Loop mode auto-creates dual exit ports: `condition_met` (green) + `max_exceeded` (amber) on outputs. [J-6]
- `_check_input_config`: Raises if both `input_type` and `input_schema` are set. [D-SF1-22]
- `_check_output_config`: Raises if both `output_type` and `output_schema` are set. [D-SF1-22]

**Edge classification [D-SF1-21]:** The single `edges` list contains all edges. To determine if an edge is a hook edge, resolve `edge.source` and check if the port lives in the source element's `hooks` container. This is done by validation (`_check_hook_edge_constraints`) and by the UI at render time.

#### RoleDefinition

Mirrors `iriai_compose.actors.Role` exactly.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Role name |
| `prompt` | `str` | — | required | System prompt (markdown) |
| `tools` | `list[str]` | `[]` | — | Tool names (Read, Edit, Write, Bash, etc.) |
| `model` | `str \| None` | `None` | — | Model identifier (e.g., `claude-opus-4-20250514`) |
| `effort` | `Literal["low", "medium", "high", "max"] \| None` | `None` | — | Effort level hint |
| `metadata` | `dict[str, Any]` | `{}` | — | Arbitrary key-value metadata |

#### ActorDefinition [D-SF1-16]

Agent or interaction actor. Session persistence is an actor property; session clearing is a phase property.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type` | `Literal["agent", "interaction"]` | — | required | Actor type |
| `role` | `RoleDefinition \| None` | `None` | required when type="agent" | Agent role definition |
| `resolver` | `str \| None` | `None` | required when type="interaction" | Interaction runtime resolver name |
| `context_keys` | `list[str]` | `[]` | — | Default context keys injected into every invocation |
| `persistent` | `bool` | `True` | — | Whether session survives workflow restarts (actor property) |
| `context_text` | `dict[str, str]` | `{}` | — | Inline text snippets for this actor's context [D-SF1-24] |
| `context_store` | `str \| None` | `None` | must reference `workflow.stores` key | Default store for context resolution [D-SF1-25] |
| `handover_key` | `str \| None` | `None` | dot notation store ref | Store location for actor's handover document [D-SF1-25] |

**Validators:**
- `_check_actor_type`: agent requires `role` non-None; interaction requires `resolver` non-None.

**NOT fields:** No `fresh_session` or `fresh_sessions`. Session clearing is on LoopConfig/FoldConfig [D-SF1-16].

#### TypeDefinition

Named output type definition with JSON Schema.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Type name (referenced by `NodeBase.input_type`/`output_type`, `PortDefinition.type_ref`) |
| `schema_def` | `dict` | — | required, valid JSON Schema Draft 2020-12 | JSON Schema for the type |
| `description` | `str \| None` | `None` | — | Human-readable |

#### CostConfig

Workflow-level cost tracking configuration.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `max_tokens` | `int \| None` | `None` | positive int | Token budget limit |
| `max_usd` | `float \| None` | `None` | positive float | USD budget limit |
| `track_by` | `Literal["node", "phase", "workflow"]` | `"workflow"` | — | Cost tracking granularity |

#### PluginInterface [D-SF1-11]

Plugin type definition. Defines the interface (I/O, config schema) that instances implement.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required | Plugin type identifier |
| `name` | `str` | — | required | Human-readable name |
| `description` | `str \| None` | `None` | — | Description |
| `inputs` | `list[PortDefinition]` | `[PortDefinition(name="input")]` | — | Input port interface [D-SF1-11] |
| `outputs` | `list[PortDefinition]` | `[PortDefinition(name="output")]` | — | Output port interface [D-SF1-11] |
| `config_schema` | `dict \| None` | `None` | valid JSON Schema | Schema for instance config |
| `category` | `Literal["service", "mcp", "cli", "plugin"] \| None` | `None` | — | Plugin category |

#### PluginInstanceConfig [D-SF1-17]

Pre-configured plugin instance. Referenced by `PluginNode.instance_ref`.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `id` | `str` | — | required | Instance identifier |
| `name` | `str` | — | required | Human-readable name |
| `plugin_type` | `str` | — | required, must reference `PluginInterface.id` | Plugin type this instantiates |
| `config` | `dict` | `{}` | must validate against plugin type's `config_schema` | Instance configuration |

#### TemplateRef

Reference to a reusable task template with actor slot bindings.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `template_id` | `str` | — | required | Reference to external template |
| `bindings` | `dict[str, str]` | `{}` | — | Maps template actor slot names → workflow actor names |

#### StoreDefinition [D-SF1-23]

Named store declaration. Runner instantiates the actual implementation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `description` | `str \| None` | `None` | — | Human-readable store description |
| `keys` | `dict[str, StoreKeyDefinition] \| None` | `None` | None = open store | Declared keys with optional types. None means open store — any key accepted. |

#### StoreKeyDefinition [D-SF1-23]

Individual key declaration within a store.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected value type. None = untyped (accepts Any). |
| `description` | `str \| None` | `None` | — | Human-readable key description |

#### WorkflowInputDefinition [D-SF1-30]

Declares an expected input to the workflow. SF-2 validates all required inputs are provided at `run()` time.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Input parameter name |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected input type. None = untyped (accepts Any). |
| `required` | `bool` | `True` | — | Whether this input must be provided at run time |
| `default` | `Any` | `None` | Only valid when `required=False` | Default value when input not provided |
| `description` | `str \| None` | `None` | — | Human-readable description |

#### WorkflowOutputDefinition [D-SF1-30]

Declares an expected output from the workflow. SF-2 validates all declared outputs are produced by execution completion.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `name` | `str` | — | required | Output parameter name |
| `type_ref` | `str \| None` | `None` | Must reference `workflow.types` key if set | Expected output type. None = untyped. |
| `description` | `str \| None` | `None` | — | Human-readable description |

#### WorkflowConfig (root model) [D-SF1-6, D-SF1-21, D-SF1-30]

Top-level workflow definition. Everything referenced by name (actors, types, plugins) uses dict keys. Single `edges` list for all cross-phase connections (both data and hook edges). Workflow-level I/O declarations for SF-2 validation.

| Field | Type | Default | Constraints | Description |
|-------|------|---------|-------------|-------------|
| `schema_version` | `str` | `"1.0"` | semver string [D-SF1-6] | Schema format version |
| `name` | `str` | — | required | Workflow name |
| `description` | `str \| None` | `None` | — | Workflow description |
| `inputs` | `list[WorkflowInputDefinition]` | `[]` | — | Workflow-level input declarations. SF-2 validates required inputs provided at run time. [D-SF1-30] |
| `outputs` | `list[WorkflowOutputDefinition]` | `[]` | — | Workflow-level output declarations. SF-2 validates all declared outputs produced. [D-SF1-30] |
| `actors` | `dict[str, ActorDefinition]` | `{}` | — | Named actor definitions |
| `types` | `dict[str, TypeDefinition]` | `{}` | — | Named output type definitions |
| `phases` | `list[PhaseDefinition]` | `[]` | — | Top-level phases |
| `edges` | `list[Edge]` | `[]` | — | ALL top-level (cross-phase) edges — both data and hook [D-SF1-21] |
| `plugins` | `dict[str, PluginInterface]` | `{}` | — | Plugin type definitions |
| `plugin_instances` | `dict[str, PluginInstanceConfig]` | `{}` | — | Pre-configured plugin instances |
| `cost` | `CostConfig \| None` | `None` | — | Cost tracking config |
| `templates` | `dict[str, TemplateRef]` | `{}` | — | Template references with bindings |
| `stores` | `dict[str, StoreDefinition]` | `{}` | — | Named store declarations. Runner instantiates implementations. [D-SF1-23] |
| `context_keys` | `list[str]` | `[]` | dot notation refs to stores | Global context keys inherited by all nodes [D-SF1-24] |
| `context_text` | `dict[str, str]` | `{}` | — | Global inline text snippets injected into all context [D-SF1-24] |

### Entity Relations

| From Entity | To Entity | Kind | Label |
|-------------|-----------|------|-------|
| WorkflowConfig | ActorDefinition | one-to-many | `actors` dict values |
| WorkflowConfig | TypeDefinition | one-to-many | `types` dict values |
| WorkflowConfig | PhaseDefinition | one-to-many | `phases` list |
| WorkflowConfig | Edge | one-to-many | top-level `edges` (data + hook) |
| WorkflowConfig | PluginInterface | one-to-many | `plugins` dict values |
| WorkflowConfig | PluginInstanceConfig | one-to-many | `plugin_instances` dict values |
| WorkflowConfig | CostConfig | one-to-one | `cost` config |
| PhaseDefinition | NodeDefinition | one-to-many | internal `nodes` |
| PhaseDefinition | Edge | one-to-many | internal `edges` (data + hook) |
| PhaseDefinition | PhaseDefinition | one-to-many | nested `phases` (recursive) |
| PhaseDefinition | PortDefinition | one-to-many | `inputs`, `outputs`, `hooks` |
| NodeBase | PortDefinition | one-to-many | `inputs`, `outputs`, `hooks` |
| NodeBase | TypeDefinition | many-to-many | `input_type`/`output_type` reference `types` dict keys [D-SF1-22] |
| PhaseDefinition | TypeDefinition | many-to-many | `input_type`/`output_type` reference `types` dict keys [D-SF1-22] |
| AskNode | ActorDefinition | many-to-many | `actor` references `actors` dict key |
| PluginNode | PluginInterface | many-to-many | `plugin_ref` references `plugins` dict key |
| PluginNode | PluginInstanceConfig | many-to-many | `instance_ref` references `plugin_instances` dict key |
| PluginInstanceConfig | PluginInterface | many-to-many | `plugin_type` references `PluginInterface.id` |
| PluginInterface | PortDefinition | one-to-many | `inputs`, `outputs` |
| ActorDefinition | RoleDefinition | one-to-one | `role` (for agent type) |
| Edge | PortDefinition | many-to-many | `source`/`target` reference port names via dot notation — source may be `outputs` (data) or `hooks` (hook) |
| WorkflowConfig | StoreDefinition | one-to-many | `stores` dict values |
| StoreDefinition | StoreKeyDefinition | one-to-many | `keys` dict values (if not open store) |
| StoreKeyDefinition | TypeDefinition | many-to-many | `type_ref` references `types` dict keys |
| NodeBase | StoreDefinition | many-to-many | `artifact_key` + `context_keys` reference stores via dot notation [D-SF1-26] |
| ActorDefinition | StoreDefinition | many-to-many | `context_store` + `handover_key` reference stores [D-SF1-25] |
| WorkflowConfig | WorkflowInputDefinition | one-to-many | `inputs` list [D-SF1-30] |
| WorkflowConfig | WorkflowOutputDefinition | one-to-many | `outputs` list [D-SF1-30] |
| WorkflowInputDefinition | TypeDefinition | many-to-many | `type_ref` references `types` dict keys [D-SF1-30] |
| WorkflowOutputDefinition | TypeDefinition | many-to-many | `type_ref` references `types` dict keys [D-SF1-30] |

### Architecture Decisions

1. **Pure data layer — zero runtime coupling.** The `iriai_compose/schema/` package imports nothing from `iriai_compose.actors`, `iriai_compose.tasks`, `iriai_compose.runner`, or `iriai_compose.workflow`. Standalone Pydantic v2 models that mirror field names from the runtime classes. [code: iriai-compose/iriai_compose/actors.py:8-16]
2. **Single PortDefinition type eliminates port-type explosion.** [D-SF1-10]
3. **Single Edge type eliminates edge-type explosion.** [D-SF1-21] — mirrors PortDefinition unification. Hook vs data determined by source port container, not edge type.
4. **Three node types + four phase modes = complete representation.** Validated against all 145+ nodes across 3 workflows. [D-SF1-3, D-SF1-12]
5. **Dual routing model: per-port conditions AND switch_function.** Per-port conditions handle data-driven branching (non-exclusive fan-out on Branch, mutually exclusive first-match on Ask/Plugin). `switch_function` on BranchNode handles exclusive programmatic routing (returns port name string). Both coexist at the schema level but are mutually exclusive on a given BranchNode. [D-SF1-2, D-SF1-28]
6. **Strict phase I/O boundary enforces encapsulation.** [D-SF1-4]
7. **Expression strings with documented evaluation contexts.** [D-SF1-15]
8. **`fresh_sessions` on LoopConfig/FoldConfig for phase-iteration session management.** Session clearing is a phase lifecycle concern. Same actor participates in both persistent and fresh contexts depending on which phase it's in. [D-SF1-16]
9. **PluginNode 0+ outputs for fire-and-forget.** Side-effect plugins (git_commit_push, preview_cleanup) don't produce data. Empty outputs list eliminates spurious dangling-edge validation errors and makes the DAG semantically accurate. [D-SF1-19]
10. **Async gather is runner-only.** The schema declares ports as static data. The runner (SF-2) implements the barrier/join behavior when a BranchNode has multiple input ports. This separation keeps SF-1 as a pure data layer. [D-SF1-20]
11. **I/O type declarations on NodeBase and PhaseDefinition — not AskNode-specific.** All nodes and phases can declare their expected input and produced output data structures. Port-level `type_ref` takes precedence for multi-port granularity. Type resolution is a helper function used by validation (`_check_type_flow`) and downstream consumers (SF-2, SF-6). [D-SF1-22]
12. **Store as schema-level entity — pure interface, no implementation.** The `stores` dict declares named stores with keys and types. The runner instantiates actual implementations (Postgres, filesystem, in-memory). No `mirror_path` or other implementation config in schema. [D-SF1-23]
13. **Dot notation for all store references.** `artifact_key`, `context_keys`, `handover_key` all use `"store_name.key_name"` format. No separate `store` field on nodes. [D-SF1-26]
14. **Four-level context hierarchy — workflow → phase → actor → node.** `context_keys` (store refs) and `context_text` (inline text) at each level. Merged at runtime with deduplication. [D-SF1-24]
15. **Context store bindings on actors — declarative, not strategic.** `context_store` and `handover_key` declare WHERE an actor reads/writes. HOW (compaction strategy) is SF-2. [D-SF1-25]
16. **Artifact hosting as DAG topology.** Node → on_end hook → doc_hosting plugin. No schema-level hosting config. [D-SF1-27]
17. **`artifact_key` auto-write semantics.** Runner auto-writes node output to store at `artifact_key` after execution — before output port routing. Replaces explicit `artifacts.put()` calls. Fewer PluginNodes needed for simple artifact storage. [D-SF1-29]
18. **Workflow-level I/O declarations.** `inputs` and `outputs` on WorkflowConfig declare what the workflow expects and produces. SF-2 validates at run time. `type_ref` references `workflow.types`. [D-SF1-30]
19. **`switch_function` on BranchNode for exclusive programmatic routing.** Receives `data`, returns port name string. Mutually exclusive with per-port `condition` on the same node. `merge_function` is orthogonal (input merging). [D-SF1-28]

### Architecture Risks

| ID | Description | Severity | Mitigation | Affected Steps |
|----|-------------|----------|------------|----------------|
| RISK-1 | Phase boundary model may be too strict for some iriai-build-v2 patterns (e.g., PlanReviewPhase has two conceptual steps that map to two sequential phases) | medium | Start strict; relax if SF-4 discovers gaps (J-14). `per_subfeature_loop`'s cross-iteration data access validated as representable via Fold accumulator. | STEP-4, STEP-6 |
| RISK-2 | Per-port condition strings may be error-prone for users to author | low | SF-6 provides condition builder. SF-2 validates syntax at execution. Common patterns validated: `"data.complete"`, `"data is True"`, `"data.verdict == 'approved'"`. | STEP-1, STEP-2 |
| RISK-3 | Inline Python in all expression fields is a security concern for untrusted workflows | medium | SF-1 stores opaque strings. SF-2 runner evaluates in restricted context (no imports, no side effects, only documented variables). | STEP-1–4 |
| RISK-4 | Pydantic v2 model_json_schema() output may need post-processing for React JSON Schema Form | low | SF-6 may need thin adapter. | STEP-5, STEP-7 |
| RISK-5 | YAML key ordering with pyyaml sort_keys=False may not be perfectly stable | low | Acceptable for SF-1. SF-3 snapshot tests use ruamel.yaml if needed. | STEP-7 |
| RISK-6 | Default ports serialize verbose YAML (every node emits inputs/outputs/hooks) | low | Use `exclude_defaults=False` for correctness. Consider `exclude_defaults=True` if verbosity is an issue. | STEP-7, STEP-8 |
| RISK-7 | Branch non-exclusive fan-out may create unexpected parallel execution if conditions overlap | medium | Validation warns when conditions missing. Documentation explicit. Validated against `runner.parallel()` pattern. Runner implements barrier for multi-input gather [D-SF1-20]. | STEP-2, STEP-6 |
| RISK-8 | Phase-level `fresh_sessions` is blanket — clears ALL agent sessions in the phase per iteration, even if only one actor needs it | low | In practice, `_clear_agent_session` in iriai-build-v2 is always called at the start of the loop for all agents that will be invoked. InteractionActors are unaffected (uses Pending, not sessions). If selective clearing is needed later, can evolve to `fresh_sessions: list[str]` (actor names). Start simple. | STEP-4 |
| RISK-9 | Collection expression `ctx` may not contain all needed data for complex patterns | medium | Complex patterns use Plugin pre-processing before Map/Fold phase. Validated: `per_subfeature_loop`'s collection accessible via context key. | STEP-4 |
| RISK-10 | Hook-sourced edge identification requires port resolution at validation and render time | low | Port resolution is already needed for all edge validation (dangling edge checks, type flow). Hook classification is an O(1) lookup per edge once the port index is built. No additional traversal cost. | STEP-6 |
| RISK-11 | Node-level `input_type`/`output_type` and port-level `type_ref` could create confusion when both are set on a single-port node | low | Clear precedence rule: port-level `type_ref` always wins. `resolve_port_type()` helper enforces this. Validation can optionally warn if both are set and disagree. | STEP-1, STEP-6 |
| RISK-12 | Open stores allow any key — no compile-time validation of dynamic key references like `"{prefix}:{slug}"` | low | Open stores are intentionally unconstrained for dynamic patterns. Validation only checks store name prefix, not key existence. Runtime errors surface at execution time. | STEP-5, STEP-6 |
| RISK-13 | Four-level context merge may create unexpectedly large prompts if workflow + phase + actor + node all inject context | medium | Runner should track total context size. Context strategy (compaction, summarization) deferred to SF-2 runner config. | STEP-1, STEP-4 |
| RISK-14 | Dot notation parsing may conflict with keys containing dots in their names | low | Store keys should not contain dots. Validation can enforce this. First dot is always the store/key separator. | STEP-6 |
| RISK-15 | `switch_function` returning a port name not in the BranchNode's output ports is a runtime error, not catchable at schema validation time | low | Runtime error produces clear diagnostic: "switch_function returned '{name}' but available ports are [...]". Schema validation ensures at least 1 output port exists. Documentation examples show correct usage. | STEP-2, STEP-6 |
| RISK-16 | `artifact_key` auto-write may cause unexpected store writes if node output is large or structured differently than expected | low | Same risk as explicit `artifacts.put()` in iriai-build-v2 — the schema declares intent, the runner writes. Store key `type_ref` validation (if set) catches type mismatches at validation time. | STEP-1, STEP-6 |
| RISK-17 | Workflow I/O validation at run time may reject valid workflows where outputs are produced conditionally (not all branches reach all output-producing nodes) | medium | SF-2 runner should treat `outputs` as "expected when workflow succeeds" — not "guaranteed on every path". Documentation clarifies. Conditional outputs can be marked optional (future extension). | STEP-5, STEP-6 |

---

## Implementation Steps

### STEP-1: Package Scaffold & Base Models

**Objective:** Create `iriai_compose/schema/` package with `PortDefinition` (including `condition`), `NodeBase` (default ports [D-SF1-11], I/O type fields [D-SF1-22]), `ActorDefinition` (with type validation and `persistent`), `RoleDefinition`, and `TypeDefinition`.

**Scope:**
- `iriai_compose/schema/__init__.py` — create
- `iriai_compose/schema/base.py` — create
- `iriai_compose/schema/types.py` — create
- `iriai_compose/schema/actors.py` — create
- `iriai_compose/pyproject.toml` — modify (add pyyaml, testing extra)
- `iriai_compose/iriai_compose/actors.py` — read
- `iriai_compose/iriai_compose/tasks.py` — read

**Instructions:**

1. Create `iriai_compose/schema/__init__.py` with docstring and placeholder `__all__`.

2. Create `iriai_compose/schema/base.py`: `PortDefinition` (name, type_ref, description, condition), `_default_inputs()`, `_default_outputs()`, `_default_hooks()`, `NodeBase` (id, type, summary, context_keys, context_text, artifact_key, input_type, input_schema, output_type, output_schema, inputs, outputs, hooks, position). All as documented in Architecture entities section.

   NodeBase validators:
   - `_check_input_config`: model_validator that raises `ValueError` if both `input_type` and `input_schema` are set. Message: `"input_type and input_schema are mutually exclusive"`.
   - `_check_output_config`: model_validator that raises `ValueError` if both `output_type` and `output_schema` are set. Message: `"output_type and output_schema are mutually exclusive"`.

3. Create `iriai_compose/schema/actors.py`: `RoleDefinition` (name, prompt, tools, model, effort, metadata — matching `iriai_compose.actors.Role` exactly), `ActorDefinition` (type, role, resolver, context_keys, persistent) with:
   - `persistent: bool = True` — when True, sessions survive workflow restarts. This is an actor property.
   - `_check_actor_type` model_validator that enforces: agent requires role, interaction requires resolver.

4. Create `iriai_compose/schema/types.py`: `TypeDefinition` (name, schema_def, description).

5. Add `pyyaml>=6.0` to `pyproject.toml` main dependencies. Add `testing = ["pyyaml>=6.0"]` optional dependency group.

**Acceptance Criteria:**
- `from iriai_compose.schema.base import NodeBase, PortDefinition` succeeds
- `RoleDefinition` fields exactly match `iriai_compose.actors.Role` (name, prompt, tools, model, effort, metadata)
- `NodeBase.hooks` uses `PortDefinition` — no `HookDefinition` type exists [D-SF1-10]
- `PortDefinition` includes `condition: str | None = None` [D-SF1-2]
- `NodeBase()` defaults match [D-SF1-11]
- `NodeBase` has `context_text: dict[str, str]` defaulting to `{}` [D-SF1-24]
- NodeBase has `input_type`, `input_schema`, `output_type`, `output_schema` — all `str | None` or `dict | None` defaulting to `None` [D-SF1-22]
- `NodeBase(input_type="PRD", input_schema={"type": "object"})` raises ValidationError (mutual exclusion)
- `NodeBase(output_type="PRD", output_schema={"type": "object"})` raises ValidationError (mutual exclusion)
- `NodeBase(input_type="PRD", output_type="TechnicalPlan")` succeeds — cross-pair is fine
- `ActorDefinition(type="agent")` without role raises ValidationError
- `ActorDefinition(type="interaction")` without resolver raises ValidationError
- ActorDefinition fields: type, role, resolver, context_keys, persistent (NO `fresh_session` — session clearing is on phase configs [D-SF1-16])
- `pip install -e .` succeeds with pyyaml

**Counterexamples:**
- Do NOT create `HookDefinition` [D-SF1-10]
- Do NOT create `HookEdge` [D-SF1-21]
- Do NOT import from `iriai_compose.actors` at runtime
- Do NOT add `ruamel.yaml`
- Do NOT add `kind` field to `PortDefinition`
- Do NOT default inputs/outputs to empty lists [D-SF1-11]
- Do NOT put `fresh_session` or `fresh_sessions` on ActorDefinition — session clearing is a phase iteration concern on LoopConfig/FoldConfig [D-SF1-16]
- Do NOT put `output_type`/`output_schema` on AskNode — they are on NodeBase, inherited by all types [D-SF1-22]

---

### STEP-2: Node Type Models (Ask, Branch, Plugin)

**Objective:** Three node types as discriminated union. All inherit `input_type`/`input_schema`/`output_type`/`output_schema` from NodeBase [D-SF1-22]. PluginNode with mutually exclusive plugin_ref/instance_ref [D-SF1-17] and 0+ outputs for fire-and-forget [D-SF1-19]. BranchNode with `switch_function` for exclusive routing [D-SF1-28], `merge_function` for input merging, and 1+ inputs/outputs [D-SF1-13].

**Scope:**
- `iriai_compose/schema/nodes.py` — create
- `iriai_compose/schema/base.py` — read

**Instructions:**

Create `iriai_compose/schema/nodes.py` with `AskNode`, `BranchNode`, `PluginNode`, and `NodeDefinition` discriminated union as documented in Architecture entities section. Include all validators:
- AskNode: `_fix_input_ports` (fixed single input). No output_type/output_schema fields on AskNode itself — these are inherited from NodeBase [D-SF1-22].
- BranchNode: `_validate_branch_ports` (min 1 input, min 1 output) — no upper bounds. Gather semantics when >1 input (runner implements barrier [D-SF1-20]). `switch_function: str | None = None` for exclusive routing [D-SF1-28]. `merge_function: str | None = None` for input merging (orthogonal to routing). `_validate_switch_function_config` model_validator: if `switch_function` is set, iterate `outputs` and raise `ValueError("switch_function and per-port conditions are mutually exclusive")` if any output port has `condition is not None`.
- PluginNode: `_fix_input_ports` (fixed single input), `_check_plugin_ref` (exactly one of plugin_ref/instance_ref). Outputs allow empty list (0+) for fire-and-forget [D-SF1-19] — no `_validate_min_outputs`.

**Acceptance Criteria:**
- AskNode default ports work [D-SF1-11]
- AskNode inherits input_type/input_schema/output_type/output_schema from NodeBase [D-SF1-22]
- `AskNode(id="a", type="ask", actor="pm", prompt="...", output_type="PRD")` works — output_type inherited from NodeBase
- `AskNode(id="a", type="ask", actor="pm", prompt="...", output_type="PRD", output_schema={"type":"object"})` raises — mutual exclusion from NodeBase
- AskNode input always fixed [D-SF1-12]
- BranchNode min 1 input, min 1 output enforced [D-SF1-13] — both can be exactly 1
- BranchNode has NO actor field [D-28]
- BranchNode has `switch_function: str | None = None` [D-SF1-28]
- BranchNode has `merge_function: str | None = None`
- `BranchNode(id="b", type="branch", switch_function="data.next_step", outputs=[PortDefinition(name="step_a"), PortDefinition(name="step_b")])` validates — exclusive routing with no conditions
- `BranchNode(id="b", type="branch", switch_function="data.x", outputs=[PortDefinition(name="a", condition="data.x")])` raises — switch_function + condition conflict [D-SF1-28]
- `BranchNode(id="b", type="branch", merge_function="...", switch_function="...")` validates — merge_function is orthogonal to routing
- BranchNode inherits output_type/output_schema from NodeBase — can declare merged output shape
- PluginNode plugin_ref/instance_ref mutual exclusion [D-SF1-17]
- PluginNode input always fixed [D-SF1-14]
- PluginNode with `outputs: []` (empty) validates — fire-and-forget [D-SF1-19]
- PluginNode inherits output_type from NodeBase — can declare plugin output shape (e.g., `output_type: "FileList"`)
- NodeDefinition discriminated union round-trips

**Counterexamples:**
- Do NOT add actor to BranchNode [D-28]
- Do NOT add options to AskNode [D-SF1-12]
- Do NOT create Map/Fold/Loop node types [D-SF1-4]
- Do NOT add output_type/output_schema/input_type/input_schema as AskNode-specific fields — they are on NodeBase [D-SF1-22]
- Do NOT allow `switch_function` + per-port `condition` on the same BranchNode — they are mutually exclusive routing strategies [D-SF1-28]

---

### STEP-3: Edge Model (Unified Data + Hook)

**Objective:** Define a single `Edge` model that serves for both data edges (with optional inline transform) and hook edges (fire-and-forget, no transform). No separate `HookEdge` type [D-SF1-21].

**Scope:**
- `iriai_compose/schema/edges.py` — create
- `iriai_compose/schema/base.py` — read

**Instructions:**

Create `iriai_compose/schema/edges.py` with a single `Edge` model as documented in the Architecture entities section. The `Edge` has: `source`, `target`, `transform_fn` (optional), `description` (optional).

Add a module-level docstring explaining the unified edge model:
- Data edges: source is `"node_id.port_name"` where port_name resolves to a port in the source element's `outputs` list. `transform_fn` is optional.
- Hook edges: source is `"node_id.hook_name"` where hook_name resolves to a port in the source element's `hooks` list. `transform_fn` must be `None`. The validation module (STEP-6) enforces this constraint.
- `$input` and `$output` are pseudo-ports for phase boundary wiring.

Add a helper function `is_hook_source(source_str: str, port_index: dict) -> bool` that takes a source string and a pre-built port index (mapping `"node_id.port_name"` → container name like `"outputs"` or `"hooks"`) and returns `True` if the source resolves to a hook port. This helper is used by validation (STEP-6) and can be used by downstream consumers (SF-2 runner, SF-6 UI).

Also add a helper function `parse_port_ref(ref: str) -> tuple[str, str]` that splits `"node_id.port_name"` into `(node_id, port_name)`. Returns `("$input", "")` or `("$output", "")` for phase boundary pseudo-ports.

**Acceptance Criteria:**
- `Edge` with `transform_fn` stores inline Python [D-21]
- `Edge` without `transform_fn` (None) is valid — used for hook edges
- `$input`/$`$output` pseudo-ports parse correctly via `parse_port_ref`
- `is_hook_source` returns True for hook port refs, False for output port refs
- No `HookEdge` class exists anywhere [D-SF1-21]

**Counterexamples:**
- Do NOT create `HookEdge` as a separate model [D-SF1-21]
- Do NOT add `transform_ref` [D-21]
- Do NOT add `edge_type` or `is_hook` discriminator field to `Edge` — the source port determines semantics [D-SF1-21]
- Do NOT add model-level validation of `transform_fn` on `Edge` itself — the Edge model doesn't know which ports are hooks. That's the validation module's job (STEP-6), which has access to the full workflow context.

---

### STEP-4: Phase Definition Model

**Objective:** PhaseDefinition with 4 modes, single `edges` list (no separate `hook_edges` [D-SF1-21]), I/O type fields [D-SF1-22]. All PhaseConfig expression fields include description with eval context documentation [D-SF1-15]. Loop auto-creates dual exit ports. `fresh_sessions: bool = False` on LoopConfig and FoldConfig [D-SF1-16].

**Scope:**
- `iriai_compose/schema/phases.py` — create
- `iriai_compose/schema/base.py` — read
- `iriai_compose/schema/nodes.py` — read
- `iriai_compose/schema/edges.py` — read

**Instructions:** Create SequentialConfig, MapConfig, FoldConfig, LoopConfig, PhaseDefinition as documented in Architecture entities. All expression fields (collection, accumulator_init, reducer, exit_condition) have Field descriptions documenting evaluation contexts per the Expression Evaluation Contexts table. Add `fresh_sessions: bool = False` to LoopConfig and FoldConfig — when True, runner clears all agent actor sessions used within the phase before each iteration [D-SF1-16].

PhaseDefinition has a single `edges: list[Edge]` field — no `hook_edges` field. Both data edges and hook edges go in this list [D-SF1-21].

PhaseDefinition has `input_type`, `input_schema`, `output_type`, `output_schema` with the same mutual exclusion validators as NodeBase [D-SF1-22].

**Acceptance Criteria:**
- Loop auto-creates condition_met + max_exceeded ports [J-6]
- Fold without fold_config raises
- Phase ports match NodeBase defaults [D-SF1-11]
- FoldConfig, LoopConfig, MapConfig expression fields have description with eval context [D-SF1-15]
- LoopConfig with `fresh_sessions=True` stores correctly [D-SF1-16]
- FoldConfig with `fresh_sessions=True` stores correctly [D-SF1-16]
- PhaseDefinition has ONE `edges` field, not separate `edges` + `hook_edges` [D-SF1-21]
- PhaseDefinition has `input_type`, `input_schema`, `output_type`, `output_schema` [D-SF1-22]
- `PhaseDefinition(id="p", mode="fold", fold_config=..., input_type="SubfeatureList", output_type="ProcessedResult")` works
- `PhaseDefinition(id="p", mode="fold", fold_config=..., input_type="X", input_schema={"type":"object"})` raises (mutual exclusion)
- A PhaseDefinition with edges whose sources reference hook ports is valid at the model level (constraint enforcement is in STEP-6 validation)

**Counterexamples:**
- Do NOT create separate phase subclasses per mode
- Do NOT put loop exit logic in BranchNode [D-SF1-5]
- Do NOT put `fresh_sessions` on SequentialConfig or MapConfig — sequential has no iterations, map already isolates sessions [D-SF1-16]
- Do NOT add `hook_edges: list[Edge]` or `hook_edges: list[HookEdge]` field [D-SF1-21]

---

### STEP-5: WorkflowConfig Root Model & JSON Schema Generation

**Objective:** Root WorkflowConfig with single `edges` list [D-SF1-21], CostConfig, PluginInterface, PluginInstanceConfig, TemplateRef, WorkflowInputDefinition, WorkflowOutputDefinition [D-SF1-30]. Populate `__init__.py` with all re-exports.

**Scope:**
- `iriai_compose/schema/workflow.py` — create
- `iriai_compose/schema/cost.py` — create
- `iriai_compose/schema/templates.py` — create
- `iriai_compose/schema/stores.py` — create
- `iriai_compose/schema/plugins.py` — create
- `iriai_compose/schema/__init__.py` — modify

**Instructions:** Create all models as documented in Architecture entities. PluginInterface uses PortDefinition with same defaults as NodeBase [D-SF1-11]. WorkflowConfig has a single `edges: list[Edge]` field for all top-level connections [D-SF1-21]. Create `iriai_compose/schema/stores.py`: `StoreDefinition` (description, keys) and `StoreKeyDefinition` (type_ref, description). Add `stores`, `context_keys`, `context_text` to WorkflowConfig. Create `WorkflowInputDefinition` and `WorkflowOutputDefinition` in `workflow.py` [D-SF1-30] — add `inputs: list[WorkflowInputDefinition] = []` and `outputs: list[WorkflowOutputDefinition] = []` to WorkflowConfig. Update `__init__.py` with complete `__all__` exports including new models.

**Acceptance Criteria:**
- `from iriai_compose.schema import WorkflowConfig, WorkflowInputDefinition, WorkflowOutputDefinition` succeeds
- `WorkflowConfig.model_json_schema()` produces valid JSON Schema
- No `HookEdge`, `hook_edges`, TransformRef, or options in schema
- `switch_function` present on BranchNode in schema [D-SF1-28]
- `fresh_sessions` on LoopConfig/FoldConfig in schema (NOT on ActorDefinition)
- `instance_ref` on PluginNode present in schema
- WorkflowConfig has ONE `edges` field (type `list[Edge]`), not `edges` + `hook_edges` [D-SF1-21]
- WorkflowConfig has `inputs: list[WorkflowInputDefinition]` and `outputs: list[WorkflowOutputDefinition]` [D-SF1-30]
- `WorkflowInputDefinition` has fields: `name` (str, required), `type_ref` (str|None), `required` (bool, default True), `default` (Any, default None), `description` (str|None)
- `WorkflowOutputDefinition` has fields: `name` (str, required), `type_ref` (str|None), `description` (str|None)
- `WorkflowConfig(name="test", inputs=[WorkflowInputDefinition(name="feature", type_ref="Feature", required=True)])` works
- JSON Schema shows a single Edge definition (no HookEdge definition)
- JSON Schema shows `input_type`, `input_schema`, `output_type`, `output_schema` on NodeBase (inherited by all node types) AND on PhaseDefinition [D-SF1-22]
- JSON Schema shows `inputs` and `outputs` arrays on WorkflowConfig with proper definitions [D-SF1-30]
- JSON Schema does NOT show `output_type`/`output_schema` as AskNode-specific fields — they are on the base

**Counterexamples:**
- Do NOT add transforms registry [D-21]
- Do NOT add version history [D-17]
- Do NOT import from iriai_compose.tasks or .actors
- Do NOT add `hook_edges` field to WorkflowConfig [D-SF1-21]

---

### STEP-6: Structural Validation

**Objective:** Validation functions returning `list[ValidationError]`. 21 checks including hook edge constraint enforcement [D-SF1-21], I/O config mutual exclusion [D-SF1-22], type reference validation, type flow checking with `resolve_port_type()` helper, `switch_function` config validation [D-SF1-28], workflow I/O type ref validation [D-SF1-30], and lenient required field checking.

**Scope:**
- `iriai_compose/schema/validation.py` — create
- All prior schema modules — read

**Instructions:** Create `ValidationError` dataclass and `validate_workflow`, `validate_type_flow`, `detect_cycles` functions. Private `_check_*` helpers for each validation.

**Authoritative error codes (21 total) [H-3]:**

| # | Code | Description | Emitted by |
|---|------|-------------|------------|
| 1 | `dangling_edge` | Edge references nonexistent node/port | `_check_edges` |
| 2 | `duplicate_node_id` | Two nodes share ID within a phase | `_check_duplicate_ids` |
| 3 | `duplicate_phase_id` | Two phases share ID | `_check_duplicate_ids` |
| 4 | `invalid_actor_ref` | Node actor not in `workflow.actors` | `_check_actor_refs` |
| 5 | `invalid_phase_mode_config` | Missing mode-specific config | `_check_phase_configs` |
| 6 | `invalid_hook_edge_transform` | Hook-sourced edge has `transform_fn` | `_check_hook_edge_constraints` |
| 7 | `phase_boundary_violation` | `$input`/`$output` wiring errors | `_check_phase_boundaries` |
| 8 | `cycle_detected` | DAG cycle found | `_check_cycles` |
| 9 | `unreachable_node` | No incoming edges, not phase entry | `_check_reachability` |
| 10 | `type_mismatch` | Edge source output type ≠ target input type | `_check_type_flow` |
| 11 | `invalid_branch_config` | Branch missing min ports | `_check_branch_configs` |
| 12 | `invalid_plugin_ref` | plugin_ref/instance_ref not found | `_check_plugin_refs` |
| 13 | `missing_output_condition` | Multi-port without conditions or switch_function (warning) | `_check_output_port_conditions` |
| 14 | `invalid_io_config` | Both input_type and input_schema set (or both output_*) | `_check_io_configs` |
| 15 | `invalid_type_ref` | Type reference not in `workflow.types` | `_check_type_refs` |
| 16 | `invalid_store_ref` | Store name prefix not in `workflow.stores` | `_check_store_refs` |
| 17 | `invalid_store_key_ref` | Key not in non-open store | `_check_store_key_refs` |
| 18 | `store_type_mismatch` | Node output_type doesn't match store key type_ref | `_check_store_key_types` |
| 19 | `invalid_switch_function_config` | BranchNode has both `switch_function` and per-port `condition` [D-SF1-28] | `_check_switch_function_config` |
| 20 | `invalid_workflow_io_ref` | Workflow input/output `type_ref` not in `workflow.types` [D-SF1-30] | `_check_workflow_io_refs` |
| 21 | `missing_required_field` | Required field missing in lenient loading path | `_check_required_fields` |

This list is authoritative for SF-3's test fixtures. Every code must have at least one test fixture that triggers it.

Port resolution [D-SF1-10]: Build a port index mapping `"node_id.port_name"` → `{"container": "outputs"|"inputs"|"hooks", "port": PortDefinition}`. Use this index for all edge validation.

Hook edge constraint [D-SF1-21]: `_check_hook_edge_constraints()` iterates all edges (both phase-internal and top-level), resolves each `edge.source` via the port index. If the source port's container is `"hooks"` and `edge.transform_fn is not None`, emit `invalid_hook_edge_transform` error with path and message like `"Hook edge from '{source}' must not have transform_fn — hook edges are fire-and-forget"`.

Phase boundary [D-SF1-4]: verify $input and $output wiring.
Plugin refs [D-SF1-17]: check plugin_ref in workflow.plugins OR instance_ref in workflow.plugin_instances.

I/O config validation [D-SF1-22]: `_check_io_configs()` iterates all nodes and phases. For each, checks that `input_type` and `input_schema` are not both set, and `output_type` and `output_schema` are not both set. Note: the Pydantic model validators on NodeBase/PhaseDefinition already enforce this at construction time, but `_check_io_configs` catches it for lenient loading paths.

Type reference validation [D-SF1-22]: `_check_type_refs()` iterates all nodes and phases. For each `input_type` or `output_type` value, verifies it exists as a key in `workflow.types`. For each `PortDefinition.type_ref`, also verifies. Emits `invalid_type_ref` error if not found.

Type flow with resolution [D-SF1-22]: `_check_type_flow()` uses `resolve_port_type()` helper to determine effective types at each end of a data edge. The helper implements the precedence rule: port-level `type_ref` > node-level `input_type`/`output_type` for single-port elements > None.

Switch function config validation [D-SF1-28]: `_check_switch_function_config()` iterates all BranchNodes. For each with `switch_function is not None`, checks that no output port has `condition is not None`. If conflict found, emits `invalid_switch_function_config` error with path like `"phases[0].nodes[1]"` and message `"BranchNode '{id}' has both switch_function and per-port conditions — these are mutually exclusive routing strategies"`. Note: the Pydantic model validator on BranchNode already catches this at construction time, but `_check_switch_function_config` catches it for lenient loading paths.

Workflow I/O validation [D-SF1-30]: `_check_workflow_io_refs()` iterates `workflow.inputs` and `workflow.outputs`. For each with `type_ref is not None`, verifies the type_ref exists as a key in `workflow.types`. Emits `invalid_workflow_io_ref` error if not found, with path like `"inputs[0].type_ref"` or `"outputs[1].type_ref"`.

Required field validation: `_check_required_fields()` catches required field violations not caught by Pydantic in lenient loading paths. Checks: AskNode has `actor` and `prompt`, PhaseDefinition has `id` and `mode`, loop_config has `exit_condition`, fold_config has `collection`/`accumulator_init`/`reducer`. Emits `missing_required_field` errors.

Also export:
- `build_port_index(config: WorkflowConfig) -> dict[str, PortIndexEntry]` helper for use by downstream consumers (SF-2, SF-6).
- `resolve_port_type(element, port: PortDefinition, is_input: bool) -> str | None` helper for type resolution [D-SF1-22].

**Acceptance Criteria:**
- Valid config → empty list
- Each error code has clear path and message
- Never raises — always returns list
- Port references resolve across all three lists (inputs, outputs, hooks) [D-SF1-10]
- Hook-sourced edge with `transform_fn` → `invalid_hook_edge_transform` error [D-SF1-21]
- Hook-sourced edge with `transform_fn=None` → no error
- Data-sourced edge with `transform_fn` → no error
- `build_port_index` correctly classifies ports by container
- Node with both `input_type` and `input_schema` → `invalid_io_config` error [D-SF1-22]
- Node with both `output_type` and `output_schema` → `invalid_io_config` error [D-SF1-22]
- Node with `output_type: "NonexistentType"` → `invalid_type_ref` error [D-SF1-22]
- `resolve_port_type` returns port-level `type_ref` when set, falls back to node-level type for single-port elements, returns None otherwise
- Edge from node with `output_type: "PRD"` to node with `input_type: "TechnicalPlan"` → `type_mismatch` error
- Edge from node with `output_type: "PRD"` to node with `input_type: "PRD"` → no error
- Edge from node with `output_type: "PRD"` to node with no input_type → no error (untyped is compatible)
- BranchNode with `switch_function` set AND output port with `condition` → `invalid_switch_function_config` error [D-SF1-28]
- BranchNode with `switch_function` set AND no output port conditions → no error [D-SF1-28]
- BranchNode without `switch_function` AND output ports with conditions → no error (normal fan-out)
- `WorkflowInputDefinition(name="x", type_ref="NonexistentType")` in `workflow.inputs` → `invalid_workflow_io_ref` error [D-SF1-30]
- `WorkflowOutputDefinition(name="y", type_ref="ValidType")` where "ValidType" in `workflow.types` → no error
- AskNode without `actor` field in lenient load → `missing_required_field` error

**Counterexamples:**
- Do NOT raise from validate_workflow [J-5]
- Do NOT validate Python syntax of expression fields (including `switch_function` — syntax is SF-2's concern)
- Do NOT validate that `switch_function` return values match output port names — that's a runtime check [RISK-15]
- Do NOT block saving
- Do NOT add `edge_type` or `is_hook` to Edge model — classification happens via port index lookup [D-SF1-21]

---

### STEP-7: YAML Serialization & JSON Schema Export

**Objective:** YAML load/dump and JSON Schema CLI export. Final `__init__.py` updates.

**Scope:**
- `iriai_compose/schema/yaml_io.py` — create
- `iriai_compose/schema/json_schema.py` — create
- `iriai_compose/schema/__init__.py` — modify

**Instructions:** Create `load_workflow`, `load_workflow_lenient`, `dump_workflow` in yaml_io.py. Create `generate_json_schema` with `__main__` CLI in json_schema.py. Update `__init__.py` to export yaml_io, json_schema, and validation functions.

YAML serialization writes all edges (data + hook) into a single `edges` list per phase and at the workflow level [D-SF1-21]. No `hook_edges` key in YAML output.

**Acceptance Criteria:**
- YAML round-trip preserves all fields including `input_type`, `input_schema`, `output_type`, `output_schema` on all node types and phases
- YAML round-trip preserves `switch_function` on BranchNode [D-SF1-28]
- YAML round-trip preserves `inputs` and `outputs` on WorkflowConfig [D-SF1-30]
- JSON Schema includes `input_type`/`input_schema`/`output_type`/`output_schema` on NodeBase (inherited by all node discriminated union members) AND on PhaseDefinition [D-SF1-22]
- JSON Schema includes `switch_function` on BranchNode [D-SF1-28]
- JSON Schema includes `inputs` (array of WorkflowInputDefinition) and `outputs` (array of WorkflowOutputDefinition) on WorkflowConfig [D-SF1-30]
- JSON Schema includes `fresh_sessions` on LoopConfig/FoldConfig, `instance_ref` on PluginNode, `condition` on PortDefinition, expression descriptions
- JSON Schema has ONE `Edge` definition — no `HookEdge` [D-SF1-21]
- `python -m iriai_compose.schema.json_schema` creates file
- YAML output does NOT sort keys
- YAML output has `edges:` key but NOT `hook_edges:` key on phases and workflow [D-SF1-21]
- YAML round-trip preserves `output_type` on PluginNode (e.g., `output_type: "FileList"`) and `input_type` on BranchNode

**Counterexamples:**
- Do NOT use ruamel.yaml [D-SF1-8]
- Do NOT include TransformRef or HookEdge in schema [D-21, D-SF1-21]
- Do NOT serialize `hook_edges` as a separate YAML key [D-SF1-21]

---

### STEP-8: Tests & Fixtures

**Objective:** Comprehensive pytest tests and YAML fixtures covering all entities, validators, validation, YAML round-trip, and JSON Schema. Tests verify all hardening additions: I/O type fields on NodeBase and PhaseDefinition [D-SF1-22], `fresh_sessions` on LoopConfig/FoldConfig [D-SF1-16], instance_ref [D-SF1-17], ActorDefinition validation, expression eval context documentation [D-SF1-15], unified edge model [D-SF1-21], `switch_function` on BranchNode [D-SF1-28], workflow-level I/O [D-SF1-30], and all 21 validation error codes [H-3].

**Scope:**
- `tests/test_schema_models.py` — create
- `tests/test_schema_validation.py` — create
- `tests/test_schema_yaml.py` — create
- `tests/test_schema_json.py` — create
- `tests/fixtures/workflows/minimal_ask.yaml` — create
- `tests/fixtures/workflows/gate_pattern.yaml` — create
- `tests/fixtures/workflows/minimal_branch.yaml` — create
- `tests/fixtures/workflows/branch_gather_dispatch.yaml` — create
- `tests/fixtures/workflows/minimal_plugin.yaml` — create
- `tests/fixtures/workflows/plugin_instance_ref.yaml` — create
- `tests/fixtures/workflows/loop_interview.yaml` — create
- `tests/fixtures/workflows/fold_phase.yaml` — create
- `tests/fixtures/workflows/map_phase.yaml` — create
- `tests/fixtures/workflows/loop_fresh_sessions.yaml` — create
- `tests/fixtures/workflows/plugin_fire_and_forget.yaml` — create
- `tests/fixtures/workflows/branch_gather_only.yaml` — create
- `tests/fixtures/workflows/hook_edges.yaml` — create
- `tests/fixtures/workflows/typed_io.yaml` — create
- `tests/fixtures/workflows/invalid/dangling_edge.yaml` — create
- `tests/fixtures/workflows/invalid/missing_fold_config.yaml` — create
- `tests/fixtures/workflows/invalid/boundary_violation.yaml` — create
- `tests/fixtures/workflows/invalid/dual_output_config.yaml` — create
- `tests/fixtures/workflows/invalid/hook_edge_with_transform.yaml` — create
- `tests/fixtures/workflows/invalid/dual_input_config.yaml` — create
- `tests/fixtures/workflows/invalid/invalid_type_ref.yaml` — create
- `tests/fixtures/workflows/invalid/switch_with_conditions.yaml` — create
- `tests/fixtures/workflows/invalid/invalid_workflow_io.yaml` — create
- `tests/fixtures/workflows/switch_function_branch.yaml` — create
- `tests/fixtures/workflows/workflow_io.yaml` — create

**Instructions:**

1. **Model tests** (`test_schema_models.py`):
   - **NodeBase I/O type fields [D-SF1-22]:** Test that `input_type`, `input_schema`, `output_type`, `output_schema` exist on NodeBase and are inherited by all three node types. Test mutual exclusion: `input_type` + `input_schema` raises. `output_type` + `output_schema` raises. Cross-pair allowed: `input_type` + `output_type` works. All four None by default.
   - **AskNode:** Test that AskNode has NO `output_type`/`output_schema` as its own fields — verify they come from NodeBase. Test `AskNode(output_type="PRD")` works (inherited). Test defaults, conditions (Gate/Choose), fixed inputs.
   - **BranchNode:** Test 1+ inputs/outputs (both can be 1), no actor, merge_function, switch_function, default inputs. Test `BranchNode(output_type="MergedResult")` works (inherited). Test `BranchNode(input_type="ReviewData")` works. Test `switch_function` exclusive routing [D-SF1-28]: `BranchNode(switch_function="data.path", outputs=[PortDefinition(name="a"), PortDefinition(name="b")])` validates. Test `switch_function` + condition conflict: `BranchNode(switch_function="...", outputs=[PortDefinition(name="a", condition="data.x")])` raises ValueError. Test `merge_function` + `switch_function` coexist (orthogonal): `BranchNode(merge_function="...", switch_function="...")` validates.
   - **PluginNode:** Test plugin_ref/instance_ref exclusion, fixed inputs, 0 outputs (fire-and-forget) validates [D-SF1-19]. Test `PluginNode(output_type="FileList")` works (inherited). Test `PluginNode(input_type="ContextDict")` works.
   - **PhaseDefinition:** Test loop/fold modes. Test `input_type`/`input_schema`/`output_type`/`output_schema` with mutual exclusion [D-SF1-22]. Test `PhaseDefinition(input_type="X", output_type="Y")` works.
   - Discriminated union. WorkflowConfig round-trip. PortDefinition is only port type. Port consistency. ActorDefinition validation. `fresh_sessions` on LoopConfig and FoldConfig. PluginInterface defaults.
   - **WorkflowConfig I/O [D-SF1-30]:** Test `WorkflowInputDefinition` construction with all fields. Test `WorkflowOutputDefinition` construction. Test `WorkflowConfig(name="test", inputs=[WorkflowInputDefinition(name="feature", type_ref="Feature")], outputs=[WorkflowOutputDefinition(name="result", type_ref="Result")])` round-trips. Test defaults: `required=True`, `default=None`. Test optional input: `WorkflowInputDefinition(name="x", required=False, default={"key": "value"})`. Test empty `inputs`/`outputs` (defaults).
   - **Edge model tests:** single Edge type used for both data and hook connections; no HookEdge class importable [D-SF1-21]. Test `parse_port_ref` helper. Test `is_hook_source` helper. PhaseDefinition has `edges` but no `hook_edges` field. WorkflowConfig has `edges` but no `hook_edges` field.

2. **Validation tests** (`test_schema_validation.py`):
   - **Every error code** (all 21 per [H-3]) including `invalid_hook_edge_transform` [D-SF1-21], `invalid_io_config` [D-SF1-22], `invalid_type_ref` [D-SF1-22], `invalid_switch_function_config` [D-SF1-28], `invalid_workflow_io_ref` [D-SF1-30], `missing_required_field`.
   - Port resolution across all three lists (inputs, outputs, hooks).
   - PluginNode instance_ref validation.
   - **Switch function config validation [D-SF1-28]:** BranchNode with `switch_function` AND output port with `condition` → `invalid_switch_function_config`. BranchNode with `switch_function` and condition-free outputs → no error. BranchNode without `switch_function` and outputs with conditions → no error.
   - **Workflow I/O validation [D-SF1-30]:** `WorkflowInputDefinition(type_ref="NonexistentType")` → `invalid_workflow_io_ref`. `WorkflowOutputDefinition(type_ref="ValidType")` with "ValidType" in types → no error. Workflow with no inputs/outputs → no error. Workflow input with `type_ref=None` → no error (untyped).
   - **Required field validation:** AskNode without actor → `missing_required_field`. Phase without mode → `missing_required_field`.
   - **I/O config validation [D-SF1-22]:** Node with both `input_type` and `input_schema` → `invalid_io_config`. Node with both `output_type` and `output_schema` → `invalid_io_config`. Phase with both `input_type` and `input_schema` → `invalid_io_config`.
   - **Type reference validation [D-SF1-22]:** Node `input_type: "NonexistentType"` → `invalid_type_ref`. Node `output_type: "NonexistentType"` → `invalid_type_ref`. Node with valid `output_type` referencing existing `workflow.types` key → no error. Phase `input_type`/`output_type` validated similarly.
   - **Type flow with resolution [D-SF1-22]:** Edge from `output_type: "PRD"` node to `input_type: "TechnicalPlan"` node → `type_mismatch`. Edge from `output_type: "PRD"` to `input_type: "PRD"` → no error. Edge from `output_type: "PRD"` to untyped node → no error. Edge where port-level `type_ref` overrides node-level type → port type used.
   - **`resolve_port_type` tests:** returns port-level `type_ref` when set; returns node-level type for single-port nodes when port has no `type_ref`; returns None for multi-port nodes without port-level types.
   - Hook edge constraint tests. `build_port_index` classifies ports correctly.

3. **YAML tests** (`test_schema_yaml.py`): All fixtures load. Round-trip preserves. Error cases raise correctly. `typed_io.yaml` fixture demonstrates `input_type`/`output_type` on Ask, Branch, Plugin nodes and phases [D-SF1-22]. `hook_edges.yaml` demonstrates hook edges in single `edges` list. Verify YAML output has `edges:` but NOT `hook_edges:`.

4. **JSON Schema tests** (`test_schema_json.py`): Valid JSON. Discriminator present. No banned fields. `input_type`/`input_schema`/`output_type`/`output_schema` present on NodeBase definition (NOT as AskNode-specific) and on PhaseDefinition [D-SF1-22]. `fresh_sessions` on LoopConfig/FoldConfig. Expression descriptions present. No `HookEdge` definition [D-SF1-21]. Single `Edge` definition in `$defs`.

5. **Fixtures**: 18 valid + 7 invalid YAML fixtures as listed in scope.
   - `typed_io.yaml` NEW: Demonstrates I/O type fields on all node types and a phase. Contains: an AskNode with `input_type` and `output_type` set, a PluginNode with `output_type` set, a BranchNode with `input_type` and `output_type` set, a PhaseDefinition with `input_type` and `output_type` set. Types reference entries in `workflow.types`.
   - `invalid/dual_input_config.yaml` NEW: Node with both `input_type: "PRD"` and `input_schema: {type: object}` — should produce `invalid_io_config` validation error.
   - `invalid/dual_output_config.yaml` UPDATED: Node with both `output_type` and `output_schema` — now tests NodeBase-level mutual exclusion (not AskNode-specific).
   - `invalid/invalid_type_ref.yaml` NEW: Node with `output_type: "NonexistentType"` where that type is not in `workflow.types` — should produce `invalid_type_ref` validation error.
   - `invalid/switch_with_conditions.yaml` NEW: BranchNode with both `switch_function: "data.path"` and an output port with `condition: "data.approved"` — should produce `invalid_switch_function_config` validation error [D-SF1-28].
   - `invalid/invalid_workflow_io.yaml` NEW: WorkflowConfig with `inputs: [{name: "x", type_ref: "NonexistentType"}]` — should produce `invalid_workflow_io_ref` validation error [D-SF1-30].
   - `switch_function_branch.yaml` NEW: Valid workflow with a BranchNode using `switch_function: "'approved' if data.verdict else 'rejected'"` and two output ports `approved` and `rejected` (no conditions). Demonstrates exclusive routing [D-SF1-28].
   - `workflow_io.yaml` NEW: WorkflowConfig with `inputs` (required + optional with default) and `outputs` declarations. Demonstrates workflow-level I/O [D-SF1-30].
   - All other fixtures as previously listed.

**Acceptance Criteria:**
- `pytest tests/test_schema_*.py` all pass
- All 21 validation error codes tested [H-3], including `invalid_hook_edge_transform` [D-SF1-21], `invalid_io_config` [D-SF1-22], `invalid_type_ref` [D-SF1-22], `invalid_switch_function_config` [D-SF1-28], `invalid_workflow_io_ref` [D-SF1-30], `missing_required_field`
- Every new feature (D-SF1-15 through D-SF1-30) tested
- No test references HookEdge, hook_edges, options, output_paths
- No test puts `fresh_sessions` on ActorDefinition or any node type — only on LoopConfig/FoldConfig [D-SF1-16]
- No test puts `output_type`/`output_schema` as AskNode-specific fields — they are on NodeBase [D-SF1-22]
- `typed_io.yaml` fixture has `input_type`/`output_type` on multiple node types and a phase [D-SF1-22]
- `invalid/dual_input_config.yaml` triggers `invalid_io_config` error
- `invalid/invalid_type_ref.yaml` triggers `invalid_type_ref` error
- JSON Schema tests verify `input_type`/`input_schema`/`output_type`/`output_schema` are on NodeBase, not AskNode [D-SF1-22]
- `switch_function_branch.yaml` fixture has BranchNode with `switch_function` and condition-free output ports [D-SF1-28]
- `workflow_io.yaml` fixture has `inputs` and `outputs` on WorkflowConfig [D-SF1-30]
- `invalid/switch_with_conditions.yaml` triggers `invalid_switch_function_config` error
- `invalid/invalid_workflow_io.yaml` triggers `invalid_workflow_io_ref` error
- JSON Schema tests verify `switch_function` on BranchNode [D-SF1-28]
- JSON Schema tests verify `inputs`/`outputs` on WorkflowConfig with proper sub-definitions [D-SF1-30]

**Counterexamples:**
- Do NOT test runtime execution (SF-2/SF-3)
- Do NOT test UI rendering (SF-6)
- Do NOT use iriai_compose.testing (doesn't exist yet)
- Do NOT expect empty inputs on any node [D-SF1-11]
- Do NOT put `fresh_sessions` on any actor or node in fixtures or tests [D-SF1-16]
- Do NOT create any `HookEdge` instances in tests [D-SF1-21]
- Do NOT reference `hook_edges` field in any fixture YAML [D-SF1-21]
- Do NOT put `output_type`/`output_schema` as AskNode-only fields in tests — use NodeBase inheritance [D-SF1-22]
- Do NOT combine `switch_function` with per-port `condition` on the same BranchNode in valid fixtures — they are mutually exclusive [D-SF1-28]

---

## File Manifest

| Path | Action |
|------|--------|
| `iriai_compose/schema/__init__.py` | create |
| `iriai_compose/schema/base.py` | create |
| `iriai_compose/schema/nodes.py` | create |
| `iriai_compose/schema/edges.py` | create |
| `iriai_compose/schema/phases.py` | create |
| `iriai_compose/schema/actors.py` | create |
| `iriai_compose/schema/types.py` | create |
| `iriai_compose/schema/cost.py` | create |
| `iriai_compose/schema/plugins.py` | create |
| `iriai_compose/schema/templates.py` | create |
| `iriai_compose/schema/stores.py` | create |
| `iriai_compose/schema/workflow.py` | create |
| `iriai_compose/schema/validation.py` | create |
| `iriai_compose/schema/yaml_io.py` | create |
| `iriai_compose/schema/json_schema.py` | create |
| `pyproject.toml` | modify |
| `tests/test_schema_models.py` | create |
| `tests/test_schema_validation.py` | create |
| `tests/test_schema_yaml.py` | create |
| `tests/test_schema_json.py` | create |
| `tests/fixtures/workflows/minimal_ask.yaml` | create |
| `tests/fixtures/workflows/gate_pattern.yaml` | create |
| `tests/fixtures/workflows/minimal_branch.yaml` | create |
| `tests/fixtures/workflows/branch_gather_dispatch.yaml` | create |
| `tests/fixtures/workflows/minimal_plugin.yaml` | create |
| `tests/fixtures/workflows/plugin_instance_ref.yaml` | create |
| `tests/fixtures/workflows/loop_interview.yaml` | create |
| `tests/fixtures/workflows/fold_phase.yaml` | create |
| `tests/fixtures/workflows/map_phase.yaml` | create |
| `tests/fixtures/workflows/loop_fresh_sessions.yaml` | create |
| `tests/fixtures/workflows/plugin_fire_and_forget.yaml` | create |
| `tests/fixtures/workflows/branch_gather_only.yaml` | create |
| `tests/fixtures/workflows/hook_edges.yaml` | create |
| `tests/fixtures/workflows/typed_io.yaml` | create |
| `tests/fixtures/workflows/store_registry.yaml` | create |
| `tests/fixtures/workflows/context_hierarchy.yaml` | create |
| `tests/fixtures/workflows/artifact_hosting.yaml` | create |
| `tests/fixtures/workflows/invalid/dangling_edge.yaml` | create |
| `tests/fixtures/workflows/invalid/missing_fold_config.yaml` | create |
| `tests/fixtures/workflows/invalid/boundary_violation.yaml` | create |
| `tests/fixtures/workflows/invalid/dual_output_config.yaml` | create |
| `tests/fixtures/workflows/invalid/hook_edge_with_transform.yaml` | create |
| `tests/fixtures/workflows/invalid/dual_input_config.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_type_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/switch_with_conditions.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_workflow_io.yaml` | create |
| `tests/fixtures/workflows/switch_function_branch.yaml` | create |
| `tests/fixtures/workflows/workflow_io.yaml` | create |
| `tests/fixtures/workflows/invalid/undeclared_store_ref.yaml` | create |
| `tests/fixtures/workflows/invalid/invalid_store_key.yaml` | create |
| `iriai_compose/__init__.py` | read |
| `iriai_compose/actors.py` | read |
| `iriai_compose/tasks.py` | read |
| `iriai_compose/runner.py` | read |
| `iriai_compose/storage.py` | read |

---

## Interface Contracts

### SF-1 → SF-2 (Python Import)
```python
from iriai_compose.schema import (
    WorkflowConfig, AskNode, BranchNode, PluginNode, NodeDefinition,
    PhaseDefinition, Edge, PortDefinition, ActorDefinition, CostConfig,
    PluginInterface, PluginInstanceConfig,
    WorkflowInputDefinition, WorkflowOutputDefinition,
    load_workflow, dump_workflow,
)
from iriai_compose.schema import StoreDefinition, StoreKeyDefinition
from iriai_compose.schema.edges import parse_port_ref, is_hook_source
from iriai_compose.schema.validation import build_port_index, resolve_port_type
```
**Canonical import path [C-2]:** All imports use `iriai_compose.schema`, NOT `iriai_compose.declarative.schema`.

SF-2 loader uses `load_workflow()`. Runner dispatches on `NodeDefinition.type`. Port resolution uniform across inputs/outputs/hooks. All nodes guaranteed at least one input port [D-SF1-11].

**I/O type fields [D-SF1-22]:** All nodes and phases have `input_type`, `input_schema`, `output_type`, `output_schema` (inherited from NodeBase for nodes). The runner uses `output_type` on AskNode to tell the agent what structured output to produce (same as existing `Ask.output_type`). For PluginNode, the runner can use `output_type` to validate plugin return data. For BranchNode, `output_type` describes the merge output. `input_type` on any node enables runtime input validation before execution.

**Unified edge model [D-SF1-21]:** All edges are `Edge` instances in a single list. The runner uses `build_port_index()` and `is_hook_source()` to classify edges:
- **Data edge** (source in `outputs`): Runner delivers data, optionally applying `transform_fn`.
- **Hook edge** (source in `hooks`): Runner triggers the target node as a fire-and-forget side effect. No transform. The runner uses the `hooks` container classification to determine this — no `edge_type` field needed.

**Type flow resolution [D-SF1-22]:** The runner can use `resolve_port_type()` for runtime type checking. Resolution priority: port-level `type_ref` > node/phase-level `input_type`/`output_type` for single-port elements > None. This enables the runner to validate data at edge boundaries.

**Output port routing [D-SF1-2, D-SF1-28]:**
- **Ask/Plugin** = mutually exclusive first-match on per-port `condition`.
- **Branch with `switch_function`** = exclusive programmatic routing. Runner evaluates `switch_function` against `data` (merged input or passthrough). Function returns port name string. Runner fires only that port. If the returned name doesn't match any output port, runner raises `ExecutionError` with diagnostic: `"switch_function returned '{name}' but available ports are [...]"`.
- **Branch without `switch_function`** = non-exclusive all-match on per-port `condition`. All ports whose condition evaluates truthy fire simultaneously.
- **PluginNode** may have 0 output ports (fire-and-forget) — runner executes the plugin but does not deliver data downstream [D-SF1-19].
- **Execution order with `merge_function` + `switch_function`:** (1) all input ports receive data, (2) `merge_function` merges inputs into single `data`, (3) `switch_function` receives merged `data`, returns port name, (4) only that port fires.

**Async gather/barrier [D-SF1-20]:** When a BranchNode has N>1 input ports, the runner awaits data on ALL connected input ports before firing the node. This is the DAG-level equivalent of `asyncio.gather()`. The runner tracks port satisfaction state per node. If `merge_function` is set, the runner evaluates it against `inputs = {port_name: data, ...}` to produce a single merged dict before evaluating output port conditions. If `merge_function` is None, the runner passes the `inputs` dict directly.

**Session management [D-SF1-16]:** `fresh_sessions` is on LoopConfig and FoldConfig, not on actors or nodes. When the runner enters a loop/fold iteration with `fresh_sessions=True`, it clears ALL agent actor sessions for actors used within that phase before starting the iteration. Session key format unchanged: `"{actor_name}:{feature_id}"`. This means:
- Same actor can participate in a sequential phase (persistent sessions) AND a loop phase with `fresh_sessions: true` (cleared each iteration) without needing separate actor entries.
- The runner does NOT need separate actor entries for same-role-different-session — it reads `fresh_sessions` from the phase config at each iteration boundary.
- InteractionActor is unaffected by clearing (uses Pending objects, not sessions).

**Store registry [D-SF1-23]:** Runner receives `workflow.stores` dict. For each named store, runner instantiates an ArtifactStore implementation. Dot-notation references are resolved by splitting on first dot. **Context hierarchy [D-SF1-24]:** Runner merges context_keys and context_text at 4 levels (workflow → phase → actor → node) before each node invocation. **Context bindings [D-SF1-25]:** `context_store` tells runner which store to use for `context_provider.resolve()`. `handover_key` tells runner where to read/write handover docs.

**`artifact_key` auto-write [D-SF1-29]:** When a node has `artifact_key` set (e.g., `"artifacts.prd"`), the runner automatically writes the node's output to that store key after execution, BEFORE output port routing. Execution order: (1) node executes → produces output, (2) runner writes output to store at `artifact_key` (dot notation: first segment = store name, rest = key), (3) output port conditions/switch_function evaluate against the output data, (4) matching ports fire. This replaces explicit `runner.artifacts.put(key, value, feature=feature)` calls from iriai-build-v2. If the store key has a `type_ref`, the runner should validate the output matches the type before writing (or rely on schema-level `store_type_mismatch` validation). Auto-write is a no-op if `artifact_key` is None.

**Workflow I/O validation [D-SF1-30]:** `workflow.inputs` declares expected inputs. At `run()` time, the runner validates: (1) all inputs with `required=True` are provided, (2) provided inputs with `type_ref` match the declared type. `workflow.outputs` declares expected outputs. At workflow completion, the runner can optionally validate: all declared outputs exist in the appropriate store. The runner should apply `default` values for optional inputs not provided. Workflow inputs are available in the initial context/data passed to the first phase.

**Plugin instance resolution [D-SF1-17]:** `instance_ref` → lookup in `workflow.plugin_instances` → resolve type via `plugin_type`.

**Map actor deduplication:** Runner auto-creates unique actor instances per iteration (like `_make_parallel_actor`).

**Expression evaluation [D-SF1-15]:** All expressions evaluated in restricted Python context with only documented variables.

### SF-1 → SF-3 (Python Import)
```python
from iriai_compose.schema import (
    WorkflowConfig, validate_workflow, ValidationError,
    WorkflowInputDefinition, WorkflowOutputDefinition,
    load_workflow, dump_workflow,
)
from iriai_compose.schema.validation import resolve_port_type
```
SF-3's test fixtures must cover all 21 validation error codes [H-3]. The `switch_function_branch.yaml` and `workflow_io.yaml` fixtures test the new features. Invalid fixtures `switch_with_conditions.yaml` and `invalid_workflow_io.yaml` test the corresponding error codes.

### SF-1 → SF-4 (YAML Schema)
SF-4 produces `.yaml` files conforming to `WorkflowConfig`. Inputs omittable on AskNode/PluginNode (validators enforce defaults). Loop phases that need fresh sessions set `fresh_sessions: true` in loop_config. Expression fields use documented eval contexts. Hook edges go in the same `edges` list as data edges [D-SF1-21] — the source port determines semantics. Nodes can declare `input_type`/`output_type` for documentation and type-checking [D-SF1-22].

**`switch_function` usage in migration [D-SF1-28]:** Gate-style routing (approved/rejected) should use per-port `condition` on AskNode outputs (existing model). Only use `switch_function` on BranchNode when the routing logic is a programmatic function that returns a port name. Most iriai-build-v2 patterns use imperative `if/else` control flow — these map to per-port conditions, not `switch_function`.

**`artifact_key` simplification [D-SF1-29]:** Wherever iriai-build-v2 does `await runner.artifacts.put("key", value, feature=feature)` immediately after a task, the migration can use `artifact_key: "store.key"` on the node instead. This eliminates the need for a separate PluginNode just for artifact storage. Plugin nodes should only be used for side-effect operations (hosting, MCP, git).

**Workflow I/O [D-SF1-30]:** Each migrated workflow should declare `inputs` (e.g., Feature, workspace config) and `outputs` (e.g., final artifacts) on WorkflowConfig for SF-2 validation.

### SF-1 → SF-6 (JSON Schema)
```python
from iriai_compose.schema import generate_json_schema
schema = generate_json_schema()
```
Generated once at build time. Single `PortDefinition` type — UI renders differently by container. Single `Edge` type — UI renders differently by source port classification [D-SF1-21]:
- Source port in `outputs` → solid line with type label at midpoint.
- Source port in `hooks` → dashed purple (#a78bfa) line, no type label, no transform editing.

The UI builds a port index from the loaded workflow and uses `is_hook_source()` to determine edge rendering style. No `edge_type` field on the Edge model — the classification is derived at render time.

**I/O type fields on all nodes and phases [D-SF1-22]:** The UI can display "expects: PRD" and "produces: TechnicalPlan" on ANY node card — not just Ask. Inspector fields for `input_type`/`input_schema`/`output_type`/`output_schema` render on all node type inspectors and phase inspectors. The `output_schema` builder (inline field-by-field) works for ALL node types, not just Ask. JSON Schema shows these fields on NodeBase (discriminated union base), so the UI does not need per-type special-casing.

Every node has at least one input. AskNode/BranchNode have 1+ outputs; PluginNode may have 0 outputs (fire-and-forget — card shows no output port, no outgoing edge handle) [D-SF1-19]. `fresh_sessions` is NOT a checkbox in actor editor/role builder — it's a toggle in the PhaseInspector for loop/fold modes. `instance_ref` enables PluginPicker. Expression `description` values provide inline docs.

**`switch_function` in BranchInspector [D-SF1-28]:** JSON Schema includes `switch_function: string | null` on BranchNode. The BranchInspector should render a CodeMirror Python editor for `switch_function` (same as D-28 design). When `switch_function` is set, output ports should NOT show condition editors (the routing mode is exclusive via the function). When `switch_function` is null/empty, output ports show per-port condition editors (non-exclusive fan-out mode). The card face shows `ƒ switch(...)` pill when `switch_function` is set.

**Workflow I/O in JSON Schema [D-SF1-30]:** `WorkflowInputDefinition` and `WorkflowOutputDefinition` appear in the schema. The UI may use these for workflow-level configuration panels (e.g., "Workflow Inputs" section in workflow inspector showing required vs optional inputs with types).

---

---


---