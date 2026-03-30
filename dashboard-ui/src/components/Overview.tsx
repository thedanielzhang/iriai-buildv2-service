import { useStore } from '../store/useStore'
import { phaseCls, phaseColor, relTime, getActiveStatus } from '../utils'

export function Overview() {
  const { tracked, data, setView } = useStore()

  if (!tracked.length) {
    return (
      <div className="empty-state">
        <div className="es-title">No features tracked</div>
        <div className="es-sub">Add a feature by its ID to start monitoring.</div>
      </div>
    )
  }

  return (
    <div className="overview-grid">
      {tracked.map(id => {
        const d = data[id]
        if (!d) {
          return (
            <div key={id} className="feature-card" onClick={() => setView(id)}>
              <div className="loading"><div className="spinner" />Loading {id}...</div>
            </div>
          )
        }
        const dag = d.dag
        let pct = 0
        if (dag && d.groups) {
          const done = d.groups.filter(g => g.status === 'complete').length
          pct = Math.round(done / dag.total_groups * 100)
        }
        const status = getActiveStatus(d)

        return (
          <div key={id} className="feature-card" onClick={() => setView(id)}>
            <div className="card-bar" style={{ background: phaseColor(d.phase) }} />
            <div className="fc-header">
              <div>
                <div className="fc-name">{d.name}</div>
                <div className="fc-id">{d.id}</div>
              </div>
              <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
            </div>
            {dag && (
              <div className="fc-progress">
                <div className="fc-bar"><div className="fc-fill" style={{ width: `${pct}%` }} /></div>
                <div className="fc-text">{status}</div>
              </div>
            )}
            <div className="fc-time">{relTime(d.updated_at)}</div>
          </div>
        )
      })}
    </div>
  )
}
