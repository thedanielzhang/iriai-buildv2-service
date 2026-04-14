import type { FeatureData, HealthState, PhaseMode } from './types'

export function isBugflowFeature(d: FeatureData): boolean {
  return d.workflow_name === 'bugfix-v2' && !!d.bugflow
}

export function relTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (sec < 0) return 'just now'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return `${Math.floor(sec / 86400)}d ago`
}

export function minutesSince(iso: string | null | undefined): number {
  if (!iso) return Infinity
  return (Date.now() - new Date(iso).getTime()) / 60000
}

export function phaseColor(p: string): string {
  if (p === 'implementation') return 'var(--amber)'
  if (p === 'complete') return 'var(--green)'
  if (p === 'failed') return 'var(--red)'
  return 'var(--brand)'
}

export function phaseCls(p: string): string {
  if (p === 'implementation') return 'impl'
  if (p === 'complete') return 'done'
  if (p === 'failed') return 'fail'
  return 'other'
}

export function getActiveStatus(data: { dag: any; groups: any[]; phase: string; active_gate?: string | null; active_agent?: string | null }): string {
  const featureData = data as FeatureData
  if (isBugflowFeature(featureData)) {
    const bugflow = featureData.bugflow!
    if (bugflow.status_text) return bugflow.status_text
    if (bugflow.active_step) return bugflow.active_step
    if (bugflow.promoting_lane_id) return `Promoting ${bugflow.promoting_lane_id}`
    if (bugflow.active_lane_ids?.length) return `Active ${bugflow.active_lane_ids.join(', ')}`
    if (bugflow.active_cluster_id) return `Active ${bugflow.active_cluster_id}`
    if (bugflow.active_report_id) return `Reviewing ${bugflow.active_report_id}`
    const openCount = Object.entries(bugflow.counts || {})
      .filter(([key]) => key !== 'resolved')
      .reduce((sum, [, value]) => sum + value, 0)
    if (openCount === 0 && (bugflow.counts?.resolved ?? 0) > 0) return 'Queue clear'
    if (openCount === 0) return 'Idle'
    return `${openCount} reports in queue`
  }

  const { dag, groups, phase, active_gate } = data
  if (phase === 'complete') return 'Complete'
  if (phase === 'failed') return 'Failed'
  if (!dag || !groups || groups.length === 0) return humanPhase(phase)

  const done = groups.filter(g => g.status === 'complete').length
  const active = groups.find(g => g.status === 'active')

  if (active) {
    const hasFailedVerify = active.verify_steps.length > 0 &&
      !active.verify_steps[active.verify_steps.length - 1].passed
    if (hasFailedVerify) {
      const fixCount = active.fix_steps.filter((s: any) => s.type === 'fix').length
      return `G${active.index} — fix loop (${fixCount} fixes dispatched)`
    }
    const rem = active.task_count - active.completed_count
    if (rem === 0)
      return `G${active.index}/${dag.total_groups} — verifying`
    return `G${active.index}/${dag.total_groups} — ${rem} task${rem !== 1 ? 's' : ''} in progress`
  }

  if (done === groups.length) {
    if (active_gate) return `Gate: ${active_gate}`
    const allGatesDone = dag.total_groups === done
    if (allGatesDone) return 'Quality gates'
    return 'DAG complete — quality gates'
  }

  return humanPhase(phase)
}

function humanPhase(p: string): string {
  const map: Record<string, string> = {
    pm: 'Product management',
    scoping: 'Scoping',
    design: 'Design',
    architecture: 'Architecture',
    plan_review: 'Plan review',
    task_planning: 'Task planning',
    implementation: 'Implementation',
    complete: 'Complete',
    failed: 'Failed',
  }
  return map[p] || p
}

// Stuck threshold: 3 min before backend's 10 min watchdog kill
const STUCK_THRESHOLD_MIN = 3

export function getHealthState(d: FeatureData): HealthState {
  if (isBugflowFeature(d)) {
    return d.bugflow?.health ?? 'idle'
  }

  if (d.phase === 'complete') return 'complete'
  if (d.phase === 'failed') return 'complete' // terminal — not stuck, just done

  // Best timestamp for "last known activity"
  const lastActive = d.last_activity_at || d.updated_at
  const idleMin = minutesSince(lastActive)
  const isStale = isFinite(idleMin) && idleMin > STUCK_THRESHOLD_MIN

  // Pre-implementation phases — no DAG yet
  if (!d.dag || !d.groups || d.groups.length === 0) {
    if (isStale) return 'stuck'
    return 'idle'
  }

  const active = d.groups.find(g => g.status === 'active')

  // DAG group actively executing
  if (active) {
    if (isStale) return 'stuck'
    const hasFailedVerify = active.verify_steps.length > 0 &&
      !active.verify_steps[active.verify_steps.length - 1].passed
    if (hasFailedVerify) return 'fix-loop'
    return 'running'
  }

  // All groups complete — gate review phase
  const allDone = d.groups.every(g => g.status === 'complete')
  if (allDone) {
    if (isStale) return 'stuck'
    // Gate fix loop: actively cycling through verdict→triage→RCA→fix→reverify→regression
    if (d.active_gate && d.active_gate_steps.length > 0) {
      // If the last verdict failed, we're in a gate fix loop
      const verdicts = d.active_gate_steps.filter(s => s.type === 'verdict')
      if (verdicts.length > 0 && !verdicts[verdicts.length - 1].passed) return 'fix-loop'
      return 'running'
    }
    return 'running'
  }

  if (isStale) return 'stuck'
  return 'running'
}

export function getPhaseMode(d: FeatureData): PhaseMode {
  if (isBugflowFeature(d)) {
    const health = getHealthState(d)
    if (health === 'complete-ish' || health === 'complete') return 'complete'
    if (health === 'fix-loop') return 'fix-loop'
    return 'implementing'
  }

  if (d.phase === 'complete') return 'complete'
  if (!d.dag || !d.groups || d.groups.length === 0) return 'planning'

  const active = d.groups.find(g => g.status === 'active')
  if (active) {
    const hasFailedVerify = active.verify_steps.length > 0 &&
      !active.verify_steps[active.verify_steps.length - 1].passed
    if (hasFailedVerify) return 'fix-loop'
    return 'implementing'
  }

  const allDone = d.groups.every(g => g.status === 'complete')
  if (allDone) return 'gates'
  return 'implementing'
}

export function healthColor(h: HealthState): string {
  switch (h) {
    case 'idle': return 'var(--text-2)'
    case 'running': return 'var(--brand)'
    case 'fix-loop': return 'var(--amber)'
    case 'degraded': return 'var(--amber)'
    case 'stuck': return 'var(--red)'
    case 'complete': return 'var(--green)'
    case 'awaiting-user': return 'var(--cyan)'
    case 'blocked': return 'var(--red)'
    case 'complete-ish': return 'var(--green)'
  }
}

export function healthCls(h: HealthState): string {
  return `health-${h}`
}

export function getGateAttemptNumber(steps: { key: string; type: string }[]): number {
  // Count verdict entries to determine attempt number
  return steps.filter(s => s.type === 'verdict').length
}
