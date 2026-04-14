import { memo, useMemo } from 'react'
import { useStore } from '../store/useStore'
import { phaseCls, relTime, getActiveStatus, getHealthState, healthColor, minutesSince } from '../utils'
import type { FeatureData, Group, TimelineEntry } from '../types'

const trailLabel: Record<string, string> = {
  verify: 'V', 're-verify': 'RV', rca: 'RCA', triage: 'T',
  dispatch: 'D', reverify: 'RV', regression: 'Rg', fix: 'Fix', verdict: 'Rev',
  queue: 'Q', report: 'R', cluster: 'C', decision: 'Dec', push: 'Push', observation: 'Obs',
}

function trailPillCls(step: TimelineEntry): string {
  if (step.passed === true) return 'pass'
  if (step.passed === false) return 'fail'
  if (step.type === 'rca' || step.type === 'triage' || step.type === 'dispatch') return 'info'
  return 'neutral'
}

interface FeatureCardProps {
  id: string
  d: FeatureData | undefined
  onSelect: (id: string) => void
}

const FeatureCard = memo(function FeatureCard({ id, d, onSelect }: FeatureCardProps) {
  if (!d) {
    return (
      <div className="feature-card" onClick={() => onSelect(id)}>
        <div className="loading"><div className="spinner" />Loading {id}...</div>
      </div>
    )
  }

  const health = getHealthState(d)
  const accentColor = healthColor(health)

  const pct = useMemo(() => {
    if (!d.dag || !d.groups) return 0
    const done = d.groups.filter(g => g.status === 'complete').length
    return Math.round(done / d.dag.total_groups * 100)
  }, [d.dag, d.groups])

  const status = useMemo(() => getActiveStatus(d), [d])

  const activeGroup: Group | undefined = useMemo(
    () => d.groups?.find(g => g.status === 'active'),
    [d.groups],
  )

  const inProgressTasks = useMemo(() => {
    if (!activeGroup) return []
    return activeGroup.tasks.filter(t => t.status === 'in_progress')
  }, [activeGroup])

  const trailSteps = useMemo(() => {
    if (!activeGroup) {
      return d.bugflow?.artifact_timeline?.slice(0, 4) ?? []
    }
    const combined: TimelineEntry[] = [
      ...activeGroup.verify_steps.map(v => ({
        key: v.key, type: v.type, passed: v.passed as boolean | null,
        summary: v.summary, created_at: v.created_at,
      })),
      ...activeGroup.fix_steps,
    ]
    return combined.slice(-4)
  }, [activeGroup])

  const bugflowSummary = useMemo(() => {
    if (!d.bugflow) return null
    const counts = d.bugflow.counts || {}
    const open = Object.entries(counts)
      .filter(([key]) => key !== 'resolved')
      .reduce((sum, [, value]) => sum + value, 0)
    return `${open} open • ${counts.queued ?? 0} queued • ${counts.resolved ?? 0} resolved`
  }, [d.bugflow])

  const healthLine = useMemo(() => {
    if (health === 'stuck') {
      const mins = Math.round(minutesSince(d.last_activity_at))
      return { text: `No activity for ${mins}m`, color: 'var(--red)' }
    }
    if (health === 'fix-loop') {
      return { text: 'Fix loop \u2014 verify FAIL', color: 'var(--amber)' }
    }
    if (health === 'running') {
      return { text: status, color: 'var(--text-2)' }
    }
    return null
  }, [health, d.last_activity_at, status])

  return (
    <div
      className="feature-card"
      onClick={() => onSelect(id)}
      style={{ borderLeft: `4px solid ${accentColor}` }}
    >
      <div className="fc-header">
        <div>
          <div className="fc-name">{d.name}</div>
          <div className="fc-id">{d.id}</div>
        </div>
        <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
      </div>

      {d.dag && (
        <div className="fc-progress">
          <div className="fc-bar"><div className="fc-fill" style={{ width: `${pct}%` }} /></div>
        </div>
      )}

      {healthLine && (
        <div
          className="fc-health-line"
          style={{
            fontFamily: 'var(--mono)',
            fontSize: 11,
            color: healthLine.color,
            marginTop: 4,
            marginBottom: 4,
            lineHeight: '16px',
          }}
        >
          {healthLine.text}
        </div>
      )}

      {inProgressTasks.length > 0 && (
        <div
          className="fc-active-tasks"
          style={{
            fontFamily: 'var(--mono)',
            fontSize: 11,
            color: 'var(--text-2)',
            marginBottom: 4,
            whiteSpace: 'nowrap',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
          }}
        >
          <span style={{ color: 'var(--blue)', marginRight: 4 }}>{'\u25CF'}</span>
          {inProgressTasks.slice(0, 2).map(t => t.name).join(', ')}
          {inProgressTasks.length > 2 && (
            <span style={{ color: 'var(--text-3)', marginLeft: 4 }}>
              +{inProgressTasks.length - 2} more
            </span>
          )}
        </div>
      )}

      {trailSteps.length > 0 && (
        <div
          className="fc-trail"
          style={{ display: 'flex', gap: 3, marginBottom: 6, flexWrap: 'wrap' }}
        >
          {trailSteps.map((step, i) => (
            <span key={step.key || i} className={`cs-trail-pill ${trailPillCls(step)}`}>
              {trailLabel[step.type] || step.type}
            </span>
          ))}
        </div>
      )}

      {bugflowSummary && (
        <div className="fc-text">{bugflowSummary}</div>
      )}

      <div className="fc-time">{relTime(d.last_activity_at || d.updated_at)}</div>
    </div>
  )
})

export function Overview() {
  const tracked = useStore(s => s.tracked)
  const data = useStore(s => s.data)
  const setView = useStore(s => s.setView)

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
      {tracked.map(id => (
        <FeatureCard key={id} id={id} d={data[id]} onSelect={setView} />
      ))}
    </div>
  )
}
