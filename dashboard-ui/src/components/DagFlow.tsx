import { useState } from 'react'
import type { Group, Task, TimelineEntry, VerifyStep } from '../types'
import { relTime } from '../utils'

export function DagFlow({ groups, totalTasks, totalGroups }: {
  groups: Group[]
  totalTasks: number
  totalGroups: number
}) {
  const activeGroup = groups.find(g => g.status === 'active')

  return (
    <div className="section">
      <div className="section-title">
        DAG Progress — {totalTasks} tasks in {totalGroups} groups
      </div>
      <div className="dag-flow">
        <div className="dag-main">
          {groups.map((g, i) => (
            <div key={g.index} style={{ display: 'flex', alignItems: 'center' }}>
              {i > 0 && (
                <div className={`dag-edge ${
                  g.status === 'complete' || groups[i - 1].status === 'complete' ? 'done' : ''
                }`} />
              )}
              <div className="dag-node">
                <div className={`dag-circle ${g.status}`}>G{g.index}</div>
                <div className="dag-label">{g.completed_count}/{g.task_count}</div>
              </div>
            </div>
          ))}
        </div>

        {activeGroup && activeGroup.tasks.length > 0 && (
          <ActiveGroupExpanded group={activeGroup} />
        )}

        {activeGroup && (activeGroup.verify_steps.length > 0 || activeGroup.fix_steps.length > 0) && (
          <FixBranch
            groupIndex={activeGroup.index}
            verifySteps={activeGroup.verify_steps}
            fixSteps={activeGroup.fix_steps}
          />
        )}
      </div>
    </div>
  )
}

function ActiveGroupExpanded({ group }: { group: Group }) {
  const completed = group.tasks.filter(t => t.status === 'complete').length
  return (
    <div className="fix-branch">
      <div className="fix-branch-header">
        <div className="fix-branch-connector" />
        <div className="fix-branch-title">G{group.index} Tasks</div>
        <div className="fix-branch-count">{completed}/{group.tasks.length} complete</div>
      </div>
      <div className="dag-task-grid">
        {group.tasks.map(t => (
          <TaskDot key={t.id} task={t} />
        ))}
      </div>
    </div>
  )
}

function TaskDot({ task }: { task: Task }) {
  const [open, setOpen] = useState(false)
  const icon = task.status === 'complete' ? '✓' : task.status === 'in_progress' ? '◈' : '○'

  return (
    <div className="dag-task-item" onClick={() => setOpen(!open)}>
      <div className="dag-task-dot-row">
        <span className={`dag-task-dot ${task.status}`}>{icon}</span>
        <span className="dag-task-name">{task.name || task.id}</span>
        {task.subfeature_id && <span className="dag-task-sf">{task.subfeature_id}</span>}
      </div>
      {open && (
        <div className="dag-task-detail">
          {task.summary && <div className="dag-task-summary">{task.summary}</div>}
          {task.description && <div className="dag-task-desc">{task.description}</div>}
          {task.file_scope?.length > 0 && (
            <div className="dispatch-files">
              {task.file_scope.map((f, i) => (
                <code key={i}><span className={`file-action ${f.action}`}>{f.action}</span> {f.path}</code>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FixBranch({ groupIndex, verifySteps, fixSteps }: {
  groupIndex: number
  verifySteps: VerifyStep[]
  fixSteps: TimelineEntry[]
}) {
  const steps: { type: string; label: string; passed: boolean | null; time: string; summary: string }[] = [
    ...verifySteps.map(v => ({
      type: 'verify',
      label: v.type === 're-verify' ? 'Re-verify' : 'Verify',
      passed: v.passed,
      time: v.created_at,
      summary: v.summary,
    })),
    ...fixSteps
      .filter(f => f.type !== 'fix-attempts')
      .map(f => ({
        type: f.type,
        label: f.type === 'fix' ? 'Fix' : f.type === 'rca' ? 'RCA' : f.type === 'triage' ? 'Triage' : f.type === 'dispatch' ? 'Dispatch' : f.type === 'reverify' ? 'Re-verify' : f.type === 'regression' ? 'Regr.' : f.type,
        passed: f.passed,
        time: f.created_at,
        summary: f.summary,
      })),
  ].sort((a, b) => a.time.localeCompare(b.time))

  if (!steps.length) return null

  return (
    <div className="fix-branch">
      <div className="fix-branch-header">
        <div className="fix-branch-connector" />
        <div className="fix-branch-title">Fix Loop — G{groupIndex}</div>
        <div className="fix-branch-count">{steps.length} step{steps.length !== 1 ? 's' : ''}</div>
      </div>
      <div className="fix-flow">
        {steps.map((s, i) => (
          <div key={i} className="fix-flow-segment">
            {i > 0 && <div className={`fix-edge ${stepEdgeClass(steps[i - 1])}`} />}
            <div className="fix-node">
              <div className={`fix-circle ${stepClass(s)}`}>
                {stepIcon(s)}
              </div>
              <div className="fix-node-label">{s.label}</div>
              <div className="fix-node-time">{relTime(s.time)}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function stepClass(s: { passed: boolean | null; type: string }): string {
  if (s.passed === true) return 'pass'
  if (s.passed === false) return 'fail'
  if (s.type === 'rca' || s.type === 'triage' || s.type === 'dispatch') return 'info'
  return 'neutral'
}

function stepEdgeClass(prev: { passed: boolean | null }): string {
  if (prev.passed === true) return 'pass'
  if (prev.passed === false) return 'fail'
  return ''
}

function stepIcon(s: { passed: boolean | null; type: string }): string {
  if (s.passed === true) return '✓'
  if (s.passed === false) return '✗'
  if (s.type === 'rca') return '?'
  if (s.type === 'triage') return '÷'
  if (s.type === 'dispatch') return '→'
  if (s.type === 'fix') return '⚡'
  return '•'
}
