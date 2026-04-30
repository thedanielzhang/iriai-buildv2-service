import { AnimatePresence, motion } from 'motion/react'
import { useEffect, useMemo, useRef, useState, type CSSProperties, type MouseEvent, type ReactNode } from 'react'
import { useStore } from '../store/useStore'
import type { AgentActivity, ArtifactCard, DagRepairCycle, FeatureData, FileScope, Task, TimelineEntry } from '../types'
import { relTime } from '../utils'
import './XPCommandCenterMockup.css'

type ExhibitTab =
  | 'overview'
  | 'dag'
  | 'agents'
  | 'artifacts'
  | 'workstreams'
  | 'milestones'
  | 'operations'

interface DisplayTask {
  id: string
  ui_key?: string
  agent_key?: string
  name: string
  status: string
  summary: string
  description?: string
  repo_path: string
  subfeature_id: string
  acceptance_criteria: string[]
  file_scope: FileScope[]
  runtime?: string
  dependencies?: string[]
  route_kind?: 'implementation' | 'merge' | 'verify' | 'repair' | 'decision' | 'checkpoint'
  route_wave_label?: string
  route_batch_key?: string
  route_sequence?: number
  route_started_at?: string | null
  route_ended_at?: string | null
  linked_task_ids?: string[]
}

interface DisplayWorkstream {
  id: string
  name: string
  summary?: string
  status?: string
  completed_tasks?: number
  total_tasks?: number
  subfeature_slugs?: string[]
}

interface GroupWithTasks {
  tasks?: Task[]
}

interface MilestoneDisplayItem {
  key: string
  title?: string
  type: string
  summary: string
  created_at: string
}

const fallbackTasks: DisplayTask[] = [
  {
    id: 'T-g26-bridge-state',
    name: 'Bridge state publisher',
    status: 'running',
    summary: 'Wire active project state through the real app path while preserving lazy publisher semantics and bridge acknowledgements.',
    repo_path: 'iriai-studio-backend',
    subfeature_id: 'bridge-protocol',
    acceptance_criteria: ['AC-41', 'AC-73'],
    file_scope: [
      { path: 'iriai-studio-backend/iriai_studio_backend/bridge/state.py', action: 'update' },
      { path: 'iriai-studio/src/bridge/projectStore.test.ts', action: 'update' },
    ],
    runtime: 'Claude',
  },
  {
    id: 'T-g26-project-create',
    name: 'Project create contract',
    status: 'running',
    summary: 'Reconcile ProjectStore async create behavior with the canonical active project lifecycle.',
    repo_path: 'iriai-studio',
    subfeature_id: 'project-and-launcher',
    acceptance_criteria: ['AC-54', 'AC-60'],
    file_scope: [{ path: 'iriai-studio/src/stores/projectStore.ts', action: 'update' }],
    runtime: 'Claude',
  },
  {
    id: 'T-g26-diagnostics',
    name: 'Diagnostics bundle contract',
    status: 'queued',
    summary: 'Keep the single bearer-gated ZIP bundle contract and repair stale JSON endpoint references.',
    repo_path: 'iriai-studio-backend',
    subfeature_id: 'diagnostics',
    acceptance_criteria: ['AC-45', 'AC-68'],
    file_scope: [{ path: 'iriai-studio-backend/iriai_studio_backend/diagnostics.py', action: 'update' }],
    runtime: 'Claude',
  },
  {
    id: 'T-g26-verifier',
    name: 'Final aggregate verifier',
    status: 'waiting',
    summary: 'Adversarially verify every repaired task after focused fixes and sanitizer preflight complete.',
    repo_path: 'all repos',
    subfeature_id: 'verification',
    acceptance_criteria: ['group-26'],
    file_scope: [],
    runtime: 'Codex',
  },
]

const fallbackAgents: AgentActivity[] = [
  {
    name: 'implementer-dag-g26-r0-fix-project-create-active-state',
    role: 'Implementer',
    runtime: 'Claude',
    status: 'running',
    group_idx: 26,
    prompt_preview: 'Apply the project-create-active-state RCA. Touch only product files and preserve canonical DAG gates.',
    output_preview: 'Editing ProjectStore contract tests and active-state wiring. Waiting on local verification.',
    related_artifact_keys: ['dag-repair-rca:g26:project-create-active-state:retry-0'],
    related_files: ['iriai-studio/src/stores/projectStore.ts'],
  },
  {
    name: 'verifier-dag-lens-g26-r0-contract-protocol',
    role: 'Protocol lens',
    runtime: 'Codex',
    status: 'complete',
    group_idx: 26,
    prompt_preview: 'Read-only verification of bridge event contracts, REST shapes, ack envelopes, and fixture parity.',
    output_preview: 'Found contract drift in diagnostics bundle references and bridge command catalog prose.',
    related_artifact_keys: ['dag-repair-lens:g26:contract-protocol:retry-0'],
    related_files: [],
  },
  {
    name: 'root-cause-analyst-dag-g26-r0-projectstore-contract',
    role: 'RCA',
    runtime: 'Claude',
    status: 'complete',
    group_idx: 26,
    prompt_preview: 'Cluster verify findings into root causes and produce file-scoped repair instructions.',
    output_preview: 'Grouped active-state, path layout, and diagnostics issues into separate non-overlapping fixes.',
    related_artifact_keys: ['dag-repair-triage:g26:retry-0'],
    related_files: [],
  },
  {
    name: 'contradiction-resolver-dag-g26-projectstore',
    role: 'Contradiction resolver',
    runtime: 'Codex',
    status: 'complete',
    group_idx: 26,
    prompt_preview: 'Resolve whether active-state behavior is a product contradiction, stale artifact, or code repair.',
    output_preview: 'Decision accepted. Treat source sidecar contract as authoritative and continue with code repair.',
    related_artifact_keys: ['contradiction:dag-repair:g26:retry-0:project-create-active-state'],
    related_files: [],
  },
]

const fallbackArtifacts: ArtifactCard[] = [
  {
    key: 'dag',
    title: 'Root DAG',
    family: 'planning',
    summary: 'The implementation graph: task order, checkpoints, dependencies, gates, and execution waves.',
    created_at: new Date().toISOString(),
    status: 'available',
    public_safe: true,
    source: 'demo',
  },
  {
    key: 'dag-repair-rca:g26',
    title: 'Repair RCA',
    family: 'verification',
    summary: 'Root-cause groups and repair dispatch for the current failed checkpoint.',
    created_at: new Date().toISOString(),
    status: 'fresh',
    public_safe: true,
    source: 'demo',
  },
  {
    key: 'contradiction-decisions',
    title: 'Decision Ledger',
    family: 'decision',
    summary: 'Resolved ambiguity so agents can continue without waiting for manual judgement.',
    created_at: new Date().toISOString(),
    status: 'available',
    public_safe: true,
    source: 'demo',
  },
  {
    key: 'artifact-audit-summary',
    title: 'Sidecar Audit',
    family: 'audit',
    summary: 'Canonical JSON sidecar and planning-index parity evidence.',
    created_at: new Date().toISOString(),
    status: 'available',
    public_safe: true,
    source: 'demo',
  },
]

const tabs: Array<{ id: ExhibitTab; label: string; hint: string }> = [
  { id: 'overview', label: 'Overview', hint: 'Iriai Studio, current checkpoint, and latest proof' },
  { id: 'dag', label: 'DAG Map', hint: 'Current implementation group, dispatches, and verifier loops' },
  { id: 'agents', label: 'Agent Floor', hint: 'Agents repairing and verifying the Iriai Studio workbench' },
  { id: 'artifacts', label: 'Artifact Trail', hint: 'Public-safe proof behind the build' },
  { id: 'workstreams', label: 'Workstreams', hint: 'Five product tracks and their implementation progress' },
  { id: 'milestones', label: 'Milestones', hint: 'Narrative checkpoints already earned' },
  { id: 'operations', label: 'Operations', hint: 'Internal repair, sanitizer, and preflight signals' },
]

function percent(done: number, total: number): number {
  return total > 0 ? Math.max(0, Math.min(100, Math.round((done / total) * 100))) : 0
}

function statusTone(status: string | undefined): string {
  const normalized = (status || '').toLowerCase().replace(/_/g, '-')
  if (['complete', 'completed', 'passed', 'approved'].includes(normalized)) return 'complete'
  if (['running', 'active', 'in-progress', 'fixing'].includes(normalized)) return 'running'
  if (['blocked', 'failed', 'error', 'stuck'].includes(normalized)) return 'blocked'
  if (['queued', 'pending', 'waiting'].includes(normalized)) return 'queued'
  return 'neutral'
}

function compactDuration(seconds?: number | null): string {
  if (!seconds || seconds < 0) return 'live'
  if (seconds < 60) return `${seconds}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${Math.round(seconds / 3600)}h`
}

function sentenceCase(value: string): string {
  const normalized = value.replace(/[_-]/g, ' ').trim()
  if (!normalized) return ''
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function shortPath(path: string): string {
  const parts = path.split('/').filter(Boolean)
  return parts.length > 3 ? `${parts[0]}/.../${parts.slice(-2).join('/')}` : path
}

function textFromUnknown(value: unknown, fallback = ''): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  if (Array.isArray(value)) {
    return value.map(item => textFromUnknown(item)).filter(Boolean).join(', ') || fallback
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const primaryKeys = [
      'id',
      'criterion_id',
      'acceptance_criterion_id',
      'gate',
      'name',
      'title',
      'description',
      'text',
    ]
    for (const key of primaryKeys) {
      const text = textFromUnknown(record[key])
      if (text) return text
    }
    const notCriteria = textFromUnknown(record.not_criteria)
    if (notCriteria) return `not: ${notCriteria}`
    try {
      return JSON.stringify(record)
    } catch {
      return fallback
    }
  }
  return fallback
}

function normalizeTokenList(value: unknown): string[] {
  const values = Array.isArray(value) ? value : value !== undefined && value !== null ? [value] : []
  return values
    .map(item => truncate(textFromUnknown(item), 80))
    .filter(Boolean)
}

function truncate(value: string | undefined, max = 120): string {
  const text = (value || '').trim()
  if (!text) return ''
  return text.length > max ? `${text.slice(0, max - 1).trim()}...` : text
}

function TextDisclosureModal({ title, text, onClose }: {
  title: string
  text: string
  onClose: () => void
}) {
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const closeOnBackdrop = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  useEffect(() => {
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  return (
    <motion.div
      className="text-reader-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      role="dialog"
      aria-modal="true"
      aria-label={title}
      onMouseDown={closeOnBackdrop}
    >
      <motion.div
        className="text-reader"
        initial={{ y: 18, scale: 0.985 }}
        animate={{ y: 0, scale: 1 }}
        exit={{ y: 14, scale: 0.988 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
      >
        <header>
          <div>
            <span>Full public overview</span>
            <h2>{title}</h2>
          </div>
          <button
            ref={closeButtonRef}
            type="button"
            onClick={onClose}
            onPointerDown={(event) => {
              event.preventDefault()
              onClose()
            }}
          >
            Close
          </button>
        </header>
        <div className="text-reader-body">
          <p>{text}</p>
        </div>
      </motion.div>
    </motion.div>
  )
}

function ExpandableText({ text, max = 360, modalThreshold = 720, className, moreLabel = 'Read full description', modalTitle = 'Full overview' }: {
  text: string
  max?: number
  modalThreshold?: number
  className?: string
  moreLabel?: string
  modalTitle?: string
}) {
  const [modalOpen, setModalOpen] = useState(false)
  const cleanText = text.trim()
  const needsModal = cleanText.length > modalThreshold
  if (!cleanText) return null
  return (
    <div className="expandable-copy">
      <p className={className}>{needsModal ? truncate(cleanText, max) : cleanText}</p>
      {needsModal && (
        <button type="button" onClick={() => setModalOpen(true)}>
          {moreLabel}
        </button>
      )}
      <AnimatePresence>
        {modalOpen && (
          <TextDisclosureModal
            title={modalTitle}
            text={cleanText}
            onClose={() => setModalOpen(false)}
          />
        )}
      </AnimatePresence>
    </div>
  )
}

function taskDisplayTitle(task: DisplayTask): string {
  return task.name || task.id
}

function taskSelectionKey(task: DisplayTask): string {
  return task.ui_key || [
    task.route_batch_key || task.route_wave_label || task.route_kind || 'task',
    task.route_sequence ?? 'base',
    task.id,
  ].join('::')
}

function titleFromTaskId(taskId: string): string {
  const expanded = taskId
    .replace(/^T(?:ASK)?[-_]?/i, '')
    .replace(/project-and-launcher/gi, 'project launcher')
    .replace(/workflow-supervisor-workspace|wsw/gi, 'workspace supervisor')
    .replace(/artifact-repo-phase-lifecycle|arl/gi, 'artifact lifecycle')
    .replace(/checkpoint-resume/gi, 'checkpoint resume')
    .replace(/live-edit-sync/gi, 'live edit sync')
    .replace(/planning-phase-view/gi, 'planning phase view')
    .replace(/review-phase-views/gi, 'review phase view')
    .replace(/\bbfs\b/gi, 'backend foundation')
    .replace(/\bbp\b/gi, 'bridge protocol')
    .replace(/\bpg\b/gi, 'Postgres')
    .replace(/\bimpl\b/gi, 'implementation')
    .replace(/\btgt\b/gi, 'target')
    .replace(/\bsf(\d+)\b/gi, 'subfeature $1')
    .replace(/\bs(\d+)\b/gi, 'slice $1')
    .replace(/[-_]+/g, ' ')
    .replace(/\b(ws|dag|rca|ui|api|ci|e2e)\b/gi, value => value.toUpperCase())
    .replace(/\b\w/g, value => value.toUpperCase())
  return expanded.slice(0, 96)
}

function inferRepoFromTaskId(taskId: string): string {
  const lower = taskId.toLowerCase()
  if (lower.includes('backend') || lower.includes('bfs') || lower.includes('bp') || lower.includes('sf2')) return 'iriai-studio-backend'
  if (lower.includes('webview') || lower.includes('sidepane') || lower.includes('project') || lower.includes('launcher')) return 'iriai-studio'
  return 'workspace'
}

function selectedMockupFeatureId(): string | null {
  const params = new URLSearchParams(window.location.search)
  return params.get('feature') || window.location.pathname.match(/^\/feature\/([^/?#]+)/)?.[1] || null
}

function publicDashboardDtoToFeatureData(raw: any, fallbackId: string): FeatureData | null {
  if (!raw || typeof raw !== 'object') return null
  const summary = raw.summary || {}
  const totals = raw.dag?.totals || {}
  const activeGroup = raw.dag?.active_group || null
  const artifacts = raw.artifacts?.cards || []
  const milestones = Array.isArray(raw.milestones) ? raw.milestones : []
  const route = Array.isArray(raw.dag?.route) ? raw.dag.route : []
  return {
    id: String(raw.id || fallbackId),
    name: String(summary.title || raw.slug || fallbackId),
    phase: String(summary.phase_label || 'public'),
    workflow_name: 'public-dashboard',
    updated_at: String(raw.updated_at || new Date().toISOString()),
    last_activity_at: String(raw.updated_at || new Date().toISOString()),
    dag: {
      total_tasks: Number(totals.tasks || 0),
      total_groups: Number(totals.groups || 0),
      execution_order: [],
    },
    groups: activeGroup ? [{
      index: Number(activeGroup.index || 0),
      task_count: Number(activeGroup.task_count || 0),
      completed_count: Number(activeGroup.completed_count || 0),
      status: 'active',
      tasks: [],
      verify_steps: [],
      fix_steps: [],
    }] : [],
    gates: {},
    active_gate: null,
    active_gate_steps: [],
    timeline: route.map((stop: any) => ({
      key: String(stop.route_stop_id || stop.label || 'route-stop'),
      type: String(stop.dispatch_id || 'route'),
      passed: null,
      summary: String(stop.label || stop.status || 'Workflow route stop'),
      created_at: String(stop.occurred_at || raw.updated_at || new Date().toISOString()),
    })),
    workstreams: [],
    public_exhibit: {
      public_summary: {
        title: String(summary.title || raw.slug || fallbackId),
        tagline: String(summary.tagline || ''),
        description: String(summary.description || ''),
        phase_label: String(summary.phase_label || ''),
        status_label: String(summary.status_label || ''),
        progress_narrative: String(summary.progress_narrative || ''),
        current_focus: String(summary.current_focus || ''),
        next_checkpoint: String(summary.next_checkpoint || ''),
        health: String(summary.health || 'running'),
        percent_complete: Number(summary.percent_complete || 0),
        completed_groups: Number(totals.completed_groups || 0),
        total_groups: Number(totals.groups || 0),
        completed_tasks: Number(totals.completed_tasks || 0),
        total_tasks: Number(totals.tasks || 0),
        updated_at: String(raw.updated_at || ''),
        source: 'public-dashboard',
      },
      dag_exhibit: {
        narrative: String(raw.dag?.narrative || ''),
        total_groups: Number(totals.groups || 0),
        total_tasks: Number(totals.tasks || 0),
        completed_groups: Number(totals.completed_groups || 0),
        active_group: activeGroup,
        next_groups: [],
        source: 'public-dashboard',
      },
      agent_exhibit: {
        headline: String(raw.agents?.current_dispatch_label || 'Agent activity'),
        active_agents: raw.agents?.active || [],
        recent_agents: raw.agents?.recent || [],
        round_summaries: [],
      },
      artifact_exhibit: {
        cards: artifacts,
        total_count: artifacts.length,
        generated: artifacts.length > 0,
      },
      workstream_exhibit: {
        summary: String(raw.workstreams?.summary || ''),
        source: 'public-dashboard',
        workstreams: raw.workstreams?.items || raw.workstreams?.workstreams || [],
      },
      milestone_feed: milestones,
      current_work: null,
    },
    events: [],
    active_agent: raw.agents?.active?.[0]?.name || null,
  }
}

interface MockupFeatureState {
  selectedFeatureId: string | null
  data: FeatureData | null
  loading: boolean
  failed: boolean
}

interface FeatureExhibitDashboardProps {
  data: FeatureData | null
  featureId?: string | null
  loading?: boolean
  failed?: boolean
  allowDemo?: boolean
  onHome?: () => void
}

function useFeatureForMockup(): MockupFeatureState {
  const selectedFeatureId = selectedMockupFeatureId()
  const storeData = useStore(state => selectedFeatureId ? state.data[selectedFeatureId] || null : null)
  const [directData, setDirectData] = useState<FeatureData | null>(null)
  const [loading, setLoading] = useState(false)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    if (!selectedFeatureId) {
      setDirectData(null)
      setLoading(false)
      setFailed(false)
      return
    }

    let cancelled = false
    setDirectData(null)
    setLoading(!storeData)
    setFailed(false)
    fetch(`/api/feature/${selectedFeatureId}`, { cache: 'no-store' })
      .then(async response => {
        if (response.ok) return response.json()
        const publicResponse = await fetch(`/api/public/features/${selectedFeatureId}`, { cache: 'no-store' }).catch(() => null)
        if (!publicResponse?.ok) return null
        const publicDto = await publicResponse.json()
        return publicDashboardDtoToFeatureData(publicDto, selectedFeatureId)
      })
      .then((data: FeatureData | null) => {
        if (!cancelled) {
          setDirectData(data)
          setLoading(false)
          setFailed(!data && !storeData)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setDirectData(null)
          setLoading(false)
          setFailed(!storeData)
        }
      })

    return () => {
      cancelled = true
    }
  }, [selectedFeatureId, storeData])

  return {
    selectedFeatureId,
    data: selectedFeatureId ? directData || storeData : null,
    loading,
    failed,
  }
}

function normalizeTask(task: Partial<DisplayTask> | Task): DisplayTask {
  return {
    id: textFromUnknown(task.id, 'task'),
    name: textFromUnknown(task.name || task.id, 'Implementation task'),
    status: textFromUnknown(task.status, 'queued'),
    summary: textFromUnknown(task.summary || ('description' in task ? task.description : ''), 'Studio task evidence is being assembled.'),
    description: 'description' in task ? textFromUnknown(task.description) || undefined : undefined,
    repo_path: textFromUnknown(task.repo_path),
    subfeature_id: textFromUnknown(task.subfeature_id),
    acceptance_criteria: normalizeTokenList(task.acceptance_criteria),
    file_scope: task.file_scope || [],
    runtime: 'runtime' in task ? task.runtime : undefined,
    dependencies: 'dependencies' in task ? normalizeTokenList(task.dependencies) : [],
  }
}

function buildModel(data: FeatureData | null) {
  const exhibit = data?.public_exhibit
  const summary = exhibit?.public_summary
  const current = exhibit?.current_work
  const repairActiveGroupIndex = data?.dag_repair?.active_group_index ?? null
  const deterministicActiveGroup = repairActiveGroupIndex !== null && repairActiveGroupIndex !== undefined
    ? data?.groups.find(group => group.index === repairActiveGroupIndex) || null
    : null
  const activeGroup = deterministicActiveGroup ?? data?.groups.find(group => group.status === 'active') ?? current?.active_group ?? null
  const groupTasks = activeGroup && Array.isArray((activeGroup as GroupWithTasks).tasks)
    ? ((activeGroup as GroupWithTasks).tasks || []).filter((task: Task) => task.status !== 'complete')
    : []
  const dagTaskIds = activeGroup?.index !== undefined && data?.dag?.execution_order?.[activeGroup.index]
    ? data.dag.execution_order[activeGroup.index]
    : repairActiveGroupIndex !== null && repairActiveGroupIndex !== undefined && data?.dag?.execution_order?.[repairActiveGroupIndex]
      ? data.dag.execution_order[repairActiveGroupIndex]
      : []
  const rawTasks = groupTasks.length ? groupTasks : current?.active_tasks?.length ? current.active_tasks : []
  const implementationWaveLabel = `Wave ${activeGroup?.index ?? repairActiveGroupIndex ?? 'current'}`
  const tasks = rawTasks.length
    ? rawTasks.map(normalizeTask)
    : dagTaskIds.length
      ? dagTaskIds.map((taskId, index) => normalizeTask({
          id: taskId,
          name: titleFromTaskId(taskId),
          status: index < (activeGroup?.completed_count || 0) ? 'complete' : 'pending',
          summary: 'Root DAG task station. Public task metadata has not been emitted yet, so this card is grounded in execution order and will fill in as task artifacts arrive.',
          repo_path: inferRepoFromTaskId(taskId),
          subfeature_id: '',
          acceptance_criteria: [],
          file_scope: [],
          dependencies: [],
          route_kind: 'implementation',
          route_wave_label: implementationWaveLabel,
        }))
      : fallbackTasks
  const routedTasks = tasks.map(task => ({
    ...task,
    route_kind: task.route_kind || 'implementation',
    route_wave_label: task.route_wave_label || implementationWaveLabel,
  }))
  const currentAgents = [
    ...(current?.active_agents || exhibit?.agent_exhibit?.active_agents || []),
    ...(exhibit?.agent_exhibit?.recent_agents || []),
  ]
  const agents = currentAgents.length ? currentAgents.slice(0, 64) : fallbackAgents
  const artifacts = exhibit?.artifact_exhibit?.cards?.length ? exhibit.artifact_exhibit.cards : fallbackArtifacts
  const outcomes = current?.recent_outcomes?.length
    ? current.recent_outcomes
    : exhibit?.milestone_feed?.slice(0, 8).map(item => ({
        key: item.source,
        type: item.kind,
        passed: null,
        summary: item.summary,
        created_at: item.created_at,
      })) || []
  const completedGroups = data?.dag_repair?.summary.completed_groups ?? summary?.completed_groups ?? data?.groups.filter(group => group.status === 'complete').length ?? 26
  const totalGroups = data?.dag_repair?.summary.total_groups ?? summary?.total_groups ?? data?.dag?.total_groups ?? 75
  const completedTasks = data?.groups.reduce((sum, group) => sum + group.completed_count, 0) ?? summary?.completed_tasks ?? 309
  const totalTasks = data?.dag?.total_tasks ?? summary?.total_tasks ?? 920
  const workstreams = (exhibit?.workstream_exhibit?.workstreams || data?.workstreams || []) as DisplayWorkstream[]
  const milestoneFeed = exhibit?.milestone_feed || []
  const isDemo = !data
  const activeRcaGroups = data?.dag_repair?.current_cycle?.rca_group_count ?? 0
  const fixableGroups = data?.dag_repair?.current_cycle?.fixable_group_count ?? 0
  const expandedVerifyRuns = data?.dag_repair?.summary.expanded_verify_runs ?? 0

  return {
    usingFallback: isDemo,
    id: data?.id || 'demo',
    title: summary?.title || data?.name || 'Visual Studio Code frontend for project and workflow manager',
    tagline: summary?.tagline || 'Workflow-first project operations inside a VS Code desktop app.',
    description: summary?.description || 'Iriai Studio turns a VS Code fork into a project-aware workflow cockpit with bridge-backed state, artifact provenance, checkpointed implementation, and multi-agent repair loops.',
    phase: summary?.phase_label || data?.phase || 'Implementation',
    statusLabel: summary?.status_label || data?.phase || 'DAG execution in progress',
    health: summary?.health || (data?.phase === 'complete' ? 'complete' : 'running'),
    percentComplete: summary?.percent_complete ?? percent(completedGroups, totalGroups),
    completedGroups,
    totalGroups,
    completedTasks,
    totalTasks,
    activeGroupIndex: data?.dag_repair?.active_group_index ?? activeGroup?.index ?? 26,
    activeGroupTaskCount: activeGroup?.task_count ?? routedTasks.length,
    activeGroupCompletedCount: activeGroup?.completed_count ?? 0,
    currentFocus: summary?.current_focus || 'Building the project/workflow manager through checkpointed implementation groups, verifier gates, and focused repair passes.',
    nextCheckpoint: current?.next_checkpoint || summary?.next_checkpoint || 'Group verifier approval and checkpoint.',
    progressNarrative: summary?.progress_narrative || summary?.description || '',
    activeRcaGroups,
    fixableGroups,
    expandedVerifyRuns,
    tasks: routedTasks,
    agents,
    artifacts,
    outcomes: outcomes.length ? outcomes : [
      { key: 'dispatch', type: 'dispatch', passed: null, summary: 'Repair dispatch prepared the next set of fix groups.', created_at: new Date().toISOString() },
      { key: 'decision', type: 'decision', passed: null, summary: 'Contradiction resolver accepted the active-state decision.', created_at: new Date().toISOString() },
    ],
    workstreams,
    milestoneFeed,
    timeline: data?.timeline || [],
    repair: data?.dag_repair || exhibit?.dag_exhibit?.repair || null,
  }
}

type DisplayModel = ReturnType<typeof buildModel>

function taskRuntime(task: DisplayTask, agents: AgentActivity[]): string {
  if (task.runtime) return task.runtime
  const matched = agents.find(agent => agent.task_id === task.id)
  if (matched?.runtime) return matched.runtime
  if (task.route_kind === 'verify' || task.route_kind === 'decision' || task.route_kind === 'checkpoint') return 'Codex'
  if (task.route_kind === 'repair') return 'Claude'
  if (task.name.toLowerCase().includes('verify')) return 'Codex'
  return 'Claude'
}

function agentMatchesActiveGroup(agent: AgentActivity, groupIdx: number): boolean {
  return agent.group_idx === groupIdx || agent.name.includes(`g${groupIdx}`) || agent.name.includes(`dag-g${groupIdx}`)
}

function agentsForRouteRole(model: DisplayModel, role: string): AgentActivity[] {
  return model.agents.filter(agent => agentRoleBucket(agent) === role && agentMatchesActiveGroup(agent, model.activeGroupIndex))
}

function titleFromAgentName(agentName: string | undefined, fallback: string): string {
  const cleaned = (agentName || '')
    .replace(/^root-cause-analyst-dag-g\d+-r\d+-/i, '')
    .replace(/^implementer-dag-g\d+-r\d+-fix-/i, '')
    .replace(/^verifier-dag-g\d+-r\d+-focused-reverify-/i, '')
    .replace(/^verifier-dag-g\d+-r\d+-/i, '')
    .replace(/^verifier-dag-lens-g\d+-r\d+-/i, '')
    .replace(/^contradiction-resolver-dag-g\d+-/i, '')
    .replace(/^dag-g\d+-r\d+-/i, '')
    .replace(/^fix-/i, '')
    .trim()
  return cleaned ? titleFromTaskId(cleaned) : fallback
}

function fileScopeFromAgent(agent?: AgentActivity): FileScope[] {
  return (agent?.related_files || []).map(path => ({ path, action: 'touches' }))
}

function previewLooksGeneric(value: string | undefined): boolean {
  const normalized = (value || '').trim().toLowerCase()
  if (!normalized) return true
  if (
    normalized.startsWith('{')
    || normalized.startsWith('[')
    || normalized.includes('/users/')
    || normalized.includes('.iriai/')
    || normalized.includes('"group_idx"')
    || normalized.includes('"result_task_ids"')
    || normalized.includes('traceback')
  ) return true
  return [
    'apply a focused repair',
    'identify the root cause',
    'verify the current dag group',
    'continue the current workflow step',
    'prompt preview unavailable',
    'resolve a spec or artifact contradiction',
    'group verifier findings',
  ].some(phrase => normalized.includes(phrase))
}

function agentPromptText(agent: AgentActivity): string {
  if (!previewLooksGeneric(agent.prompt_preview)) return agent.prompt_preview as string
  const role = agentRoleBucket(agent).replace(/s$/, '').toLowerCase()
  const title = titleFromAgentName(agent.name, agent.task_id || 'current workflow item')
  const files = agent.related_files?.slice(0, 2).map(shortPath).join(', ')
  const artifacts = agent.related_artifact_keys?.slice(0, 2).join(', ')
  if (files) return `${role} focused on ${title}; file scope includes ${files}.`
  if (artifacts) return `${role} focused on ${title}; grounded by ${artifacts}.`
  return `${role} focused on ${title} for group ${agent.group_idx ?? 'current'}.`
}

function agentOutputText(agent: AgentActivity): string {
  if (agent.output_preview && agent.output_preview !== agent.prompt_preview && !previewLooksGeneric(agent.output_preview)) return agent.output_preview
  if (agent.summary && agent.summary !== agent.prompt_preview && !previewLooksGeneric(agent.summary)) return agent.summary
  return agent.status === 'running'
    ? 'Running now; waiting for this Studio repair or verification return to update the checkpoint route.'
    : 'No public-safe return yet; use the task, artifacts, and touched files as current evidence.'
}

function agentPromptTextForTask(agent: AgentActivity, task?: DisplayTask): string {
  if (!previewLooksGeneric(agent.prompt_preview)) return agent.prompt_preview as string
  if (!task) return agentPromptText(agent)
  const role = agentRoleBucket(agent).replace(/s$/, '').toLowerCase()
  return `${role} assigned to ${task.id}: ${task.summary || task.description}`
}

function agentOutputTextForTask(agent: AgentActivity, task?: DisplayTask): string {
  const output = agentOutputText(agent)
  if (!task || !output.includes('No public-safe return yet')) return output
  return `Waiting for a public-safe return on ${task.id}; the selected task card shows the Studio objective and verification surface.`
}

function agentRouteSummary(agent: AgentActivity | undefined, fallback: string): string {
  return agent ? agentPromptText(agent) : fallback
}

function routeTaskWithAgent(
  fallbackId: string,
  name: string,
  summary: string,
  route_kind: DisplayTask['route_kind'],
  status: string,
  runtime: string,
  meta: string[],
  route_wave_label: string,
  linked_task_ids: string[],
  agent?: AgentActivity,
  options: Partial<Pick<DisplayTask, 'route_batch_key' | 'route_sequence' | 'route_started_at' | 'route_ended_at'>> = {},
): DisplayTask {
  return {
    ...routeTask(
      fallbackId,
      name,
      agentRouteSummary(agent, summary),
      route_kind,
      agent?.status || status,
      agent?.runtime || runtime,
      [
        ...meta,
        ...(agent?.related_artifact_keys || []).slice(0, 2).map(key => key.split(':')[0]),
      ],
      route_wave_label,
      linked_task_ids,
      {
        ...options,
        route_started_at: agent?.started_at || options.route_started_at,
        route_ended_at: agent?.ended_at || options.route_ended_at,
      },
    ),
    agent_key: agent ? agentKey(agent) : undefined,
    file_scope: fileScopeFromAgent(agent),
  }
}

function agentRoleBucket(agent: AgentActivity): string {
  const value = `${agent.role} ${agent.name}`.toLowerCase()
  if (value.includes('verify') || value.includes('lens')) return 'Verifiers'
  if (value.includes('rca') || value.includes('triage') || value.includes('root-cause')) return 'RCA'
  if (value.includes('contradiction')) return 'Decisions'
  if (value.includes('fix') || value.includes('repair')) return 'Fixers'
  return 'Implementers'
}

function agentKey(agent: AgentActivity): string {
  return [
    agent.name,
    agent.started_at || 'no-start',
    agent.ended_at || 'active',
    agent.task_id || 'no-task',
  ].join('|')
}

function taskForAgent(agent: AgentActivity, tasks: DisplayTask[]): DisplayTask | undefined {
  const agentName = (agent.name || '').toLowerCase()
  const agentTitle = titleFromAgentName(agent.name, '').toLowerCase()
  const agentTaskId = (agent.task_id || '').toLowerCase()
  return tasks.find(task =>
    (!!task.agent_key && task.agent_key === agentKey(agent))
    || (agent.task_id && task.id === agent.task_id)
    || agent.name === task.id
    || (!!agentTaskId && task.id.toLowerCase().includes(agentTaskId))
    || (!!agentTitle && task.name.toLowerCase().includes(agentTitle))
    || (!!agentTitle && task.summary.toLowerCase().includes(agentTitle))
    || (!!task.route_batch_key && agentName.includes(task.route_batch_key.toLowerCase()))
    || agent.related_files?.some(file => task.file_scope.some(scope => scope.path === file))
  )
}

function ViewFrame({ children }: { children: ReactNode }) {
  return (
    <AnimatePresence mode="wait">
      <motion.section
        className="exhibit-view"
        initial={{ opacity: 0, y: 14, scale: 0.992 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: -12, scale: 0.992 }}
        transition={{ duration: 0.22, ease: 'easeOut' }}
      >
        {children}
      </motion.section>
    </AnimatePresence>
  )
}

function Metric({ label, value, caption }: { label: string; value: string | number; caption: string }) {
  return (
    <div className="xp-metric">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{caption}</small>
    </div>
  )
}

function ProgressMeter({ done, total, label }: { done: number; total: number; label: string }) {
  const value = percent(done, total)
  return (
    <div className="xp-progress">
      <div>
        <span>{label}</span>
        <strong>{value}%</strong>
      </div>
      <div className="xp-progress-track">
        <motion.div
          className="xp-progress-fill"
          initial={{ width: 0 }}
          animate={{ width: `${value}%` }}
          transition={{ duration: 0.55, ease: 'easeOut' }}
        />
      </div>
    </div>
  )
}

function StatusPill({ children, tone = 'neutral' }: { children: ReactNode; tone?: string }) {
  return <span className={`xp-pill ${tone}`}>{children}</span>
}

function runtimeToneFor(runtime: string): string {
  const normalized = runtime.toLowerCase()
  if (normalized.includes('codex')) return 'codex'
  if (normalized.includes('claude')) return 'claude'
  return 'neutral'
}

function DetailRail({ kicker, title, body, children, className }: {
  kicker: string
  title: string
  body?: string
  children?: ReactNode
  className?: string
}) {
  return (
    <aside className={`detail-rail${className ? ` ${className}` : ''}`}>
      <span>{kicker}</span>
      <h2>{title}</h2>
      {body && <p>{body}</p>}
      {children}
    </aside>
  )
}

function EmptyDetail({ kicker, title, body }: { kicker: string; title: string; body: string }) {
  return (
    <DetailRail className="empty-detail" kicker={kicker} title={title} body={body}>
      <div className="empty-detail-card">
        <span>Nothing selected</span>
        <p>Pick a card in the main panel to load the Studio task, agent, artifact, workstream, milestone, or repair signal behind this checkpoint.</p>
      </div>
    </DetailRail>
  )
}

function artifactContentUrl(artifact: ArtifactCard): string | null {
  if (artifact.content_url?.startsWith('/api/public/')) return artifact.content_url
  if (artifact.artifact_id) return `/api/public/artifacts/${encodeURIComponent(artifact.artifact_id)}/content`
  return null
}

function artifactRenderMode(artifact: ArtifactCard): string {
  const explicit = artifact.render_mode?.toLowerCase()
  if (explicit) return explicit
  if (artifact.key.endsWith('.json')) return 'json'
  if (artifact.key.endsWith('.html')) return 'html'
  if (artifact.key.endsWith('.md')) return 'markdown'
  return 'text'
}

function formattedJson(value: string): string {
  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}

type OverviewStageId = 'verify' | 'expanded' | 'rca' | 'fix' | 'reverify' | 'checkpoint'

function deriveActiveStage(model: DisplayModel): OverviewStageId {
  const cycle = model.repair?.current_cycle
  if (!cycle) return model.completedGroups >= model.totalGroups ? 'checkpoint' : 'verify'
  if (cycle.status === 'passed') return 'checkpoint'

  const runningAgentText = model.agents
    .filter(agent => statusTone(agent.status) === 'running')
    .map(agent => `${agent.name} ${agent.role} ${agent.task_id || ''}`.toLowerCase())
    .join(' ')

  if (runningAgentText.includes('focused-reverify') || runningAgentText.includes('reverify')) return 'reverify'
  if (runningAgentText.includes('root-cause') || runningAgentText.includes('rca')) return 'rca'
  if (runningAgentText.includes('implementer') || runningAgentText.includes('fix')) return 'fix'
  if (runningAgentText.includes('verifier')) return cycle.retry === 'initial' ? 'verify' : 'reverify'
  if (cycle.scheduled_round_count > 0 || cycle.fixable_group_count > 0) return 'fix'
  if (cycle.rca_group_count > 0) return 'rca'
  if (cycle.lens_count > 0) return 'expanded'
  return 'verify'
}

function stageLabel(stage: OverviewStageId): string {
  return {
    verify: 'checkpoint verification',
    expanded: 'expanded verifier lenses',
    rca: 'root-cause clustering',
    fix: 'parallel product repair',
    reverify: 'focused repair verification',
    checkpoint: 'checkpoint publication',
  }[stage]
}

function deriveOverviewStatus(model: DisplayModel) {
  const cycle = model.repair?.current_cycle
  const activeStage = deriveActiveStage(model)
  const activeAgent = model.agents.find(agent => statusTone(agent.status) === 'running')
  const retryLabel = cycle ? cycle.retry === 'initial' ? 'initial pass' : `retry ${cycle.retry}` : 'current pass'
  const groupLabel = `Group ${model.activeGroupIndex}`
  const stageHeadlines: Record<OverviewStageId, string> = {
    verify: `${groupLabel} is under checkpoint verification`,
    expanded: `${groupLabel} is under expanded Studio verifier sweep`,
    rca: `${groupLabel} is clustering Studio verifier findings`,
    fix: `${groupLabel} is repairing Studio product surfaces`,
    reverify: `${groupLabel} is reverifying Studio repair groups`,
    checkpoint: `${groupLabel} is ready to publish checkpoint proof`,
  }
  const currentHeadline = cycle ? stageHeadlines[activeStage] : model.currentFocus
  const activeAgentTitle = activeAgent
    ? titleFromAgentName(activeAgent.name, activeAgent.task_id || activeAgent.role || 'active task')
    : ''
  const agentDetail = activeAgent
    ? `${activeAgent.role || 'Agent'} is working on ${activeAgentTitle} for the Iriai Studio checkpoint.`
    : 'No active agent is currently reported; the next Iriai Studio dispatch will appear as soon as the bridge emits it.'
  const repairDetail = cycle
    ? `${retryLabel}: ${cycle.lens_count} verifier lens${cycle.lens_count === 1 ? '' : 'es'}, ${cycle.rca_group_count} root-cause group${cycle.rca_group_count === 1 ? '' : 's'}, ${cycle.fixable_group_count} repair group${cycle.fixable_group_count === 1 ? '' : 's'}, and ${cycle.scheduled_round_count} parallel repair round${cycle.scheduled_round_count === 1 ? '' : 's'}.`
    : model.progressNarrative || model.currentFocus

  return {
    tone: statusTone(model.health),
    healthLabel: sentenceCase(model.health || 'running'),
    statusLabel: cycle ? `${groupLabel} / ${retryLabel}` : model.statusLabel,
    currentHeadline,
    currentDetail: `${agentDetail} ${repairDetail}`,
    nextHeadline: activeStage === 'checkpoint'
      ? 'Publish the checkpoint proof'
      : `Run aggregate approval for checkpoint ${model.activeGroupIndex}`,
    nextDetail: cycle
      ? `Once ${stageLabel(activeStage)} finishes, Codex verifies the full repaired group and persists checkpoint ${model.activeGroupIndex} only if every gate passes.`
      : model.nextCheckpoint,
  }
}

function latestMilestone(model: DisplayModel) {
  return model.milestoneFeed[0] || null
}

function OverviewView({ model }: { model: DisplayModel }) {
  const overview = deriveOverviewStatus(model)
  const activeStage = deriveActiveStage(model)
  const milestone = latestMilestone(model)
  const activeAgents = model.agents.filter(agent => statusTone(agent.status) === 'running')
  const latestOutcome = model.outcomes[0]
  const stages: Array<{ id: OverviewStageId; label: string }> = [
    { id: 'verify', label: 'Group verify' },
    { id: 'expanded', label: 'Lens sweep' },
    { id: 'rca', label: 'RCA clusters' },
    { id: 'fix', label: 'Repair waves' },
    { id: 'reverify', label: 'Focused reverify' },
    { id: 'checkpoint', label: 'Checkpoint proof' },
  ]
  const activeStageIndex = stages.findIndex(stage => stage.id === activeStage)

  return (
    <ViewFrame>
      <div className={`overview-poster ${model.usingFallback ? 'demo-mode' : ''}`}>
        <div className="poster-copy overview-hero-copy">
          <div className="eyebrow-row">
            <StatusPill tone={overview.tone}>{overview.healthLabel}</StatusPill>
            <span>{overview.statusLabel}</span>
            {model.usingFallback && <span>demo data</span>}
          </div>
          <h1>{model.title}</h1>
          <p>{model.tagline}</p>
          <ExpandableText
            className="overview-description"
            text={model.description || model.progressNarrative}
            max={360}
            modalThreshold={720}
            moreLabel="Read full overview"
            modalTitle={model.title}
          />
        </div>
        <div className="poster-score" style={{ '--progress': `${model.percentComplete}%` } as CSSProperties}>
          <strong>{model.percentComplete}%</strong>
          <span>{model.completedGroups} of {model.totalGroups} groups checkpointed</span>
        </div>
      </div>

      <div className="overview-now-panel">
        <section className="overview-current-card">
          <span>Live checkpoint</span>
          <h2>{overview.currentHeadline}</h2>
          <p>{overview.currentDetail}</p>
          <div className="overview-current-facts">
            <Metric label="Active group" value={`G${model.activeGroupIndex}`} caption={`checkpoint ${model.repair?.latest_checkpoint_group ?? Math.max(0, model.activeGroupIndex - 1)} complete`} />
            <Metric label="Active agents" value={activeAgents.length} caption={activeAgents[0] ? titleFromAgentName(activeAgents[0].name, activeAgents[0].role) : 'none running'} />
            <Metric label="Elapsed" value={compactDuration(model.repair?.summary?.active_group_elapsed_seconds)} caption="current group" />
          </div>
        </section>

        <aside className="overview-progress-card">
          <span>Build progress</span>
          <ProgressMeter label="DAG groups" done={model.completedGroups} total={model.totalGroups} />
          <ProgressMeter label="Implementation tasks" done={model.completedTasks} total={model.totalTasks} />
        </aside>
      </div>

      <div className="overview-stage-strip" aria-label="Current orchestration stage">
        {stages.map((stage, index) => (
          <div className={`overview-stage ${stage.id === activeStage ? 'active' : index < activeStageIndex ? 'complete' : ''}`} key={stage.id}>
            <span>{index + 1}</span>
            <strong>{stage.label}</strong>
          </div>
        ))}
      </div>

      <div className="overview-proof-grid">
        <section className="overview-proof-card latest-milestone">
          <span>Latest proof</span>
          <h2>{milestone?.title || sentenceCase(latestOutcome?.type || 'Milestone pending')}</h2>
          <p>{milestone?.summary || latestOutcome?.summary || 'The public milestone feed will appear as soon as the bridge publishes the next narrative checkpoint.'}</p>
          <small>{milestone?.created_at ? relTime(milestone.created_at) : latestOutcome?.created_at ? relTime(latestOutcome.created_at) : 'timestamp pending'}</small>
        </section>

        <section className="overview-proof-card next-step">
          <span>Next checkpoint</span>
          <h2>{overview.nextHeadline}</h2>
          <p>{overview.nextDetail}</p>
          <small>{latestOutcome?.summary || 'Awaiting the next verifier artifact.'}</small>
        </section>
      </div>
    </ViewFrame>
  )
}

function taskCardBadge(task: DisplayTask, fallback = 'Task'): string {
  if (task.route_wave_label) return task.route_wave_label
  if (task.route_kind) return task.route_kind.replace(/-/g, ' ')
  return task.subfeature_id || fallback
}

function taskCardRefs(task: DisplayTask, label = 'gates'): string {
  if (!task.acceptance_criteria.length) return `${label} pending`
  const visible = task.acceptance_criteria.slice(0, 3).join(', ')
  const remaining = task.acceptance_criteria.length - 3
  return remaining > 0 ? `${visible} +${remaining}` : visible
}

function TaskCardBody({ task, runtime, badge, refsLabel = 'gates', summaryMax }: {
  task: DisplayTask
  runtime: string
  badge?: string
  refsLabel?: string
  summaryMax?: number
}) {
  const fullSummary = task.summary || task.description || ''
  const fullRefs = taskCardRefs(task, refsLabel)
  return (
    <div className="task-card-body">
      <div className="task-card-top">
        <span className="task-card-badge" title={badge || taskCardBadge(task)}>{badge || taskCardBadge(task)}</span>
        <StatusPill tone={runtimeToneFor(runtime)}>{runtime}</StatusPill>
      </div>
      <code className="task-card-id" title={task.id}>{task.id}</code>
      <h3 title={taskDisplayTitle(task)}>{taskDisplayTitle(task)}</h3>
      <div className="task-card-surface">
        <span>{(task.route_kind || 'implementation').replace(/-/g, ' ')}</span>
        <strong title={task.repo_path || task.subfeature_id || 'workspace'}>{task.repo_path || task.subfeature_id || 'workspace'}</strong>
      </div>
      <p title={fullSummary}>{summaryMax ? truncate(fullSummary, summaryMax) : fullSummary}</p>
      <div className="task-card-bottom">
        <span>{task.file_scope.length ? `${task.file_scope.length} files` : 'file scope pending'}</span>
        <span title={fullRefs}>{fullRefs}</span>
      </div>
    </div>
  )
}

function TaskInspector({ task, agents }: { task: DisplayTask | null; agents: AgentActivity[] }) {
  if (!task) {
    return (
      <EmptyDetail
        kicker="Studio task evidence"
        title="Choose a task in the current Studio group"
        body="Open an implementation, verifier, RCA, repair, decision, or checkpoint stop to see the Iriai Studio objective, gates, files, and return that move this checkpoint."
      />
    )
  }
  const agent = agents.find(item => taskForAgent(item, [task]))
  return (
    <DetailRail
      className="task-inspector"
      kicker="Studio work item"
      title={task.name}
      body={task.summary}
    >
      <div className="inspector-grid">
        <div className="detail-section">
          <span>Acceptance gates</span>
          <div className="token-row">
            {(task.acceptance_criteria.length ? task.acceptance_criteria : ['pending']).slice(0, 8).map(item => <code key={item}>{item}</code>)}
          </div>
        </div>
        <div className="detail-section">
          <span>Studio files</span>
          <div className="file-list">
            {(task.file_scope.length ? task.file_scope : [{ path: 'No file scope reported yet', action: 'read' }]).slice(0, 5).map(file => (
              <code key={`${file.action}-${file.path}`}>{file.action}: {shortPath(file.path)}</code>
            ))}
          </div>
        </div>
      </div>
      <div className="detail-section prompt-peek">
        <div className="detail-subsection">
          <span>Dispatch objective</span>
          <p>{agent ? agentPromptTextForTask(agent, task) : 'The bridge has not emitted a public-safe Studio dispatch brief for this task yet.'}</p>
        </div>
        <div className="detail-subsection">
          <span>Checkpoint return</span>
          <p>{agent ? agentOutputTextForTask(agent, task) : 'Waiting for the repair or verifier return.'}</p>
        </div>
      </div>
    </DetailRail>
  )
}

const verifierLensNames = [
  'Build dependency lens',
  'Runtime composition lens',
  'Contract protocol lens',
  'Acceptance coverage lens',
  'Security boundary lens',
  'Regression downstream lens',
]

function routeTask(
  id: string,
  name: string,
  summary: string,
  route_kind: DisplayTask['route_kind'],
  status: string,
  runtime: string,
  meta: string[] = [],
  route_wave_label?: string,
  linked_task_ids: string[] = [],
  options: Partial<Pick<DisplayTask, 'route_batch_key' | 'route_sequence' | 'route_started_at' | 'route_ended_at'>> = {},
): DisplayTask {
  return {
    id,
    name,
    status,
    summary,
    repo_path: route_kind === 'verify' || route_kind === 'checkpoint' ? 'verification' : route_kind || 'workflow',
    subfeature_id: route_kind || 'workflow',
    acceptance_criteria: meta,
    file_scope: [],
    runtime,
    dependencies: [],
    route_kind,
    route_wave_label,
    linked_task_ids,
    ...options,
  }
}

function linkedTaskIdsFor(model: DisplayModel): string[] {
  return model.tasks.map(task => task.id)
}

function taskBatchPreview(taskIds: string[], max = 4): string[] {
  return taskIds.slice(0, max)
}

function batchTitleFor(label: string, tasks: DisplayTask[]): string {
  if (tasks.some(task => task.route_kind === 'implementation')) return 'Implementation dispatch'
  if (label.includes('Repair wave')) return `${label}: parallel fix dispatch`
  if (label.includes('RCA dispatch')) return 'Root-cause analysts in parallel'
  if (label.includes('Expanded verify')) return 'Read-only verifier lenses'
  if (label.includes('Initial verify') || label.includes('Normal verify')) return 'Checkpoint verifier'
  if (label.includes('Focused reverify')) return 'Focused reverify batch'
  if (label.includes('Contradiction resolution')) return 'Decision resolution batch'
  if (label.includes('Aggregate verify')) return 'Final aggregate verifier'
  if (label.includes('Merge')) return 'Group merge'
  if (label.includes('Checkpoint')) return 'Checkpoint persistence'
  if (tasks.some(task => task.route_kind === 'checkpoint')) return 'Checkpoint persistence'
  return label
}

type RouteBatch = {
  key: string
  label: string
  title: string
  tasks: DisplayTask[]
  detail?: string
}

function groupedRouteBatches(tasks: DisplayTask[]): RouteBatch[] {
  const batches: RouteBatch[] = []
  const orderedTasks = [...tasks].sort((a, b) => {
    const sequenceDelta = (a.route_sequence ?? 0) - (b.route_sequence ?? 0)
    if (sequenceDelta) return sequenceDelta
    const aTime = Date.parse(a.route_started_at || '')
    const bTime = Date.parse(b.route_started_at || '')
    if (!Number.isNaN(aTime) && !Number.isNaN(bTime) && aTime !== bTime) return aTime - bTime
    return a.id.localeCompare(b.id)
  })
  orderedTasks.forEach(task => {
    const label = task.route_wave_label || 'Workflow'
    const key = task.route_batch_key || label
    const last = batches[batches.length - 1]
    if (last?.key === key) {
      last.tasks.push(task)
      last.title = batchTitleFor(label, last.tasks)
      return
    }
    batches.push({ key, label, title: batchTitleFor(label, [task]), tasks: [task] })
  })
  return batches
}

function dispatchDetailFor(batch: RouteBatch): string {
  if (batch.label.includes('Expanded verify')) return `${batch.tasks.length} verifier lens${batch.tasks.length === 1 ? '' : 'es'}`
  if (batch.label.includes('RCA dispatch')) return `${batch.tasks.length} RCA task${batch.tasks.length === 1 ? '' : 's'}`
  if (batch.label.includes('Repair wave')) return `${batch.tasks.length} repair group${batch.tasks.length === 1 ? '' : 's'}`
  if (batch.label.includes('Focused reverify')) return `${batch.tasks.length} focused verifier${batch.tasks.length === 1 ? '' : 's'}`
  if (batch.label.includes('Contradiction resolution')) return `${batch.tasks.length} decision item${batch.tasks.length === 1 ? '' : 's'}`
  return `${batch.tasks.length} workflow item${batch.tasks.length === 1 ? '' : 's'}`
}

function routeBatchTone(batch: RouteBatch): string {
  const tones = batch.tasks.map(task => statusTone(task.status))
  if (tones.includes('running')) return 'running'
  if (tones.includes('blocked')) return 'blocked'
  if (tones.length && tones.every(tone => tone === 'complete')) return 'complete'
  if (tones.includes('queued')) return 'queued'
  return 'neutral'
}

function currentDispatchIndex(batches: RouteBatch[], startIndex = 0): number {
  const scopedBatches = batches.slice(startIndex)
  const runningIndex = scopedBatches.findIndex(batch => routeBatchTone(batch) === 'running')
  if (runningIndex >= 0) return startIndex + runningIndex
  const blockedIndex = scopedBatches.map(routeBatchTone).lastIndexOf('blocked')
  if (blockedIndex >= 0) return startIndex + blockedIndex
  const queuedIndex = scopedBatches.findIndex(batch => {
    const tone = routeBatchTone(batch)
    return tone === 'queued' || tone === 'neutral'
  })
  if (queuedIndex >= 0) return startIndex + queuedIndex
  return scopedBatches.length ? batches.length - 1 : -1
}

function currentDispatchKeyFor(model: DisplayModel): string | null {
  const cycle = model.repair?.current_cycle
  if (!cycle || cycle.group_idx !== model.activeGroupIndex) return null
  const cycleKey = cycleRouteKey(cycle)
  if (cycle.fixable_group_count > 0 && cycle.applied_fix_count < cycle.fixable_group_count) {
    const scheduledRoundCount = Math.max(cycle.scheduled_round_count || 0, 1)
    const groupsPerRound = Math.max(1, Math.ceil(cycle.fixable_group_count / scheduledRoundCount))
    const nextRepairIndex = Math.min(cycle.fixable_group_count - 1, cycle.applied_fix_count || 0)
    const repairRound = Math.min(scheduledRoundCount, Math.floor(nextRepairIndex / groupsPerRound) + 1)
    return `${cycleKey}-repair-${repairRound}`
  }
  if ((cycle.contradiction_count || 0) > 0 && (cycle.rejected_contradiction_count || 0) >= (cycle.contradiction_count || 0)) return `${cycleKey}-decision`
  if (cycle.rca_group_count > 0 && cycle.fixable_group_count === 0) return `${cycleKey}-rca`
  if (cycle.lens_count > 0 && cycle.rca_group_count === 0) return `${cycleKey}-expanded-verify`
  if (cycle.fixable_group_count > 0 && cycle.applied_fix_count >= cycle.fixable_group_count) return `${cycleKey}-focused-reverify`
  if (cycle.status === 'failed') return `${cycleKey}-aggregate-verify`
  return `${cycleKey}-normal-verify`
}

function implementationRouteBatchForModel(model: DisplayModel): RouteBatch {
  const repos = Array.from(new Set(model.tasks.map(task => task.repo_path || task.subfeature_id || 'workspace'))).slice(0, 4)
  return {
    key: `implementation-wave-${model.activeGroupIndex}`,
    label: `Wave ${model.activeGroupIndex}`,
    title: batchTitleFor(`Wave ${model.activeGroupIndex}`, model.tasks),
    tasks: model.tasks,
    detail: `${model.tasks.length} implementation stop${model.tasks.length === 1 ? '' : 's'} / ${repos.join(' / ') || 'workspace'}`,
  }
}

function routeBatchesForModel(model: DisplayModel): RouteBatch[] {
  return [
    implementationRouteBatchForModel(model),
    ...groupedRouteBatches(buildFollowupTasks(model)).map(batch => ({
      ...batch,
      detail: dispatchDetailFor(batch),
    })),
  ]
}

function activeRouteBatchIndexForModel(model: DisplayModel, batches: RouteBatch[]): number {
  const hasRepairCycle = repairCyclesForRoute(model).length > 0
  const explicitCurrentDispatchKey = currentDispatchKeyFor(model)
  const explicitCurrentDispatchIndex = explicitCurrentDispatchKey
    ? batches.findIndex(batch => batch.key === explicitCurrentDispatchKey)
    : -1
  return explicitCurrentDispatchIndex >= 0
    ? explicitCurrentDispatchIndex
    : currentDispatchIndex(batches, hasRepairCycle ? 1 : 0)
}

function activeRouteBatchForModel(model: DisplayModel): { batch: RouteBatch | null; index: number; batches: RouteBatch[] } {
  const batches = routeBatchesForModel(model)
  const index = activeRouteBatchIndexForModel(model, batches)
  return { batch: index >= 0 ? batches[index] : null, index, batches }
}

function agentMatchesRouteBatch(agent: AgentActivity, batch: RouteBatch | null): boolean {
  if (!batch) return false
  const label = batch.label.toLowerCase()
  const role = agentRoleBucket(agent)
  const haystack = `${agent.name} ${agent.role} ${agent.task_id || ''} ${(agent.related_artifact_keys || []).join(' ')}`.toLowerCase()
  if (batch.tasks.some(task => task.route_kind === 'implementation')) return role === 'Implementers'
  if (label.includes('rca dispatch')) return role === 'RCA'
  if (label.includes('repair wave')) return role === 'Fixers'
  if (label.includes('focused reverify')) return role === 'Verifiers' && (haystack.includes('focused') || haystack.includes('reverify'))
  if (label.includes('expanded verify')) return role === 'Verifiers' && (haystack.includes('lens') || haystack.includes('verify'))
  if (label.includes('contradiction resolution')) return role === 'Decisions'
  if (label.includes('normal verify') || label.includes('aggregate verify') || label.includes('initial verify')) return role === 'Verifiers'
  return true
}

function cycleRetryRank(retry: string | undefined): number {
  if (!retry || retry === 'initial') return -1
  const exactNumber = retry.match(/^-?\d+$/)
  if (exactNumber) return Number(retry)
  const match = retry.match(/(?:retry[-_:]?|r)(\d+)/i) || retry.match(/\d+/)
  return match ? Number(match[1] || match[0]) : 999
}

function cycleRetryLabel(cycle: DagRepairCycle): string {
  if (cycle.retry === 'initial') return 'Initial'
  const rank = cycleRetryRank(cycle.retry)
  return Number.isFinite(rank) && rank >= 0 ? `Retry ${rank}` : `Retry ${cycle.retry || 'next'}`
}

function cycleRouteKey(cycle: DagRepairCycle): string {
  if (cycle.retry === 'initial') return 'initial'
  const rank = cycleRetryRank(cycle.retry)
  return Number.isFinite(rank) && rank >= 0 ? `retry-${rank}` : `retry-${cycle.retry || 'next'}`.replace(/[^a-z0-9-]+/gi, '-')
}

function repairCyclesForRoute(model: DisplayModel): DagRepairCycle[] {
  const byKey = new Map<string, DagRepairCycle>()
  const putCycle = (cycle: DagRepairCycle) => {
    if (String(cycle.retry).trim() === '-1') return
    const key = `${cycle.group_idx}:${cycleRetryRank(cycle.retry)}`
    const existing = byKey.get(key)
    if (!existing) {
      byKey.set(key, cycle)
      return
    }
    byKey.set(key, {
      ...existing,
      ...cycle,
      started_at: existing.started_at && cycle.started_at
        ? (Date.parse(existing.started_at) <= Date.parse(cycle.started_at) ? existing.started_at : cycle.started_at)
        : existing.started_at || cycle.started_at,
      ended_at: existing.ended_at && cycle.ended_at
        ? (Date.parse(existing.ended_at) >= Date.parse(cycle.ended_at) ? existing.ended_at : cycle.ended_at)
        : existing.ended_at || cycle.ended_at,
      stage_durations: {
        ...(existing.stage_durations || {}),
        ...(cycle.stage_durations || {}),
      },
      lens_count: Math.max(existing.lens_count || 0, cycle.lens_count || 0),
      rca_group_count: Math.max(existing.rca_group_count || 0, cycle.rca_group_count || 0),
      fixable_group_count: Math.max(existing.fixable_group_count || 0, cycle.fixable_group_count || 0),
      scheduled_round_count: Math.max(existing.scheduled_round_count || 0, cycle.scheduled_round_count || 0),
      applied_fix_count: Math.max(existing.applied_fix_count || 0, cycle.applied_fix_count || 0),
      contradiction_count: Math.max(existing.contradiction_count || 0, cycle.contradiction_count || 0),
      rejected_contradiction_count: Math.max(existing.rejected_contradiction_count || 0, cycle.rejected_contradiction_count || 0),
      final_blocker_summary: cycle.final_blocker_summary || existing.final_blocker_summary,
    })
  }
  for (const cycle of model.repair?.cycles || []) {
    if (cycle.group_idx !== model.activeGroupIndex) continue
    putCycle(cycle)
  }
  const current = model.repair?.current_cycle
  if (current && current.group_idx === model.activeGroupIndex) {
    putCycle(current)
  }
  return [...byKey.values()].sort((a, b) => {
    const retryDelta = cycleRetryRank(a.retry) - cycleRetryRank(b.retry)
    if (retryDelta) return retryDelta
    const aStarted = Date.parse(a.started_at || '')
    const bStarted = Date.parse(b.started_at || '')
    if (!Number.isNaN(aStarted) && !Number.isNaN(bStarted) && aStarted !== bStarted) return aStarted - bStarted
    return (a.retry || '').localeCompare(b.retry || '')
  })
}

function cycleStageStatus(cycle: DagRepairCycle, stage: 'normal' | 'expanded' | 'rca' | 'repair' | 'reverify' | 'aggregate'): string {
  if (cycle.status === 'passed') return 'complete'
  if (stage === 'normal') return cycle.lens_count || cycle.rca_group_count || cycle.fixable_group_count ? 'failed' : cycle.status
  if (stage === 'expanded') return cycle.lens_count ? 'complete' : 'queued'
  if (stage === 'rca') return cycle.rca_group_count ? 'complete' : 'queued'
  if (stage === 'repair') return cycle.fixable_group_count ? (cycle.applied_fix_count >= cycle.fixable_group_count ? 'complete' : 'running') : 'queued'
  if (stage === 'reverify') {
    const allRepairsReturned = cycle.fixable_group_count > 0 && cycle.applied_fix_count >= cycle.fixable_group_count
    return allRepairsReturned ? 'running' : 'queued'
  }
  return cycle.status === 'failed' ? 'failed' : 'queued'
}

function agentMatchesRetry(agent: AgentActivity, retry: string | undefined): boolean {
  if (!retry || retry === 'initial') return !/-r\d+/i.test(agent.name)
  const rank = cycleRetryRank(retry)
  return Number.isFinite(rank) && rank >= 0
    ? new RegExp(`(?:^|[-:])r${rank}(?:[-:]|$)`, 'i').test(agent.name)
    : agent.name.toLowerCase().includes(retry.toLowerCase())
}

function agentsForRouteRoleAndCycle(model: DisplayModel, role: string, cycle: DagRepairCycle): AgentActivity[] {
  const roleAgents = agentsForRouteRole(model, role)
  return roleAgents.filter(agent => agentMatchesRetry(agent, cycle.retry))
}

function retryTokenForCycle(cycle: DagRepairCycle): string | null {
  const rank = cycleRetryRank(cycle.retry)
  return Number.isFinite(rank) && rank >= 0 ? `retry-${rank}` : null
}

function repairArtifactGroupId(key: string, prefix: string, cycle: DagRepairCycle): string | null {
  const retryToken = retryTokenForCycle(cycle)
  if (!retryToken) return null
  const marker = `${prefix}:g${cycle.group_idx}:`
  if (!key.startsWith(marker) || !key.endsWith(`:${retryToken}`)) return null
  return key.slice(marker.length, key.length - retryToken.length - 1)
}

function repairArtifactEntries(model: DisplayModel, prefix: string, cycle: DagRepairCycle): TimelineEntry[] {
  const byGroupId = new Map<string, TimelineEntry>()
  ;(model.timeline as TimelineEntry[]).forEach(entry => {
    const groupId = repairArtifactGroupId(entry.key || '', prefix, cycle)
    if (!groupId) return
    const existing = byGroupId.get(groupId)
    if (!existing || Date.parse(entry.created_at || '') > Date.parse(existing.created_at || '')) {
      byGroupId.set(groupId, entry)
    }
  })
  return [...byGroupId.values()].sort((a, b) => {
    const aTime = Date.parse(a.created_at || '')
    const bTime = Date.parse(b.created_at || '')
    if (!Number.isNaN(aTime) && !Number.isNaN(bTime) && aTime !== bTime) return aTime - bTime
    return a.key.localeCompare(b.key)
  })
}

function focusedReverifyAgentsForCycle(model: DisplayModel, cycle: DagRepairCycle): AgentActivity[] {
  return agentsForRouteRoleAndCycle(model, 'Verifiers', cycle).filter(agent => {
    const haystack = `${agent.name} ${agent.role} ${agent.task_id || ''} ${(agent.related_artifact_keys || []).join(' ')}`.toLowerCase()
    return haystack.includes('focused-reverify') || haystack.includes('dag-repair-reverify')
  })
}

function taskTitleFromRepairArtifact(entry: TimelineEntry | undefined, fallback: string): string {
  if (!entry) return fallback
  const groupId = entry.key.split(':')[2]
  return titleFromTaskId(groupId || entry.summary || fallback)
}

function buildFollowupTasks(model: DisplayModel): DisplayTask[] {
  const linkedTaskIds = linkedTaskIdsFor(model)
  const activeTaskIds = new Set(model.tasks.map(task => task.id))
  const upstreamDependencyCount = model.tasks.reduce(
    (sum, task) => sum + (task.dependencies || []).filter(dep => !activeTaskIds.has(dep)).length,
    0,
  )
  let sequence = 0
  const followups: DisplayTask[] = [
    routeTask(
      `g${model.activeGroupIndex}-merge`,
      'Group merge',
      `Merge ${model.tasks.length} implementation stations into one group candidate${upstreamDependencyCount ? ` after ${upstreamDependencyCount} upstream dependencies` : ''}.`,
      'merge',
      model.tasks.some(task => statusTone(task.status) !== 'complete') ? 'running' : 'complete',
      'Workflow',
      [`${model.tasks.length} task outputs`],
      'Merge',
      linkedTaskIds,
      { route_batch_key: 'merge', route_sequence: sequence++ },
    ),
  ]

  const cycles = repairCyclesForRoute(model)
  if (!cycles.length) {
    followups.push(
      routeTask(
        `g${model.activeGroupIndex}-normal-verify`,
        'Normal verifier',
        'Codex will run the normal group verifier before any checkpoint can be approved.',
        'verify',
        'queued',
        'Codex',
        ['checkpoint authority', `${model.tasks.length} tasks`],
        'Initial verify',
        linkedTaskIds,
        { route_batch_key: 'initial-normal-verify', route_sequence: sequence++ },
      ),
      routeTask(
        `g${model.activeGroupIndex}-aggregate-verify`,
        'Aggregate verifier',
        'Final Codex verifier station. This is the only station that can approve the group after repairs.',
        'verify',
        'queued',
        'Codex',
        ['full group approval', `${model.tasks.length} tasks`],
        'Aggregate verify',
        linkedTaskIds,
        { route_batch_key: 'initial-aggregate-verify', route_sequence: sequence++ },
      ),
    )
  }

  cycles.forEach((cycle, cycleIndex) => {
    const cycleLabel = cycleRetryLabel(cycle)
    const cycleKey = cycleRouteKey(cycle)
    const base = 100 + cycleIndex * 100
    const startedAt = cycle.started_at

    followups.push(routeTask(
      `g${model.activeGroupIndex}-${cycleKey}-normal-verify`,
      `${cycleLabel} normal verifier`,
      cycle.final_blocker_summary
        ? `Verifier result: ${cycle.final_blocker_summary}`
        : 'Codex checkpoint verification runs before repair can begin.',
      'verify',
      cycleStageStatus(cycle, 'normal'),
      'Codex',
      ['checkpoint authority', `${model.tasks.length} tasks`],
      `${cycleLabel}: Normal verify`,
      linkedTaskIds,
      { route_batch_key: `${cycleKey}-normal-verify`, route_sequence: base + 1, route_started_at: startedAt },
    ))

    const lensCount = cycle.lens_count || (cycle.retry === model.repair?.current_cycle?.retry && model.expandedVerifyRuns > 0 ? verifierLensNames.length : 0)
    Array.from({ length: lensCount }).forEach((_, index) => {
      const name = verifierLensNames[index] || `Verifier lens ${index + 1}`
      followups.push(routeTask(
        `g${model.activeGroupIndex}-${cycleKey}-lens-${index + 1}`,
        name,
        'Read-only expanded verification station. It broadens discovery before RCA and repair, but cannot approve the checkpoint by itself.',
        'verify',
        cycleStageStatus(cycle, 'expanded'),
        index === 3 || index === 2 ? 'Codex' : 'Claude',
        ['read-only lens', `${model.tasks.length} tasks`],
        `${cycleLabel}: Expanded verify`,
        linkedTaskIds,
        { route_batch_key: `${cycleKey}-expanded-verify`, route_sequence: base + 10 + index, route_started_at: startedAt },
      ))
    })

    if (cycle.rca_group_count > 0) {
      const rcaAgents = agentsForRouteRoleAndCycle(model, 'RCA', cycle)
      Array.from({ length: cycle.rca_group_count }).forEach((_, index) => {
        const agent = rcaAgents[index]
        const title = titleFromAgentName(agent?.name, `Issue cluster ${index + 1}`)
        followups.push(routeTaskWithAgent(
          `g${model.activeGroupIndex}-${cycleKey}-rca-${index + 1}`,
          `RCA: ${title}`,
          'Root-cause task for one concrete cluster of verifier and lens findings. The selected agent prompt/output explains what evidence it analyzed and which repair path it proposed.',
          'repair',
          cycleStageStatus(cycle, 'rca'),
          'Claude',
          ['root-cause task', `${model.tasks.length} implementation tasks`],
          `${cycleLabel}: RCA dispatch`,
          linkedTaskIds,
          agent,
          { route_batch_key: `${cycleKey}-rca`, route_sequence: base + 20 + index, route_started_at: agent?.started_at || startedAt },
        ))
      })
    }

    if ((cycle.contradiction_count || 0) > 0) {
      const decisionAgents = agentsForRouteRoleAndCycle(model, 'Decisions', cycle)
      Array.from({ length: cycle.contradiction_count || 0 }).forEach((_, index) => {
        const agent = decisionAgents[index]
        const title = titleFromAgentName(agent?.name, `Decision task ${index + 1}`)
        followups.push(routeTaskWithAgent(
          `g${model.activeGroupIndex}-${cycleKey}-contradiction-${index + 1}`,
          `Decision: ${title}`,
          cycle.rejected_contradiction_count
            ? 'The resolver rejected this contradiction output, so the route keeps it visible as a blocked decision station.'
            : 'Codex contradiction resolution station. Accepted decisions become authoritative context for the next verifier and repair pass.',
          'decision',
          (cycle.rejected_contradiction_count || 0) > index ? 'blocked' : 'complete',
          'Codex',
          ['decision'],
          `${cycleLabel}: Contradiction resolution`,
          linkedTaskIds,
          agent,
          { route_batch_key: `${cycleKey}-decision`, route_sequence: base + 30 + index, route_started_at: agent?.started_at || startedAt },
        ))
      })
    }

    if (cycle.fixable_group_count > 0) {
      const fixAgents = agentsForRouteRoleAndCycle(model, 'Fixers', cycle)
      const scheduledRoundCount = Math.max(cycle.scheduled_round_count || 0, 1)
      const groupsPerRound = Math.max(1, Math.ceil(cycle.fixable_group_count / scheduledRoundCount))
      Array.from({ length: cycle.fixable_group_count }).forEach((_, index) => {
        const applied = cycle.applied_fix_count || 0
        const repairRound = Math.min(scheduledRoundCount, Math.floor(index / groupsPerRound) + 1)
        const agent = fixAgents[index]
        const title = titleFromAgentName(agent?.name, `Repair task ${index + 1}`)
        followups.push(routeTaskWithAgent(
          `g${model.activeGroupIndex}-${cycleKey}-fix-${index + 1}`,
          `Repair: ${title}`,
          'Repair task dispatched from RCA output. Non-overlapping file scopes can run together; final aggregate verification still decides whether the group advances.',
          'repair',
          applied > index ? 'complete' : 'running',
          'Claude',
          ['fix dispatch'],
          `${cycleLabel}: Repair wave ${repairRound}/${scheduledRoundCount}`,
          linkedTaskIds,
          agent,
          { route_batch_key: `${cycleKey}-repair-${repairRound}`, route_sequence: base + 40 + repairRound + index / 100, route_started_at: agent?.started_at || startedAt },
        ))
      })
    }

    const focusedReverifyArtifacts = repairArtifactEntries(model, 'dag-repair-reverify', cycle)
    const focusedReverifyAgents = focusedReverifyAgentsForCycle(model, cycle)
    const observedFocusedReverifyCount = Math.max(focusedReverifyArtifacts.length, focusedReverifyAgents.length)
    const fallbackFocusedReverifyCount = Math.min(
      cycle.applied_fix_count || 0,
      cycle.fixable_group_count || cycle.applied_fix_count || 0,
    )
    const focusedReverifyCount = observedFocusedReverifyCount || fallbackFocusedReverifyCount
    if (focusedReverifyCount > 0) {
      Array.from({ length: focusedReverifyCount }).forEach((_, index) => {
        const agent = focusedReverifyAgents[index]
        const artifact = focusedReverifyArtifacts[index]
        const artifactTitle = taskTitleFromRepairArtifact(artifact, `Repair group ${index + 1}`)
        const title = titleFromAgentName(agent?.name, artifactTitle)
        followups.push(routeTaskWithAgent(
          `g${model.activeGroupIndex}-${cycleKey}-focused-reverify-${index + 1}`,
          `Reverify: ${title}`,
          artifact?.summary || 'Focused verification of one returned repair group before the full aggregate verifier runs.',
          'verify',
          agent?.status || cycleStageStatus(cycle, 'reverify'),
          'Claude',
          ['focused reverify', artifact?.key.split(':')[2] || `repair group ${index + 1}`],
          `${cycleLabel}: Focused reverify`,
          linkedTaskIds,
          agent,
          {
            route_batch_key: `${cycleKey}-focused-reverify`,
            route_sequence: base + 60 + index / 100,
            route_started_at: agent?.started_at || artifact?.created_at || startedAt,
            route_ended_at: agent?.ended_at || artifact?.created_at,
          },
        ))
      })
    }

    followups.push(routeTask(
      `g${model.activeGroupIndex}-${cycleKey}-aggregate-verify`,
      `${cycleLabel} aggregate verifier`,
      'Final Codex verifier station. This is the only station that can approve the group after repairs.',
      'verify',
      cycleStageStatus(cycle, 'aggregate'),
      'Codex',
      ['full group approval', `${model.tasks.length} tasks`],
      `${cycleLabel}: Aggregate verify`,
      linkedTaskIds,
      { route_batch_key: `${cycleKey}-aggregate-verify`, route_sequence: base + 70, route_started_at: startedAt, route_ended_at: cycle.ended_at },
    ))
  })

  followups.push(routeTask(
    `g${model.activeGroupIndex}-checkpoint`,
    `Group ${model.activeGroupIndex} checkpoint`,
    'Persist the group checkpoint after aggregate verification approves every implementation and repair obligation.',
    'checkpoint',
    'queued',
    'Workflow',
    ['persist progress'],
    'Checkpoint',
    linkedTaskIds,
    { route_batch_key: 'checkpoint', route_sequence: 10000 },
  ))
  return followups
}

function routeTaskBadge(task: DisplayTask, model: DisplayModel): string {
  if (task.route_kind === 'implementation') return task.route_wave_label || `Wave ${model.activeGroupIndex}`
  if (task.route_wave_label?.includes('Expanded verify')) return 'Verifier lens'
  if (task.route_wave_label?.includes('RCA dispatch')) return 'RCA task'
  if (task.route_wave_label?.includes('Repair wave')) return 'Repair task'
  if (task.route_wave_label?.includes('Focused reverify')) return 'Reverify task'
  if (task.route_wave_label?.includes('Contradiction resolution')) return 'Decision task'
  if (task.route_wave_label?.includes('Aggregate verify')) return 'Verifier task'
  if (task.route_wave_label?.includes('Initial verify') || task.route_wave_label?.includes('Normal verify')) return 'Verifier task'
  if (task.route_wave_label?.includes('Merge')) return 'Merge task'
  if (task.route_wave_label?.includes('Checkpoint')) return 'Checkpoint task'
  return 'Workflow task'
}

function RouteTaskStation({ task, model, selected, onSelectTask, index }: {
  task: DisplayTask
  model: DisplayModel
  selected: boolean
  onSelectTask: (taskKey: string) => void
  index: number
}) {
  const runtime = taskRuntime(task, model.agents)
  return (
    <button
      type="button"
      className={`route-task-station ${task.route_kind || 'implementation'} ${statusTone(task.status)} ${selected ? 'selected' : ''}`}
      aria-pressed={!!selected}
      onClick={() => onSelectTask(taskSelectionKey(task))}
      style={{ '--station-index': index } as CSSProperties}
    >
      <span className="route-station-dot" />
      <TaskCardBody
        task={task}
        runtime={runtime}
        badge={routeTaskBadge(task, model)}
        refsLabel={task.route_kind === 'implementation' ? 'gates' : 'refs'}
        summaryMax={112}
      />
      {!!task.linked_task_ids?.length && (
        <div className="route-linked-tasks">
          <span>{task.route_kind === 'verify' ? 'Checks tasks' : 'Related tasks'}</span>
          <div>
            {taskBatchPreview(task.linked_task_ids).map(taskId => <code key={taskId}>{taskId}</code>)}
            {task.linked_task_ids.length > 4 && <em>+{task.linked_task_ids.length - 4}</em>}
          </div>
        </div>
      )}
    </button>
  )
}

function ActiveGroupRouteMap({ model, selectedTaskKey, onSelectTask }: {
  model: DisplayModel
  selectedTaskKey: string | null
  onSelectTask: (taskKey: string) => void
}) {
  const timelineBatches = useMemo(() => routeBatchesForModel(model), [model])
  const activeDispatchIndex = useMemo(() => activeRouteBatchIndexForModel(model, timelineBatches), [model, timelineBatches])
  const activeDispatchRef = useRef<HTMLLIElement | null>(null)
  const scrollToCurrentDispatch = () => {
    activeDispatchRef.current?.scrollIntoView({
      behavior: 'smooth',
      block: 'center',
      inline: 'nearest',
    })
  }

  return (
    <section className="group-route-map">
      <div className="group-route-line">
        <div className="route-stage dispatch-history">
          <div className="route-stage-label">
            <div>
              <span>Checkpoint route</span>
              <strong>{timelineBatches.length} dispatches</strong>
              <small>Group {model.activeGroupIndex} routes Studio work through verifier, RCA, repair, reverify, and checkpoint gates.</small>
            </div>
            <button
              type="button"
              className="scroll-current-dispatch"
              disabled={activeDispatchIndex < 0}
              onClick={scrollToCurrentDispatch}
            >
              Jump to active route stop
            </button>
          </div>
          <ol className="route-timeline" aria-label="Implementation, verifier, and repair dispatch timeline">
            {timelineBatches.map((batch, batchIndex) => (
              <li
                className={`route-timeline-batch ${batch.tasks[0]?.route_kind || 'workflow'} ${batchIndex === activeDispatchIndex ? 'current-dispatch' : ''}`}
                key={`${batch.label}-${batchIndex}`}
                ref={batchIndex === activeDispatchIndex ? activeDispatchRef : undefined}
              >
                <div className="route-timeline-marker">
                  <span>{batchIndex + 1}</span>
                </div>
                <div className="route-timeline-content">
                  <div className="route-wave-group-head">
                    <span>{batchIndex === activeDispatchIndex ? `Current dispatch · ${batch.label}` : batch.label}</span>
                    <strong>{batch.title}</strong>
                    <small>{batch.detail}</small>
                  </div>
                  <div className="route-followup-track">
                    {batch.tasks.map((task, index) => (
                      <RouteTaskStation
                        key={taskSelectionKey(task)}
                        task={task}
                        model={model}
                        selected={selectedTaskKey === taskSelectionKey(task)}
                        onSelectTask={onSelectTask}
                        index={index}
                      />
                    ))}
                  </div>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  )
}

function DagMapView({ model, selectedTask, selectedTaskKey, onSelectTask }: {
  model: DisplayModel
  selectedTask: DisplayTask | null
  selectedTaskKey: string | null
  onSelectTask: (taskKey: string) => void
}) {
  const followupTasks = useMemo(() => buildFollowupTasks(model), [model])
  const dispatchCount = 1 + groupedRouteBatches(followupTasks).length
  const latestCheckpoint = model.repair?.latest_checkpoint_group ?? Math.max(model.activeGroupIndex - 1, 0)
  const checkpointedLabel = latestCheckpoint > 0 ? `0-${latestCheckpoint}` : `${latestCheckpoint}`
  const remainingStart = Math.min(model.activeGroupIndex + 1, Math.max(model.totalGroups - 1, 0))
  const remainingLabel = model.activeGroupIndex >= model.totalGroups - 1
    ? 'none'
    : `${remainingStart}-${model.totalGroups - 1}`
  return (
    <ViewFrame>
      <div className="dag-map">
        <div className="exhibit-main-column dag-route-column">
          <header className="map-header">
            <div className="map-header-copy">
              <span className="view-kicker">DAG Map</span>
              <h1>Checkpoint {model.activeGroupIndex}: Studio repair route.</h1>
              <p>Follow the {model.tasks.length}-task group from completed Studio implementation work through merge, verifier lenses, RCA-backed repair groups, focused reverify, aggregate approval, and checkpoint persistence.</p>
            </div>
            <div className="map-header-summary">
              <div className="active">
                <strong>{model.activeGroupIndex}</strong>
                <span>active group</span>
              </div>
              <div>
                <strong>{checkpointedLabel}</strong>
                <span>groups done</span>
              </div>
              <div>
                <strong>{remainingLabel}</strong>
                <span>groups left</span>
              </div>
              <div>
                <strong>{model.tasks.length}</strong>
                <span>tasks</span>
              </div>
              <div>
                <strong>{dispatchCount}</strong>
                <span>dispatches</span>
              </div>
            </div>
          </header>
          <ActiveGroupRouteMap model={model} selectedTaskKey={selectedTaskKey} onSelectTask={onSelectTask} />
        </div>
        <aside className="dag-inspector-panel" key={selectedTask?.id || 'empty-task-detail'}>
          <TaskInspector task={selectedTask} agents={model.agents} />
        </aside>
      </div>
    </ViewFrame>
  )
}

function AgentCard({ agent, assignedTask, selected, onSelect }: {
  agent: AgentActivity
  assignedTask?: DisplayTask
  selected?: boolean
  onSelect?: () => void
}) {
  const assignedRuntime = assignedTask ? taskRuntime(assignedTask, [agent]) : agent.runtime || 'runtime'
  return (
    <button
      type="button"
      className={`agent-card ${statusTone(agent.status)} ${selected ? 'selected' : ''}`}
      aria-pressed={!!selected}
      onClick={onSelect}
    >
      <div className="agent-card-head">
        <span className="status-led" />
        <div>
          <h3>{agent.name}</h3>
          <p>{agent.role} - {agent.runtime || 'runtime'} - {agent.status}</p>
        </div>
        <span className="agent-duration">{compactDuration(agent.duration_seconds)}</span>
      </div>
      <div className="agent-task-context">
        <span>Current Studio task</span>
        {assignedTask ? (
          <TaskCardBody
            task={assignedTask}
            runtime={assignedRuntime}
            badge={assignedTask.subfeature_id || assignedTask.route_wave_label || 'Task'}
            summaryMax={96}
          />
        ) : (
          <p>{agentPromptTextForTask(agent)}</p>
        )}
      </div>
      <div className="agent-peek">
        <strong>Dispatch brief</strong>
        <p title={agentPromptTextForTask(agent, assignedTask)}>{agentPromptTextForTask(agent, assignedTask)}</p>
      </div>
      <div className="agent-peek output">
        <strong>Latest return</strong>
        <p title={agentOutputTextForTask(agent, assignedTask)}>{agentOutputTextForTask(agent, assignedTask)}</p>
      </div>
      {!!agent.related_artifact_keys?.length && (
        <div className="token-row">
          {agent.related_artifact_keys.slice(0, 3).map(key => <code key={key}>{key}</code>)}
        </div>
      )}
    </button>
  )
}

function AgentDetail({ agent, assignedTask }: { agent: AgentActivity | null; assignedTask?: DisplayTask }) {
  if (!agent) {
    return (
      <EmptyDetail
        kicker="Studio agent trace"
        title="Choose a repair or verification run"
        body="Open a Claude or Codex run to see the Iriai Studio task it touched, the dispatch brief, returned evidence, artifacts, and files."
      />
    )
  }
  return (
    <DetailRail
      kicker={`${agent.role || 'Agent'} / ${agent.runtime || 'runtime'}`}
      title={agent.name}
      body={agentOutputTextForTask(agent, assignedTask)}
    >
      <div className="detail-section">
        <span>Run state</span>
        <div className="token-row">
          <StatusPill tone={statusTone(agent.status)}>{agent.status || 'unknown'}</StatusPill>
          <code>{compactDuration(agent.duration_seconds)}</code>
          {agent.group_idx !== undefined && agent.group_idx !== null && <code>g{agent.group_idx}</code>}
        </div>
      </div>
      <div className="detail-section">
        <span>Studio task</span>
        {assignedTask ? (
          <>
            <p><strong>{taskDisplayTitle(assignedTask)}</strong></p>
            <p>{assignedTask.summary || assignedTask.description}</p>
            <div className="token-row">
              <code>{assignedTask.id}</code>
              <code>{assignedTask.repo_path || assignedTask.subfeature_id || 'workspace'}</code>
            </div>
          </>
        ) : (
          <p>Studio task context pending; this agent has not been linked to a public route card yet.</p>
        )}
      </div>
      <div className="detail-section">
        <span>Dispatch brief</span>
        <p>{agentPromptTextForTask(agent, assignedTask)}</p>
      </div>
      <div className="detail-section">
        <span>Latest repair/verifier return</span>
        <p>{agentOutputTextForTask(agent, assignedTask)}</p>
      </div>
      {!!agent.related_artifact_keys?.length && (
        <div className="detail-section">
          <span>Evidence artifacts</span>
          <div className="token-row">
            {agent.related_artifact_keys.slice(0, 8).map(key => <code key={key}>{key}</code>)}
            {agent.related_artifact_keys.length > 8 && <em>+{agent.related_artifact_keys.length - 8} more</em>}
          </div>
        </div>
      )}
      {!!agent.related_files?.length && (
        <div className="detail-section">
          <span>Studio files touched</span>
          <div className="file-list">{agent.related_files.slice(0, 8).map(file => <code key={file}>{shortPath(file)}</code>)}</div>
        </div>
      )}
    </DetailRail>
  )
}

function AgentFloorView({ model, selectedAgentName, onSelectAgent }: {
  model: DisplayModel
  selectedAgentName: string | null
  onSelectAgent: (name: string) => void
}) {
  const agentTasks = useMemo(() => [...model.tasks, ...buildFollowupTasks(model)], [model])
  const activeRoute = useMemo(() => activeRouteBatchForModel(model), [model])
  const currentBatch = activeRoute.batch
  const currentBatchTasks = currentBatch?.tasks || []
  const currentAgentKeys = new Set(currentBatchTasks.map(task => task.agent_key).filter(Boolean))
  const currentAgents = currentAgentKeys.size
    ? model.agents.filter(agent => currentAgentKeys.has(agentKey(agent)))
    : model.agents.filter(agent =>
      agentMatchesActiveGroup(agent, model.activeGroupIndex)
      && agentMatchesRouteBatch(agent, currentBatch)
      && statusTone(agent.status) !== 'complete',
    )
  const selectedAgent = selectedAgentName ? currentAgents.find(agent => agentKey(agent) === selectedAgentName) || null : null
  const selectedAgentTask = selectedAgent ? taskForAgent(selectedAgent, agentTasks) : undefined
  const roleSummary = Object.entries(currentAgents.reduce<Record<string, number>>((acc, agent) => {
    const role = agentRoleBucket(agent)
    acc[role] = (acc[role] || 0) + 1
    return acc
  }, {}))
    .map(([role, count]) => `${count} ${role.toLowerCase()}`)
    .join(' / ')
  const runtimeSummary = Array.from(new Set(currentAgents.map(agent => agent.runtime || 'runtime'))).join(' + ') || 'waiting'
  const dispatchLabel = currentBatch
    ? currentBatch.label.replace(/^Current dispatch ·\s*/i, '')
    : 'No current dispatch'
  return (
    <ViewFrame>
      <div className="agent-floor">
        <div className="exhibit-main-column">
          <header className="floor-header agent-wave-hero">
            <div>
              <span className="view-kicker">Agent Floor</span>
              <h1>{currentBatch ? `${currentBatch.title} is live.` : 'The next dispatch is being assembled.'}</h1>
              <p>
                Showing only agents attached to the current group dispatch, so this floor stays focused on the work happening right now instead of every historical Claude and Codex run.
              </p>
            </div>
            <div className="agent-wave-summary">
              <div>
                <span>Dispatch</span>
                <strong>{dispatchLabel}</strong>
              </div>
              <div>
                <span>Agents</span>
                <strong>{currentAgents.length}</strong>
              </div>
              <div>
                <span>Runtime mix</span>
                <strong>{runtimeSummary}</strong>
              </div>
            </div>
          </header>
          <section className="agent-wave-board">
            <div className="agent-wave-heading">
              <div>
                <span>Current dispatch only</span>
                <strong>{currentBatch?.detail || `${currentBatchTasks.length} route tasks`}</strong>
              </div>
              <p>{roleSummary || 'No model workers are attached to this dispatch yet; the route is waiting for the next agent event.'}</p>
            </div>
            {currentAgents.length ? (
              <div className="agent-wave-grid">
                {currentAgents.map(agent => (
                  <AgentCard
                    key={agentKey(agent)}
                    agent={agent}
                    assignedTask={currentBatchTasks.find(task => task.agent_key === agentKey(agent)) || taskForAgent(agent, agentTasks)}
                    selected={selectedAgentName === agentKey(agent)}
                    onSelect={() => onSelectAgent(agentKey(agent))}
                  />
                ))}
              </div>
            ) : (
              <div className="empty-agent-wave">
                <strong>No agents are currently mapped to this dispatch.</strong>
                <p>The route still knows the next Studio tasks; agent cards will appear here as soon as the bridge starts the worker runs.</p>
              </div>
            )}
          </section>
          <section className="agent-wave-manifest" aria-label="Current dispatch task manifest">
            <div className="agent-wave-manifest-head">
              <span>Dispatch task manifest</span>
              <strong>{currentBatchTasks.length} task{currentBatchTasks.length === 1 ? '' : 's'}</strong>
            </div>
            <div className="agent-wave-task-list">
              {currentBatchTasks.map(task => {
                const linkedAgent = task.agent_key ? currentAgents.find(agent => agentKey(agent) === task.agent_key) : undefined
                return (
                  <button
                    type="button"
                    key={taskSelectionKey(task)}
                    className={`agent-wave-task-chip ${statusTone(task.status)} ${linkedAgent && selectedAgentName === agentKey(linkedAgent) ? 'selected' : ''}`}
                    disabled={!linkedAgent}
                    onClick={() => linkedAgent && onSelectAgent(agentKey(linkedAgent))}
                  >
                    <span>{routeTaskBadge(task, model)}</span>
                    <strong>{taskDisplayTitle(task)}</strong>
                    <small>{linkedAgent ? linkedAgent.name : task.runtime || taskRuntime(task, model.agents)}</small>
                  </button>
                )
              })}
            </div>
          </section>
        </div>
        <AgentDetail agent={selectedAgent} assignedTask={selectedAgentTask} />
      </div>
    </ViewFrame>
  )
}

function ArtifactPreviewOverlay({ artifact, onClose }: {
  artifact: ArtifactCard
  onClose: () => void
}) {
  const url = artifactContentUrl(artifact)
  const renderMode = artifactRenderMode(artifact)
  const closeButtonRef = useRef<HTMLButtonElement | null>(null)
  const [readerState, setReaderState] = useState<{
    status: 'loading' | 'ready' | 'unavailable' | 'error'
    content?: string
    objectUrl?: string
    contentType?: string
    message?: string
  }>({ status: url ? 'loading' : 'unavailable', message: url ? undefined : 'This artifact has not been published to the public artifact database yet.' })
  const closeOnBackdrop = (event: MouseEvent<HTMLDivElement>) => {
    if (event.target === event.currentTarget) onClose()
  }

  useEffect(() => {
    closeButtonRef.current?.focus()
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [onClose])

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | undefined
    if (!url) {
      setReaderState({
        status: 'unavailable',
        message: 'This artifact has not been published to the public artifact database yet.',
      })
      return
    }
    setReaderState({ status: 'loading' })
    fetch(url, { headers: { accept: '*/*' } })
      .then(async response => {
        if (!response.ok) {
          throw new Error(`Artifact reader returned ${response.status}`)
        }
        const contentType = response.headers.get('content-type') || ''
        if (renderMode === 'image' || renderMode === 'video') {
          const blob = await response.blob()
          objectUrl = URL.createObjectURL(blob)
          if (!cancelled) setReaderState({ status: 'ready', objectUrl, contentType })
        } else {
          const content = await response.text()
          if (!cancelled) setReaderState({ status: 'ready', content, contentType })
        }
      })
      .catch(error => {
        if (!cancelled) {
          setReaderState({
            status: 'error',
            message: error instanceof Error ? error.message : 'Unable to load artifact content.',
          })
        }
      })
    return () => {
      cancelled = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [renderMode, url])

  return (
    <motion.div
      className="artifact-reader-backdrop"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      role="dialog"
      aria-modal="true"
      aria-label={`Artifact preview: ${artifact.title}`}
      onMouseDown={closeOnBackdrop}
    >
      <motion.div
        className="artifact-reader"
        initial={{ y: 24, scale: 0.982 }}
        animate={{ y: 0, scale: 1 }}
        exit={{ y: 16, scale: 0.986 }}
        transition={{ duration: 0.18, ease: 'easeOut' }}
      >
        <header>
          <div>
            <span>{artifact.family || 'Artifact'}</span>
            <h2>{artifact.title}</h2>
            <p>{artifact.key}</p>
          </div>
          <div className="artifact-reader-actions">
            <button
              ref={closeButtonRef}
              type="button"
              onClick={onClose}
              onPointerDown={(event) => {
                event.preventDefault()
                onClose()
              }}
            >
              Close
            </button>
          </div>
        </header>
        <div className="artifact-reader-frame">
          {readerState.status === 'loading' && (
            <div className="artifact-frame-notice">
              <strong>Loading artifact</strong>
              <p>Fetching the public-safe database copy for the reader.</p>
            </div>
          )}
          {readerState.status === 'error' && (
            <div className="artifact-frame-notice">
              <strong>Artifact unavailable</strong>
              <p>{readerState.message || 'The public artifact content could not be loaded.'}</p>
            </div>
          )}
          {readerState.status === 'unavailable' && (
            <div className="artifact-preview-sheet">
              <span>Public artifact pending</span>
              <h3>{artifact.title}</h3>
              <p>{readerState.message}</p>
              <p>{artifact.summary || 'This artifact is listed from workflow metadata, but the public dashboard backend has not stored a safe reader copy yet.'}</p>
            </div>
          )}
          {readerState.status === 'ready' && renderMode === 'html' && (
            <iframe
              title={`Artifact preview ${artifact.title}`}
              srcDoc={readerState.content || ''}
              sandbox=""
              referrerPolicy="no-referrer"
            />
          )}
          {readerState.status === 'ready' && renderMode === 'json' && (
            <pre className="artifact-reader-text">{formattedJson(readerState.content || '')}</pre>
          )}
          {readerState.status === 'ready' && (renderMode === 'markdown' || renderMode === 'text' || !['html', 'json', 'image', 'video'].includes(renderMode)) && (
            <pre className="artifact-reader-text">{readerState.content}</pre>
          )}
          {readerState.status === 'ready' && renderMode === 'image' && readerState.objectUrl && (
            <img className="artifact-reader-media" src={readerState.objectUrl} alt={artifact.title} />
          )}
          {readerState.status === 'ready' && renderMode === 'video' && readerState.objectUrl && (
            <video className="artifact-reader-media" src={readerState.objectUrl} controls />
          )}
        </div>
        <footer className="artifact-reader-provenance">
          <span>Digest</span>
          <code>{artifact.sha256 || 'pending'}</code>
          <span>Safety</span>
          <code>{artifact.safety_status || (artifact.public_safe ? 'passed' : 'not published')}</code>
          <span>Source</span>
          <code>{artifact.source || 'source pending'}</code>
        </footer>
      </motion.div>
    </motion.div>
  )
}

function ArtifactDetail({ artifact, onPreview }: {
  artifact: ArtifactCard | null
  onPreview: () => void
}) {
  if (!artifact) {
    return (
      <EmptyDetail
        kicker="Studio proof object"
        title="Choose an artifact"
        body="Open a public-safe proof card to see which Iriai Studio requirement, design, DAG, decision, or checkpoint artifact backs the build."
      />
    )
  }
  const readerReady = Boolean(artifactContentUrl(artifact))
  return (
    <DetailRail
      kicker={`${artifact.family || 'artifact'} / ${artifact.status || 'status unknown'}`}
      title={artifact.title}
      body={artifact.summary}
    >
      <div className="artifact-open-panel">
        <span>Artifact reader</span>
        <p>{readerReady ? 'Open the public-safe database copy in an overlay reader. The dashboard never links to local files, tunnels, or raw workflow-hosted pages.' : 'This artifact is selected from workflow metadata, but its public-safe database copy has not been published yet.'}</p>
        <div className="artifact-open-actions">
          <button type="button" onClick={onPreview}>View Artifact</button>
        </div>
      </div>
      <div className="detail-section">
        <span>Artifact key</span>
        <div className="token-row"><code>{artifact.key}</code></div>
      </div>
      <div className="detail-section">
        <span>Freshness</span>
        <p>{artifact.created_at ? relTime(artifact.created_at) : 'No timestamp reported.'}</p>
      </div>
      <div className="detail-section">
        <span>Provenance</span>
        <div className="token-row">
          <code>{artifact.source || 'source pending'}</code>
          <code>{artifact.public_safe ? 'public-safe' : 'internal-only'}</code>
        </div>
      </div>
    </DetailRail>
  )
}

function ArtifactTrailView({ model, selectedArtifactKey, onSelectArtifact }: {
  model: DisplayModel
  selectedArtifactKey: string | null
  onSelectArtifact: (key: string) => void
}) {
  const artifacts = model.artifacts.slice(0, 10)
  const selectedArtifact = selectedArtifactKey ? artifacts.find(artifact => artifact.key === selectedArtifactKey) || null : null
  const [previewArtifactKey, setPreviewArtifactKey] = useState<string | null>(null)
  const previewArtifact = artifacts.find(artifact => artifact.key === previewArtifactKey) || null
  return (
    <ViewFrame>
      <div className="artifact-trail">
        <div className="exhibit-main-column">
          <header className="museum-header">
            <span className="view-kicker">Artifact Trail</span>
            <h1>Proof trail for the Studio build.</h1>
            <p>PRDs, sidecars, DAGs, decisions, audits, and gate reviews show how the VS Code workbench is being specified, built, checked, and repaired.</p>
          </header>
          <div className="artifact-gallery">
            {artifacts.map((artifact, index) => (
              <button
                type="button"
                className={`artifact-pedestal ${selectedArtifactKey === artifact.key ? 'selected' : ''}`}
                aria-pressed={selectedArtifactKey === artifact.key}
                key={artifact.key}
                onClick={() => onSelectArtifact(artifact.key)}
              >
                <div className="artifact-index">{String(index + 1).padStart(2, '0')}</div>
                <span>{artifact.family}</span>
                <h2 title={artifact.title}>{artifact.title}</h2>
                <p title={artifact.summary}>{artifact.summary}</p>
                <div className="artifact-card-actions">
                  <span>Select artifact</span>
                  <span>{artifact.public_safe ? 'Public-safe' : 'Internal'}</span>
                </div>
                <footer>
                  <small>{artifact.status}</small>
                  <small>{artifact.source}</small>
                </footer>
              </button>
            ))}
          </div>
        </div>
        <ArtifactDetail artifact={selectedArtifact} onPreview={() => selectedArtifact && setPreviewArtifactKey(selectedArtifact.key)} />
      </div>
      <AnimatePresence>
        {previewArtifact && (
          <ArtifactPreviewOverlay
            artifact={previewArtifact}
            onClose={() => setPreviewArtifactKey(null)}
          />
        )}
      </AnimatePresence>
    </ViewFrame>
  )
}

function WorkstreamDetail({ workstream }: { workstream: DisplayWorkstream | null }) {
  if (!workstream) {
    return (
      <EmptyDetail
        kicker="Studio product track"
        title="Choose a workstream"
        body="Open a Studio track to see which shell, backend, bridge, project, chat, or phase-view capabilities it contributes."
      />
    )
  }
  return (
    <DetailRail
      kicker={workstream.status || 'workstream'}
      title={workstream.name}
      body={workstreamDisplaySummary(workstream)}
    >
      <div className="detail-section">
        <span>Progress</span>
        <ProgressMeter label="Tasks" done={workstream.completed_tasks || 0} total={Math.max(workstream.total_tasks || 1, 1)} />
      </div>
      {!!workstream.subfeature_slugs?.length && (
        <div className="detail-section">
          <span>Subfeatures</span>
          <div className="token-row">
            {workstream.subfeature_slugs.slice(0, 10).map(slug => <code key={slug}>{slug}</code>)}
            {workstream.subfeature_slugs.length > 10 && <em>+{workstream.subfeature_slugs.length - 10} more</em>}
          </div>
        </div>
      )}
    </DetailRail>
  )
}

function workstreamDisplaySummary(workstream: DisplayWorkstream): string {
  const summary = (workstream.summary || '').trim()
  if (summary && !/^\d+\s+subfeature\(s\)\s+contributing/i.test(summary)) return summary
  const slugs = workstream.subfeature_slugs || []
  const surfaces = slugs.slice(0, 4).map(slug => titleFromTaskId(slug))
  if (surfaces.length) {
    const listed = surfaces.join(', ')
    const suffix = slugs.length > surfaces.length ? `, plus ${slugs.length - surfaces.length} more Studio surface${slugs.length - surfaces.length === 1 ? '' : 's'}` : ''
    return `${workstream.name} owns ${listed}${suffix} for the Iriai Studio build.`
  }
  return 'Studio track summary will appear as the bridge generates public narrative artifacts.'
}

function WorkstreamsView({ model, selectedWorkstreamId, onSelectWorkstream }: {
  model: DisplayModel
  selectedWorkstreamId: string | null
  onSelectWorkstream: (id: string) => void
}) {
  const rows = model.workstreams.length ? model.workstreams : [
    {
      id: 'current',
      name: 'Current feature implementation',
      summary: model.currentFocus,
      status: model.health,
      completed_tasks: model.completedTasks,
      total_tasks: model.totalTasks,
      subfeature_slugs: ['feature-wide DAG'],
    },
  ]
  const selectedWorkstream = selectedWorkstreamId ? rows.find(workstream => workstream.id === selectedWorkstreamId) || null : null
  return (
    <ViewFrame>
      <div className="workstreams-view">
        <div className="exhibit-main-column">
          <header className="floor-header">
            <span className="view-kicker">Workstreams</span>
            <h1>Five tracks make Iriai Studio real.</h1>
            <p>Shell and bridge foundation, execution core, coordination, project launcher, chat, and phase views move together through the checkpointed build.</p>
          </header>
          <div className="workstream-list">
            {rows.map(workstream => (
              <button
                type="button"
                className={`workstream-row ${selectedWorkstreamId === workstream.id ? 'selected' : ''}`}
                aria-pressed={selectedWorkstreamId === workstream.id}
                key={workstream.id}
                onClick={() => onSelectWorkstream(workstream.id)}
              >
                <div>
                  <span>{workstream.status || 'active'}</span>
                  <h2>{workstream.name}</h2>
                  <p>{workstreamDisplaySummary(workstream)}</p>
                  <div className="token-row">
                    {(workstream.subfeature_slugs || []).slice(0, 5).map(slug => <code key={slug}>{slug}</code>)}
                  </div>
                </div>
                <ProgressMeter label="Tasks" done={workstream.completed_tasks || 0} total={Math.max(workstream.total_tasks || 1, 1)} />
              </button>
            ))}
          </div>
        </div>
        <WorkstreamDetail workstream={selectedWorkstream} />
      </div>
    </ViewFrame>
  )
}

function MilestoneDetail({ item }: { item: MilestoneDisplayItem | null }) {
  if (!item) {
    return (
      <EmptyDetail
        kicker="Studio milestone"
        title="Choose a checkpoint story"
        body="Open a milestone to see what the Iriai Studio workflow proved and which artifact produced the public narrative."
      />
    )
  }
  return (
    <DetailRail
      kicker={item.type}
      title={item.title || 'Studio milestone'}
      body={item.summary}
    >
      <div className="detail-section">
        <span>When</span>
        <p>{relTime(item.created_at)}</p>
      </div>
      <div className="detail-section">
        <span>Source</span>
        <div className="token-row"><code>{item.key}</code></div>
      </div>
    </DetailRail>
  )
}

function MilestonesView({ model, selectedMilestoneKey, onSelectMilestone }: {
  model: DisplayModel
  selectedMilestoneKey: string | null
  onSelectMilestone: (key: string) => void
}) {
  const items = model.milestoneFeed.length ? model.milestoneFeed.map(item => ({
    key: `${item.source}-${item.created_at}`,
    title: item.title,
    type: item.kind,
    summary: item.summary,
    created_at: item.created_at,
  })) : model.outcomes.map(item => ({
    key: item.key,
    title: sentenceCase(item.type),
    type: item.type,
    summary: item.summary,
    created_at: item.created_at,
  }))
  const selectedMilestone = selectedMilestoneKey ? items.find(item => item.key === selectedMilestoneKey) || null : null

  return (
    <ViewFrame>
      <div className="milestones-view">
        <div className="exhibit-main-column">
          <header className="floor-header">
            <span className="view-kicker">Milestones</span>
            <h1>What the Studio build has already proven.</h1>
            <p>Milestones condense approved architecture, fork setup, workstream launches, and implementation checkpoints into a public narrative trail.</p>
          </header>
          <div className="milestone-timeline">
            {items.slice(0, 12).map(item => (
              <button
                type="button"
                className={`milestone-item ${selectedMilestoneKey === item.key ? 'selected' : ''}`}
                aria-pressed={selectedMilestoneKey === item.key}
                key={item.key}
                onClick={() => onSelectMilestone(item.key)}
              >
                <span>{item.type}</span>
                <h3 title={item.title || sentenceCase(item.type)}>{item.title || sentenceCase(item.type)}</h3>
                <p title={item.summary}>{truncate(item.summary, 180)}</p>
                <small>{relTime(item.created_at)}</small>
              </button>
            ))}
          </div>
        </div>
        <MilestoneDetail item={selectedMilestone} />
      </div>
    </ViewFrame>
  )
}

interface OperationMetricItem {
  key: string
  label: string
  value: string | number
  caption: string
  detail: string
}

function OperationDetail({ item, model }: { item: OperationMetricItem | null; model: DisplayModel }) {
  if (!item) {
    return (
      <EmptyDetail
        kicker="Studio repair telemetry"
        title="Choose a repair signal"
        body="Open an internal metric to see how verifier breadth, repair groups, path sanitization, and preflight checks are shaping the current Studio checkpoint."
      />
    )
  }
  return (
    <DetailRail
      kicker="Studio repair telemetry"
      title={item.label}
      body={item.detail}
    >
      <div className="detail-section">
        <span>Metric</span>
        <Metric label={item.label} value={item.value} caption={item.caption} />
      </div>
      <div className="detail-section">
        <span>Latest blocker</span>
        <p>{model.repair?.current_cycle?.final_blocker_summary || 'No current blocker reported by repair metrics.'}</p>
      </div>
    </DetailRail>
  )
}

function OperationsView({ model, selectedOperationKey, onSelectOperation }: {
  model: DisplayModel
  selectedOperationKey: string | null
  onSelectOperation: (key: string) => void
}) {
  const summary = model.repair?.summary
  const metrics: OperationMetricItem[] = [
    {
      key: 'expanded-verify',
      label: 'Expanded verify',
      value: summary?.expanded_verify_runs || 0,
      caption: 'runs observed',
      detail: 'Read-only verifier lenses run after a normal verifier failure to discover more issues before RCA and repair.',
    },
    {
      key: 'fix-groups',
      label: 'Fix groups',
      value: summary?.fix_groups_scheduled || 0,
      caption: 'scheduled',
      detail: 'Repair groups scheduled from RCA output. Non-overlapping file scopes can run in parallel.',
    },
    {
      key: 'applied-fixes',
      label: 'Applied fixes',
      value: summary?.fix_groups_applied || 0,
      caption: 'returned',
      detail: 'Repair groups that returned implementation results for focused or aggregate verification.',
    },
    {
      key: 'sanitized-paths',
      label: 'Sanitized paths',
      value: summary?.sanitizer_ignored_paths || 0,
      caption: 'ignored context paths',
      detail: 'Workflow/context paths stripped out of implementation result metadata before strict DAG preflight.',
    },
    {
      key: 'rewrites',
      label: 'Rewrites',
      value: summary?.sanitizer_rewritten_paths || 0,
      caption: 'canonicalized paths',
      detail: 'Known stale product paths rewritten to canonical repository paths before validation.',
    },
    {
      key: 'invalid-paths',
      label: 'Invalid paths',
      value: summary?.sanitizer_invalid_paths || 0,
      caption: 'still blocking',
      detail: 'Product-looking paths that still could not be resolved. These remain hard preflight blockers.',
    },
  ]
  const selectedMetric = selectedOperationKey ? metrics.find(metric => metric.key === selectedOperationKey) || null : null
  return (
    <ViewFrame>
      <div className="operations-view">
        <div className="exhibit-main-column">
          <header className="floor-header">
            <span className="view-kicker">Operations</span>
            <h1>Internal signals behind checkpoint health.</h1>
            <p>Expanded verifier runs, fix groups, sanitizer counts, and path rewrites stay here so public tabs can stay focused on the Studio story.</p>
          </header>
          <div className="ops-grid">
            {metrics.map(metric => (
              <button
                type="button"
                className={`xp-metric ${selectedOperationKey === metric.key ? 'selected' : ''}`}
                aria-pressed={selectedOperationKey === metric.key}
                key={metric.key}
                onClick={() => onSelectOperation(metric.key)}
              >
                <span>{metric.label}</span>
                <strong>{metric.value}</strong>
                <small>{metric.caption}</small>
              </button>
            ))}
          </div>
          <div className="ops-blocker">
            <span>Latest blocker</span>
            <p>{model.repair?.current_cycle?.final_blocker_summary || 'No current blocker reported by repair metrics.'}</p>
          </div>
        </div>
        <OperationDetail item={selectedMetric} model={model} />
      </div>
    </ViewFrame>
  )
}

function exhibitLoadingState({
  data,
  featureId,
  loading,
  failed,
  allowDemo,
}: FeatureExhibitDashboardProps): 'demo' | 'loading' | 'failed' | 'ready' {
  if (data) return 'ready'
  if (failed) return 'failed'
  if (loading || featureId) return 'loading'
  if (allowDemo) return 'demo'
  return 'loading'
}

function FeatureExhibitLoading({ featureId, failed }: { featureId: string; failed: boolean }) {
  return (
    <ViewFrame>
      <div className="overview-loading-state">
        <StatusPill tone={failed ? 'blocked' : 'running'}>{failed ? 'Connection issue' : 'Loading live feature'}</StatusPill>
        <h1>{failed ? 'Live exhibit unavailable' : 'Preparing the live exhibit'}</h1>
        <p>
          {failed
            ? `Could not load feature ${featureId}. The dashboard is holding the public surface instead of showing demo data as if it were live.`
            : `Fetching canonical workflow state for ${featureId}.`}
        </p>
      </div>
    </ViewFrame>
  )
}

export function FeatureExhibitDashboard({
  data,
  featureId = null,
  loading = false,
  failed = false,
  allowDemo = false,
  onHome,
}: FeatureExhibitDashboardProps) {
  const [activeTab, setActiveTab] = useState<ExhibitTab>('overview')
  const loadingState = exhibitLoadingState({ data, featureId, loading, failed, allowDemo })
  const model = useMemo(() => buildModel(data), [data])
  const [selectedRouteTaskKey, setSelectedRouteTaskKey] = useState<string | null>(null)
  const [selectedAgentName, setSelectedAgentName] = useState<string | null>(null)
  const [selectedArtifactKey, setSelectedArtifactKey] = useState<string | null>(null)
  const [selectedWorkstreamId, setSelectedWorkstreamId] = useState<string | null>(null)
  const [selectedMilestoneKey, setSelectedMilestoneKey] = useState<string | null>(null)
  const [selectedOperationKey, setSelectedOperationKey] = useState<string | null>(null)
  const routeSelectableTasks = useMemo(() => [...model.tasks, ...buildFollowupTasks(model)], [model])
  const selectedRouteTask = selectedRouteTaskKey ? routeSelectableTasks.find(task => taskSelectionKey(task) === selectedRouteTaskKey) || null : null

  const activeTabMeta = tabs.find(tab => tab.id === activeTab) || tabs[0]

  return (
    <div className="xp-exhibit-shell">
      <header className="xp-topbar">
        <div
          className={`xp-brand${onHome ? ' interactive' : ''}`}
          onClick={onHome}
          role={onHome ? 'button' : undefined}
          tabIndex={onHome ? 0 : undefined}
          onKeyDown={(event) => {
            if (!onHome) return
            if (event.key === 'Enter' || event.key === ' ') {
              event.preventDefault()
              onHome()
            }
          }}
        >
          <span className="xp-brand-mark">ir</span>
          <div>
            <strong>Iriai Studio build exhibit</strong>
            <small>checkpointed multi-agent delivery</small>
          </div>
        </div>
        <nav className="xp-tabs" aria-label="Dashboard mockup sections">
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={activeTab === tab.id ? 'active' : ''}
              onClick={() => setActiveTab(tab.id)}
              title={tab.hint}
            >
              {tab.label}
            </button>
          ))}
        </nav>
        <div className="xp-live-badge">
          <span />
          {loadingState === 'demo' ? 'Demo' : 'Live'}
        </div>
      </header>

      <main className="xp-main">
        <div className="tab-context">
          <span>{activeTabMeta.label}</span>
          <p>{activeTabMeta.hint}</p>
        </div>

        {(loadingState === 'loading' || loadingState === 'failed') && featureId ? (
          <FeatureExhibitLoading featureId={featureId} failed={loadingState === 'failed'} />
        ) : (
          <>
            {activeTab === 'overview' && <OverviewView model={model} />}
            {activeTab === 'dag' && <DagMapView model={model} selectedTask={selectedRouteTask} selectedTaskKey={selectedRouteTaskKey} onSelectTask={setSelectedRouteTaskKey} />}
            {activeTab === 'agents' && <AgentFloorView model={model} selectedAgentName={selectedAgentName} onSelectAgent={setSelectedAgentName} />}
            {activeTab === 'artifacts' && <ArtifactTrailView model={model} selectedArtifactKey={selectedArtifactKey} onSelectArtifact={setSelectedArtifactKey} />}
            {activeTab === 'workstreams' && <WorkstreamsView model={model} selectedWorkstreamId={selectedWorkstreamId} onSelectWorkstream={setSelectedWorkstreamId} />}
            {activeTab === 'milestones' && <MilestonesView model={model} selectedMilestoneKey={selectedMilestoneKey} onSelectMilestone={setSelectedMilestoneKey} />}
            {activeTab === 'operations' && <OperationsView model={model} selectedOperationKey={selectedOperationKey} onSelectOperation={setSelectedOperationKey} />}
          </>
        )}
      </main>
    </div>
  )
}

export function XPCommandCenterMockup() {
  const featureState = useFeatureForMockup()

  return (
    <FeatureExhibitDashboard
      data={featureState.data}
      featureId={featureState.selectedFeatureId}
      loading={featureState.loading}
      failed={featureState.failed}
      allowDemo
    />
  )
}
