import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import { relTime, phaseCls, getActiveStatus, getHealthState, getPhaseMode, healthCls } from '../utils'
import { CurrentStatus } from './CurrentStatus'
import { Workstreams } from './Workstreams'
import { DagFlow } from './DagFlow'
import { TaskList } from './TaskList'
import { Gates } from './Gates'
import { Timeline } from './Timeline'
import { EventLog } from './EventLog'
import { CollapsibleSection } from './CollapsibleSection'
import { BugflowView } from './BugflowView'

export function WorkstreamView() {
  const d = useStore(s => s.data[s.view])
  const setView = useStore(s => s.setView)

  const derived = useMemo(() => {
    if (!d) return null
    const activeGroup = d.groups?.find(g => g.status === 'active')
    return {
      health: getHealthState(d),
      phaseMode: getPhaseMode(d),
      status: getActiveStatus(d),
      activeGroup,
      completedGroups: d.groups?.filter(g => g.status === 'complete').length ?? 0,
      totalGroups: d.dag?.total_groups ?? 0,
      completedTasks: d.groups?.reduce((sum, g) => sum + g.completed_count, 0) ?? 0,
      totalTasks: d.dag?.total_tasks ?? 0,
      passedGates: Object.values(d.gates).filter(Boolean).length,
      totalGates: Object.keys(d.gates).length,
      activeTasks: activeGroup ? activeGroup.tasks.filter(t => t.status === 'in_progress') : [],
    }
  }, [d])

  if (!d || !derived) {
    return <div className="loading"><div className="spinner" />Loading...</div>
  }

  if (d.workflow_name === 'bugfix-v2' && d.bugflow) {
    return <BugflowView />
  }

  const { health, phaseMode, status, activeGroup, completedGroups, totalGroups, completedTasks, totalTasks, passedGates, totalGates, activeTasks } = derived

  // Phase-aware section visibility
  const showDag = phaseMode !== 'planning'
  const showTaskList = phaseMode === 'implementing' || phaseMode === 'fix-loop'
  const showGates = phaseMode === 'gates' || phaseMode === 'complete'
  const showTimeline = phaseMode !== 'planning'
  const showEventLog = true // always visible

  // Phase-aware defaultOpen
  const dagDefaultOpen = phaseMode === 'implementing' || phaseMode === 'fix-loop'
  const taskListDefaultOpen = phaseMode === 'implementing' && !!activeGroup && activeGroup.tasks.length > 0
  const gatesDefaultOpen = phaseMode === 'gates'
  const timelineDefaultOpen = phaseMode === 'fix-loop'
  const eventLogDefaultOpen = phaseMode === 'planning'

  return (
    <>
      {/* Sticky status strip */}
      <div className="status-strip">
        <div className="status-strip-main">
          <div className="ws-back" onClick={() => setView('overview')}>
            &larr;
          </div>
          <div className="status-strip-name">{d.name}</div>
          <span className={`health-dot ${healthCls(health)}`} />
          <div className="status-strip-text">{status}</div>
          <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
          {d.active_agent && (
            <span className="cs-agent-badge">{d.active_agent}</span>
          )}
          <div className="status-strip-stats">
            <span className="cs-inline-stat">{completedGroups}/{totalGroups} groups</span>
            <span className="cs-inline-stat">{completedTasks}/{totalTasks} tasks</span>
            <span className="cs-inline-stat">{passedGates}/{totalGates} gates</span>
            <span className="cs-inline-stat">{relTime(d.last_activity_at || d.updated_at)}</span>
          </div>
        </div>
        {activeTasks.length > 0 && (
          <div className="status-strip-tasks">
            {activeTasks.map(t => (
              <span key={t.id} className="status-strip-task-pill">
                <span className="status-strip-task-dot" />
                {t.name}
              </span>
            ))}
          </div>
        )}
      </div>

      {/* Detail sections from CurrentStatus (no summary row) */}
      <CurrentStatus />

      {d.workstreams?.length > 0 && (
        <Workstreams workstreams={d.workstreams} />
      )}

      {showDag && (
        d.dag && d.groups.length > 0 ? (
          <CollapsibleSection
            title={`DAG — ${completedGroups}/${totalGroups} groups`}
            defaultOpen={dagDefaultOpen}
          >
            <DagFlow groups={d.groups} totalTasks={d.dag.total_tasks} totalGroups={d.dag.total_groups} />
          </CollapsibleSection>
        ) : (
          <div className="section">
            <div className="section-title">Implementation</div>
            <div className="dag-flow" style={{ textAlign: 'center', color: 'var(--text-2)', padding: 40 }}>
              No DAG yet — feature is in <strong>{d.phase}</strong> phase
            </div>
          </div>
        )
      )}

      {showTaskList && activeGroup && activeGroup.tasks.length > 0 && (
        <CollapsibleSection
          title={`Group ${activeGroup.index} Tasks — ${activeGroup.completed_count}/${activeGroup.task_count}`}
          defaultOpen={taskListDefaultOpen}
        >
          <TaskList tasks={activeGroup.tasks} groupIndex={activeGroup.index} />
        </CollapsibleSection>
      )}

      {showGates && (
        <CollapsibleSection
          title={`Post-DAG Gates — ${passedGates}/${totalGates}`}
          defaultOpen={gatesDefaultOpen}
        >
          <Gates gates={d.gates} />
        </CollapsibleSection>
      )}

      {showTimeline && (
        <CollapsibleSection
          title={`Verify / Fix Timeline — ${d.timeline.length} entries`}
          defaultOpen={timelineDefaultOpen}
        >
          <Timeline entries={d.timeline} />
        </CollapsibleSection>
      )}

      {showEventLog && (
        <CollapsibleSection
          title={`Event Log — ${d.events.length} events`}
          defaultOpen={eventLogDefaultOpen}
        >
          <EventLog events={d.events} />
        </CollapsibleSection>
      )}
    </>
  )
}
