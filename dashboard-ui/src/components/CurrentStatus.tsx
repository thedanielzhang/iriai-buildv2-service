import { useState } from 'react'
import { useStore } from '../store/useStore'
import { getActiveStatus, phaseCls, relTime } from '../utils'
import type { FeatureData, Group } from '../types'
import { DispatchDetail, FixAttemptsList } from './BugDetail'

export function CurrentStatus() {
  const { view, data } = useStore()

  if (view === 'overview') return null
  const d = data[view]
  if (!d) return null

  const status = getActiveStatus(d)
  const active = d.groups?.find(g => g.status === 'active')
  const isFixLoop = active?.verify_steps.length
    ? !active.verify_steps[active.verify_steps.length - 1].passed
    : false
  const completedGroups = d.groups?.filter(g => g.status === 'complete').length ?? 0
  const totalGroups = d.dag?.total_groups ?? 0
  const completedTasks = d.groups?.reduce((sum, g) => sum + g.completed_count, 0) ?? 0
  const totalTasks = d.dag?.total_tasks ?? 0
  const passedGates = Object.values(d.gates).filter(Boolean).length
  const totalGates = Object.keys(d.gates).length

  return (
    <div className="current-status">
      <div className="cs-status-row">
        <div className={`cs-dot ${isFixLoop ? 'fix-loop' : 'running'}`} />
        <div className="cs-status-text">{status}</div>
        <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
        {d.active_agent && (
          <span className="cs-agent-badge">{d.active_agent}</span>
        )}
        <div className="cs-inline-stats">
          <span className="cs-inline-stat">{completedGroups}/{totalGroups} groups</span>
          <span className="cs-inline-stat">{completedTasks}/{totalTasks} tasks</span>
          <span className="cs-inline-stat">{passedGates}/{totalGates} gates</span>
          <span className="cs-inline-stat">{relTime(d.updated_at)}</span>
        </div>
      </div>

      {active && <ActiveGroupDetail group={active} isFixLoop={isFixLoop} />}

      {!active && completedGroups === totalGroups && totalGroups > 0 && (
        <GatesSummary data={d} passedGates={passedGates} totalGates={totalGates} />
      )}
    </div>
  )
}

function ActiveGroupDetail({ group, isFixLoop }: { group: Group; isFixLoop: boolean }) {
  const remaining = group.task_count - group.completed_count
  const lastVerify = group.verify_steps.length > 0
    ? group.verify_steps[group.verify_steps.length - 1]
    : null
  const fixCount = group.fix_steps.filter(s => s.type === 'fix').length
  const verifyCount = group.verify_steps.length

  const fixAttempts = group.fix_steps.find(s => s.type === 'fix-attempts')

  // Unified timeline: verify + fix steps (excluding fix-attempts), sorted by time
  const allSteps = [
    ...group.verify_steps.map(v => ({ key: v.key, type: v.type, passed: v.passed as boolean | null, summary: v.summary, created_at: v.created_at })),
    ...group.fix_steps.filter(f => f.type !== 'fix-attempts'),
  ].sort((a, b) => a.created_at.localeCompare(b.created_at))

  const latestStep = allSteps.length > 0 ? allSteps[allSteps.length - 1] : null
  const trailSteps = allSteps.slice(-8)

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
            {verifyCount} verification attempt{verifyCount !== 1 ? 's' : ''}, {fixCount} fix{fixCount !== 1 ? 'es' : ''} dispatched
          </span>
        </div>
      )}

      {latestStep && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Current Progress</span>
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

      {lastVerify && isFixLoop && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Initial Verdict</span>
          <span className={`cs-verdict-badge ${lastVerify.passed ? 'cs-pass' : 'cs-fail'}`}>
            {lastVerify.passed ? 'PASS' : 'FAIL'}
          </span>
          <span className="cs-detail-time">{relTime(lastVerify.created_at)}</span>
          <div className="cs-detail-body">{lastVerify.summary}</div>
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
        <FixAttemptsCollapsible raw={fixAttempts.summary} time={fixAttempts.created_at} />
      )}
    </div>
  )
}

function trailClass(s: { passed: boolean | null; type: string }): string {
  if (s.passed === true) return 'pass'
  if (s.passed === false) return 'fail'
  if (s.type === 'rca' || s.type === 'triage' || s.type === 'dispatch') return 'info'
  return 'neutral'
}

function trailLabel(type: string): string {
  const map: Record<string, string> = {
    verify: 'V', 're-verify': 'RV', rca: 'R', triage: 'T',
    dispatch: 'D', reverify: 'RV', regression: 'Rg', fix: 'F',
  }
  return map[type] || type[0]?.toUpperCase() || '?'
}

function GatesSummary({ data, passedGates, totalGates }: { data: FeatureData; passedGates: number; totalGates: number }) {
  const pendingGates = Object.entries(data.gates)
    .filter(([, passed]) => !passed)
    .map(([name]) => name)

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
}

function FixAttemptsCollapsible({ raw, time }: { raw: string; time: string }) {
  const [open, setOpen] = useState(false)
  const count = (raw.match(/"bug_id"/g) || []).length

  return (
    <div className="cs-detail-block">
      <div className="cs-detail-row cs-link-row" onClick={() => setOpen(!open)}>
        <span className="cs-detail-label">Fix Attempts</span>
        <span className="cs-detail-value">
          {open ? '▼' : '▶'} {count} attempt{count !== 1 ? 's' : ''}
        </span>
        <span className="cs-detail-time">{relTime(time)}</span>
      </div>
      {open && <FixAttemptsList raw={raw} />}
    </div>
  )
}

