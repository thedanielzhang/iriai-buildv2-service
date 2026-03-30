<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

Supersede the older Branch-only revision with the D-GR-22 contract bundle as SF-2's baseline. The loader/runner now assumes one canonical serialized workflow shape: `WorkflowConfig.phases[]` at the root, each phase containing `nodes[]` and `children[]`, with recursion used for parsing, validation, and execution graph construction instead of any flat top-level `nodes` contract. Hook behavior is serialized only through ordinary `edges`; hook-vs-data is inferred from the resolved source port container, so SF-2 does not read or write a separate hook section or serialized `edge.port_type`. For composer consumers, the only canonical schema source is `WorkflowConfig.model_json_schema()` served through `/api/schema/workflow`; any checked-in `workflow-schema.json` is secondary build/test output, not the runtime contract. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:20] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1021] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:510]

<!-- SF: dag-loader-runner -->
### J-1 — SF-2: DAG Loader & Runner

**Step Annotations:**
- Step 1 uses a shared recursive load path for both `validate()` and `run()`: parse YAML, `model_validate()`, then descend through `WorkflowConfig.phases -> PhaseDefinition.nodes/children`; stale flat contracts such as top-level `nodes` or nested `phase.phases` are rejected instead of silently normalized. [decision: D-GR-22]
- Graph construction keeps hook wiring inside ordinary edge lists only. An edge is treated as a hook when its `source` port resolves from the element's hook container; SF-2 never requires a serialized `port_type` field in YAML. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004]
- Composer-facing schema assumptions for this journey come from `/api/schema/workflow`, backed by `WorkflowConfig.model_json_schema()`. The runner must not depend on a static schema snapshot being newer than the live library models. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:510]

**Error Path UX:** Validation returns deterministic nested field paths such as `phases[0].children[1].edges[2]` for illegal `port_type`, malformed dot-notation, or deprecated separate hook serialization so downstream API/UI layers can point to the exact failing container.

**Empty State UX:** An empty workflow or empty sibling container fails structural validation rather than falling back to an implicit flat root graph.

**NOT Criteria:**
- SF-2 must NOT accept a separate serialized hook-wiring section as a second source of truth.
- SF-2 must NOT require or emit `edge.port_type` in persisted YAML.
- SF-2 must NOT treat a checked-in `workflow-schema.json` file as the authoritative runtime schema contract.
- SF-2 must NOT rebuild a flat top-level `workflow.nodes` format as its persisted contract.

<!-- SF: dag-loader-runner -->
### J-2 — SF-2: DAG Loader & Runner

**Step Annotations:**
- Nested execution preserves containment: each phase executes its local `nodes` plus nested `children` without flattening the entire workflow into one global node list. This keeps translated fold > loop and loop > map structures representable. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35]
- Cross-phase flow still uses normal edges and boundary ports, but child phases remain serialized inside their parent phase in YAML. The runner builds container-local DAGs from that nested structure instead of asking SF-6 to persist a flat graph. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006] [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1021]

**Error Path UX:** Containment errors identify the closest enclosing phase path so authors can tell whether the defect belongs to the parent container or a specific child phase.

**Empty State UX:** A phase with no executable `nodes` and no `children` is treated as invalid configuration until the schema explicitly defines a pure container-only phase mode.

**NOT Criteria:**
- The runner must NOT flatten `children` into the workflow root before validation.
- Nested phase edges must NOT be rewritten into a separate hook registry or callback section.
- Hook edges must NOT bypass phase-boundary resolution rules just because they target nested content.

<!-- SF: dag-loader-runner -->
### J-3 — SF-2: DAG Loader & Runner

**Step Annotations:**
- `validate()` reuses the same nested YAML parser as `run()`, but stops before runtime hydration, so editor and API callers can detect stale shape mismatches without `agent_runtimes` or `RuntimeConfig`. [code: .iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:795]
- Legacy documents that still serialize hook metadata separately or include `edge.port_type` receive schema errors instructing authors to move hook wiring into edges and rely on source-port resolution. [decision: D-GR-22]

**Error Path UX:** Errors are returned as field-scoped validation problems consumable by SF-5 validate endpoints and SF-6 inspector panels.

**Empty State UX:** N/A — validation API surface, not a rendered UI state.

**NOT Criteria:**
- `validate()` must NOT require runtime dependencies to reject stale serialized contracts.
- Validation must NOT autocorrect deprecated hook serialization silently.
- Validation must NOT accept a static schema artifact as the canonical parse contract.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner
N/A — backend-only Python subfeature with no rendered interface. The only composer-facing surface added by this revision is the schema contract consumed via `/api/schema/workflow`, not a responsive UI.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

Backend-only subfeature. `load_workflow()` parses YAML and recursively descends `phases[].nodes` plus `phases[].children`, building one execution graph per container rather than normalizing to a persisted flat graph. `validate()` shares that same parser and returns field-scoped structural errors without requiring runtimes, which makes it safe for SF-5 validation endpoints and SF-6 authoring flows. `run()` classifies edges by resolving the source port container: hook edges are ordinary edges whose source lives in a hook port set, remain fire-and-forget, and may not carry transforms; data edges may carry transforms. Composer integrations fetch schema from `/api/schema/workflow`; a static `workflow-schema.json` can still exist for build/test snapshots, but it is never the authoritative runtime interface. [decision: D-GR-22] [code: iriai-compose/iriai_compose/runner.py:62] [code: iriai-compose/iriai_compose/runner.py:162] [code: .iriai/artifacts/features/beced7b1/broad/architecture.md:353]

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner
N/A for a rendered surface. SF-2 should emit stable `field_path` and `message` validation records so SF-5/SF-6 can expose errors accessibly; this revision adds no direct keyboard, focus, or screen-reader surface inside SF-2 itself.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

1. Flatten the persisted workflow into top-level `workflow.nodes` plus phase membership metadata and let the runner reconstruct containment later — rejected because D-GR-22 makes nested YAML phase containment authoritative and the editor already owns the flat-to-nested transformation internally.
2. Serialize hook wiring in a separate hook section or keep author-provided `edge.port_type` in YAML — rejected because hook-ness is derived from source-port resolution and the saved contract must expose one edge model, not parallel hook metadata.
3. Treat a checked-in `workflow-schema.json` as the canonical composer contract and `/api/schema/workflow` as optional — rejected because D-GR-22 makes runtime schema delivery from `WorkflowConfig.model_json_schema()` authoritative; static schema files are build/test artifacts only.
4. Silently normalize stale flat or separate-hook documents during load — rejected because it preserves split contracts across SF-1, SF-2, SF-5, and SF-6 and hides the exact contract drift D-GR-22 was created to eliminate.

<!-- SF: dag-loader-runner -->
### SF-2: DAG Loader & Runner

SF-2 sits at the center of the contract chain: it parses the YAML that SF-6 saves, powers the validation surface that SF-5 exposes, and executes the result inside `iriai-compose`. If SF-2 tolerates stale flat-node, separate-hook, or static-schema-first assumptions, it becomes the accidental compatibility layer that keeps the cross-subfeature split alive. D-GR-22 resolves that split explicitly: persisted workflows remain nested under `phases[].nodes` and `phases[].children`, hook connectivity is represented only through ordinary edges with inferred hook classification, and composer schema consumers read the live contract from `/api/schema/workflow`. This revised artifact therefore replaces the older Branch-conflict-centered framing with a loader/runner contract centered on recursive phase containment, single-edge-model hook handling, and live schema export. That design remains additive to the existing imperative runner abstractions in `WorkflowRunner` and `DefaultWorkflowRunner`, while giving SF-5/SF-6 one stable parse/validate/execute interface for translated `iriai-build-v2` workflows. [decision: D-GR-22] [code: .iriai/artifacts/features/beced7b1/plan-review-discussion-4.md:20] [code: iriai-compose/iriai_compose/runner.py:62] [code: iriai-compose/iriai_compose/runner.py:162]
