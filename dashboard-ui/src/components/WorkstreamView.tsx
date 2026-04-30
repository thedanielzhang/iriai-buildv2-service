import { useMemo } from 'react'
import { useStore } from '../store/useStore'
import { relTime, phaseCls, getActiveStatus, getHealthState, getPhaseMode, healthCls } from '../utils'
import { BugflowView } from './BugflowView'
import { FeatureExhibitDashboard } from './XPCommandCenterMockup'
import { PublicExhibitView } from './PublicExhibitView'

export function WorkstreamView() {
  const view = useStore(s => s.view)
  const d = useStore(s => s.data[s.view])
  const setView = useStore(s => s.setView)
  const searchParams = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : new URLSearchParams()
  const legacyView = searchParams.get('legacy') === '1' || searchParams.get('view') === 'legacy'

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
    if (!legacyView) {
      return <FeatureExhibitDashboard data={null} featureId={view} loading onHome={() => setView('overview')} />
    }
    return <div className="loading"><div className="spinner" />Loading...</div>
  }

  if (d.workflow_name === 'bugfix-v2' && d.bugflow) {
    return <BugflowView />
  }

  const { health, phaseMode, status, activeGroup, completedGroups, totalGroups, completedTasks, totalTasks, passedGates, totalGates, activeTasks } = derived

  if (!legacyView) {
    return <FeatureExhibitDashboard data={d} featureId={d.id} onHome={() => setView('overview')} />
  }

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

      <PublicExhibitView
        data={d}
        phaseMode={phaseMode}
        activeGroup={activeGroup}
        completedGroups={completedGroups}
        totalGroups={totalGroups}
        completedTasks={completedTasks}
        totalTasks={totalTasks}
        passedGates={passedGates}
        totalGates={totalGates}
      />
    </>
  )
}
