import { useStore } from '../store/useStore'
import { phaseCls, relTime } from '../utils'
import { CurrentStatus } from './CurrentStatus'
import { Workstreams } from './Workstreams'
import { DagFlow } from './DagFlow'
import { TaskList } from './TaskList'
import { Gates } from './Gates'
import { Timeline } from './Timeline'
import { EventLog } from './EventLog'

export function WorkstreamView() {
  const { view, data, setView } = useStore()
  const d = data[view]

  if (!d) {
    return <div className="loading"><div className="spinner" />Loading...</div>
  }

  const activeGroup = d.groups?.find(g => g.status === 'active')

  return (
    <>
      <CurrentStatus />

      <div className="ws-header">
        <div className="ws-back" onClick={() => setView('overview')}>←</div>
        <div className="ws-title">{d.name}</div>
        <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
        <div className="ws-meta">{d.id} · {d.workflow_name} · {relTime(d.updated_at)}</div>
      </div>

      {d.workstreams?.length > 0 && (
        <Workstreams workstreams={d.workstreams} />
      )}

      {d.dag && d.groups.length > 0 ? (
        <DagFlow groups={d.groups} totalTasks={d.dag.total_tasks} totalGroups={d.dag.total_groups} />
      ) : (
        <div className="section">
          <div className="section-title">Implementation</div>
          <div className="dag-flow" style={{ textAlign: 'center', color: 'var(--text-2)', padding: 40 }}>
            No DAG yet — feature is in <strong>{d.phase}</strong> phase
          </div>
        </div>
      )}

      {activeGroup && <TaskList tasks={activeGroup.tasks} groupIndex={activeGroup.index} />}

      <Gates gates={d.gates} />

      <Timeline entries={d.timeline} />

      <EventLog events={d.events} />
    </>
  )
}
