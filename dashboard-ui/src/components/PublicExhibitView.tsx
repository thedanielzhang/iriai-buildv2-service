import { useMemo, useState } from 'react'
import type { AgentExhibit, ArtifactCard, FeatureData, Group, PhaseMode, PublicMilestone, Task } from '../types'
import { humanPhase, relTime } from '../utils'
import { CollapsibleSection } from './CollapsibleSection'
import { CurrentStatus } from './CurrentStatus'
import { DagFlow } from './DagFlow'
import { DagRepairMetricsPanel } from './DagRepairMetricsPanel'
import { EventLog } from './EventLog'
import { Gates } from './Gates'
import { TaskList } from './TaskList'
import { Timeline } from './Timeline'

type ExhibitTab = 'overview' | 'dag' | 'agents' | 'artifacts' | 'workstreams' | 'timeline' | 'operations'

interface PublicExhibitViewProps {
  data: FeatureData
  phaseMode: PhaseMode
  activeGroup?: Group
  completedGroups: number
  totalGroups: number
  completedTasks: number
  totalTasks: number
  passedGates: number
  totalGates: number
}

const tabs: Array<{ id: ExhibitTab; label: string }> = [
  { id: 'overview', label: 'Overview' },
  { id: 'dag', label: 'DAG' },
  { id: 'agents', label: 'Agents' },
  { id: 'artifacts', label: 'Artifacts' },
  { id: 'workstreams', label: 'Workstreams' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'operations', label: 'Operations' },
]

function pct(done: number, total: number): number {
  return total > 0 ? Math.round((done / total) * 100) : 0
}

function statusClass(value: string | null | undefined): string {
  const normalized = (value || '').toLowerCase().replace(/_/g, '-')
  if (['complete', 'completed', 'passed', 'approved', 'resolved', 'promoted', 'pushed', 'verified'].includes(normalized)) return 'pass'
  if (['failed', 'blocked', 'stuck', 'stalled', 'error', 'rejected', 'cancelled'].includes(normalized)) return 'fail'
  if ([
    'running',
    'active',
    'in-progress',
    'fixing',
    'fix-loop',
    'reverify',
    'recovering',
    'quality-gates',
    'promoting',
    'promotion-pending',
    'active-fix',
    'active-verify',
    'awaiting-user',
    'planning',
    'implementation',
  ].includes(normalized)) return 'running'
  return 'neutral'
}

const standardWorkflowPhases = [
  'pm',
  'scoping',
  'broad',
  'design',
  'architecture',
  'subfeature',
  'plan-review',
  'task-planning',
  'implementation',
  'post-test-observation',
  'complete',
]

const bugfixWorkflowPhases = [
  'bug-intake',
  'env-setup',
  'bug-reproduction',
  'baseline',
  'diagnosis-fix',
  'regression',
  'approval',
  'cleanup',
  'complete',
]

const bugflowWorkflowPhases = ['bugflow-setup', 'bugflow-queue', 'complete']

function phaseOrderFor(data: FeatureData): string[] {
  if (data.workflow_name === 'bugfix-v2') return bugflowWorkflowPhases
  if (data.workflow_name === 'bugfix') return bugfixWorkflowPhases
  if (data.workflow_name === 'planning') return [...standardWorkflowPhases.slice(0, 8), 'complete']
  return standardWorkflowPhases
}

function normalizeState(value: string): string {
  return value.toLowerCase().replace(/_/g, '-')
}

function countBy<T>(items: T[], keyFn: (item: T) => string | null | undefined): Array<[string, number]> {
  const counts = new Map<string, number>()
  for (const item of items) {
    const key = keyFn(item)?.trim() || 'unspecified'
    counts.set(key, (counts.get(key) || 0) + 1)
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
}

function allTasks(data: FeatureData): Task[] {
  return data.groups.flatMap(group => group.tasks || [])
}

function WorkflowPhaseLadder({ data }: { data: FeatureData }) {
  const rawCurrent = normalizeState(data.phase || '')
  const phases = [...phaseOrderFor(data)]
  if (rawCurrent && !phases.includes(rawCurrent)) phases.splice(Math.max(phases.length - 1, 0), 0, rawCurrent)
  const currentIndex = phases.indexOf(rawCurrent)

  return (
    <div className="phase-ladder" aria-label="Workflow phase map">
      {phases.map((phase, index) => {
        let state: 'complete' | 'active' | 'pending' = 'pending'
        if (data.phase === 'complete' || (currentIndex >= 0 && index < currentIndex)) state = 'complete'
        if (index === currentIndex && data.phase !== 'complete') state = 'active'
        return (
          <div key={`${phase}-${index}`} className={`phase-step ${state}`}>
            <div className="phase-step-dot" />
            <div className="phase-step-label">{humanPhase(phase)}</div>
          </div>
        )
      })}
    </div>
  )
}

function MetricChipList({ entries, limit = 8 }: { entries: Array<[string, number]>; limit?: number }) {
  if (!entries.length) return <div className="exhibit-empty compact">No entries yet.</div>
  return (
    <div className="metric-chip-list">
      {entries.slice(0, limit).map(([key, value]) => (
        <span key={key} className={`metric-chip ${statusClass(key)}`}>
          {key.replace(/_/g, ' ')} <strong>{value}</strong>
        </span>
      ))}
    </div>
  )
}

function TaskSurfacePanel({ data, agents }: { data: FeatureData; agents?: AgentExhibit }) {
  const tasks = allTasks(data)
  const taskStatusCounts = countBy(tasks, task => task.status)
  const fileActionCounts = countBy(tasks.flatMap(task => task.file_scope || []), file => file.action)
  const timelineTypeCounts = countBy(data.timeline, entry => entry.type)
  const agentRoleCounts = countBy([...(agents?.active_agents || []), ...(agents?.recent_agents || [])], agent => agent.role)
  const repoCounts = countBy(tasks, task => task.repo_path)
  const subfeatureCounts = countBy(tasks, task => task.subfeature_id)

  return (
    <section className="exhibit-panel exhibit-panel-wide">
      <div className="section-title">Workflow And Task Surface</div>
      <div className="exhibit-surface-grid">
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Workflow phases</div>
          <WorkflowPhaseLadder data={data} />
        </div>
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Implementation task states</div>
          <MetricChipList entries={taskStatusCounts} />
        </div>
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Task file actions</div>
          <MetricChipList entries={fileActionCounts} />
        </div>
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Verifier and repair events</div>
          <MetricChipList entries={timelineTypeCounts} />
        </div>
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Agent roles represented</div>
          <MetricChipList entries={agentRoleCounts} />
        </div>
        <div className="exhibit-surface-card">
          <div className="exhibit-mini-label">Repos and work areas</div>
          <MetricChipList entries={repoCounts.length ? repoCounts : subfeatureCounts} />
        </div>
      </div>
    </section>
  )
}

function MilestoneList({ milestones, limit = 6 }: { milestones: PublicMilestone[]; limit?: number }) {
  if (!milestones.length) {
    return <div className="exhibit-empty">No public milestones have been published yet.</div>
  }
  return (
    <div className="exhibit-milestone-list">
      {milestones.slice(0, limit).map((m, i) => (
        <div key={`${m.source}-${i}`} className="exhibit-milestone">
          <div className="exhibit-milestone-top">
            <span className="exhibit-kind">{m.kind}</span>
            <span className="exhibit-muted">{relTime(m.created_at)}</span>
          </div>
          <div className="exhibit-milestone-title">{m.title}</div>
          <div className="exhibit-copy">{m.summary}</div>
        </div>
      ))}
    </div>
  )
}

export function PublicExhibitView({
  data,
  phaseMode,
  activeGroup,
  completedGroups,
  totalGroups,
  completedTasks,
  totalTasks,
  passedGates,
  totalGates,
}: PublicExhibitViewProps) {
  const [activeTab, setActiveTab] = useState<ExhibitTab>('overview')
  const exhibit = data.public_exhibit
  const summary = exhibit?.public_summary
  const dag = exhibit?.dag_exhibit
  const agents = exhibit?.agent_exhibit
  const artifacts = exhibit?.artifact_exhibit
  const workstreams = exhibit?.workstream_exhibit
  const milestones = exhibit?.milestone_feed ?? []

  const artifactFamilies = useMemo(() => {
    const grouped: Record<string, ArtifactCard[]> = {}
    for (const card of artifacts?.cards ?? []) {
      grouped[card.family] = grouped[card.family] || []
      grouped[card.family].push(card)
    }
    return grouped
  }, [artifacts])

  return (
    <div className="exhibit-shell">
      <section className="exhibit-hero">
        <div className="exhibit-hero-copy">
          <div className="exhibit-kicker">Public Workflow Exhibit</div>
          <h1>{summary?.title || data.name}</h1>
          <p className="exhibit-tagline">{summary?.tagline || 'A live multi-agent feature delivery run.'}</p>
          <p className="exhibit-description">{summary?.description || 'The bridge is preparing public narrative artifacts for this feature.'}</p>
          <div className="exhibit-hero-pills">
            <span className={`exhibit-status ${statusClass(summary?.health || phaseMode)}`}>{summary?.health || phaseMode}</span>
            <span>{summary?.phase_label || data.phase}</span>
            <span>{summary?.source || 'deterministic-fallback'}</span>
            <span>{relTime(summary?.updated_at || data.last_activity_at || data.updated_at)}</span>
          </div>
        </div>
        <div className="exhibit-progress-card">
          <div className="exhibit-progress-number">{summary?.percent_complete ?? pct(completedGroups, totalGroups)}%</div>
          <div className="exhibit-progress-label">DAG checkpoint progress</div>
          <div className="fc-bar"><div className="fc-fill" style={{ width: `${summary?.percent_complete ?? pct(completedGroups, totalGroups)}%` }} /></div>
          <div className="exhibit-stat-grid">
            <div><strong>{completedGroups}</strong><span>groups done</span></div>
            <div><strong>{totalGroups}</strong><span>groups total</span></div>
            <div><strong>{completedTasks}</strong><span>tasks done</span></div>
            <div><strong>{totalTasks}</strong><span>tasks total</span></div>
          </div>
        </div>
      </section>

      <nav className="exhibit-tabs">
        {tabs.map(tab => (
          <button
            key={tab.id}
            type="button"
            className={`exhibit-tab ${activeTab === tab.id ? 'active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {activeTab === 'overview' && (
        <div className="exhibit-grid">
          <section className="exhibit-panel exhibit-panel-wide">
            <div className="section-title">Current Focus</div>
            <h2>{summary?.status_label || 'Workflow in progress'}</h2>
            <p className="exhibit-copy">{summary?.progress_narrative}</p>
            <div className="exhibit-focus-row">
              <div>
                <div className="exhibit-mini-label">Now</div>
                <div>{summary?.current_focus}</div>
              </div>
              <div>
                <div className="exhibit-mini-label">Next checkpoint</div>
                <div>{summary?.next_checkpoint}</div>
              </div>
            </div>
          </section>
          <TaskSurfacePanel data={data} agents={agents || undefined} />
          <section className="exhibit-panel">
            <div className="section-title">Agents At Work</div>
            <div className="exhibit-big-number">{agents?.active_agents.length ?? 0}</div>
            <div className="exhibit-copy">{agents?.headline || 'No active agents at this instant.'}</div>
            {agents?.current_repair_cycle && (
              <div className="exhibit-mini-note">
                Group {agents.current_repair_cycle.group_idx}, retry {agents.current_repair_cycle.retry}
              </div>
            )}
          </section>
          <section className="exhibit-panel">
            <div className="section-title">Artifact Trail</div>
            <div className="exhibit-big-number">{artifacts?.total_count ?? 0}</div>
            <div className="exhibit-copy">
              {artifacts?.generated ? 'Curated gallery generated by the bridge.' : 'Deterministic artifact inventory is available.'}
            </div>
          </section>
          <section className="exhibit-panel exhibit-panel-wide">
            <div className="section-title">Recent Milestones</div>
            <MilestoneList milestones={milestones} />
          </section>
        </div>
      )}

      {activeTab === 'dag' && (
        <div className="exhibit-stack">
          <section className="exhibit-panel">
            <div className="section-title">DAG Narrative</div>
            <p className="exhibit-copy">{dag?.narrative || 'No DAG narrative has been published yet.'}</p>
            <div className="exhibit-stat-grid compact">
              <div><strong>{dag?.completed_groups ?? completedGroups}</strong><span>complete</span></div>
              <div><strong>{dag?.total_groups ?? totalGroups}</strong><span>groups</span></div>
              <div><strong>{dag?.total_tasks ?? totalTasks}</strong><span>tasks</span></div>
              <div><strong>{activeGroup ? `G${activeGroup.index}` : '-'}</strong><span>active</span></div>
            </div>
          </section>
          {data.dag && data.groups.length > 0 && (
            <DagFlow groups={data.groups} totalTasks={data.dag.total_tasks} totalGroups={data.dag.total_groups} />
          )}
          {data.dag_repair && <DagRepairMetricsPanel metrics={data.dag_repair} />}
        </div>
      )}

      {activeTab === 'agents' && (
        <div className="exhibit-stack">
          <section className="exhibit-panel">
            <div className="section-title">Live Agent Floor</div>
            <div className="agent-grid">
              {(agents?.active_agents ?? []).map(agent => (
                <div key={agent.name} className="agent-card active">
                  <div className="agent-card-top">
                    <span className="status-strip-task-dot" />
                    <strong>{agent.name}</strong>
                  </div>
                  <div className="exhibit-copy">{agent.role} · {agent.runtime}</div>
                  <div className="exhibit-muted">Started {relTime(agent.started_at)}</div>
                </div>
              ))}
              {(!agents || agents.active_agents.length === 0) && <div className="exhibit-empty">No agent is currently running.</div>}
            </div>
          </section>
          {agents?.round_summaries?.length ? (
            <section className="exhibit-panel">
              <div className="section-title">Bridge-Written Round Summaries</div>
              <div className="exhibit-card-grid">
                {agents.round_summaries.map(round => (
                  <div key={round.key} className="artifact-card">
                    <div className="artifact-card-top"><span>{round.key}</span><span>{relTime(round.created_at)}</span></div>
                    <div className="exhibit-copy">{round.summary || 'Round summary is available.'}</div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          <section className="exhibit-panel">
            <div className="section-title">Recent Agent Handoffs</div>
            <div className="agent-grid">
              {(agents?.recent_agents ?? []).slice(0, 12).map((agent, i) => (
                <div key={`${agent.name}-${agent.ended_at}-${i}`} className="agent-card">
                  <strong>{agent.name}</strong>
                  <div className="exhibit-copy">{agent.role} · {agent.runtime}</div>
                  <div className="exhibit-muted">{agent.status} {relTime(agent.ended_at)}</div>
                </div>
              ))}
            </div>
          </section>
        </div>
      )}

      {activeTab === 'artifacts' && (
        <div className="exhibit-stack">
          {Object.keys(artifactFamilies).length === 0 && (
            <section className="exhibit-panel">
              <div className="section-title">Artifact Gallery</div>
              <div className="exhibit-empty">No public-safe artifact cards have been assembled yet.</div>
            </section>
          )}
          {Object.entries(artifactFamilies).map(([family, cards]) => (
            <section key={family} className="exhibit-panel">
              <div className="section-title">{family} Artifacts</div>
              <div className="exhibit-card-grid">
                {cards.map(card => (
                  <article key={card.key} className="artifact-card">
                    <div className="artifact-card-top">
                      <span>{card.key}</span>
                      <span>{relTime(card.created_at)}</span>
                    </div>
                    <h3>{card.title}</h3>
                    <p className="exhibit-copy">{card.summary}</p>
                    <div className="exhibit-hero-pills">
                      <span>{card.status}</span>
                      <span>{card.source}</span>
                    </div>
                  </article>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {activeTab === 'workstreams' && (
        <div className="exhibit-stack">
          <section className="exhibit-panel">
            <div className="section-title">Workstream Narrative</div>
            <p className="exhibit-copy">{workstreams?.summary || 'Workstreams organize the DAG into delivery lanes.'}</p>
          </section>
          <div className="ws-grid">
            {(workstreams?.workstreams ?? []).map(ws => {
              const progress = pct(ws.completed_tasks, ws.total_tasks)
              return (
                <div key={ws.id} className="ws-card exhibit-workstream-card">
                  <div className="ws-card-name">{ws.name}</div>
                  <div className={`exhibit-status ${statusClass(ws.status)}`}>{ws.status}</div>
                  <p className="exhibit-copy">{ws.summary}</p>
                  <div className="ws-card-bar"><div className="ws-card-fill" style={{ width: `${progress}%` }} /></div>
                  <div className="ws-card-stat">{ws.completed_tasks}/{ws.total_tasks} tasks · {ws.subfeature_slugs.length} subfeatures</div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {activeTab === 'timeline' && (
        <div className="exhibit-stack">
          <section className="exhibit-panel">
            <div className="section-title">Public Milestone Feed</div>
            <MilestoneList milestones={milestones} limit={20} />
          </section>
          <CollapsibleSection title={`Technical Timeline — ${data.timeline.length} entries`} defaultOpen={false}>
            <Timeline entries={data.timeline} />
          </CollapsibleSection>
        </div>
      )}

      {activeTab === 'operations' && (
        <div className="exhibit-stack">
          <section className="exhibit-panel">
            <div className="section-title">Raw Workflow State</div>
            <div className="exhibit-hero-pills">
              <span>phase: {data.phase}</span>
              <span>workflow: {data.workflow_name}</span>
              <span>mode: {phaseMode}</span>
              <span>updated: {relTime(data.updated_at)}</span>
            </div>
          </section>
          <CurrentStatus />
          <TaskSurfacePanel data={data} agents={agents || undefined} />
          {data.dag_repair && <DagRepairMetricsPanel metrics={data.dag_repair} />}
          {activeGroup && activeGroup.tasks.length > 0 && (
            <CollapsibleSection
              title={`Group ${activeGroup.index} Tasks — ${activeGroup.completed_count}/${activeGroup.task_count}`}
              defaultOpen={phaseMode === 'implementing'}
            >
              <TaskList tasks={activeGroup.tasks} groupIndex={activeGroup.index} />
            </CollapsibleSection>
          )}
          <CollapsibleSection title={`Post-DAG Gates — ${passedGates}/${totalGates}`} defaultOpen={phaseMode === 'gates'}>
            <Gates gates={data.gates} />
          </CollapsibleSection>
          <CollapsibleSection title={`Event Log — ${data.events.length} events`} defaultOpen={false}>
            <EventLog events={data.events} />
          </CollapsibleSection>
        </div>
      )}
    </div>
  )
}
