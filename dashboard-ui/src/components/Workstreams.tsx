import type { Workstream } from '../types'

export function Workstreams({ workstreams }: { workstreams: Workstream[] }) {
  if (!workstreams.length) return null

  return (
    <div className="section">
      <div className="section-title">Workstreams</div>
      <div className="ws-grid">
        {workstreams.map(ws => {
          const pct = ws.total_tasks ? Math.round(ws.completed_tasks / ws.total_tasks * 100) : 0
          return (
            <div key={ws.id} className="ws-card">
              <div className="ws-card-name">{ws.name}</div>
              <div className="ws-card-subs">{ws.subfeature_slugs.join(', ')}</div>
              {ws.depends_on.length > 0 && (
                <div className="ws-card-deps">depends on: {ws.depends_on.join(', ')}</div>
              )}
              <div className="ws-card-bar">
                <div className="ws-card-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="ws-card-stat">{ws.completed_tasks}/{ws.total_tasks} tasks</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
