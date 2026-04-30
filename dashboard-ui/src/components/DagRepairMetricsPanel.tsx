import type { DagRepairCycle, DagRepairMetrics } from '../types'

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) return '-'
  if (seconds < 60) return `${seconds}s`
  const minutes = Math.floor(seconds / 60)
  const rem = seconds % 60
  if (minutes < 60) return rem ? `${minutes}m ${rem}s` : `${minutes}m`
  const hours = Math.floor(minutes / 60)
  const mins = minutes % 60
  return mins ? `${hours}h ${mins}m` : `${hours}h`
}

const stageLabels: Record<string, string> = {
  preflight_initial: 'Preflight',
  normal_verify: 'Normal verify',
  expanded_verify: 'Expanded verify',
  triage_rca: 'Triage + RCA',
  dispatch: 'Dispatch',
  fix: 'Fix',
  focused_reverify: 'Focused reverify',
  final_preflight_verify: 'Final preflight',
}

function statusClass(status: DagRepairCycle['status']): string {
  if (status === 'passed') return 'pass'
  if (status === 'failed') return 'fail'
  if (status === 'running') return 'running'
  return 'neutral'
}

export function DagRepairMetricsPanel({ metrics }: { metrics: DagRepairMetrics }) {
  const current = metrics.current_cycle
  const summary = metrics.summary
  const maxStageSeconds = Math.max(
    1,
    ...metrics.cycles.flatMap(c => Object.values(c.stage_durations || {})),
  )

  return (
    <div className="section dag-repair-panel">
      <div className="section-title">DAG Repair Observatory</div>

      <div className="dag-repair-hero">
        <div>
          <div className="dag-repair-kicker">Active group</div>
          <div className="dag-repair-active">
            {metrics.active_group_index == null ? 'None' : `G${metrics.active_group_index}`}
          </div>
          <div className="dag-repair-muted">
            Last checkpoint: {metrics.latest_checkpoint_group == null ? 'none' : `G${metrics.latest_checkpoint_group}`}
          </div>
        </div>
        <div>
          <div className="dag-repair-kicker">Elapsed</div>
          <div className="dag-repair-active">{formatDuration(summary.active_group_elapsed_seconds)}</div>
          <div className="dag-repair-muted">{summary.retry_count_for_active_group} retry cycle(s)</div>
        </div>
        <div>
          <div className="dag-repair-kicker">Parallel repair</div>
          <div className="dag-repair-active">{summary.fix_groups_applied}/{summary.fix_groups_scheduled}</div>
          <div className="dag-repair-muted">applied / scheduled groups</div>
        </div>
        <div>
          <div className="dag-repair-kicker">Sanitizer</div>
          <div className="dag-repair-active">
            {summary.sanitizer_ignored_paths + summary.sanitizer_rewritten_paths}
          </div>
          <div className="dag-repair-muted">
            ignored {summary.sanitizer_ignored_paths} / rewrote {summary.sanitizer_rewritten_paths}
          </div>
        </div>
      </div>

      {current && (
        <div className="dag-repair-current">
          <div className="dag-repair-row-title">
            Current cycle: G{current.group_idx} retry {current.retry}
            <span className={`dag-repair-status ${statusClass(current.status)}`}>{current.status}</span>
          </div>
          {current.final_blocker_summary && (
            <div className="dag-repair-blocker">{current.final_blocker_summary}</div>
          )}
        </div>
      )}

      <div className="dag-repair-stages">
        {metrics.cycles.slice(-4).flatMap(cycle =>
          Object.entries(cycle.stage_durations).map(([stage, seconds]) => (
            <div key={`${cycle.group_idx}-${cycle.retry}-${stage}`} className="dag-repair-stage">
              <div className="dag-repair-stage-label">
                <span>G{cycle.group_idx} r{cycle.retry} · {stageLabels[stage] || stage}</span>
                <span>{formatDuration(seconds)}</span>
              </div>
              <div className="dag-repair-bar">
                <div
                  className="dag-repair-bar-fill"
                  style={{ width: `${Math.max(4, Math.round((seconds / maxStageSeconds) * 100))}%` }}
                />
              </div>
            </div>
          )),
        )}
      </div>

      <div className="dag-repair-table">
        <div className="dag-repair-table-head">
          <span>Cycle</span>
          <span>Status</span>
          <span>Duration</span>
          <span>Lenses</span>
          <span>RCA</span>
          <span>Fixes</span>
          <span>Contr.</span>
        </div>
        {metrics.cycles.slice(-6).map(cycle => (
          <div key={`${cycle.group_idx}-${cycle.retry}`} className="dag-repair-table-row">
            <span>G{cycle.group_idx} r{cycle.retry}</span>
            <span className={`dag-repair-status ${statusClass(cycle.status)}`}>{cycle.status}</span>
            <span>{formatDuration(cycle.duration_seconds)}</span>
            <span>{cycle.lens_count}</span>
            <span>{cycle.rca_group_count}</span>
            <span>{cycle.applied_fix_count}/{cycle.fixable_group_count}</span>
            <span>{cycle.rejected_contradiction_count}/{cycle.contradiction_count}</span>
          </div>
        ))}
      </div>

      {summary.final_preflight_failures > 0 || summary.sanitizer_invalid_paths > 0 ? (
        <div className="dag-repair-warning">
          {summary.final_preflight_failures} final preflight failure(s);
          {' '}{summary.sanitizer_invalid_paths} invalid product path(s) preserved for fail-closed verification.
        </div>
      ) : null}
    </div>
  )
}
