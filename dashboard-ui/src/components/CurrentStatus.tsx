import { useState, useMemo, memo } from 'react'
import { useStore } from '../store/useStore'
import { getHealthState, minutesSince, relTime } from '../utils'
import type { FeatureData, Group } from '../types'
import { DispatchDetail, FixAttemptsList } from './BugDetail'

export function CurrentStatus() {
  const view = useStore(s => s.view)
  const d = useStore(s => s.data[s.view])

  if (view === 'overview') return null
  if (!d) return null

  const { active, isFixLoop, completedGroups, totalGroups, passedGates, totalGates, health, stuckMinutes } = useMemo(() => {
    const activeGroup = d.groups?.find(g => g.status === 'active') ?? null
    const hasFailedVerify = activeGroup?.verify_steps.length
      ? !activeGroup.verify_steps[activeGroup.verify_steps.length - 1].passed
      : false
    return {
      active: activeGroup,
      isFixLoop: hasFailedVerify,
      completedGroups: d.groups?.filter(g => g.status === 'complete').length ?? 0,
      totalGroups: d.dag?.total_groups ?? 0,
      completedTasks: d.groups?.reduce((sum, g) => sum + g.completed_count, 0) ?? 0,
      totalTasks: d.dag?.total_tasks ?? 0,
      passedGates: Object.values(d.gates).filter(Boolean).length,
      totalGates: Object.keys(d.gates).length,
      health: getHealthState(d),
      stuckMinutes: Math.floor(minutesSince(d.last_activity_at || d.updated_at)),
    }
  }, [d])

  return (
    <div className="current-status">
      {/* Stuck detection banner */}
      {health === 'stuck' && (
        <div className={`stuck-banner ${stuckMinutes > 5 ? 'stuck-red' : 'stuck-amber'}`}>
          <span className="stuck-icon">&#9888;</span>
          {' '}No activity for {stuckMinutes}m — agent may be stuck (watchdog kills at 10m)
        </div>
      )}

      {active && <ActiveGroupDetail group={active} isFixLoop={isFixLoop} />}

      {!active && completedGroups === totalGroups && totalGroups > 0 && (
        <GatesSummary data={d} passedGates={passedGates} totalGates={totalGates} />
      )}

      {!active && d.active_gate && d.active_gate_steps.length > 0 && (
        <ActiveGateDetail gateName={d.active_gate} steps={d.active_gate_steps} />
      )}
    </div>
  )
}

const ActiveGroupDetail = memo(function ActiveGroupDetail({ group, isFixLoop }: { group: Group; isFixLoop: boolean }) {
  const remaining = group.task_count - group.completed_count
  const lastVerify = group.verify_steps.length > 0
    ? group.verify_steps[group.verify_steps.length - 1]
    : null
  const fixCount = group.fix_steps.filter(s => s.type === 'fix').length
  const verifyCount = group.verify_steps.length

  const fixAttempts = group.fix_steps.find(s => s.type === 'fix-attempts')

  // Unified timeline: verify + fix steps (excluding fix-attempts), sorted by time
  const allSteps = useMemo(() => {
    return [
      ...group.verify_steps.map(v => ({ key: v.key, type: v.type, passed: v.passed as boolean | null, summary: v.summary, created_at: v.created_at })),
      ...group.fix_steps.filter(f => f.type !== 'fix-attempts'),
    ].sort((a, b) => a.created_at.localeCompare(b.created_at))
  }, [group.verify_steps, group.fix_steps])

  // Group steps into iterations (each verify/re-verify/reverify starts a new iteration)
  const iterations = useMemo(() => {
    const iters: (typeof allSteps)[] = []
    let current: typeof allSteps = []
    for (const s of allSteps) {
      if ((s.type === 'verify' || s.type === 're-verify' || s.type === 'reverify') && current.length > 0) {
        iters.push(current)
        current = []
      }
      current.push(s)
    }
    if (current.length) iters.push(current)
    return iters
  }, [allSteps])

  const latestStep = allSteps.length > 0 ? allSteps[allSteps.length - 1] : null
  const trailSteps = allSteps.slice(-12)
  const currentIteration = iterations.length
  const maxIterations = 7

  return (
    <div className="cs-detail">
      <div className="cs-detail-row">
        <span className="cs-detail-label">Active Group</span>
        <span className="cs-detail-value">
          G{group.index} — {group.completed_count}/{group.task_count} tasks complete
          {remaining > 0 && `, ${remaining} remaining`}
        </span>
      </div>

      {isFixLoop && (
        <div className="cs-detail-row">
          <span className="cs-detail-label">Fix Loop</span>
          <span className="cs-detail-value cs-fix-loop">
            <span className="cs-iter-badge">Iter {currentIteration} / {maxIterations}</span>
            {' '}{verifyCount} verification{verifyCount !== 1 ? 's' : ''}, {fixCount} fix{fixCount !== 1 ? 'es' : ''}
          </span>
        </div>
      )}

      {latestStep && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Current Progress</span>
          <div className="cs-progress-trail">
            {iterations.length > 1 ? (
              // Group trail pills by iteration with gap separators
              iterations.map((iter, iterIdx) => {
                // Only show pills from recent iterations (last 3)
                if (iterIdx < iterations.length - 3) return null
                const iterPills = iter.slice(-4) // last 4 steps per iteration
                return (
                  <span key={iterIdx} className="cs-iter-group">
                    {iterIdx > Math.max(0, iterations.length - 3) && (
                      <span className="cs-iter-sep" />
                    )}
                    {iterPills.map((s, i) => (
                      <span key={i} className={`cs-trail-pill ${trailClass(s)}`}>
                        {trailLabel(s.type)}
                      </span>
                    ))}
                  </span>
                )
              })
            ) : (
              trailSteps.map((s, i) => (
                <span key={i} className={`cs-trail-pill ${trailClass(s)}`}>
                  {trailLabel(s.type)}
                </span>
              ))
            )}
          </div>
          <div className="cs-latest-step">
            <span className={`tl-type ${latestStep.type}`}>{latestStep.type}</span>
            {latestStep.passed === true && <span className="tl-pass pass">PASS</span>}
            {latestStep.passed === false && <span className="tl-pass fail">FAIL</span>}
            <span className="cs-detail-time">{relTime(latestStep.created_at)}</span>
          </div>
          {latestStep.summary && latestStep.type !== 'dispatch' && (
            <div className={`cs-detail-body ${isFixLoop ? 'cs-detail-body-expanded' : ''}`}>
              {latestStep.summary}
            </div>
          )}
          {latestStep.type === 'dispatch' && latestStep.summary && (
            <div className={`cs-detail-body ${isFixLoop ? 'cs-detail-body-expanded' : ''}`}>
              <DispatchDetail raw={latestStep.summary} />
            </div>
          )}
        </div>
      )}

      {lastVerify && isFixLoop && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Latest Verdict</span>
          <span className={`cs-verdict-badge ${lastVerify.passed ? 'cs-pass' : 'cs-fail'}`}>
            {lastVerify.passed ? 'PASS' : 'FAIL'}
          </span>
          <span className="cs-detail-time">{relTime(lastVerify.created_at)}</span>
          <div className={`cs-detail-body ${isFixLoop ? 'cs-detail-body-expanded' : ''}`}>
            {lastVerify.summary}
          </div>
        </div>
      )}

      {!isFixLoop && lastVerify && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Last Verdict</span>
          <span className={`cs-verdict-badge ${lastVerify.passed ? 'cs-pass' : 'cs-fail'}`}>
            {lastVerify.passed ? 'PASS' : 'FAIL'}
          </span>
          <span className="cs-detail-time">{relTime(lastVerify.created_at)}</span>
          <div className="cs-detail-body">{lastVerify.summary}</div>
        </div>
      )}

      {fixAttempts && fixAttempts.summary && (
        <FixAttemptsCollapsible
          raw={fixAttempts.summary}
          time={fixAttempts.created_at}
          defaultOpen={isFixLoop}
        />
      )}
    </div>
  )
})

function trailClass(s: { passed: boolean | null; type: string }): string {
  if (s.passed === true) return 'pass'
  if (s.passed === false) return 'fail'
  if (s.type === 'rca' || s.type === 'triage' || s.type === 'dispatch') return 'info'
  return 'neutral'
}

function trailLabel(type: string): string {
  const map: Record<string, string> = {
    verify: 'V', 're-verify': 'RV', rca: 'RCA', triage: 'T',
    dispatch: 'D', reverify: 'RV', regression: 'Rg', fix: 'Fix',
    verdict: 'Rev',
  }
  return map[type] || type[0]?.toUpperCase() || '?'
}

const ActiveGateDetail = memo(function ActiveGateDetail({ gateName, steps }: { gateName: string; steps: import('../types').TimelineEntry[] }) {
  const { verdictCount, dispatchCount, latestStep, trailSteps, currentCyclePhase } = useMemo(() => {
    const verdicts = steps.filter(s => s.type === 'verdict')
    const dispatches = steps.filter(s => s.type === 'dispatch')
    const latest = steps.length > 0 ? steps[steps.length - 1] : null

    // Determine what phase of the current cycle we're in
    let phase = 'reviewing'
    if (latest) {
      if (latest.type === 'verdict') phase = latest.passed ? 'passed' : 'verdict failed'
      else if (latest.type === 'triage') phase = 'triaging bugs'
      else if (latest.type === 'rca') phase = 'analyzing root causes'
      else if (latest.type === 'dispatch') phase = 'dispatching fixes'
      else if (latest.type === 'reverify') phase = 'verifying fixes'
      else if (latest.type === 'regression') phase = latest.passed ? 'regression passed' : 'regression failed'
    }

    // Show last 12 trail pills from recent cycles
    return {
      verdictCount: verdicts.length,
      dispatchCount: dispatches.length,
      latestStep: latest,
      trailSteps: steps.slice(-12),
      currentCyclePhase: phase,
    }
  }, [steps])

  return (
    <div className="cs-detail">
      <div className="cs-detail-row">
        <span className="cs-detail-label">Active Gate</span>
        <span className="cs-detail-value">
          {gateName}
          {verdictCount > 0 && (
            <> — <span className="cs-iter-badge">Cycle {verdictCount}</span> {currentCyclePhase}</>
          )}
          {verdictCount === 0 && ' — initial review'}
        </span>
      </div>

      {dispatchCount > 0 && (
        <div className="cs-detail-row">
          <span className="cs-detail-label">Fix History</span>
          <span className="cs-detail-value">
            {dispatchCount} fix dispatch{dispatchCount !== 1 ? 'es' : ''} across {verdictCount} review cycle{verdictCount !== 1 ? 's' : ''}
          </span>
        </div>
      )}

      {latestStep && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Gate Progress</span>
          <div className="cs-progress-trail">
            {trailSteps.map((s, i) => (
              <span key={i} className={`cs-trail-pill ${trailClass(s)}`}>
                {trailLabel(s.type)}
              </span>
            ))}
          </div>
          <div className="cs-latest-step">
            <span className={`tl-type ${latestStep.type}`}>{latestStep.type}</span>
            {latestStep.passed === true && <span className="tl-pass pass">PASS</span>}
            {latestStep.passed === false && <span className="tl-pass fail">FAIL</span>}
            <span className="cs-detail-time">{relTime(latestStep.created_at)}</span>
          </div>
          {latestStep.summary && latestStep.type !== 'dispatch' && (
            <div className="cs-detail-body">{latestStep.summary}</div>
          )}
          {latestStep.type === 'dispatch' && latestStep.summary && (
            <div className="cs-detail-body">
              <DispatchDetail raw={latestStep.summary} />
            </div>
          )}
        </div>
      )}
    </div>
  )
})

const GatesSummary = memo(function GatesSummary({ data, passedGates, totalGates }: { data: FeatureData; passedGates: number; totalGates: number }) {
  const pendingGates = useMemo(() =>
    Object.entries(data.gates)
      .filter(([, passed]) => !passed)
      .map(([name]) => name),
    [data.gates]
  )

  return (
    <div className="cs-detail">
      <div className="cs-detail-row">
        <span className="cs-detail-label">Gates</span>
        <span className="cs-detail-value">
          {passedGates}/{totalGates} passed
          {pendingGates.length > 0 && ` — pending: ${pendingGates.join(', ')}`}
        </span>
      </div>
    </div>
  )
})

function FixAttemptsCollapsible({ raw, time, defaultOpen = false }: { raw: string; time: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const count = (raw.match(/"bug_id"/g) || []).length

  return (
    <div className="cs-detail-block">
      <div className="cs-detail-row cs-link-row" onClick={() => setOpen(!open)}>
        <span className="cs-detail-label">Fix Attempts</span>
        <span className="cs-detail-value">
          {open ? '\u25BC' : '\u25B6'} {count} attempt{count !== 1 ? 's' : ''}
        </span>
        <span className="cs-detail-time">{relTime(time)}</span>
      </div>
      {open && <FixAttemptsList raw={raw} />}
    </div>
  )
}
