## Broad Artifact (prd:broad)

{
  "title": "iriai-compose Workflow Creator",
  "overview": "Two deliverables: (1) Extend iriai-compose with a declarative DAG-based workflow format, loader, run() entry point, and custom testing framework. (2) Build iriai-workflows, a visual workflow composer webapp with Windows XP / MS Paint aesthetic, plus a tools.iriai.app hub for tool discovery. The declarative format uses a minimal primitive set (Ask, Map, Fold, Loop, Branch, Plugin) with typed edges, transforms, hooks, and phase groupings. The litmus test: iriai-build-v2's planning, develop, and bugfix workflows must be fully translatable, representable, and runnable in the new system.",
  "problem_statement": "Agent workflows in iriai-build-v2 are defined imperatively in Python — tightly coupled to implementation details, non-portable across projects, and invisible to users. Creating or modifying workflows requires deep knowledge of the iriai-compose Python subclass API. There is no way to visually design, inspect, share, or test workflow configurations. This limits workflow iteration speed and prevents the broader iriai platform developer community from building custom agent orchestration flows.",
  "target_users": "iriai platform developers on hobby tier and above. Power users who build agent orchestration workflows for software development automation. They understand agent roles, prompts, and multi-step execution but should not need to write Python to define workflows.",
  "structured_requirements": [
    {
      "id": "R1",
      "category": "Declarative Format",
      "description": "YAML-primary DAG format representing workflows as typed nodes and edges. Six primitive node types: Ask (atomic agent invocation), Map (parallel fan-out over collection), Fold (sequential iteration with accumulator), Loop (repeat until condition), Branch (conditional routing), Plugin (external service call). Edges carry typed data with optional named transform functions. Nodes have on_start/on_done hooks as named registered functions. YAML primary, JSON accepted (yaml.safe_load handles both).",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-1",
          "excerpt": "DAG with primitives, limit specialized subfunctions",
          "reasoning": "User chose DAG approach over full declarative patterns or templates"
        },
        {
          "type": "decision",
          "reference": "D-2",
          "excerpt": "Both Map and Fold primitives",
          "reasoning": "User confirmed need for both parallel fan-out and sequential accumulation with context"
        },
        {
          "type": "decision",
          "reference": "D-3",
          "excerpt": "Hooks on nodes for side effects",
          "reasoning": "User chose hooks over separate side-effect nodes or edge properties"
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/tasks.py",
          "excerpt": "Ask, Interview, Gate, Choose, Respond task types",
          "reasoning": "Existing task types decompose into Ask primitive compositions"
        }
      ]
    },
    {
      "id": "R2",
      "category": "Declarative Format",
      "description": "Phases as named groups of nodes with their own on_start/on_done hooks and skip conditions (e.g., skip phase if artifact already exists). Phases enforce sequential boundaries in the DAG and are represented as visual bounding boxes in the composer. Phases are saveable as reusable templates in a Phases Library.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-4",
          "excerpt": "Phases must be included on account of their start/stop hooks/conditionals",
          "reasoning": "User confirmed phases are not just cosmetic — they carry execution semantics"
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py",
          "excerpt": "PlanningWorkflow.build_phases() returns 6 sequential phases",
          "reasoning": "Existing workflows use phases as sequential execution boundaries"
        }
      ]
    },
    {
      "id": "R3",
      "category": "Declarative Format",
      "description": "Edge transforms as named pure functions (no agent calls) that reshape data between nodes. Four categories: schema transforms (serialize/deserialize Pydantic models), context assembly (merge multiple artifacts into prompts with tiered filtering), filtering/selection (choose which data flows based on conditions), and formatting (cosmetic reshaping like feedback formatting, URL injection). Transforms are registered by name and referenced in YAML.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py",
          "excerpt": "_build_subfeature_context() tiered merge, _format_feedback(), to_str()",
          "reasoning": "These imperative transforms must become declarative edge transforms"
        },
        {
          "type": "decision",
          "reference": "D-5",
          "excerpt": "Pure transforms belong on edges, agent transforms are nodes",
          "reasoning": "User confirmed the distinction between pure transforms and agent-powered transforms"
        }
      ]
    },
    {
      "id": "R4",
      "category": "Declarative Format",
      "description": "Plugins as first-class DAG participants. Services like hosting, workspace management, and preview servers are configured as plugins in the workflow composer and usable as nodes in the DAG. Each plugin declares its interface (inputs, outputs, configuration schema). The runner provides plugin implementations at execution time. Users can define custom plugins.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-6",
          "excerpt": "Plugins should be configurable in the workflow composer and plugged into the DAG",
          "reasoning": "User chose plugins as first-class nodes over implicit services or hooks"
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/env_setup.py",
          "excerpt": "LaunchPreviewServerTask, workspace_manager.setup_feature_workspace()",
          "reasoning": "Existing service integrations must become declarative plugin nodes"
        }
      ]
    },
    {
      "id": "R5",
      "category": "Declarative Format",
      "description": "Cost configuration in the schema — budget caps, model pricing references, and alert thresholds per node/phase. No runtime cost UI in the composer; this is metadata for future runners to enforce and report on. Every Ask node naturally produces cost data via the Claude Agent SDK's ResultMessage (total_cost_usd, usage).",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-7",
          "excerpt": "Cost tracking built into workflow representation, not displayed in builder UI",
          "reasoning": "User decided cost is a schema concern for future runners, not a composer UI feature"
        },
        {
          "type": "research",
          "reference": "Claude Agent SDK ResultMessage",
          "excerpt": "total_cost_usd, usage dict with input_tokens/output_tokens",
          "reasoning": "SDK provides cost data automatically per invocation"
        }
      ]
    },
    {
      "id": "R6",
      "category": "Declarative Format",
      "description": "Schema versioning support. Each workflow config has a version identifier. The format supports diffing between versions. Future runners use version metadata for hot-swap decisions. The composer maintains version history per workflow.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-8",
          "excerpt": "Hot-swap not a builder concern — builder just produces versioned configs",
          "reasoning": "User chose to keep hot-swap as a runner concern, builder just versions"
        }
      ]
    },
    {
      "id": "R7",
      "category": "Runtime",
      "description": "Top-level run() function in iriai-compose that any consumer can call: load YAML, hydrate into executable DAG, execute against provided runtimes and workspaces. This is the primary entry point for running declarative workflows. Any project importing iriai-compose gets this capability.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-9",
          "excerpt": "run() function in iriai-compose, not exposed as separate validate/dry-run methods",
          "reasoning": "User wants a single entry point any app can call to run schema-defined workflows"
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py",
          "excerpt": "DefaultWorkflowRunner.execute_workflow()",
          "reasoning": "Existing runner provides execution infrastructure; run() wraps YAML loading + execution"
        }
      ]
    },
    {
      "id": "R8",
      "category": "Runtime",
      "description": "Custom testing framework in iriai-compose (iriai_compose.testing) built alongside the schema during development. Validates schema correctness (structural, type flow across edges). Runs workflows against mock/echo runtimes to verify execution paths. Asserts that specific nodes get reached, artifacts get produced, branches take expected paths. Serves as the regression suite proving the litmus test passes.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "Custom testing framework built as we develop the schema",
          "reasoning": "User wants purpose-built testing, not generic validation"
        },
        {
          "type": "code",
          "reference": "iriai-compose/tests/conftest.py",
          "excerpt": "MockAgentRuntime records calls with role, prompt, output_type",
          "reasoning": "Existing mock infrastructure can be extended for the testing framework"
        }
      ]
    },
    {
      "id": "R9",
      "category": "Runtime",
      "description": "Migration of iriai-build-v2's three workflows (planning, develop, bugfix) from imperative Python to declarative YAML. This serves as both the litmus test for format completeness and the first real content in the system. Each workflow must be fully translatable, representable, and produce identical execution behavior when run through the new system.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-11",
          "excerpt": "Migration plan for converting existing iriai-build-v2 workflows",
          "reasoning": "User explicitly requested migration as a requirement, not just aspirational"
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py",
          "excerpt": "per_subfeature_loop, gate_and_revise, compile_artifacts, interview_gate_review",
          "reasoning": "Most complex workflow patterns that must be representable in declarative format"
        }
      ]
    },
    {
      "id": "R10",
      "category": "Platform",
      "description": "Tools hub at tools.iriai.app — minimal authenticated launcher page. Reads dev_tier claim from auth-service JWT (hobby or pro). Displays grid of tool cards, tier-gated. Workflow composer available to hobby+ tier. Links to individual tool URLs. Uses auth-react for authentication.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-12",
          "excerpt": "tools.iriai.app with minimal launcher, tier-gated via JWT dev_tier",
          "reasoning": "User defined the platform entry point and tier gating model"
        },
        {
          "type": "code",
          "reference": "platform/auth/auth-service/app/routers/oauth.py:1196",
          "excerpt": "dev_tier: user.dev_tier in token_claims",
          "reasoning": "JWT already includes dev_tier claim — no auth-service changes needed"
        }
      ]
    },
    {
      "id": "R11",
      "category": "Composer App",
      "description": "Workflow composer as a separate webapp (React + FastAPI + SQLite). Windows XP / MS Paint aesthetic matching the deploy-console design system (purple gradients, 3D beveled effects, frosted glass taskbar). Auth via homelocal auth-react (frontend) and auth-python (backend). Deployed on Railway.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Design inspired by MS Paint / current dev platform",
          "reasoning": "User specified Windows XP aesthetic matching deploy-console"
        },
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css",
          "excerpt": "XP-style inset/outset borders, purple gradients, frosted glass taskbar",
          "reasoning": "Existing design system to match/extend"
        }
      ]
    },
    {
      "id": "R12",
      "category": "Composer App",
      "description": "Workflows List page — landing page showing grid/list of saved workflow configs. Each card shows name, description, last modified, version count. Actions: create new, duplicate, import YAML, delete, search/filter.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-14",
          "excerpt": "Screen map confirmed with workflows list as landing page",
          "reasoning": "User approved the screen map structure"
        }
      ]
    },
    {
      "id": "R13",
      "category": "Composer App",
      "description": "Workflow Editor — primary screen with dual-pane layout. Visual DAG canvas (React Flow) as the primary editing surface. Collapsible YAML pane for inspection and power-user editing (secondary, not featured). Node palette sidebar with all 6 primitives plus saved custom tasks and phases. Phase grouping as visual bounding boxes. Node inspector panel with context-specific configuration per node type. Edge inspector with transform selection and type annotations. Inline sub-canvases for Map/Fold/Loop. Toolbar with save, export YAML, validate, version history, undo/redo.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-15",
          "excerpt": "Dual-pane with visual graph editor primary, YAML secondary",
          "reasoning": "User chose dual-pane but specified YAML should not be the featured editing mode"
        },
        {
          "type": "decision",
          "reference": "D-16",
          "excerpt": "Inline sub-canvases for Map/Fold/Loop",
          "reasoning": "User confirmed sub-canvas interaction model over separate tabs"
        },
        {
          "type": "research",
          "reference": "Flowise AgentFlow V2 Iteration Node",
          "excerpt": "Nested nodes visually inside container node boundaries",
          "reasoning": "Industry pattern for sub-flow containment"
        }
      ]
    },
    {
      "id": "R14",
      "category": "Composer App",
      "description": "Ask node inspector — context-specific configuration: actor selection (pick from role library or create inline), prompt template editor with {{ variable }} interpolation from upstream outputs, output schema selection (reference from schemas library or raw JSON schema editor), hooks selection (on_start/on_done from registered functions), settings (timeout, max_turns, model override, budget cap).",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-17",
          "excerpt": "Each primitive has specific configuration in the inspector",
          "reasoning": "User required every primitive to have a clear configuration surface"
        },
        {
          "type": "research",
          "reference": "Claude Agent SDK structured outputs",
          "excerpt": "output_format accepts Pydantic.model_json_schema(), works with tool use",
          "reasoning": "Output schema in Ask maps directly to SDK's structured output enforcement"
        }
      ]
    },
    {
      "id": "R15",
      "category": "Composer App",
      "description": "Map/Fold/Loop/Branch node inspectors — Map: collection source reference, max parallelism, inline sub-canvas. Fold: collection source, accumulator init, inline sub-canvas. Loop: condition expression, max iterations, inline sub-canvas. Branch: condition type (expression or AI-driven), named output paths, per-path routing to downstream nodes.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-17",
          "excerpt": "Each primitive has specific configuration in the inspector",
          "reasoning": "User required comprehensive configuration for all primitives"
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py",
          "excerpt": "per_subfeature_loop with tiered context accumulation",
          "reasoning": "Fold primitive must handle this pattern — sequential with accumulator"
        }
      ]
    },
    {
      "id": "R16",
      "category": "Composer App",
      "description": "Roles Library — dedicated page for CRUD of agent roles. System prompt editor, tool selector, model picker, metadata fields. Import/export CLAUDE.md format. Inline creation from workflow editor with promotion to library for reuse.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "User chose the hybrid model — create in-context or pick from library"
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/actors.py:8-16",
          "excerpt": "Role(name, prompt, tools, model, metadata)",
          "reasoning": "Role model defines what the library manages"
        }
      ]
    },
    {
      "id": "R17",
      "category": "Composer App",
      "description": "Output Schemas Library — dedicated page or section with JSON schema editor for reusable structured output definitions. Referenced by Ask nodes for Claude SDK output_format enforcement.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-19",
          "excerpt": "JSON schema editor, not visual field builder",
          "reasoning": "User chose raw JSON schema editor over visual schema builder"
        }
      ]
    },
    {
      "id": "R18",
      "category": "Composer App",
      "description": "Custom Task Templates — dedicated page for saving subgraph compositions as reusable single-node templates with defined input/output interfaces. Appear in the node palette alongside primitives. Like Flowise Execute Flow pattern.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-17",
          "excerpt": "Custom task templates as saved subgraphs",
          "reasoning": "User confirmed reusable subgraph templates as a requirement"
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py",
          "excerpt": "gate_and_revise, per_subfeature_loop — reusable helper patterns",
          "reasoning": "These imperative helpers become declarative task templates"
        }
      ]
    },
    {
      "id": "R19",
      "category": "Composer App",
      "description": "Phases Library — dedicated page for saving phase templates (named groups of nodes with hooks/conditions) as reusable, droppable units across workflows.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-20",
          "excerpt": "Phases library for reusable phase templates",
          "reasoning": "User explicitly added phases library as a missing requirement"
        }
      ]
    },
    {
      "id": "R20",
      "category": "Composer App",
      "description": "Plugins Registry — dedicated page for browsing and configuring available plugins. Each plugin declares its parameter schema and I/O types. Users configure plugin instances here and use them as nodes in the DAG.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-6",
          "excerpt": "Plugins configurable in workflow composer, plugged into DAG",
          "reasoning": "User chose plugins as first-class configurable DAG participants"
        }
      ]
    },
    {
      "id": "R21",
      "category": "Composer App",
      "description": "Transforms & Hooks Library — dedicated page or section for named pure functions used as edge transforms and node hooks. Shows function signature (input type to output type) and code preview.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-5",
          "excerpt": "Pure transforms on edges, registered by name",
          "reasoning": "Transforms need a management surface for discovery and configuration"
        }
      ]
    },
    {
      "id": "R22",
      "category": "Composer App",
      "description": "Version History — per-workflow version list, diff between versions, restore to previous version. Integrated into the workflow editor or as a dedicated view.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-8",
          "excerpt": "Builder produces versioned configs",
          "reasoning": "Versioning is the builder's contribution to the hot-swap story"
        }
      ]
    },
    {
      "id": "R23",
      "category": "Testing",
      "description": "Comprehensive testing plan covering: (1) Development-time — unit tests for iriai-compose primitives and loader, integration tests for the testing framework against mock runtimes, E2E tests for the composer UI (React + API), API contract tests for FastAPI endpoints. (2) Post-development verification — the litmus test: all 3 iriai-build-v2 workflows translated to YAML, loaded, and run through mock runtimes with assertions on execution paths, artifact production, and branch decisions.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-21",
          "excerpt": "Comprehensive testing plan for during development and post-development verification",
          "reasoning": "User explicitly required testing at both development and verification stages"
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "User exports a workflow YAML from the composer and runs it via iriai-compose run()",
      "expected_observation": "The workflow executes successfully against provided runtimes, producing the expected artifacts and following the expected DAG execution order",
      "not_criteria": "",
      "requirement_ids": [
        "R1",
        "R7"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-9",
          "excerpt": "run() function in iriai-compose any app can call",
          "reasoning": "Primary entry point for executing declarative workflows"
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "Developer translates iriai-build-v2's planning workflow to declarative YAML",
      "expected_observation": "All 6 phases (scoping, PM, design, architecture, plan review, task planning) are representable. Key patterns — per-subfeature Fold with tiered context, gate-and-revise loops, compilation, interview-based gate review — all work. Testing framework assertions pass.",
      "not_criteria": "",
      "requirement_ids": [
        "R1",
        "R2",
        "R3",
        "R8",
        "R9"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/planning/phases/pm.py",
          "excerpt": "broad_interview, decompose_and_gate, per_subfeature_loop, integration_review, targeted_revision, compile_artifacts, interview_gate_review",
          "reasoning": "All helper functions must be representable as primitive compositions"
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "Developer translates iriai-build-v2's develop workflow (implementation phase) to declarative YAML",
      "expected_observation": "DAG execution groups (parallel within group, sequential across groups), per-group verification with retry, handover document compression, QA → review → user approval loop — all representable and passing tests",
      "not_criteria": "",
      "requirement_ids": [
        "R1",
        "R8",
        "R9"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/develop/phases/implementation.py",
          "excerpt": "_implement_dag with parallel groups, _verify, handover.compress()",
          "reasoning": "Implementation phase patterns must work in declarative format"
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "Developer translates iriai-build-v2's bugfix workflow to declarative YAML",
      "expected_observation": "Linear 8-phase flow with parallel RCA (dual analyst pattern), diagnosis-and-fix retry loop, preview server plugin integration — all representable and passing tests",
      "not_criteria": "",
      "requirement_ids": [
        "R1",
        "R4",
        "R8",
        "R9"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py",
          "excerpt": "Parallel RCA analysts, bug_fixer adjudication, verification loop",
          "reasoning": "Bugfix patterns including parallel analysis must be representable"
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "User edits a workflow in the visual canvas and checks the YAML pane",
      "expected_observation": "YAML pane updates in real-time to reflect canvas changes. Editing YAML updates the canvas. Round-trip is lossless — no data lost when switching between views.",
      "not_criteria": "",
      "requirement_ids": [
        "R13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-15",
          "excerpt": "Dual-pane with visual graph editor primary, YAML secondary",
          "reasoning": "Both panes must stay in sync"
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "User logs into tools.iriai.app with a hobby-tier account",
      "expected_observation": "Tools hub shows workflow composer card as enabled. Pro-only tool cards are visible but disabled/locked with tier upgrade prompt.",
      "not_criteria": "",
      "requirement_ids": [
        "R10"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "platform/auth/auth-service/app/routers/oauth.py:1196",
          "excerpt": "dev_tier: user.dev_tier in JWT claims",
          "reasoning": "Tier gating reads directly from JWT"
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "User creates a role inline on an Ask node and promotes it to the library",
      "expected_observation": "Role appears in the Roles Library and is selectable from other Ask nodes in the same or different workflows",
      "not_criteria": "",
      "requirement_ids": [
        "R16"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "Inline roles must be promotable to reusable library entries"
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "User creates a workflow with 50+ nodes on the canvas",
      "expected_observation": "Canvas remains responsive — zoom, pan, node selection, and inspector panel all perform without perceptible lag",
      "not_criteria": "Does not need to support 500+ node workflows in initial release",
      "requirement_ids": [
        "R13"
      ],
      "citations": [
        {
          "type": "research",
          "reference": "React Flow performance documentation",
          "excerpt": "React.memo and selective rendering for large graphs",
          "reasoning": "Performance is a known concern for large node graphs"
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Create a workflow from scratch",
      "actor": "Platform developer (hobby+ tier)",
      "preconditions": "User is authenticated on the iriai platform with hobby or pro tier",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "User navigates to tools.iriai.app and sees the workflow composer card (available for hobby+ tier)",
          "observes": "Tool cards displayed, composer card is enabled/clickable",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 2,
          "action": "User clicks the composer card and is routed to the composer app",
          "observes": "Workflows List page loads showing any existing workflows",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 3,
          "action": "User clicks 'New Workflow', enters name and description",
          "observes": "Empty workflow editor canvas opens with node palette sidebar visible",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 4,
          "action": "User drags an Ask node from the palette onto the canvas",
          "observes": "Ask node appears on canvas, node inspector panel opens on the right",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 5,
          "action": "User configures the Ask node: creates a role inline (system prompt, tools, model), writes a prompt template with {{ variable }} interpolation, selects an output schema from the library",
          "observes": "Inspector shows all configuration fields, role is created inline with option to promote to library",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 6,
          "action": "User drags a Branch node and connects an edge from the Ask output",
          "observes": "Edge appears with type annotation showing the data type flowing. Edge inspector allows adding a transform",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 7,
          "action": "User adds a Loop node on the rejection path of the Branch and connects it back to form a gate-and-revise pattern",
          "observes": "Loop node contains an inline sub-canvas for the retry body. DAG structure is clearly visible",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 8,
          "action": "User selects multiple nodes and groups them into a Phase, configuring phase hooks and skip conditions",
          "observes": "Visual bounding box appears around the selected nodes with phase label and configuration",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 9,
          "action": "User clicks Validate in the toolbar",
          "observes": "Validation runs — type flow across edges is checked, required fields verified, any errors highlighted on the canvas",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 10,
          "action": "User clicks Save, then Export YAML",
          "observes": "Workflow saved to database with version 1. YAML file downloaded. YAML pane shows the generated output",
          "not_criteria": "",
          "citations": []
        }
      ],
      "outcome": "A new declarative workflow YAML is saved, validated, and exportable for use with any iriai-compose runner",
      "related_journey_id": "",
      "requirement_ids": [
        "R1",
        "R2",
        "R10",
        "R12",
        "R13",
        "R14",
        "R15",
        "R16"
      ]
    },
    {
      "id": "J-2",
      "name": "Translate iriai-build-v2 planning workflow to declarative format",
      "actor": "Platform developer / migration engineer",
      "preconditions": "iriai-build-v2 planning workflow exists as imperative Python. Declarative format and primitives are implemented in iriai-compose.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Developer analyzes the 6 phases of the planning workflow (scoping, PM, design, architecture, plan review, task planning)",
          "observes": "Each phase maps to a Phase group in the declarative format with appropriate hooks",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 2,
          "action": "Developer creates roles for all actors (lead_pm, pm_decomposer, pm_compiler, designer, architect, reviewers, user) in the Roles Library",
          "observes": "Roles saved with system prompts, tool lists, and model preferences from iriai-build-v2",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 3,
          "action": "Developer builds the PM phase DAG: broad interview (Ask loop) → decompose (Ask + Branch for gate) → per-subfeature Fold (with tiered context edge transform) → integration review → conditional revision (Branch) → compile (Ask) → interview gate review (Loop with nested Ask + Branch)",
          "observes": "All patterns representable using Ask, Fold, Loop, Branch primitives with edge transforms",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 4,
          "action": "Developer saves the gate-and-revise pattern as a Custom Task Template for reuse across phases",
          "observes": "Template appears in the node palette, usable as a single node in design and architecture phases",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 5,
          "action": "Developer exports the complete YAML and runs it through iriai-compose's testing framework with mock runtimes",
          "observes": "All nodes reached in expected order, artifacts produced at correct keys, branch decisions match expected paths",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 6,
          "action": "Developer runs the YAML workflow via iriai-compose run() with real Claude runtimes",
          "observes": "Workflow executes identically to the imperative Python version — same phases, same actor interactions, same artifact outputs",
          "not_criteria": "",
          "citations": []
        }
      ],
      "outcome": "Planning workflow is fully represented as YAML DAG, passes validation, and executes identically through iriai-compose run()",
      "related_journey_id": "",
      "requirement_ids": [
        "R1",
        "R2",
        "R3",
        "R4",
        "R7",
        "R8",
        "R9",
        "R18"
      ]
    },
    {
      "id": "J-3",
      "name": "Build and reuse a custom task template",
      "actor": "Platform developer",
      "preconditions": "User has the workflow composer open with an existing workflow",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "User builds a subgraph: Ask (produce artifact) → Branch (gate approval) → on rejection: Loop back with Ask (revise with feedback)",
          "observes": "Subgraph visible on canvas with correct edge connections",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 2,
          "action": "User selects the subgraph nodes and clicks 'Save as Template'",
          "observes": "Dialog prompts for template name, description, and input/output interface definition",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 3,
          "action": "User defines inputs (actor role, prompt, output schema) and outputs (approved artifact text)",
          "observes": "Template saved to Custom Task Templates library",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 4,
          "action": "In a different workflow, user drags the saved template from the node palette",
          "observes": "Template appears as a single node with the defined input/output ports",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 5,
          "action": "User configures the template node's inputs (selects a designer role, provides design prompt)",
          "observes": "Template instance configured, internal subgraph hidden but viewable via expand/inspect",
          "not_criteria": "",
          "citations": []
        }
      ],
      "outcome": "A reusable gate-and-revise pattern is saved as a template and used across multiple workflows",
      "related_journey_id": "",
      "requirement_ids": [
        "R13",
        "R18"
      ]
    },
    {
      "id": "J-4",
      "name": "Validation catches type mismatch on edge",
      "actor": "Platform developer",
      "preconditions": "User is building a workflow with multiple connected nodes",
      "path_type": "failure",
      "failure_trigger": "User connects an Ask node outputting a PRD schema to a Branch node expecting a Verdict schema",
      "steps": [
        {
          "step_number": 1,
          "action": "User draws an edge from an Ask node (output_type: PRD) to a Branch node (expects: Verdict with .approved boolean)",
          "observes": "Edge appears with a warning indicator (type mismatch)",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 2,
          "action": "User clicks Validate",
          "observes": "Validation error highlights the edge: 'Type mismatch — PRD does not satisfy Verdict. Add a transform or insert a node.'",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 3,
          "action": "User clicks the edge and adds a transform from the Transforms Library, or inserts an Ask node between them to produce a Verdict from the PRD",
          "observes": "Validation re-runs automatically, error clears, edge shows green type annotation",
          "not_criteria": "",
          "citations": []
        }
      ],
      "outcome": "User is alerted to a type mismatch between connected nodes and can fix it by adding a transform",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "R3",
        "R13",
        "R14"
      ]
    },
    {
      "id": "J-5",
      "name": "Configure and use a plugin in the DAG",
      "actor": "Platform developer",
      "preconditions": "User needs artifact hosting in their workflow (like iriai-build-v2's HostedInterview pattern)",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "User navigates to Plugins Registry and finds the hosting plugin",
          "observes": "Plugin card shows its interface: inputs (artifact_key, content, label), outputs (url), and configuration (hosting_url)",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 2,
          "action": "User configures a plugin instance with their hosting URL",
          "observes": "Configured instance saved and available in the node palette",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 3,
          "action": "User drags the hosting plugin node into the workflow after an Ask node that produces an artifact",
          "observes": "Plugin node appears with input ports matching the declared interface",
          "not_criteria": "",
          "citations": []
        },
        {
          "step_number": 4,
          "action": "User connects the Ask output to the plugin input, optionally adding an edge transform to extract the artifact text",
          "observes": "Edge shows type flow: Ask output → transform → plugin input. Plugin node shows it will host the artifact",
          "not_criteria": "",
          "citations": []
        }
      ],
      "outcome": "A hosting plugin is configured and used as a node in the workflow DAG",
      "related_journey_id": "",
      "requirement_ids": [
        "R4",
        "R13",
        "R20"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "No specific compliance requirements. Standard platform security practices apply.",
    "data_sensitivity": "Workflow configs may contain system prompts with proprietary process knowledge. Role definitions may contain sensitive operational instructions. No PII stored directly.",
    "pii_handling": "No PII in workflow configs. User identity (user_id from JWT sub claim) associated with owned resources for access control.",
    "auth_requirements": "JWT-based authentication via auth-service. All composer API endpoints require valid access token. Tools hub reads dev_tier claim for tier gating. Backend validates JWT via auth-python (JWKS endpoint).",
    "data_retention": "Workflow configs and versions retained indefinitely. No automatic purging.",
    "third_party_exposure": "Exported YAML files may be shared externally. They contain role prompts and workflow structure but no credentials or secrets. Plugin configurations may reference external service URLs.",
    "data_residency": "SQLite database local to the FastAPI backend deployment. Workflow YAML exports are portable files.",
    "risk_mitigation_notes": "Transforms and hooks reference Python functions by name — the runner resolves them at execution time, not the builder. No arbitrary code execution in the composer. Plugin credentials are stored in the runner environment, not in the YAML."
  },
  "data_entities": [
    {
      "name": "Workflow",
      "fields": [
        "id: uuid",
        "name: string",
        "description: string",
        "yaml_content: text",
        "current_version: integer",
        "created_at: datetime",
        "updated_at: datetime",
        "user_id: string"
      ],
      "constraints": [
        "name unique per user",
        "yaml_content must pass schema validation"
      ],
      "is_new": true
    },
    {
      "name": "WorkflowVersion",
      "fields": [
        "id: uuid",
        "workflow_id: fk",
        "version_number: integer",
        "yaml_content: text",
        "created_at: datetime",
        "change_description: string"
      ],
      "constraints": [
        "version_number auto-increments per workflow"
      ],
      "is_new": true
    },
    {
      "name": "Role",
      "fields": [
        "id: uuid",
        "name: string",
        "system_prompt: text",
        "tools: json_array",
        "model: string nullable",
        "metadata: json",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "name unique per user"
      ],
      "is_new": true
    },
    {
      "name": "OutputSchema",
      "fields": [
        "id: uuid",
        "name: string",
        "json_schema: json",
        "description: string",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "json_schema must be valid JSON Schema"
      ],
      "is_new": true
    },
    {
      "name": "CustomTaskTemplate",
      "fields": [
        "id: uuid",
        "name: string",
        "description: string",
        "subgraph_yaml: text",
        "input_interface: json",
        "output_interface: json",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "subgraph_yaml must pass schema validation"
      ],
      "is_new": true
    },
    {
      "name": "PhaseTemplate",
      "fields": [
        "id: uuid",
        "name: string",
        "description: string",
        "nodes_yaml: text",
        "hooks: json",
        "skip_conditions: json",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "nodes_yaml must pass schema validation"
      ],
      "is_new": true
    },
    {
      "name": "PluginConfig",
      "fields": [
        "id: uuid",
        "plugin_type: string",
        "instance_name: string",
        "configuration: json",
        "parameter_schema: json",
        "io_types: json",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "instance_name unique per user and plugin_type"
      ],
      "is_new": true
    },
    {
      "name": "TransformFunction",
      "fields": [
        "id: uuid",
        "name: string",
        "input_type: string",
        "output_type: string",
        "code: text",
        "description: string",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "name unique per user"
      ],
      "is_new": true
    }
  ],
  "cross_service_impacts": [
    {
      "service": "iriai-compose",
      "impact": "Major extension — new declarative format, YAML loader, DAG runner, primitive node types (Ask, Map, Fold, Loop, Branch, Plugin), edge transform system, hook system, phase groupings, run() entry point, and testing framework (iriai_compose.testing)",
      "action_needed": "Extend library with new modules: schema definition, loader, DAG executor, testing framework. Existing Python subclass API can be broken if needed — new declarative format supersedes it."
    },
    {
      "service": "iriai-build-v2",
      "impact": "Read-only reference. Its 3 workflows (planning, develop, bugfix) are the litmus test — must be translatable to declarative YAML.",
      "action_needed": "No code changes. Analyze workflows to extract patterns and validate format completeness. Produce equivalent YAML representations."
    },
    {
      "service": "auth-service",
      "impact": "No changes needed. JWT already includes dev_tier claim.",
      "action_needed": "None — existing JWT claims sufficient for tier gating."
    },
    {
      "service": "tools.iriai.app (new)",
      "impact": "New minimal frontend app. Reads JWT, displays tier-gated tool cards.",
      "action_needed": "Build new React SPA with auth-react integration. Deploy on Railway."
    },
    {
      "service": "deploy-console-frontend",
      "impact": "Design system reference. Windows XP theme CSS to be replicated or extracted into shared package.",
      "action_needed": "Consider extracting windows-xp.css and UI components into a shared @iriai/ui package, or copy theme files."
    }
  ],
  "open_questions": [
    "Should the Windows XP theme CSS be extracted into a shared @iriai/ui package, or should iriai-workflows copy the theme files from deploy-console?",
    "What URL should the workflow composer live at? (e.g., compose.iriai.app, workflows.iriai.app)",
    "For the migration: should the 3 translated iriai-build-v2 workflows ship as built-in starter templates in the composer?",
    "How should transforms and hooks be distributed? As a built-in library in iriai-compose, or user-defined in the composer, or both?",
    "Should the YAML format support $ref for reusable inline definitions (like JSON Schema $ref), or should all reuse go through the library system?"
  ],
  "requirements": [],
  "acceptance_criteria": [],
  "out_of_scope": [
    "Multi-user collaboration on workflow configs",
    "Runtime agent execution inside the composer — it is a builder/config tool only",
    "Cost dashboards or analytics UI — cost configuration lives in the YAML schema for future runners",
    "Hot-swap UI — the builder produces versioned configs, runners handle swap mechanics",
    "Migration tooling from iriai-build v1 (legacy)",
    "Quality or subjective scoring — cost tracking limited to token counts and USD",
    "Mobile-responsive design — desktop-first tool for developers"
  ],
  "complete": true
}

---

## Decomposition

{
  "subfeatures": [
    {
      "id": "SF-1",
      "slug": "declarative-schema",
      "name": "Declarative Schema & Primitives",
      "description": "Define the YAML-primary DAG format in iriai-compose as Pydantic models and JSON Schema. Six primitive node types (Ask, Map, Fold, Loop, Branch, Plugin) with typed configuration. Typed edges with optional named transform references. Phase groupings with on_start/on_done hooks and skip conditions. Plugin interface declarations (inputs, outputs, config schema). Cost configuration metadata (budget caps, model pricing, alert thresholds per node/phase). Schema versioning field. No execution logic — this is pure data modeling and validation. Produces the schema that the loader, runner, testing framework, and composer UI all consume.",
      "rationale": "The schema is the foundational contract for the entire system. Everything else — runtime execution, testing, visual editing — depends on this format definition being stable and complete. Isolating it ensures the format is designed for all consumers, not biased toward any single one.",
      "requirement_ids": [
        "R1",
        "R2",
        "R3",
        "R4",
        "R5",
        "R6"
      ],
      "journey_ids": [
        "J-1",
        "J-2",
        "J-4"
      ]
    },
    {
      "id": "SF-2",
      "slug": "dag-loader-runner",
      "name": "DAG Loader & Runner",
      "description": "Build the YAML loader that hydrates declarative configs into executable DAG objects, and the top-level run() entry point in iriai-compose. Loader: parse YAML, validate against schema, resolve node references, build dependency graph, wire typed edges. Runner: topological sort for execution order, respect phase boundaries, execute nodes against provided AgentRuntime instances, manage artifact flow between nodes via edge transforms, resolve named transforms/hooks from a registry, handle Map (parallel fan-out), Fold (sequential accumulation), Loop (repeat-until), Branch (conditional routing), and Plugin (external service delegation). Extends existing DefaultWorkflowRunner infrastructure.",
      "rationale": "The loader and runner are tightly coupled — you can't meaningfully test loading without running, and the runner's needs (topological execution, artifact passing, transform resolution) directly inform how the loader hydrates the schema. Grouping them ensures the hydration format matches execution needs.",
      "requirement_ids": [
        "R7"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-3",
      "slug": "testing-framework",
      "name": "Testing Framework",
      "description": "Build iriai_compose.testing — a purpose-built testing module for declarative workflows. Schema validation: structural correctness, type flow across edges, required fields, cycle detection. Execution testing: mock/echo AgentRuntime that records calls and returns configurable responses, execution path assertions (assert node X reached before node Y, assert artifact produced at key K, assert branch took path P), snapshot testing for YAML round-trips. Test fixtures: helpers to build minimal valid workflows programmatically for unit tests. Extends existing MockAgentRuntime from conftest.py. This framework is used by SF-4 (migration) to prove the litmus test and by any future workflow developer for regression testing.",
      "rationale": "A dedicated testing framework is distinct from both the runtime (SF-2) and the migration (SF-4). It produces reusable infrastructure — mock runtimes, assertion helpers, fixtures — that the migration exercises but doesn't define. Keeping it separate ensures the framework is general-purpose, not migration-specific.",
      "requirement_ids": [
        "R8",
        "R23"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-4",
      "slug": "workflow-migration",
      "name": "Workflow Migration & Litmus Test",
      "description": "Translate iriai-build-v2's three workflows (planning, develop, bugfix) from imperative Python to declarative YAML. Planning: 6 phases (scoping, PM, design, architecture, plan review, task planning) with patterns including broad interview loops, decomposition with gate, per-subfeature Fold with tiered context assembly, integration review, gate-and-revise loops, compilation, and interview-based gate review. Develop: DAG execution groups (parallel within group, sequential across), per-group verification with retry, handover document compression, QA → review → user approval loop. Bugfix: linear 8-phase flow with parallel RCA (dual analyst), diagnosis-and-fix retry loop, preview server plugin integration. Register all required named transforms (tiered context builder, handover compression, feedback formatting, etc.) and hooks. Write comprehensive test suites using the SF-3 testing framework proving execution path equivalence.",
      "rationale": "The migration is both the completeness proof for the schema (SF-1) and the first real content in the system. It requires deep analysis of iriai-build-v2's imperative code — a fundamentally different skill from schema design or framework building. Keeping it separate lets the migration reveal schema gaps without being conflated with schema development.",
      "requirement_ids": [
        "R9"
      ],
      "journey_ids": [
        "J-2"
      ]
    },
    {
      "id": "SF-5",
      "slug": "composer-app-foundation",
      "name": "Composer App Foundation & Tools Hub",
      "description": "Scaffold the iriai-workflows webapp (React + FastAPI + SQLite) and the tools.iriai.app hub. Backend: FastAPI app structure, SQLAlchemy models for all 8 data entities (Workflow, WorkflowVersion, Role, OutputSchema, CustomTaskTemplate, PhaseTemplate, PluginConfig, TransformFunction), Alembic migrations, CRUD API endpoints for all entities, JWT auth via auth-python (JWKS validation), user_id scoping on all resources. Frontend: React app with routing, auth-react integration (login/logout, token management), Windows XP / MS Paint design system (purple gradients, 3D beveled effects, frosted glass taskbar — matching deploy-console), Workflows List landing page (grid/list of saved configs, create/duplicate/import/delete/search). Tools hub: minimal React SPA at tools.iriai.app reading dev_tier JWT claim, displaying tier-gated tool cards, linking to composer URL. Railway deployment configs for both apps.",
      "rationale": "The app foundation provides the infrastructure (auth, database, API, routing, design system) that both the editor (SF-6) and libraries (SF-7) build on. Including the tools hub here is natural — it's a single page sharing the same auth setup. This subfeature can be developed in parallel with the iriai-compose work (SF-1 through SF-4).",
      "requirement_ids": [
        "R10",
        "R11",
        "R12"
      ],
      "journey_ids": [
        "J-1"
      ]
    },
    {
      "id": "SF-6",
      "slug": "workflow-editor",
      "name": "Workflow Editor & Canvas",
      "description": "Build the primary workflow editing experience in iriai-workflows. React Flow DAG canvas as the main editing surface with drag-and-drop node placement. Node palette sidebar with all 6 primitives (Ask, Map, Fold, Loop, Branch, Plugin) plus custom task templates and phase templates from libraries. Collapsible YAML pane with bidirectional sync (canvas ↔ YAML, lossless round-trip). Node inspector panel with context-specific configuration: Ask (role picker/inline creator, prompt template editor with {{ variable }} interpolation, output schema selector, hooks, settings), Map/Fold/Loop (collection source, inline sub-canvas for body, max parallelism/iterations), Branch (condition type, named output paths). Edge inspector with transform selection and type annotations. Phase grouping as visual bounding boxes (select nodes → group into phase → configure hooks/skip conditions). Toolbar: save, export YAML, validate (type flow checking, required fields, error highlighting on canvas), version history access, undo/redo. Performance target: responsive with 50+ nodes.",
      "rationale": "The editor is the core user-facing deliverable — the visual canvas, node inspectors, YAML sync, and validation. It's the largest and most complex frontend subfeature. It consumes the schema (SF-1) to know what fields each node type needs, and consumes libraries (SF-7) for role/schema/template selection. Keeping it separate from libraries allows parallel development of the editing experience and the management surfaces.",
      "requirement_ids": [
        "R13",
        "R14",
        "R15"
      ],
      "journey_ids": [
        "J-1",
        "J-3",
        "J-4",
        "J-5"
      ]
    },
    {
      "id": "SF-7",
      "slug": "libraries-registries",
      "name": "Libraries & Registries",
      "description": "Build all six library/registry pages in iriai-workflows, plus the version history view. All follow a shared CRUD + list + detail/editor pattern. Roles Library: system prompt editor, tool selector, model picker, metadata fields, import/export CLAUDE.md format, inline-to-library promotion flow from the editor. Output Schemas Library: JSON Schema editor (raw editor, not visual field builder), name/description metadata, referenced by Ask nodes. Custom Task Templates: saved subgraph compositions with defined input/output interfaces, appear in node palette alongside primitives, expandable to inspect internal structure. Phases Library: saved phase templates (node groups + hooks + skip conditions), droppable into workflows as reusable units. Plugins Registry: browse available plugin types, configure instances with parameter schemas, see I/O type declarations, configured instances appear in node palette. Transforms & Hooks Library: named pure functions with input/output type signatures, code preview, used as edge transforms and node hooks. Version History: per-workflow version list, YAML diff between versions, restore to previous version.",
      "rationale": "All six libraries share the same UI pattern (list → detail → editor) and API pattern (CRUD endpoints scoped to user_id). Grouping them enables shared component extraction (list views, search/filter, editor chrome) and consistent UX. Individually each library is small; together they form a coherent subfeature of comparable complexity to the editor.",
      "requirement_ids": [
        "R16",
        "R17",
        "R18",
        "R19",
        "R20",
        "R21",
        "R22"
      ],
      "journey_ids": [
        "J-1",
        "J-2",
        "J-3",
        "J-5"
      ]
    }
  ],
  "edges": [
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-2",
      "interface_type": "python_import",
      "description": "Loader imports Pydantic schema models (WorkflowConfig, NodeDefinition, EdgeDefinition, PhaseDefinition, etc.) to parse and validate YAML into typed objects. Runner imports node type enums and config models to dispatch execution.",
      "data_contract": "iriai_compose.declarative.schema module exports: WorkflowConfig, AskNode, MapNode, FoldNode, LoopNode, BranchNode, PluginNode, Edge, Phase, CostConfig, TransformRef, HookRef. All are Pydantic BaseModel subclasses with JSON Schema generation via model_json_schema().",
      "owner": "SF-1",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/tasks.py",
          "excerpt": "Existing task types (Ask, Interview, Gate, Choose, Respond) as dataclass models",
          "reasoning": "New schema models follow the same pattern but as Pydantic models for YAML/JSON validation"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-3",
      "interface_type": "python_import",
      "description": "Testing framework imports schema models to validate structural correctness and type flow. Uses model_json_schema() for schema-level validation, field accessors for type flow checking across edges.",
      "data_contract": "Same schema module as SF-2 consumes. Additionally uses Edge.transform_ref and Node.output_type for type flow analysis.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "Custom testing framework built as we develop the schema",
          "reasoning": "Testing framework validates schema correctness as a primary function"
        }
      ]
    },
    {
      "from_subfeature": "SF-2",
      "to_subfeature": "SF-3",
      "interface_type": "python_import",
      "description": "Testing framework uses the runner's run() function and DAG executor to run workflows against mock runtimes. Wraps run() with assertion hooks to track execution paths, artifact production, and branch decisions.",
      "data_contract": "iriai_compose.declarative.run(yaml_path, runtime, workspace, transform_registry, hook_registry) → ExecutionResult. ExecutionResult contains: nodes_executed (ordered list), artifacts (dict), branch_paths_taken (dict), cost_summary.",
      "owner": "SF-2",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/tests/conftest.py",
          "excerpt": "MockAgentRuntime records calls with role, prompt, output_type",
          "reasoning": "Testing framework extends this mock pattern to work with the new runner"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-4",
      "interface_type": "yaml_schema",
      "description": "Migration produces YAML files conforming to the schema defined in SF-1. The schema must be expressive enough to represent all patterns found in iriai-build-v2's three workflows.",
      "data_contract": "YAML files validated against WorkflowConfig JSON Schema. Migration may surface schema gaps that require SF-1 revisions.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-11",
          "excerpt": "Migration plan for converting existing iriai-build-v2 workflows",
          "reasoning": "Migration is the completeness test for the schema"
        }
      ]
    },
    {
      "from_subfeature": "SF-2",
      "to_subfeature": "SF-4",
      "interface_type": "python_import",
      "description": "Migration uses run() to execute translated YAML workflows and verify they produce equivalent behavior to the imperative Python versions.",
      "data_contract": "Same run() interface as SF-3 consumes. Migration also registers named transforms and hooks via TransformRegistry.register(name, fn) and HookRegistry.register(name, fn).",
      "owner": "SF-2",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py",
          "excerpt": "_build_subfeature_context(), _format_feedback(), to_str()",
          "reasoning": "These imperative helpers must be registered as named transforms for the runner to resolve"
        }
      ]
    },
    {
      "from_subfeature": "SF-3",
      "to_subfeature": "SF-4",
      "interface_type": "python_import",
      "description": "Migration writes test suites using the testing framework's assertion helpers, mock runtimes, and fixtures to prove execution path equivalence.",
      "data_contract": "iriai_compose.testing exports: MockRuntime (configurable responses per role/node), assert_node_reached(result, node_id), assert_artifact_produced(result, key, schema), assert_branch_taken(result, branch_id, path), WorkflowTestCase base class.",
      "owner": "SF-3",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "Custom testing framework built as we develop the schema",
          "reasoning": "Migration is the primary consumer of the testing framework"
        }
      ]
    },
    {
      "from_subfeature": "SF-1",
      "to_subfeature": "SF-6",
      "interface_type": "json_schema",
      "description": "The workflow editor reads the JSON Schema (generated from SF-1's Pydantic models) to know what fields each node type requires, what edge types are valid, and what configuration options exist. The YAML pane serializes/deserializes using this schema. Validation uses it for type flow checking.",
      "data_contract": "JSON Schema published as a static artifact (e.g., workflow-schema.json) or fetched from a backend endpoint. Frontend uses it for: node inspector field generation, edge type validation, YAML syntax validation, export format.",
      "owner": "SF-1",
      "citations": [
        {
          "type": "decision",
          "reference": "D-15",
          "excerpt": "Dual-pane with visual graph editor primary, YAML secondary",
          "reasoning": "Both the canvas and YAML pane need to understand the schema for rendering and validation"
        }
      ]
    },
    {
      "from_subfeature": "SF-5",
      "to_subfeature": "SF-6",
      "interface_type": "api_and_components",
      "description": "App foundation provides: authenticated API client (axios with JWT interceptor), React router shell (editor is a route), design system components (XP-themed buttons, panels, inputs), database-backed workflow CRUD (save/load/export endpoints), and auth context (user_id for scoping).",
      "data_contract": "API endpoints: GET/PUT /api/workflows/:id (full YAML content), POST /api/workflows/:id/versions (save new version), POST /api/workflows/:id/validate (server-side validation). React context: useAuth() hook providing user, accessToken. Component library: XPButton, XPPanel, XPInput, XPToolbar, XPSidebar.",
      "owner": "SF-5",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-frontend/src/app/styles/windows-xp.css",
          "excerpt": "XP-style inset/outset borders, purple gradients, frosted glass taskbar",
          "reasoning": "Design system components from SF-5 are consumed by the editor"
        }
      ]
    },
    {
      "from_subfeature": "SF-5",
      "to_subfeature": "SF-7",
      "interface_type": "api_and_components",
      "description": "App foundation provides the same infrastructure as SF-6: authenticated API client, router shell (library pages are routes), design system components, and CRUD API endpoints for all 8 entity types.",
      "data_contract": "API endpoints: standard REST CRUD for /api/roles, /api/schemas, /api/templates, /api/phases, /api/plugins, /api/transforms. All scoped to authenticated user_id. Response format: { items: [...], total: int } for lists, individual entity for detail. Same React context and component library as SF-6.",
      "owner": "SF-5",
      "citations": [
        {
          "type": "decision",
          "reference": "D-14",
          "excerpt": "Screen map confirmed with workflows list as landing page",
          "reasoning": "Library pages are sibling routes to the workflows list, all sharing the app shell"
        }
      ]
    },
    {
      "from_subfeature": "SF-7",
      "to_subfeature": "SF-6",
      "interface_type": "react_components",
      "description": "Libraries expose picker/selector components consumed by the editor's node inspectors. Role picker for Ask nodes, schema selector for output_type, template browser for the node palette, plugin selector, transform picker for edge inspector.",
      "data_contract": "React components: RolePicker({ onSelect, onCreateInline }), SchemaPicker({ onSelect }), TemplateBrowser({ onDrag }), PluginPicker({ onSelect }), TransformPicker({ edgeType, onSelect }). Each fetches from its own API endpoint and renders in the XP design system.",
      "owner": "SF-7",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "The editor needs picker components that bridge to the library data"
        }
      ]
    },
    {
      "from_subfeature": "SF-6",
      "to_subfeature": "SF-7",
      "interface_type": "callback_events",
      "description": "Editor triggers library mutations: inline role creation promotes to library, subgraph selection saves as custom task template, node group saves as phase template. Editor emits these as callbacks that library components handle.",
      "data_contract": "Callbacks: onPromoteRole(inlineRole) → creates Role via API, onSaveTemplate(selectedNodes, edges, interface) → creates CustomTaskTemplate via API, onSavePhase(selectedNodes, hooks, skipConditions) → creates PhaseTemplate via API. Returns created entity ID for the editor to reference.",
      "owner": "SF-6",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Inline + library hybrid for roles",
          "reasoning": "Inline-to-library promotion requires the editor to trigger library writes"
        }
      ]
    }
  ],
  "decomposition_rationale": "The feature splits naturally along two axes: iriai-compose runtime (schema → loader → testing → migration) and iriai-workflows visual app (foundation → editor → libraries). The iriai-compose side forms a strict dependency chain where each layer builds on the previous. The iriai-workflows side has a foundation layer feeding two parallel workstreams (editor and libraries) that integrate at the edges. The tools hub is absorbed into the app foundation since it's a single page sharing the same auth infrastructure. This yields 7 subfeatures of roughly comparable complexity, with clear boundaries and explicit interface contracts between them.",
  "complete": true
}

---

## Subfeature: Declarative Schema & Primitives (declarative-schema)

{
  "title": "SF-1 — Declarative Schema & Primitives",
  "overview": "Declarative schema contract for workflows in `iriai-compose`, revised to the cycle-4 D-GR-22 plus D-GR-30 baseline and cycle-6 D-GR-35. This PRD is the canonical wire shape for loader, backend, editor, testing, and migration consumers: `WorkflowConfig` has a closed root field set, actors discriminate with `actor_type: agent|human`, ports are typed everywhere including hooks, `BranchNode` uses the D-GR-12 per-port conditions model (non-exclusive fan-out where each output port carries its own condition expression, optional `merge_function` for gather), hooks serialize only as ordinary edges, and the composer consumes JSON Schema from `/api/schema/workflow` while static `workflow-schema.json` remains build/test-only.",
  "problem_statement": "The schema is the foundational contract across `iriai-compose`, the composer backend, the workflow editor, and migration/testing tools. Cycle 4 closed the contract with D-GR-22 and D-GR-30. Cycle 6 added D-GR-35, which replaces the earlier exclusive single-path branch routing model with D-GR-12 per-port conditions: non-exclusive fan-out where each output port carries its own condition expression, plus optional `merge_function` for gathering multiple inputs. The schema must be explicit everywhere that nodes live inside phases, hooks are typed ports serialized only through ordinary edges, actors use `actor_type: agent|human`, the workflow root does not grow unapproved registries, and the composer's runtime schema source is `/api/schema/workflow`.",
  "target_users": "Platform engineers and workflow authors who define, translate, validate, render, and run declarative workflows. Primary consumers are: `iriai-compose` loader/runner code, the `iriai-workflows` backend and React editor, the testing/migration toolchain, and any external project importing `iriai-compose` and expecting a portable workflow contract.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "`WorkflowConfig` MUST remain YAML-first and include only `schema_version`, `workflow_version`, `name`, `description`, `metadata`, `actors`, `phases`, `edges`, `templates`, `plugins`, `types`, and `cost_config` at the root. Top-level `phases` are the only place nodes enter the document; top-level `edges` are reserved for cross-phase wiring; unapproved root additions such as `stores` and `plugin_instances` are invalid.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "YAML remains nested (`phases[].nodes`, `phases[].children`).",
          "reasoning": "The cycle-4 baseline makes nested phase containment the authoritative YAML contract with a closed root field set."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "`ActorDefinition` MUST use `actor_type` as its discriminator with only `agent` and `human` as valid values. `AgentActorDef` carries provider/model/role/persistent/context_keys semantics, and `HumanActorDef` carries identity/channel semantics without embedding environment-specific credentials or reviving `interaction` as a serialized alias.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-30",
          "excerpt": "actor_type: agent|human only — no interaction alias.",
          "reasoning": "Cycle-4 closed the actor discriminator contract."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "functional",
      "description": "The schema MUST expose exactly three atomic node types: `AskNode`, `BranchNode`, and `PluginNode`. `AskNode` is an atomic actor invocation with a `prompt` field. `BranchNode` uses the D-GR-12 per-port conditions model: each output port in `outputs` carries its own `condition` expression (non-exclusive fan-out — multiple ports may fire if their conditions are satisfied), and an optional `merge_function` handles gather semantics when multiple inputs converge. `PluginNode` invokes external capabilities. `switch_function` and `output_field` are not valid fields; `merge_function` is only valid on `BranchNode` for gather and is not a routing function.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "D-GR-12 per-port model is the single authority. Non-exclusive fan-out; switch_function remains rejected; merge_function valid for gather.",
          "reasoning": "Cycle-6 replaced the exclusive single-path routing model with per-port conditions across all subfeatures."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "`EdgeDefinition` MUST serialize connections with `source` and `target` dot notation plus optional `transform_fn`. It MUST NOT serialize a `port_type` field. Hook-vs-data behavior is determined by resolving the source port container (`hooks` vs `outputs`), and hook wiring is represented only as ordinary edges.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`.",
          "reasoning": "This is the specific contract correction the stale downstream artifacts must adopt."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "`PhaseDefinition` MUST be the primary execution container and include `id`, `name`, `mode`, a single discriminated-union `mode_config`, typed `inputs`/`outputs`/`hooks`, `context_keys`, `metadata`, `cost`, `nodes`, `children`, and phase-local `edges`. `nodes` serialize under `phases[].nodes`, nested phases serialize under `phases[].children`, and internal edges live with the owning phase.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML phase containment is authoritative.",
          "reasoning": "The broad architecture already models PhaseDefinition with nodes and children under the nested containment shape."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "functional",
      "description": "The schema MUST support four phase execution modes: `sequential`, `map`, `fold`, and `loop`. Iteration and fan-out semantics belong to phases, not to standalone Map/Fold/Loop node types. Each mode has a matching `ModeConfig` variant discriminated by the `mode` field.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "R3-2",
          "excerpt": "Execution modes belong on phases.",
          "reasoning": "The earlier phase-mode decision remains valid; ModeConfig is now explicitly a discriminated union."
        }
      ]
    },
    {
      "id": "REQ-7",
      "category": "functional",
      "description": "Loop-mode phases MUST expose two independently routable exit ports, `condition_met` and `max_exceeded`, through the same edge model used everywhere else.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "R3-4",
          "excerpt": "Loop mode has dual exit ports.",
          "reasoning": "The loop termination contract remains part of the stable schema surface."
        }
      ]
    },
    {
      "id": "REQ-8",
      "category": "functional",
      "description": "Every node and phase MUST define `on_start` and `on_end` as typed hook ports governed by the same port-definition rules as other outputs, but hook behavior is serialized only through edges and port resolution. The schema MUST NOT introduce a separate serialized hooks section, callback list, or hook-specific edge type.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hooks stay edge-based; no separate serialized hook model.",
          "reasoning": "Hook serialization is a direct stale-artifact cleanup requirement from cycle 4."
        }
      ]
    },
    {
      "id": "REQ-9",
      "category": "functional",
      "description": "`TemplateDefinition` MUST remain a reusable phase abstraction that expands into the same nested phase model. Templates use the same `nodes`, `children`, `edges`, and port contracts as inline phases.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-9",
          "excerpt": "Templates are reusable workflow building blocks, not a parallel schema dialect.",
          "reasoning": "Template reuse must preserve the canonical phase contract."
        }
      ]
    },
    {
      "id": "REQ-10",
      "category": "functional",
      "description": "`PluginInterface` MUST define plugin identity, description, typed inputs/outputs, and configuration schema so plugin nodes remain first-class schema participants without changing the core edge or phase model or requiring a separate root `plugin_instances` registry.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-12",
          "excerpt": "Plugin interfaces are part of the declarative contract.",
          "reasoning": "Plugins remain on the schema surface aligned to the same typed-port model; no root registries."
        }
      ]
    },
    {
      "id": "REQ-11",
      "category": "functional",
      "description": "Ask-node prompt assembly MUST remain a layered model: workflow/phase context injection, actor role prompt, the node's `prompt` field, and edge-delivered input data. The nested YAML rewrite must not flatten or hide those prompt inputs.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Align AskNode field names to Design (prompt instead of task/context_text).",
          "reasoning": "The `prompt` field is the canonical AskNode prompt surface; `task` and `context_text` are not valid field names."
        }
      ]
    },
    {
      "id": "REQ-12",
      "category": "functional",
      "description": "The schema MUST preserve hybrid data flow: local movement via edges, phase and node context selection via `context_keys`, and existing artifact-oriented persistence semantics where explicitly modeled. Nested phase containment does not justify adding new root registries such as `stores` to carry runtime state.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-3",
          "excerpt": "Hybrid edge flow plus persistent artifacts/context remains part of the data model.",
          "reasoning": "The rewrite changes serialization structure, not the underlying data-flow capabilities."
        }
      ]
    },
    {
      "id": "REQ-13",
      "category": "functional",
      "description": "Cost configuration MUST remain attachable at workflow, phase, and node levels so the declarative contract can carry pricing metadata, caps, and alert thresholds without coupling the schema to any one UI.",
      "priority": "should",
      "citations": [
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Cost tracking belongs in the workflow representation.",
          "reasoning": "The schema must still carry cost metadata even though the rewrite focuses on containment and interfaces."
        }
      ]
    },
    {
      "id": "REQ-14",
      "category": "functional",
      "description": "The schema MUST version both the wire format (`schema_version`) and the content (`workflow_version`) so consumers can distinguish schema evolution from ordinary workflow edits.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-14",
          "excerpt": "Schema versioning and workflow versioning are distinct.",
          "reasoning": "The canonical contract still needs explicit version fields after the interface rewrite."
        }
      ]
    },
    {
      "id": "REQ-15",
      "category": "functional",
      "description": "Port typing MUST remain explicit through `type_ref` or `schema_def` with strict mutual exclusion. This applies to phase ports, node ports, hook ports, and `BranchNode.outputs` (each `BranchOutputPort` must carry exactly one of `type_ref` or `schema_def`), and type-chain validation must work across nested phases and cross-phase edges.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Port definitions use `type_ref` XOR `schema_def`.",
          "reasoning": "The type system contract extends to BranchOutputPort entries; BranchNode.paths is replaced by BranchNode.outputs."
        }
      ]
    },
    {
      "id": "REQ-16",
      "category": "functional",
      "description": "The schema package MUST generate JSON Schema via `model_json_schema()`, and `/api/schema/workflow` MUST be the canonical composer-facing delivery path for that schema. Static `workflow-schema.json` remains a build/test artifact only and MUST NOT be treated as the editor's runtime source of truth.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "`/api/schema/workflow` is canonical; static `workflow-schema.json` is build/test only.",
          "reasoning": "This is the cycle-4 contract that resolves the remaining SF-1/SF-5/SF-6 split."
        }
      ]
    },
    {
      "id": "REQ-17",
      "category": "functional",
      "description": "Validation MUST reject stale contract variants and structural violations, including flat top-level node assumptions, malformed `source`/`target` refs, hook edges with transforms, serialized `port_type`, separate hook sections, invalid nested containment, unresolved refs, type mismatches, invalid mode configs, unknown branch output ports, stale actor discriminators or values, unapproved root additions such as `stores` or `plugin_instances`, and rejected branch fields such as `switch_function`. The stale exclusive-routing BranchNode shape (`condition_type`, `condition`, top-level `paths`) is also rejected — authors must use the per-port `outputs` model.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "output_field is fully removed; switch_function remains rejected; stale condition_type/condition/paths shape is superseded by per-port outputs.",
          "reasoning": "Validation is where the D-GR-35 contract actively prevents both old branch shapes from surviving."
        }
      ]
    },
    {
      "id": "REQ-18",
      "category": "functional",
      "description": "The declarative schema module MUST remain additive to the existing imperative `iriai-compose` subclass API. Introducing nested YAML, edge-only hook serialization, and canonical schema delivery MUST NOT break the current runtime ABCs or imperative workflows.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "scope constraint — Backward compatibility with iriai-compose's existing Python subclass API",
          "excerpt": "The declarative format is additive, not a replacement.",
          "reasoning": "Backward compatibility is an explicit scope constraint and remains mandatory."
        }
      ]
    },
    {
      "id": "REQ-19",
      "category": "functional",
      "description": "The litmus test remains unchanged: the planning, develop, and bugfix workflows from `iriai-build-v2` MUST be fully translatable, representable, and runnable through the nested phase contract, with three atomic node types, phase modes, hook edges, templates, and plugins.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable",
          "excerpt": "Planning, develop, and bugfix workflows remain the completeness test.",
          "reasoning": "The rewrite cannot reduce expressiveness relative to the agreed project scope."
        }
      ]
    },
    {
      "id": "REQ-20",
      "category": "functional",
      "description": "Schema structure is no longer architect-deferred. Phases MUST be saveable, importable, inline-creatable, detachable, and reusable while preserving the canonical nested YAML contract of `phases[].nodes` and `phases[].children`.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML phase containment is authoritative.",
          "reasoning": "Cycle 4 explicitly removed the remaining structure ambiguity."
        }
      ]
    },
    {
      "id": "REQ-21",
      "category": "security",
      "description": "Expression evaluation security is a formal contract: AST allowlist, blocked builtins, bounded size/complexity, timeout, and defined scope contexts. All `BranchNode` per-port conditions are expression strings subject to this sandbox contract. The sandbox also applies to `transform_fn` on data edges and expression-backed phase mode configs.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Per-port conditions are expressions only — no output_field mode per port.",
          "reasoning": "All BranchNode conditions are now expressions; the sandbox applies uniformly. output_field is removed so there is no non-evaluated branch path remaining."
        }
      ]
    },
    {
      "id": "REQ-22",
      "category": "functional",
      "description": "Universal port-definition rules MUST apply everywhere in the nested model: all input/output/hook port maps and `BranchNode.outputs` (each `BranchOutputPort`) use the same typed-port contract (`type_ref` XOR `schema_def`), support YAML shorthand, and preserve type information through save/load/export/import without relying on serialized `port_type`.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Typed port definitions are universal across the schema.",
          "reasoning": "The nested rewrite should consolidate, not fragment, port-definition rules. BranchNode.paths is replaced by BranchNode.outputs with BranchOutputPort entries following the same contract."
        }
      ]
    },
    {
      "id": "REQ-23",
      "category": "functional",
      "description": "This PRD-defined wire shape is the canonical contract for downstream SF-1 design, plan, runner, backend, editor, and migration artifacts. No consumer may add alternate actor discriminators, extra root registries, alternate branch routing fields, or runtime `workflow-schema.json` consumption without a later approved decision.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-30",
          "excerpt": "SF-1 PRD is canonical; plan and system-design must match exactly.",
          "reasoning": "Cycle-5 feedback established this PRD as the enforcement boundary for downstream artifact drift."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "Developer authors a workflow YAML with top-level `phases`, nested `phases[].nodes`, nested `phases[].children`, and top-level cross-phase `edges`, then validates and round-trips it.",
      "expected_observation": "`WorkflowConfig.model_validate()` accepts the document, `model_json_schema()` reflects the nested structure, and save/load preserves phase containment without flattening nodes to the workflow root or adding extra root registries.",
      "not_criteria": "Validation accepts top-level nodes, drops nested child phases, rewrites the document into a flat graph, or tolerates stray root `stores` / `plugin_instances`.",
      "requirement_ids": [
        "REQ-1",
        "REQ-5",
        "REQ-16",
        "REQ-20",
        "REQ-23"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML is authoritative.",
          "reasoning": "This acceptance criterion verifies the main containment rewrite."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "Developer defines an `AskNode` with an actor reference, a `prompt` field, and typed outputs inside `phases[].nodes`.",
      "expected_observation": "Validation succeeds, the actor reference resolves to the existing runtime actor model, the `prompt` field is accepted as the node's primary prompt surface, and the node remains an atomic prompt/response primitive inside the nested phase container.",
      "not_criteria": "The node requires `task` or `context_text` instead of `prompt`, a sub-DAG body, runtime credentials, or an alternate non-atomic execution model.",
      "requirement_ids": [
        "REQ-2",
        "REQ-3",
        "REQ-11"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Align AskNode field names to Design (prompt instead of task/context_text).",
          "reasoning": "The `prompt` field is the canonical AskNode prompt surface."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "Developer defines both a phase-local edge and a cross-phase edge using `source`/`target` dot notation plus `$input` and `$output` boundary refs.",
      "expected_observation": "Validation accepts `source`/`target` references, stores phase-local edges with the owning phase, stores cross-phase edges at workflow level, and does not require any serialized `port_type`.",
      "not_criteria": "Edges require `from_node`/`from_port`, serialize a `port_type`, or ignore `$input`/`$output` boundaries.",
      "requirement_ids": [
        "REQ-1",
        "REQ-4",
        "REQ-5"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "No serialized port_type; nested phase containment is authoritative.",
          "reasoning": "Phase-local and cross-phase edges must follow the canonical dot-notation contract."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "Developer defines a fold-mode phase with `nodes`, a nested child phase under `children`, and the required fold configuration.",
      "expected_observation": "Validation succeeds and the fold phase preserves both its contained nodes and its nested child phase without introducing a standalone Fold node type.",
      "not_criteria": "The phase must be represented as a separate Fold node, or nested content is rejected because it is not flat.",
      "requirement_ids": [
        "REQ-5",
        "REQ-6",
        "REQ-20"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R3-2",
          "excerpt": "Execution modes belong on phases.",
          "reasoning": "This criterion checks that fold remains a phase concern inside the nested model."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "Developer defines a loop-mode phase with `max_iterations: 5` and wires `condition_met` and `max_exceeded` to different targets.",
      "expected_observation": "Both exit ports validate as routable outputs on the phase and remain addressable through the normal edge contract.",
      "not_criteria": "Only one exit port is available, or `max_exceeded` is merged into the normal exit path.",
      "requirement_ids": [
        "REQ-7"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R3-4",
          "excerpt": "Loop mode exposes two exit ports.",
          "reasoning": "This criterion verifies the explicit loop termination contract."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "Developer defines a loop-mode phase with a condition and no `max_iterations`.",
      "expected_observation": "Validation succeeds, `condition_met` is active, and `max_exceeded` remains part of the phase contract without requiring a safety cap in every loop.",
      "not_criteria": "Loop mode is rejected for omitting `max_iterations`, or the dual-exit contract disappears when the cap is omitted.",
      "requirement_ids": [
        "REQ-7"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R3-4",
          "excerpt": "Dual exits are part of the stable loop model.",
          "reasoning": "The schema should preserve loop semantics with or without an explicit safety cap."
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "Developer defines a map-mode phase with collection and `max_parallelism` settings inside the nested phase model.",
      "expected_observation": "Validation succeeds and the phase expresses parallel fan-out without introducing Map as a standalone node type.",
      "not_criteria": "Parallel fan-out requires a separate Map node, or nested structure is disallowed for map-mode phases.",
      "requirement_ids": [
        "REQ-5",
        "REQ-6"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R3-2",
          "excerpt": "Map semantics live on phases.",
          "reasoning": "This verifies that phase-mode modeling survives the rewrite."
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "Developer wires a phase `on_start` hook to a plugin node using an ordinary edge like `source: \"phase_a.on_start\"`.",
      "expected_observation": "Validation accepts the edge, infers that it is a hook edge from the source port container, and forbids a transform without requiring any serialized `port_type`.",
      "not_criteria": "The workflow needs a separate hook section, a hook callback list, or `port_type: \"hook\"` in YAML.",
      "requirement_ids": [
        "REQ-4",
        "REQ-8",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hook wiring stays edge-based with no serialized `port_type`.",
          "reasoning": "This is one of the direct stale-contract fixes from cycle 4."
        }
      ]
    },
    {
      "id": "AC-9",
      "user_action": "Developer wires a node `on_end` hook to a plugin node using an ordinary edge.",
      "expected_observation": "Validation accepts the edge, infers hook behavior from the source port, and preserves the same edge representation used for data flow.",
      "not_criteria": "Node hook edges require a dedicated hook edge class, a separate hook section, or a serialized hook discriminator.",
      "requirement_ids": [
        "REQ-4",
        "REQ-8",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hooks are ordinary edges plus port resolution.",
          "reasoning": "Both phase and node hooks must use the same serialized model."
        }
      ]
    },
    {
      "id": "AC-10",
      "user_action": "Developer creates an edge whose source and target port types do not match across nested phases.",
      "expected_observation": "Validation fails with a clear error naming the edge and the incompatible types.",
      "not_criteria": "The mismatch is silently accepted, or the error loses the edge context because the graph is nested.",
      "requirement_ids": [
        "REQ-15",
        "REQ-17",
        "REQ-22"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Typed ports remain the basis for edge compatibility checks.",
          "reasoning": "Type mismatch reporting must continue to work after the nested rewrite."
        }
      ]
    },
    {
      "id": "AC-11",
      "user_action": "Migration engineer encodes the `iriai-build-v2` planning PM patterns using nested phases, phase-local nodes, per-port branch conditions, and hook edges.",
      "expected_observation": "The workflow validates without requiring extra node kinds or imperative escape hatches, and the major planning patterns remain representable.",
      "not_criteria": "Translation requires top-level nodes, compound Map/Fold/Loop nodes, or `switch_function` to model existing workflows.",
      "requirement_ids": [
        "REQ-3",
        "REQ-5",
        "REQ-6",
        "REQ-8",
        "REQ-19"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable",
          "excerpt": "Existing workflows remain the completeness test.",
          "reasoning": "This acceptance criterion verifies the scope-level litmus test."
        }
      ]
    },
    {
      "id": "AC-12",
      "user_action": "Developer nests a map child phase inside a fold parent using `children`.",
      "expected_observation": "Validation succeeds and preserves the child phase under the parent's `children` array.",
      "not_criteria": "Nested phases are rejected, flattened to siblings, or serialized under a stale alternate field.",
      "requirement_ids": [
        "REQ-5",
        "REQ-6",
        "REQ-20"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested child phases serialize under the phase container.",
          "reasoning": "This directly checks the canonical nested containment shape."
        }
      ]
    },
    {
      "id": "AC-13",
      "user_action": "Developer defines both an `agent` actor and a `human` actor using `actor_type` in the same workflow.",
      "expected_observation": "Both validate against the declarative actor union without requiring environment-specific runtime secrets.",
      "not_criteria": "The schema accepts `type: interaction` as a wire alias, rejects one actor class, or leaks runtime credential details into workflow YAML.",
      "requirement_ids": [
        "REQ-2",
        "REQ-23"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-30",
          "excerpt": "actor_type: agent|human only — no interaction alias.",
          "reasoning": "The criterion explicitly verifies that interaction is not accepted as a wire value."
        }
      ]
    },
    {
      "id": "AC-14",
      "user_action": "Developer creates an inter-phase cycle outside loop mode in the nested graph.",
      "expected_observation": "Validation fails and reports the cycle path clearly.",
      "not_criteria": "The cycle passes because containment is nested, or the error only surfaces at runtime.",
      "requirement_ids": [
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested containment is canonical, not an excuse to weaken structural validation.",
          "reasoning": "The validator still needs to enforce graph correctness under the new layout."
        }
      ]
    },
    {
      "id": "AC-15",
      "user_action": "Developer attaches `transform_fn` to a hook edge.",
      "expected_observation": "Validation fails because hook edges are fire-and-forget lifecycle triggers inferred from the source hook port.",
      "not_criteria": "Hook edges accept transforms because `port_type` is absent, or the system cannot tell a hook edge from a data edge.",
      "requirement_ids": [
        "REQ-4",
        "REQ-8",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hooks are inferred from port resolution, not an explicit edge type.",
          "reasoning": "The contract still forbids transforms on hook edges even without serialized `port_type`."
        }
      ]
    },
    {
      "id": "AC-16",
      "user_action": "Composer backend serves `/api/schema/workflow`, and the frontend fetches it at runtime before rendering the editor.",
      "expected_observation": "The returned JSON Schema reflects the live nested phase contract, and the frontend does not need a bundled static schema as its runtime source of truth.",
      "not_criteria": "The editor depends on a checked-in `workflow-schema.json` at runtime or drifts from the backend schema until a rebuild.",
      "requirement_ids": [
        "REQ-16"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "`GET /api/schema/workflow` is already described as the canonical schema endpoint.",
          "reasoning": "The revised PRD aligns SF-1 delivery to the existing backend contract."
        }
      ]
    },
    {
      "id": "AC-17",
      "user_action": "Developer writes an expression longer than 10,000 characters into an expression-backed schema field.",
      "expected_observation": "Validation fails with a specific size-limit error.",
      "not_criteria": "Oversized expressions pass or only fail later at runtime.",
      "requirement_ids": [
        "REQ-21"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R6-1",
          "excerpt": "Expression fields are bounded by an explicit sandbox contract.",
          "reasoning": "The schema must continue to enforce the expression safety ceiling."
        }
      ]
    },
    {
      "id": "AC-18",
      "user_action": "SF-2 implementer reads the schema PRD to build the shared expression evaluator.",
      "expected_observation": "The PRD specifies that all `BranchNode` per-port conditions are expression strings subject to the full sandbox contract (AST allowlist, blocked builtins, size/complexity bounds, timeout). The contract is clear that `switch_function` is rejected and `merge_function` is not an expression field.",
      "not_criteria": "The contract says expressions are restricted without defining the restrictions, or conflates the gather `merge_function` with a Python-evaluated routing function.",
      "requirement_ids": [
        "REQ-21"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Per-port conditions are expressions only.",
          "reasoning": "All conditions are now expressions; the sandbox applies uniformly. The implementer needs to know merge_function is a gather hook, not a sandboxed expression."
        }
      ]
    },
    {
      "id": "AC-19",
      "user_action": "Developer defines a phase, node, or hook port using only `schema_def`.",
      "expected_observation": "Validation succeeds and uses the inline JSON Schema for type-flow checks, including hook-port payload typing.",
      "not_criteria": "Validation rejects inline schema-only hook ports or silently strips the schema.",
      "requirement_ids": [
        "REQ-8",
        "REQ-15",
        "REQ-22"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Ports may be typed by `schema_def` instead of `type_ref`.",
          "reasoning": "Inline schemas remain part of the typed-port contract; hook ports are included."
        }
      ]
    },
    {
      "id": "AC-20",
      "user_action": "Developer defines a port or branch output port with both `type_ref` and `schema_def`.",
      "expected_observation": "Validation fails because exactly one typing mechanism must be present.",
      "not_criteria": "Both fields are accepted simultaneously.",
      "requirement_ids": [
        "REQ-15",
        "REQ-22"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Port typing uses strict mutual exclusion.",
          "reasoning": "This is a core typed-port invariant; BranchOutputPort entries follow the same XOR rule."
        }
      ]
    },
    {
      "id": "AC-21",
      "user_action": "Developer defines a port or branch output port with neither `type_ref` nor `schema_def`.",
      "expected_observation": "Validation fails because the port has no declared type information.",
      "not_criteria": "Untyped ports pass silently in places where the contract requires explicit typing.",
      "requirement_ids": [
        "REQ-15",
        "REQ-22"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "Ports must resolve to exactly one typed definition.",
          "reasoning": "The same XOR rule also rejects missing type declarations in BranchOutputPort entries."
        }
      ]
    },
    {
      "id": "AC-22",
      "user_action": "Developer uses YAML shorthand with a bare string type name in a port definition.",
      "expected_observation": "The shorthand is accepted and normalized as a typed-port definition without requiring verbose JSON syntax.",
      "not_criteria": "Bare-string shorthand is rejected or creates ambiguous state that cannot round-trip cleanly.",
      "requirement_ids": [
        "REQ-22"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "R7-1",
          "excerpt": "The schema supports concise typed-port authoring.",
          "reasoning": "YAML ergonomics remain important even after the containment rewrite."
        }
      ]
    },
    {
      "id": "AC-23",
      "user_action": "Developer defines a `BranchNode` with multiple output ports each carrying a `condition` expression, then connects edges from `branch_id.approved`, `branch_id.needs_revision`, and `branch_id.rejected`.",
      "expected_observation": "Validation succeeds, each output port key becomes an addressable port name, all per-port conditions are subject to the expression sandbox, and multiple ports may fire concurrently if their conditions are satisfied (non-exclusive fan-out).",
      "not_criteria": "Branch routing collapses to a single-path exclusive model, requires a top-level `condition_type` / `condition` / `paths` shape, or treats only one condition as active.",
      "requirement_ids": [
        "REQ-3",
        "REQ-4",
        "REQ-21"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "D-GR-12 per-port model is the single authority. Non-exclusive fan-out; each output port carries its own condition.",
          "reasoning": "This AC is the primary verification of the D-GR-35 branch model change."
        }
      ]
    },
    {
      "id": "AC-24",
      "user_action": "Developer defines a `BranchNode` with a `merge_function` string and two output ports, where the gather node receives inputs from parallel upstream branches before evaluating port conditions.",
      "expected_observation": "Validation succeeds, `merge_function` is accepted as an optional gather hook string on `BranchNode`, and the resulting merged payload is then available to per-port condition evaluation.",
      "not_criteria": "`merge_function` is rejected as an invalid field, or it is treated as a routing function equivalent to the removed `switch_function`.",
      "requirement_ids": [
        "REQ-3",
        "REQ-21"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "`merge_function` is valid for gather semantics when multiple inputs converge.",
          "reasoning": "merge_function was previously rejected; D-GR-35 makes it valid for gather. This AC verifies that reversal."
        }
      ]
    },
    {
      "id": "AC-25",
      "user_action": "Developer adds `switch_function` to a branch definition.",
      "expected_observation": "Validation fails with an error directing the author to use the per-port `outputs` model with individual `condition` expressions on each port.",
      "not_criteria": "`switch_function` is accepted as an alias or tolerated for backward compatibility.",
      "requirement_ids": [
        "REQ-3",
        "REQ-17",
        "REQ-23"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "`switch_function` remains rejected — not a valid field.",
          "reasoning": "switch_function is explicitly and permanently invalid in the D-GR-35 model."
        }
      ]
    },
    {
      "id": "AC-26",
      "user_action": "Developer defines an edge from `branch_id.unknown_port` where `unknown_port` is not present in the branch node's `outputs`.",
      "expected_observation": "Validation fails and reports the invalid port plus the set of valid branch output port names.",
      "not_criteria": "Unknown branch ports pass because the graph is nested, or the error omits the available port names.",
      "requirement_ids": [
        "REQ-3",
        "REQ-4",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Per-port model: output port keys in `outputs` are the authoritative port names.",
          "reasoning": "The edge model must enforce branch output port names as real ports; `paths` terminology replaced by `outputs`."
        }
      ]
    },
    {
      "id": "AC-27",
      "user_action": "Developer adds `stores` or `plugin_instances` to the workflow root and validates the file.",
      "expected_observation": "Validation fails with an unsupported-root-field error naming the rejected key and preserves the rest of the canonical root shape.",
      "not_criteria": "Extra root registries are silently ignored, stripped, or treated as part of the approved wire contract.",
      "requirement_ids": [
        "REQ-1",
        "REQ-17",
        "REQ-23"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-30",
          "excerpt": "The workflow root field set is closed; unapproved registries are invalid.",
          "reasoning": "This criterion directly enforces the no-stores/no-plugin_instances contract at the root level."
        }
      ]
    },
    {
      "id": "AC-28",
      "user_action": "Developer defines a `BranchNode` using the stale `condition_type` / `condition` / `paths` shape from before D-GR-35.",
      "expected_observation": "Validation fails, naming `condition_type`, `condition`, and `paths` as unsupported top-level branch fields and directing the author to the per-port `outputs` model.",
      "not_criteria": "The stale exclusive-routing shape is silently accepted or partially interpreted.",
      "requirement_ids": [
        "REQ-3",
        "REQ-17",
        "REQ-23"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "BranchNode semantics replaced by D-GR-12 per-port model. stale condition_type/condition/paths shape is superseded.",
          "reasoning": "The old model must fail fast with clear guidance to the new per-port model."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Define a nested declarative workflow from scratch",
      "actor": "Platform developer authoring workflow YAML",
      "preconditions": "`iriai-compose` declarative models are available and the developer has a text editor plus access to the schema docs.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "The developer creates a new YAML file and writes a top-level `WorkflowConfig` with version fields, actors, top-level `phases`, and workflow-level cross-phase `edges`.",
          "observes": "The workflow shape is phase-first rather than node-first, uses the closed root field set, and does not require top-level nodes.",
          "not_criteria": "The developer has to flatten nodes to the workflow root, add unapproved root registries, or invent a second top-level graph structure.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML is authoritative.",
              "reasoning": "The first step in authoring must reflect the canonical top-level contract."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The developer defines actor entries and named types used by the workflow.",
          "observes": "Actor definitions align to `actor_type: agent|human` while still mapping cleanly to existing runtime actor concepts in `iriai-compose`.",
          "not_criteria": "Actor entries require environment secrets, revive `interaction`, or introduce a third unsupported actor family.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-30",
              "excerpt": "actor_type: agent|human only — no interaction alias.",
              "reasoning": "The declarative actor surface should map to existing concepts using only agent|human."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "The developer defines a top-level phase and places atomic nodes under `phases[0].nodes`.",
          "observes": "The phase owns its internal execution elements instead of referencing top-level node IDs.",
          "not_criteria": "Nodes must live outside the phase or be referenced only by a detached ID list.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested phase containment uses `phases[].nodes`.",
              "reasoning": "The nested containment model is the intended architecture."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "The developer nests a sub-phase under `phases[0].children` to express a contained execution group.",
          "observes": "The child phase is serialized inline under the parent phase rather than flattened to a sibling list.",
          "not_criteria": "Nested phases are forced into a stale alternate field or moved back to workflow level.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested phase containment uses `phases[].children`.",
              "reasoning": "This is the specific field-level contract the rewrite is standardizing."
            }
          ]
        },
        {
          "step_number": 5,
          "action": "The developer adds `AskNode`, `BranchNode`, and `PluginNode` definitions inside phase `nodes`. For `AskNode` they provide a `prompt` field; for `BranchNode` they provide an `outputs` map where each port carries its own `condition` expression.",
          "observes": "Only three atomic node kinds are needed, branch routing uses per-port conditions with non-exclusive fan-out, and `switch_function` / `output_field` do not appear.",
          "not_criteria": "Map/Fold/Loop must be authored as extra node types, `switch_function` reappears, or branch routing reverts to the stale exclusive `condition_type`/`condition`/`paths` shape.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "D-GR-12 per-port model is the single authority. Non-exclusive fan-out.",
              "reasoning": "Branch behavior and AskNode prompt field are both aligned to D-GR-35."
            }
          ]
        },
        {
          "step_number": 6,
          "action": "The developer wires phase-local and cross-phase connections with `source` and `target` dot notation plus `$input` and `$output` when crossing phase boundaries.",
          "observes": "Phase-local edges stay with the owning phase, and cross-phase edges stay at workflow level.",
          "not_criteria": "Edges require `from_node`/`to_node` fields or lose boundary information when phases are nested.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Edge model uses source/target dot notation; no port_type.",
              "reasoning": "The PRD describes the contract that downstream serialization already assumes."
            }
          ]
        },
        {
          "step_number": 7,
          "action": "The developer wires lifecycle behavior from `on_start` and `on_end` ports using ordinary edges.",
          "observes": "Hook behavior is represented entirely through edges and port resolution with no extra hook section or serialized edge discriminator.",
          "not_criteria": "Hooks must be declared in a separate callback list or serialized with `port_type: hook`.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Hooks stay edge-based; no serialized `port_type`.",
              "reasoning": "Hook serialization is one of the key cycle-4 fixes."
            }
          ]
        },
        {
          "step_number": 8,
          "action": "The developer loads the YAML with `yaml.safe_load()` and `WorkflowConfig.model_validate()`.",
          "observes": "Validation checks nested containment, typed ports, per-port branch conditions, hook-edge rules, and stale-field rejection before any runner logic executes.",
          "not_criteria": "Broken nested structure or stale fields are only discovered later in the editor or runner.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Stale contract variants must be removed consistently.",
              "reasoning": "Validation is where the canonical contract becomes enforceable."
            }
          ]
        }
      ],
      "outcome": "A complete nested workflow YAML validates successfully and is ready for loader execution, editor round-tripping, or migration comparison.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-3",
        "REQ-4",
        "REQ-5",
        "REQ-16",
        "REQ-17",
        "REQ-20"
      ]
    },
    {
      "id": "J-2",
      "name": "Translate `iriai-build-v2` planning and implementation patterns into the nested schema",
      "actor": "Migration engineer converting existing imperative workflows",
      "preconditions": "The engineer has the current `iriai-build-v2` planning/develop sources and the declarative schema contract.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "The engineer models the planning interview/gating logic as loop-mode and sequential phases whose internal nodes live under `nodes` and whose contained execution groups live under `children`.",
          "observes": "Imperative phase sequencing maps cleanly into nested declarative phases without introducing extra compound node kinds.",
          "not_criteria": "The translation requires flattening every phase to workflow level or introducing special-purpose loop nodes.",
          "citations": [
            {
              "type": "code",
              "reference": "iriai-build-v2/src/iriai_build_v2/workflows/planning/workflow.py:24-56",
              "excerpt": "The planning workflow is already organized as an ordered phase sequence.",
              "reasoning": "The completeness test begins from existing phase-based workflow structure."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The engineer models per-subfeature iteration and retry logic with fold/map/loop phase modes and branch nodes inside those phases.",
          "observes": "Nested iteration remains expressible through phase modes plus atomic nodes, even when the imperative source loops over subfeatures or retries fixes.",
          "not_criteria": "The translation requires standalone Map/Fold/Loop nodes or an imperative escape hatch.",
          "citations": [
            {
              "type": "code",
              "reference": "iriai-build-v2/src/iriai_build_v2/workflows/_common/_helpers.py:399-547",
              "excerpt": "`per_subfeature_loop` shows real sequential looping and gating patterns the schema must represent.",
              "reasoning": "This helper is one of the concrete imperative patterns the declarative schema must encode."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "The engineer models review/approve branches using a `BranchNode` with per-port conditions: each outcome port carries its own expression, and multiple outcomes can fire if their conditions are simultaneously satisfied.",
          "observes": "Each output port becomes an explicit port name used by normal edges, `switch_function` is not needed, and the non-exclusive fan-out allows parallel routing where the imperative code had multiple simultaneous true branches.",
          "not_criteria": "Routing bypasses edges, reverts to the stale exclusive `condition_type`/`condition`/`paths` shape, or requires `switch_function`.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "D-GR-12 per-port model is the single authority.",
              "reasoning": "Translation must use the settled per-port branch model, not resurrect an older exclusive one."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "The engineer models setup/publication side effects as hook edges from phase or node lifecycle ports.",
          "observes": "Lifecycle behavior is visible in the graph as ordinary edges, not hidden in callback lists.",
          "not_criteria": "Artifact publishing or setup logic has to be encoded in a separate hook registry or callback block.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Hook serialization is edge-based only.",
              "reasoning": "Migration output must share the same hook model as the schema and editor."
            }
          ]
        },
        {
          "step_number": 5,
          "action": "The engineer validates the translated workflow against the schema.",
          "observes": "The translated workflow stays representable within the three-node, phase-mode, nested-containment model.",
          "not_criteria": "Any required planning/develop pattern falls outside the declarative contract.",
          "citations": [
            {
              "type": "decision",
              "reference": "scope constraint — iriai-build-v2 workflows must be fully translatable, representable, and runnable",
              "excerpt": "Existing workflows remain the litmus test.",
              "reasoning": "Successful translation is the explicit project completeness bar."
            }
          ]
        }
      ],
      "outcome": "The translated workflow preserves the meaningful `iriai-build-v2` execution structure while conforming to the new nested YAML contract.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-3",
        "REQ-5",
        "REQ-6",
        "REQ-8",
        "REQ-19",
        "REQ-20"
      ]
    },
    {
      "id": "J-3",
      "name": "Validation rejects stale or structurally invalid schema variants",
      "actor": "Developer or migration engineer loading malformed YAML",
      "preconditions": "A workflow YAML file contains stale fields or structural mistakes.",
      "path_type": "failure",
      "failure_trigger": "The document uses flat node placement, stale actor or branch fields, unauthorized root additions, serialized `port_type`, separate hook sections, invalid nested containment, or other rejected contract variants.",
      "steps": [
        {
          "step_number": 1,
          "action": "The developer loads YAML that places nodes at workflow root, adds `stores` / `plugin_instances`, or nests phases outside `children`.",
          "observes": "Validation fails with a structural error explaining that nodes belong under phases, child phases belong under `children`, and the workflow root is closed to unapproved registries.",
          "not_criteria": "The loader quietly normalizes the file into an unspecified structure or silently strips the extra root keys.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested phase containment is authoritative.",
              "reasoning": "The validator must actively reject stale flat-shape documents and unauthorized root fields."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The developer loads YAML that serializes hook behavior with a separate hook section or `port_type`.",
          "observes": "Validation fails with a clear unsupported-field error that directs the author back to edge-based hook serialization.",
          "not_criteria": "The stale hook model is tolerated or silently ignored.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Stale separate hook-section and serialized port_type assumptions must be removed.",
              "reasoning": "The rewrite must close that stale-contract hole through validation."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "The developer loads YAML where a hook edge also defines `transform_fn`.",
          "observes": "Validation fails because hook edges are inferred from source hook ports and cannot carry transforms.",
          "not_criteria": "The document passes because no explicit hook edge type exists.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Hooks are ordinary edges, not a separate serialized edge type.",
              "reasoning": "The validator still has to enforce hook-edge constraints."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "The developer loads YAML that uses `type: interaction` on an actor, includes `switch_function` on a branch node, or uses the stale `condition_type`/`condition`/`paths` branch shape.",
          "observes": "Validation fails with guidance to use `actor_type: agent|human` and the per-port `outputs` branch model with individual `condition` expressions per port.",
          "not_criteria": "Stale actor aliases or stale branch shapes are treated as backward-compatible wire variants.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "switch_function remains rejected; output_field is fully removed; stale condition_type/condition/paths shape superseded by per-port outputs.",
              "reasoning": "All stale branch fields must fail fast; the stale exclusive model must not survive."
            }
          ]
        },
        {
          "step_number": 5,
          "action": "The developer fixes the stale fields and reruns validation.",
          "observes": "The corrected document validates against the canonical nested phase, edge, actor, and per-port branch model.",
          "not_criteria": "The developer has to guess which of multiple competing schema variants the system now expects.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Cycle 4 defines one canonical schema/interface contract.",
              "reasoning": "The failure path should converge authors toward that single contract."
            }
          ]
        }
      ],
      "outcome": "Invalid legacy or structurally inconsistent workflow YAML is blocked early with clear guidance toward the canonical schema.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-4",
        "REQ-17",
        "REQ-20"
      ]
    },
    {
      "id": "J-4",
      "name": "Composer consumes the live schema contract from `/api/schema/workflow`",
      "actor": "Workflow editor frontend developer",
      "preconditions": "The composer backend is running and can import the `iriai-compose` declarative schema package.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "The backend exposes `GET /api/schema/workflow` using the current `WorkflowConfig.model_json_schema()` output.",
          "observes": "The endpoint returns the current JSON Schema for the nested workflow contract.",
          "not_criteria": "The canonical schema is only available as a checked-in static file or a manually copied artifact.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "`GET /api/schema/workflow` is the canonical schema delivery path.",
              "reasoning": "The PRD aligns SF-1 delivery to the existing backend contract."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The frontend fetches `/api/schema/workflow` before rendering editor inspectors and validation rules.",
          "observes": "The editor receives a schema that includes nested phase containment and the no-`port_type` edge model.",
          "not_criteria": "The editor authors against a stale bundled schema or assumes flat nodes because the live endpoint is ignored.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "`/api/schema/workflow` is canonical and static schema is build/test only.",
              "reasoning": "This step validates the core producer-consumer contract change."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "The frontend renders phase, edge, and branch UI from the fetched schema.",
          "observes": "The UI expects `phases[].nodes`, `phases[].children`, `source`/`target` edges, per-port branch conditions on `BranchNode.outputs`, and hook inference via ports rather than a serialized hook discriminator.",
          "not_criteria": "The UI renders stale `port_type` controls, a stale exclusive branch UI, or a runtime dependency on `workflow-schema.json`.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "Per-port model is authoritative across all subfeatures including the editor.",
              "reasoning": "The rewritten SF-1 PRD must meet the editor contract already described downstream."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "The frontend saves and reloads a workflow using the fetched schema contract.",
          "observes": "Round-trip preserves nested phase containment and edge-only hook serialization without needing runtime schema patches.",
          "not_criteria": "Round-trip only works against an internal editor-only shape that diverges from the live backend schema.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "The serializer already drops `port_type` and groups nodes/edges by phase.",
              "reasoning": "The live schema contract should match the editor's intended serialization behavior."
            }
          ]
        }
      ],
      "outcome": "The composer renders and validates against the live, current schema contract instead of a stale static snapshot.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-4",
        "REQ-5",
        "REQ-16",
        "REQ-20"
      ]
    },
    {
      "id": "J-5",
      "name": "Author a loop with explicit success and safety-cap exits",
      "actor": "Workflow author modeling retry or interview logic",
      "preconditions": "The author is editing a loop-mode phase in the declarative schema.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "The author defines a loop-mode phase and wires `condition_met` and `max_exceeded` to different targets.",
          "observes": "Both exits are available through normal edge references and can be routed independently.",
          "not_criteria": "Loop termination collapses to a single implicit output path.",
          "citations": [
            {
              "type": "decision",
              "reference": "R3-4",
              "excerpt": "Loop phases have dual exit ports.",
              "reasoning": "This step verifies the explicit loop termination contract."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The author validates and reloads the loop definition.",
          "observes": "The two exit ports remain part of the phase contract after round-trip serialization.",
          "not_criteria": "One of the loop exits disappears after save/load because nested serialization normalizes it away.",
          "citations": [
            {
              "type": "decision",
              "reference": "R3-4",
              "excerpt": "Dual exits are part of the stable loop model.",
              "reasoning": "The nested YAML rewrite must preserve loop-routing semantics."
            }
          ]
        }
      ],
      "outcome": "Loop-mode workflows can express both normal completion and safety-cap termination without leaving the canonical edge model.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-7"
      ]
    },
    {
      "id": "J-6",
      "name": "Composer detects schema-source drift or endpoint failure instead of silently using stale schema",
      "actor": "Workflow editor frontend developer",
      "preconditions": "The editor is loading its schema contract at runtime.",
      "path_type": "failure",
      "failure_trigger": "The schema endpoint is unavailable or a stale bundled schema would otherwise be used as a silent fallback.",
      "steps": [
        {
          "step_number": 1,
          "action": "The editor attempts to fetch `/api/schema/workflow` and the request fails or returns an unexpected response.",
          "observes": "The failure is surfaced as a schema-load problem and blocks or warns on editor initialization.",
          "not_criteria": "The editor silently falls back to a stale bundled schema and continues authoring against the wrong contract.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Static schema is build/test only, not the canonical runtime source.",
              "reasoning": "Runtime fallback to stale static schema would violate the cycle-4 decision."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The developer restores the endpoint or retries against a healthy backend.",
          "observes": "The editor resumes using the live schema contract from `/api/schema/workflow`.",
          "not_criteria": "The editor remains pinned to a stale local schema after the backend is fixed.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "`GET /api/schema/workflow` is the canonical delivery path.",
              "reasoning": "Recovery should return the editor to that intended canonical source."
            }
          ]
        }
      ],
      "outcome": "Schema-source failures are explicit and recoverable; they do not reintroduce static-schema-first drift.",
      "related_journey_id": "J-4",
      "requirement_ids": [
        "REQ-16",
        "REQ-17"
      ]
    },
    {
      "id": "J-7",
      "name": "Migration output using stale hook or branch fields fails fast and is corrected",
      "actor": "Migration engineer validating translated workflow YAML",
      "preconditions": "A translated workflow still contains old `switch_function`, `condition_type`/`condition`/`paths` branch shape, serialized `port_type`, or separate-hook assumptions.",
      "path_type": "failure",
      "failure_trigger": "Migration emits stale fields instead of the D-GR-22/D-GR-35 nested phase + per-port branch contract.",
      "steps": [
        {
          "step_number": 1,
          "action": "The engineer validates the translated YAML and sees errors for stale branch or hook fields.",
          "observes": "Validation points directly to `switch_function`, the stale `condition_type`/`condition`/`paths` shape, serialized `port_type`, or invalid hook structure as unsupported by the canonical schema.",
          "not_criteria": "The loader partially accepts stale translation output and leaves downstream tools to guess what the schema means.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "stale condition_type/condition/paths shape superseded; switch_function remains rejected.",
              "reasoning": "Migration output must be forced into the same canonical contract as hand-authored YAML."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "The engineer rewrites the translation to use `children`, ordinary edges for hooks, and a `BranchNode.outputs` map with per-port `condition` expressions, then validates again.",
          "observes": "The corrected translation passes validation and matches the same contract expected by the loader and editor.",
          "not_criteria": "Migration keeps a private alternate schema dialect that only one downstream tool understands.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "Per-port model is the single authority.",
              "reasoning": "Correcting translation output means converging on the settled per-port branch and hook contract."
            }
          ]
        }
      ],
      "outcome": "Migration output is either canonical or rejected; stale translation formats cannot persist as an unofficial second schema.",
      "related_journey_id": "J-2",
      "requirement_ids": [
        "REQ-3",
        "REQ-4",
        "REQ-17",
        "REQ-20"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None beyond standard internal engineering controls.",
    "data_sensitivity": "Internal. Workflow YAML and JSON Schema expose proprietary orchestration structure, prompts, and typed interfaces, but not operational secrets by default.",
    "pii_handling": "No direct PII handling in the schema module. Human actors are declared abstractly rather than by storing personal profile data.",
    "auth_requirements": "The schema package itself has no intrinsic auth boundary. When exposed to the composer, the canonical `/api/schema/workflow` delivery path should inherit the backend's authenticated API policy rather than rely on a public static file.",
    "data_retention": "Not applicable for the library artifact itself. Exported YAML or generated JSON Schema retention is determined by the consuming service or repository.",
    "third_party_exposure": "YAML or JSON Schema may be shared externally, but the contract should expose workflow structure and types only; it should not require embedded credentials or secrets. Static `workflow-schema.json` is build/test only, reducing accidental runtime drift from checked-in artifacts.",
    "data_residency": "Not applicable at the library level; residency is inherited from whichever backend serves or stores workflows.",
    "risk_mitigation_notes": "The main product risk is contract drift between schema producer and consumers. D-GR-22, D-GR-30, and D-GR-35 mitigate that by making nested YAML, actor_type:agent|human, closed root field set, edge-based hook serialization, per-port branch conditions, and `/api/schema/workflow` the single canonical interface. All BranchNode per-port conditions are expressions subject to the sandbox; `switch_function` is rejected. Validation must fail fast on stale `switch_function`, the old `condition_type`/`condition`/`paths` branch shape, serialized `port_type`, or separate hook-section assumptions so downstream tools cannot silently diverge."
  },
  "data_entities": [
    {
      "name": "WorkflowConfig",
      "fields": [
        "schema_version: str",
        "workflow_version: int",
        "name: str",
        "description: Optional[str]",
        "metadata: Optional[dict]",
        "actors: dict[str, ActorDefinition]",
        "phases: list[PhaseDefinition]",
        "edges: list[EdgeDefinition]",
        "templates: Optional[dict[str, TemplateDefinition]]",
        "plugins: Optional[dict[str, PluginInterface]]",
        "types: Optional[dict[str, JsonSchema]]",
        "cost_config: Optional[WorkflowCostConfig]"
      ],
      "constraints": [
        "YAML-first root model",
        "No top-level nodes",
        "Workflow-level edges are cross-phase only",
        "No root `stores` or `plugin_instances`",
        "All refs resolve",
        "Workflow graph is acyclic outside intentional loop semantics"
      ],
      "is_new": true
    },
    {
      "name": "ActorDefinition",
      "fields": [
        "actor_type: Literal['agent','human']",
        "agent fields: provider, model, role, persistent, context_keys",
        "human fields: identity, channel"
      ],
      "constraints": [
        "Discriminator field is `actor_type`",
        "Valid values are only `agent` and `human`",
        "No `interaction` alias",
        "No environment-specific credential fields"
      ],
      "is_new": true
    },
    {
      "name": "AskNode",
      "fields": [
        "id: str",
        "type: Literal['ask']",
        "actor_ref: str",
        "prompt: str",
        "inputs: dict[str, WorkflowInputDefinition]",
        "outputs: dict[str, WorkflowOutputDefinition]",
        "hooks: dict[str, WorkflowOutputDefinition]",
        "artifact_key: Optional[str]",
        "cost: Optional[NodeCostConfig]",
        "context_keys: Optional[list[str]]"
      ],
      "constraints": [
        "`prompt` is the canonical prompt field; `task` and `context_text` are not valid",
        "`actor_ref` resolves to an `ActorDefinition` in the workflow's `actors` map",
        "Serializes only inside `phases[].nodes`"
      ],
      "is_new": true
    },
    {
      "name": "BranchNode",
      "fields": [
        "id: str",
        "type: Literal['branch']",
        "merge_function: Optional[str]",
        "outputs: dict[str, BranchOutputPort]"
      ],
      "constraints": [
        "At least two output ports in `outputs`",
        "Non-exclusive fan-out: multiple ports may fire if their conditions are satisfied",
        "`merge_function` is an optional gather hook — not a routing function; invoked when multiple inputs converge before condition evaluation",
        "`switch_function` is not a valid field",
        "`output_field` is not a valid field",
        "Stale top-level `condition_type`, `condition`, and `paths` fields are rejected",
        "Serializes only inside `phases[].nodes`"
      ],
      "is_new": true
    },
    {
      "name": "BranchOutputPort",
      "fields": [
        "condition: str",
        "type_ref: Optional[str]",
        "schema_def: Optional[dict]",
        "description: Optional[str]"
      ],
      "constraints": [
        "`condition` is always an expression string subject to the security sandbox (AST allowlist, blocked builtins, size/complexity bounds, timeout)",
        "Exactly one of `type_ref` or `schema_def` (same XOR rule as all other typed ports)",
        "Port key in the parent `outputs` map becomes the addressable output port name on the BranchNode"
      ],
      "is_new": true
    },
    {
      "name": "PhaseDefinition",
      "fields": [
        "id: str",
        "name: str",
        "mode: Literal['sequential','map','fold','loop']",
        "mode_config: Optional[ModeConfig]",
        "inputs: dict[str, WorkflowInputDefinition]",
        "outputs: dict[str, WorkflowOutputDefinition]",
        "hooks: dict[str, WorkflowOutputDefinition]",
        "nodes: list[NodeDefinition]",
        "children: list[PhaseDefinition]",
        "edges: list[EdgeDefinition]",
        "context_keys: list[str]",
        "cost: Optional[PhaseCostConfig]",
        "metadata: Optional[dict]"
      ],
      "constraints": [
        "Primary execution container",
        "`nodes` serialize under `phases[].nodes`",
        "Nested phases serialize under `phases[].children`",
        "Phase-local edges stay with the phase",
        "Loop mode exposes `condition_met` and `max_exceeded`",
        "`mode_config` is a single discriminated-union field; separate flat mode config fields are not valid"
      ],
      "is_new": true
    },
    {
      "name": "ModeConfig",
      "fields": [
        "MapModeConfig: mode=Literal['map'], collection: str, max_parallelism: Optional[int]",
        "FoldModeConfig: mode=Literal['fold'], collection: str, accumulator_init: Any",
        "LoopModeConfig: mode=Literal['loop'], condition: str, max_iterations: Optional[int]",
        "SequentialModeConfig: mode=Literal['sequential'], metadata: Optional[dict]"
      ],
      "constraints": [
        "Discriminated union with `mode` as discriminator field",
        "`mode_config` on PhaseDefinition typed as Union[MapModeConfig, FoldModeConfig, LoopModeConfig, SequentialModeConfig]",
        "Exactly one mode-specific config variant applies per phase",
        "Non-sequential modes require their matching config variant",
        "Separate flat fields (`map_config`, `fold_config`, `loop_config`, `sequential_config`) are not valid on PhaseDefinition"
      ],
      "is_new": true
    },
    {
      "name": "EdgeDefinition",
      "fields": [
        "source: str",
        "target: str",
        "transform_fn: Optional[str]",
        "description: Optional[str]"
      ],
      "constraints": [
        "`source` and `target` use dot notation or `$input`/`$output` boundary refs",
        "No serialized `port_type`",
        "Hook-vs-data determined by source port container",
        "Hook edges must not define `transform_fn`"
      ],
      "is_new": true
    },
    {
      "name": "PortDefinition",
      "fields": [
        "type_ref: Optional[str]",
        "schema_def: Optional[dict]",
        "description: Optional[str]",
        "required: Optional[bool]"
      ],
      "constraints": [
        "Exactly one of `type_ref` or `schema_def`",
        "Applies uniformly to inputs, outputs, hooks, and `BranchNode.outputs` (BranchOutputPort extends this contract with a `condition` field)"
      ],
      "is_new": true
    },
    {
      "name": "HookPortEvent",
      "fields": [
        "source_id: str",
        "source_type: str",
        "event: str",
        "status: str",
        "result: Optional[Any]",
        "error: Optional[str]",
        "timestamp: str",
        "duration_ms: Optional[int]",
        "cost_usd: Optional[float]"
      ],
      "constraints": [
        "Produced by hook ports and delivered through ordinary edges"
      ],
      "is_new": true
    },
    {
      "name": "TemplateDefinition",
      "fields": [
        "id: str",
        "name: str",
        "description: Optional[str]",
        "phase: PhaseDefinition | Ref",
        "bind: Optional[dict[str, Any]]"
      ],
      "constraints": [
        "Expands into the same nested phase contract as inline phases"
      ],
      "is_new": true
    },
    {
      "name": "PluginInterface",
      "fields": [
        "id: str",
        "name: str",
        "description: Optional[str]",
        "inputs: dict[str, WorkflowInputDefinition]",
        "outputs: dict[str, WorkflowOutputDefinition]",
        "config_schema: dict"
      ],
      "constraints": [
        "Plugin nodes stay within the shared typed-port contract",
        "No separate root `plugin_instances` registry required"
      ],
      "is_new": true
    }
  ],
  "cross_service_impacts": [
    {
      "service": "iriai-compose (SF-2 loader/runner)",
      "impact": "The loader and runner must consume nested phase containment as `phases[].nodes` and `phases[].children`, honor phase-local `edges`, resolve `actor_type: agent|human`, infer hook-vs-data behavior from port resolution with no serialized `port_type`, and evaluate `BranchNode` per-port conditions using the expression sandbox with non-exclusive fan-out.",
      "action_needed": "Update loader hydration, graph-building, and validation to treat `children` as the recursive phase field, evaluate per-port conditions non-exclusively (multiple outputs may fire), accept `merge_function` as a gather hook, reject stale actor/root/hook/branch fields including `switch_function`, `condition_type`, `output_field`, and `interaction` alias, and preserve additive compatibility with the imperative API."
    },
    {
      "service": "iriai-compose (SF-3 testing framework)",
      "impact": "Fixtures and assertions must construct workflows in the nested YAML shape and stop assuming flat nodes, serialized `port_type`, or any alternate hook model. Branch fixtures must use the per-port `outputs` model with per-port `condition` expressions.",
      "action_needed": "Refresh schema fixtures, round-trip tests, and negative tests so they author nested phases, use `actor_type: agent|human`, use per-port branch conditions, and assert rejection of stale fields like `switch_function`, `condition_type`/`condition`/`paths`, and serialized `port_type`."
    },
    {
      "service": "iriai-build-v2 migration tooling (SF-4)",
      "impact": "Migration output must emit nested phase YAML using `children`, ordinary hook edges, and per-port `BranchNode.outputs` conditions so the translated workflows are valid for both the loader and editor.",
      "action_needed": "Rewrite translation and fixture assumptions away from stale branch fields (`switch_function`, `condition_type`/`condition`/`paths`) and ensure build-v2 planning, develop, and bugfix workflows target the canonical per-port branch contract."
    },
    {
      "service": "iriai-workflows backend (SF-5 composer-app-foundation)",
      "impact": "The backend becomes the canonical schema delivery layer through `GET /api/schema/workflow`; validation and editor bootstrap should consume the live schema rather than a bundled static file, and backend models must not add root `stores` / `plugin_instances` drift.",
      "action_needed": "Implement `/api/schema/workflow` as the authoritative composer schema endpoint, wire validation to the same schema package, and remove static-schema-first plus extra-root-field wording from PRD/plan artifacts."
    },
    {
      "service": "iriai-workflows frontend (SF-6 workflow-editor)",
      "impact": "The editor's serializer/deserializer must keep its internal flat store private and round-trip to the nested YAML contract with `phases[].nodes`, `phases[].children`, ordinary hook edges, per-port `BranchNode.outputs` conditions, and no serialized `port_type`.",
      "action_needed": "Keep the transformation layer but rewrite stale PRD/system-design text so runtime schema fetch comes from `/api/schema/workflow`, hook serialization stays edge-based, branch UI reflects per-port non-exclusive conditions with optional `merge_function`, actor model uses `agent|human` only, and nested containment is the only YAML contract."
    }
  ],
  "open_questions": [
    "No schema-shape open questions remain. Caching behavior for `/api/schema/workflow` is an implementation concern and does not change the canonical wire contract."
  ],
  "requirements": [],
  "acceptance_criteria": [],
  "out_of_scope": [
    "Execution-engine implementation details beyond the schema/validation contract.",
    "Any alternate flat YAML dialect with top-level nodes or detached phase membership lists.",
    "Separate serialized hook sections, hook registries, or hook-specific edge discriminators such as `port_type`.",
    "Treating static `workflow-schema.json` as the editor's canonical runtime schema source.",
    "Root `stores` or `plugin_instances` registries without new approval.",
    "Actor wire aliases other than `actor_type: agent|human` — `interaction` is explicitly excluded.",
    "`switch_function` or any other routing-function branch field — `merge_function` is valid for gather but is not a routing function.",
    "`output_field` as a BranchNode routing mode — removed by D-GR-35.",
    "The stale exclusive single-path `condition_type`/`condition`/`paths` BranchNode shape — replaced by per-port `outputs` model.",
    "Standalone Map/Fold/Loop node types or other compound-node replacements for phase modes.",
    "Replacing or breaking the existing imperative `iriai-compose` subclass API.",
    "Runtime agent execution inside the composer application.",
    "Migration tooling for legacy iriai-build v1 configs."
  ],
  "complete": true
}

---

## Subfeature: DAG Loader & Runner (dag-loader-runner)

{
  "title": "SF-2: DAG Loader & Runner",
  "overview": "Revised the SF-2 PRD to make the SF-1 declarative-schema PRD the single canonical wire contract for the loader and runner, incorporating the D-GR-35 per-port BranchNode model as the authoritative branch routing contract. Key corrections applied: (1) actor union is exactly agent|human — no interaction discriminator; (2) BranchNode uses the D-GR-35 per-port model — inputs dict + optional merge_function for gather + outputs dict where each port carries its own condition expression, with non-exclusive fan-out; (3) switch_function remains rejected; (4) old SF-1 BranchNode fields (condition_type, condition top-level, paths, output_field mode) are now stale and rejected; (5) merge_function is valid and must NOT be rejected; (6) WorkflowConfig root is a closed set matching SF-1 exactly — no stores or plugin_instances; (7) typed ports (PortDefinition with type_ref XOR schema_def) apply uniformly to phase inputs/outputs/hooks, node inputs/outputs/hooks, and BranchNode.outputs. All Cycle 4 runtime decisions remain. /api/schema/workflow is the canonical composer-facing schema source and static workflow-schema.json is explicitly non-runtime.",
  "problem_statement": "SF-2 had been revised for nested phases and edge-based hooks, but stale SF-1 plan and system-design artifacts still described incompatible shapes — runtime workflow-schema.json, alternate actor forms (interaction), unapproved root fields (stores, plugin_instances), untyped hook ports, and conflicting BranchNode models — that could survive as informal runtime contracts if SF-2 did not reject them explicitly.\\n\\nThe loader and runner are the execution choke point. They make the PRD-backed wire contract executable and must reject everything else early. This revision therefore makes the current SF-1 PRD the only source of truth for SF-2's schema-facing behavior: exact root fields (closed set), exact actor discriminators (agent|human), exact typed-port model including hook ports (type_ref XOR schema_def), exact branch routing surface per D-GR-35 (per-port condition expressions on outputs, non-exclusive fan-out, optional merge_function for gather, switch_function rejected), nested phase containment, and runtime-schema delivery boundaries.\\n\\nWithout these anchors, downstream tools — the backend, editor, testing, and migration components — can each drift toward a different schema dialect, making translated iriai-build-v2 workflows unrunnable and the composer unsalvageable as a single authoring surface.",
  "target_users": "Platform engineers implementing or consuming declarative execution in iriai-compose, backend and editor engineers who need runtime and authoring to share one schema contract, and migration and testing engineers verifying that translated iriai-build-v2 workflows are both representable and executable without a second dialect.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "SF-2 MUST treat the current SF-1 PRD and its WorkflowConfig models as the only authoritative declarative wire contract. Validation and execution must use the in-process SF-1 models directly rather than a checked-in schema snapshot or stale SF-1 plan/system-design variants.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:353",
          "excerpt": "/api/schema/workflow returns JSON Schema from model_json_schema().",
          "reasoning": "Confirms the canonical live schema endpoint used by composer integrations."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "WorkflowConfig loading MUST accept only the SF-1 root fields schema_version, workflow_version, name, description, metadata, actors, phases, edges, templates, plugins, types, and cost_config. The loader MUST reject unapproved root additions such as stores, plugin_instances, top-level nodes, or any alternate root graph containers with actionable field-path errors.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "WorkflowConfig Root Fields (Closed Set). No stores or plugin_instances root fields permitted.",
          "reasoning": "SF-1 PRD defines the exact closed root set SF-2 must enforce."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "functional",
      "description": "Actor hydration MUST follow the SF-1 actor union exactly: actor_type is only agent or human. The loader MUST reject stale actor discriminators including interaction, and the runner MUST preserve this wire contract even when host applications adapt human interactions onto existing runtime abstractions.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Actor Types: discriminated by actor_type field with only two valid values: agent and human. No interaction alias permitted.",
          "reasoning": "SF-1 PRD closes the actor union to exactly two discriminators."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "Nested phase containment is authoritative: WorkflowConfig.phases contains top-level phases, each PhaseDefinition owns typed inputs, outputs, hooks, nodes, children, and phase-local edges, and flattened editor stores are never valid serialized runtime input.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "PhaseDefinition: Typed inputs, outputs, and hooks (all use PortDefinition). nodes list, children list for nested phases, phase-local edges list.",
          "reasoning": "SF-1 PRD makes nested containment and typed phase ports authoritative."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "The loader MUST index and validate typed ports across workflow boundaries, phases, nodes, hooks, and BranchNode.outputs using the SF-1 typed-port contract (type_ref XOR schema_def). Each port must define exactly one of type_ref or schema_def. Hook ports participate in the same typed-port system as data ports.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Port Typing: Applies uniformly to phase inputs/outputs/hooks, node inputs/outputs/hooks, and BranchNode output ports. Each port uses PortDefinition with exactly one of type_ref or schema_def.",
          "reasoning": "SF-1 PRD establishes the typed-port contract as universal; updated to reference BranchNode.outputs per D-GR-35."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "functional",
      "description": "The runner MUST build recursive DAGs from nested phases and in-phase nodes at every depth, executing child phases inside their parent phase context and preserving phase-local versus workflow-level edge ownership.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:106",
          "excerpt": "parallel() already provides fail-fast concurrency semantics.",
          "reasoning": "Supports recursive map/fan-out execution expectations."
        }
      ]
    },
    {
      "id": "REQ-7",
      "category": "functional",
      "description": "Hooks MUST be serialized and executed only as ordinary edges whose source resolves to a hook port. Serialized workflows must not include edge.port_type, separate hook sections, callback registries, or any hook-specific edge type.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004",
          "excerpt": "edge.port_type is dropped from serialized form.",
          "reasoning": "Supports the edge-only hook serialization contract."
        }
      ]
    },
    {
      "id": "REQ-8",
      "category": "functional",
      "description": "BranchNode execution MUST follow the D-GR-35 per-port model: inputs is a dict of typed input ports supporting gather from multiple upstream sources; the optional merge_function is valid and governs how multiple inputs are combined before condition evaluation; outputs is a dict where each key names an output port and each port's condition expression is evaluated independently; fan-out is non-exclusive — multiple output ports MAY fire in the same execution if their conditions are met. switch_function is not a valid field and MUST be rejected. The old SF-1 BranchNode fields condition_type, condition (top-level), paths, and output_field mode are stale and MUST be rejected.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "D-GR-12 per-port model is the single authority. Fan-out is non-exclusive. merge_function is valid for gather. switch_function remains rejected. output_field is fully removed. old condition_type/condition/paths are stale.",
          "reasoning": "D-GR-35 makes the per-port BranchNode model authoritative and supersedes the old SF-1 exclusive three-field schema."
        }
      ]
    },
    {
      "id": "REQ-9",
      "category": "functional",
      "description": "SF-2 MUST execute only the canonical atomic node types AskNode, BranchNode, and PluginNode, with sequential/map/fold/loop behavior owned by phase modes rather than standalone Map/Fold/Loop node executors.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Three Atomic Node Types: AskNode, BranchNode, PluginNode.",
          "reasoning": "SF-1 PRD limits atomic node types to three; phase modes own iteration semantics."
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25",
          "excerpt": "Existing workflows rely on nested review loops and phase sequencing.",
          "reasoning": "Confirms the litmus-test workflow patterns SF-2 must execute declaratively."
        }
      ]
    },
    {
      "id": "REQ-10",
      "category": "functional",
      "description": "Sequential, map, fold, and loop phases MUST dispatch recursively from the nested phase tree so translated iriai-build-v2 workflows preserve review loops, parallel analysis, retry behavior, and child-phase structure. Loop-mode phases must preserve the independently routable condition_met and max_exceeded exits.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Loop mode exposes two exit ports: condition_met and max_exceeded.",
          "reasoning": "SF-1 PRD defines the loop dual-exit contract SF-2 must route through the ordinary edge model."
        },
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25",
          "excerpt": "Existing workflows rely on nested review loops and phase sequencing.",
          "reasoning": "Confirms the litmus-test workflow patterns SF-2 must execute declaratively."
        }
      ]
    },
    {
      "id": "REQ-11",
      "category": "functional",
      "description": "AgentRuntime.invoke() MUST remain unchanged, and SF-2 MUST propagate node identity and hierarchical context through runner-managed ContextVar state with merge order workflow -> phase -> actor -> node. Declarative execution must not require a breaking runtime ABI change.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep invoke() unchanged; merge workflow -> phase -> actor -> node.",
          "reasoning": "Preserves runtime compatibility while standardizing declarative context assembly."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:32",
          "excerpt": "ContextVar-backed phase identity already exists in the runtime.",
          "reasoning": "Supports the non-breaking context propagation requirement."
        }
      ]
    },
    {
      "id": "REQ-12",
      "category": "functional",
      "description": "SF-2 MUST expose validate(workflow) for structural validation without live runtimes and run(workflow, config, *, inputs=None) for structural plus runtime-reference validation against the exact same SF-1 contract. run() must not accept documents that validate() would reject as non-canonical.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep invoke() unchanged; merge workflow -> phase -> actor -> node.",
          "reasoning": "Preserves runtime compatibility while standardizing declarative context assembly."
        }
      ]
    },
    {
      "id": "REQ-13",
      "category": "functional",
      "description": "/api/schema/workflow MUST remain the canonical composer-facing schema delivery path because it is derived from the same SF-1 models SF-2 executes. SF-2 must not depend on runtime workflow-schema.json, and composer/editor failure states must surface endpoint unavailability instead of silently falling back to a stale local bundle.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:353",
          "excerpt": "/api/schema/workflow returns JSON Schema from model_json_schema().",
          "reasoning": "Confirms the canonical live schema endpoint used by composer integrations."
        }
      ]
    },
    {
      "id": "REQ-14",
      "category": "functional",
      "description": "Validation MUST reject stale contract drift with actionable errors, including: stores, plugin_instances, top-level nodes (root-level), alternate actor discriminators (interaction), missing typed hook ports, switch_function, old BranchNode top-level fields condition_type / condition / paths / output_field mode, unknown branch output port references, serialized port_type, separate hook sections, invalid nested containment, and hook edges carrying transform_fn. merge_function is valid and MUST NOT be rejected.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "switch_function remains rejected. merge_function is valid for gather. output_field is fully removed. old condition_type/condition/paths are stale.",
          "reasoning": "D-GR-35 revises the stale-field rejection list: merge_function is removed from rejection, switch_function and old three-field schema remain."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Acceptance criteria validate rejection of stale fields including port_type, interaction actor, switch_function, stores.",
          "reasoning": "SF-1 PRD makes rejection of stale fields a first-class requirement."
        }
      ]
    },
    {
      "id": "REQ-15",
      "category": "functional",
      "description": "Declarative execution MUST return a single observability contract via ExecutionResult plus ExecutionHistory / phase metrics keyed by logical phase ID, while keeping checkpoint/resume out of the core SF-2 API.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-24",
          "excerpt": "Execution history and phase metrics are core; checkpoint/resume is not.",
          "reasoning": "Moves resumability above SF-2 while keeping observability in scope."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md:1815",
          "excerpt": "ExecutionResult includes history-based observability surface.",
          "reasoning": "Supports the execution-output contract after D-GR-24."
        }
      ]
    },
    {
      "id": "REQ-16",
      "category": "security",
      "description": "Expression-bearing behavior and hook behavior MUST remain explicit and inspectable. Each BranchNode output port condition is an expression string evaluated under the shared expression security contract (AST allowlist, timeout, size limits). There is no output_field mode per port — per-port conditions are expressions only. Hook classification must come from port resolution rather than executable serialized metadata.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Per-port conditions are expressions only — no output_field mode per port. output_field is fully removed from the BranchNode schema everywhere.",
          "reasoning": "D-GR-35 removes output_field as a per-port routing mode; all per-port conditions are expressions subject to sandbox security."
        },
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        }
      ]
    },
    {
      "id": "REQ-17",
      "category": "non-functional",
      "description": "Declarative execution MUST ship additively under a new namespace without breaking DefaultWorkflowRunner, WorkflowRunner.parallel(), current storage abstractions, or existing imperative workflows that import iriai-compose.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep invoke() unchanged; merge workflow -> phase -> actor -> node.",
          "reasoning": "Preserves runtime compatibility while standardizing declarative context assembly."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:106",
          "excerpt": "parallel() already provides fail-fast concurrency semantics.",
          "reasoning": "Supports recursive map/fan-out execution expectations."
        }
      ]
    },
    {
      "id": "REQ-18",
      "category": "functional",
      "description": "Live integration coverage SHOULD use configured plugin runtimes or externally managed stdio MCP servers plus separate test runtimes; the SF-2 runner must not take ownership of MCP subprocess lifecycle or add production-plugin test branches.",
      "priority": "should",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-25",
          "excerpt": "Use separate test runtimes and external stdio MCP servers.",
          "reasoning": "Keeps plugin/runtime integrations aligned with existing repo boundaries."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "Developer runs a workflow whose root document contains only the approved SF-1 fields and whose phases contain nested nodes and children.",
      "expected_observation": "The loader accepts the workflow through the in-process SF-1 models, builds recursive phase/node DAGs, and executes it successfully through the declarative runner.",
      "not_criteria": "The loader expects flattened top-level nodes, accepts extra root containers, or relies on a checked-in schema file.",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-4",
        "REQ-6",
        "REQ-12"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "Developer validates YAML that includes root-level stores or plugin_instances.",
      "expected_observation": "Validation fails before execution with a field-specific error explaining that those root additions are not part of the canonical SF-1 WorkflowConfig contract.",
      "not_criteria": "Runtime silently ignores the extra root fields or accepts them as informal extensions.",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "No stores or plugin_instances root fields permitted.",
          "reasoning": "SF-1 PRD closes the root set; AC-2 verifies the loader enforces that closure."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "Developer defines both an agent actor and a human actor in one workflow and executes Ask nodes that reference them.",
      "expected_observation": "The loader accepts the actor union exactly as declared by SF-1, and the runner resolves each actor through the host runtime bridge without changing the workflow wire shape.",
      "not_criteria": "The workflow must serialize interaction instead of human, or the runner mutates the saved contract to match a host-specific actor model.",
      "requirement_ids": [
        "REQ-3",
        "REQ-11",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Actor Types: discriminated by actor_type field with only two valid values: agent and human. No interaction alias permitted.",
          "reasoning": "SF-1 PRD closes the actor union; AC-3 verifies round-trip fidelity."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "Developer validates YAML that uses actor_type: interaction or mixes human fields with agent-only fields.",
      "expected_observation": "Validation fails with a precise actor-path error that points back to actor_type: agent|human and the correct field family.",
      "not_criteria": "The loader tolerates stale actor discriminators or guesses how to coerce the actor into a valid shape.",
      "requirement_ids": [
        "REQ-3",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "No interaction alias permitted.",
          "reasoning": "SF-1 PRD makes interaction explicitly prohibited; AC-4 verifies early rejection."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "Developer wires a phase on_start edge and a node on_end edge using ordinary source/target refs with typed hook ports.",
      "expected_observation": "Validation accepts the edges, infers hook behavior from the source hook port, and preserves hook ports inside the same typed-port system used for data ports.",
      "not_criteria": "Hook execution requires port_type, a separate hooks block, or untyped hook ports that bypass validation.",
      "requirement_ids": [
        "REQ-4",
        "REQ-5",
        "REQ-7",
        "REQ-16"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Hook ports are part of the node port model (no separate hook section). EdgeDefinition: No serialized port_type field.",
          "reasoning": "SF-1 PRD makes typed hook ports and edge-based hook inference authoritative."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "Developer defines a port using only schema_def, another using only type_ref, and then creates a hook edge and a data edge across nested phases.",
      "expected_observation": "Validation succeeds for the XOR-typed ports, indexes both data and hook ports correctly, and enforces type compatibility across the nested graph and BranchNode.outputs.",
      "not_criteria": "Hook ports are exempt from the typed-port rules, or the runner accepts ports with both or neither typing field.",
      "requirement_ids": [
        "REQ-5",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
          "excerpt": "Each port uses PortDefinition with exactly one of type_ref or schema_def. Must not define both; must define at least one.",
          "reasoning": "SF-1 PRD defines the XOR constraint; AC-6 verifies uniform enforcement across all port positions including BranchNode.outputs."
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "Developer defines a BranchNode with inputs (one or more typed input ports), an optional merge_function, and outputs where each port has a condition expression; then connects downstream edges from selected output ports.",
      "expected_observation": "For each output port whose condition evaluates to true, the runner fires every edge attached to that port — multiple output ports may fire in the same execution. When no condition is met, no output fires and execution records the no-match outcome. merge_function is accepted and used to combine multiple inputs before condition evaluation.",
      "not_criteria": "Branch routing depends on switch_function; old condition_type / condition / paths fields are accepted; only one output port is permitted to fire per execution (exclusive routing); merge_function triggers a validation error.",
      "requirement_ids": [
        "REQ-5",
        "REQ-8",
        "REQ-16"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "Fan-out is non-exclusive. merge_function is valid for gather. Per-port conditions are expressions only. switch_function remains rejected. output_field is fully removed.",
          "reasoning": "D-GR-35 per-port model is the single authority; AC-7 verifies the non-exclusive fan-out, merge_function acceptance, and per-port expression evaluation."
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "Developer validates YAML containing switch_function, old BranchNode fields condition_type, condition (top-level), paths, or output_field mode, or an edge referencing an unknown BranchNode output port name.",
      "expected_observation": "Validation fails with a migration-oriented error naming each unsupported field and directing the author to the D-GR-35 per-port outputs model. For unknown output port references, the error lists the valid output port names. merge_function does NOT trigger an error.",
      "not_criteria": "Runtime silently accepts switch_function or the old three-field branch schema; merge_function is incorrectly rejected as stale.",
      "requirement_ids": [
        "REQ-8",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-35",
          "excerpt": "switch_function remains rejected. merge_function is valid. old condition_type/condition/paths are stale. output_field is fully removed.",
          "reasoning": "D-GR-35 revises the stale-field rejection list; AC-8 verifies the updated boundary."
        }
      ]
    },
    {
      "id": "AC-9",
      "user_action": "Developer executes translated iriai-build-v2 workflows that include nested fold/loop review patterns and parallel analysis steps.",
      "expected_observation": "Phase modes and child-phase recursion execute correctly, and phase metrics/history are keyed by logical phase ID.",
      "not_criteria": "Branch nodes or hook edges are repurposed to emulate missing phase semantics, or nested loops flatten into one-level execution.",
      "requirement_ids": [
        "REQ-9",
        "REQ-10",
        "REQ-15"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-build-v2/src/iriai_build_v2/workflows/bugfix/phases/diagnosis_fix.py:25",
          "excerpt": "Existing workflows rely on nested review loops and phase sequencing.",
          "reasoning": "Confirms the litmus-test workflow patterns SF-2 must execute declaratively."
        }
      ]
    },
    {
      "id": "AC-10",
      "user_action": "Runtime implementer inspects the declarative runner API and executes a workflow with existing AgentRuntime implementations.",
      "expected_observation": "AgentRuntime.invoke() remains unchanged, node identity/context are propagated through runner-managed context, and no runtime ABI shim is required.",
      "not_criteria": "Declarative execution requires every runtime to adopt a new node_id parameter or a new agent interface.",
      "requirement_ids": [
        "REQ-11",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep invoke() unchanged; merge workflow -> phase -> actor -> node.",
          "reasoning": "Preserves runtime compatibility while standardizing declarative context assembly."
        }
      ]
    },
    {
      "id": "AC-11",
      "user_action": "Composer backend serves GET /api/schema/workflow, and the editor uses it for authoring controls while the runner validates the same YAML in-process.",
      "expected_observation": "Backend, editor, validator, and runner all stay aligned because the endpoint is derived from the exact SF-1 models SF-2 executes.",
      "not_criteria": "Composer or runtime treats workflow-schema.json as a runtime contract or allows endpoint/schema drift to go unnoticed.",
      "requirement_ids": [
        "REQ-1",
        "REQ-12",
        "REQ-13"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:353",
          "excerpt": "/api/schema/workflow returns JSON Schema from model_json_schema().",
          "reasoning": "Confirms the canonical live schema endpoint used by composer integrations."
        }
      ]
    },
    {
      "id": "AC-12",
      "user_action": "Editor opens while /api/schema/workflow is unavailable.",
      "expected_observation": "The UI reports schema unavailability explicitly and defers schema-driven authoring until the endpoint recovers.",
      "not_criteria": "The editor silently falls back to a stale bundled workflow-schema.json and continues authoring against a different contract than the runner.",
      "requirement_ids": [
        "REQ-13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
          "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
        }
      ]
    },
    {
      "id": "AC-13",
      "user_action": "Consumer inspects execution output after a declarative run.",
      "expected_observation": "ExecutionResult exposes completion data plus ExecutionHistory / phase metrics, and no mandatory core checkpoint or resume API is required.",
      "not_criteria": "Runtime correctness depends on a built-in checkpoint store or a resume flag in the core runner surface.",
      "requirement_ids": [
        "REQ-15"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-24",
          "excerpt": "Execution history and phase metrics are core; checkpoint/resume is not.",
          "reasoning": "Moves resumability above SF-2 while keeping observability in scope."
        }
      ]
    },
    {
      "id": "AC-14",
      "user_action": "Live preview or MCP-backed plugin workflows are exercised in test and production-like environments.",
      "expected_observation": "Tests use separate test runtimes and runtime integration uses configured plugin runtimes or external stdio servers.",
      "not_criteria": "The runner spawns and owns MCP subprocess lifecycle or adds production-plugin test-mode branches.",
      "requirement_ids": [
        "REQ-18"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-25",
          "excerpt": "Use separate test runtimes and external stdio MCP servers.",
          "reasoning": "Keeps plugin/runtime integrations aligned with existing repo boundaries."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Execute a canonical nested declarative workflow",
      "actor": "Platform engineer running a YAML workflow through iriai_compose.declarative.run()",
      "preconditions": "The workflow uses only the SF-1 PRD root fields, actors use actor_type: agent|human, phases contain typed inputs/outputs/hooks, BranchNodes use the D-GR-35 per-port model (inputs, optional merge_function, outputs with per-port conditions), and required runtimes are configured.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Call run() with a workflow path and runtime config.",
          "observes": "SF-2 loads the workflow through the current SF-1 models rather than a copied schema file or stale alternate artifact.",
          "not_criteria": "Loading depends on workflow-schema.json at runtime or on a second root-shape definition.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
              "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Let the loader validate the document root and actors.",
          "observes": "Validation confirms only the approved root fields are present and accepts only actor_type: agent|human.",
          "not_criteria": "Extra root fields (stores, plugin_instances) are tolerated, or actor coercion hides a stale interaction discriminator.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
              "excerpt": "WorkflowConfig Root Fields (Closed Set). No stores or plugin_instances. No interaction alias.",
              "reasoning": "SF-1 PRD closes both root fields and actor discriminators."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Let the loader walk the workflow structure.",
          "observes": "The loader indexes typed phase, node, hook, and branch output-port definitions across phases[].nodes, phases[].children, and workflow-level edges. Each BranchNode.outputs port is validated as a BranchOutputPort (typed PortDefinition plus a condition expression).",
          "not_criteria": "Hook ports bypass the typed-port system, nested child phases are flattened implicitly, or old BranchNode.paths fields are accepted.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "Per-port conditions are expressions only. outputs dict where each port carries its own condition expression.",
              "reasoning": "D-GR-35 per-port model defines BranchOutputPort as a PortDefinition extended with a condition expression."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "Enter a phase and execute an Ask node, a Branch node, and a hook edge.",
          "observes": "The Ask node resolves through the unchanged runtime boundary. The Branch node evaluates each output port's condition expression independently; all ports whose conditions evaluate true fire their downstream edges (non-exclusive fan-out). The optional merge_function is called before condition evaluation if multiple inputs are present. The hook edge is discovered by source-port resolution with no switch_function, port_type, or breaking invoke(..., node_id=...) signature required.",
          "not_criteria": "The runner requires switch_function, the old condition_type/condition/paths schema, port_type, or enforces exclusive single-path routing.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "Fan-out is non-exclusive. merge_function is valid. switch_function remains rejected.",
              "reasoning": "D-GR-35 per-port model governs BranchNode execution semantics."
            },
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "Keep invoke() unchanged; merge workflow -> phase -> actor -> node.",
              "reasoning": "Preserves runtime compatibility while standardizing declarative context assembly."
            }
          ]
        },
        {
          "step_number": 5,
          "action": "Observe the completed workflow result.",
          "observes": "ExecutionResult reports completion plus history and phase metrics keyed by logical phase ID.",
          "not_criteria": "Completion depends on a mandatory built-in checkpoint/resume API.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-24",
              "excerpt": "Execution history and phase metrics are core; checkpoint/resume is not.",
              "reasoning": "Moves resumability above SF-2 while keeping observability in scope."
            }
          ]
        }
      ],
      "outcome": "The workflow runs from the same canonical SF-1 / D-GR-35 contract the backend publishes and the editor authors.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-3",
        "REQ-4",
        "REQ-5",
        "REQ-6",
        "REQ-8",
        "REQ-10",
        "REQ-11",
        "REQ-12"
      ]
    },
    {
      "id": "J-2",
      "name": "Share one schema contract across backend, editor, and runner",
      "actor": "Composer/backend engineer integrating SF-5 and SF-6 with iriai-compose",
      "preconditions": "The backend exposes GET /api/schema/workflow, the editor keeps a flat internal store only internally, and SF-2 validates workflows directly against the same SF-1 models.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Serve GET /api/schema/workflow from the backend.",
          "observes": "The endpoint returns JSON Schema derived from WorkflowConfig.model_json_schema() for the canonical SF-1 contract, including the D-GR-35 BranchNode shape.",
          "not_criteria": "The backend serves a stale copied schema file, or serves the old condition_type/condition/paths BranchNode shape.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:353",
              "excerpt": "/api/schema/workflow returns JSON Schema from model_json_schema().",
              "reasoning": "Confirms the canonical live schema endpoint used by composer integrations."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Save a workflow from the editor's flat internal canvas store.",
          "observes": "Save/export serializes to the canonical nested YAML root, groups nodes into phase.nodes, emits children for nested phases, keeps typed hooks, emits BranchNode with inputs/outputs per-port model (and merge_function if present), and omits serialized port_type.",
          "not_criteria": "Save persists editor-only flattening, extra root fields, alternate hook metadata, or old BranchNode.paths shape that the runner rejects.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "D-GR-12 per-port model is the single authority.",
              "reasoning": "BranchNode serialization must use the D-GR-35 shape on save."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Send the saved YAML to validate() and then to run().",
          "observes": "Both APIs accept the same workflow shape because they consume the exact same SF-1 / D-GR-35 contract the endpoint publishes.",
          "not_criteria": "Validation and runtime diverge because they used different schema authorities, or merge_function triggers a rejection in one but not the other.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
              "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
            }
          ]
        }
      ],
      "outcome": "Backend, editor, and runner round-trip one canonical workflow shape with no unofficial schema dialects.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-4",
        "REQ-5",
        "REQ-7",
        "REQ-12",
        "REQ-13",
        "REQ-14"
      ]
    },
    {
      "id": "J-3",
      "name": "Reject stale actor or root-shape drift before execution",
      "actor": "Workflow author importing older YAML into the composer or runner",
      "preconditions": "YAML includes root-level stores, plugin_instances, top-level nodes, actor_type: interaction, or another pre-canonical shape.",
      "path_type": "failure",
      "failure_trigger": "Structural validation sees a stale root field or invalid actor discriminator.",
      "steps": [
        {
          "step_number": 1,
          "action": "Call validate() or run() on the stale workflow.",
          "observes": "Validation fails before execution and points to the unsupported root or actor field with guidance toward the canonical SF-1 PRD shape.",
          "not_criteria": "The loader silently ignores, coerces, or partially executes the stale document.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/declarative-schema/prd.md",
              "excerpt": "No stores or plugin_instances root fields permitted. No interaction alias permitted.",
              "reasoning": "SF-1 PRD closes both root fields and actor discriminators."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Rewrite the workflow to use only approved root fields and actor_type: agent|human, then retry.",
          "observes": "The corrected workflow validates and proceeds to execution against the same canonical contract used everywhere else.",
          "not_criteria": "The author has to maintain a second legacy serialization format for SF-2.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
              "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
            }
          ]
        }
      ],
      "outcome": "Root-shape and actor-shape drift are blocked early so stale SF-1 artifacts cannot survive as alternate runtime contracts.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-2",
        "REQ-3",
        "REQ-12",
        "REQ-14"
      ]
    },
    {
      "id": "J-4",
      "name": "Reject stale hook or branch serialization",
      "actor": "Workflow author importing older YAML into the composer or runner",
      "preconditions": "YAML includes edge.port_type, a separate hooks block, switch_function, old BranchNode fields condition_type / condition (top-level) / paths / output_field mode, or another stale routing field. Note: merge_function is valid under D-GR-35 and does NOT appear in this failure precondition.",
      "path_type": "failure",
      "failure_trigger": "Structural validation sees stale hook metadata or a stale branch routing field (switch_function, old condition_type/condition/paths, or output_field mode per port).",
      "steps": [
        {
          "step_number": 1,
          "action": "Call validate() or run() on the stale workflow.",
          "observes": "Validation fails with field-specific guidance directing the author back to typed ports, ordinary edges for hooks, and the D-GR-35 per-port BranchNode.outputs model. For old condition_type/condition/paths fields, the error explicitly names each stale field and references the inputs/merge_function/outputs replacement shape. merge_function by itself does NOT fail validation.",
          "not_criteria": "The runtime silently infers semantics from stale port_type; switch_function or old condition_type/condition/paths are accepted as compatibility shims; merge_function is incorrectly rejected.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-35",
              "excerpt": "switch_function remains rejected. merge_function is valid. old condition_type/condition/paths are stale. output_field is fully removed.",
              "reasoning": "D-GR-35 revises the stale-field list; J-4 failure path must reflect updated rejection boundary."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Rewrite the workflow through the canonical save/export path and retry.",
          "observes": "The workflow validates because hook behavior is encoded only through source/target port refs and branch routing uses the inputs/merge_function/outputs per-port model.",
          "not_criteria": "The author must preserve a second branch or hook dialect that only one downstream tool understands.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
              "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
            }
          ]
        }
      ],
      "outcome": "Hook and branch drift are rejected early and corrected toward the single D-GR-35-aligned executable wire format.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-5",
        "REQ-7",
        "REQ-8",
        "REQ-12",
        "REQ-14",
        "REQ-16"
      ]
    },
    {
      "id": "J-5",
      "name": "Surface schema-endpoint failure instead of falling back to a stale runtime schema file",
      "actor": "Composer user opening the workflow editor while the backend schema endpoint is unavailable",
      "preconditions": "The editor depends on GET /api/schema/workflow for live authoring metadata.",
      "path_type": "failure",
      "failure_trigger": "The schema request fails or times out.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open the editor and request GET /api/schema/workflow.",
          "observes": "The UI surfaces an explicit schema-unavailable error and disables schema-driven authoring actions until the endpoint recovers.",
          "not_criteria": "The editor silently falls back to a stale bundled workflow-schema.json file.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested YAML, edge-based hooks, live schema endpoint.",
              "reasoning": "Defines the authoritative schema/interface contract SF-2 must consume and enforce."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Retry after the backend restores the endpoint.",
          "observes": "The editor resumes using the live schema and saved workflows continue to validate against the same models SF-2 runs.",
          "not_criteria": "Recovery requires rebuilding the editor or swapping schema files to restore correctness.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:353",
              "excerpt": "/api/schema/workflow returns JSON Schema from model_json_schema().",
              "reasoning": "Confirms the canonical live schema endpoint used by composer integrations."
            }
          ]
        }
      ],
      "outcome": "Schema availability failures degrade visibly and safely instead of reintroducing a static-schema-first runtime contract.",
      "related_journey_id": "J-2",
      "requirement_ids": [
        "REQ-13"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None beyond standard platform engineering controls.",
    "data_sensitivity": "Internal workflow definitions, prompts, typed interfaces, and execution metadata.",
    "pii_handling": "No new direct PII surface in the loader/runner itself. Human actors are schema-level interaction definitions (identity, channel) rather than stored credentials or profiles.",
    "auth_requirements": "Library-level runtime has no auth boundary; composer access to GET /api/schema/workflow is handled by SF-5, but the endpoint must remain the canonical runtime schema source for authoring.",
    "data_retention": "Execution-history retention is determined by the consuming application; SF-2 itself only defines the runtime result surface.",
    "third_party_exposure": "Only through configured agent/plugin runtimes or host-managed human-interaction channels supplied by the consuming application.",
    "data_residency": "No library-level residency guarantees.",
    "risk_mitigation_notes": "Treat the current SF-1 PRD as the only authoritative wire contract and fail fast on all stale variants. Reject alternate root fields (stores, plugin_instances), alternate actor discriminators (interaction), serialized hook metadata (port_type, hooks sections), and stale branch routing surfaces (switch_function, old condition_type/condition/paths, output_field mode per port) so downstream tools cannot drift back toward multiple workflow dialects. Per D-GR-35: merge_function is valid for gather and must not be rejected. All BranchNode per-port output conditions are expressions evaluated under the shared sandbox security contract (AST allowlist, timeout, size limits); there is no output_field declarative-lookup mode on branch output ports."
  },
  "data_entities": [
    {
      "name": "WorkflowConfig",
      "fields": [
        "schema_version (str)",
        "workflow_version (int)",
        "name (str)",
        "description (Optional[str])",
        "metadata (Optional[dict])",
        "actors (dict[str, ActorDefinition])",
        "phases (list[PhaseDefinition])",
        "edges (list[EdgeDefinition]) — cross-phase only",
        "templates (Optional[dict[str, TemplateDefinition]])",
        "plugins (Optional[dict[str, PluginInterface]])",
        "types (Optional[dict[str, JsonSchema]])",
        "cost_config (Optional[WorkflowCostConfig])"
      ],
      "constraints": [
        "Closed set — only the twelve SF-1 PRD root fields are allowed",
        "No root-level stores or plugin_instances",
        "No top-level nodes container",
        "Workflow-level edges are cross-phase only"
      ],
      "is_new": false
    },
    {
      "name": "ActorDefinition",
      "fields": [
        "actor_type: agent | human (discriminator)",
        "agent fields: provider, model, role, persistent, context_keys",
        "human fields: identity, channel"
      ],
      "constraints": [
        "Discriminated union — exactly agent or human",
        "No interaction alias permitted in serialized workflows",
        "No environment-specific credentials embedded in workflow YAML"
      ],
      "is_new": false
    },
    {
      "name": "PhaseDefinition",
      "fields": [
        "id (str)",
        "name (str)",
        "mode: sequential | map | fold | loop",
        "mode-specific config",
        "inputs (dict[str, PortDefinition])",
        "outputs (dict[str, PortDefinition])",
        "hooks (dict[str, PortDefinition])",
        "nodes (list[NodeDefinition])",
        "children (list[PhaseDefinition])",
        "edges (list[EdgeDefinition])",
        "context_keys",
        "metadata",
        "cost"
      ],
      "constraints": [
        "nodes serialize under phases[].nodes",
        "Nested phases serialize under phases[].children",
        "Phase-local edges stay with the owning phase",
        "Loop mode exposes condition_met and max_exceeded exit ports"
      ],
      "is_new": false
    },
    {
      "name": "NodeDefinition",
      "fields": [
        "id (str)",
        "type: ask | branch | plugin",
        "inputs (dict[str, PortDefinition])",
        "outputs (dict[str, PortDefinition])",
        "hooks (dict[str, PortDefinition])",
        "artifact_key",
        "context_keys",
        "cost"
      ],
      "constraints": [
        "Only three atomic node types (AskNode, BranchNode, PluginNode)",
        "Nodes serialize only inside phases[].nodes",
        "Hook ports participate in the same typed-port system as data ports"
      ],
      "is_new": false
    },
    {
      "name": "BranchNode",
      "fields": [
        "inputs (dict[str, PortDefinition]) — one or more typed input ports; supports gather from multiple upstream sources",
        "merge_function (Optional[str]) — optional callable name invoked to combine multiple inputs before condition evaluation; valid field",
        "outputs (dict[str, BranchOutputPort]) — named output ports, each carrying a typed PortDefinition plus a condition expression string"
      ],
      "constraints": [
        "Fan-out is non-exclusive: each output port's condition is evaluated independently; multiple ports MAY fire in the same execution if their conditions are satisfied",
        "Per-port conditions are expressions only — evaluated under the shared sandbox security contract (AST allowlist, timeout, size limits)",
        "No output_field mode per output port",
        "switch_function is not a valid field and MUST be rejected at validation",
        "Old SF-1 BranchNode fields condition_type, top-level condition, paths, and output_field mode are stale and MUST be rejected at validation",
        "merge_function is valid and MUST NOT be rejected",
        "Unknown output port name references in edges are invalid and rejected at validation"
      ],
      "is_new": false
    },
    {
      "name": "BranchOutputPort",
      "fields": [
        "type_ref (Optional[str]) — reference to named type in types registry (inherited from PortDefinition)",
        "schema_def (Optional[dict]) — inline JSON Schema (inherited from PortDefinition)",
        "description (Optional[str]) — (inherited from PortDefinition)",
        "condition (str) — expression string evaluated to determine whether this output port fires; required on every branch output port"
      ],
      "constraints": [
        "XOR: exactly one of type_ref or schema_def must be present (inherited from PortDefinition)",
        "condition must be a non-empty string; empty or missing condition is a validation error",
        "Condition evaluation uses the shared AST-allowlist expression sandbox with timeout and size limits",
        "No output_field shorthand — per-port conditions are expressions only"
      ],
      "is_new": true
    },
    {
      "name": "PortDefinition",
      "fields": [
        "type_ref (Optional[str]) — reference to named type in types registry",
        "schema_def (Optional[dict]) — inline JSON Schema",
        "description (Optional[str])",
        "required (Optional[bool]) — for input ports"
      ],
      "constraints": [
        "XOR: exactly one of type_ref or schema_def must be present",
        "Must not define both; must define at least one",
        "Applies uniformly to phase inputs/outputs/hooks, node inputs/outputs/hooks, and BranchNode.inputs",
        "YAML shorthand (bare string type name) normalizes to full PortDefinition"
      ],
      "is_new": false
    },
    {
      "name": "EdgeDefinition",
      "fields": [
        "source (str) — dot notation e.g. phase_a.node_1 or phase_b.on_end",
        "target (str) — dot notation",
        "transform_fn (Optional[str])",
        "description (Optional[str])"
      ],
      "constraints": [
        "No serialized port_type field",
        "Hook-vs-data inferred from resolving source port container (hooks vs outputs)",
        "Hook edges must not define transform_fn",
        "Source and target use dot notation or boundary refs"
      ],
      "is_new": false
    },
    {
      "name": "RuntimeConfig",
      "fields": [
        "agent_runtime",
        "interaction_runtimes (host-managed human-interaction adapters)",
        "artifacts",
        "sessions",
        "context_provider",
        "plugin_registry",
        "workflow execution wiring"
      ],
      "constraints": [
        "Runtime dependency bundle only; not part of WorkflowConfig",
        "Must not change the declarative wire contract",
        "Must not require breaking AgentRuntime changes"
      ],
      "is_new": true
    },
    {
      "name": "HierarchicalContext",
      "fields": [
        "workflow scope",
        "phase scope",
        "actor scope",
        "node scope"
      ],
      "constraints": [
        "Merge order: workflow -> phase -> actor -> node",
        "Propagated via runner-managed ContextVar — no breaking invoke() changes"
      ],
      "is_new": true
    },
    {
      "name": "ExecutionResult / ExecutionHistory",
      "fields": [
        "completion state",
        "workflow output",
        "trace and branch path data (including which output ports fired per BranchNode execution)",
        "phase metrics/history (keyed by logical phase ID)",
        "hook warnings and execution errors"
      ],
      "constraints": [
        "Metrics keyed by logical phase ID",
        "No mandatory core checkpoint/resume API"
      ],
      "is_new": true
    },
    {
      "name": "ValidationError",
      "fields": [
        "field_path (str)",
        "message (str)",
        "severity (str)",
        "code (str)"
      ],
      "constraints": [
        "Used to reject stale root fields, actor discriminators, hook metadata, branch routing fields, and nested-DAG violations before execution"
      ],
      "is_new": true
    }
  ],
  "cross_service_impacts": [
    {
      "service": "SF-1 Declarative Schema PRD",
      "impact": "SF-2 now treats the current SF-1 PRD as the only canonical wire contract, including the D-GR-35 per-port BranchNode model. BranchNode entity must reflect inputs/merge_function/outputs shape; old condition_type/condition/paths shape is stale.",
      "action_needed": "Align SF-1 BranchNode schema to D-GR-35: inputs dict + optional merge_function + outputs dict with per-port BranchOutputPort (PortDefinition + condition expression). Remove condition_type, top-level condition, paths, and output_field mode from BranchNode everywhere."
    },
    {
      "service": "SF-1 stale plan / system-design artifacts",
      "impact": "Stale SF-1 artifacts still describe the old three-field BranchNode (condition_type/condition/paths) and may reference merge_function as rejected — both are now incorrect under D-GR-35. Also still reference runtime workflow-schema.json and alternate actor forms.",
      "action_needed": "Rewrite stale SF-1 plan/system-design BranchNode sections to the D-GR-35 per-port model. Remove rejections of merge_function; add rejections of switch_function and old condition_type/condition/paths fields. Also fix workflow-schema.json and interaction actor references."
    },
    {
      "service": "SF-5 Composer App Foundation",
      "impact": "Backend must expose GET /api/schema/workflow from the same in-process SF-1 models SF-2 validates and runs, reflecting the D-GR-35 BranchNode shape with inputs/merge_function/outputs.",
      "action_needed": "Remove any static-schema-first assumptions; ensure the schema endpoint reflects BranchNode.outputs (per-port conditions) rather than the old paths shape. Keep endpoint behavior tied to canonical SF-1 models."
    },
    {
      "service": "SF-6 Workflow Editor",
      "impact": "Editor may keep a flat internal store, but save/load/import/export must normalize to the canonical nested YAML contract including D-GR-35 BranchNode shape. merge_function must be accepted without error. Old condition_type/condition/paths must be rejected on import.",
      "action_needed": "Align BranchNode authoring surface to the inputs/merge_function/outputs per-port model; update serializer/importer to stop emitting or tolerating old condition_type/condition/paths/switch_function fields. Validate that merge_function is passed through correctly."
    },
    {
      "service": "SF-3 Testing Framework",
      "impact": "Tests and fixtures must target the D-GR-35 BranchNode contract (per-port outputs, non-exclusive fan-out, merge_function valid, switch_function rejected, old three-field schema rejected).",
      "action_needed": "Refresh BranchNode fixtures to use inputs/merge_function/outputs per-port model. Update assertions so old condition_type/condition/paths/switch_function variants fail explicitly, and merge_function passes. Add non-exclusive fan-out test coverage (multiple ports firing simultaneously)."
    },
    {
      "service": "SF-4 Workflow Migration",
      "impact": "Migrated workflows must emit only the canonical SF-1 / D-GR-35 shape. Translated iriai-build-v2 BranchNode usages must use the per-port outputs model.",
      "action_needed": "Update migration emitters to produce D-GR-35 BranchNode output: translate any old condition_type/condition/paths shapes to inputs/merge_function/outputs per-port form. Verify translated iriai-build-v2 workflows validate and run against the canonical contract."
    },
    {
      "service": "iriai-compose imperative runtime",
      "impact": "Declarative runtime remains additive and cannot break WorkflowRunner, DefaultWorkflowRunner, or existing host integrations. Human actor adaptation happens at the host boundary, not in the wire contract.",
      "action_needed": "Keep new declarative APIs under a separate namespace and preserve current runtime ABCs while adapting human actors at the host boundary."
    }
  ],
  "open_questions": [],
  "requirements": [],
  "acceptance_criteria": [],
  "out_of_scope": [
    "Supporting a second serialized workflow dialect for flattened graphs or alternate root containers.",
    "Serializing hooks through port_type, hidden callback lists, or separate hook sections.",
    "Serializing branch logic through switch_function or the old three-field schema (condition_type / condition / paths). The D-GR-35 per-port outputs model with optional merge_function is the only valid branch routing surface.",
    "Per-port output_field declarative-lookup mode on branch output ports — per-port conditions are expressions only.",
    "Treating workflow-schema.json as a runtime/editor schema contract.",
    "Adding stores or plugin_instances to the declarative WorkflowConfig root without an explicit future PRD change.",
    "A mandatory built-in core checkpoint/resume API in SF-2.",
    "Runner-managed MCP subprocess lifecycle.",
    "Production-plugin test-mode branches as the live-test strategy."
  ],
  "complete": true
}

---

## Subfeature: Testing Framework (testing-framework)

{
  "title": "PRD: Testing Framework (SF-3) Revision R18",
  "overview": "Artifact updated at /Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/prd.md. This revision makes SF-2 dag-loader-runner the unambiguous canonical ABI owner by anchoring all consumer requirements to SF-2 REQ-11 and explicitly declaring plan decision D-SF3-16 non-compliant. The stale ABC block in the SF-3 plan that shows `node_id: str | None = None` on `AgentRuntime.invoke()` is identified as the primary non-compliance artifact and must be removed before implementation proceeds. SF-3 is a pure consumer: it adds test ergonomics on top of the published SF-2 surface and may not redefine the invocation interface, node-identity carrier, or merge order.",
  "problem_statement": "The SF-3 testing-framework plan still encodes plan decision D-SF3-16, which states that \"AgentRuntime.invoke() explicitly owns the node routing contract via node_id kwarg.\" That decision directly contradicts the production AgentRuntime ABC in runner.py:36–50 (no node_id parameter), SF-2 REQ-11 (invoke() MUST remain unchanged, node identity via ContextVar), and Cycle 4 D-GR-23. Until D-SF3-16 is removed and the stale ABC block is corrected, SF-3 and SF-4 will implement against a breaking interface that SF-2 is prohibited from delivering.",
  "target_users": "SF-3 implementers, the Architect (who must remove D-SF3-16 and the stale plan ABC before implementation), and SF-4 migration engineers who need deterministic node-aware tests and consistent hierarchical context behavior against one published SF-2 ABI.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "`MockAgentRuntime` must keep the fluent no-argument API and perform node-specific matching from the current-node `ContextVar` published by SF-2 dag-loader-runner rather than from any change to `AgentRuntime.invoke()`.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep `AgentRuntime.invoke()` unchanged and propagate `node_id` via `ContextVar`.",
          "reasoning": "Authoritative cross-subfeature runtime contract that SF-3 consumes."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:36-50",
          "excerpt": "`AgentRuntime.invoke()` has no `node_id` kwarg in the production ABC.",
          "reasoning": "The existing ABC is the non-breaking contract SF-3 must target."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "Prompt-aware mock behavior and downstream migration parity must consume hierarchical context from SF-2 in the canonical merge order `workflow -> phase -> actor -> node`, deduplicated in that order.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Hierarchical context merge order is `workflow -> phase -> actor -> node`.",
          "reasoning": "Resolves drifting merge-order assumptions across SF-2, SF-3, and SF-4."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31",
          "excerpt": "SF-2 REQ-11 mandates this merge order.",
          "reasoning": "SF-2 owns the merge order as part of its published ABI."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "non-functional",
      "description": "SF-2 dag-loader-runner is the canonical runtime ABI owner for SF-3 and SF-4, as established by SF-2 REQ-11: `AgentRuntime.invoke()` stays unchanged (no `node_id` kwarg), node identity is runner-owned `ContextVar` state, the merge order is `workflow -> phase -> actor -> node`, and core checkpoint/resume is not part of the mandatory SF-2 runtime contract. SF-3 is a consumer of this ABI; it may not redefine any part of it.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31",
          "excerpt": "SF-2 REQ-11: `AgentRuntime.invoke()` MUST remain unchanged; node identity travels via runner-managed `ContextVar`; core checkpoint/resume outside mandatory contract.",
          "reasoning": "SF-2 PRD is the authoritative ABI owner; SF-3 is a downstream consumer."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:36-50",
          "excerpt": "Production ABC has no `node_id` kwarg.",
          "reasoning": "Confirms the non-breaking contract is already live."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "The Architect must remove plan decision D-SF3-16 ('AgentRuntime.invoke() explicitly owns the node routing contract via `node_id` kwarg') and the stale ABC block in the SF-3 plan that shows `node_id: str | None = None` as a parameter of `invoke()`. No SF-3 or SF-4 consumer artifact may retain this contract.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28",
          "excerpt": "D-SF3-16: `AgentRuntime.invoke()` explicitly owns the node routing contract via `node_id` kwarg.",
          "reasoning": "This is the specific stale plan decision that directly contradicts SF-2 REQ-11 and must be removed."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:78-90",
          "excerpt": "Stale ABC block showing `node_id: str | None = None` on `invoke()`.",
          "reasoning": "The plan's verified contract section encodes the breaking interface and must be corrected."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "Execution-path assertions and migration parity checks must rely on SF-2's published observability surface (`ExecutionResult`, `ExecutionHistory`, and phase metrics) rather than on any built-in checkpoint/resume contract from SF-2.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35",
          "excerpt": "SF-2 REQ-15: declarative execution returns `ExecutionResult` plus `ExecutionHistory`/phase metrics while keeping checkpoint/resume out of the core SF-2 API.",
          "reasoning": "Observability is published; checkpoint/resume ownership is not mandatory core ABI."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "functional",
      "description": "SF-3 must not introduce any wrapper, adapter, or consumer-owned mechanism that carries node identity to `AgentRuntime.invoke()` other than reading the runner-published `ContextVar`. Any `when_node()` routing in `MockAgentRuntime` must source node identity exclusively from that `ContextVar`.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:32-33",
          "excerpt": "`_current_phase_var: ContextVar[str]` already exists in production runner.",
          "reasoning": "Establishes the ContextVar pattern that node identity must follow in the declarative runner."
        },
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Node identity propagated via ContextVar.",
          "reasoning": "Consumer-owned carriers would reintroduce the broken ABI through the back door."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "Developer configures `MockAgentRuntime` with both `when_node()` and `when_role()` matchers and runs a workflow through `run(workflow, RuntimeConfig(agent_runtime=mock))`.",
      "expected_observation": "The node-specific matcher wins for the targeted node, the role matcher remains the fallback, and this works under the unchanged `AgentRuntime.invoke()` ABC because node identity is sourced from the SF-2 runner `ContextVar`.",
      "not_criteria": "Role matching must not override node matching, unmatched calls must not silently return `None`, and the test must not require a breaking `invoke(..., node_id=...)` contract.",
      "requirement_ids": [
        "REQ-1",
        "REQ-3"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:36-50",
          "excerpt": "Production ABC confirms no `node_id` kwarg exists.",
          "reasoning": "The acceptance criterion verifies end-to-end node-routing without an ABI break."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "Developer creates `MockAgentRuntime()` with no constructor arguments and configures node-aware behavior through fluent methods only.",
      "expected_observation": "`when_node()` routing and call recording work while `AgentRuntime.invoke()` remains unchanged, and no dict constructor or `node_id` kwarg path exists.",
      "not_criteria": "Dict-based constructor paths must not be accepted, and `when_node()` must not depend on a parameter added to `invoke()`.",
      "requirement_ids": [
        "REQ-1",
        "REQ-4",
        "REQ-6"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:25",
          "excerpt": "D-SF3-2: MockRuntime keeps fluent no-arg builder API.",
          "reasoning": "Even the plan's own fluent-builder decision conflicts with D-SF3-16, confirming D-SF3-16 is the stale outlier."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "Developer or migration engineer uses prompt-aware mock handlers or prompt rendering that depends on hierarchical context.",
      "expected_observation": "Context-sensitive behavior is evaluated against the canonical merged context ordered as `workflow -> phase -> actor -> node`, and no consumer-specific merge contract is needed.",
      "not_criteria": "No alternate merge order may be assumed, and context assembly must not drop or reorder higher-level inputs relative to the published SF-2 ABI.",
      "requirement_ids": [
        "REQ-2",
        "REQ-3"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Hierarchical context merge order `workflow -> phase -> actor -> node`.",
          "reasoning": "Makes merge-order behavior directly testable in consumer code."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "Developer writes execution-path or migration parity assertions after a completed declarative run.",
      "expected_observation": "The available observability surface is `ExecutionResult`, `ExecutionHistory`, and phase metrics as published by SF-2; no mandatory core checkpoint/resume API is required for the assertion contract.",
      "not_criteria": "Tests must not depend on a built-in SF-2 checkpoint/resume ABI, a synthetic `history=` `run()` kwarg, or any consumer-owned resumability contract.",
      "requirement_ids": [
        "REQ-3",
        "REQ-5"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35",
          "excerpt": "SF-2 REQ-15 keeps checkpoint/resume outside the core API.",
          "reasoning": "SF-3 consumers must not reintroduce a checkpoint/resume dependency SF-2 explicitly excluded."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "Architect reviews the SF-3 plan after this revision is applied.",
      "expected_observation": "Plan decision D-SF3-16 has been removed, the stale ABC block showing `node_id: str | None = None` on `invoke()` has been corrected, and every implementation note referencing node routing via `invoke()` parameter has been rewritten to reference the runner `ContextVar`.",
      "not_criteria": "Any version of D-SF3-16 or any `node_id` kwarg on `AgentRuntime.invoke()` must not remain in the consumer plan.",
      "requirement_ids": [
        "REQ-3",
        "REQ-4"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28",
          "excerpt": "D-SF3-16 is the specific stale decision to remove.",
          "reasoning": "Providing a verifiable before/after target for the Architect's plan correction."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "Runtime implementer inspects the declarative runner API and implements `AgentRuntime` for use with the SF-3 test harness.",
      "expected_observation": "`AgentRuntime.invoke()` matches the current production ABC exactly (role, prompt, output_type, workspace, session_key — no `node_id`), and node identity is available through `ContextVar` without any ABC change.",
      "not_criteria": "The SF-3 test harness must not require a runtime implementation that adds `node_id` to `invoke()`.",
      "requirement_ids": [
        "REQ-3",
        "REQ-4",
        "REQ-6"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:36-50",
          "excerpt": "Production ABC: role, prompt, output_type, workspace, session_key — no node_id.",
          "reasoning": "This is the ground truth the plan's stale ABC must be corrected to match."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Run a Node-Aware Test Against the Published SF-2 ABI",
      "actor": "Workflow developer",
      "preconditions": "The developer has the revised SF-3 testing package and an SF-2 runner that publishes current node identity via `ContextVar` and execution observability via `ExecutionResult`/`ExecutionHistory`. SF-2 REQ-11 is the implemented ABI.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Create `MockAgentRuntime()` and configure both `when_node(\"x\")` and `when_role(\"pm\")` matchers.",
          "observes": "The fluent API accepts the configuration with no constructor dicts or explicit runtime-signature changes, because SF-3 is a consumer of the published SF-2 ABI rather than a definer of it.",
          "not_criteria": "The developer must not need to configure a `node_id` kwarg on `AgentRuntime.invoke()` or any consumer-owned context-carrier mechanism.",
          "citations": [
            {
              "type": "code",
              "reference": "iriai-compose/iriai_compose/runner.py:36-50",
              "excerpt": "Production ABC has no `node_id` kwarg.",
              "reasoning": "Confirms the non-breaking signature the fluent API must target."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Run the workflow via `run(workflow, RuntimeConfig(agent_runtime=mock))`.",
          "observes": "Node-specific routing works under the unchanged `AgentRuntime.invoke()` ABC because SF-2 supplies current node identity through its runner `ContextVar`, and prompt-aware handlers see context in `workflow -> phase -> actor -> node` order.",
          "not_criteria": "Execution must not require a breaking `invoke(..., node_id=...)` contract, an alternate merge order, or any wrapper that changes the runner ABI.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:31",
              "excerpt": "SF-2 REQ-11 mandates unchanged invoke() and ContextVar-based node identity.",
              "reasoning": "Anchors the journey step to the authoritative ABI owner requirement."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Assert execution-path behavior with the standard SF-3 assertions.",
          "observes": "Assertions validate the expected node path and execution observability by consuming `ExecutionResult`, `ExecutionHistory`, and phase metrics from SF-2 — no checkpoint/resume API required.",
          "not_criteria": "Assertions must not require a built-in core checkpoint/resume contract, synthetic `history=` `run()` parameters, or consumer-specific result fields outside the published SF-2 ABI.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/dag-loader-runner/prd.md:35",
              "excerpt": "SF-2 REQ-15 keeps checkpoint/resume outside core API.",
              "reasoning": "Verifies that SF-3 assertion contract does not expand SF-2's mandatory surface."
            }
          ]
        }
      ],
      "outcome": "The developer can write deterministic node-aware tests and execution assertions without forcing a runtime-interface break or inventing a parallel resumability contract.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-3",
        "REQ-5",
        "REQ-6"
      ]
    },
    {
      "id": "J-2",
      "name": "Remove Stale Consumer Assumptions Before Implementation",
      "actor": "Architect",
      "preconditions": "The SF-3 plan still contains D-SF3-16 and the stale ABC block showing `node_id: str | None = None` on `invoke()`. The revised R18 PRD and SF-2 PRD/REQ-11 are the authoritative product artifacts.",
      "path_type": "failure",
      "failure_trigger": "A consumer plan or design note requires `invoke(..., node_id=...)`, implies a different context merge order, or treats checkpoint/resume as part of the core SF-2 ABI.",
      "steps": [
        {
          "step_number": 1,
          "action": "Review the SF-3 plan against SF-2 REQ-11 and the revised R18 PRD.",
          "observes": "D-SF3-16 and the stale ABC block (plan.md lines 78–90 showing `node_id: str | None = None`) are identifiable and directly conflict with the published SF-2 ABI.",
          "not_criteria": "The mismatch must not be treated as optional, consumer-local, or deferrable.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/testing-framework/plan.md:28",
              "excerpt": "D-SF3-16 explicitly endorses the breaking `node_id` kwarg.",
              "reasoning": "The concrete stale decision the Architect must remove."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Remove D-SF3-16, correct the stale ABC block, and rewrite all node-routing notes to reference the runner `ContextVar` with merge order `workflow -> phase -> actor -> node`.",
          "observes": "The consumer plan now aligns to SF-2 as ABI owner: `invoke()` has no `node_id` kwarg, node identity comes from `ContextVar`, execution assertions consume SF-2 observability without a checkpoint/resume dependency.",
          "not_criteria": "The revised plan must not retain any `invoke(..., node_id=...)` requirement, conflicting merge-order text, or mandatory core checkpoint/resume dependency.",
          "citations": [
            {
              "type": "code",
              "reference": "iriai-compose/iriai_compose/runner.py:36-50",
              "excerpt": "Corrected plan ABC must match this production signature exactly.",
              "reasoning": "Ground truth against which the plan correction is verified."
            }
          ]
        }
      ],
      "outcome": "Implementation planning proceeds against the published SF-2 ABI (REQ-11) instead of the stale D-SF3-16 breaking-interface assumption.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-2",
        "REQ-3",
        "REQ-4",
        "REQ-5",
        "REQ-6"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None; testing-only library module.",
    "data_sensitivity": "Public synthetic test data.",
    "pii_handling": "No PII expected; mocks and fixtures use synthetic inputs.",
    "auth_requirements": "None at the library surface.",
    "data_retention": "Snapshot files remain developer-managed test artifacts.",
    "third_party_exposure": "None; no external calls are required for the revised contract.",
    "data_residency": "N/A.",
    "risk_mitigation_notes": "Prevent accidental ABI widening in downstream consumers. SF-3 must not force a breaking `AgentRuntime.invoke()` change, must not define a competing runtime-context contract, and must not reintroduce a mandatory checkpoint/resume dependency that SF-2 explicitly does not own. D-SF3-16 is the primary non-compliance risk; its removal must be verified before any SF-3 implementation file is written."
  },
  "data_entities": [
    {
      "name": "MockAgentRuntime",
      "fields": [
        "no-arg constructor",
        "_matchers: list[ResponseMatcher]",
        "calls: list[MockCall]",
        "when_node()",
        "when_role()",
        "default_response()"
      ],
      "constraints": [
        "Must use fluent configuration only",
        "Must read current node identity from the runner-owned ContextVar published by SF-2 — not from any parameter added to AgentRuntime.invoke()",
        "Must not require or simulate AgentRuntime.invoke(node_id=...)",
        "Must not define a testing-owned ABI variant"
      ],
      "is_new": false
    },
    {
      "name": "MockCall",
      "fields": [
        "node_id",
        "role",
        "prompt",
        "output_type",
        "response",
        "cost",
        "timestamp"
      ],
      "constraints": [
        "node_id is captured from runner ContextVar state, not from an invoke() kwarg",
        "Recorded call shape must remain compatible with the unchanged production ABC (runner.py:36–50)"
      ],
      "is_new": false
    },
    {
      "name": "ExecutionResult / ExecutionHistory",
      "fields": [
        "success",
        "nodes_executed: list[tuple[str, str]]",
        "branch_paths: dict[str, str]",
        "history: ExecutionHistory | None",
        "phase metrics"
      ],
      "constraints": [
        "They are the published observability surface for SF-3/SF-4 consumers",
        "They must not be treated as a built-in core checkpoint/resume contract",
        "SF-3 assertion helpers compute node-ID views locally from nodes_executed — they do not extend these SF-2 dataclasses"
      ],
      "is_new": false
    }
  ],
  "cross_service_impacts": [
    {
      "service": "dag-loader-runner (SF-2)",
      "impact": "SF-2 REQ-11 is the canonical ABI contract and SF-2 is the sole owner: AgentRuntime.invoke() unchanged, node identity via runner ContextVar, hierarchical context workflow -> phase -> actor -> node, observability via ExecutionResult/ExecutionHistory, checkpoint/resume outside core contract.",
      "action_needed": "Keep AgentRuntime.invoke() unchanged matching current production ABC at runner.py:36–50. Propagate current node identity via ContextVar. Assemble hierarchical context in canonical order. Keep checkpoint/resume out of the mandatory core runtime contract. No new action needed beyond maintaining REQ-11."
    },
    {
      "service": "testing-framework plan (SF-3)",
      "impact": "Plan decision D-SF3-16 and the stale ABC block at plan.md lines 78–90 showing `node_id: str | None = None` on `invoke()` directly contradict SF-2 REQ-11 and the production ABC. These are the primary non-compliance artifacts blocking implementation.",
      "action_needed": "Remove D-SF3-16 entirely. Correct the stale ABC block to match the production signature (no node_id). Rewrite all node-routing implementation notes to read from the runner ContextVar. Verify no test module adds node_id to invoke()."
    },
    {
      "service": "workflow-migration (SF-4)",
      "impact": "Migration tests, open questions, and bridge assumptions must consume the same SF-2 ABI and observability boundary as SF-3. Any SF-4 artifact that treats D-SF3-16 as a dependency is non-compliant.",
      "action_needed": "Align downstream migration artifacts to the unchanged AgentRuntime.invoke() interface, the canonical merge order, and the no-core-checkpoint/resume boundary. Remove any migration artifact that treats D-SF3-16 as a dependency or assumes invoke() carries node_id."
    }
  ],
  "open_questions": [
    "Should execution snapshots remain JSON-only, or is there still a case for YAML snapshot files?",
    "Should the enhanced `MockAgentRuntime` extend the existing test `MockAgentRuntime` from `iriai-compose/tests/conftest.py`, or remain a fresh implementation in the production `testing/` namespace?",
    "How deep should `validate_type_flow()` inspect inline transforms when inferring type compatibility?",
    "If resume-oriented helpers remain desirable in SF-3, should they be deferred to a follow-on artifact that layers above SF-2's observability surface rather than expanding the runner ABI?"
  ],
  "requirements": [
    "SF-2 dag-loader-runner REQ-11 is the canonical ABI contract; SF-3 is a pure consumer.",
    "`AgentRuntime.invoke()` remains unchanged (no `node_id` kwarg) matching production ABC at runner.py:36–50.",
    "Node-aware routing uses runner-owned `ContextVar` exclusively — no consumer-owned carrier.",
    "Hierarchical context merge order is fixed at `workflow -> phase -> actor -> node`.",
    "Plan decision D-SF3-16 and the stale ABC block at plan.md lines 78–90 are explicitly non-compliant and must be removed before implementation.",
    "Execution assertions consume SF-2 observability (ExecutionResult/ExecutionHistory); no core checkpoint/resume dependency permitted."
  ],
  "acceptance_criteria": [
    "Node-aware mock routing works without changing the `AgentRuntime.invoke()` ABC.",
    "Fluent no-arg mocks remain the only supported construction pattern.",
    "Prompt-aware behavior assumes the canonical `workflow -> phase -> actor -> node` context merge order.",
    "D-SF3-16 and the stale `node_id` kwarg ABC block are removed from the SF-3 plan before implementation.",
    "Runtime implementers targeting SF-3 do not need to add `node_id` to their `AgentRuntime` implementation."
  ],
  "out_of_scope": [
    "Introducing new testing capabilities beyond the R18 ABI-alignment correction.",
    "Breaking the `AgentRuntime` ABC to add a `node_id` kwarg — explicitly prohibited by SF-2 REQ-11 and this PRD.",
    "Supporting alternate hierarchical context merge orders.",
    "Defining or requiring a built-in core checkpoint/resume API in SF-2.",
    "Restoring D-SF3-16 under any consumer-local framing."
  ],
  "complete": true
}

---

## Subfeature: Workflow Migration & Litmus Test (workflow-migration)

{
  "title": "Workflow Migration & Litmus Test (SF-4) — R13",
  "overview": "Revised SF-4 workflow-migration PRD to R13. The primary change formalizes the ownership split that Cycle 5 feedback required: SF-2 dag-loader-runner is named as the canonical **ABI publisher**, and SF-4 is repositioned throughout as a **downstream consumer** that aligns to SF-2's published boundary rather than co-owning or reinterpreting it. Specific changes from R12: (1) REQ-54 strengthened to explicitly name SF-4 as a consumer, not a co-owner; (2) J-1 preconditions updated to state SF-2 \"has published\" the ABI (publisher framing); (3) J-3 actor now identified as a \"downstream consumer of SF-2\"; (4) Cross-service impact for SF-2 retitled \"ABI owner\" with the explicit note that any SF-4/SF-2 conflict is a stale SF-4 artifact, not an SF-2 gap; (5) Out-of-scope list adds \"co-ownership of the SF-2 runtime boundary by SF-4\"; (6) Security risk-mitigation notes add that SF-4 language implying co-ownership must be corrected. No checkpoint/resume language was introduced. No `node_id` kwarg dependency remains.",
  "problem_statement": "SF-4 has to prove that iriai-build-v2 workflows can be translated into portable declarative YAML and run correctly through iriai-compose. That proof only works if migration authors, test utilities, and consumer integrations all target one published runtime boundary and never invent parallel contracts. Earlier revisions left stale downstream assumptions — some plan language still required `invoke(..., node_id=...)`, some bridge artifacts treated checkpoint/resume as a mandatory SF-2 runtime concern, and merge-order precedence remained ambiguous. Cycle 4 (D-GR-23) resolved the runtime-context contract but stale SF-3 and SF-4 consumer artifacts did not yet fully reflect it. Cycle 5 feedback formalizes the ownership split: SF-2 dag-loader-runner is the ABI publisher; SF-3 and SF-4 are downstream consumers that must align to SF-2's published boundary without reinterpreting or extending it.",
  "target_users": "Migration engineers translating iriai-build-v2 workflows to declarative YAML, testing-framework implementers ensuring parity test coverage is aligned to the canonical SF-2 runtime ABI, and platform developers wiring iriai-build-v2 into the declarative runner as a downstream consumer.",
  "structured_requirements": [
    {
      "id": "REQ-34",
      "category": "functional",
      "description": "Hierarchical additive context injection in migrated workflows must consume the canonical SF-2 runtime ABI: structural context resolves in `workflow -> phase -> actor -> node` order, deduplicated with first occurrence preserved, and current node identity is supplied via runner-managed `ContextVar` rather than a changed `AgentRuntime.invoke()` signature.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Keep `AgentRuntime.invoke()` unchanged and propagate `node_id` via `ContextVar`. Hierarchical context merge order is `workflow -> phase -> actor -> node`.",
          "reasoning": "This is the authoritative cross-subfeature runtime contract that SF-4 now adopts as a downstream consumer."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:5-50",
          "excerpt": "`ContextVar` is already used and `AgentRuntime.invoke()` has no `node_id` parameter.",
          "reasoning": "The existing runtime interface confirms the non-breaking pattern the PRD must depend on."
        }
      ]
    },
    {
      "id": "REQ-40",
      "category": "functional",
      "description": "Tier 2 mock execution tests must consume SF-3's fluent `MockAgentRuntime`/`MockInteractionRuntime`/`MockPluginRuntime` surface only where it remains aligned to the SF-2 ABI owner contract, including `ContextVar`-based node matching and no `invoke(..., node_id=...)` dependency.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Node identity propagation uses `ContextVar`, not a breaking keyword argument.",
          "reasoning": "SF-4's mock-execution contract must align with the ratified runtime contract owned by SF-2 and surfaced by SF-3."
        },
        {
          "type": "code",
          "reference": "subfeatures/testing-framework/prd.md:562-617",
          "excerpt": "SF-3 defines fluent `when_node(...)` matching for mock runtimes backed by ContextVar.",
          "reasoning": "SF-4 test expectations must reference the producer artifact that SF-3 now exports, aligned to SF-2."
        }
      ]
    },
    {
      "id": "REQ-53",
      "category": "functional",
      "description": "The iriai-build-v2 declarative bridge and migration smoke coverage must call declarative workflows through `run()` and `RuntimeConfig`, and must consume SF-2's published execution observability surface (`ExecutionResult`, `ExecutionHistory`, phase metrics) without inventing bridge-specific runtime ABI changes or requiring core checkpoint/resume in SF-2.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Avoid an unnecessary ABC break across runtimes.",
          "reasoning": "The bridge is a downstream consumer and must preserve the non-breaking runtime contract published by SF-2."
        },
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:41-50",
          "excerpt": "`invoke()` accepts `role`, `prompt`, `output_type`, `workspace`, and `session_key` only.",
          "reasoning": "The bridge must respect the current abstract interface exported by iriai-compose as published by SF-2."
        }
      ]
    },
    {
      "id": "REQ-54",
      "category": "non-functional",
      "description": "SF-4 requirements, acceptance criteria, journeys, and open questions must treat SF-2 dag-loader-runner as the runtime ABI owner and must not contain stale downstream assumptions about `node_id` kwargs, alternate merge precedence, or mandatory core checkpoint/resume behavior. SF-4 is a consumer, not a co-owner, of the SF-2 runtime boundary.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "SF-2 must remain the ABI owner with a clearly published boundary; SF-3 and SF-4 are consumers that must align downstream.",
          "reasoning": "Cycle 5 feedback formalizes this ownership model. SF-4 artifacts that imply co-ownership must be corrected."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-17",
      "user_action": "Validate hierarchical context injection in a migrated workflow with nested phases and node-scoped mock matching.",
      "expected_observation": "Resolved context is assembled in `workflow -> phase -> actor -> node` order (published by SF-2), Jinja2 templates can access the expected namespaces, and node-scoped behavior is matched through `ContextVar` without a `node_id` kwarg on `AgentRuntime.invoke()`.",
      "not_criteria": "No namespace leakage, no reordered merge precedence, and no SF-4-local reinterpretation of the SF-2 runtime ABI.",
      "requirement_ids": [
        "REQ-34",
        "REQ-54"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "Hierarchical context merge order is `workflow -> phase -> actor -> node`.",
          "reasoning": "This acceptance criterion directly validates the ratified ordering contract as published by SF-2."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "Run a Tier 2 planning-workflow mock execution using SF-3 fluent mock runtimes with node-specific matchers.",
      "expected_observation": "The workflow executes with correct phase-mode assertions, and node-specific mocked responses are selected via `when_node(...)` behavior backed by the shared `ContextVar` path defined by SF-2 and consumed by SF-3.",
      "not_criteria": "No dict-constructor mock setup, no direct `invoke(..., node_id=...)` calls, and no test harness dependency on a core checkpoint/resume API in SF-2.",
      "requirement_ids": [
        "REQ-40",
        "REQ-54"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "subfeatures/testing-framework/prd.md:562-617",
          "excerpt": "SF-3's mock API is fluent and node-scoped, consuming SF-2's ContextVar.",
          "reasoning": "SF-4's Tier 2 tests must consume the current SF-3 test surface aligned to SF-2, not stale assumptions."
        }
      ]
    },
    {
      "id": "AC-23",
      "user_action": "Run the declarative iriai-build-v2 bridge path through `run_declarative()` or the CLI `--declarative` flag.",
      "expected_observation": "The bridge constructs `RuntimeConfig`, calls `run()`, and inspects `ExecutionResult`/`ExecutionHistory`/phase metrics without requiring or passing a `node_id` keyword and without depending on a built-in resume contract in SF-2.",
      "not_criteria": "No direct runtime ABI changes, no bridge-specific `invoke(..., node_id=...)` shim, and no assumption that SF-2 owns checkpoint persistence or resume orchestration.",
      "requirement_ids": [
        "REQ-53"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "iriai-compose/iriai_compose/runner.py:41-50",
          "excerpt": "The abstract runtime signature is unchanged.",
          "reasoning": "The consumer integration must remain compatible with the current runtime interface as published by SF-2."
        }
      ]
    },
    {
      "id": "AC-24",
      "user_action": "Review the revised SF-4 migration artifact and downstream parity expectations against the SF-2 PRD.",
      "expected_observation": "All runtime-boundary language in SF-4 points to SF-2 as ABI owner; SF-4 uses only SF-2's published observability surface; no open question asks SF-2 to define a core checkpoint/resume contract.",
      "not_criteria": "No stale downstream artifact may continue to treat `node_id` kwargs or checkpoint/resume as part of the canonical SF-2 ABI. No SF-4 language implies co-ownership of the SF-2 runtime boundary.",
      "requirement_ids": [
        "REQ-54"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-23",
          "excerpt": "SF-2 must remain the ABI owner with a clearly published boundary; SF-3 and SF-4 are consumers that must align downstream.",
          "reasoning": "This criterion validates the artifact-level hygiene requirement enforced by Cycle 5 feedback."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Translate Planning Workflow Against The Canonical SF-2 ABI",
      "actor": "Migration engineer with access to iriai-build-v2 source, the SF-2 published runtime ABI, and SF-3 fluent mock runtimes",
      "preconditions": "SF-2 has published the approved runtime ABI (invoke unchanged, ContextVar node identity, canonical merge order, ExecutionResult observability, no core checkpoint/resume). SF-3 exposes mock runtimes aligned to that ABI. `planning.yaml` is ready for iterative migration.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Author or update `planning.yaml` using hierarchical context references that rely on workflow, phase, actor, and node scopes.",
          "observes": "The YAML and prompt templates assume the canonical structural context order `workflow -> phase -> actor -> node` as published by SF-2.",
          "not_criteria": "No conflicting merge-order assumption and no lower-scope key duplication intended to override earlier scopes.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "Canonical merge order is `workflow -> phase -> actor -> node`.",
              "reasoning": "This step depends on the ratified context assembly model owned by SF-2."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Run Tier 2 mock execution tests with SF-3 fluent mocks using `when_node(...)` for node-specific behavior.",
          "observes": "Node-scoped matching works through `ContextVar` propagation (runtime-managed by SF-2 and consumed by SF-3 mocks) while `AgentRuntime.invoke()` remains unchanged.",
          "not_criteria": "No direct `invoke(..., node_id=...)` dependency and no stale mock-runtime contract layered on top of the SF-2 ABI.",
          "citations": [
            {
              "type": "code",
              "reference": "subfeatures/testing-framework/prd.md:1976-1980",
              "excerpt": "SF-3 resolves node identity through `ContextVar` published by SF-2 and keeps the runtime signature non-breaking.",
              "reasoning": "This is the concrete SF-3 contract that SF-4 now consumes, itself aligned to SF-2."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Execute the migrated workflow through the iriai-build-v2 declarative bridge.",
          "observes": "The bridge passes existing runtimes through `RuntimeConfig`, calls `run()`, and consumes `ExecutionResult`/history metrics as the published SF-2 observability surface.",
          "not_criteria": "No bridge-local runtime shim and no expectation that SF-2 exposes checkpoint/resume APIs to complete the run.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "Avoid an unnecessary ABC break across runtimes.",
              "reasoning": "The bridge is a downstream consumer of SF-2's published observability contract, not a runtime extender."
            }
          ]
        }
      ],
      "outcome": "The migrated workflow, its tests, and its consumer bridge all run against one published SF-2 runtime ABI. SF-4 has made no extension to that boundary.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-34",
        "REQ-40",
        "REQ-53"
      ]
    },
    {
      "id": "J-2",
      "name": "Remove A Stale node_id Consumer Assumption",
      "actor": "Migration engineer or architect reviewing downstream SF-3 or SF-4 artifacts",
      "preconditions": "SF-2 has published its canonical ABI. A downstream artifact still assumes `AgentRuntime.invoke(..., node_id=...)`.",
      "path_type": "failure",
      "failure_trigger": "A plan, test, or bridge helper encodes a `node_id` kwarg or another consumer-owned ABI extension that was not published by SF-2.",
      "steps": [
        {
          "step_number": 1,
          "action": "Compare the stale consumer artifact against the SF-2 PRD and the current runner signature.",
          "observes": "The mismatch is explicit: SF-2 is the ABI owner with an unchanged `AgentRuntime.invoke()` signature; node identity flows via runner-managed `ContextVar`, not a kwarg.",
          "not_criteria": "The mismatch must not be treated as optional, implicit, or safe to paper over with a consumer-local shim.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "SF-2 must remain the ABI owner with a clearly published boundary.",
              "reasoning": "Any downstream artifact that adds a `node_id` kwarg is widening the boundary it does not own."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Rewrite the consumer artifact so node-aware behavior reads from the shared `ContextVar` path and rerun the affected test or bridge flow.",
          "observes": "The downstream artifact now matches the canonical SF-2 ABI and continues to support node-aware behavior through SF-3 tooling aligned to that ABI.",
          "not_criteria": "The fix must not preserve a hidden `node_id` argument path or a second competing runtime contract.",
          "citations": [
            {
              "type": "code",
              "reference": "subfeatures/testing-framework/prd.md:562-617",
              "excerpt": "SF-3 uses `when_node(...)` matching backed by `ContextVar`, not a `node_id` kwarg.",
              "reasoning": "The corrected artifact should align to the same pattern SF-3 uses as a downstream consumer."
            }
          ]
        }
      ],
      "outcome": "Downstream testing and migration artifacts converge back to the canonical SF-2 runtime boundary, with SF-4 remaining a clean consumer.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-40",
        "REQ-54"
      ]
    },
    {
      "id": "J-3",
      "name": "Run Declarative Bridge Without A Core Resume Contract",
      "actor": "Platform developer integrating iriai-build-v2 with declarative workflows as a downstream consumer of SF-2",
      "preconditions": "A migrated workflow is loadable, the bridge can construct `RuntimeConfig`, and SF-2's published observability surface (`ExecutionResult`, `ExecutionHistory`, phase metrics) is available.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Invoke the declarative bridge path for a migrated workflow.",
          "observes": "The bridge calls `run()` with the canonical SF-2 inputs and existing runtime instances without needing a resume flag, checkpoint store contract, or modified runtime signature.",
          "not_criteria": "The bridge must not require a custom resume flag, checkpoint store contract, or modified runtime signature to start execution.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "Keep `AgentRuntime.invoke()` unchanged.",
              "reasoning": "Existing runtime instances pass through `RuntimeConfig` unchanged; no bridge-local ABI extension is permitted."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Inspect the completed run for parity evidence.",
          "observes": "Completion and debugging data come from SF-2's published `ExecutionResult`, `ExecutionHistory`, and phase metrics keyed by logical phase ID.",
          "not_criteria": "Consumer validation must not depend on a core checkpoint/resume API being present in SF-2; resumability is an application-layer concern.",
          "citations": [
            {
              "type": "code",
              "reference": "subfeatures/dag-loader-runner/prd.md",
              "excerpt": "REQ-15: checkpoint/resume is kept out of the core SF-2 API; observability surface is `ExecutionResult` plus `ExecutionHistory`.",
              "reasoning": "SF-4's bridge validation must be bounded to what SF-2 actually publishes."
            }
          ]
        }
      ],
      "outcome": "Consumer integration validates migration parity through the approved SF-2 observability surface. SF-4 remains a downstream consumer and adds no extension to SF-2's core runtime.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-53",
        "REQ-54"
      ]
    },
    {
      "id": "J-4",
      "name": "Consumer Expects Core Checkpoint/Resume From SF-2",
      "actor": "Platform developer or migration engineer whose downstream artifact treats checkpoint/resume as part of the SF-2 core",
      "preconditions": "SF-2's PRD is available and explicitly scopes checkpoint/resume out of the mandatory core contract.",
      "path_type": "failure",
      "failure_trigger": "A bridge helper, test harness, or migration note treats checkpoint/resume as a mandatory SF-2 runtime API.",
      "steps": [
        {
          "step_number": 1,
          "action": "Review the downstream artifact against the SF-2 PRD and Cycle 4/5 decision log.",
          "observes": "The artifact is out of contract: SF-2 owns execution observability, not a mandatory checkpoint/resume API.",
          "not_criteria": "The mismatch must not be reframed as missing SF-2 functionality or left as an open migration blocker.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-23",
              "excerpt": "Cycle 4 locked the runtime-context and checkpoint boundary; SF-2 must remain the ABI owner; checkpoint/resume is out of core.",
              "reasoning": "Any downstream artifact treating resume as an SF-2 gap is misreading the published contract."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Update the artifact to use workflow-level/plugin-level/app-level recovery where needed and keep SF-2 assertions focused on execution observability.",
          "observes": "The downstream flow now treats resume as an application-layer concern and remains compatible with the canonical SF-2 runner contract.",
          "not_criteria": "The recovery path must not smuggle checkpoint/resume requirements back into SF-2 through test-only or bridge-only abstractions.",
          "citations": [
            {
              "type": "code",
              "reference": "subfeatures/dag-loader-runner/prd.md",
              "excerpt": "REQ-15 explicitly keeps checkpoint/resume out of core; AC-13 confirms no mandatory resume API.",
              "reasoning": "Resume is an application concern; SF-4 must not be the vector that reintroduces it into SF-2."
            }
          ]
        }
      ],
      "outcome": "Migration and bridge validation no longer depend on a core SF-2 checkpoint/resume contract. SF-4 responsibilities are bounded to the published SF-2 observability surface.",
      "related_journey_id": "J-3",
      "requirement_ids": [
        "REQ-53",
        "REQ-54"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None",
    "data_sensitivity": "Internal",
    "pii_handling": "No PII in YAML workflow files or migration test fixtures.",
    "auth_requirements": "No new auth requirement for this PRD revision; runtime/plugin auth remains inherited from the consuming environment.",
    "data_retention": "YAML workflows, parity fixtures, and PRD artifacts remain version-controlled source files with standard repository retention.",
    "third_party_exposure": "No new third-party exposure introduced by this revision; runtime integrations still reference external services only through configured runtimes/plugins.",
    "data_residency": "No geographic residency constraint identified.",
    "risk_mitigation_notes": "The revision makes SF-2 the explicit and sole ABI owner so downstream consumers (SF-4 included) cannot widen the runtime boundary independently. Node-aware behavior is documented as ContextVar-based (runner-managed, not caller-supplied). Checkpoint/resume is explicitly an application-layer concern and must not re-enter the SF-2 core contract through consumer-layer workarounds. SF-4 is positioned as a downstream consumer; any SF-4 language that implies co-ownership of the SF-2 runtime boundary must be treated as a stale artifact requiring correction."
  },
  "data_entities": [
    {
      "name": "HierarchicalContext",
      "fields": [
        "workflow scope",
        "phase scope",
        "actor scope",
        "node scope"
      ],
      "constraints": [
        "Merge order is `workflow -> phase -> actor -> node`, published by SF-2 as the canonical order",
        "Duplicate keys preserve first occurrence in that order",
        "Node identity is runtime-published through runner-managed ContextVar; not passed as a new invoke argument",
        "SF-4 consumes this contract; it does not define or extend it"
      ],
      "is_new": false
    },
    {
      "name": "ExecutionResult / ExecutionHistory",
      "fields": [
        "completion state",
        "workflow output",
        "branch paths",
        "execution history",
        "phase metrics"
      ],
      "constraints": [
        "Observability surface is owned and published by SF-2; SF-4 consumes it",
        "Phase metrics are keyed by logical phase ID",
        "No mandatory core checkpoint/resume API is implied by or required from these structures",
        "SF-4 must not treat the absence of a resume API as an SF-2 gap to be filled by a consumer-layer shim"
      ],
      "is_new": false
    }
  ],
  "cross_service_impacts": [
    {
      "service": "iriai-compose dag-loader-runner (SF-2)",
      "impact": "ABI owner. SF-4 explicitly treats SF-2 as the canonical publisher of the runtime contract: unchanged AgentRuntime.invoke(), ContextVar node propagation, workflow -> phase -> actor -> node merge order, ExecutionResult/ExecutionHistory observability, and no mandatory core checkpoint/resume API.",
      "action_needed": "Keep the published ABI stable as defined. SF-4 has no action items against SF-2; any conflict between SF-4 language and the SF-2 PRD is a stale SF-4 artifact that must be corrected."
    },
    {
      "service": "iriai-compose testing-framework (SF-3)",
      "impact": "Downstream consumer aligned to SF-2 ABI. SF-4 parity tests consume SF-3 only where SF-3 is aligned to the SF-2-owned runtime contract (fluent mocks, ContextVar-based node matching, no node_id kwarg, no checkpoint/resume dependency).",
      "action_needed": "Maintain fluent mock runtimes that read current node identity from the SF-2-published ContextVar; remove any stale node_id kwarg or checkpoint/resume dependency. SF-4 must not consume SF-3 APIs that contradict the SF-2 ABI."
    },
    {
      "service": "iriai-build-v2",
      "impact": "Downstream consumer. The declarative bridge and smoke coverage are explicitly constrained to the published SF-2 runner boundary and observability surface.",
      "action_needed": "Keep the consumer integration additive through run() and RuntimeConfig only, with no bridge-specific invoke shim and no requirement for SF-2-owned checkpoint/resume behavior. Resume is an application-layer concern for iriai-build-v2 to handle independently."
    }
  ],
  "open_questions": [
    "Should actor-centric templates use the exact same storage format in YAML and the composer's CustomTaskTemplate table, or does the composer wrap them with extra metadata?",
    "Is there a nesting-depth limit for phase modes beyond the four-level develop-workflow pattern?",
    "How should the runner resolve inline EdgeTransform `fn` names such as `envelope_extract`?",
    "What mechanism should the declarative path use for phase tracking in iriai-build-v2: callback/hook, wrapper around `run()`, or custom runner subclass?",
    "Should consumer-specific plugin implementations live in iriai-compose with dependency injection or in iriai-build-v2 as adapters?",
    "Should the migrated YAML workflow files live in iriai-build-v2 or in iriai-compose as portable reference workflows?"
  ],
  "requirements": [
    "`AgentRuntime.invoke()` remains unchanged; SF-2 is the publisher, SF-4 is a consumer.",
    "Node identity propagation is `ContextVar`-based (runner-managed by SF-2), not a `node_id` keyword argument.",
    "Hierarchical context assembly order is fixed at `workflow -> phase -> actor -> node` as published by SF-2.",
    "SF-4 Tier 2 tests consume SF-3's fluent mock-runtime API aligned to SF-2; no stale mock constructors or ABI extensions.",
    "Core checkpoint/resume is not part of the mandatory SF-2 runtime contract; SF-4 must not depend on it."
  ],
  "acceptance_criteria": [
    "AC-17 validates the canonical hierarchical merge order and non-breaking runtime contract as published by SF-2.",
    "AC-4 validates Tier 2 planning-workflow execution using SF-3 fluent mocks and ContextVar node matching aligned to SF-2.",
    "AC-23 validates that the iriai-build-v2 declarative bridge stays compatible with the unchanged iriai-compose runtime ABI and does not require checkpoint/resume from SF-2.",
    "AC-24 validates the artifact-level hygiene requirement: all SF-4 language treats SF-2 as ABI owner and SF-4 as consumer only."
  ],
  "out_of_scope": [
    "Changing the abstract `AgentRuntime.invoke()` signature.",
    "Introducing a `node_id` keyword contract in migration tests, bridge code, or any other downstream consumer.",
    "Treating checkpoint/resume as a mandatory core SF-2 runtime API or backfilling it through consumer-layer abstractions.",
    "Reopening the resolved hierarchical merge-order decision from D-GR-23.",
    "Co-ownership of the SF-2 runtime boundary by SF-4; SF-4 is a consumer only."
  ],
  "complete": true
}

---

## Subfeature: Composer App Foundation & Tools Hub (composer-app-foundation)

{
  "title": "Composer App Foundation & Tools Hub (SF-5)",
  "overview": "Revised artifact (Cycle 6). Applies the Cycle 5 rebase (`tools/compose/backend` + `tools/compose/frontend` + `platform/toolshub/frontend`, PostgreSQL + Alembic, exactly five foundation tables, no plugin/phase-template/reference-index surfaces) and resolves four Cycle 6 cross-artifact contradictions: (1) mutation hook event types locked to the exhaustive four-kind enumeration (`created`, `updated`, `soft_deleted`, `restored`), (2) mutation hooks confirmed on all four entity types, (3) starter template persistence locked to DB rows with `user_id='__system__'`, and (4) import endpoint locked to the collection-level `POST /api/workflows/import`.",
  "problem_statement": "SF-5 had drifted from the approved implementation contract in three operationally important ways: stale `tools/iriai-workflows` topology, conflicting SQLite assumptions, and absorbed plugin/reference-index responsibilities. The Cycle 6 review surfaced four additional contradictions between the PRD and related plan/system-design artifacts that would cause implementation conflicts: handlers unsure which event types to fire, hooks tracked on only one of four required entities, starters stored in two incompatible locations, and import endpoints with conflicting path semantics. This revision makes the PRD the authoritative source for all four decisions and removes ambiguity that implementers and downstream subfeatures would otherwise have to resolve independently.",
  "target_users": "Platform developers on hobby and pro tiers who use the tools hub to discover the Workflow Composer and use the compose app to create, import, duplicate, validate, version, and export workflows. SF-6 and SF-7 are also direct downstream consumers: SF-6 depends on SF-5 for the authenticated compose shell, workflow persistence, and canonical runtime schema/validation endpoints; SF-7 depends on SF-5's mutation hook interface (REQ-18) to maintain the reference index.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "SF-5 must follow the accepted repo topology: `tools/compose/backend` for the FastAPI backend, `tools/compose/frontend` for the compose SPA, and `platform/toolshub/frontend` for the static tools hub. `tools/iriai-workflows` is not part of the approved implementation path.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-A3",
          "excerpt": "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`.",
          "reasoning": "This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "The compose backend must use a structured FastAPI service layout aligned with existing platform services: `app/main.py`, `app/config.py`, `app/database.py`, `app/models/`, `app/schemas/`, `app/routers/`, `app/dependencies/`, and `app/middleware/`, with Pydantic Settings-based environment configuration.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/main.py:19",
          "excerpt": "Imports settings, database, logging, routers, and middleware from a structured FastAPI service layout.",
          "reasoning": "SF-5 should reuse the existing platform backend layout pattern instead of inventing a new service structure."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "functional",
      "description": "SF-5 persistence must use PostgreSQL with SQLAlchemy 2.x and Alembic as the schema source of truth, normalize `postgresql://` URLs to `postgresql+psycopg://`, and track migrations in the isolated `alembic_version_compose` table. SQLite is out of scope.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/database.py:13",
          "excerpt": "Converts legacy postgresql:// URLs to postgresql+psycopg:// for psycopg3.",
          "reasoning": "SF-5 should follow the existing database URL normalization pattern used by platform services."
        },
        {
          "type": "decision",
          "reference": "D-A5",
          "excerpt": "PostgreSQL + Alembic is the compose foundation storage contract.",
          "reasoning": "Matches the approved platform direction and avoids stale SQLite drift."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "SF-5 database scope is exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`. SF-5 must not add plugin tables, a tools table, phase-template tables, or `workflow_entity_refs`.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R1",
          "excerpt": "SF-5 is rebased to the accepted `tools/compose` + PostgreSQL/Alembic contract and stays limited to exactly five foundation tables.",
          "reasoning": "The explicit revision request for the current artifact."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R2",
          "excerpt": "Foundation-level `workflow_entity_refs` assumptions are removed from SF-5; reference-index expansion belongs to SF-7.",
          "reasoning": "Prevents scope contamination and keeps table ownership aligned with the accepted contract."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "`WorkflowVersion` is a required audit entity. Workflow create, import, and duplicate operations must create version 1 atomically, and save-version behavior must remain append-only and immutable. Version writes do not trigger mutation hook events on the parent Workflow entity. Version-history browsing, diffing, and restore UI are out of scope for SF-5.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Backend workflow version recording remains required in v1 even though version-history UI is deferred.",
          "reasoning": "Auditability survives the v1 UI scope cut."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R6",
          "excerpt": "`version_saved` does not emit a Workflow entity hook — versions are append-only audit rows, not mutable entity state.",
          "reasoning": "Prevents SF-7 from over-counting entity mutations caused by version writes."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "security",
      "description": "All non-health backend endpoints must require JWT Bearer authentication validated against auth-service JWKS, derive `user_id` from `sub`, and expose `dev_tier` for tools-hub gating.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/dependencies/auth.py:83",
          "excerpt": "JWKS cache and fetch path are defined for JWT validation against the auth service.",
          "reasoning": "SF-5 should follow the same platform JWKS validation pattern for backend auth."
        },
        {
          "type": "code",
          "reference": "platform/auth/auth-service/app/routers/oauth.py:1196",
          "excerpt": "The access token includes the `dev_tier` claim.",
          "reasoning": "Tools-hub tier gating depends on a real token claim already emitted by auth-service."
        }
      ]
    },
    {
      "id": "REQ-7",
      "category": "security",
      "description": "`workflows`, `roles`, `output_schemas`, and `custom_task_templates` must be user-scoped and soft-deletable. Cross-user access attempts must return not-found semantics rather than leaking record existence. `workflow_versions` are immutable audit rows and are not soft-deleted independently.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-2",
          "excerpt": "Soft-delete with recovery was chosen as the safer data-management model.",
          "reasoning": "Soft delete is a user-facing recovery requirement, not just an implementation preference."
        }
      ]
    },
    {
      "id": "REQ-8",
      "category": "functional",
      "description": "The backend must provide the standard platform error envelope, public `GET /health`, public `GET /ready` with database readiness checks, and an explicit production CORS allow-list for compose and tools-hub browser origins rather than wildcard credentials configuration.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/schemas/errors.py:29",
          "excerpt": "ErrorResponse standardizes `error`, `error_description`, and optional `details`.",
          "reasoning": "SF-5 should match the platform error envelope so downstream clients can handle failures consistently."
        }
      ]
    },
    {
      "id": "REQ-9",
      "category": "security",
      "description": "SF-5 must implement per-user rate limiting and structured JSON logging with request correlation and auth/import/delete event coverage, while avoiding raw workflow YAML and prompt-body logging.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:16",
          "excerpt": "Rate-limit key extraction uses JWT `sub` when present and falls back to remote address.",
          "reasoning": "SF-5 should bucket limits by authenticated user whenever possible."
        }
      ]
    },
    {
      "id": "REQ-10",
      "category": "functional",
      "description": "The backend must expose authenticated workflow CRUD, search, and cursor-paginated list/detail endpoints for user-owned workflows.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-3",
          "excerpt": "Cursor-based pagination was chosen for platform consistency.",
          "reasoning": "The workflow list should follow the same pagination contract as the rest of the platform."
        }
      ]
    },
    {
      "id": "REQ-11",
      "category": "functional",
      "description": "The workflow API must support duplicate, import, export, starter-template retrieval, and save-version actions, subject to the following canonical contracts: (a) Import endpoint: `POST /api/workflows/import` is the collection-level creation endpoint — it creates a new user-owned workflow from uploaded YAML. `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint. Import must reject malformed YAML with parse errors, may return validation warnings for schema-invalid YAML, and must never create partial workflow state. (b) Starter template persistence: Starter templates are system-owned rows in the `workflows` table with `user_id='__system__'` and `deleted_at=NULL`, seeded by an Alembic data migration that reads iriai-build-v2 planning/develop/bugfix YAML source files at migration time. No filesystem template assets are served by the compose backend at request time. `GET /api/workflows/templates` returns starter templates without user-scoping. Duplicating a template creates a new user-owned workflow; the system template row is never modified by user actions.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R5",
          "excerpt": "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation). `POST /api/workflows/{id}/import` is not a valid SF-5 endpoint.",
          "reasoning": "Import is fundamentally a creation operation. Standardizing on the collection form removes the endpoint path ambiguity between plan and system design."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R4",
          "excerpt": "Starter templates are persisted as `user_id='__system__'` rows in the `workflows` table, seeded by an Alembic data migration. Filesystem asset serving is not used.",
          "reasoning": "DB rows are queryable via the same API layer as user-owned workflows and support duplicate/versioning semantics consistently without a separate asset-serving surface."
        },
        {
          "type": "decision",
          "reference": "D-7",
          "excerpt": "Starter templates and user workflows both belong on the landing page.",
          "reasoning": "Starter-template delivery is a product requirement, not a developer convenience."
        }
      ]
    },
    {
      "id": "REQ-12",
      "category": "functional",
      "description": "SF-5 must provide baseline authenticated CRUD/list endpoints for `roles`, `output_schemas`, and `custom_task_templates` using the same user-scoping and soft-delete conventions, while deferring advanced delete-reference checks and tool-library surfaces to SF-7.",
      "priority": "should",
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R2",
          "excerpt": "Reference-index expansion and advanced delete-reference checks belong to SF-7.",
          "reasoning": "SF-5 ships foundation-level CRUD only; SF-7 builds the reference-safety layer on top."
        }
      ]
    },
    {
      "id": "REQ-13",
      "category": "functional",
      "description": "`GET /api/schema/workflow` is the canonical runtime schema endpoint and must return `WorkflowConfig.model_json_schema()` from `iriai-compose`. `POST /api/workflows/{id}/validate` must validate against that same runtime contract and return path/message error details.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Runtime schema delivery comes from `/api/schema/workflow`; static `workflow-schema.json` is build/test only.",
          "reasoning": "This is the explicit cycle-4 resolution for schema delivery."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/broad/architecture.md:510",
          "excerpt": "`@router.get(\"/api/schema/workflow\")` returns `WorkflowConfig.model_json_schema()`.",
          "reasoning": "The broad architecture already assumes runtime schema delivery from the backend endpoint."
        }
      ]
    },
    {
      "id": "REQ-14",
      "category": "functional",
      "description": "Persisted and exported workflow YAML must use the canonical nested workflow contract: phase-contained nodes under `phases[].nodes` / `phases[].children`, hook wiring represented through ordinary edges, and no serialized `port_type` or separate `hooks` section.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "YAML remains nested; hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`.",
          "reasoning": "Nested phase containment and edge-only hook serialization are the authoritative YAML contract across all affected subfeatures."
        }
      ]
    },
    {
      "id": "REQ-15",
      "category": "functional",
      "description": "The compose frontend must live in `tools/compose/frontend` as a React 18 + TypeScript + Vite SPA using React Router, React Query, `@homelocal/auth`, and an authenticated API client for the compose backend.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-19",
          "excerpt": "Vite was chosen for the new greenfield frontends.",
          "reasoning": "Bundler selection is already resolved for the compose and tools-hub apps."
        }
      ]
    },
    {
      "id": "REQ-16",
      "category": "functional",
      "description": "SF-5 must provide the compose shell and workflows landing experience with exactly four foundation folders in the Explorer-style sidebar: Workflows, Roles, Output Schemas, and Task Templates. Plugin pages, Tool Library pages, and reference-check UI do not ship in SF-5.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-18",
          "excerpt": "Compose uses Explorer-style sidebar navigation.",
          "reasoning": "This is the approved shell pattern for the compose app."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13",
          "excerpt": "The sidebar shows Workflows, Roles, Output Schemas, and Task Templates, with no Plugins folder.",
          "reasoning": "The current SF-5 design decision artifact already removes stale plugin-folder assumptions."
        }
      ]
    },
    {
      "id": "REQ-17",
      "category": "functional",
      "description": "The tools hub must live at `platform/toolshub/frontend` as a static authenticated SPA that reads `dev_tier`, renders a hardcoded developer-tools card catalog, and routes the Workflow Composer card to `compose.iriai.app` in the same tab.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "The tools hub uses a hardcoded tool-card array for the initial release.",
          "reasoning": "The first release does not require a separate backend for the tools hub catalog."
        }
      ]
    },
    {
      "id": "REQ-18",
      "category": "functional",
      "description": "SF-5's service layer must expose a stable, in-process mutation hook interface for all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`). Hooks are invoked synchronously after a successful database commit and emit one of exactly four event kinds — `created`, `updated`, `soft_deleted`, `restored` — together with the entity type, entity id, and `user_id`. This enumeration is exhaustive: `imported`, `version_saved`, `deleted`, and any other event names are not valid hook events (import maps to `created`; a soft-delete is `soft_deleted` — there is no separate `deleted` kind; version writes do not trigger entity hooks). Hooks must cover all four entity types; a Workflow-only implementation is not sufficient. SF-7 (or any downstream extension) registers refresh callbacks against this interface without modifying SF-5 service code. SF-5 must never create or update `workflow_entity_refs` rows — that responsibility belongs entirely to SF-7 via this hook interface.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R3",
          "excerpt": "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types; SF-7 subscribes to those hooks to maintain `workflow_entity_refs`. SF-5 never creates reference-index rows directly.",
          "reasoning": "Cleanly separates SF-5 from SF-7 without tight coupling while giving SF-7 a reliable trigger surface."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R6",
          "excerpt": "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`. No other event kinds exist.",
          "reasoning": "Removes the contradiction between plan and system design event-type lists and gives SF-7 a stable, closed interface to code against."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "An engineer inspects the approved SF-5 file/repo contract.",
      "expected_observation": "The compose backend/frontend map to `tools/compose/{backend,frontend}`, the tools hub maps to `platform/toolshub/frontend`, and SF-5 does not depend on `tools/iriai-workflows`.",
      "not_criteria": "New SF-5 implementation work is planned under `tools/iriai-workflows`.",
      "requirement_ids": [
        "REQ-1",
        "REQ-15",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-A3",
          "excerpt": "Repo topology is `tools/compose/backend`, `tools/compose/frontend`, and `platform/toolshub/frontend`.",
          "reasoning": "This is the accepted implementation contract and supersedes stale `tools/iriai-workflows` assumptions."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "An engineer inspects the initial Alembic chain and database contract.",
      "expected_observation": "The migration chain uses PostgreSQL, tracks revisions in `alembic_version_compose`, and creates exactly five SF-5 tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`.",
      "not_criteria": "SQLite remains the foundation engine, or plugin/tools/reference-index tables are created in SF-5.",
      "requirement_ids": [
        "REQ-3",
        "REQ-4"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-A5",
          "excerpt": "PostgreSQL + Alembic is the compose foundation storage contract.",
          "reasoning": "Matches the approved platform direction and avoids stale SQLite drift."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "The user opens `tools.iriai.app`, authenticates, and clicks Workflow Composer.",
      "expected_observation": "The tools hub shows the composer card and same-tab navigation lands on `compose.iriai.app` with the authenticated compose shell available.",
      "not_criteria": "Protected tool states are visible before auth resolves, or the composer opens in a new tab.",
      "requirement_ids": [
        "REQ-16",
        "REQ-17"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-10",
          "excerpt": "The tools hub uses hardcoded tool cards for the initial tool catalog.",
          "reasoning": "The initial tools-hub experience is card-driven and does not depend on a backend catalog."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "The user creates a workflow from the Workflows view.",
      "expected_observation": "A workflow row and `WorkflowVersion` v1 are created atomically, and the workflow appears without a full-page reload.",
      "not_criteria": "A workflow is created without version 1, or the user must refresh to see it.",
      "requirement_ids": [
        "REQ-5",
        "REQ-10",
        "REQ-11"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Backend workflow version recording remains required in v1 for the audit trail.",
          "reasoning": "Creation is incomplete unless the initial version row exists immediately."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "An authenticated caller requests `GET /api/schema/workflow`.",
      "expected_observation": "The backend returns JSON Schema generated from `WorkflowConfig.model_json_schema()`.",
      "not_criteria": "A bundled static file is treated as the canonical runtime response.",
      "requirement_ids": [
        "REQ-13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "`/api/schema/workflow` is the canonical schema delivery path for the composer.",
          "reasoning": "This is the direct acceptance test for the cycle-4 schema-source decision."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "A user opens `/workflows/{id}/edit`.",
      "expected_observation": "The frontend requests the workflow record and `GET /api/schema/workflow` before rendering schema-dependent editing affordances.",
      "not_criteria": "The editor boots from a stale bundled schema or skips the runtime schema request.",
      "requirement_ids": [
        "REQ-13",
        "REQ-15"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:261",
          "excerpt": "Editor boot requests the workflow record and `GET /api/schema/workflow`.",
          "reasoning": "SF-6 already assumes this backend integration point."
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "The user saves or exports a workflow with nested phases and hook edges.",
      "expected_observation": "Persisted/exported YAML uses nested phase containment and edge-only hook serialization with no serialized `port_type`.",
      "not_criteria": "Save/export emits a flat root graph, a separate hooks section, or persisted `port_type`.",
      "requirement_ids": [
        "REQ-5",
        "REQ-11",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "YAML remains nested and hook wiring remains edge-based with no serialized `port_type`.",
          "reasoning": "Both structural and hook-serialization assertions are part of the same resolved contract."
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "The user invokes `POST /api/workflows/import` with malformed YAML.",
      "expected_observation": "Import returns parse errors and no workflow or version rows are created.",
      "not_criteria": "Partial workflow or version rows are persisted; the endpoint path used is `POST /api/workflows/{id}/import`; or the user receives only a generic failure with no path/message context.",
      "requirement_ids": [
        "REQ-11",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R5",
          "excerpt": "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation).",
          "reasoning": "AC-8 must reference the canonical endpoint to verify the path standardization decision."
        },
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:608",
          "excerpt": "Import is a first-class endpoint with dedicated validation behavior.",
          "reasoning": "Import should be all-or-nothing and should not persist partial invalid workflows."
        }
      ]
    },
    {
      "id": "AC-9",
      "user_action": "User A attempts to access User B's workflow by ID.",
      "expected_observation": "The API returns a not-found response.",
      "not_criteria": "The API returns a permission error that confirms the record exists.",
      "requirement_ids": [
        "REQ-6",
        "REQ-7"
      ],
      "citations": [
        {
          "type": "code",
          "reference": ".iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553",
          "excerpt": "Ownership checks return 404, not 403.",
          "reasoning": "The current SF-5 plan already defines the security posture for cross-user access."
        }
      ]
    },
    {
      "id": "AC-10",
      "user_action": "An engineer inspects the SF-5 API surface.",
      "expected_observation": "Workflow endpoints plus baseline role/schema/task-template CRUD exist, while `/api/plugins`, `/api/tools`, and `/api/{entity}/references/{id}` are absent from SF-5.",
      "not_criteria": "Plugin, tools, or reference-index surfaces are introduced as foundation APIs.",
      "requirement_ids": [
        "REQ-4",
        "REQ-12",
        "REQ-16"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-55",
          "excerpt": "Plugin database entities and `/api/plugins` are removed from SF-5.",
          "reasoning": "The current SF-5 design decisions already lock the API surface boundaries."
        }
      ]
    },
    {
      "id": "AC-11",
      "user_action": "An engineer inspects production-ready backend behavior.",
      "expected_observation": "`GET /health` and `GET /ready` exist, readiness checks database connectivity, and production CORS is limited to the compose/tools hub browser origins.",
      "not_criteria": "Production uses wildcard credentialed CORS or lacks a readiness check.",
      "requirement_ids": [
        "REQ-8"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/main.py:11",
          "excerpt": "FastAPI app wiring includes global error handling and middleware at the service entry point.",
          "reasoning": "Health and CORS behavior belong in the same service-level integration surface."
        }
      ]
    },
    {
      "id": "AC-12",
      "user_action": "An authenticated caller exceeds the per-user API limit.",
      "expected_observation": "The API returns `429` with retry guidance and logs the event without storing raw YAML or prompt bodies.",
      "not_criteria": "Rate limits are global-only, or structured logs capture full workflow bodies.",
      "requirement_ids": [
        "REQ-9"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "platform/deploy-console/deploy-console-service/app/middleware/rate_limit.py:50",
          "excerpt": "Rate-limit handler returns HTTP 429 and logs rate-limit events.",
          "reasoning": "SF-5 should match the existing platform behavior for limit enforcement and logging."
        }
      ]
    },
    {
      "id": "AC-13",
      "user_action": "An engineer inspects SF-5's service layer.",
      "expected_observation": "A stable mutation hook registration interface exists; it accepts typed callbacks for exactly the four event kinds `created`, `updated`, `soft_deleted`, and `restored` on all four foundation entity types (`Workflow`, `Role`, `OutputSchema`, `CustomTaskTemplate`); hooks fire after successful commit; and no event kinds beyond these four exist in the interface. SF-5 contains no code that creates or updates `workflow_entity_refs` rows.",
      "not_criteria": "SF-5 creates reference-index rows directly; SF-7 must reach into SF-5 model internals to detect entity mutations; the hook interface covers workflows only; or additional event kinds such as `imported`, `version_saved`, or `deleted` exist in the interface.",
      "requirement_ids": [
        "REQ-4",
        "REQ-18"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R3",
          "excerpt": "SF-5 exposes an in-process, post-commit mutation hook interface on all four foundation entity types.",
          "reasoning": "The hook interface is SF-5's contribution to the SF-7 reference-index handoff."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R6",
          "excerpt": "The mutation hook event type enumeration is exhaustive: `created`, `updated`, `soft_deleted`, `restored`.",
          "reasoning": "AC-13 must verify the closed enumeration to prevent SF-7 from coding against phantom event kinds."
        }
      ]
    },
    {
      "id": "AC-14",
      "user_action": "An authenticated user calls `GET /api/workflows/templates` and then duplicates a starter template.",
      "expected_observation": "The response lists system-seeded starter templates (including the iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows; duplicating one creates a new user-owned workflow row with version 1.",
      "not_criteria": "Starter template content is loaded from a filesystem path at request time; duplicating a template modifies the system template row; or starter templates appear in the user's own editable workflow list without an explicit duplicate step.",
      "requirement_ids": [
        "REQ-5",
        "REQ-11"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R4",
          "excerpt": "Starter templates are persisted as `user_id='__system__'` rows, seeded by an Alembic data migration. Filesystem asset serving is not used.",
          "reasoning": "AC-14 directly verifies the persistence approach decision against the two competing options."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Developer launches Compose from the Tools Hub",
      "actor": "Authenticated hobby-tier or pro-tier platform developer",
      "preconditions": "The developer has a valid platform account and can authenticate with auth-service.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Open `tools.iriai.app`.",
          "observes": "A static authenticated tools hub loads with a developer-tools card catalog.",
          "not_criteria": "The page is blank while auth resolves, or protected cards appear actionable before auth state is known.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-12",
              "excerpt": "The tools hub mirrors deploy-console's split-pane landing pattern.",
              "reasoning": "The initial tools-hub experience should look like the established developer-tools launcher surface."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Click the Workflow Composer card.",
          "observes": "The browser navigates in the same tab to `compose.iriai.app`.",
          "not_criteria": "A new tab opens, or the route points to a stale `tools/iriai-workflows` URL.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-A3",
              "excerpt": "Accepted topology is `tools/compose` and `platform/toolshub/frontend`.",
              "reasoning": "The tools hub is an entry point into compose, not an embedded shell."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Wait for compose to load.",
          "observes": "The compose shell opens on the Workflows landing experience with the four SF-5 folders visible.",
          "not_criteria": "Plugin or tool-library surfaces appear in the SF-5 shell, or editor boot blocks the shell from rendering.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/design-decisions.md:13",
              "excerpt": "The Workflows list shell is the default compose landing experience with the 4-folder Explorer layout.",
              "reasoning": "The shell should render independently as the compose landing page."
            }
          ]
        }
      ],
      "outcome": "The developer reaches the canonical compose app entry point through the tools hub.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-16",
        "REQ-17"
      ]
    },
    {
      "id": "J-2",
      "name": "Developer creates a workflow and starts editor bootstrap",
      "actor": "Authenticated compose user",
      "preconditions": "The user is on the compose Workflows landing view.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Click the primary new-workflow action and submit the create form.",
          "observes": "A workflow row is created and `WorkflowVersion` v1 exists immediately.",
          "not_criteria": "The workflow is created without an audit version, or the list requires a full refresh.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-13",
              "excerpt": "Backend version recording is required in v1 for the audit trail.",
              "reasoning": "Initial creation must be versioned immediately to satisfy the audit model."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Open the new workflow.",
          "observes": "The app routes to `/workflows/{id}/edit` without losing authenticated state.",
          "not_criteria": "The route breaks auth state or opens an unknown workflow id.",
          "citations": [
            {
              "type": "code",
              "reference": "platform/deploy-console/deploy-console-frontend/src/App.tsx:79",
              "excerpt": "OAuth callback and protected-route wiring show the expected authenticated SPA routing pattern.",
              "reasoning": "SF-5 should preserve auth-aware route transitions when moving from list view into an editor route."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Let the editor bootstrap start.",
          "observes": "The frontend requests both the workflow record and `GET /api/schema/workflow` before mounting schema-driven editor affordances.",
          "not_criteria": "The editor treats a static bundled schema as authoritative or skips runtime schema fetch.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Static `workflow-schema.json` is build/test only; `/api/schema/workflow` is canonical.",
              "reasoning": "The editor boot path must align with the resolved runtime schema source."
            }
          ]
        }
      ],
      "outcome": "A new workflow exists with version 1 and is ready for schema-aware editing.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-5",
        "REQ-10",
        "REQ-11",
        "REQ-13",
        "REQ-15"
      ]
    },
    {
      "id": "J-3",
      "name": "User saves and exports a canonical workflow definition",
      "actor": "Authenticated compose user editing a workflow",
      "preconditions": "A workflow exists and the editor has schema data available.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Trigger save from the editor.",
          "observes": "The payload is validated against the canonical runtime contract before persistence.",
          "not_criteria": "The save path persists the editor's flat internal graph as canonical YAML.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1006",
              "excerpt": "Flat internal workflow state is grouped into nested phase nodes for serialization.",
              "reasoning": "The editor already models save as a transformation into nested phase YAML."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Save completes.",
          "observes": "The workflow stores nested phase YAML, hook connections remain edges, and a new immutable workflow version is appended.",
          "not_criteria": "Save writes a separate hooks section, stores serialized `port_type`, or updates workflow YAML without creating the next version row.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "Nested phase YAML and edge-only hook serialization with no serialized `port_type` are authoritative.",
              "reasoning": "This is the exact persisted-contract rule SF-5 must enforce."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Export the workflow.",
          "observes": "The downloaded YAML matches the same canonical structure used for save and validation.",
          "not_criteria": "Export emits a different schema shape than the one the backend validated and stored.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:1004",
              "excerpt": "`edge.port_type` is dropped during serialization and hook-ness is reconstructed from ports.",
              "reasoning": "Export must match the same canonical serialization path used during save."
            }
          ]
        }
      ],
      "outcome": "The saved and exported workflow remains portable and runtime-compatible.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-5",
        "REQ-11",
        "REQ-14"
      ]
    },
    {
      "id": "J-8",
      "name": "User imports a valid workflow YAML",
      "actor": "Authenticated compose user",
      "preconditions": "The user has a valid iriai-compose YAML file (e.g. exported from iriai-build-v2).",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Navigate to the Workflows landing view and click Import.",
          "observes": "An import dialog or file picker opens.",
          "not_criteria": "The import action routes to an existing workflow detail or requires an existing workflow id in the URL.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R5",
              "excerpt": "The canonical import endpoint is `POST /api/workflows/import` (collection-level creation).",
              "reasoning": "The import UX should not require a pre-existing workflow id because import creates a new entity."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Upload the YAML file.",
          "observes": "The frontend sends `POST /api/workflows/import` with the file content.",
          "not_criteria": "The request is routed to `POST /api/workflows/{id}/import`, conflating import with an instance-level update.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R5",
              "excerpt": "`POST /api/workflows/{id}/import` is not a valid SF-5 endpoint.",
              "reasoning": "Endpoint path standardization prevents plan/system-design conflicts from surfacing at implementation time."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Import succeeds.",
          "observes": "A new user-owned workflow row is created with `WorkflowVersion` v1, and the user is navigated to the new workflow detail.",
          "not_criteria": "The import overwrites an existing workflow, creates a workflow without a version row, or requires a full-page refresh to see the new entry.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-13",
              "excerpt": "Backend version recording is required in v1.",
              "reasoning": "Import is a create operation — it must atomically create version 1 just like any other creation path."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "Mutation hooks fire.",
          "observes": "SF-5's mutation hook interface emits a `created` event (not `imported`) for the new Workflow entity.",
          "not_criteria": "The import triggers a hook event kind other than `created`, or no hook fires for the new workflow.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R6",
              "excerpt": "`imported` is not a valid hook event kind — import maps to `created`.",
              "reasoning": "The exhaustive four-kind enumeration means import must map to `created` so SF-7 receives the standard reference-index trigger."
            }
          ]
        }
      ],
      "outcome": "A valid YAML file is imported as a new user-owned workflow with version 1, and downstream hook subscribers receive a standard `created` event.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-5",
        "REQ-11",
        "REQ-14",
        "REQ-18"
      ]
    },
    {
      "id": "J-9",
      "name": "User starts from a starter template",
      "actor": "Authenticated compose user",
      "preconditions": "The user is on the compose Workflows landing view. System starter templates have been seeded by the Alembic data migration.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Request `GET /api/workflows/templates`.",
          "observes": "The backend returns system-seeded starter templates (including iriai-build-v2 planning/develop/bugfix workflows) sourced from `user_id='__system__'` DB rows.",
          "not_criteria": "Template content is loaded from a filesystem path at request time rather than from the DB.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R4",
              "excerpt": "Starter templates are persisted as `user_id='__system__'` rows, seeded by an Alembic data migration.",
              "reasoning": "Verifies that the filesystem-asset approach is not used at request time."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Select a starter template to use.",
          "observes": "A duplicate action is triggered, creating a new user-owned workflow row with version 1 derived from the template.",
          "not_criteria": "The system template row is modified, or the duplicate action fails because the template's `user_id` does not match the caller's id.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R4",
              "excerpt": "Duplicating a template creates a new user-owned workflow; the system template row is never modified by user actions.",
              "reasoning": "The duplicate path must bypass the normal user-scoping ownership check for the source row."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Open the duplicated workflow.",
          "observes": "The new workflow appears in the user's workflow list and can be edited, saved, and exported.",
          "not_criteria": "The duplicate points back to the system template row, or the new workflow has no associated version 1 row.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-13",
              "excerpt": "Backend version recording is required in v1.",
              "reasoning": "Duplicate, like create and import, must produce an atomic workflow + version 1 pair."
            }
          ]
        }
      ],
      "outcome": "The user has an editable copy of the starter template; the system template row is unchanged.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-5",
        "REQ-11"
      ]
    },
    {
      "id": "J-4",
      "name": "Canonical schema endpoint is unavailable during editor bootstrap",
      "actor": "Authenticated compose user opening a workflow",
      "preconditions": "A workflow exists, but the schema endpoint is temporarily unavailable.",
      "path_type": "failure",
      "failure_trigger": "`GET /api/schema/workflow` fails or times out.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open `/workflows/{id}/edit` while the schema endpoint is unavailable.",
          "observes": "The editor route shows a blocking, recoverable schema-load error state with retry and back navigation.",
          "not_criteria": "The app silently falls back to a stale local schema or spins forever.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "The runtime composer contract is delivered through `/api/schema/workflow`, not a static schema fallback.",
              "reasoning": "If the canonical endpoint fails, the app must fail explicitly rather than switch contracts."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Retry after the backend recovers.",
          "observes": "The editor bootstrap succeeds using the same runtime schema endpoint.",
          "not_criteria": "Recovery requires a hard refresh or a manual local-schema workaround.",
          "citations": [
            {
              "type": "code",
              "reference": "platform/deploy-console/deploy-console-frontend/src/App.tsx:46",
              "excerpt": "Platform apps already use a query-client-driven SPA shell capable of retrying failed backend fetches.",
              "reasoning": "SF-5 should support normal authenticated SPA recovery behavior for backend-dependent editor boot flows."
            }
          ]
        }
      ],
      "outcome": "Schema failure is explicit and recoverable without changing contracts.",
      "related_journey_id": "J-2",
      "requirement_ids": [
        "REQ-13",
        "REQ-15"
      ]
    },
    {
      "id": "J-5",
      "name": "User imports malformed YAML",
      "actor": "Authenticated compose user importing a workflow file",
      "preconditions": "The user has a YAML file to import.",
      "path_type": "failure",
      "failure_trigger": "The uploaded file is malformed YAML.",
      "steps": [
        {
          "step_number": 1,
          "action": "Upload malformed YAML via `POST /api/workflows/import`.",
          "observes": "The backend returns parse errors and creates no workflow or version rows.",
          "not_criteria": "A partial workflow is persisted; the user receives only a generic failure with no path/message context; or the error comes from `POST /api/workflows/{id}/import`.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R5",
              "excerpt": "The canonical import endpoint is `POST /api/workflows/import`.",
              "reasoning": "The failure journey must reference the canonical endpoint to confirm path standardization."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Correct the file and retry import.",
          "observes": "Import succeeds, creates a workflow plus version 1, and may surface non-blocking validation warnings if the YAML is structurally parseable but not fully schema-valid.",
          "not_criteria": "Retry leaves behind duplicate partial rows from the failed attempt.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "The authoritative contract removes separate hooks sections and serialized `port_type`.",
              "reasoning": "Import validation must explicitly block the stale contract the rewrite was asked to remove."
            }
          ]
        }
      ],
      "outcome": "Invalid YAML is rejected safely, and retrying produces a clean imported workflow.",
      "related_journey_id": "J-8",
      "requirement_ids": [
        "REQ-5",
        "REQ-11",
        "REQ-14"
      ]
    },
    {
      "id": "J-6",
      "name": "Cross-user workflow access is denied without leaking existence",
      "actor": "Authenticated platform developer",
      "preconditions": "Another user's workflow id is known or guessed.",
      "path_type": "failure",
      "failure_trigger": "The actor requests a workflow they do not own.",
      "steps": [
        {
          "step_number": 1,
          "action": "Navigate directly to another user's workflow detail or editor route.",
          "observes": "The API and UI return a not-found result.",
          "not_criteria": "The UI or API confirms that the workflow exists but belongs to someone else.",
          "citations": [
            {
              "type": "code",
              "reference": ".iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/plan.md:553",
              "excerpt": "Ownership checks return 404, not 403.",
              "reasoning": "The current SF-5 plan already defines the security posture for cross-user access."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Return to the user's own workflow list.",
          "observes": "The user's own data remains available and unchanged.",
          "not_criteria": "The failed lookup corrupts local session state or reveals cross-user metadata.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-2",
              "excerpt": "Soft-delete with user-scoping was chosen as the safer data-management model.",
              "reasoning": "User-scoped queries ensure cross-user lookup failure never contaminates the caller's session."
            }
          ]
        }
      ],
      "outcome": "Unauthorized access is blocked without existence leakage.",
      "related_journey_id": "J-2",
      "requirement_ids": [
        "REQ-6",
        "REQ-7"
      ]
    },
    {
      "id": "J-7",
      "name": "Tools hub session is missing and the user must authenticate first",
      "actor": "Platform developer without an active browser session",
      "preconditions": "The user opens the tools hub from a logged-out browser state.",
      "path_type": "failure",
      "failure_trigger": "Protected tools-hub content is requested without a valid auth session.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open `tools.iriai.app` without an active session.",
          "observes": "The user is prompted to authenticate before protected tool cards are usable.",
          "not_criteria": "The hub exposes protected tool access states without auth or renders a blank page.",
          "citations": [
            {
              "type": "code",
              "reference": "platform/deploy-console/deploy-console-frontend/src/App.tsx:46",
              "excerpt": "Platform apps already handle unauthenticated entry via auth redirect.",
              "reasoning": "The tools hub should follow the same unauthenticated entry handling as the existing developer tool apps."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Complete authentication and return to the tools hub.",
          "observes": "The developer-tools card catalog appears and the Workflow Composer card can be used normally.",
          "not_criteria": "The user is stranded on the callback route or loses the intended return path.",
          "citations": [
            {
              "type": "code",
              "reference": "platform/deploy-console/deploy-console-frontend/src/App.tsx:79",
              "excerpt": "OAuth callback route wiring handles the post-auth redirect back to the protected resource.",
              "reasoning": "Post-auth redirect is the established pattern for returning users to their intended destination."
            }
          ]
        }
      ],
      "outcome": "Missing session state is resolved through normal auth flow without exposing protected tools.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-6",
        "REQ-17"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None beyond standard platform security controls.",
    "data_sensitivity": "Internal workflow definitions, prompts, reusable role/task metadata, and JSON Schemas.",
    "pii_handling": "No new end-user PII is stored in SF-5. The backend uses opaque JWT-derived `user_id` for ownership and reads `dev_tier` for tool access gating.",
    "auth_requirements": "JWT Bearer auth via auth-service JWKS on all non-health endpoints. Browser clients use the existing homelocal auth packages and send bearer tokens to the backend.",
    "data_retention": "Workflow, role, schema, and task-template rows are soft-deleted. `workflow_versions` remain append-only audit records. Automatic purge is out of scope for v1.",
    "third_party_exposure": "YAML exports can leave the platform when users download or share them. SF-5 foundation must not store plugin runtime secrets, custom tool configs, or runner-managed credentials.",
    "data_residency": "Railway-hosted PostgreSQL in the configured compose deployment region.",
    "risk_mitigation_notes": "Keep production CORS explicit; rely on bearer-token API calls rather than cookie-bound mutation flows; do not log raw YAML or prompt bodies; use `/api/schema/workflow` as the single runtime schema source; enforce the five-table SF-5 boundary so plugin, tools, and reference-index surfaces only land in their owning subfeatures; expose mutation hooks post-commit using the exhaustive four-kind enumeration (`created`, `updated`, `soft_deleted`, `restored`) so SF-7 can maintain reference-index state without SF-5 owning `workflow_entity_refs` rows; keep starter templates as `user_id='__system__'` DB rows so they are subject to the same query/access layer as user-owned workflows and never served from uncontrolled filesystem paths."
  },
  "data_entities": [
    {
      "name": "Workflow",
      "fields": [
        "id: UUID",
        "name: string",
        "description: string | null",
        "yaml_content: text",
        "current_version: int",
        "is_valid: bool",
        "user_id: string",
        "created_at: datetime",
        "updated_at: datetime | null",
        "deleted_at: datetime | null"
      ],
      "constraints": [
        "Unique per user among non-deleted workflow names (user_id, name, deleted_at IS NULL)",
        "`yaml_content` stores canonical nested workflow YAML",
        "`current_version` mirrors the latest `workflow_versions.version_number`",
        "Rows with `user_id='__system__'` are system-seeded starter templates; they are never soft-deleted by user actions and are returned only by `GET /api/workflows/templates`"
      ],
      "is_new": true
    },
    {
      "name": "WorkflowVersion",
      "fields": [
        "id: UUID",
        "workflow_id: UUID",
        "version_number: int",
        "yaml_content: text",
        "change_description: string | null",
        "user_id: string",
        "created_at: datetime"
      ],
      "constraints": [
        "Unique (workflow_id, version_number)",
        "Append-only after creation",
        "Version writes do not trigger mutation hook events on the parent Workflow entity"
      ],
      "is_new": true
    },
    {
      "name": "Role",
      "fields": [
        "id: UUID",
        "name: string",
        "prompt: text",
        "tools: JSON list[string]",
        "model: string | null",
        "effort: string | null",
        "metadata: JSON object",
        "user_id: string",
        "created_at: datetime",
        "updated_at: datetime | null",
        "deleted_at: datetime | null"
      ],
      "constraints": [
        "Unique per user among non-deleted role names",
        "Fields align with the current iriai_compose.Role contract (name, prompt, tools, model, effort, metadata)"
      ],
      "is_new": true
    },
    {
      "name": "OutputSchema",
      "fields": [
        "id: UUID",
        "name: string",
        "description: string | null",
        "json_schema: JSON",
        "user_id: string",
        "created_at: datetime",
        "updated_at: datetime | null",
        "deleted_at: datetime | null"
      ],
      "constraints": [
        "Unique per user among non-deleted schema names",
        "`json_schema` stores the reusable output contract referenced by workflows"
      ],
      "is_new": true
    },
    {
      "name": "CustomTaskTemplate",
      "fields": [
        "id: UUID",
        "name: string",
        "description: string | null",
        "subgraph_yaml: text",
        "input_interface: JSON",
        "output_interface: JSON",
        "user_id: string",
        "created_at: datetime",
        "updated_at: datetime | null",
        "deleted_at: datetime | null"
      ],
      "constraints": [
        "Unique per user among non-deleted template names",
        "SF-5 stores the foundation record only; SF-7 may extend this table with actor_slots"
      ],
      "is_new": true
    }
  ],
  "cross_service_impacts": [
    {
      "service": "auth-service",
      "impact": "SF-5 consumes JWTs, JWKS validation, and the `dev_tier` claim for tools-hub/composer access flows.",
      "action_needed": "Register compose and tools-hub OAuth clients; no auth-service code changes are required."
    },
    {
      "service": "deploy-console",
      "impact": "SF-5 reuses service layout, auth validation, logging/rate-limit patterns, and authenticated SPA shell conventions.",
      "action_needed": "Use deploy-console as an implementation reference only."
    },
    {
      "service": "iriai-compose",
      "impact": "SF-5 depends on `WorkflowConfig.model_json_schema()` and runtime validation semantics to keep compose persistence aligned with the runner contract.",
      "action_needed": "Keep the compose backend pinned to the iriai-compose version that defines the canonical workflow schema."
    },
    {
      "service": "iriai-build-v2",
      "impact": "SF-5 reads iriai-build-v2 planning/develop/bugfix YAML source files once at Alembic data migration time to seed `user_id='__system__'` starter template rows. No filesystem paths from iriai-build-v2 are retained in the compose service after migration.",
      "action_needed": "Read iriai-build-v2 YAML files during Alembic data migration only; no ongoing runtime dependency on iriai-build-v2 paths."
    },
    {
      "service": "SF-6 Workflow Editor",
      "impact": "SF-6 consumes the authenticated compose shell, workflow CRUD/versioning, runtime schema endpoint, validation endpoint, and canonical YAML contract.",
      "action_needed": "Build editor flows against `/api/schema/workflow` and the nested workflow contract only."
    },
    {
      "service": "SF-7 Libraries & Registries",
      "impact": "SF-7 owns the `workflow_entity_refs` reference-index table and subscribes to SF-5's mutation hook interface to keep that index fresh. SF-5 hooks cover all four entity types and emit exactly four event kinds (`created`, `updated`, `soft_deleted`, `restored`). SF-7 must not register against event kinds beyond these four. SF-7 also adds advanced library UI, reference-safe delete flows, a tools table, and custom_task_templates.actor_slots.",
      "action_needed": "SF-5 must ship the mutation hook interface (REQ-18) before SF-7 work begins. SF-5 must not create or mutate `workflow_entity_refs` rows at any point. SF-7 must not assume `imported`, `version_saved`, or `deleted` event kinds exist."
    }
  ],
  "open_questions": [
    "Should `/api/schema/workflow` expose an ETag or schema hash so the frontend can safely cache and invalidate runtime schema changes?",
    "Should import validation reject unknown extra fields strictly, or allow warning-level tolerance for forward-compatible schema additions?",
    "Should SF-5's mutation hook interface be a simple in-process callback list, or should it use a lightweight event emitter pattern (e.g. Python `blinker`) to support multiple SF-7 subscribers without coupling to import order?"
  ],
  "requirements": [
    "REQ-1: Follow accepted repo topology — `tools/compose/backend`, `tools/compose/frontend`, `platform/toolshub/frontend`. No `tools/iriai-workflows`.",
    "REQ-2: Structured FastAPI service layout aligned with existing platform services.",
    "REQ-3: PostgreSQL + SQLAlchemy 2.x + Alembic; normalize `postgresql+psycopg://`; isolated `alembic_version_compose` table. SQLite excluded.",
    "REQ-4: Exactly five foundation tables: `workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`. No plugin, tools, phase-template, or `workflow_entity_refs` tables.",
    "REQ-5: `WorkflowVersion` required audit entity — atomic v1 on create/import/duplicate, append-only. Version writes do not trigger entity hooks.",
    "REQ-6: JWT Bearer auth via auth-service JWKS on all non-health endpoints; `dev_tier` exposed for gating.",
    "REQ-7: User-scoped soft-delete on all four mutable entity types; 404 not 403 for cross-user access; versions immutable.",
    "REQ-8: Standard platform error envelope, `GET /health`, `GET /ready` with DB check, explicit production CORS allow-list.",
    "REQ-9: Per-user rate limiting and structured JSON security logging; no raw YAML or prompt-body logging.",
    "REQ-10: Authenticated workflow CRUD, search, and cursor-paginated list/detail endpoints.",
    "REQ-11: Import endpoint is `POST /api/workflows/import` (collection-level create, not instance update). Starter templates are `user_id='__system__'` DB rows seeded by Alembic data migration from iriai-build-v2 — no filesystem asset serving. All-or-nothing import.",
    "REQ-12: Baseline authenticated CRUD/list for `roles`, `output_schemas`, `custom_task_templates`; advanced reference checks deferred to SF-7.",
    "REQ-13: `GET /api/schema/workflow` returns `WorkflowConfig.model_json_schema()`; `POST /api/workflows/{id}/validate` validates against same contract.",
    "REQ-14: Persisted/exported YAML uses nested `phases[].nodes`/`phases[].children`, edge-only hooks, no serialized `port_type`.",
    "REQ-15: Compose frontend at `tools/compose/frontend` as React 18 + TypeScript + Vite SPA with React Query, React Router, `@homelocal/auth`.",
    "REQ-16: Explorer-style shell with exactly four SF-5 sidebar folders: Workflows, Roles, Output Schemas, Task Templates.",
    "REQ-17: Tools hub at `platform/toolshub/frontend` as static authenticated SPA with hardcoded `dev_tier`-gated tool cards.",
    "REQ-18: In-process post-commit mutation hook interface on ALL FOUR foundation entity types — exactly four event kinds: `created`, `updated`, `soft_deleted`, `restored`. Exhaustive enumeration: `imported`→`created`, `version_saved` not hooked, `deleted` not a valid kind. SF-5 never creates `workflow_entity_refs` rows."
  ],
  "acceptance_criteria": [
    "AC-1: SF-5 implementation maps to `tools/compose/{backend,frontend}` and `platform/toolshub/frontend`; no `tools/iriai-workflows` dependency.",
    "AC-2: Initial Alembic chain uses PostgreSQL, tracks in `alembic_version_compose`, creates exactly five tables; no SQLite, plugin, or reference-index tables.",
    "AC-3: Opening tools hub and selecting Workflow Composer navigates same-tab to `compose.iriai.app` with authenticated shell.",
    "AC-4: Creating a workflow produces both the workflow row and `WorkflowVersion` v1 atomically.",
    "AC-5: `GET /api/schema/workflow` returns JSON Schema from `WorkflowConfig.model_json_schema()`, not a static file.",
    "AC-6: Editor boot requests `GET /api/schema/workflow` before rendering schema-dependent affordances.",
    "AC-7: Save/export uses nested phase containment and edge-only hook serialization with no serialized `port_type`.",
    "AC-8: `POST /api/workflows/import` with malformed YAML creates no workflow or version rows and returns parse errors. `POST /api/workflows/{id}/import` does not exist in SF-5.",
    "AC-9: Cross-user workflow access returns not-found, not a permission error.",
    "AC-10: SF-5 API surface includes workflow and baseline library CRUD only; no plugin, tools, or reference-index endpoints.",
    "AC-11: `GET /health` and `GET /ready` exist with DB check; production CORS is restricted to compose/toolshub origins.",
    "AC-12: Per-user API rate limit returns 429 with retry guidance and logs without raw YAML bodies.",
    "AC-13: SF-5 service layer exposes a typed mutation hook interface with exactly four event kinds (`created`, `updated`, `soft_deleted`, `restored`) on all four entity types called post-commit; no other event kinds exist; SF-5 contains no `workflow_entity_refs` row creation.",
    "AC-14: `GET /api/workflows/templates` returns system-seeded starter templates from `user_id='__system__'` DB rows; duplicating one creates a new user-owned workflow with version 1; no filesystem paths are served."
  ],
  "out_of_scope": [
    "Multi-user collaboration on workflow configs",
    "Runtime workflow execution inside the compose app or tools hub",
    "Reusing or extending `tools/iriai-workflows` as the canonical compose implementation path",
    "SQLite support or a SQLite-first local persistence contract",
    "Plugin registry UI, plugin tables, or `/api/plugins` endpoints",
    "Tool Library UI, custom tools table, or `/api/tools` endpoints",
    "`workflow_entity_refs` table creation, row materialization, or `GET /api/{entity}/references/{id}` in SF-5 — hook infrastructure is SF-5's responsibility; the reference index and its API belong to SF-7",
    "Version-history list, diff, or restore UI",
    "Phase template library pages",
    "Migration tooling from iriai-build v1",
    "Serving starter template content from filesystem paths at request time — all template content lives in DB rows seeded by Alembic data migration",
    "An instance-level import endpoint (`POST /api/workflows/{id}/import`) — if replace-from-import semantics are needed, that decision belongs to a future subfeature",
    "Mutation hook event kinds beyond the four canonical ones (`imported`, `version_saved`, `deleted`, etc.)"
  ],
  "complete": true
}

---

## Subfeature: Workflow Editor & Canvas (workflow-editor)

{
  "title": "PRD: Workflow Editor & Canvas (SF-6)",
  "overview": "Revised artifact written to `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md`. R11 keeps the SF-6 interaction model from the prior D-GR-22 rewrite, rebases the editor onto the accepted compose foundation contract, and aligns the cross-service impact section to the Cycle 5 ownership boundary: SF-5 is limited to exactly five foundation tables and exposes workflow mutation hooks (create/update/delete lifecycle events) for downstream consumers; SF-7 owns the `workflow_entity_refs` reference-index table and subscribes to those hooks — the editor never writes to or depends on the reference index directly. The editor lives in `tools/compose/frontend`, depends on `tools/compose/backend` FastAPI + PostgreSQL/Alembic services, and treats `/api/schema/workflow` plus the five SF-5 foundation tables as the only required foundation surfaces. The editor may keep a flat React Flow store internally, but save/load/export/import must normalize to the nested runtime contract.",
  "problem_statement": "The workflow editor is the core user-facing deliverable of the iriai-compose workflow creator. Without it, developers still have to author YAML directly, which defeats the purpose of visually translating and maintaining iriai-build-v2 planning, develop, and bugfix workflows. The editor also has to stay lossless against the actual iriai-compose runtime contract so that authored workflows can be validated, exported, and executed without hidden schema drift.\n\nThe remaining risk was foundation drift. Earlier SF-5/SF-6 artifacts still mixed in the placeholder `tools/iriai-workflows` topology, SQLite assumptions, plugin-management surfaces, and foundation-owned reference-index behavior that no longer match the accepted compose stack. SF-6 now treats core editing as a `tools/compose` concern only: boot, save, import, export, and validate depend on the five canonical SF-5 tables and schema/validation endpoints, while plugin registry and `workflow_entity_refs` expansion stay additive in SF-7.",
  "target_users": "Platform developers on hobby tier and above who build agent orchestration workflows and want a direct visual editor for prompts, roles, branches, phases, and lifecycle hooks without memorizing YAML field names.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "The editor must use a React Flow canvas as the primary editing surface with pan, zoom, fit-to-screen, and floating inspectors for graph authoring.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-2",
          "excerpt": "Left palette, center canvas, no YAML pane.",
          "reasoning": "Establishes the canvas as the primary authoring surface."
        },
        {
          "type": "decision",
          "reference": "D-3",
          "excerpt": "Floating XP windows for node/edge/phase inspectors.",
          "reasoning": "Defines the required inspection model around the canvas."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "The canvas must expose only three atomic node types for direct placement: Ask, Branch, and Plugin; iteration semantics must be expressed through phase modes rather than extra node kinds.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-7",
          "excerpt": "Three atomic node types only: Ask, Branch, Plugin.",
          "reasoning": "Locks the visible primitive set."
        },
        {
          "type": "decision",
          "reference": "D-8",
          "excerpt": "Phases carry execution modes: sequential, map, fold, loop.",
          "reasoning": "Moves iteration semantics into phases instead of separate node types."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "functional",
      "description": "Lifecycle hooks must be authored only as visible `on_start` and `on_end` ports on nodes and phases, with hook behavior serialized through normal edges rather than a separate hooks section.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Hooks are `on_start` / `on_end` ports on nodes and phases.",
          "reasoning": "Defines the user-facing hook model."
        },
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`.",
          "reasoning": "Locks the canonical serialization contract."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "Users must create phases with a selection-rectangle gesture, configure sequential/map/fold/loop mode in the phase inspector, and nest phases as needed for real workflows.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-9",
          "excerpt": "Paint-style selection rectangle creates phases.",
          "reasoning": "Defines the containment-creation interaction."
        },
        {
          "type": "decision",
          "reference": "D-25",
          "excerpt": "Phases can nest.",
          "reasoning": "Required for fold-with-inner-loop workflow patterns."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "All save, load, import, export, and validation flows must normalize to canonical nested YAML where top-level workflows own `phases[]`, each phase owns `nodes[]`, and nested phases live in `children[]`.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "YAML remains nested (`phases[].nodes`, `phases[].children`).",
          "reasoning": "Establishes the canonical runtime shape."
        },
        {
          "type": "decision",
          "reference": "D-33",
          "excerpt": "Serialization maps a flat React Flow store to nested YAML and back.",
          "reasoning": "Defines how the editor can remain flat internally while honoring the runtime contract."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "functional",
      "description": "Edge serialization must use only `source`, `target`, and optional `transform_fn`; hook-vs-data must be derived from source port resolution, and no serialized `port_type` field may appear in YAML.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-19",
          "excerpt": "Edge transforms are authored as inline Python and stored as `transform_fn`.",
          "reasoning": "Aligns the editor to the runtime edge field."
        },
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hook wiring remains edge-based with no serialized `port_type`.",
          "reasoning": "Removes stale serialization fields from the contract."
        }
      ]
    },
    {
      "id": "REQ-7",
      "category": "functional",
      "description": "The editor must fetch `GET /api/schema/workflow` on load and use it as the canonical runtime schema source for schema-driven validation and editor boot.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-34",
          "excerpt": "Runtime schema for the composer comes from `GET /api/schema/workflow`.",
          "reasoning": "Defines the canonical schema delivery path."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:36",
          "excerpt": "`GET /api/schema/workflow` returns `WorkflowConfig.model_json_schema()` from iriai-compose.",
          "reasoning": "Documents the backend endpoint that serves the runtime schema."
        }
      ]
    },
    {
      "id": "REQ-8",
      "category": "functional",
      "description": "Validation must have two tiers: client-side checks against the fetched runtime schema plus server-side deep validation through SF-2 `validate()` exposed by the workflow backend.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-28",
          "excerpt": "Server-side validation uses SF-2 `validate()`.",
          "reasoning": "Defines the deep-validation engine."
        },
        {
          "type": "decision",
          "reference": "D-29",
          "excerpt": "Two-tier validation: client-side fast checks plus server-side deep checks.",
          "reasoning": "Defines the validation architecture."
        }
      ]
    },
    {
      "id": "REQ-9",
      "category": "functional",
      "description": "The editor must preserve round-trip fidelity across save, export, import, and reload, including nested phases, hook edges, loop exits, and positions.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-33",
          "excerpt": "Serialization maps a flat React Flow store to nested YAML and back.",
          "reasoning": "Makes lossless round-trip a core editor responsibility."
        },
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Nested YAML containment and edge-based hook serialization are authoritative.",
          "reasoning": "Defines what must survive round-trip."
        }
      ]
    },
    {
      "id": "REQ-10",
      "category": "functional",
      "description": "Collapsed phases and templates must render as `CollapsedGroupCard` metadata cards with no mini-canvas thumbnail and no child-node React Flow rendering while collapsed.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-35",
          "excerpt": "Collapsed phases/templates use `CollapsedGroupCard` (260x52px), not mini-canvas thumbnails.",
          "reasoning": "Locks the collapsed rendering choice."
        }
      ]
    },
    {
      "id": "REQ-11",
      "category": "functional",
      "description": "The editor must support undo/redo for meaningful canvas mutations and auto-save after inactivity without interrupting active text or code editing.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-22",
          "excerpt": "No version-history UI in v1; auto-save every 30s.",
          "reasoning": "Defines save behavior."
        },
        {
          "type": "decision",
          "reference": "D-23",
          "excerpt": "Undo/redo stack depth is 50.",
          "reasoning": "Defines revision-stack expectations."
        }
      ]
    },
    {
      "id": "REQ-12",
      "category": "performance",
      "description": "Canvas interactions must remain responsive at 50+ visible nodes, with collapsed groups used as the primary rendering optimization.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-24",
          "excerpt": "Target responsive performance is 50+ visible nodes.",
          "reasoning": "Sets the scale target."
        },
        {
          "type": "decision",
          "reference": "D-35",
          "excerpt": "Collapsed phases/templates use `CollapsedGroupCard`.",
          "reasoning": "Provides the main performance lever for large graphs."
        }
      ]
    },
    {
      "id": "REQ-13",
      "category": "security",
      "description": "The editor must never execute inline Python locally; it stores `transform_fn` and other expressions as data, validates them structurally, and blocks editing when the canonical runtime schema endpoint is unavailable.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-34",
          "excerpt": "Runtime schema ... comes from `GET /api/schema/workflow`.",
          "reasoning": "Prevents fallback to stale or untrusted schema contracts."
        },
        {
          "type": "decision",
          "reference": "D-28",
          "excerpt": "Server-side validation uses SF-2 `validate()`.",
          "reasoning": "Keeps structural validation centralized without local code execution."
        }
      ]
    },
    {
      "id": "REQ-14",
      "category": "functional",
      "description": "The editor must ship inside `tools/compose/frontend` and boot/save against only the SF-5 compose foundation contract: workflow/version CRUD, roles, output schemas, custom task templates, `POST /validate`, and `GET /api/schema/workflow` backed by the five canonical PostgreSQL/Alembic tables. Core editor flows must not depend on `tools/iriai-workflows`, SQLite, `/api/plugins`, or `workflow_entity_refs` endpoints.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R1",
          "excerpt": "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables.",
          "reasoning": "Defines the canonical foundation topology the editor depends on."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R2",
          "excerpt": "Stale tools/iriai-workflows, SQLite, plugin-surface, and foundation-level workflow_entity_refs assumptions removed; reference-index expansion belongs to SF-7.",
          "reasoning": "Locks the SF-6 dependency boundary against stale artifacts."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "User opens `/workflows/:id/edit`.",
      "expected_observation": "The editor waits for both the workflow payload and `GET /api/schema/workflow` to succeed before rendering the working canvas.",
      "not_criteria": "The editor silently boots against a stale bundled schema or enters a partially usable state.",
      "requirement_ids": [
        "REQ-1",
        "REQ-7",
        "REQ-13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-34",
          "excerpt": "Runtime schema ... comes from `GET /api/schema/workflow`.",
          "reasoning": "Defines the required boot dependency."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "User drags an Ask node from the palette to the canvas.",
      "expected_observation": "An Ask node appears at the drop position with data ports and visible hook ports; placement mode ends after the drop.",
      "not_criteria": "Sticky placement mode persists or hook ports are missing.",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-3"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-6",
          "excerpt": "Drag-and-drop from palette is one-shot.",
          "reasoning": "Defines placement behavior."
        },
        {
          "type": "decision",
          "reference": "D-13",
          "excerpt": "Hooks are `on_start` / `on_end` ports on nodes and phases.",
          "reasoning": "Requires visible hook ports on the node."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "User draws from a node's `on_end` port to another node input and saves the workflow.",
      "expected_observation": "A dashed hook edge appears on canvas, and saved YAML contains only dot-notation `source` / `target` refs for that hook edge with no `port_type`.",
      "not_criteria": "A separate hooks block is emitted or YAML includes serialized `port_type`.",
      "requirement_ids": [
        "REQ-3",
        "REQ-6",
        "REQ-9"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "Hook wiring remains edge-based with no separate `hooks` section and no serialized `port_type`.",
          "reasoning": "Directly defines hook-edge serialization."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "User creates a phase with the selection rectangle and changes it to fold mode.",
      "expected_observation": "The selected nodes become phase children, fold config controls appear, and fold-only context variables become available inside child inspectors.",
      "not_criteria": "Fold requires a separate Fold node type or fold context leaks outside the phase.",
      "requirement_ids": [
        "REQ-4"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-9",
          "excerpt": "Paint-style selection rectangle creates phases.",
          "reasoning": "Defines the phase-creation gesture."
        },
        {
          "type": "decision",
          "reference": "D-8",
          "excerpt": "Phases carry execution modes: sequential, map, fold, loop.",
          "reasoning": "Defines mode configuration on the phase."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "User nests a loop phase inside another phase and sets `max_iterations=3`.",
      "expected_observation": "The inner phase stays inside the outer phase and exposes both `condition_met` and `max_exceeded` exits as distinct connections.",
      "not_criteria": "The loop shows only one exit or the inner phase escapes the parent boundary.",
      "requirement_ids": [
        "REQ-4"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-25",
          "excerpt": "Phases can nest.",
          "reasoning": "Required for nested containment behavior."
        },
        {
          "type": "decision",
          "reference": "D-27",
          "excerpt": "Loop phases expose `condition_met` and `max_exceeded` exits.",
          "reasoning": "Defines the expected loop exits."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "User clicks Validate.",
      "expected_observation": "Client-side issues are calculated against the fetched runtime schema and merged with server-side `validate()` results into one validation panel.",
      "not_criteria": "Validation uses only a static schema artifact or requires running the workflow.",
      "requirement_ids": [
        "REQ-7",
        "REQ-8",
        "REQ-13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-29",
          "excerpt": "Two-tier validation: client-side fast checks plus server-side deep checks.",
          "reasoning": "Defines the merged validation flow."
        },
        {
          "type": "decision",
          "reference": "D-34",
          "excerpt": "Runtime schema ... comes from `GET /api/schema/workflow`.",
          "reasoning": "Requires runtime schema-backed client validation."
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "User saves a workflow with loose top-level nodes, nested phases, and hook edges.",
      "expected_observation": "The saved YAML normalizes loose nodes under a synthetic root phase and preserves nested `children[]`, `nodes[]`, and hook edges as normal edges.",
      "not_criteria": "The saved YAML emits top-level nodes, flattens nesting, or loses hook-edge identity.",
      "requirement_ids": [
        "REQ-5",
        "REQ-6",
        "REQ-9"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-33",
          "excerpt": "Serialization maps a flat React Flow store to nested YAML and back.",
          "reasoning": "Defines the normalization behavior."
        },
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "YAML remains nested (`phases[].nodes`, `phases[].children`).",
          "reasoning": "Makes top-level-node output invalid."
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "User collapses several large phases and pans/zooms a 50+ node workflow.",
      "expected_observation": "Interactions stay responsive and collapsed groups render as lightweight metadata cards without child-node mounts.",
      "not_criteria": "Collapsed groups still render their children or visibly degrade interaction latency.",
      "requirement_ids": [
        "REQ-10",
        "REQ-12"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-35",
          "excerpt": "Collapsed phases/templates use `CollapsedGroupCard` ... not mini-canvas thumbnails.",
          "reasoning": "Defines the collapsed rendering."
        }
      ]
    },
    {
      "id": "AC-9",
      "user_action": "User performs multiple edits then presses Undo and Redo.",
      "expected_observation": "Changes revert and reapply in order, and open inspector state stays synchronized with canvas state.",
      "not_criteria": "Inspector state drifts from the reverted graph state or edit history truncates unexpectedly.",
      "requirement_ids": [
        "REQ-11"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-23",
          "excerpt": "Undo/redo stack depth is 50.",
          "reasoning": "Defines expected edit-history behavior."
        }
      ]
    },
    {
      "id": "AC-10",
      "user_action": "User opens the editor while `/api/schema/workflow` is unavailable.",
      "expected_observation": "A blocking error state appears and editing is deferred until the canonical schema endpoint recovers.",
      "not_criteria": "The app silently falls back to a local `workflow-schema.json` copy.",
      "requirement_ids": [
        "REQ-7",
        "REQ-13"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-22",
          "excerpt": "`/api/schema/workflow` is the canonical schema delivery path ... static `workflow-schema.json` is build/test only.",
          "reasoning": "Makes runtime fallback invalid."
        }
      ]
    },
    {
      "id": "AC-11",
      "user_action": "User opens an existing workflow in the compose editor before SF-7 plugin/reference endpoints are deployed.",
      "expected_observation": "The core canvas, save/validate flows, and role/schema/task-template affordances load from the compose foundation and remain usable.",
      "not_criteria": "Editor boot blocks on `/api/plugins`, `GET /api/{entity}/references/{id}`, SQLite-only assumptions, or a legacy `tools/iriai-workflows` app shell.",
      "requirement_ids": [
        "REQ-7",
        "REQ-8",
        "REQ-14"
      ],
      "citations": [
        {
          "type": "decision",
          "reference": "D-SF5-R1",
          "excerpt": "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables.",
          "reasoning": "Core editor must operate without SF-7 surfaces."
        },
        {
          "type": "decision",
          "reference": "D-SF5-R2",
          "excerpt": "Reference-index expansion belongs to SF-7.",
          "reasoning": "SF-7 surfaces are additive; blocking on them is invalid."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Build A Workflow From Scratch",
      "actor": "Platform developer with an authenticated workflow-editing session in the compose app",
      "preconditions": "A new or existing workflow is open in `tools/compose/frontend` and the runtime schema endpoint is healthy.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Open the editor page for a workflow.",
          "observes": "The canvas, palette, and toolbar render after the workflow record and `/api/schema/workflow` finish loading.",
          "not_criteria": "The editor boots against a stale local schema or renders an incomplete editing surface.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-34",
              "excerpt": "Runtime schema ... comes from `GET /api/schema/workflow`.",
              "reasoning": "Defines boot behavior."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Drag an Ask node to the canvas and open its inspector.",
          "observes": "The node appears with visible hook ports and the inspector opens near it with prompt and output-type editing fields.",
          "not_criteria": "Hooks are hidden from the node or only editable through hidden metadata.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-13",
              "excerpt": "Hooks are `on_start` / `on_end` ports on nodes and phases.",
              "reasoning": "Requires visible hook ports and edge-based authoring."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Add a Branch node, connect the Ask output, and create named paths.",
          "observes": "A typed data edge appears and Branch path handles update live on the node.",
          "not_criteria": "Branch routing requires a separate node type or delayed page refresh to show path ports.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-11",
              "excerpt": "Branch remains an explicit node.",
              "reasoning": "Keeps routing visible on the canvas."
            }
          ]
        },
        {
          "step_number": 4,
          "action": "Create a phase around several nodes, switch it to fold mode, validate, and save.",
          "observes": "The phase becomes a fold container, validation merges client/server results, and save persists canonical nested YAML.",
          "not_criteria": "Save flattens nesting, validation wipes the canvas, or YAML emits separate hook metadata.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "YAML remains nested ... hook wiring remains edge-based.",
              "reasoning": "Defines the saved contract."
            }
          ]
        }
      ],
      "outcome": "The user visually authors a valid workflow that round-trips to the canonical iriai-compose YAML contract.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-3",
        "REQ-4",
        "REQ-5",
        "REQ-6",
        "REQ-7",
        "REQ-8",
        "REQ-9"
      ]
    },
    {
      "id": "J-2",
      "name": "Build Nested Fold And Loop Phases",
      "actor": "Platform developer modeling per-subfeature iteration",
      "preconditions": "The workflow contains upstream data that can serve as a collection source.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Arrange several Ask, Branch, and Plugin nodes for a subfeature pipeline.",
          "observes": "The nodes remain loose on the canvas until explicitly grouped.",
          "not_criteria": "Nodes auto-group without a deliberate phase action.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-10",
              "excerpt": "Loose top-level nodes are allowed in the canvas UX.",
              "reasoning": "Defines pre-grouping behavior."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Create an outer phase and set it to fold mode.",
          "observes": "The outer phase exposes fold configuration and child nodes stay visible inside the phase.",
          "not_criteria": "Fold mode requires a standalone Fold node type or hides child nodes unexpectedly.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-8",
              "excerpt": "Phases carry execution modes: sequential, map, fold, loop.",
              "reasoning": "Defines fold as a phase mode."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Create a nested inner phase and set it to loop mode with `max_iterations`.",
          "observes": "The inner phase remains nested and shows distinct `condition_met` and `max_exceeded` exits.",
          "not_criteria": "The loop has only one exit or the inner phase breaks parent containment.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-27",
              "excerpt": "Loop phases expose `condition_met` and `max_exceeded` exits.",
              "reasoning": "Defines loop exits."
            }
          ]
        }
      ],
      "outcome": "The user represents nested fold-with-inner-loop behavior directly in the canvas and saves it losslessly to nested YAML.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-4",
        "REQ-5",
        "REQ-9"
      ]
    },
    {
      "id": "J-3",
      "name": "Import Malformed YAML",
      "actor": "Platform developer importing an external workflow file",
      "preconditions": "An existing workflow is open in the editor.",
      "path_type": "failure",
      "failure_trigger": "The selected YAML file contains syntax errors or parse failures.",
      "steps": [
        {
          "step_number": 1,
          "action": "Choose File -> Import and select a YAML file.",
          "observes": "The editor asks for confirmation before replacing the current canvas.",
          "not_criteria": "The existing canvas changes before confirmation.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-29",
              "excerpt": "Two-tier validation ...",
              "reasoning": "The editor must distinguish parse failure from structural validation after import."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Confirm the import and hit a parse error.",
          "observes": "The editor shows a red error toast with a line-numbered parse message and leaves the current canvas untouched.",
          "not_criteria": "The import partially mutates the existing workflow or loses the prior state.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-29",
              "excerpt": "Two-tier validation: client-side fast checks plus server-side deep checks.",
              "reasoning": "Supports safe handling of malformed input before structural validation."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Retry with parseable but structurally invalid YAML.",
          "observes": "The workflow loads with explicit validation warnings in the validation panel.",
          "not_criteria": "Warnings are silently swallowed or invalid content is treated as fully clean.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-28",
              "excerpt": "Server-side validation uses SF-2 `validate()`.",
              "reasoning": "Structural issues should still surface after parse succeeds."
            }
          ]
        }
      ],
      "outcome": "Malformed YAML is rejected safely; parseable-but-invalid YAML stays repairable inside the editor.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-8",
        "REQ-9"
      ]
    },
    {
      "id": "J-4",
      "name": "Schema Endpoint Unavailable On Editor Load",
      "actor": "Platform developer opening a workflow",
      "preconditions": "The workflow API is reachable but `/api/schema/workflow` is failing or timing out.",
      "path_type": "failure",
      "failure_trigger": "The canonical runtime schema cannot be fetched during editor boot.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open `/workflows/:id/edit`.",
          "observes": "The editor shows a blocking error state explaining that the runtime schema could not be loaded.",
          "not_criteria": "The editor silently falls back to a stale local schema copy or permits edits against an unknown contract.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-22",
              "excerpt": "`/api/schema/workflow` is the canonical schema delivery path ... static `workflow-schema.json` is build/test only.",
              "reasoning": "Defines the failure behavior and invalid fallback."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Retry after the schema endpoint recovers.",
          "observes": "The editor loads normally and the workflow opens.",
          "not_criteria": "The failure is cached permanently after the endpoint is healthy again.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-34",
              "excerpt": "Runtime schema for the composer comes from `GET /api/schema/workflow`.",
              "reasoning": "Boot should recover when the canonical endpoint is healthy again."
            }
          ]
        }
      ],
      "outcome": "The user never edits against a stale or untrusted schema contract.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-7",
        "REQ-13"
      ]
    },
    {
      "id": "J-5",
      "name": "Core Editor Works On The Five-table Compose Foundation",
      "actor": "Platform developer editing workflows during a staged compose rollout",
      "preconditions": "`tools/compose/frontend` and `tools/compose/backend` are deployed with the SF-5 foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), while SF-7 plugin/reference-index endpoints are not yet live.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Open `/workflows/:id/edit` in the compose app.",
          "observes": "The editor boots from the workflow payload plus `/api/schema/workflow` and shows the core canvas.",
          "not_criteria": "Boot attempts to route through `tools/iriai-workflows` or blocks on optional plugin/reference APIs.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R1",
              "excerpt": "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables.",
              "reasoning": "Defines the foundation topology the editor depends on."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Open Ask or template inspectors that use library-backed pickers.",
          "observes": "Roles, output schemas, and task templates load from the compose foundation and remain usable.",
          "not_criteria": "Picker rendering requires plugin-management tables or `workflow_entity_refs` data.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R2",
              "excerpt": "Reference-index expansion belongs to SF-7.",
              "reasoning": "SF-7 surfaces are not a boot dependency."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Save the current workflow.",
          "observes": "Save and validation succeed through the workflow/version endpoints while preserving the nested YAML contract.",
          "not_criteria": "Save requires `/api/plugins`, reference-index endpoints, or any SQLite-local persistence path.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R2",
              "excerpt": "Stale SQLite, plugin-surface, and foundation-level workflow_entity_refs assumptions removed.",
              "reasoning": "Save path must not depend on stale infrastructure."
            }
          ]
        }
      ],
      "outcome": "Core editor flows remain usable on the accepted five-table compose foundation, with SF-7 expansion kept additive.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-7",
        "REQ-8",
        "REQ-14"
      ]
    },
    {
      "id": "J-6",
      "name": "Optional Library Expansion Is Not Yet Available",
      "actor": "Platform developer editing workflows on the compose foundation",
      "preconditions": "The editor is open and optional SF-7 plugin or reference-check surfaces are not deployed.",
      "path_type": "failure",
      "failure_trigger": "The user invokes an affordance that belongs to the later SF-7 library/reference expansion.",
      "steps": [
        {
          "step_number": 1,
          "action": "Click an optional plugin-library or reference-check affordance from the editor chrome.",
          "observes": "The control is disabled or shows a non-blocking unavailable/coming-soon message.",
          "not_criteria": "The editor crashes, shows a blank screen, or forces the user out of the current workflow.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R2",
              "excerpt": "Reference-index expansion belongs to SF-7.",
              "reasoning": "SF-7 affordances must degrade gracefully."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Continue editing and save the workflow.",
          "observes": "The current canvas state stays intact and core save succeeds through the compose foundation endpoints.",
          "not_criteria": "Unsaved edits are lost or save is blocked because optional SF-7 surfaces are missing.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-SF5-R1",
              "excerpt": "SF-5 rebased to tools/compose + PostgreSQL + exactly five foundation tables.",
              "reasoning": "Core save path is independent of SF-7."
            }
          ]
        }
      ],
      "outcome": "Missing SF-7 surfaces degrade gracefully without blocking core workflow editing.",
      "related_journey_id": "J-5",
      "requirement_ids": [
        "REQ-14"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "None specific to the editor beyond inherited platform controls.",
    "data_sensitivity": "Internal workflow definitions may contain proprietary prompts, role definitions, and process logic.",
    "pii_handling": "No workflow-specific PII is expected; authenticated user identity is used only for scoping and ownership.",
    "auth_requirements": "Standard JWT-based authenticated session via auth-react and backend auth enforcement.",
    "data_retention": "Workflow saves persist under the compose backend's `workflows` / `workflow_versions` retention behavior until deleted.",
    "third_party_exposure": "Users may export YAML externally; the editor must not embed secrets.",
    "data_residency": "Inherits `tools/compose/backend` Railway deployment and PostgreSQL/Alembic storage policy.",
    "risk_mitigation_notes": "The editor stores inline Python as data and does not execute it locally. Structural validation stays centralized through runtime schema fetch and backend `validate()`. If the canonical schema endpoint is unavailable, editing is blocked rather than falling back to a stale local schema. Core editor boot/save must stay within the accepted five-table compose foundation. Workflow mutation hooks (fired by SF-5 on create/update/delete) drive reference-index synchronization in SF-7 downstream; the editor has no write dependency on `workflow_entity_refs` and plugin/reference-index surfaces remain optional SF-7 additions."
  },
  "data_entities": [
    {
      "name": "WorkflowRecord",
      "fields": [
        "id",
        "name",
        "yaml_content",
        "current_version",
        "user_id",
        "created_at",
        "updated_at"
      ],
      "constraints": [
        "Persisted in the compose backend `workflows` table",
        "Version snapshots live in `workflow_versions`",
        "Core editor boot/save must not require `workflow_entity_refs`"
      ],
      "is_new": false
    },
    {
      "name": "WorkflowConfig",
      "fields": [
        "schema_version",
        "name",
        "description",
        "actors",
        "types",
        "phases",
        "edges",
        "plugins",
        "plugin_instances",
        "stores",
        "context_keys",
        "context_text"
      ],
      "constraints": [
        "No top-level serialized nodes collection",
        "Top-level graph structure is rooted in `phases[]`",
        "Cross-phase edges live at workflow root"
      ],
      "is_new": false
    },
    {
      "name": "PhaseDefinition",
      "fields": [
        "id",
        "mode",
        "sequential_config",
        "map_config",
        "fold_config",
        "loop_config",
        "nodes",
        "edges",
        "children",
        "inputs",
        "outputs",
        "position"
      ],
      "constraints": [
        "Nested containment must use `children[]`",
        "Loop phases expose `condition_met` and `max_exceeded` exits",
        "Each phase owns its internal nodes and edges"
      ],
      "is_new": false
    },
    {
      "name": "Edge",
      "fields": [
        "source",
        "target",
        "transform_fn",
        "description"
      ],
      "constraints": [
        "Hook-vs-data is inferred from source port resolution",
        "Hook edges must not carry `transform_fn`",
        "No serialized `port_type` field"
      ],
      "is_new": false
    },
    {
      "name": "WorkflowEditorState",
      "fields": [
        "graph.nodes",
        "graph.edges",
        "schema.workflowSchema",
        "ui.openInspectors",
        "ui.selectionRect",
        "ui.collapsedGroups",
        "ui.validationIssues",
        "undoStack",
        "redoStack"
      ],
      "constraints": [
        "Internal state may stay flat for React Flow",
        "Serialization must normalize to nested YAML `phases[].nodes` / `phases[].children`",
        "Hook-vs-data may be derived internally but not serialized",
        "Core editor boot depends only on the five-table compose foundation endpoints; optional SF-7 plugin/reference-index surfaces remain non-blocking and are never a save dependency"
      ],
      "is_new": true
    }
  ],
  "cross_service_impacts": [
    {
      "service": "SF-1 Declarative Schema",
      "impact": "SF-6 now explicitly treats `phases[].nodes` / `phases[].children` as canonical and expects hook wiring to remain edge-only.",
      "action_needed": "Ensure SF-1 PRD/design/plan/system-design consistently use `children[]` and never describe a separate hooks section or serialized `port_type`."
    },
    {
      "service": "SF-2 DAG Loader & Runner",
      "impact": "SF-6 depends on the loader and validator consuming the same nested structure and inferring hook edges from port resolution.",
      "action_needed": "Keep SF-2 validation and graph-build logic aligned to edge-only hook serialization and `transform_fn=None` for hook edges."
    },
    {
      "service": "compose-frontend (tools/compose/frontend)",
      "impact": "SF-6 is mounted in the accepted compose SPA rather than a legacy `tools/iriai-workflows` shell.",
      "action_needed": "Keep routing, auth providers, and editor bootstrap inside `tools/compose/frontend`."
    },
    {
      "service": "compose-backend (tools/compose/backend)",
      "impact": "SF-6 depends on workflow/version CRUD, roles, output schemas, custom task templates, validation, and `/api/schema/workflow` backed by PostgreSQL/Alembic and exactly five SF-5 foundation tables. SF-5 also fires workflow mutation hooks (create/update/delete lifecycle events) that downstream consumers can subscribe to; the editor itself does not subscribe to or depend on those hooks.",
      "action_needed": "Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose mutation hooks for SF-7 reference-index refresh; do not make `/api/plugins`, `workflow_entity_refs`, or reference-index endpoints a prerequisite for core editor boot/save."
    },
    {
      "service": "SF-7 Libraries & Registries",
      "impact": "SF-7 owns the `workflow_entity_refs` reference-index table and `GET /api/{entity}/references/{id}` endpoint as a downstream extension of SF-5. SF-7 subscribes to SF-5 workflow mutation hooks to keep the reference index synchronized after editor save/create/delete flows; the editor's save path flows through SF-5 endpoints only and is unaware of the reference refresh.",
      "action_needed": "SF-7 must own all `workflow_entity_refs` schema and sync logic; plugin registry surfaces and reference-check affordances must remain additive and non-blocking for the core editor; templates and optional affordances must preserve `children[]` plus edge-based hook wiring without becoming a boot dependency."
    }
  ],
  "open_questions": [],
  "requirements": [],
  "acceptance_criteria": [],
  "out_of_scope": [
    "YAML side pane and live bidirectional YAML editing",
    "Version-history browsing UI inside the editor",
    "Visual JSON Schema builder",
    "Named transform registry UI or transform picker",
    "Runtime workflow execution inside the editor",
    "Collaborative multi-user editing",
    "Separate serialized hooks section",
    "Serialized `port_type` field",
    "Runtime fallback to static `workflow-schema.json`",
    "MiniCanvasThumbnail / CMP-64",
    "`tools/iriai-workflows` as the editor deployment shell",
    "SQLite as a runtime persistence dependency for compose editor flows",
    "Core-editor boot dependency on `/api/plugins` or `GET /api/{entity}/references/{id}`",
    "Foundation-owned `workflow_entity_refs` expansion"
  ],
  "complete": true
}

---

## Subfeature: Libraries & Registries (libraries-registries)

{
  "title": "Libraries & Registries (SF-7)",
  "overview": "Revision R14 (Cycle 5). SF-7 is rebased onto the accepted `tools/compose` + PostgreSQL/Alembic compose foundation. `workflow_entity_refs` ownership moves entirely into SF-7 as a follow-on extension; SF-5 stays limited to exactly five foundation tables and exposes only workflow mutation hooks SF-7 uses to refresh the reference index. Stale `tools/iriai-workflows`, SQLite, plugin-library, and foundation-level reference-index assumptions are removed. Active SF-7 scope: role/schema/template delete preflight backed by the SF-7 reference index, full Tool CRUD library, `actor_slots` persistence on `custom_task_templates`, JWT auth/validation guardrails, and no plugin library surfaces.",
  "problem_statement": "SF-7 gives the compose app reusable libraries for roles, output schemas, task templates, and tools. Those libraries need accurate pre-delete reference checks, reusable tool registration, and persisted task-template actor slots, but they must not re-open the already-accepted SF-5 foundation contract.\n\nThe stale artifact (R13) still treated reference indexing as a foundation concern and inherited older topology and scope assumptions — specifically, SF-5 was described as the owner of the `workflow_entity_refs` table even though the broader architecture had already fixed SF-5 at five tables. This revision resets the contract: SF-5 stays a five-table `tools/compose` PostgreSQL/Alembic foundation that exposes workflow mutation hooks, while SF-7 owns the follow-on library extensions that add `workflow_entity_refs`, the `tools` table, role-backed tool delete checks, and `actor_slots` persistence on top of that base.",
  "target_users": "Platform developers on hobby tier and above who build workflows inside compose, manage reusable roles/schemas/templates/tools, and need accurate reference visibility before deleting shared library items.",
  "structured_requirements": [
    {
      "id": "REQ-1",
      "category": "functional",
      "description": "SF-7 must extend the accepted compose topology: library surfaces live inside the compose app backed by `tools/compose` frontend/backend and PostgreSQL + Alembic, not `tools/iriai-workflows` or SQLite.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-27",
          "excerpt": "`tools/compose` is accepted; `tools/iriai-workflows` is rejected.",
          "reasoning": "The revision must inherit the accepted topology rather than preserve stale paths."
        },
        {
          "type": "decision",
          "reference": "D-GR-28",
          "excerpt": "PostgreSQL + SQLAlchemy + Alembic remains canonical.",
          "reasoning": "This fixes the stale SQLite assumption."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/integration-review-sources-plan.md:6170",
          "excerpt": "`tools/compose/frontend`, `tools/compose/backend`, `tools/iriai-workflows` NOT used.",
          "reasoning": "The repo-wide accepted topology is recorded in the feature plan."
        }
      ]
    },
    {
      "id": "REQ-2",
      "category": "functional",
      "description": "Roles, Schemas, and Task Templates must use a pre-delete reference check backed by `workflow_entity_refs`, introduced as an SF-7-owned follow-on PostgreSQL/Alembic extension; SF-5 remains limited to exactly five foundation tables and only exposes the workflow mutation hooks SF-7 needs to refresh the index.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-29",
          "excerpt": "SF-5 stays at five tables; `workflow_entity_refs` moves to SF-7 scope.",
          "reasoning": "This is the core ownership change requested in the Cycle 5 feedback."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:22",
          "excerpt": "Create exactly 5 SF-5 tables.",
          "reasoning": "The foundation contract leaves no room for foundation-owned reference-index tables."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-5.md:24",
          "excerpt": "SF-7 should own the `workflow_entity_refs` reference-index extension.",
          "reasoning": "The accepted Cycle 5 guidance explicitly moves ownership into SF-7."
        }
      ]
    },
    {
      "id": "REQ-3",
      "category": "functional",
      "description": "SF-7 delete UX for Roles, Schemas, and Task Templates must be non-destructive: `EntityDeleteDialog` and `useReferenceCheck` call `GET /api/{entity}/references/{id}` before any DELETE request, and the backend must not parse workflow YAML on demand for that lookup.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-26",
          "excerpt": "`workflow_entity_refs` backs `GET /api/{entity}/references/{id}`.",
          "reasoning": "This is the canonical delete-preflight contract."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26",
          "excerpt": "`useReferenceCheck` calls the references endpoint before delete.",
          "reasoning": "The SF-7 interaction design already encodes the desired UX."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:52",
          "excerpt": "Remove YAML-scan delete helpers in favor of indexed reference checks.",
          "reasoning": "The plan language matches the requested revision."
        }
      ]
    },
    {
      "id": "REQ-4",
      "category": "functional",
      "description": "The Tool Library remains a full CRUD library page with list, detail, and editor views; registered tools populate the Role editor tool checklist via `GET /api/tools`, and tool delete protection remains role-backed rather than `workflow_entity_refs`-backed.",
      "priority": "must",
      "citations": [
        {
          "type": "decision",
          "reference": "D-GR-7",
          "excerpt": "Tool Library restored with full CRUD and role integration.",
          "reasoning": "Tool CRUD remains active scope after the rebase."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:106",
          "excerpt": "/tools route, Tool entity CRUD, role editor integration.",
          "reasoning": "The review history records the accepted tool-library scope."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13",
          "excerpt": "`Role.tools` is `list[str]`.",
          "reasoning": "Tool delete checks still branch on persisted role arrays rather than workflow refs."
        }
      ]
    },
    {
      "id": "REQ-5",
      "category": "functional",
      "description": "`custom_task_templates` must persist `actor_slots` through a follow-on Alembic migration and API support so task template actor-slot definitions survive reloads and remain reusable across workflows.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:108",
          "excerpt": "Alembic migration for `actor_slots` is an implementation prerequisite.",
          "reasoning": "The revision must keep actor-slot persistence explicit."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143",
          "excerpt": "SF-7 adds actor_slots to CustomTaskTemplate.",
          "reasoning": "The cross-subfeature review confirms this is an SF-7 extension, not SF-5 foundation scope."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199",
          "excerpt": "Without the migration, actor slot definitions are lost on reload.",
          "reasoning": "This captures the concrete failure the requirement prevents."
        }
      ]
    },
    {
      "id": "REQ-6",
      "category": "non-functional",
      "description": "Library pages must feel immediate: warm-cache list pages load within 500ms, cold fetches within 2 seconds, and data access uses stale-while-revalidate query behavior.",
      "priority": "should",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710",
          "excerpt": "Warm-cache within 500ms; cold fetches within 2 seconds.",
          "reasoning": "These are the established SF-7 responsiveness targets."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/prd_sf7_and_merged.md:271",
          "excerpt": "Use stale-while-revalidate query behavior for library APIs.",
          "reasoning": "The prior merged PRD already fixed the desired caching model."
        }
      ]
    },
    {
      "id": "REQ-7",
      "category": "security",
      "description": "All library API endpoints require JWT Bearer auth, scope data to the authenticated user, and return 404 rather than 403 for cross-user access attempts.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24",
          "excerpt": "Scope all resource access by authenticated `user_id`; return `404` for other users.",
          "reasoning": "SF-7 inherits compose foundation tenancy controls."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:25",
          "excerpt": "JWT auth on all non-health endpoints.",
          "reasoning": "Library APIs stay behind the same compose auth boundary."
        }
      ]
    },
    {
      "id": "REQ-8",
      "category": "security",
      "description": "Server-side validation must enforce JSON payload size limits and entity-name sanitization across library entities, with clear 413/422 responses and matching frontend guards.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:110",
          "excerpt": "256KB JSON payload size limits.",
          "reasoning": "The review history keeps payload limits as required SF-7 scope."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-discussion-2.md:111",
          "excerpt": "Name sanitization regex on the server and frontend.",
          "reasoning": "Entity naming rules remain part of the accepted SF-7 guardrails."
        },
        {
          "type": "research",
          "reference": "OWASP Input Validation Cheat Sheet",
          "excerpt": "Apply server-side allowlist validation with length limits as early as possible.",
          "reasoning": "This supports rejecting malformed or oversized library payloads before persistence."
        }
      ]
    },
    {
      "id": "REQ-9",
      "category": "functional",
      "description": "SF-7 library scope remains limited to Roles, Output Schemas, Task Templates, and Tools inside compose; do not restore Plugins Library pages, plugin endpoints, or PluginPicker surfaces.",
      "priority": "must",
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:198",
          "excerpt": "Plugin surfaces must be removed rather than restored.",
          "reasoning": "The review explicitly called stale plugin surfaces the largest SF-7 contradiction."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213",
          "excerpt": "Do not create a PluginPicker.",
          "reasoning": "The current SF-7 plan already narrows picker scope to non-plugin library entities."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:40",
          "excerpt": "Exclude plugin-management entities and `/api/plugins` from SF-5.",
          "reasoning": "The foundation contract also rejects plugin-management database/API surfaces."
        }
      ]
    }
  ],
  "structured_acceptance_criteria": [
    {
      "id": "AC-1",
      "user_action": "A user tries to delete a role that is still referenced by a saved workflow, then removes that reference in the workflow and saves again.",
      "expected_observation": "Delete is blocked before any DELETE call with the referencing workflow list; after the workflow save, reopening delete shows the normal confirmation with no stale workflow names.",
      "not_criteria": "The user must not have to issue a DELETE request just to discover references, and stale reference rows must not remain after the saved workflow changes clear them.",
      "requirement_ids": [
        "REQ-2",
        "REQ-3"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26",
          "excerpt": "Delete preflight starts with the references endpoint.",
          "reasoning": "This is the intended user-visible flow for referenced roles."
        },
        {
          "type": "decision",
          "reference": "D-GR-29",
          "excerpt": "Reference-index ownership moves into SF-7 follow-on scope.",
          "reasoning": "The acceptance test must validate the rebased ownership model."
        }
      ]
    },
    {
      "id": "AC-2",
      "user_action": "An engineer inspects the initial SF-5 migration and the first SF-7 extension migrations for compose.",
      "expected_observation": "SF-5 creates exactly five foundation tables (`workflows`, `workflow_versions`, `roles`, `output_schemas`, `custom_task_templates`), while SF-7 follow-on Alembic revisions add `workflow_entity_refs`, the `tools` table, and the `actor_slots` column on `custom_task_templates` inside the compose PostgreSQL backend.",
      "not_criteria": "The foundation migration must not create `workflow_entity_refs`, `tools`, plugin tables, or SQLite-specific persistence, and the extension work must not target `tools/iriai-workflows`.",
      "requirement_ids": [
        "REQ-1",
        "REQ-2",
        "REQ-5"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:58",
          "excerpt": "Exactly 5 tables exist in the SF-5 foundation migration.",
          "reasoning": "This anchors the inspection criterion for the foundation layer."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/reviews/pm.md:143",
          "excerpt": "SF-7 adds a Tool entity and actor_slots.",
          "reasoning": "The extension inspection must confirm these stay in SF-7 scope."
        }
      ]
    },
    {
      "id": "AC-3",
      "user_action": "A user creates a task template with actor slots, saves it, refreshes the page, and reopens the template.",
      "expected_observation": "Actor slots are fully persisted with names, type constraints, and default bindings, and the API returns `actor_slots` on reload.",
      "not_criteria": "Actor slots must not exist only in frontend state or disappear on reload.",
      "requirement_ids": [
        "REQ-5"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199",
          "excerpt": "Without the migration, actor slots are lost on page reload.",
          "reasoning": "This is the direct user-facing acceptance condition for the fix."
        }
      ]
    },
    {
      "id": "AC-4",
      "user_action": "A user edits a custom tool and then opens a Role editor that references it.",
      "expected_observation": "The tool detail view updates, and the Role editor checklist shows the updated tool metadata after query invalidation.",
      "not_criteria": "Editing must not create a second tool record or leave stale tool metadata in the Role editor.",
      "requirement_ids": [
        "REQ-4"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:131",
          "excerpt": "Tool Library keeps list, detail, and editor flows.",
          "reasoning": "The updated tool must propagate across that whole flow."
        }
      ]
    },
    {
      "id": "AC-5",
      "user_action": "A user tries to delete a custom tool that is still referenced by roles.",
      "expected_observation": "Delete is blocked with the referencing role names; after removing those role references, the standard delete confirmation appears and the tool disappears from Role editor checklists.",
      "not_criteria": "The tool must not be deleted while still referenced, and deleted tools must not remain selectable.",
      "requirement_ids": [
        "REQ-4"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42",
          "excerpt": "Tool delete checks role usage, not workflow refs.",
          "reasoning": "This is the intended blocking contract for tool deletion."
        }
      ]
    },
    {
      "id": "AC-6",
      "user_action": "A user submits oversized JSON or an invalid entity name through the UI or API.",
      "expected_observation": "The server rejects the request with the documented 413 or 422 validation errors and no record is created or updated.",
      "not_criteria": "Validation must not exist only in the frontend, and malformed or oversized payloads must not be stored.",
      "requirement_ids": [
        "REQ-8"
      ],
      "citations": [
        {
          "type": "research",
          "reference": "OWASP Input Validation Cheat Sheet",
          "excerpt": "Server-side validation must happen before processing untrusted input.",
          "reasoning": "This supports the rejection behavior for malformed library payloads."
        }
      ]
    },
    {
      "id": "AC-7",
      "user_action": "A user opens the compose library sidebar and library-selection pickers in the editor.",
      "expected_observation": "The available library surfaces are Roles, Output Schemas, Task Templates, and Tools, with no Plugins page and no PluginPicker affordance.",
      "not_criteria": "A Plugins library, plugin endpoint affordance, or PluginPicker must not reappear in the rebased SF-7 surface.",
      "requirement_ids": [
        "REQ-9"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205",
          "excerpt": "Pickers are RolePicker, SchemaPicker, and TemplateBrowser.",
          "reasoning": "The picker surface already excludes plugins in the revised plan."
        },
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:213",
          "excerpt": "Do not create a PluginPicker.",
          "reasoning": "This is the concrete artifact-level guardrail."
        }
      ]
    },
    {
      "id": "AC-8",
      "user_action": "User A attempts to access User B's role, schema, template, or tool by direct API or deep link.",
      "expected_observation": "The request resolves as not found, and no foreign resource metadata is revealed.",
      "not_criteria": "The API must not return 403 or otherwise confirm that the other user's library item exists.",
      "requirement_ids": [
        "REQ-7"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/composer-app-foundation/prd.md:24",
          "excerpt": "Return `404` for other users' records.",
          "reasoning": "SF-7 inherits the compose tenancy boundary."
        }
      ]
    },
    {
      "id": "AC-9",
      "user_action": "A user opens a library list, then revisits it in the same session after the initial load.",
      "expected_observation": "The cached list renders within the warm-cache 500ms target and background refresh does not block interaction; a cold visit still resolves within the 2-second target.",
      "not_criteria": "The user must not sit behind a spinner beyond the cold-load target, and cached revisits must not feel like full reloads.",
      "requirement_ids": [
        "REQ-6"
      ],
      "citations": [
        {
          "type": "code",
          "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/compile-sources-prd.md:6710",
          "excerpt": "Warm-cache 500ms; cold fetches 2 seconds.",
          "reasoning": "This directly defines the page-load acceptance thresholds."
        }
      ]
    }
  ],
  "journeys": [
    {
      "id": "J-1",
      "name": "Create and Use a Role from the Roles Library",
      "actor": "Platform developer with at least one saved workflow in compose",
      "preconditions": "Authenticated user in the compose app; Roles Library is accessible from the rebased compose shell.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Open the Roles Library inside compose.",
          "observes": "The list view loads with existing role cards, search, and a primary New Role action.",
          "not_criteria": "Other users' roles are not visible, and the user does not land in a stale `tools/iriai-workflows` surface.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:90",
              "excerpt": "Keep the Roles library in scope.",
              "reasoning": "This is the current SF-7 entry point for role management."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Create a new role with a name, model, prompt, and selected tools.",
          "observes": "The Role editor accepts the values and shows built-in and registered tools as selectable groups.",
          "not_criteria": "Registered tools are not missing, and invalid names are not accepted.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13",
              "excerpt": "`Role.tools` stores string identifiers.",
              "reasoning": "The role editor must keep presenting tools in the persisted format compose expects."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Save the role and select it from the Ask-node role picker in a workflow.",
          "observes": "The role appears in the library and becomes selectable from RolePicker in the workflow editor.",
          "not_criteria": "Duplicate role rows are not created, and the picker does not require delete-preflight data just to list roles.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:205",
              "excerpt": "RolePicker loads standard library data.",
              "reasoning": "This is the expected integration path between SF-7 and SF-6."
            }
          ]
        }
      ],
      "outcome": "A reusable role exists in the compose library and can be attached to saved workflow content.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-4",
        "REQ-7",
        "REQ-9"
      ]
    },
    {
      "id": "J-2",
      "name": "Delete a Role Referenced by Saved Workflows",
      "actor": "Platform developer cleaning up an unused role",
      "preconditions": "A saved workflow currently references the role through persisted library data.",
      "path_type": "failure",
      "failure_trigger": "The user initiates delete on a role that is still referenced by saved workflow content.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open delete for the referenced role.",
          "observes": "A blocking dialog appears before any destructive request, listing the referencing workflows from the SF-7 reference index.",
          "not_criteria": "The role is not deleted, and the system does not parse workflow YAML or require a DELETE attempt just to discover references.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:26",
              "excerpt": "Delete opens in a checking state backed by the references endpoint.",
              "reasoning": "This defines the non-destructive preflight experience."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Remove the role reference in the workflow editor and save the workflow.",
          "observes": "The workflow save succeeds and the role's reference status updates on the next delete preflight.",
          "not_criteria": "Unsaved editor changes are not treated as cleared references, and stale workflow names do not persist after the saved change.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:12",
              "excerpt": "Only saved workflow changes count as persisted references.",
              "reasoning": "The delete flow must stay tied to persisted workflow state."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Retry delete after all saved references are removed.",
          "observes": "The standard delete confirmation appears and the user can safely remove the role.",
          "not_criteria": "The reference list is not stale, and the role is not blocked by a foundation-owned table that SF-5 was never supposed to create.",
          "citations": [
            {
              "type": "decision",
              "reference": "D-GR-29",
              "excerpt": "The reference table belongs to SF-7 follow-on scope.",
              "reasoning": "The retried delete must validate against the rebased ownership model."
            }
          ]
        }
      ],
      "outcome": "The user understands why deletion was blocked, clears the saved references safely, and then deletes the role without stale index data.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-2",
        "REQ-3"
      ]
    },
    {
      "id": "J-3",
      "name": "Delete a Tool Referenced by Roles",
      "actor": "Platform developer attempting to remove a custom tool",
      "preconditions": "The custom tool is still referenced by one or more saved roles.",
      "path_type": "failure",
      "failure_trigger": "The user initiates delete on a tool that is still referenced by role `tools` arrays.",
      "steps": [
        {
          "step_number": 1,
          "action": "Open delete for the referenced tool.",
          "observes": "A blocking dialog lists the referencing roles and offers only Close until the role references are removed.",
          "not_criteria": "The tool is not deleted, and the dialog does not show workflow names or `workflow_entity_refs` validation codes.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:42",
              "excerpt": "Tool deletion keeps a distinct role-reference contract.",
              "reasoning": "This is the expected failure mode for referenced tools."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Remove the tool from the referencing roles and save those roles.",
          "observes": "The roles save successfully with updated `tools` arrays.",
          "not_criteria": "Other tool selections are not corrupted, and the saved role shape does not switch from string identifiers to a new ID model.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/iriai-compose/iriai_compose/actors.py:13",
              "excerpt": "`tools` remains a list of strings.",
              "reasoning": "Tool removal must preserve the existing role storage model."
            }
          ]
        },
        {
          "step_number": 3,
          "action": "Retry tool delete after the role saves complete.",
          "observes": "The normal delete confirmation appears and the deleted tool disappears from later Role editor checklists.",
          "not_criteria": "The tool is not deleted while still referenced, and deleted tools do not remain selectable in the role editor.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/design-decisions.md:43",
              "excerpt": "Retry delete reruns a fresh role reference check.",
              "reasoning": "The recovery step depends on persisted role state, not stale client cache."
            }
          ]
        }
      ],
      "outcome": "The tool is deleted only after all saved role references are removed, preserving role integrity.",
      "related_journey_id": "J-1",
      "requirement_ids": [
        "REQ-4"
      ]
    },
    {
      "id": "J-4",
      "name": "Persist Actor Slots in a Task Template",
      "actor": "Platform developer creating a reusable multi-agent task template",
      "preconditions": "Authenticated user in the compose Task Templates editor with the scoped template canvas available.",
      "path_type": "happy",
      "failure_trigger": "",
      "steps": [
        {
          "step_number": 1,
          "action": "Create a task template subgraph and define one or more actor slots.",
          "observes": "The editor captures each actor slot's name, type constraint, and optional default binding.",
          "not_criteria": "Duplicate or unnamed actor slots are not accepted, and the editor does not imply that client-only state is enough.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/plan.md:179",
              "excerpt": "Template editor includes the actor-slot side panel.",
              "reasoning": "The scoped editor is where actor-slot authoring occurs."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Save the template, refresh the page, and reopen it.",
          "observes": "The template reloads with the same actor slots intact because the server persists and returns `actor_slots`.",
          "not_criteria": "Actor slots are not dropped on reload, and they do not rely on local browser persistence to survive a refresh.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:199",
              "excerpt": "Actor slots are lost on reload if the migration is missing.",
              "reasoning": "The happy path must prove that failure has been eliminated."
            }
          ]
        }
      ],
      "outcome": "The task template stores reusable actor-slot definitions that survive reloads and can be reused in later workflows.",
      "related_journey_id": "",
      "requirement_ids": [
        "REQ-5"
      ]
    },
    {
      "id": "J-5",
      "name": "Reject Invalid Actor Slot Definitions",
      "actor": "Platform developer editing a task template",
      "preconditions": "Authenticated user is in the task-template editor and attempts to save invalid actor-slot data.",
      "path_type": "failure",
      "failure_trigger": "The user enters malformed actor-slot data, such as duplicate names or an invalid default binding.",
      "steps": [
        {
          "step_number": 1,
          "action": "Enter invalid actor-slot definitions and attempt to save the template.",
          "observes": "The UI and API reject the save with clear validation feedback describing the invalid actor-slot data.",
          "not_criteria": "The template is not partially saved, and invalid actor-slot payloads are not silently normalized into persisted data.",
          "citations": [
            {
              "type": "code",
              "reference": "/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/plan-review-cycle-4.md:207",
              "excerpt": "Templates API must accept and validate `actor_slots` explicitly.",
              "reasoning": "Invalid slot data must fail in the request path, not after persistence."
            }
          ]
        },
        {
          "step_number": 2,
          "action": "Correct the actor-slot data and save again.",
          "observes": "The save succeeds, and a refresh reopens the template with only the corrected actor-slot definitions.",
          "not_criteria": "The user does not need to rely on local workarounds, and the server does not preserve the previously invalid slot payload.",
          "citations": [
            {
              "type": "research",
              "reference": "OWASP Input Validation Cheat Sheet",
              "excerpt": "Server-side validation should reject malformed input before processing.",
              "reasoning": "The recovery path depends on validation happening before persistence."
            }
          ]
        }
      ],
      "outcome": "The user is prevented from persisting invalid actor-slot data, corrects the issue, and saves a consistent template state.",
      "related_journey_id": "J-4",
      "requirement_ids": [
        "REQ-5",
        "REQ-8"
      ]
    }
  ],
  "security_profile": {
    "compliance_requirements": "No new external compliance regime is introduced beyond standard platform auth, tenancy isolation, and input-validation controls.",
    "data_sensitivity": "Internal — workflow-library metadata, prompts, schema JSON, and tool definitions.",
    "pii_handling": "No new high-sensitivity PII is introduced; the main identity field is JWT `sub`, used for ownership and tenancy scoping.",
    "auth_requirements": "JWT Bearer auth on compose library APIs via the existing auth-service boundary; all reads and writes are user-scoped and return 404 for cross-user access.",
    "data_retention": "Library entities follow the compose soft-delete lifecycle; reference-index rows are rebuilt or removed as workflows and library entities change. Automated hard-delete policy is out of scope for this revision.",
    "third_party_exposure": "No direct third-party exposure is added by library CRUD. Tool definitions may describe external systems, but secrets and runtime credentials are not stored in these tables.",
    "data_residency": "Compose library data resides in the compose PostgreSQL deployment region used by the accepted `tools/compose` backend.",
    "risk_mitigation_notes": "Keep SF-5 at five base tables; ship `workflow_entity_refs` and `tools` as SF-7 follow-on Alembic changes. Use non-destructive reference preflights before delete. Reject malformed or oversized payloads server-side. Do not restore plugin library surfaces. Keep tool references role-backed rather than workflow-ref-backed."
  },
  "data_entities": [
    {
      "name": "Tool",
      "fields": [
        "id",
        "user_id",
        "name",
        "description",
        "source",
        "input_schema",
        "created_at",
        "updated_at",
        "deleted_at"
      ],
      "constraints": [
        "Created by SF-7 as a follow-on table, not by the SF-5 foundation migration",
        "Unique per user among non-deleted rows",
        "Built-in tools are not stored in this table",
        "Delete is blocked while any non-deleted role still references the tool name"
      ],
      "is_new": true
    },
    {
      "name": "WorkflowEntityRef",
      "fields": [
        "workflow_id",
        "entity_type",
        "entity_id",
        "created_at"
      ],
      "constraints": [
        "Created by SF-7 as a follow-on extension on top of the five-table foundation",
        "Composite uniqueness on (workflow_id, entity_type, entity_id)",
        "Only persisted workflow references count toward delete blocking",
        "Applies to roles, output schemas, and task templates; tools remain role-referenced"
      ],
      "is_new": true
    },
    {
      "name": "CustomTaskTemplate",
      "fields": [
        "actor_slots"
      ],
      "constraints": [
        "`actor_slots` must be a JSON array of unique slot definitions",
        "The API must persist and return `actor_slots` after reload",
        "The `actor_slots` column is added by an SF-7 follow-on migration without expanding SF-5 beyond five foundation tables"
      ],
      "is_new": false
    }
  ],
  "cross_service_impacts": [
    {
      "service": "SF-5 composer-app-foundation",
      "impact": "Provides the accepted `tools/compose` PostgreSQL/Alembic foundation, the five base tables, and workflow mutation hooks that SF-7 extends. SF-5 must not absorb `workflow_entity_refs`, `tools`, plugin tables, or SQLite assumptions.",
      "action_needed": "Keep SF-5 limited to `workflows`, `workflow_versions`, `roles`, `output_schemas`, and `custom_task_templates`; expose workflow create/import/duplicate/save/delete mutation hooks so SF-7 can refresh the reference index from saved workflow state."
    },
    {
      "service": "SF-6 workflow-editor",
      "impact": "Workflow saves determine when role/schema/template references become persisted and visible to library delete preflights.",
      "action_needed": "Continue saving persisted library references through the compose workflow routes so SF-7 can refresh `workflow_entity_refs` from saved state rather than unsaved canvas state."
    },
    {
      "service": "SF-4 workflow-migration",
      "impact": "Imported or migrated workflows must produce the same persisted library-reference shape that the SF-7 index reads.",
      "action_needed": "Ensure workflow import and migration flows end at the compose workflow save boundary so SF-7 reference-index rows can be rebuilt after import."
    },
    {
      "service": "iriai-compose",
      "impact": "`Role.tools` remains a string-array contract consumed by the Role editor and tool delete protection.",
      "action_needed": "Preserve the current `list[str]` tool identifier model for v1; any future move to tool IDs is a separate follow-up decision."
    }
  ],
  "open_questions": [
    "Should `workflow_entity_refs` materialize `user_id` directly for faster queries, or should tenancy remain derived via joins to `workflows`?",
    "Should custom tool references remain name-based in `Role.tools` for v1, or should a later phase migrate them to stable tool IDs?",
    "What exact serialized shape should task-template actor-slot default bindings use in declarative workflow YAML so SF-1, SF-6, and SF-7 stay aligned?"
  ],
  "requirements": [
    "Artifact path: `/Users/danielzhang/src/iriai/.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md`",
    "R14 rebases SF-7 onto the accepted `tools/compose` + PostgreSQL/Alembic foundation.",
    "SF-5 stays at exactly five foundation tables; `workflow_entity_refs`, `tools`, and `actor_slots` follow-on changes are SF-7 scope.",
    "Role/schema/template delete preflight is backed by `GET /api/{entity}/references/{id}` and the SF-7 reference index.",
    "Tool CRUD, role-backed tool delete checks, auth/validation guardrails, and no plugin library surfaces remain explicit SF-7 scope."
  ],
  "acceptance_criteria": [
    "Referenced role deletion is blocked before DELETE and becomes allowed immediately after saved workflow references are removed.",
    "Inspecting migrations shows five SF-5 foundation tables, with `workflow_entity_refs`, `tools`, and `actor_slots` added only in SF-7 follow-on migrations.",
    "Task-template actor slots persist across reloads and are returned by the API.",
    "Edited tools refresh into Role editor checklists and referenced tools cannot be deleted until role references are removed.",
    "Oversized JSON and invalid entity names are rejected server-side, cross-user access returns 404, and plugin library surfaces do not reappear."
  ],
  "out_of_scope": [
    "Plugins Library pages, plugin endpoints, and PluginPicker surfaces",
    "Phase Templates Library",
    "Multi-user sharing or collaboration on library entities",
    "Tool auto-discovery from MCP servers",
    "Template version history or versioning UI",
    "Changing SF-5 foundation ownership beyond the accepted five-table boundary"
  ],
  "complete": true
}