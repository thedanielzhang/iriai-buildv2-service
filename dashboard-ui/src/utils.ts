export function relTime(iso: string): string {
  if (!iso) return ''
  const sec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (sec < 0) return 'just now'
  if (sec < 60) return `${sec}s ago`
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`
  return `${Math.floor(sec / 86400)}d ago`
}

export function phaseColor(p: string): string {
  if (p === 'implementation') return 'var(--amber)'
  if (p === 'complete') return 'var(--green)'
  if (p === 'failed') return 'var(--red)'
  return 'var(--blue)'
}

export function phaseCls(p: string): string {
  if (p === 'implementation') return 'impl'
  if (p === 'complete') return 'done'
  if (p === 'failed') return 'fail'
  return 'other'
}

export function getActiveStatus(data: { dag: any; groups: any[]; phase: string }): string {
  const { dag, groups, phase } = data
  if (!dag || !groups) return phase
  const done = groups.filter(g => g.status === 'complete').length
  const active = groups.find(g => g.status === 'active')
  if (active) {
    const hasFailedVerify = active.verify_steps.length > 0 &&
      !active.verify_steps[active.verify_steps.length - 1].passed
    if (hasFailedVerify)
      return `Fix loop on Group ${active.index} — verifying fixes`
    const rem = active.task_count - active.completed_count
    if (rem === 0)
      return `Group ${active.index}/${dag.total_groups} — running verification`
    return `Group ${active.index}/${dag.total_groups} — ${rem} tasks in progress`
  }
  if (done === dag.total_groups) return 'DAG complete — running quality gates'
  return phase
}
