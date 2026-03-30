```json
{
  "approved": false,
  "revision_plan": {
    "requests": [
      {
        "description": "SF-1 Declarative Schema \u2014 Full Plan Rewrite\n\n**Affected artifacts:** plan, system-design\n\nThe SF-1 plan was never updated after D-GR-35. It contradicts the PRD on ~30 blocking points:\n\n- Replace `switch_function` with rejection logic \u2014 PRD REQ-3/AC-25 require it to be rejected\n- Replace `stores`/`plugin_instances` at WorkflowConfig root with rejection logic \u2014 PRD REQ-1/AC-27\n- Replace `type: \"interaction\"` actor discriminator with `actor_type: \"agent\" | \"human\"` \u2014 PRD REQ-2/AC-13\n- Replace `phases` recursive field inside PhaseDefinition with `children` \u2014 PRD REQ-5\n- Replace four separate mode config fields with single discriminated-union `mode_config` \u2014 PRD REQ-6\n- Replace `PortDefinition.condition` on all ports with `BranchOutputPort.condition` only on branch outputs \u2014 PRD REQ-15/REQ-22\n- Add `schema_def` to PortDefinition (XOR with `type_ref`) \u2014 PRD REQ-15\n- Add `workflow_version: int` to WorkflowConfig \u2014 PRD REQ-14\n- Add BranchOutputPort as distinct type \u2014 PRD data entity\n- Add HookPortEvent model \u2014 PRD data entity\n- Split CostConfig into WorkflowCostConfig/PhaseCostConfig/NodeCostConfig \u2014 PRD data entities\n- Add expression size validation (10,000 char limit) \u2014 PRD REQ-21/AC-17\n- Add YAML bare-string shorthand desugaring \u2014 PRD REQ-22/AC-22\n- Add stale BranchNode field rejection (condition_type/condition/paths) \u2014 PRD REQ-17/AC-28\n- Remove `context_text` from all levels \u2014 not in PRD approved fields\n- Remove `inputs`/`outputs` from WorkflowConfig root \u2014 not in PRD closed root set\n- Remove `artifact_key` from NodeBase \u2014 D-GR-14\n- Remove `fresh_sessions` from LoopConfig/FoldConfig \u2014 D-SF1-16 system design says no\n- Remove `input_type`/`input_schema`/`output_type`/`output_schema` from NodeBase \u2014 use PortDefinition\n- Fix TemplateRef \u2192 TemplateDefinition with full phase-tree body\n- Fix Edge \u2192 EdgeDefinition naming\n- Fix BranchNode min outputs from 1 to 2\n- Add per-port `BranchNode.outputs: dict[str, BranchOutputPort]` model with non-exclusive fan-out\n- Add `merge_functio",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-1.",
        "affected_subfeatures": [
          "declarative-schema"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-2 DAG Loader & Runner \u2014 Plan Rewrite + Security Fix\n\n**Affected artifacts:** plan, system-design\n\nCritical expression security gap plus stale BranchNode model in system design:\n\n- **Replace bare exec() (D-SF2-5) with AST allowlist + timeout + size limit** \u2014 PRD REQ-16, SF-1 REQ-21, Design CMP-7 ExpressionSandbox\n- Implement Design CMP-7 ExpressionSandbox component: AST allowlist visitor, blocked builtins, 5s timeout, 10,000-char size limit\n- Add expression size check before evaluation\n- Add expression timeout enforcement\n- Add path traversal protection on run()/load_workflow() inputs\n- Add plugin execution context capability scoping\n- Add YAML document size limit and alias expansion guard\n- Add recursion depth enforcement for nested phases (max depth parameter)\n- Sanitize error messages before they reach API responses (strip tracebacks)\n- Add entry point plugin trust verification/allowlist\n- Fix system design sf2_node_executor: replace stale `condition_type/condition/paths` with D-GR-35 per-port model\n- Fix system design AskNode field: `prompt` not `task`\n- Add `code` field to validation_error entity in system design\n- Align BranchNode execution to non-exclusive fan-out with optional merge_function",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-2.",
        "affected_subfeatures": [
          "dag-loader-runner"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-3 Testing Framework \u2014 Full Plan Rewrite\n\n**Affected artifacts:** plan, system-design\n\nThe plan is the pre-R18 stale artifact \u2014 the exact thing the PRD was written to eliminate:\n\n- **Remove D-SF3-5** (node_id kwarg on invoke) \u2014 this is the primary non-compliance risk per PRD security profile\n- **Replace dict-based MockRuntime** with fluent no-arg MockAgentRuntime: when_node()/when_role()/default_response()/respond()/respond_sequence()/respond_with()/raise_error()/then_crash()/on_call()/with_cost()\n- **Add ContextVar-based node resolution** \u2014 import _current_node from runner, read during invoke()\n- **Add MockPluginRuntime** (Design CMP-3) at iriai_compose/testing/mock_plugin.py\n- Add assert_loop_iterations, assert_fold_items_processed, assert_map_fan_out, assert_error_routed\n- Add MockExhaustedError for respond_sequence() exhaustion\n- Add 4-strategy matcher priority: node > role+prompt > role-only > default\n- Fix class naming: MockRuntime \u2192 MockAgentRuntime, MockInteraction \u2192 MockInteractionRuntime\n- Add fluent MockInteractionRuntime with when_node()/approve_sequence()/respond_with()/script()\n- Fix port containers from list to dict[str, PortDefinition]\n- Remove RuntimeConfig(history=...) resume references \u2014 checkpoint/resume is out of scope\n- Fix D-SF3-16 ID collision between PRD and system design\n- Add pre-implementation verification gate confirming plan correction before STEP-20\n- Align run_test() parameter to match SF-2's actual run() signature\n- Fix interaction runtime key name ('default' vs 'human')\n- Add journey traceability to PRD J-1 and J-2",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-3.",
        "affected_subfeatures": [
          "testing-framework"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-4 Workflow Migration \u2014 Plan Rewrite + Persistence Model Resolution\n\n**Affected artifacts:** plan, system-design\n\nIrreconcilable artifact persistence model + missing bridge + exec/eval incompatibility:\n\n- **Resolve persistence model**: Adopt D-GR-14 explicit store PluginNodes (System Design position) \u2014 remove artifact_key auto-write (C-4) from all YAMLs, templates, tests, node counts, and hook edge sources\n- **Add iriai-build-v2 bridge implementation**: _declarative.py wrapper, --yaml CLI flag, plugins/adapters.py with 6 adapter classes (Store, Hosting, Mcp, Subprocess, Http, Config), create_plugin_runtimes factory\n- **Fix transform exec/eval incompatibility**: Rewrite multi-line function transforms as eval()-compatible expressions or registered named functions\n- **Add config plugin type** (6th type per D-GR-10) \u2014 reclassify build_env_overrides from edge transform to config Plugin\n- Fix all AskNode field names: `actor` \u2192 `actor_ref` per D-GR-41\n- Fix mock import names: MockRuntime \u2192 MockAgentRuntime, MockInteraction \u2192 MockInteractionRuntime, add MockPluginRuntime\n- Fix SF-1 type name imports: MapConfig \u2192 MapModeConfig, Edge \u2192 EdgeDefinition, TemplateRef \u2192 TemplateDefinition, etc.\n- Resolve SF-2/SF-4 scope boundary: clarify whether migration is in SF-2 STEP-12-15 or SF-4 STEP-28-35\n- Fix decision ID collisions (D-SF4-7, D-SF4-22)\n- Add seed_loader.py security: parameterized queries, credential handling via env vars not CLI args, audit logging\n- Add Jinja2 template parameter sanitization\n- Add negative security tests (malicious transforms, blocked builtins, oversized expressions)\n- Fix schema_version value consistency\n- Add plugin instance config count alignment (8 instances, 7 transforms)\n- Add AC-24 verification step",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-4.",
        "affected_subfeatures": [
          "workflow-migration"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-5 Composer App Foundation \u2014 Plan + System Design Rewrite\n\n**Affected artifacts:** plan, system-design\n\nPervasive contradictions across all documents:\n\n**Plan fixes:**\n- **Remove PluginType/PluginInstance** tables, models, and endpoints \u2014 PRD REQ-4 five-table boundary\n- **Add WorkflowVersion** model as MUST-priority audit entity \u2014 PRD REQ-5\n- Fix Workflow model fields: add description, current_version, is_valid; remove is_example\n- Fix Role model field: system_prompt \u2192 prompt (matching iriai-compose contract)\n- **Add POST /api/workflows/import** endpoint \u2014 PRD REQ-11 canonical import\n- **Add GET /api/workflows/templates** endpoint \u2014 PRD REQ-11 starter templates\n- Add POST /api/workflows/{id}/duplicate endpoint\n- Add POST /api/workflows/{id}/versions endpoint\n- Add GET /ready endpoint with database readiness check\n- **Add mutation hook interface** for all 4 entity types with exactly 4 event kinds (created/updated/soft_deleted/restored) \u2014 PRD REQ-18\n- Add per-user rate limiting middleware \u2014 PRD REQ-9\n- Add structured JSON logging with field redaction \u2014 PRD REQ-9\n- Fix sidebar to 4 folders (remove Plugins) \u2014 PRD REQ-16\n- Fix starter template persistence: user_id='__system__' DB rows via Alembic data migration (not seed.py, not filesystem)\n- Fix database driver: psycopg3 (not asyncpg)\n- Add YAML input size limits and bomb protection\n- Add entity name sanitization\n- Add restore endpoint for soft-deleted entities\n- Add structured acceptance criteria to STEP-40\n- Remove CMP-30\u201347 visual primitives from SF-5 scope (belongs to SF-7)\n- Add EditorSchemaBootstrapGate (CMP-18) implementation task\n- Add YAMLContractErrorPanel (CMP-19) implementation task\n- Add design annotations for PRD J-7, J-8, J-9\n\n**System Design fixes:**\n- **Fix mutation hook event kinds**: created/updated/soft_deleted/restored (not created/imported/version_saved/deleted)\n- **Extend hooks to all 4 entity types** (not just Workflow)\n- Remove phantom node types (AgentNode, CustomNode, EvalNode)\n- Fix import ",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-5.",
        "affected_subfeatures": [
          "composer-app-foundation"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-6 Workflow Editor \u2014 Plan Rewrite\n\n**Affected artifacts:** plan, system-design\n\nSwitchFunctionEditor contradiction + missing critical components:\n\n- **Remove SwitchFunctionEditor entirely** \u2014 Design and System Design both reject it; remove from STEP-47, 49, 50, 55, 57\n- **Remove D-SF6-8** (dual switch_function/per-port-condition model) \u2014 adopt D-SF6-9 (per-port conditions only)\n- **Add SchemaBootstrapGate** (CMP-69) implementation step \u2014 critical blocking component per AC-1/AC-10\n- **Add MergeFunctionEditor** (CMP-65) implementation step with 3 states\n- **Add PortConditionRow** (CMP-66) implementation step with 3 states\n- **Add `schema_def` to PortDefinition TS type** \u2014 required for round-trip fidelity per SF-1 REQ-22\n- Fix port model: arrays \u2192 dict-keyed maps (Record<string, PortDefinition>)\n- Add `collapsedGroups` to WorkflowSnapshot for undo/redo\n- Fix node palette placement: left side per PRD D-2 (not right)\n- Fix node components to wrap SF-7 primitives per D-58 three-layer model\n- Add verification blocks for PRD J-4, J-5, J-6\n- Fix requirement traceability (most steps incorrectly map to REQ-13/14)\n- Remove REQ-15 references (doesn't exist in PRD)\n- Add content for 9 \"Unchanged from original plan\" steps\n- Fix journey ID mapping to match PRD J-1 through J-6\n- Fix Design journey annotations: J-4 should be schema unavailability (not hook edges), remove phantom J-8\n- Add YAML import safety: safe schema mode, file size limit, alias expansion limit\n- Add secret-stripping for YAML export\n- Add auth token wiring verification for auto-save/schema-fetch/validation calls\n- Add no-local-execution enforcement mechanism for inline Python",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-6.",
        "affected_subfeatures": [
          "workflow-editor"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      },
      {
        "description": "SF-7 Libraries & Registries \u2014 Full Plan Rewrite\n\n**Affected artifacts:** plan, system-design\n\nFundamental architecture contradiction + scope violations:\n\n- **Replace YAML-parsing-on-delete with materialized workflow_entity_refs** \u2014 align to PRD/Design/System Design architecture\n- **Remove entire Plugins Registry** (STEP-67, PluginPicker in STEP-69/70) \u2014 PRD REQ-9 prohibits\n- **Add Tools Library page** with full CRUD (list, detail, editor views) \u2014 PRD REQ-4\n- **Add Alembic migration steps** for workflow_entity_refs, tools, and actor_slots tables\n- **Add GET /api/{entity}/references/{id}** endpoint \u2014 PRD REQ-3 delete preflight\n- **Add SF-5 mutation hook subscription** \u2014 register refresh/purge callbacks at FastAPI lifespan startup\n- Add APScheduler reconciliation job for periodic workflow_entity_refs resync\n- Add POST /api/admin/reconcile-entity-refs with admin-only authorization\n- Fix EntityDeleteDialog to use dedicated GET preflight (not DELETE 409 pattern)\n- Fix RoleEditorForm: form-based editor (not 4-step wizard)\n- Fix actor_slots: JSON column on custom_task_templates (not separate table with per-row CRUD)\n- Add ActorSlotsEditor (CMP-137) as standalone reusable component\n- Add ResourceStateCard (CMP-138) implementation\n- Add LibraryCollectionPage (CMP-134) as reusable list shell\n- Fix component IDs to match Design\n- Add verification blocks for PRD J-3, J-4, J-5\n- Add entity-name sanitization regex \u2014 PRD REQ-8\n- Add 256KB JSON payload size limits with 413 responses \u2014 PRD REQ-8\n- Add rate limiting on SF-7 endpoints\n- Add JWT auth dependency on new endpoints\n- Remove plugin entries from EntityType union and type maps\n- Add stale-while-revalidate caching strategy\n- Fix ToolChecklistGrid to fetch from /api/tools (not hardcoded built-in only)\n- Fix path topology: tools/compose/ (not platform/compose/)",
        "reasoning": "Cycle 7 review found plan/system-design contradicts PRD. Full rewrite needed per RR-7.",
        "affected_subfeatures": [
          "libraries-registries"
        ],
        "affected_artifact_types": [
          "prd",
          "design",
          "plan",
          "system-design"
        ],
        "affected_requirement_ids": [],
        "cross_subfeature": false
      }
    ],
    "new_decisions": [
      "**D-GR-36**: All 7 subfeature plans and system designs are dispatched for full rewrite to align with their respective PRDs and Designs. Plans are the implementable artifacts and must match the PRD contract exactly.",
      "**D-GR-37**: SF-4 persistence model resolved in favor of D-GR-14 explicit store PluginNodes (System Design position). artifact_key auto-write (C-4) is rejected.",
      "**D-GR-38**: SF-2 expression security resolved in favor of AST allowlist + timeout + size limit (PRD/Design position). Bare exec() (D-SF2-5) is rejected.",
      "**D-GR-39**: SF-7 reference architecture resolved in favor of materialized workflow_entity_refs with mutation hook subscription (PRD/Design/System Design position). YAML-parsing-on-delete is rejected.",
      "**D-GR-40**: All cross-subfeature edge contracts are dispatched for full rewrite to reflect current type names, module paths, function signatures, and data contracts."
    ],
    "complete": true
  },
  "complete": true
}
```