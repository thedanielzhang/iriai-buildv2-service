<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

SF-7 is scoped to the library extension layer on top of SF-5's five-table compose foundation in `tools/compose`. The design centers four launch-critical capabilities: role/schema/template delete preflight backed by persisted workflow references, Tool Library CRUD with role-aware delete blocking, task-template `actor_slots` persistence UX, and explicit auth/validation feedback for 404/413/422 outcomes. Plugin-library surfaces, promotion flows, validation-code chips, and stale legacy scope are removed. Component paths are relative to `tools/compose/frontend/src/` — not the deprecated `tools/iriai-workflows` topology.

SF-6 save/load contract alignment: SF-7's `workflow_entity_refs` reference index is entirely downstream of SF-6's blocking schema bootstrap gate. Library pages load and operate independently of whether any workflow canvas is bootstrapped. `workflow_entity_refs` rows are only written when SF-6's bootstrap-gated save path completes — pre-bootstrap editor state and unsaved canvas changes never count as persisted references. Only the three atomic node types SF-6 supports for direct placement — Ask, Branch, and Plugin — can create library entity references. SwitchFunctionEditor and ErrorFlowNode do not exist in SF-6 and cannot generate reference rows. Synthetic root phase normalization means the implicit root container is a serialization artifact, not a user-placed node; it never creates actor, schema, or tool reference rows. Cross-phase edges stored at the workflow root in SF-6's nested YAML are included in the save path's reference scan, so Ask nodes connecting across phases via workflow-root edges still register actor references correctly.

---

## Journey Annotations

<!-- SF: libraries-registries -->
### J-1 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 renders a shared LibraryCollectionPage with XP cards, search, and a sticky New Role action; warm-cache data stays visible while React Query silently refreshes in the background.
- Step 2 opens RoleEditorForm in the content pane, with built-in tools grouped above registered tools so the user can distinguish permanent catalog items from editable custom tools. Validation stays inline at the field row and the save bar remains disabled until required fields are valid.
- Step 3 uses optimistic route continuity but not optimistic entity creation: success is confirmed only after the role save returns and the cached role list plus Ask-node picker queries are invalidated.

**Error Path UX:** If the role save fails with 422 or 413, the editor stays open, focus moves to the first invalid field or the banner heading, and no success toast is shown. If a detail route resolves to a cross-user or deleted role, the pane renders a neutral not-found state rather than an access-denied message.

**Empty State UX:** The Roles list empty state shows a short explainer, 'Create your first role', and a secondary 'Browse tools' hint so the user understands why the tools checklist will be useful once creation starts.

**NOT Criteria:**
- The Roles list must NOT blank the whole page during background refetch.
- Role save must NOT create duplicates from repeated clicks while a request is pending.
- The Role editor must NOT merge built-in and custom tools into one unlabeled checklist.

<!-- SF: libraries-registries -->
### J-2 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 opens EntityDeleteDialog in a loading/checking state immediately on user intent; the modal body reserves space for the workflow list so the dialog does not jump when the preflight response arrives.
- Step 1 reference resolution: the blocked-by-workflows list comes from workflow_entity_refs rows written during SF-6's bootstrap-gated save. Workflows open in the editor but not yet saved do not count as active references; dialog copy must say 'saved workflows' not 'open workflows' and must not suggest closing the editor resolves the block.
- Step 2 assumes that clearing the block requires a workflow save in SF-6 (which only runs after bootstrap succeeds). Delete copy explicitly directs the user back to the editor for a save rather than expecting unsaved canvas changes to count.
- Step 3 reruns the preflight check every time the dialog reopens. A zero-reference result reveals the destructive action; a stale 409 after confirm rehydrates the blocked-workflows state using the server payload instead of a generic toast.

**Error Path UX:** Preflight transport failures keep the modal open with Retry and Close actions; Delete remains disabled. Server-side delete races reuse the same workflow-list presentation rather than switching to a different modal pattern.

**Empty State UX:** When no workflows reference the role, the modal uses concise recoverability copy and shows only Cancel plus Delete role.

**NOT Criteria:**
- The role delete flow must NOT call DELETE merely to discover references.
- The blocked state must NOT claim that unsaved SF-6 editor state or pre-bootstrap canvas state counts as a persisted reference.
- Synthetic root phase containers in SF-6's serialized YAML must NOT appear as reference sources in the blocked-by-workflows list.
- The blocked state must NOT show validation-code chips or plugin-specific warnings outside the current SF-7 scope.
- Cross-phase edges stored at the workflow root in SF-6's save format MUST count as persisted references once the save path completes; they must NOT be silently excluded from the reference scan.

<!-- SF: libraries-registries -->
### J-3 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 reuses EntityDeleteDialog, but the blocked state swaps workflow names for role names because tool usage is role-local rather than workflow-backed.
- Step 2 depends on persisted role saves, not local checklist edits. The dialog copy and empty/loading states stay symmetric with J-2 so the user does not need to learn a second delete pattern.
- Step 3 invalidates both the tools list and any cached role-editor tool checklist query so the deleted tool disappears the next time a role form mounts.

**Error Path UX:** Tool reference-check failures show retryable inline error copy in the dialog. Tool save or delete failures surface a toast plus inline banner in the tool editor if the detail pane is open.

**Empty State UX:** If the user has no custom tools, the Tools page empty state shows the built-in catalog first and a Register custom tool CTA second so the page still feels useful before any CRUD records exist.

**NOT Criteria:**
- Tool deletion must NOT read from workflow_entity_refs.
- The blocked tool delete state must NOT show workflow names.
- The Role editor must NOT continue showing deleted custom tools after a successful invalidate-and-refetch cycle.

<!-- SF: libraries-registries -->
### J-4 — SF-7: Libraries & Registries

**Step Annotations:**
- Step 1 uses ActorSlotsEditor as a structured repeater embedded beside the task-template canvas summary. Each slot row collects slot name, actor type constraint, and optional default role in one compact row with add/remove controls.
- Step 2 confirms persistence through a full page refresh path, so success feedback includes a short note that actor slots were saved to the reusable template definition, not just the current browser session.

**Error Path UX:** Duplicate slot names, blank slot names, and invalid default-role bindings are shown inline on the row plus in a summary banner above the table. Save remains disabled until row-level issues are cleared.

**Empty State UX:** A first-time template with no actor slots renders a dashed empty panel titled 'No actor slots defined yet' with a primary 'Add actor slot' button and helper text explaining where slot bindings are used.

**NOT Criteria:**
- Actor slots must NOT exist only in local canvas state.
- Save success must NOT omit persisted actor_slots from the follow-up detail response.
- The actor-slot editor must NOT allow duplicate slot names to appear valid.

<!-- SF: libraries-registries -->
### CMP-28: EntityDeleteDialog
<!-- SF: libraries-registries — Original ID: CMP-133 -->

- **Status:** new
- **Location:** `features/libraries/shared/EntityDeleteDialog.tsx`
- **Description:** Shared alert dialog for delete preflight and confirmation across roles, schemas, templates, and tools. Starts in reference-check state, resolves to blocked-by-workflows, blocked-by-roles, confirm-delete, or retryable error. Roles/schemas/templates show saved workflow names from workflow_entity_refs (populated only by SF-6 bootstrap-gated saves); tools show saved role names. Copy always says 'saved workflows' to reflect that pre-bootstrap/unsaved editor state never counts.
- **Props/Variants:** `entityType ('role' | 'schema' | 'template' | 'tool'), entityName, isOpen, checkState ('loading' | 'blocked-workflows' | 'blocked-roles' | 'ready' | 'error'), referenceNames[], onRetry, onClose, onConfirmDelete`
- **States:** checking-references, blocked-by-workflows, blocked-by-roles, confirm-delete, reference-check-error
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:19` — "Roles, Schemas, and Task Templates must use a pre-delete reference check backed by the canonical workflow_entity_refs junction table." — The dialog contract is defined by the PRD's delete preflight requirement.
  - [decision] `D-GR-26` — "workflow_entity_refs is the canonical persisted reference model; only SF-6 bootstrap-gated saves write to it." — The blocked-by-workflows variant must reflect only post-bootstrap saved state, not pre-bootstrap or unsaved editor state.
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/workflow-editor/prd.md:REQ-7` — "Editor bootstrap waits for /api/schema/workflow before enabling save/import/validate." — SF-6's bootstrap gate is what controls when workflow_entity_refs rows are written; this bounds what the dialog can show.

### CMP-29: LibraryCollectionPage
<!-- SF: libraries-registries — Original ID: CMP-134 -->

- **Status:** new
- **Location:** `features/libraries/shared/LibraryCollectionPage.tsx`
- **Description:** Reusable list shell for Roles, Output Schemas, Task Templates, and Tools. Combines search, primary create action, XP card/list layout, stale-while-revalidate loading treatment, and inline empty/error states. Loads independently of SF-6 bootstrap state.
- **Props/Variants:** `entityType ('roles' | 'schemas' | 'templates' | 'tools'), viewMode ('grid' | 'list'), items[], queryState ('loading' | 'empty' | 'error' | 'ready'), selectedId?, onCreate, onSelect, onSearch`
- **States:** loading, empty, error, populated
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:21` — "The Tool Library remains a full CRUD library page with list, detail, and editor views." — PRD defines the full library CRUD surface that this component shells.
  - [research] `TanStack Query important defaults` — "Stale queries refetch in the background." — Stale-while-revalidate pattern keeps list visible during refresh.

### CMP-30: RoleEditorForm
<!-- SF: libraries-registries — Original ID: CMP-135 -->

- **Status:** new
- **Location:** `features/libraries/roles/RoleEditorForm.tsx`
- **Description:** Form-based role editor for name, model, prompt, metadata, and grouped tool checklist. Uses inline validation for bad names and oversized payloads, shows built-in tools in a locked section, and refreshes downstream role-picker caches after save.
- **Props/Variants:** `mode ('create' | 'edit'), roleDraft, builtInTools[], customTools[], validationState ('idle' | 'invalid' | 'saving' | 'saved'), onSave, onDelete, onCancel`
- **States:** draft, invalid, saving, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:48` — "The Role editor shows built-in plus registered tools in separate groups." — PRD requires grouped tool display to distinguish permanent catalog items from editable custom tools.

### CMP-31: ToolEditorForm
<!-- SF: libraries-registries — Original ID: CMP-136 -->

- **Status:** new
- **Location:** `features/libraries/tools/ToolEditorForm.tsx`
- **Description:** Tool CRUD editor for custom tools only. Captures name, source, description, and input schema. Delete entry points route through EntityDeleteDialog so tools can be blocked by role references before deletion.
- **Props/Variants:** `mode ('create' | 'edit'), toolDraft, validationState ('idle' | 'invalid' | 'saving' | 'saved'), onSave, onDelete, onCancel`
- **States:** draft, invalid, saving, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:34` — "Tool delete is blocked with referencing role names until references are removed." — Tool delete must route through EntityDeleteDialog's blocked-by-roles state before mutation.

### CMP-32: ActorSlotsEditor
<!-- SF: libraries-registries — Original ID: CMP-137 -->

- **Status:** new
- **Location:** `features/libraries/templates/ActorSlotsEditor.tsx`
- **Description:** Structured repeater for task-template actor_slots. Each row edits slot name, actor type constraint, and optional default role, with row-level validation and a compact summary for persisted reuse.
- **Props/Variants:** `slots[], availableRoles[], editorState ('empty' | 'editing' | 'invalid' | 'saved'), onAddSlot, onUpdateSlot, onRemoveSlot`
- **States:** empty, populated, invalid, saved
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:22` — "custom_task_templates must persist actor_slots through migration and API support." — PRD requirement for round-trip actor_slots persistence drives this component's save contract.
  - [decision] `D-GR-29` — "The actor-slot editor belongs in SF-7's extension layer, not SF-5's five-table foundation." — Scope boundary decision places this component in SF-7.

### CMP-33: ResourceStateCard
<!-- SF: libraries-registries — Original ID: CMP-138 -->

- **Status:** new
- **Location:** `features/libraries/shared/ResourceStateCard.tsx`
- **Description:** Route-level fallback surface for loading, not found, server error, and request-validation states. Used by library detail panes and list routes so auth scoping and validation failures are explicit without exposing cross-user existence information.
- **Props/Variants:** `tone ('neutral' | 'error' | 'warning'), state ('loading' | 'not-found' | 'error' | 'validation'), title, body, actionLabel?, onAction?`
- **States:** loading, not-found, error, validation
- **Citations:**
  - [code] `.iriai/artifacts/features/beced7b1/subfeatures/libraries-registries/prd.md:24` — "Cross-user access attempts must return 404 rather than 403." — PRD's auth scoping requirement drives the not-found vs. forbidden presentation decision.

---

## Verifiable States

<!-- SF: libraries-registries -->
### CMP-28 (EntityDeleteDialog) States

| State | Visual Description |
|-------|-------------------|
| checking-references | XP modal titled 'Delete Code Review Lead?' with a spinner row labeled 'Checking references...'; destructive button is hidden and body height is reserved for later content. |
| blocked-by-workflows | Warning modal titled 'Can't delete role yet' with body copy including 'saved workflows' (not 'open workflows') and a stacked list of saved workflow names such as 'build-v2 / planning' and 'deploy-preview'. Footer shows only Close. |
| blocked-by-roles | Warning modal titled 'Can't delete tool yet' with a stacked list of role names such as 'code-review-lead' and 'qa-runner'; no workflow names visible. Footer shows only Close. |
| confirm-delete | Neutral modal with recovery copy such as 'This tool will be recoverable for 30 days.' No reference list rendered. Footer shows Cancel and a red Delete button. |
| reference-check-error | Modal body contains a red inline banner titled 'Couldn't verify references', a short explanation, and Retry plus Close actions; Delete is unavailable. |

### CMP-29 (LibraryCollectionPage) States

| State | Visual Description |
|-------|-------------------|
| loading | Content area shows a toolbar and a grid of XP-style skeleton cards; previous route chrome remains visible and no blocking overlay covers the shell. |
| empty | Centered empty card with heading like 'No custom tools yet', one primary CTA, and one short helper sentence. |
| error | Inline content card with a red icon, 'Couldn't load roles', and a Retry button; sidebar and toolbar stay visible. |
| populated | Search bar, create button, and at least one entity card row or grid render together, with selection highlighting on the active item. |

### CMP-30 (RoleEditorForm) States

| State | Visual Description |
|-------|-------------------|
| draft | Role form shows editable name/model/prompt fields and two tool groups titled 'Built-in tools' and 'Custom tools'; save bar is idle. |
| invalid | One or more field rows show red helper text, and a summary banner states 'Fix 2 issues before saving'; Save is disabled. |
| saving | Sticky footer save button shows spinner text such as 'Saving role...'; inputs are temporarily disabled. |
| saved | Success toast appears with 'Role saved', and the dirty-state badge disappears from the editor header. |

### CMP-31 (ToolEditorForm) States

| State | Visual Description |
|-------|-------------------|
| draft | Tool form shows Name, Source, Description, and Input schema sections with a destructive Delete tool action in the footer. |
| invalid | Input-schema or name validation shown inline with red helper text and the footer summary banner explains what failed. |
| saving | Footer save button shows spinner text such as 'Saving tool...'; the form remains visible in place. |
| saved | Success toast appears with 'Tool updated', and reopening a role editor later shows the updated tool description. |

### CMP-32 (ActorSlotsEditor) States

| State | Visual Description |
|-------|-------------------|
| empty | Dashed panel titled 'No actor slots defined yet' with an Add actor slot button and helper text about reusable role bindings. |
| populated | Table-like list of actor-slot rows showing slot name, actor type chip, default role dropdown value, and row delete control. |
| invalid | One or more rows show inline errors such as 'Slot names must be unique', and the header summary banner repeats the issue count. |
| saved | Compact success note beneath the section title reads 'Actor slots saved to template', and the rows remain visible after save completes. |

### CMP-33 (ResourceStateCard) States

| State | Visual Description |
|-------|-------------------|
| loading | XP card centered in the detail pane with spinner and message like 'Loading tool details...'. |
| not-found | Neutral card with heading 'Role not found', explanation that it may have been deleted, and a Back to Roles action. |
| error | Error card with heading 'Request failed', short retry guidance, and a primary Retry action. |
| validation | Warning card with heading 'Request rejected' and a short list of server-returned 413/422 issues. |

---

## Responsive Behavior

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries
Desktop-first and consistent with the compose shell at tools/compose. Under 1024px, SF-7 does not invent a separate mobile CRUD flow and instead relies on the existing compose unsupported-screen treatment. From 1024px to 1359px, list pages stay single-column within the content pane and detail editors stack metadata sections vertically. At 1360px and above, list/detail views can split into a wider two-column workspace, while delete dialogs grow from roughly 520px to 600px wide without changing their content model.

---

## Interaction Patterns

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

List data uses stale-while-revalidate behavior: cached entity lists render immediately, background refetch updates them silently, and create/update/delete mutations invalidate the relevant list plus dependent pickers or checklists. Delete is always two-step: opening the dialog triggers a reference preflight; only an unreferenced result reveals the destructive action. Confirm delete expects a server recheck to protect against races. Tool checklist updates are not optimistic; the UI waits for a successful tool mutation, then invalidates the role-editor query. Actor-slot editing is local until save, but the save response is the source of truth. Cross-user access is presented as not-found. Reference population alignment with SF-6 bootstrap: workflow_entity_refs is populated exclusively by SF-6's save path, which only runs after the blocking schema bootstrap from /api/schema/workflow completes. Library list pages and detail editors load independently and do not gate on SF-6's bootstrap state. The blocked-by-workflows dialog copy says 'saved workflows' so users know they must open and save the referencing workflow in SF-6 to clear the block. Cross-phase edges are part of SF-6's workflow-root save payload and do generate reference rows; they are not a separate path the reference scan can miss.

---

## Accessibility Notes

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries
EntityDeleteDialog uses role='alertdialog', moves focus to the title or first action on open, traps focus within the modal while open, and returns focus to the invoking delete button on close. Reference results are semantic lists, not comma-separated text, so screen readers can count blocked workflows or roles. Success toasts remain aria-live='polite' and error/warning toasts use alert semantics, matching the existing global toast pattern. Inline validation pairs field inputs with visible helper text and error summary banners, so duplicate actor-slot names or invalid tool names are announced in more than one place. Not-found and validation cards use explicit headings and do not rely on color alone to communicate 404 vs 422/413 differences.

---

## Alternatives Considered

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

1. Keep plugin-library and promotion surfaces in the design. Rejected because the current SF-7 PRD and review feedback do not trace them as launch scope.
2. Use DELETE 409 responses as the first time the user learns about references. Rejected because REQ-1 and REQ-2 require a non-destructive preflight.
3. Persist actor-slot definitions only inside unsaved canvas state and infer them later. Rejected because AC-2 requires round-trip persistence and reload visibility.
4. Show explicit 403 access denied messaging for cross-user detail routes. Rejected because REQ-6 requires 404 semantics to avoid leaking record existence.
5. Keep a view-only editor fallback when /api/schema/workflow is unavailable. Rejected because SF-6 requires a blocking bootstrap gate that disables the full editor (with retry affordance) on schema fetch failure. A view-only fallback would allow library entities to appear unreferenced while the schema is stale, producing incorrect delete-preflight results in SF-7.
6. Handle references from SwitchFunctionEditor or ErrorFlowNode node types in delete preflight. Rejected because SF-6's editor reset removes these node types entirely; only Ask, Branch, and Plugin are supported for direct canvas placement, so no reference rows from those removed types can ever reach workflow_entity_refs.
7. Treat the synthetic root phase container as a potential reference source. Rejected because SF-6's synthetic root is a serialization normalization artifact, not a user-placed node; it must not generate actor, schema, or tool reference rows, and the dialog must never show it as a referencing workflow.

---

## Rationale

<!-- SF: libraries-registries -->
### SF-7: Libraries & Registries

The previous SF-7 design drifted in three directions at once: legacy journey IDs, plugin-library contamination, and inconsistent delete/reference semantics. The revised PRD and plan-review cycles settle the boundary: SF-5 remains a five-table foundation, while SF-7 owns the reference-index extension and the library behaviors built on top of it. This revision additionally aligns SF-7's design to SF-6's editor reset contract. SF-6 owns the blocking bootstrap gate, synthetic root phase normalization, and three-atomic-node-type enforcement — SF-7 receives only the completed save/load payload downstream. The delete-preflight UX communicates that only persisted saves (post-bootstrap) write to workflow_entity_refs, synthetic root containers never appear as reference sources, and cross-phase edges at the workflow root ARE included in reference scans. SwitchFunctionEditor and ErrorFlowNode are removed from SF-6 entirely, so SF-7's design never needs to handle references from those node types. All component paths are anchored to tools/compose/frontend/src/ per the accepted topology.

---

## Component Summary

| ID | Name | Subfeature | Status | Location |
|----|------|------------|--------|----------|
| CMP-1 | EdgeDefinition | declarative-schema | new | iriai_compose/schema/edges.py |
| CMP-2 | BranchNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-3 | PhaseDefinition | declarative-schema | new | iriai_compose/schema/phases.py |
| CMP-4 | WorkflowConfig | declarative-schema | new | iriai_compose/schema/workflow.py |
| CMP-5 | AskNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-6 | PluginNode | declarative-schema | new | iriai_compose/schema/nodes.py |
| CMP-7 | MockAgentRuntime | testing-framework | extending | iriai_compose/runner.py |
| CMP-8 | MockInteractionRuntime | testing-framework | extending | iriai_compose/runner.py |
| CMP-9 | MockPluginRuntime | testing-framework | new | iriai_compose/testing/mock_plugin.py |
| CMP-10 | Node Card Reads Metadata | workflow-migration | extending | design artifact |
| CMP-11 | Tier 2 Mock Runtime Contract | workflow-migration | extending | design artifact |
| CMP-12 | Consumer Integration Boundary | workflow-migration | extending | design artifact |
| CMP-13 | SidebarTree | composer-app-foundation | new | tools/compose/frontend — shell layout |
| CMP-14 | NewDropdown | composer-app-foundation | new | tools/compose/frontend — toolbar |
| CMP-15 | GridCard | composer-app-foundation | new | tools/compose/frontend — Workflows view |
| CMP-16 | EditorSchemaBootstrapGate | composer-app-foundation | new | tools/compose/frontend — editor route |
| CMP-17 | YAMLContractErrorPanel | composer-app-foundation | new | tools/compose/frontend — feedback surfaces |
| CMP-18 | AskFlowNode | workflow-editor | new | features/editor/nodes/AskNode.tsx |
| CMP-19 | BranchFlowNode | workflow-editor | new | features/editor/nodes/BranchNode.tsx |
| CMP-20 | PluginFlowNode | workflow-editor | new | features/editor/nodes/PluginNode.tsx |
| CMP-21 | DataEdge | workflow-editor | new | features/editor/edges/DataEdge.tsx |
| CMP-22 | HookEdge | workflow-editor | new | features/editor/edges/HookEdge.tsx |
| CMP-23 | OutputPathsEditor | workflow-editor | new | features/editor/inspectors/OutputPathsEditor.tsx |
| CMP-24 | MergeFunctionEditor | workflow-editor | new | features/editor/inspectors/MergeFunctionEditor.tsx |
| CMP-25 | PortConditionRow | workflow-editor | new | features/editor/inspectors/PortConditionRow.tsx |
| CMP-26 | PhaseContainer | workflow-editor | new | features/editor/phases/PhaseContainer.tsx |
| CMP-27 | SchemaBootstrapGate | workflow-editor | new | features/editor/schema/SchemaBootstrapGate.tsx |
| CMP-28 | EntityDeleteDialog | libraries-registries | new | features/libraries/shared/EntityDeleteDialog.tsx |
| CMP-29 | LibraryCollectionPage | libraries-registries | new | features/libraries/shared/LibraryCollectionPage.tsx |
| CMP-30 | RoleEditorForm | libraries-registries | new | features/libraries/roles/RoleEditorForm.tsx |
| CMP-31 | ToolEditorForm | libraries-registries | new | features/libraries/tools/ToolEditorForm.tsx |
| CMP-32 | ActorSlotsEditor | libraries-registries | new | features/libraries/templates/ActorSlotsEditor.tsx |
| CMP-33 | ResourceStateCard | libraries-registries | new | features/libraries/shared/ResourceStateCard.tsx |