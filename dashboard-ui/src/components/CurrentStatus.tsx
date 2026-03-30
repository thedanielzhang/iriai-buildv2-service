import { useStore } from '../store/useStore'
import { getActiveStatus, phaseCls, relTime } from '../utils'
import type { FeatureData, Group } from '../types'

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
      <div className="cs-left">
        <div className="cs-status-row">
          <div className={`cs-dot ${isFixLoop ? 'fix-loop' : 'running'}`} />
          <div className="cs-status-text">{status}</div>
          <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
        </div>

        {active && <ActiveGroupDetail group={active} isFixLoop={isFixLoop} />}

        {!active && completedGroups === totalGroups && totalGroups > 0 && (
          <GatesSummary data={d} passedGates={passedGates} totalGates={totalGates} />
        )}
      </div>

      <div className="cs-right">
        <div className="cs-stats">
          <StatBlock label="Groups" value={`${completedGroups}/${totalGroups}`} />
          <StatBlock label="Tasks" value={`${completedTasks}/${totalTasks}`} />
          <StatBlock label="Gates" value={`${passedGates}/${totalGates}`} />
          <StatBlock label="Updated" value={relTime(d.updated_at)} />
        </div>
      </div>
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

      {lastVerify && (
        <div className="cs-detail-block">
          <span className="cs-detail-label">Last Verdict</span>
          <span className={`cs-verdict-badge ${lastVerify.passed ? 'cs-pass' : 'cs-fail'}`}>
            {lastVerify.passed ? 'PASS' : 'FAIL'}
          </span>
          <div className="cs-detail-body">{lastVerify.summary}</div>
        </div>
      )}

      {group.fix_steps.length > 0 && (() => {
        const lastFix = group.fix_steps[group.fix_steps.length - 1]
        return (
          <div className="cs-detail-block">
            <span className="cs-detail-label">Last Fix</span>
            <span className={`cs-verdict-badge ${lastFix.passed === true ? 'cs-pass' : lastFix.passed === false ? 'cs-fail' : 'cs-neutral'}`}>
              {lastFix.type}
            </span>
            <div className="cs-detail-body">{lastFix.summary}</div>
          </div>
        )
      })()}
    </div>
  )
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

function StatBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="cs-stat">
      <div className="cs-stat-value">{value}</div>
      <div className="cs-stat-label">{label}</div>
    </div>
  )
}
