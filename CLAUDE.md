# iriai-build-v2

Agent orchestration build system using `iriai-compose` framework and Claude Agent SDK.

## iriai-compose Framework

- **Conceptual model**: Workflow → Phase → Task → Actor
- **Tasks**: `Ask` (one-shot), `Interview` (multi-turn), `Gate` (approval), `Choose` (selection), `Respond` (free-form)
- **Actors**: `AgentActor` (AI, resolved by `AgentRuntime`) with `Role` (prompt, tools, model, metadata) and `InteractionActor` (human, resolved by `InteractionRuntime`)
- **Feature**: execution context threading through workflow
- **Extension points**: `AgentRuntime` (must implement), `InteractionRuntime`, storage ABCs
- **`DefaultWorkflowRunner`**: resolves actors, merges context, manages sessions, runs parallel tasks
- **Structured output**: `output_type` on tasks → `AgentRuntime.invoke()` must return validated `BaseModel`
- **Session continuity**: every `AgentActor` gets `session_key = "{actor.name}:{feature.id}"` passed to runtime
- **Parallel safety**: same `AgentActor` must not appear in multiple parallel tasks (validated at runtime). Define separate actors sharing the same `Role` for parallel work.

## Claude Agent SDK Integration

- Use `ClaudeSDKClient` (not `query()`) — enforces structured output when `output_format` is set
- Per-invoke ephemeral client: `output_format` is fixed per CLI process, can't change per-query
- Session continuity via `options.resume` for actors with prior sessions
- `_inline_defs()` resolves `$ref` in Pydantic schemas (Claude API doesn't support `$ref`)
- SDK guarantees structured output; error subtype: `error_max_structured_output_retries`
- Runtime lives in `src/iriai_build_v2/runtimes/claude.py` (not in iriai-compose)

## Running

```bash
# Planning workflow
iriai-build plan --name "Feature name" --workspace /path/to/project

# Full build workflow
iriai-build build --name "Feature name" --workspace /path/to/project

# Bug fix workflow
iriai-build bugfix --name "Bug description" --project myproject --workspace /path/to/project
```

## Testing

```bash
python -m pytest tests/ -v
```
