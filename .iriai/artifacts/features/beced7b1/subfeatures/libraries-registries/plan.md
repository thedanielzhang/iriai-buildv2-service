### SF-7: Libraries & Registries

<!-- SF: libraries-registries -->



## Decision Log

| ID | Decision | Source |
|----|----------|--------|
| D-SF7-1 | Task Template editor imports SF-6 editor components directly (EditorCanvas, node types, edge types, inspectors) with `scopedMode` option. SF-7 creates only the scoping wrapper, SidePanel, MiniToolbar, ScaleBadge. | User choice (interview) |
| D-SF7-2 | YAML parsing on delete for reference checking. Parse all user workflows on DELETE to find entity references. O(N) per delete, always accurate, no materialized reference table. | Architect decision (delegated) |
| D-SF7-3 | SF-7 owns backend additions beyond STEP-67 basic CRUD: reference checking on delete, duplicate name validation, JSON Schema server-side validation, filtered queries (plugin instances by type_id), inline-to-library deduplication. | User confirmation (interview) |
| D-SF7-4 | TaskTemplateEditorView uses `createEditorStore({ scopedMode: true })` factory from SF-6 to create isolated store instances — NEVER imports the default singleton `useEditorStore`. | Architecture review [H-5] |
| D-SF7-5 | Picker components import from SF-6's confirmed paths: `features/editor/inspector/` (singular), `features/editor/ui/NodePalette.tsx`, `features/editor/ui/RolePalette.tsx`. | Architecture review [C-2] |
| D-SF7-6 | PromotionDialog and EntityDeleteDialog reference SF-1's authoritative 21 validation error codes (e.g., `invalid_actor_ref`, `invalid_plugin_ref`, `invalid_type_ref`). Deprecated names (`missing_actor`, `duplicate_ids`) must NOT be used. | Architecture review [SF-1 codes] |

### Prior Decisions Affecting SF-7

| ID | Decision | Impact on SF-7 |
|----|----------|---------------|
| D-16 | Remove TransformFunction model + `/api/transforms` | No transforms library page; no TransformPicker |
| D-17 | Remove version history UI | No version column in library grids |
| D-23 | Two-tier role editing: inline (Tier 1) + library (Tier 2) | RoleEditorView is 4-step content panel, not modal |
| D-25 | No PhaseTemplate; task templates read-only on canvas | No phase templates library; templates edit in library only |
| D-26 | Inline output schema creation primary, library secondary | SchemaEditorView messaging reinforces inline-first |
| D-35 | Library = sidebar folder + URL route | Each library has `/roles`, `/schemas`, `/templates`, `/plugins` route |
| D-36 | List collapses to sidebar on item select | XP Explorer pattern: click entity, list moves to sidebar tree |
| D-37 | Wizard for creation; form for editing | Role: 4-step wizard; Template: 3-step wizard; Schema/Plugin: form |
| D-38 | Inline-to-library promotion via save dialog | Lightweight PromotionDialog, not full wizard redirect |
| D-39 | Task Templates canvas-dominant; others form-based | Only template editor uses React Flow canvas |
| D-40 | Workflow + Template share canvas UX | Template canvas imports from `features/editor/` directly [D-SF7-1] |
| D-41 | Plugins: Types + Instances two-level | PluginType (interface) + PluginInstance (config) with FK relationship |
| D-42 | Actors = palette items; inspectors = drag-target slots | RolePicker renders as drag-target slot in AskInspector |
| D-43 | Toolbar palette = sole drag source for roles | Palette is authoritative source, not sidebar tree |

### SF-6 Confirmed Exports Consumed by SF-7 [C-2]

| Export | Confirmed Path | Used By |
|--------|---------------|---------|
| `createEditorStore(options?)` | `features/editor/store/editorStore.ts` | STEP-68 TaskTemplateEditorView |
| `EditorCanvas` | `features/editor/canvas/EditorCanvas.tsx` | STEP-68 TaskTemplateEditorView |
| `AskNode` | `features/editor/nodes/AskNode.tsx` | STEP-68 (via EditorCanvas) |
| `BranchNode` | `features/editor/nodes/BranchNode.tsx` | STEP-68 (via EditorCanvas) |
| `PluginNode` | `features/editor/nodes/PluginNode.tsx` | STEP-68 (via EditorCanvas) |
| `DataEdge` | `features/editor/edges/DataEdge.tsx` | STEP-68 (via EditorCanvas) |
| `HookEdge` | `features/editor/edges/HookEdge.tsx` | STEP-68 (via EditorCanvas) |
| `InspectorWindowManager` | `features/editor/inspector/InspectorWindowManager.tsx` | STEP-68 TaskTemplateEditorView |
| `AskInspector` | `features/editor/inspector/AskInspector.tsx` | STEP-69, STEP-70 (picker integration) |
| `PluginInspector` | `features/editor/inspector/PluginInspector.tsx` | STEP-69, STEP-70 (picker integration) |
| `CodeEditor` | `features/editor/inspector/CodeEditor.tsx` | STEP-66 SchemaEditorView |
| `NodePalette` | `features/editor/ui/NodePalette.tsx` | STEP-69, STEP-70 (TemplateBrowser) |
| `deserializeFromYaml` | `features/editor/serialization/deserializeFromYaml.ts` | STEP-68 |
| `serializeToYaml` | `features/editor/serialization/serializeToYaml.ts` | STEP-68 |

### SF-1 Authoritative Validation Error Codes (21 total) [H-3]

Referenced by EntityDeleteDialog and PromotionDialog for consistent messaging:

| Code | Description | Relevant to SF-7 |
|------|-------------|-------------------|
| `invalid_actor_ref` | Node actor not in `workflow.actors` | EntityDeleteDialog: deleting a role may cause this |
| `invalid_plugin_ref` | `plugin_ref` or `instance_ref` not found | EntityDeleteDialog: deleting plugin type/instance may cause this |
| `invalid_type_ref` | Type reference not in `workflow.types` | EntityDeleteDialog: deleting schema may cause this |
| `dangling_edge` | Edge references nonexistent node/port | EntityDeleteDialog: deleting template may cause this |
| `missing_required_field` | Required field missing | PromotionDialog: promoted entity clears this if it was missing |

**Deprecated names — do NOT use:** `missing_actor` → use `invalid_actor_ref`, `duplicate_ids` → use `duplicate_node_id`, `invalid_transform_ref` → REMOVED (transforms inline per D-21).

---

## File Structure

### Backend (additions to SF-5 routers)

```
tools/compose/backend/app/
├── routers/
│   ├── roles.py                 # modify — add duplicate name check, reference check on delete
│   ├── schemas.py               # modify — add JSON Schema validation, duplicate name, ref check
│   ├── templates.py             # modify — add duplicate name check, reference check on delete
│   └── plugins.py               # modify — add filtered instances query, ref check, duplicate name
├── services/
│   └── reference_checker.py     # create — YAML parsing reference checker
└── schemas/
    └── validation.py            # create — Pydantic request/response schemas for SF-7 additions
```

### Frontend

```
tools/compose/frontend/src/features/libraries/
├── index.ts                     # Re-exports for clean imports
├── hooks/
│   ├── useLibraryList.ts        # Shared: fetch + paginate + search + filter entities
│   ├── useLibraryEntity.ts      # Shared: fetch + save + delete single entity
│   ├── useReferenceCheck.ts     # Pre-delete reference check
│   └── useDuplicateNameCheck.ts # Debounced name uniqueness validation
├── components/
│   ├── LibraryGrid.tsx          # CSS grid of LibraryCards (CMP-97)
│   ├── LibraryCard.tsx          # Entity card with type icon + metadata (CMP-98)
│   ├── LibraryToolbar.tsx       # "+ New [Entity]" + ViewToggle (CMP-99)
│   ├── LibraryEmptyState.tsx    # Per-entity empty states with "Try an Example" CTA
│   ├── ExampleBadge.tsx         # Cyan "Example" badge (CMP-95)
│   └── LibraryDetailsView.tsx   # Table view for entities (reuses SF-5 DetailsView)
├── roles/
│   ├── RolesListPage.tsx        # /roles route — list view
│   ├── RoleEditorView.tsx       # Content panel, 4-step wizard (CMP-100)
│   ├── RoleStep1Identity.tsx    # Name + ModelPicker + TipCallout
│   ├── RoleStep2SystemPrompt.tsx # Full-height CodeMirror (markdown)
│   ├── RoleStep3Tools.tsx       # ToolChecklistGrid
│   ├── RoleStep4Metadata.tsx    # CodeMirror JSON + review summary
│   ├── ModelPicker.tsx          # Model dropdown (CMP-103)
│   ├── ToolChecklistGrid.tsx    # Checkbox grid of built-in tools (CMP-104)
│   └── ToolChip.tsx             # Single checkbox chip (CMP-105)
├── schemas/
│   ├── SchemasListPage.tsx      # /schemas route — list view
│   ├── SchemaEditorView.tsx     # Dual-pane editor (CMP-107)
│   ├── DualPaneLayout.tsx       # Resizable flex row (CMP-108)
│   ├── SchemaPreviewTree.tsx    # Property tree (CMP-109)
│   └── PropertyNode.tsx         # Tree node: name + type + required (CMP-110)
├── templates/
│   ├── TemplatesListPage.tsx    # /templates route — list view
│   ├── TaskTemplateEditorView.tsx # Canvas-dominant (CMP-111), imports createEditorStore from features/editor/store [H-5]
│   ├── TemplateWizardDialog.tsx # 3-step XPModal (CMP-127)
│   ├── SidePanel.tsx            # 280px metadata + ActorSlots + I/O (CMP-115)
│   ├── IOInterfaceEditor.tsx    # I/O port list with auto-detection (CMP-117)
│   ├── IOPort.tsx               # Single I/O port row (CMP-118)
│   ├── ScaleBadge.tsx           # "Task Template" context label (CMP-113)
│   └── MiniToolbar.tsx          # Scoped tools — no phase creation (CMP-114)
├── plugins/
│   ├── PluginsListPage.tsx      # /plugins route — types + instances sections
│   ├── PluginTypesGrid.tsx      # Plugin type cards (CMP-120)
│   ├── PluginTypeCard.tsx       # Type card with I/O, badge (CMP-121)
│   ├── PluginInstanceCard.tsx   # Instance card with config (CMP-122)
│   ├── PluginTypeDetailView.tsx # Read-only interface + "Create Instance" (CMP-123)
│   ├── PluginInstanceForm.tsx   # Auto-generated from config_schema (CMP-124)
│   ├── PluginTypeEditor.tsx     # Form: name, desc, I/O, config schema (CMP-125/126)
│   ├── ImplementationBanner.tsx # External code warning (CMP-125)
│   └── InputOutputListEditor.tsx # I/O port definitions (CMP-126)
├── pickers/
│   ├── RolePicker.tsx           # Drag-target slot in AskInspector (CMP-131)
│   ├── SchemaPicker.tsx         # Dropdown + "Create Inline" (CMP-132)
│   ├── PluginPicker.tsx         # Grouped dropdown: instances vs types (CMP-133)
│   └── TemplateBrowser.tsx      # Palette section for templates (CMP-134)
├── shared/
│   ├── StepIndicator.tsx        # Step progress indicator (CMP-101)
│   ├── StepNavigation.tsx       # Back/Next/Save buttons (CMP-102)
│   ├── TipCallout.tsx           # Blue info box (CMP-106)
│   ├── PromotionDialog.tsx      # Save-to-library dialog (CMP-129) — uses SF-1 validation codes [D-SF7-6]
│   ├── PromotionPreview.tsx     # Read-only config summary (CMP-130)
│   └── EntityDeleteDialog.tsx   # ConfirmDialog with reference count check — uses SF-1 validation codes [D-SF7-6]
└── types.ts                     # TypeScript interfaces for library entities
```

---

## Implementation Steps

### STEP-63: Backend Additions — Reference Checking, Validation, Filtered Queries

**Objective:** Extend SF-5's basic CRUD routers with reference-checking on delete, duplicate name validation, JSON Schema server-side validation, filtered plugin instance queries, and idempotent inline-to-library promotion. These are backend-only changes that SF-7 frontend depends on.

**Requirement IDs:** J-23, J-24, J-26, J-27, J-29
**Journey IDs:** J-23, J-24, J-27, J-29

**Scope:**
| Path | Action |
|------|--------|
| `tools/compose/backend/app/services/reference_checker.py` | create |
| `tools/compose/backend/app/schemas/validation.py` | create |
| `tools/compose/backend/app/routers/roles.py` | modify |
| `tools/compose/backend/app/routers/schemas.py` | modify |
| `tools/compose/backend/app/routers/templates.py` | modify |
| `tools/compose/backend/app/routers/plugins.py` | modify |
| `tools/compose/backend/app/models/workflow.py` | read |
| `tools/compose/backend/app/models/role.py` | read |
| `tools/compose/backend/app/models/output_schema.py` | read |
| `tools/compose/backend/app/models/task_template.py` | read |
| `tools/compose/backend/app/models/plugin_type.py` | read |
| `tools/compose/backend/app/models/plugin_instance.py` | read |
| `tools/compose/backend/app/db.py` | read |
| `tools/compose/backend/app/auth.py` | read |

**Instructions:**

**1. Create `reference_checker.py` — YAML-parsing reference checker [D-SF7-2]**

```python
# tools/compose/backend/app/services/reference_checker.py
import yaml
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.workflow import Workflow

async def check_entity_references(
    session: AsyncSession,
    entity_type: str,       # "role" | "schema" | "template" | "plugin_type" | "plugin_instance"
    entity_name: str,       # Name or ID to search for
    user_id: str,
) -> list[dict]:
    """
    Parse all non-deleted workflows for user, search YAML for entity references.
    Returns list of {workflow_id, workflow_name} that reference this entity.
    """
```

Reference detection patterns per entity type:
- **role**: `actor: "{name}"` in any AskNode within workflow YAML
- **schema**: `output_type: "{name}"` or `input_type: "{name}"` in any node/phase
- **template**: `$template_ref: "{name}"` in any template reference
- **plugin_type**: `plugin_ref: "{name}"` in any PluginNode
- **plugin_instance**: `instance_ref: "{name}"` in any PluginNode

Parse with `yaml.safe_load()`. Walk the resulting dict recursively. Return max 5 workflow names + total count. The function queries `SELECT id, name, yaml_content FROM workflows WHERE user_id = :uid AND deleted_at IS NULL`.

**2. Create `validation.py` — Pydantic schemas for SF-7 additions**

```python
# tools/compose/backend/app/schemas/validation.py
from pydantic import BaseModel

class ReferenceCheckResult(BaseModel):
    referenced: bool
    count: int
    workflow_names: list[str]  # max 5
    # Map to SF-1 validation error codes that would arise if entity deleted
    affected_validation_codes: list[str]  # e.g., ["invalid_actor_ref"] for roles

class NameCheckResult(BaseModel):
    available: bool
    message: str  # "" if available, "A role named 'pm' already exists" if not
```

The `affected_validation_codes` field maps to SF-1's authoritative codes [D-SF7-6]:
- role → `["invalid_actor_ref"]`
- schema → `["invalid_type_ref"]`
- template → `["dangling_edge"]`
- plugin_type → `["invalid_plugin_ref"]`
- plugin_instance → `["invalid_plugin_ref"]`

**3. Modify `roles.py` — Add duplicate name check, reference check, promotion dedup**

Add to the existing roles router:

- `GET /api/roles/check-name?name={name}` — Returns `NameCheckResult`. Query: `SELECT COUNT(*) FROM roles WHERE user_id = :uid AND name = :name AND deleted_at IS NULL`. Return `available: false` if count > 0.
- Modify `DELETE /api/roles/{id}` — Before soft-delete, call `check_entity_references(session, "role", role.name, user_id)`. If referenced, return `409 Conflict` with body `{"error": "referenced", "count": N, "workflow_names": [...], "affected_validation_codes": ["invalid_actor_ref"]}`.
- Modify `POST /api/roles` — Add idempotent promotion: if request includes `promote: true` header/field, check if role with same name already exists for user. If so, return existing role (200) instead of creating duplicate. If not, create new (201).

**4. Modify `schemas.py` — Add JSON Schema validation, duplicate name, reference check**

- `GET /api/schemas/check-name?name={name}` — Same pattern as roles.
- Modify `POST /api/schemas` and `PUT /api/schemas/{id}` — Validate `json_schema` field against JSON Schema Draft 2020-12 using Python `jsonschema` library's `jsonschema.validators.Draft202012Validator.check_schema()`. If invalid, return 422 with `{"error": "invalid_json_schema", "details": str(e)}`.
- Modify `DELETE /api/schemas/{id}` — Reference check before soft-delete. 409 includes `"affected_validation_codes": ["invalid_type_ref"]`.

**5. Modify `plugins.py` — Add filtered query, reference check, duplicate name**

- `GET /api/plugins/instances?plugin_type_id={type_id}` — Add optional query parameter to filter instances by type. Existing list endpoint gains `plugin_type_id: UUID | None = Query(None)` param.
- `GET /api/plugins/types/check-name?name={name}` and `GET /api/plugins/instances/check-name?name={name}` — Duplicate name checks.
- Modify `DELETE /api/plugins/types/{id}` — Reference check. Also check if type has any non-deleted instances (block if so). 409 includes `"affected_validation_codes": ["invalid_plugin_ref"]`.
- Modify `DELETE /api/plugins/instances/{id}` — Reference check against workflow YAML. 409 includes `"affected_validation_codes": ["invalid_plugin_ref"]`.

**6. Modify `templates.py` — Add duplicate name check, reference check**

- `GET /api/templates/check-name?name={name}` — Same pattern.
- Modify `DELETE /api/templates/{id}` — Reference check. 409 includes `"affected_validation_codes": ["dangling_edge"]`.

**Acceptance Criteria:**
- `DELETE /api/roles/{id}` where role "pm" is referenced in a workflow YAML returns 409 with `{"error": "referenced", "count": 1, "workflow_names": ["Planning Workflow"], "affected_validation_codes": ["invalid_actor_ref"]}`
- `DELETE /api/roles/{id}` where role is unreferenced returns 204 and sets `deleted_at`
- `GET /api/roles/check-name?name=pm` returns `{"available": false, "message": "A role named 'pm' already exists"}`
- `POST /api/schemas` with invalid `json_schema` returns 422 with error details
- `GET /api/plugins/instances?plugin_type_id={uuid}` returns only instances of that type
- `POST /api/roles` with `promote: true` and existing name returns 200 with existing role (not 201)

**Counterexamples:**
- Do NOT create a materialized reference table — parse YAML on delete [D-SF7-2]
- Do NOT use `UNIQUE` constraints for name checking — soft-deleted entities may share names; use application-level queries filtering `deleted_at IS NULL`
- Do NOT block delete for example entities (`is_example: true`) — they follow the same reference-check logic
- Do NOT cascade-delete plugin instances when deleting a plugin type — block with 409 if instances exist
- Do NOT use deprecated validation error code names (e.g., `missing_actor`) — use SF-1's authoritative codes (`invalid_actor_ref`) [D-SF7-6]

**Citations:**
- [decision: D-SF7-2] — YAML parsing on delete
- [decision: D-SF7-3] — SF-7 owns backend additions
- [decision: D-SF7-6] — SF-1 authoritative validation error codes
- [code: tools/compose/backend/app/routers/roles.py] — existing CRUD router from SF-5

---

### STEP-64: Shared Library Infrastructure — Hooks, Grid, Cards, Empty States

**Objective:** Create the shared frontend components and hooks that all 4 library pages (Roles, Schemas, Templates, Plugins) consume. This includes the data-fetching hooks (useLibraryList, useLibraryEntity), the LibraryGrid/Card/Toolbar layout components, empty states with "Try an Example" CTAs, and the entity type definitions.

**Requirement IDs:** J-23, J-24, J-25, J-26, J-29
**Journey IDs:** J-23, J-24, J-25, J-26, J-29

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/types.ts` | create |
| `features/libraries/index.ts` | create |
| `features/libraries/hooks/useLibraryList.ts` | create |
| `features/libraries/hooks/useLibraryEntity.ts` | create |
| `features/libraries/hooks/useReferenceCheck.ts` | create |
| `features/libraries/hooks/useDuplicateNameCheck.ts` | create |
| `features/libraries/components/LibraryGrid.tsx` | create |
| `features/libraries/components/LibraryCard.tsx` | create |
| `features/libraries/components/LibraryToolbar.tsx` | create |
| `features/libraries/components/LibraryEmptyState.tsx` | create |
| `features/libraries/components/ExampleBadge.tsx` | create |
| `features/libraries/components/LibraryDetailsView.tsx` | create |
| `features/libraries/shared/StepIndicator.tsx` | create |
| `features/libraries/shared/StepNavigation.tsx` | create |
| `features/libraries/shared/TipCallout.tsx` | create |
| `features/libraries/shared/EntityDeleteDialog.tsx` | create |
| `src/styles/windows-xp.css` | read |
| `src/components/` | read — SF-5 XP primitives (XPButton, ConfirmDialog, Toast, etc.) |

**Instructions:**

**1. `types.ts` — Entity type definitions**

```typescript
export interface LibraryEntity {
  id: string;
  name: string;
  is_example: boolean;
  user_id: string;
  created_at: string;
  updated_at: string;
}

export interface RoleEntity extends LibraryEntity {
  model: string;
  system_prompt: string;
  tools: string[];
  metadata: Record<string, unknown>;
}

export interface OutputSchemaEntity extends LibraryEntity {
  description: string;
  json_schema: Record<string, unknown>;
}

export interface TaskTemplateEntity extends LibraryEntity {
  description: string;
  yaml_content: string;
}

export interface PluginTypeEntity extends LibraryEntity {
  description: string;
  inputs: Array<{ name: string; type_ref?: string; description?: string }>;
  outputs: Array<{ name: string; type_ref?: string; description?: string }>;
  config_schema: Record<string, unknown>;
  category: string;
  is_builtin: boolean;
}

export interface PluginInstanceEntity extends LibraryEntity {
  plugin_type_id: string;
  config: Record<string, unknown>;
}

export type EntityType = 'role' | 'schema' | 'template' | 'plugin_type' | 'plugin_instance';

export const ENTITY_API_PATHS: Record<EntityType, string> = {
  role: '/api/roles',
  schema: '/api/schemas',
  template: '/api/templates',
  plugin_type: '/api/plugins/types',
  plugin_instance: '/api/plugins/instances',
};

/**
 * Maps entity types to SF-1 validation error codes that would arise
 * if the entity were deleted while still referenced by workflows.
 * Uses authoritative codes from SF-1 [H-3] — do NOT use deprecated names.
 */
export const ENTITY_VALIDATION_CODE_MAP: Record<EntityType, string> = {
  role: 'invalid_actor_ref',
  schema: 'invalid_type_ref',
  template: 'dangling_edge',
  plugin_type: 'invalid_plugin_ref',
  plugin_instance: 'invalid_plugin_ref',
};
```

**2. `useLibraryList.ts` — Shared list hook with pagination + search**

Hook signature:
```typescript
function useLibraryList<T extends LibraryEntity>(
  entityType: EntityType,
  options?: { search?: string; filter?: Record<string, string> }
): {
  items: T[];
  isLoading: boolean;
  error: string | null;
  hasMore: boolean;
  loadMore: () => void;
  refresh: () => void;
  totalCount: number;
}
```

Uses authenticated Axios client from SF-5. Cursor-based pagination (20 per page). Search parameter passed as `?search={query}`. Filter params passed as query strings (e.g., `?plugin_type_id={id}` for plugin instances).

Separates items into two groups: `myItems` (where `is_example === false`) and `exampleItems` (where `is_example === true`). Library pages render "My {Entity}" section first, then "Examples" section.

**3. `useLibraryEntity.ts` — Single entity CRUD hook**

```typescript
function useLibraryEntity<T extends LibraryEntity>(
  entityType: EntityType,
  entityId: string | null
): {
  entity: T | null;
  isLoading: boolean;
  error: string | null;
  save: (data: Partial<T>) => Promise<T>;
  remove: () => Promise<{
    success: boolean;
    references?: {
      count: number;
      workflow_names: string[];
      affected_validation_codes: string[];  // SF-1 codes [D-SF7-6]
    };
  }>;
  duplicate: () => Promise<T>;
}
```

The `remove()` method calls DELETE and handles 409 (referenced) by returning `{ success: false, references: {...} }` instead of throwing. The `affected_validation_codes` array from the 409 response is preserved for EntityDeleteDialog to display.

**4. `useReferenceCheck.ts` — Pre-delete reference check**

Wraps the DELETE response pattern. On 409, parses the reference body (including `affected_validation_codes`) and returns it for the EntityDeleteDialog to display.

**5. `useDuplicateNameCheck.ts` — Debounced name validation**

```typescript
function useDuplicateNameCheck(
  entityType: EntityType,
  name: string,
  excludeId?: string  // exclude current entity when editing
): { isChecking: boolean; isAvailable: boolean; message: string }
```

Calls `GET /api/{entity}/check-name?name={name}` with 300ms debounce. Returns `isAvailable` + error message for inline display. Excludes current entity ID when editing (not creating).

**6. `LibraryGrid.tsx` — CSS grid layout (CMP-97)**

```typescript
interface LibraryGridProps {
  items: LibraryEntity[];
  entityType: EntityType;
  onSelect: (id: string) => void;
  selectedId?: string;
  renderCard?: (item: LibraryEntity) => React.ReactNode;
}
```

CSS grid: `grid-template-columns: repeat(auto-fill, minmax(200px, 1fr))`. Gap: 16px. Responds to breakpoints per Section 6 of design doc. Each card gets `data-testid="library-grid-card-{id}"`.

**7. `LibraryCard.tsx` — Entity card (CMP-98)**

```typescript
interface LibraryCardProps {
  entity: LibraryEntity;
  entityType: EntityType;
  onClick: () => void;
  selected: boolean;
  icon: React.ReactNode;
  metadata?: string; // e.g., "claude-opus-4" for roles, "5 properties" for schemas
}
```

Card layout: type icon top-left, name (bold), metadata line (muted), ExampleBadge if `is_example`. No version badge [D-17]. Hover: purple border + elevated shadow. Selected: purple bg tint.

**8. `LibraryEmptyState.tsx` — Per-entity empty states**

Renders icon + heading + description + two CTAs: primary "+ New {Entity}" and secondary "Try an Example". "Try an Example" calls `POST /api/{entity}/{example_id}/duplicate` which creates an editable copy of the example for the user. Each empty state has entity-specific messaging:
- Roles: "No roles yet. Create a role to assign to Ask nodes."
- Schemas: "No output schemas yet. Tip: create schemas inline in the Ask inspector, then promote to the library." [D-26]
- Templates: "No task templates yet. Save a group of nodes from the workflow editor."
- Plugins: "No plugin instances configured. Browse available types and create an instance."

**9. `StepIndicator.tsx` (CMP-101), `StepNavigation.tsx` (CMP-102), `TipCallout.tsx` (CMP-106)**

StepIndicator: Horizontal step labels with active/completed/upcoming states. Uses `role="tablist"` / `role="tab"` per accessibility spec. Active step highlighted purple.

StepNavigation: Back + Next/Save buttons. Back disabled on step 1. Next validates current step before advancing. Final step shows "Save" (primary variant).

TipCallout: Blue info box with lightbulb icon, best-practice text. Renders as `aside` with `role="note"`.

**10. `EntityDeleteDialog.tsx` — ConfirmDialog with reference protection [D-SF7-6]**

Wraps SF-5's ConfirmDialog. Two modes:
- **Unreferenced**: Standard delete confirmation with entity name.
- **Referenced**: Shows "Cannot delete — referenced by N workflow(s)" with workflow name list (max 5, "+N more" overflow). Only "Close" button — no delete action. Text: "Remove all references to this {entity} before deleting it."

When referenced, also shows a muted note below the workflow list: "Deleting this {entity} would cause `{code}` validation errors in the listed workflows." where `{code}` is the SF-1 authoritative validation code from `ENTITY_VALIDATION_CODE_MAP` (e.g., `invalid_actor_ref` for roles, `invalid_plugin_ref` for plugins). This helps users understand the impact. The code is rendered in a monospace `<code>` tag.

**Acceptance Criteria:**
- Navigate to `/roles` with no user roles → empty state shows with icon, heading, CTAs
- Navigate to `/roles` with 3 user roles + 8 examples → "My Roles" section (3 cards), "Examples" section (8 cards with cyan badge)
- Click "Try an Example" on empty state → example duplicated, appears in "My Roles", toast: "Example role duplicated"
- Grid cards show name, metadata, ExampleBadge — no version badge
- Grid responds to breakpoints: 2 cols at 768px, 3+ at 1440px
- `useLibraryList` fetches first 20 items, shows "Load more" button if `has_more`
- EntityDeleteDialog for referenced role shows "would cause `invalid_actor_ref` validation errors"

**Counterexamples:**
- Do NOT hardcode example entities in frontend — fetch from backend with `is_example` flag
- Do NOT show examples above user content — "My {Entity}" section always first
- Do NOT use `useEffect` with inline `.filter()` for section splitting — derive in `useMemo`
- Do NOT show "Delete" button on EntityDeleteDialog when entity is referenced — only "Close"
- Do NOT use Zustand selectors with `.filter()` or `.map()` — breaks referential stability [Zustand Gotcha from MEMORY.md]
- Do NOT use deprecated SF-1 validation code names (e.g., `missing_actor`) — use authoritative codes [D-SF7-6]

**Citations:**
- [code: src/styles/windows-xp.css] — XP design tokens
- [decision: D-35] — Library = sidebar folder + URL route
- [decision: D-17] — No version badges
- [decision: D-26] — Inline-first messaging for schemas
- [decision: D-SF7-6] — SF-1 authoritative validation error codes

**data-testid assignments:**
- `library-grid` — grid container
- `library-grid-card-{id}` — individual card
- `library-toolbar` — toolbar container
- `library-toolbar-new-btn` — "+ New" button
- `library-toolbar-view-toggle` — grid/details toggle
- `library-empty-state` — empty state container
- `library-empty-state-new-btn` — primary CTA
- `library-empty-state-example-btn` — "Try an Example" CTA
- `library-section-mine` — "My {Entity}" section
- `library-section-examples` — "Examples" section
- `example-badge` — cyan example badge
- `step-indicator` — step progress bar
- `step-indicator-step-{n}` — individual step (1-indexed)
- `step-nav-back` — Back button
- `step-nav-next` — Next/Save button
- `tip-callout` — tip callout container
- `entity-delete-dialog` — delete confirmation
- `entity-delete-dialog-ref-count` — reference count display
- `entity-delete-dialog-ref-list` — referencing workflow list
- `entity-delete-dialog-validation-code` — SF-1 validation code note

---

### STEP-65: Roles Library — 4-Step Editor with ModelPicker and ToolChecklist

**Objective:** Build the Roles library page (`/roles`) with the full 4-step RoleEditorView content panel (Identity, System Prompt, Tools, Metadata), ModelPicker dropdown, ToolChecklistGrid, and TipCallouts with best-practice guidance. The role editor is a content-panel wizard (not a modal) per D-23.

**Requirement IDs:** J-23, J-27
**Journey IDs:** J-23, J-27

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/roles/RolesListPage.tsx` | create |
| `features/libraries/roles/RoleEditorView.tsx` | create |
| `features/libraries/roles/RoleStep1Identity.tsx` | create |
| `features/libraries/roles/RoleStep2SystemPrompt.tsx` | create |
| `features/libraries/roles/RoleStep3Tools.tsx` | create |
| `features/libraries/roles/RoleStep4Metadata.tsx` | create |
| `features/libraries/roles/ModelPicker.tsx` | create |
| `features/libraries/roles/ToolChecklistGrid.tsx` | create |
| `features/libraries/roles/ToolChip.tsx` | create |
| `features/libraries/hooks/useLibraryList.ts` | read |
| `features/libraries/hooks/useLibraryEntity.ts` | read |
| `features/libraries/hooks/useDuplicateNameCheck.ts` | read |
| `features/libraries/components/LibraryGrid.tsx` | read |
| `features/libraries/shared/StepIndicator.tsx` | read |
| `features/libraries/shared/StepNavigation.tsx` | read |
| `features/libraries/shared/TipCallout.tsx` | read |

**Instructions:**

**1. `RolesListPage.tsx` — Route component for `/roles`**

Uses `useLibraryList<RoleEntity>('role')`. Renders LibraryGrid with role-specific cards showing: name (bold), model badge (e.g., "claude-opus-4"), tool count (e.g., "8 tools"). ExampleBadge for `is_example` entities. Click card → navigates to `/roles/{id}` and triggers list-to-sidebar collapse [D-36].

**2. `RoleEditorView.tsx` — 4-step content panel (CMP-100)**

Content panel that fills the main content area (not a modal) [D-23]. Manages step state (1–4), dirty tracking, and beforeunload warning.

```typescript
interface RoleEditorViewProps {
  roleId: string | 'new';
}
```

Steps:
1. **Identity** — Name (required, duplicate check on blur), Model (dropdown), TipCallout
2. **System Prompt** — Full-height CodeMirror (markdown mode), TipCallout with best practices
3. **Tools** — ToolChecklistGrid with built-in tool list, TipCallout
4. **Review & Save** — Read-only summary of all fields, Save button (POST or PUT)

Step navigation: cannot advance past Step 1 without name. Can navigate back freely. Clicking completed step indicator jumps to that step. Dirty state shows orange dot on toolbar save button.

**3. `ModelPicker.tsx` — Model dropdown (CMP-103)**

Hardcoded model list matching SF-4 pre-seeded content:
```typescript
const MODELS = [
  { value: 'claude-opus-4-20250514', label: 'Claude Opus 4', tier: 'opus' },
  { value: 'claude-sonnet-4-20250514', label: 'Claude Sonnet 4', tier: 'sonnet' },
  { value: 'claude-haiku-4-5-20251001', label: 'Claude Haiku 4.5', tier: 'haiku' },
];
```

Renders as SF-5 DropdownMenu with model name + tier badge.

**4. `ToolChecklistGrid.tsx` — Tool selection (CMP-104)**

Grid of ToolChip components. Built-in tool list:
```typescript
const BUILT_IN_TOOLS = [
  'Read', 'Edit', 'Write', 'Bash', 'Grep', 'Glob',
  'WebSearch', 'WebFetch', 'Agent',
];
```

Each chip is a checkbox with tool name. Selected tools stored as `string[]` on the role entity. Grid layout: `grid-template-columns: repeat(auto-fill, minmax(120px, 1fr))`.

**5. TipCallout content per step:**
- Step 1: "Choose a descriptive name that reflects this role's purpose in your workflow. The model determines the agent's capability tier."
- Step 2: "Write the system prompt in markdown. Include the role's objective, constraints, and expected output format. Be specific about what the agent should and should NOT do."
- Step 3: "Select the tools this agent needs. Fewer tools = faster, cheaper execution. Only enable what the role actually requires."

**Acceptance Criteria:**
- Navigate to `/roles` → list page with grid of role cards
- Click "+ New Role" → RoleEditorView opens at Step 1
- Type name "test-role" → duplicate check fires on blur, shows green if available
- Try to click "Next" without name → validation error, red border on name field, stays on Step 1
- Fill name + model → click Next → Step 2 (CodeMirror loads)
- Type system prompt → Next → Step 3 (ToolChecklistGrid)
- Select tools → Next → Step 4 (summary view)
- Click Save → POST /api/roles → green toast → role appears in list + sidebar tree
- Edit existing role → all 4 steps pre-populated, starts at Step 1
- beforeunload fires if dirty (fields changed but not saved)

**Counterexamples:**
- Do NOT use a modal dialog for role creation — fills content panel [D-23]
- Do NOT allow proceeding to Step 2 without a name entered
- Do NOT show model picker as a text input — dropdown with predefined options
- Do NOT include tools not in the BUILT_IN_TOOLS list — extensibility deferred

**data-testid assignments:**
- `roles-list-page` — page container
- `role-editor` — editor content panel
- `role-editor-header` — header with name + actions
- `role-step-1` — Identity step panel
- `role-step-2` — System Prompt step panel
- `role-step-3` — Tools step panel
- `role-step-4` — Review step panel
- `role-name-input` — name text input
- `role-name-error` — inline name error message
- `role-model-picker` — model dropdown
- `role-model-option-{tier}` — model option (opus/sonnet/haiku)
- `role-prompt-editor` — CodeMirror system prompt editor
- `role-tools-grid` — tool checklist grid
- `role-tool-chip-{name}` — individual tool checkbox (e.g., `role-tool-chip-Read`)
- `role-review-summary` — step 4 summary
- `role-save-btn` — save button

---

### STEP-66: Output Schemas Library — Dual-Pane JSON Schema Editor

**Objective:** Build the Output Schemas library page (`/schemas`) with the dual-pane SchemaEditorView (CodeMirror JSON Schema left, SchemaPreviewTree right), live validation with 500ms debounce, and inline-first messaging that reinforces D-26 (output schemas are primarily created inline in the Ask inspector).

**Requirement IDs:** J-24
**Journey IDs:** J-24, J-29

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/schemas/SchemasListPage.tsx` | create |
| `features/libraries/schemas/SchemaEditorView.tsx` | create |
| `features/libraries/schemas/DualPaneLayout.tsx` | create |
| `features/libraries/schemas/SchemaPreviewTree.tsx` | create |
| `features/libraries/schemas/PropertyNode.tsx` | create |
| `features/editor/inspector/CodeEditor.tsx` | read — shared CodeMirror wrapper from SF-6 [C-2 confirmed path] |

**Instructions:**

**1. `SchemaEditorView.tsx` — Dual-pane editor (CMP-107)**

Top bar: name input + description input + Validate button + Save button. Below: DualPaneLayout with CodeMirror (left) and SchemaPreviewTree (right).

Import the CodeMirror wrapper from SF-6's confirmed path: `features/editor/inspector/CodeEditor.tsx` [C-2]. Configure with JSON mode, dark theme (#1e1e2e), line numbers. Pre-populated skeleton for new schemas:
```json
{
  "type": "object",
  "properties": {
    "field_name": {
      "type": "string",
      "description": "Description here"
    }
  },
  "required": ["field_name"]
}
```

Live validation: 500ms debounce after typing stops. Client-side: try `JSON.parse()` — if fails, show red banner "Invalid JSON" with parse error position. If valid JSON, try to interpret as JSON Schema — render preview tree. Server validates Draft 2020-12 on save.

**2. `DualPaneLayout.tsx` — Resizable split pane (CMP-108)**

Flex row with draggable divider. Default split: 50/50. Min pane width: 200px. Responsive: at 768–1023px, stacks vertically (editor top, preview bottom). Preserves split ratio in localStorage.

**3. `SchemaPreviewTree.tsx` — Property tree (CMP-109)**

Renders a tree of PropertyNode components from parsed JSON Schema. Shows:
- Property name (bold)
- Type badge (string/number/boolean/object/array)
- Required marker (red asterisk)
- Nested objects expand/collapse

Invalid schema → red banner at top: "Schema validation error: {message}". Tree renders partial valid portion. Preview must NOT crash on bad JSON — catches parse errors gracefully.

**Acceptance Criteria:**
- Navigate to `/schemas` → list page with empty state messaging: "No output schemas yet" + inline-first subtitle [D-26]
- Click "+ New Schema" → dual-pane editor opens with skeleton
- Type valid JSON Schema → preview tree renders within 500ms, green "Valid" badge
- Type invalid JSON → red "Invalid JSON" banner with line number, preview stays at last valid state
- Save with invalid schema → server returns 422 → red toast with error details
- Save with valid schema → 201 → green toast → appears in list + SchemaPicker dropdown
- Resize panes with drag handle → ratio persisted

**Counterexamples:**
- Do NOT build a visual field-builder — this is a raw JSON Schema editor [per design decisions]
- Do NOT crash or white-screen on invalid JSON — graceful degradation
- Do NOT make the library the primary creation path — messaging reinforces inline-first [D-26]
- Do NOT validate on every keystroke — 500ms debounce after typing stops
- Do NOT import CodeEditor from `features/editor/inspectors/` (plural) — confirmed path is `features/editor/inspector/` (singular) [C-2]

**data-testid assignments:**
- `schemas-list-page` — page container
- `schema-editor` — editor content panel
- `schema-name-input` — name input
- `schema-description-input` — description input
- `schema-validate-btn` — validate button
- `schema-save-btn` — save button
- `schema-code-editor` — CodeMirror pane
- `schema-preview-tree` — preview tree pane
- `schema-preview-property-{name}` — property node
- `schema-preview-valid-badge` — green valid indicator
- `schema-preview-error-banner` — red error banner
- `schema-dual-pane-divider` — resize handle

---

### STEP-67: Plugins Registry — Two-Level Types + Instances

**Objective:** Build the Plugins library page (`/plugins`) with the two-level Types + Instances structure (D-41). Plugin types define interfaces (I/O, config schema). Plugin instances are per-project configurations of a type. Built-in types are read-only; custom types are editable. Custom types show an ImplementationBanner warning that external code must provide the runtime implementation.

**Requirement IDs:** J-26, J-28
**Journey IDs:** J-26, J-28, J-29

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/plugins/PluginsListPage.tsx` | create |
| `features/libraries/plugins/PluginTypesGrid.tsx` | create |
| `features/libraries/plugins/PluginTypeCard.tsx` | create |
| `features/libraries/plugins/PluginInstanceCard.tsx` | create |
| `features/libraries/plugins/PluginTypeDetailView.tsx` | create |
| `features/libraries/plugins/PluginInstanceForm.tsx` | create |
| `features/libraries/plugins/PluginTypeEditor.tsx` | create |
| `features/libraries/plugins/ImplementationBanner.tsx` | create |
| `features/libraries/plugins/InputOutputListEditor.tsx` | create |

**Instructions:**

**1. `PluginsListPage.tsx` — Two-section layout**

Two sections stacked vertically:
- **"Plugin Types"** section: PluginTypesGrid showing all types (built-in + custom)
- **"My Instances"** section: grid of PluginInstanceCards, or empty state if none

"+ New Plugin Type" button in toolbar creates custom type. "Create Instance" flows from type detail view.

**2. `PluginTypeCard.tsx` — Type card (CMP-121)**

Shows: name, description (truncated), I/O count badges (e.g., "2 inputs, 1 output"), category badge, instance count. Two badge variants:
- Built-in: purple "Built-in" badge, card click → read-only PluginTypeDetailView
- Custom: amber "Custom" badge, card click → editable PluginTypeEditor

**3. `PluginTypeDetailView.tsx` — Read-only type interface (CMP-123)**

Shows full interface: name, description, inputs list, outputs list, config_schema preview (CodeMirror read-only), category. "Create Instance" primary button at bottom → opens PluginInstanceForm pre-linked to this type.

**4. `PluginInstanceForm.tsx` — Auto-generated config form (CMP-124)**

Generates form fields from the type's `config_schema` (JSON Schema). Maps JSON Schema types to form fields:
- `string` → text input
- `number` / `integer` → number input
- `boolean` → checkbox
- `object` → nested fieldset
- `array` → dynamic list
- `enum` → dropdown select

Name field at top. Config fields below. Validate against config_schema before save. Save creates instance via `POST /api/plugins/instances` with `plugin_type_id` FK.

**5. `PluginTypeEditor.tsx` — Custom type creation form**

Form fields: name, description, InputOutputListEditor (for inputs), InputOutputListEditor (for outputs), CodeMirror for config_schema (JSON Schema), implementation notes (text). Always shows ImplementationBanner at top.

**6. `ImplementationBanner.tsx` — External code warning (CMP-125)**

Yellow/amber banner: "This plugin type defines an interface only. Runtime implementation must be provided by the consuming project via the PluginRegistry." Always visible on PluginTypeEditor and PluginTypeDetailView (for custom types).

**7. `InputOutputListEditor.tsx` — I/O port definitions (CMP-126)**

Dynamic list of rows. Each row: name (text input), type_ref (optional text input), description (optional text input), remove button. "Add Input/Output" button appends row. Min 0 rows.

**Acceptance Criteria:**
- Navigate to `/plugins` → "Plugin Types" section shows pre-seeded types (with "Built-in" badges) and "My Instances" section
- Click built-in type → read-only detail view with I/O, config schema, "Create Instance" button
- Click "Create Instance" → form auto-generated from config_schema → fill → save → instance in "My Instances"
- Click "+ New Plugin Type" → PluginTypeEditor with ImplementationBanner at top
- Fill type name + I/O + config_schema → save → type in grid with "Custom" amber badge
- Delete type with existing instances → 409 "Cannot delete — has N configured instances"
- `GET /api/plugins/instances?plugin_type_id={id}` returns only instances of that type

**Counterexamples:**
- Do NOT allow editing built-in plugin types — read-only detail view only
- Do NOT cascade-delete instances when deleting a type — block with 409
- Do NOT hide the ImplementationBanner — always visible for custom types
- Do NOT render config_schema as raw JSON on instance form — generate form fields from schema

**data-testid assignments:**
- `plugins-list-page` — page container
- `plugins-types-section` — types grid section
- `plugins-instances-section` — instances section
- `plugin-type-card-{id}` — type card
- `plugin-type-badge-builtin` — "Built-in" purple badge
- `plugin-type-badge-custom` — "Custom" amber badge
- `plugin-type-detail` — read-only detail view
- `plugin-type-detail-create-instance-btn` — "Create Instance" button
- `plugin-instance-card-{id}` — instance card
- `plugin-instance-form` — instance creation form
- `plugin-instance-form-name` — name input
- `plugin-instance-form-config` — auto-generated config section
- `plugin-instance-form-save-btn` — save button
- `plugin-type-editor` — type creation form
- `plugin-type-editor-name` — name input
- `plugin-type-editor-io-inputs` — InputOutputListEditor for inputs
- `plugin-type-editor-io-outputs` — InputOutputListEditor for outputs
- `plugin-type-editor-config-schema` — CodeMirror config schema
- `plugin-implementation-banner` — amber warning banner
- `io-list-add-btn` — "Add" button in InputOutputListEditor
- `io-list-row-{index}` — individual I/O row

---

### STEP-68: Task Templates Library — Canvas Editor with Isolated Store [H-5]

**Objective:** Build the Task Templates library page (`/templates`) with the canvas-dominant TaskTemplateEditorView that uses SF-6's `createEditorStore()` factory [H-5, D-SF7-4] to create isolated store instances with `scopedMode: true`. The template canvas uses the same React Flow node types, edge types, and inspectors as the workflow editor but without phase creation tools. A 280px SidePanel provides metadata, actor slot definitions, and I/O interface editing.

**Requirement IDs:** J-25, J-20
**Journey IDs:** J-25, J-20, J-21

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/templates/TemplatesListPage.tsx` | create |
| `features/libraries/templates/TaskTemplateEditorView.tsx` | create |
| `features/libraries/templates/TemplateWizardDialog.tsx` | create |
| `features/libraries/templates/SidePanel.tsx` | create |
| `features/libraries/templates/IOInterfaceEditor.tsx` | create |
| `features/libraries/templates/IOPort.tsx` | create |
| `features/libraries/templates/ScaleBadge.tsx` | create |
| `features/libraries/templates/MiniToolbar.tsx` | create |
| `features/editor/store/editorStore.ts` | read — import `createEditorStore` factory function [H-5] |
| `features/editor/canvas/EditorCanvas.tsx` | read — import canvas component [C-2] |
| `features/editor/nodes/AskNode.tsx` | read [C-2] |
| `features/editor/nodes/BranchNode.tsx` | read [C-2] |
| `features/editor/nodes/PluginNode.tsx` | read [C-2] |
| `features/editor/edges/DataEdge.tsx` | read [C-2] |
| `features/editor/edges/HookEdge.tsx` | read [C-2] |
| `features/editor/inspector/InspectorWindowManager.tsx` | read [C-2 — singular `inspector/`] |
| `features/editor/serialization/deserializeFromYaml.ts` | read [C-2] |
| `features/editor/serialization/serializeToYaml.ts` | read [C-2] |

**Instructions:**

**1. `TaskTemplateEditorView.tsx` — Canvas-dominant layout with isolated store (CMP-111) [H-5]**

Layout: MiniToolbar at top, flex row with MiniCanvasPane (flex: 1) + SidePanel (280px). ScaleBadge floating at bottom-left of canvas showing "Task Template".

**CRITICAL [H-5]:** The canvas creates a **separate, isolated** editorStore instance using the `createEditorStore()` factory function exported from `features/editor/store/editorStore.ts`. It must NEVER import or use the default singleton `useEditorStore`.

```typescript
import { createEditorStore } from '@/features/editor/store/editorStore';
import { EditorCanvas } from '@/features/editor/canvas/EditorCanvas';
import { InspectorWindowManager } from '@/features/editor/inspector/InspectorWindowManager';
import { deserializeFromYaml } from '@/features/editor/serialization/deserializeFromYaml';
import { serializeToYaml } from '@/features/editor/serialization/serializeToYaml';

function TaskTemplateEditorView({ templateId }: { templateId: string }) {
  // [H-5] Create isolated store — NEVER use the singleton useEditorStore
  const storeRef = useRef(
    createEditorStore({
      scopedMode: true,  // Disables phase creation, template stamping, detach
      initialWorkflowName: '', // Will be set from template data
    })
  );
  const useTemplateStore = storeRef.current;

  // Initialize from template YAML
  useEffect(() => {
    if (templateData?.yaml_content) {
      const { nodes, edges } = deserializeFromYaml(templateData.yaml_content);
      useTemplateStore.getState().loadWorkflow(nodes, edges);
    }
  }, [templateData]);

  // Save serializes from isolated store
  const handleSave = async () => {
    const { nodes, edges } = useTemplateStore.getState();
    const yaml = serializeToYaml(nodes, edges);
    await api.put(`/api/templates/${templateId}`, { yaml_content: yaml });
  };

  return (
    <div data-testid="template-editor">
      <MiniToolbar store={useTemplateStore} />
      <div style={{ display: 'flex' }}>
        {/* Pass the isolated store to EditorCanvas */}
        <EditorCanvas store={useTemplateStore} />
        <SidePanel templateId={templateId} store={useTemplateStore} />
      </div>
      <ScaleBadge label="Task Template" />
      {/* Inspector system also uses the isolated store */}
      <InspectorWindowManager store={useTemplateStore} />
    </div>
  );
}
```

**`scopedMode: true` disables (as defined in SF-6's D-SF6-9):**
- `stampTemplate()` — no-op
- `detachTemplateGroup()` — no-op
- `setToolMode('select')` — blocked (no phase creation rectangle)
- All other actions work normally: `addNode`, `removeNodes`, `updateNodeData`, `addEdge`, undo/redo, collapse, serialization

**Store lifecycle:** The `useRef` ensures the store is created exactly once per component mount. When the template editor is unmounted, the isolated store is garbage collected — no cleanup of a shared singleton needed. If the user opens multiple template editors (via tabs), each gets its own store instance.

**2. `TemplateWizardDialog.tsx` — 3-step creation wizard (CMP-127)**

XPModal dialog for creating a new template. Steps:
1. Name + description (text inputs)
2. Actor slots — define named slots that workflows will fill (e.g., "producer_actor", "approver_actor"). Each slot: name (string), default_role (optional role ID from library)
3. Confirmation → creates template via POST, opens TaskTemplateEditorView

**3. `SidePanel.tsx` — 280px metadata panel (CMP-115)**

Three sections:
- **Metadata**: name (editable), description (editable)
- **Actor Slots**: list of named slots from wizard, each with role picker dropdown (fetches from `/api/roles`)
- **I/O Interface**: IOInterfaceEditor showing detected inputs/outputs of the template subgraph

Responsive: at 768–1023px, becomes a drawer overlay instead of inline panel.

The SidePanel receives the isolated store via prop and reads node/edge data from it (NOT from the singleton).

**4. `IOInterfaceEditor.tsx` — I/O port auto-detection (CMP-117)**

Scans the template's nodes (from the isolated store) for unconnected input ports (= template inputs) and unconnected output ports (= template outputs). Displays each as an IOPort row with: port name (auto-detected from node), rename field, type annotation (optional). Users can rename ports for the template interface but cannot add/remove (auto-detected).

**Acceptance Criteria:**
- Navigate to `/templates` → list page
- Click "+ New Template" → 3-step wizard dialog opens
- Fill name → define 2 actor slots → confirm → TaskTemplateEditorView opens with empty canvas
- Drag Ask node from palette → purple card appears on canvas
- Double-click node → inspector opens (same as workflow editor)
- Unconnected ports auto-detected in SidePanel's I/O section
- Save → PUT /api/templates/{id} with serialized yaml_content
- No phase creation tools available — Hand tool only, Select tool blocked by `scopedMode: true`
- ScaleBadge shows "Task Template" at bottom-left
- Open template editor → open workflow editor in another tab → changes in one do NOT affect the other (isolated stores)

**Counterexamples:**
- **Do NOT import the singleton `useEditorStore`** — always use `createEditorStore()` factory [H-5, D-SF7-4]
- **Do NOT share store instances** between template editor and workflow editor — each must be isolated
- Do NOT allow phase creation in template editor [D-25, D-39] — `scopedMode: true` handles this
- Do NOT create a separate set of node components — import directly from `features/editor/` [D-SF7-1]
- Do NOT make templates editable on the workflow canvas — edit in library only [D-25]
- Do NOT use PaintMenuBar — use simplified MiniToolbar
- Do NOT import from `features/editor/inspectors/` (plural) — confirmed path is `features/editor/inspector/` (singular) [C-2]
- Do NOT use `features/editor/palette/NodePalette.tsx` — confirmed path is `features/editor/ui/NodePalette.tsx` [C-2]

**Citations:**
- [decision: D-SF7-4] — `createEditorStore()` factory for isolated instances
- [decision: D-SF7-1] — Direct import of SF-6 editor components
- [code: features/editor/store/editorStore.ts] — Factory function `createEditorStore(options?: EditorStoreOptions)` with `scopedMode` option [H-5]
- [code: features/editor/store/editorStore.ts] — `EditorStoreOptions.scopedMode` disables phase creation, template stamping, detach [D-SF6-9]

**data-testid assignments:**
- `templates-list-page` — page container
- `template-editor` — editor view container
- `template-wizard-dialog` — creation wizard modal
- `template-wizard-step-1` — name + description step
- `template-wizard-step-2` — actor slots step
- `template-wizard-step-3` — confirmation step
- `template-wizard-name-input` — name input in wizard
- `template-wizard-slot-add-btn` — add actor slot button
- `template-wizard-slot-{index}` — individual slot row
- `template-wizard-create-btn` — create button
- `template-canvas` — React Flow canvas area
- `template-scale-badge` — "Task Template" badge
- `template-mini-toolbar` — simplified toolbar
- `template-side-panel` — 280px side panel
- `template-side-panel-metadata` — metadata section
- `template-side-panel-actor-slots` — actor slots section
- `template-side-panel-io` — I/O interface section
- `template-io-input-{name}` — input port row
- `template-io-output-{name}` — output port row
- `template-save-btn` — save button

---

### STEP-69: Picker Components — RolePicker, SchemaPicker, PluginPicker, TemplateBrowser

**Objective:** Build the 4 picker components that SF-6's editor inspectors consume. These are the integration surface between SF-7 libraries and SF-6 editor — they fetch data from library API endpoints and render picker UIs within inspector windows. All imports use SF-6's confirmed module paths [C-2].

**Requirement IDs:** J-16, J-18, J-23, J-24, J-26
**Journey IDs:** J-16, J-18, J-23, J-24, J-26, J-27

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/pickers/RolePicker.tsx` | create |
| `features/libraries/pickers/SchemaPicker.tsx` | create |
| `features/libraries/pickers/PluginPicker.tsx` | create |
| `features/libraries/pickers/TemplateBrowser.tsx` | create |
| `features/editor/inspector/AskInspector.tsx` | read — understands picker integration point [C-2 confirmed path] |
| `features/editor/inspector/PluginInspector.tsx` | read — understands picker integration point [C-2 confirmed path] |
| `features/editor/ui/NodePalette.tsx` | read — understands TemplateBrowser integration [C-2 confirmed path] |

**Instructions:**

**1. `RolePicker.tsx` — Drag-target slot in AskInspector (CMP-131)**

```typescript
interface RolePickerProps {
  assignedRole: { id?: string; name: string; model: string } | null;
  inlineRole: { name: string; model: string; system_prompt: string } | null;
  onAssign: (role: RoleEntity) => void;
  onRemove: () => void;
  onCreateInline: () => void;
  onOpenFullEditor: (roleId?: string) => void;
  readOnly?: boolean;
}
```

Renders three modes:
- **Empty**: Dropdown button "Select Role" + "+ Inline" button. Dropdown fetches `GET /api/roles` and lists all roles (user + examples). Click assigns.
- **Library role assigned**: Role chip (name + model badge) + "Change" button + "x" remove + "Open Full Editor" link (navigates to `/roles/{id}`)
- **Inline role assigned**: Role chip with "inline" indicator + "Save to Library" button (triggers PromotionDialog) + "x" remove + "Open Full Editor" link (opens RoleEditorView in new tab/route)

The picker also supports the drag-and-drop slot from the palette [D-42, D-43] — this is handled by the AskInspector's ActorSlotControl which renders this picker.

**2. `SchemaPicker.tsx` — Dropdown + "Create Inline" (CMP-132)**

```typescript
interface SchemaPickerProps {
  value: string | null; // schema ID or null
  inlineSchema: Record<string, unknown> | null;
  onSelect: (schemaId: string) => void;
  onCreateInline: () => void;
  onSaveToLibrary: (schema: Record<string, unknown>) => void;
  readOnly?: boolean;
}
```

Dropdown with two sections:
- "Library Schemas" — fetched from `GET /api/schemas`, shows name + property count
- "Create Inline" — option at bottom that triggers inline schema builder in AskInspector [D-26]

When inline schema exists: shows "Save to Library" button that opens PromotionDialog → POST /api/schemas.

**3. `PluginPicker.tsx` — Grouped dropdown (CMP-133)**

```typescript
interface PluginPickerProps {
  value: { type: 'type' | 'instance'; id: string } | null;
  onSelect: (selection: { type: 'type' | 'instance'; id: string }) => void;
  readOnly?: boolean;
}
```

Grouped dropdown:
- "Configured Instances" — fetched from `GET /api/plugins/instances`, shows name + type badge
- "Plugin Types" — fetched from `GET /api/plugins/types`, shows name + category badge
- Divider between sections

Selecting a type creates a PluginNode with `plugin_ref` set. Selecting an instance creates a PluginNode with `instance_ref` set and config pre-populated.

**4. `TemplateBrowser.tsx` — Palette section for templates (CMP-134)**

```typescript
interface TemplateBrowserProps {
  onDragStart: (templateId: string, templateData: TaskTemplateEntity) => void;
}
```

Renders as a section within SF-6's NodePalette (confirmed path: `features/editor/ui/NodePalette.tsx` [C-2]). Shows template icons with hover tooltips (name + description). Drag from palette → drop on canvas stamps template as read-only TemplateGroup [D-25].

Fetches templates from `GET /api/templates`. Renders each as a PaletteItem (icon + tooltip). Uses SF-6's drag-and-drop system via `useDragAndDrop` hook.

**Acceptance Criteria:**
- AskInspector actor section shows RolePicker with dropdown of all library roles
- Select role from dropdown → actor slot fills, node card updates
- Click "+ Inline" → InlineRoleCreator opens (SF-6 component), role assigned but NOT in library
- Click "Save to Library" on inline role → PromotionDialog → POST /api/roles → role now in dropdown
- SchemaPicker shows library schemas + "Create Inline" option
- PluginPicker shows grouped dropdown with instances first, types second
- TemplateBrowser shows template icons in palette, drag to stamp on canvas
- All pickers show loading spinners while fetching, handle network errors with retry

**Counterexamples:**
- Do NOT fetch roles/schemas/plugins on every render — cache with stale-while-revalidate pattern
- Do NOT allow drag from sidebar tree — palette is sole drag source [D-43]
- Do NOT show templates as editable on workflow canvas — read-only [D-25]
- Do NOT combine types and instances in a flat list — grouped sections [D-41]
- Do NOT import from `features/editor/inspectors/` (plural) — use `features/editor/inspector/` [C-2]
- Do NOT import from `features/editor/palette/` — use `features/editor/ui/` [C-2]

**Citations:**
- [decision: D-SF7-5] — SF-6 confirmed module paths [C-2]
- [code: features/editor/inspector/AskInspector.tsx] — Picker integration point
- [code: features/editor/ui/NodePalette.tsx] — TemplateBrowser integration point

**data-testid assignments:**
- `role-picker` — role picker container
- `role-picker-dropdown` — role dropdown button
- `role-picker-option-{id}` — dropdown option
- `role-picker-inline-btn` — "+ Inline" button
- `role-picker-assigned` — assigned role chip
- `role-picker-remove-btn` — remove (x) button
- `role-picker-save-to-library-btn` — "Save to Library" button
- `role-picker-full-editor-link` — "Open Full Editor" link
- `schema-picker` — schema picker container
- `schema-picker-dropdown` — dropdown
- `schema-picker-option-{id}` — dropdown option
- `schema-picker-create-inline` — "Create Inline" option
- `schema-picker-save-to-library-btn` — "Save to Library"
- `plugin-picker` — plugin picker container
- `plugin-picker-dropdown` — dropdown
- `plugin-picker-group-instances` — instances group
- `plugin-picker-group-types` — types group
- `plugin-picker-option-{id}` — dropdown option
- `template-browser` — palette section
- `template-browser-item-{id}` — draggable template icon

---

### STEP-70: Promotion Dialog + Cross-Feature Integration

**Objective:** Build the PromotionDialog for inline-to-library promotion (D-38), and wire all SF-7 picker components into SF-6's editor inspectors. This step connects the library system to the editor via the picker interfaces and mutation callbacks (onPromoteRole, onPromoteSchema, onSaveTemplate). All imports use SF-6's confirmed module paths [C-2]. PromotionDialog references SF-1's validation error codes for user messaging [D-SF7-6].

**Requirement IDs:** J-27, J-20
**Journey IDs:** J-18, J-20, J-27

**Scope:**
| Path | Action |
|------|--------|
| `features/libraries/shared/PromotionDialog.tsx` | create |
| `features/libraries/shared/PromotionPreview.tsx` | create |
| `features/editor/inspector/AskInspector.tsx` | modify — integrate RolePicker, SchemaPicker [C-2 confirmed path] |
| `features/editor/inspector/PluginInspector.tsx` | modify — integrate PluginPicker [C-2 confirmed path] |
| `features/editor/ui/NodePalette.tsx` | modify — integrate TemplateBrowser section [C-2 confirmed path] |
| `features/editor/inspector/InspectorActions.tsx` | read — understand SaveAsTemplate integration point [C-2] |
| `features/libraries/types.ts` | read — ENTITY_VALIDATION_CODE_MAP for messaging |

**Instructions:**

**1. `PromotionDialog.tsx` — Save-to-library dialog (CMP-129) [D-SF7-6]**

```typescript
interface PromotionDialogProps {
  entityType: 'role' | 'schema';
  data: Partial<RoleEntity> | Partial<OutputSchemaEntity>;
  onSave: (saved: LibraryEntity) => void;
  onClose: () => void;
}
```

XP-style modal with:
- Name input (with duplicate check on blur via `useDuplicateNameCheck`)
- PromotionPreview showing read-only summary of the entity being promoted
- Informational note (muted text): "Promoting this {entity} to the library makes it reusable across workflows and clears any `{code}` validation warnings for nodes referencing it." where `{code}` is from `ENTITY_VALIDATION_CODE_MAP` (e.g., `invalid_actor_ref` for roles, `invalid_type_ref` for schemas) [D-SF7-6]. Rendered in monospace `<code>` tag.
- Save button (disabled until name is valid and available)
- Cancel button

On save: POST to entity endpoint with `promote: true` flag. On success: calls `onSave(savedEntity)` which lets the editor update its reference from inline to library ID. Shows green toast "Role saved to library".

**2. `PromotionPreview.tsx` — Read-only config summary (CMP-130)**

Shows a condensed view of the entity being promoted:
- **Role**: name, model, system prompt (truncated), tool count
- **Schema**: name, property count, required fields

**3. Integration: AskInspector ← RolePicker + SchemaPicker [C-2]**

Modify SF-6's `features/editor/inspector/AskInspector.tsx` (confirmed path [C-2]) to import and render:
- `RolePicker` in the Actor section (replacing placeholder dropdown)
- `SchemaPicker` in the Output Schema section (replacing placeholder)

Wire callbacks:
- `onAssign(role)` → `updateNodeData(nodeId, { actor: role.name })`
- `onRemove()` → `updateNodeData(nodeId, { actor: undefined, inline_role: undefined })`
- `onCreateInline()` → shows InlineRoleCreator (existing SF-6 component at `features/editor/inspector/InlineRoleCreator.tsx`)
- `onSaveToLibrary(inlineRole)` → opens PromotionDialog → on success, updates node data to reference library role

**4. Integration: PluginInspector ← PluginPicker [C-2]**

Modify SF-6's `features/editor/inspector/PluginInspector.tsx` (confirmed path [C-2]) to import and render `PluginPicker`. On select: `updateNodeData(nodeId, { plugin_ref: id })` or `updateNodeData(nodeId, { instance_ref: id, plugin_config: instance.config })`.

**5. Integration: NodePalette ← TemplateBrowser [C-2]**

Modify SF-6's `features/editor/ui/NodePalette.tsx` (confirmed path [C-2]) to add a "Templates" section below "Primitives". Import `TemplateBrowser` component. Wire `onDragStart` to SF-6's `useDragAndDrop` handler for template stamping.

**6. Integration: SaveAsTemplate → POST /api/templates**

The "Save as Template" action in SF-6's `features/editor/inspector/InspectorActions.tsx` opens a dialog. Wire it to call `POST /api/templates` with:
```json
{
  "name": "...",
  "description": "...",
  "yaml_content": "...(serialized selected nodes/edges)"
}
```

On success: refresh TemplateBrowser palette section, show green toast.

**Acceptance Criteria:**
- In AskInspector: actor section shows RolePicker with full dropdown + inline creation
- Create inline role in editor → "Save to Library" button appears → click → PromotionDialog → enter name → save → role in library, node reference updated
- PromotionDialog shows note about `invalid_actor_ref` validation code for roles
- In AskInspector: output schema section shows SchemaPicker with library schemas + inline creation
- In PluginInspector: shows PluginPicker with grouped instances/types
- In NodePalette: "Templates" section shows library templates as draggable icons
- Drag template from palette → stamp read-only on canvas → appears in palette
- Multi-select nodes in editor → "Save as Template" in inspector → dialog → save → template in palette + library

**Counterexamples:**
- Do NOT duplicate PromotionDialog logic in SF-6 — SF-7 owns it, SF-6 calls it
- Do NOT allow promoting the same inline role twice — POST with `promote: true` returns existing if duplicate name
- Do NOT remove the inline role from the node on promotion — node keeps reference, now backed by library [D-27 J-27]
- Do NOT force a full page refresh after promotion — invalidate SWR cache, pickers auto-update
- Do NOT import from `features/editor/inspectors/` (plural) — use `features/editor/inspector/` [C-2]
- Do NOT import from `features/editor/palette/` — use `features/editor/ui/` [C-2]
- Do NOT import from `features/editor/dialogs/` — SaveAsTemplate action is in `features/editor/inspector/InspectorActions.tsx` [C-2]
- Do NOT use deprecated validation code names (e.g., `missing_actor`) in PromotionDialog messaging — use SF-1 authoritative codes [D-SF7-6]

**Citations:**
- [decision: D-SF7-5] — SF-6 confirmed module paths [C-2]
- [decision: D-SF7-6] — SF-1 authoritative validation error codes
- [code: features/editor/inspector/AskInspector.tsx] — Picker integration target
- [code: features/editor/inspector/PluginInspector.tsx] — Picker integration target
- [code: features/editor/ui/NodePalette.tsx] — TemplateBrowser integration target
- [code: features/libraries/types.ts] — ENTITY_VALIDATION_CODE_MAP

**data-testid assignments:**
- `promotion-dialog` — dialog container
- `promotion-dialog-name-input` — name input
- `promotion-dialog-name-error` — duplicate name error
- `promotion-dialog-preview` — entity preview section
- `promotion-dialog-validation-note` — SF-1 validation code note
- `promotion-dialog-save-btn` — save button (disabled when invalid)
- `promotion-dialog-cancel-btn` — cancel button

---

## Architectural Risks

| ID | Risk | Severity | Mitigation | Affected Steps |
|----|------|----------|------------|----------------|
| RISK-97 | YAML parsing on delete may be slow if user has >100 workflows with large YAML | Low | Parse is in-memory string processing; even 100 workflows at 50KB each = 5MB total. If perf becomes issue, add materialized table later. | STEP-63 |
| RISK-98 | TaskTemplateEditorView must use isolated store via `createEditorStore()` factory — singleton leakage would corrupt workflow editor state | Medium | [H-5] enforced via `createEditorStore({ scopedMode: true })` in `useRef`. Counterexample explicitly forbids importing `useEditorStore` singleton. `scopedMode` disables phase-specific actions. Store garbage-collected on unmount. | STEP-68 |
| RISK-99 | PluginInstanceForm auto-generation from JSON Schema may not cover all schema patterns (allOf, oneOf, conditional) | Medium | Support core types (string, number, boolean, object, array, enum). For complex schemas, fall back to raw JSON CodeMirror editor. Document unsupported patterns. | STEP-67 |
| RISK-100 | Picker data freshness — role created in library not immediately visible in editor's RolePicker | Low | Use SWR/React Query with short stale time (5s). PromotionDialog success callback manually invalidates picker cache. | STEP-69, STEP-70 |
| RISK-101 | Inline-to-library promotion race condition — user promotes same inline role in two editor windows | Low | Backend idempotent promotion (POST with `promote: true`) handles this — returns existing if duplicate. | STEP-63, STEP-70 |
| RISK-102 | SF-6 module path drift — if SF-6 renames paths, SF-7 imports break | Low | [C-2] confirmed paths documented in decision log table. SF-7 imports only from confirmed exports. Path changes require coordinated update. | STEP-66, STEP-68, STEP-69, STEP-70 |

---

## Journey Verifications

### J-23: Create and Use a Role

| Step | Action | Verify |
|------|--------|--------|
| 1 | Navigate to `/roles` | **Browser:** `expect: "[data-testid='roles-list-page'] visible"` |
| 2 | Click "+ New Role" | **Browser:** `expect: "[data-testid='role-editor'] visible, [data-testid='role-step-1'] active"` |
| 3 | Type "test-pm" in name field | **Browser:** `expect: "[data-testid='role-name-input'] value is 'test-pm'"` |
| 4 | Tab out of name field | **API:** `expect: "GET /api/roles/check-name?name=test-pm returns { available: true }"` |
| 5 | Select Claude Opus 4 model | **Browser:** `expect: "[data-testid='role-model-picker'] contains 'Claude Opus 4'"` |
| 6 | Click Next → Step 2 | **Browser:** `expect: "[data-testid='role-step-2'] active, [data-testid='role-prompt-editor'] visible"` |
| 7 | Type system prompt | **Browser:** `expect: "[data-testid='role-prompt-editor'] has content"` |
| 8 | Click Next → Step 3 | **Browser:** `expect: "[data-testid='role-tools-grid'] visible"` |
| 9 | Check Read, Write, Bash | **Browser:** `expect: "[data-testid='role-tool-chip-Read'] checked, [data-testid='role-tool-chip-Write'] checked, [data-testid='role-tool-chip-Bash'] checked"` |
| 10 | Click Next → Step 4 | **Browser:** `expect: "[data-testid='role-review-summary'] shows name, model, prompt preview, 3 tools"` |
| 11 | Click Save | **API:** `expect: "POST /api/roles returns 201 with { id, name: 'test-pm' }"` |
| 12 | Toast appears | **Browser:** `expect: "Toast 'Role created' visible with success variant"` |
| 13 | Role in list | **Browser:** `expect: "[data-testid='library-grid-card-{id}'] visible in roles list"` |

### J-24: Create Output Schema with Live Preview

| Step | Action | Verify |
|------|--------|--------|
| 1 | Navigate to `/schemas` | **Browser:** `expect: "[data-testid='schemas-list-page'] visible"` |
| 2 | Click "+ New Schema" | **Browser:** `expect: "[data-testid='schema-editor'] visible"` |
| 3 | Enter name "PRD" | **Browser:** `expect: "[data-testid='schema-name-input'] value is 'PRD'"` |
| 4 | Edit JSON Schema in CodeMirror | **Browser:** `expect: "[data-testid='schema-code-editor'] has valid JSON content"` |
| 5 | Wait 500ms | **Browser:** `expect: "[data-testid='schema-preview-tree'] renders property tree, [data-testid='schema-preview-valid-badge'] visible"` timeout: 1000 |
| 6 | Type invalid JSON | **Browser:** `expect: "[data-testid='schema-preview-error-banner'] visible"` |
| 7 | Fix JSON | **Browser:** `expect: "[data-testid='schema-preview-valid-badge'] visible, [data-testid='schema-preview-error-banner'] hidden"` |
| 8 | Click Save | **API:** `expect: "POST /api/schemas returns 201"` |

### J-26: Configure and Use a Plugin

| Step | Action | Verify |
|------|--------|--------|
| 1 | Navigate to `/plugins` | **Browser:** `expect: "[data-testid='plugins-list-page'] visible, [data-testid='plugins-types-section'] visible"` |
| 2 | Click built-in type card | **Browser:** `expect: "[data-testid='plugin-type-detail'] visible, [data-testid='plugin-type-badge-builtin'] visible"` |
| 3 | Click "Create Instance" | **Browser:** `expect: "[data-testid='plugin-instance-form'] visible"` |
| 4 | Fill config fields | **Browser:** `expect: "[data-testid='plugin-instance-form-config'] has filled fields"` |
| 5 | Click Save | **API:** `expect: "POST /api/plugins/instances returns 201 with { plugin_type_id: ... }"` |
| 6 | Instance in list | **Browser:** `expect: "[data-testid='plugin-instance-card-{id}'] visible in instances section"` |

### J-27: Promote Inline Role to Library

| Step | Action | Verify |
|------|--------|--------|
| 1 | In AskInspector, click "Save to Library" on inline role | **Browser:** `expect: "[data-testid='promotion-dialog'] visible"` |
| 2 | Enter name "promoted-pm" | **Browser:** `expect: "[data-testid='promotion-dialog-name-input'] value is 'promoted-pm'"` |
| 3 | Verify validation note | **Browser:** `expect: "[data-testid='promotion-dialog-validation-note'] contains 'invalid_actor_ref'"` |
| 4 | Tab out — name check | **API:** `expect: "GET /api/roles/check-name?name=promoted-pm returns { available: true }"` |
| 5 | Click Save | **API:** `expect: "POST /api/roles with promote:true returns 201"` |
| 6 | Toast appears | **Browser:** `expect: "Toast 'Role saved to library' visible"` |
| 7 | Role in picker | **Browser:** `expect: "[data-testid='role-picker-option-{id}'] visible in dropdown"` timeout: 2000 |

### J-29: Library Failure Paths — Delete Referenced

| Step | Action | Verify |
|------|--------|--------|
| 1 | Try to delete role referenced by workflow | **API:** `expect: "DELETE /api/roles/{id} returns 409 { error: 'referenced', count: 1, affected_validation_codes: ['invalid_actor_ref'] }"` |
| 2 | Dialog shows | **Browser:** `expect: "[data-testid='entity-delete-dialog'] visible, [data-testid='entity-delete-dialog-ref-count'] contains '1 workflow'"` |
| 3 | Validation code shown | **Browser:** `expect: "[data-testid='entity-delete-dialog-validation-code'] contains 'invalid_actor_ref'"` |
| 4 | Only Close button | **Browser:** `expect: "[data-testid='entity-delete-dialog'] has only Close button, no Delete"` |

---

## Test ID Registry

```
library-grid
library-grid-card-{id}
library-toolbar
library-toolbar-new-btn
library-toolbar-view-toggle
library-empty-state
library-empty-state-new-btn
library-empty-state-example-btn
library-section-mine
library-section-examples
example-badge
step-indicator
step-indicator-step-{n}
step-nav-back
step-nav-next
tip-callout
entity-delete-dialog
entity-delete-dialog-ref-count
entity-delete-dialog-ref-list
entity-delete-dialog-validation-code
roles-list-page
role-editor
role-editor-header
role-step-1
role-step-2
role-step-3
role-step-4
role-name-input
role-name-error
role-model-picker
role-model-option-{tier}
role-prompt-editor
role-tools-grid
role-tool-chip-{name}
role-review-summary
role-save-btn
schemas-list-page
schema-editor
schema-name-input
schema-description-input
schema-validate-btn
schema-save-btn
schema-code-editor
schema-preview-tree
schema-preview-property-{name}
schema-preview-valid-badge
schema-preview-error-banner
schema-dual-pane-divider
plugins-list-page
plugins-types-section
plugins-instances-section
plugin-type-card-{id}
plugin-type-badge-builtin
plugin-type-badge-custom
plugin-type-detail
plugin-type-detail-create-instance-btn
plugin-instance-card-{id}
plugin-instance-form
plugin-instance-form-name
plugin-instance-form-config
plugin-instance-form-save-btn
plugin-type-editor
plugin-type-editor-name
plugin-type-editor-io-inputs
plugin-type-editor-io-outputs
plugin-type-editor-config-schema
plugin-implementation-banner
io-list-add-btn
io-list-row-{index}
templates-list-page
template-editor
template-wizard-dialog
template-wizard-step-1
template-wizard-step-2
template-wizard-step-3
template-wizard-name-input
template-wizard-slot-add-btn
template-wizard-slot-{index}
template-wizard-create-btn
template-canvas
template-scale-badge
template-mini-toolbar
template-side-panel
template-side-panel-metadata
template-side-panel-actor-slots
template-side-panel-io
template-io-input-{name}
template-io-output-{name}
template-save-btn
role-picker
role-picker-dropdown
role-picker-option-{id}
role-picker-inline-btn
role-picker-assigned
role-picker-remove-btn
role-picker-save-to-library-btn
role-picker-full-editor-link
schema-picker
schema-picker-dropdown
schema-picker-option-{id}
schema-picker-create-inline
schema-picker-save-to-library-btn
plugin-picker
plugin-picker-dropdown
plugin-picker-group-instances
plugin-picker-group-types
plugin-picker-option-{id}
template-browser
template-browser-item-{id}
promotion-dialog
promotion-dialog-name-input
promotion-dialog-name-error
promotion-dialog-preview
promotion-dialog-validation-note
promotion-dialog-save-btn
promotion-dialog-cancel-btn
```

---