import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { useStore } from '../store/useStore'
import type {
  BugflowDecision,
  BugflowLane,
  BugflowReport,
  BugflowTimelineSection,
  FixAttempt,
  TimelineEntry,
} from '../types'
import { DispatchDetail, FixAttemptItem } from './BugDetail'
import { CollapsibleSection } from './CollapsibleSection'
import { EventLog } from './EventLog'
import { healthCls, phaseCls, relTime } from '../utils'

const BOARD_COLUMNS: Array<{ title: string; lane: string }> = [
  { title: 'Needs Intake', lane: 'intake_pending' },
  { title: 'Awaiting Confirm', lane: 'awaiting_confirmation' },
  { title: 'Queued', lane: 'queued' },
  { title: 'Active', lane: 'active_fix' },
  { title: 'Pending Retriage', lane: 'pending_retriage' },
  { title: 'Resolved', lane: 'resolved' },
  { title: 'Blocked', lane: 'blocked' },
]

export function BugflowView() {
  const d = useStore(s => s.data[s.view])
  const setView = useStore(s => s.setView)
  const addFeature = useStore(s => s.addFeature)

  if (!d?.bugflow) {
    return <div className="loading"><div className="spinner" />Loading bugflow…</div>
  }

  const bugflow = d.bugflow
  const counts = bugflow.counts || {}

  const reports = useMemo(
    () => [...(bugflow.reports || [])].sort((a, b) => {
      const laneA = reportLane(a.status)
      const laneB = reportLane(b.status)
      if (laneA !== laneB) return laneWeight(laneA) - laneWeight(laneB)
      return (b.updated_at || '').localeCompare(a.updated_at || '')
    }),
    [bugflow.reports],
  )

  const [selectedReportId, setSelectedReportId] = useState<string | null>(reports[0]?.report_id ?? null)
  const [detailTab, setDetailTab] = useState<'overview' | 'fix-history' | 'timeline'>('overview')

  useEffect(() => {
    if (!reports.length) {
      setSelectedReportId(null)
      return
    }
    if (!selectedReportId || !reports.some(r => r.report_id === selectedReportId)) {
      setSelectedReportId(reports[0].report_id)
    }
  }, [reports, selectedReportId])

  useEffect(() => {
    setDetailTab('overview')
  }, [selectedReportId])

  const selectedReport = reports.find(report => report.report_id === selectedReportId) ?? null
  const activeLanes = bugflow.active_lanes ?? []
  const promotingLane = bugflow.promoting_lane ?? null
  const queuedPromotionLanes = bugflow.verified_pending_promotion ?? []
  const recoveringLaneIds = bugflow.recovering_lane_ids ?? []
  const stalledLaneIds = bugflow.stalled_lane_ids ?? []
  const strategyPendingClusterIds = bugflow.strategy_pending_cluster_ids ?? []

  const openReportCount = BOARD_COLUMNS
    .filter(col => col.lane !== 'resolved')
    .reduce((sum, col) => sum + (counts[col.lane] ?? 0), 0)

  const waitingOnUserCount = reports.filter(report => {
    const threadStatus = `${report.thread_status || ''}`.toLowerCase()
    return reportLane(report.status) === 'awaiting_confirmation' || threadStatus.includes('waiting')
  }).length

  const oldestOpenTimestamp = reports
    .filter(report => reportLane(report.status) !== 'resolved')
    .map(report => report.created_at || report.updated_at)
    .filter(Boolean)
    .sort()[0] || null

  const lastReverify = findLatestEntry(bugflow.artifact_timeline, 'reverify')
  const lastRegression = findLatestEntry(bugflow.artifact_timeline, 'regression')
  const resolvedNoReproCount = reports.filter(report => `${report.status}`.startsWith('resolved-no-repro')).length

  const touchedRepos = bugflow.repo_status?.repos?.filter(repo => repo.touched) ?? []
  const sourceFeatureId = bugflow.source_feature_id || d.source_feature_id

  return (
    <>
      <div className="status-strip">
        <div className="status-strip-main">
          <div className="ws-back" onClick={() => setView('overview')}>
            &larr;
          </div>
          <div className="status-strip-name">{d.name}</div>
          <span className="bugflow-id-badge">{d.id}</span>
          <span className={`health-dot ${healthCls(bugflow.health)}`} />
          <div className="status-strip-text">{bugflow.status_text || bugflow.active_step}</div>
          <span className={`phase-badge ${phaseCls(d.phase)}`}>{d.phase}</span>
          {d.active_agent && <span className="cs-agent-badge">{d.active_agent}</span>}
          {sourceFeatureId && (
            <button
              className="bugflow-link-btn"
              onClick={() => {
                addFeature(sourceFeatureId)
                setView(sourceFeatureId)
              }}
            >
              Source {sourceFeatureId}
            </button>
          )}
          <div className="status-strip-stats">
            <span className="cs-inline-stat">{openReportCount} open</span>
            <span className="cs-inline-stat">{counts.intake_pending ?? 0} intake</span>
            <span className="cs-inline-stat">{counts.awaiting_confirmation ?? 0} awaiting</span>
            <span className="cs-inline-stat">{counts.queued ?? 0} queued</span>
            <span className="cs-inline-stat">{counts.active_fix ?? 0} active</span>
            <span className="cs-inline-stat">{counts.pending_retriage ?? 0} retriage</span>
            <span className="cs-inline-stat">{counts.blocked ?? 0} blocked</span>
            <span className="cs-inline-stat">{counts.resolved ?? 0} resolved</span>
            <span className="cs-inline-stat">{relTime(bugflow.last_transition_at || d.last_activity_at || d.updated_at)}</span>
          </div>
        </div>
      </div>

      <div className="bugflow-summary-grid">
        <SummaryCard title="Queue">
          <SummaryRow label="Total reports" value={`${reports.length}`} />
          <SummaryRow label="Queued clusters" value={`${bugflow.clusters.filter(cluster => clusterLane(cluster.status) === 'queued').length}`} />
          <SummaryRow label="Oldest unresolved" value={oldestOpenTimestamp ? relTime(oldestOpenTimestamp) : 'None'} />
          <SummaryRow label="Waiting on user" value={`${waitingOnUserCount}`} />
        </SummaryCard>

        <SummaryCard title="Active Work">
          <SummaryRow label="Promoting" value={promotingLane?.lane_id || 'None'} />
          <SummaryRow label="Active lanes" value={activeLanes.length ? activeLanes.map(lane => lane.lane_id).join(', ') : 'None'} />
          <SummaryRow label="Recovering" value={recoveringLaneIds.length ? recoveringLaneIds.join(', ') : 'None'} />
          <SummaryRow label="Stalled" value={stalledLaneIds.length ? stalledLaneIds.join(', ') : 'None'} />
          <SummaryRow
            label="Queued promotion"
            value={queuedPromotionLanes.length ? queuedPromotionLanes.map(lane => lane.lane_id).join(', ') : 'None'}
          />
          <SummaryRow
            label="Active reports"
            value={Array.from(new Set(activeLanes.flatMap(lane => lane.report_ids || []))).join(', ') || '—'}
          />
        </SummaryCard>

        <SummaryCard title="Verification">
          <SummaryRow label="Last reverify" value={verdictLabel(lastReverify)} />
          <SummaryRow label="Last regression" value={verdictLabel(lastRegression)} />
          <SummaryRow label="Resolved-no-repro" value={`${resolvedNoReproCount}`} />
          <SummaryRow label="Pending retriage" value={`${counts.pending_retriage ?? 0}`} />
          <SummaryRow label="Strategy pending" value={strategyPendingClusterIds.length ? strategyPendingClusterIds.join(', ') : 'None'} />
        </SummaryCard>

        <SummaryCard title="Repos">
          <SummaryRow label="Touched repos" value={touchedRepos.length ? touchedRepos.map(repo => repo.repo_name).join(', ') : 'None'} />
          <SummaryRow label="Branch" value={bugflow.repo_status?.branch_name || '—'} />
          <SummaryRow label="Last pushed" value={formatRepoPushes(bugflow.repo_status?.repos || [])} />
          <SummaryRow label="Verified / unpushed" value={bugflow.repo_status?.has_unpushed_verified_work ? 'Yes' : 'No'} />
        </SummaryCard>
      </div>

      <CollapsibleSection title={`Queue Board — ${reports.length} reports`} defaultOpen>
        <div className="bugflow-board">
          {BOARD_COLUMNS.map(column => {
            const columnReports = reports.filter(report => reportLane(report.status) === column.lane)
            return (
              <div key={column.lane} className="bugflow-lane">
                <div className="bugflow-lane-header">
                  <span>{column.title}</span>
                  <span className="bugflow-lane-count">{columnReports.length}</span>
                </div>
                <div className="bugflow-lane-body">
                  {columnReports.length === 0 && <div className="bugflow-empty">No reports</div>}
                  {columnReports.map(report => (
                    <button
                      key={report.report_id}
                      className={`bugflow-report-card ${selectedReportId === report.report_id ? 'selected' : ''}`}
                      onClick={() => setSelectedReportId(report.report_id)}
                    >
                      <div className="bugflow-report-top">
                        <span className="bugflow-report-id">{report.report_id}</span>
                        <span className={`bugflow-status-pill ${reportLane(report.status)}`}>{prettyLabel(report.status)}</span>
                      </div>
                      <div className="bugflow-report-title">{report.title || report.summary || report.report_id}</div>
                      <div className="bugflow-report-meta">
                        <span>{prettyLabel(report.category)}</span>
                        <span>{prettyLabel(report.severity)}</span>
                        {report.cluster_id && <span>{report.cluster_id}</span>}
                      </div>
                      {report.validation_summary && (
                        <div className="bugflow-report-summary">{report.validation_summary}</div>
                      )}
                      {!report.validation_summary && report.summary && (
                        <div className="bugflow-report-summary">{report.summary}</div>
                      )}
                      <div className="bugflow-report-footer">
                        <span>{report.thread_status || 'ready'}</span>
                        <span>{relTime(report.updated_at)}</span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      </CollapsibleSection>

      <div className="bugflow-detail-grid">
        <div className="bugflow-panel">
          <div className="bugflow-panel-header">
            <div>
              <div className="bugflow-panel-title">Lane Activity</div>
              <div className="bugflow-panel-subtitle">
                {promotingLane ? `Promoting ${promotingLane.lane_id}` : activeLanes.length ? `Running ${activeLanes.length} isolated lanes` : queuedPromotionLanes.length ? `${queuedPromotionLanes.length} lanes are verified and waiting promotion` : 'No active lanes'}
              </div>
            </div>
            {promotingLane && (
              <span className={`bugflow-status-pill active_fix`}>{prettyLabel(promotingLane.status)}</span>
            )}
          </div>
          {promotingLane ? (
            <LaneDetail lane={promotingLane} mode="promoting" />
          ) : activeLanes.length > 0 ? (
            <div className="bugflow-lane-stack">
              {activeLanes.map(lane => (
                <LaneDetail key={lane.lane_id} lane={lane} />
              ))}
            </div>
          ) : queuedPromotionLanes.length > 0 ? (
            <div className="bugflow-lane-stack">
              {queuedPromotionLanes.map(lane => (
                <LaneDetail key={lane.lane_id} lane={lane} mode="queued" />
              ))}
            </div>
          ) : (
            <div className="bugflow-empty bugflow-panel-empty">No lanes are active right now.</div>
          )}
        </div>

        <div className="bugflow-panel">
          <div className="bugflow-panel-header">
            <div>
              <div className="bugflow-panel-title">Report Detail</div>
              <div className="bugflow-panel-subtitle">
                {selectedReport ? `${selectedReport.report_id} — ${selectedReport.title || selectedReport.summary}` : 'Select a report from the queue board'}
              </div>
            </div>
          </div>
          {selectedReport ? (
            <ReportDetail report={selectedReport} tab={detailTab} setTab={setDetailTab} />
          ) : (
            <div className="bugflow-empty bugflow-panel-empty">No report selected.</div>
          )}
        </div>
      </div>

      <CollapsibleSection title={`Decisions / Overrides — ${bugflow.decisions.length}`} defaultOpen={bugflow.decisions.length > 0}>
        {bugflow.decisions.length === 0 ? (
          <div className="bugflow-empty bugflow-panel-empty">No confirmed overrides yet.</div>
        ) : (
          <div className="bugflow-decision-list">
            {bugflow.decisions.map(decision => (
              <DecisionCard key={decision.decision_id} decision={decision} />
            ))}
          </div>
        )}
      </CollapsibleSection>

      <CollapsibleSection title={`Artifact Timeline — ${bugflow.artifact_timeline.length} entries`} defaultOpen>
        <ArtifactTimeline sections={bugflow.timeline_sections} />
      </CollapsibleSection>

      <CollapsibleSection title={`Event Log — ${d.events.length} events`}>
        <EventLog events={d.events} />
      </CollapsibleSection>
    </>
  )
}

function SummaryCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="bugflow-summary-card">
      <div className="bugflow-summary-title">{title}</div>
      <div className="bugflow-summary-body">{children}</div>
    </div>
  )
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="bugflow-summary-row">
      <span>{label}</span>
      <span>{value}</span>
    </div>
  )
}

function LaneDetail({
  lane,
  mode = 'active',
}: {
  lane: BugflowLane
  mode?: 'active' | 'queued' | 'promoting'
}) {
  return (
      <div className="bugflow-lane-detail">
      <div className="bugflow-fix-history-meta">
        <span className={`bugflow-status-pill ${laneStatusClass(lane.status)}`}>{lane.lane_id}</span>
        <span>{prettyLabel(lane.status)}</span>
        {lane.execution_state && (
          <span className={`bugflow-status-pill ${executionStateClass(lane.execution_state)}`}>
            {prettyLabel(lane.execution_state)}
          </span>
        )}
        {lane.source_cluster_id && <span>{lane.source_cluster_id}</span>}
      </div>
      <div className="bugflow-kv-list">
        <KV label="Reports" value={lane.report_ids?.join(', ') || '—'} />
        <KV label="Category" value={prettyLabel(lane.category)} />
        <KV label="Phase" value={prettyLabel(lane.current_phase || lane.status)} />
        <KV label="Execution" value={prettyLabel(lane.execution_state) || '—'} />
        <KV label="Last Progress" value={lane.last_progress_at ? relTime(lane.last_progress_at) : '—'} />
        <KV label="Attempt" value={`${lane.lane_attempt ?? '—'}`} />
        <KV label="Promotion" value={prettyLabel(lane.promotion_status || mode)} />
        <KVCodeList label="Lock Scope" values={lane.lock_scope || []} empty="No lock scope recorded." />
        <KVCodeList label="Repo Scope" values={lane.repo_paths || []} empty="No repo scope recorded." />
        <KVCodeList label="Modified Files" values={lane.modified_files || []} empty="No modified files recorded yet." />
        <KVBlock label="Issue Summary" value={lane.issue_summary || 'No issue summary recorded yet.'} />
        <KVBlock label="Latest RCA" value={lane.latest_rca_summary || 'No RCA artifact yet.'} />
        <KVBlock label="Latest Fix" value={lane.latest_fix_summary || 'No fix summary recorded yet.'} />
        <KVBlock label="Latest Verify" value={lane.latest_verify_summary || 'No verify artifact yet.'} />
        <KVBlock label="Latest Regression" value={lane.latest_regression_summary || 'No regression artifact yet.'} />
        {(lane.wait_reason || lane.supersedes_lane_id) && (
          <KVBlock
            label={lane.supersedes_lane_id ? 'Respawned From' : 'Wait Reason'}
            value={lane.supersedes_lane_id || lane.wait_reason || '—'}
          />
        )}
        {(lane.execution_failure_reason || lane.execution_failure_kind) && (
          <KVBlock
            label="Execution Failure"
            value={`${prettyLabel(lane.execution_failure_kind) || 'Failure'}\n\n${lane.execution_failure_reason || 'No failure reason recorded.'}`}
          />
        )}
      </div>
    </div>
  )
}

function ReportDetail({
  report,
  tab,
  setTab,
}: {
  report: BugflowReport
  tab: 'overview' | 'fix-history' | 'timeline'
  setTab: (tab: 'overview' | 'fix-history' | 'timeline') => void
}) {
  const fixAttempts = report.fix_attempts || []
  const observationVerdicts = report.observation_verdicts || []

  return (
    <>
      <div className="bugflow-tab-row">
        <button className={`bugflow-tab ${tab === 'overview' ? 'active' : ''}`} onClick={() => setTab('overview')}>Overview</button>
        <button className={`bugflow-tab ${tab === 'fix-history' ? 'active' : ''}`} onClick={() => setTab('fix-history')}>Fix History</button>
        <button className={`bugflow-tab ${tab === 'timeline' ? 'active' : ''}`} onClick={() => setTab('timeline')}>Timeline</button>
      </div>

      {tab === 'overview' && (
        <div className="bugflow-kv-list">
          <KV label="Report ID" value={report.report_id} />
          <KV label="Category" value={prettyLabel(report.category)} />
          <KV label="Severity" value={prettyLabel(report.severity)} />
          <KV label="Status" value={prettyLabel(report.status)} />
          <KV label="Thread" value={report.thread_status || 'ready'} />
          <KV label="Cluster" value={report.cluster_id || '—'} />
          <KV label="Lane" value={report.lane_id || '—'} />
          <KV label="Decision" value={report.decision_id || report.decision?.decision_id || '—'} />
          <KV label="Promotion" value={prettyLabel(report.promotion_status) || '—'} />
          <KV label="UI Involved" value={report.ui_involved ? 'Yes' : 'No'} />
          <KV label="Evidence Modes" value={(report.evidence_modes || []).join(', ') || '—'} />
          <KV label="Strategy" value={prettyLabel(report.strategy_mode) || prettyLabel(report.cluster?.strategy_mode) || '—'} />
          <KV label="Strategy Round" value={`${report.strategy_round || report.cluster?.strategy_round || '—'}`} />
          <KV label="Terminal Reason" value={prettyLabel(report.terminal_reason_kind) || '—'} />
          <KVBlock label="Strategy Reason" value={stringValue(report.strategy_reason) || stringValue(report.cluster?.strategy_reason) || 'No strategy reason recorded yet.'} />
          <KVBlock label="Stable Failure Family" value={stringValue(report.stable_failure_family) || stringValue(report.cluster?.stable_failure_family) || 'No failure family recorded yet.'} />
          <KVBlock label="Failure Bundle" value={stringValue(report.latest_failure_bundle?.bundle_summary) || 'No failure bundle recorded yet.'} />
          <KVBlock label="Terminal Reason Summary" value={stringValue(report.terminal_reason_summary) || 'No terminal reason summary recorded.'} />
          <KVBlock
            label="Required Files"
            value={(report.strategy_decision?.required_files || []).map(item => `- ${item}`).join('\n') || 'No required files recorded yet.'}
          />
          <KVBlock
            label="Required Checks"
            value={(report.strategy_decision?.required_checks || []).map(item => `- ${item}`).join('\n') || 'No required checks recorded yet.'}
          />
          <KVBlock
            label="Required Evidence Modes"
            value={(report.strategy_required_evidence_modes || report.strategy_decision?.required_evidence_modes || []).map(item => `- ${item}`).join('\n') || 'No strategy-specific evidence modes recorded yet.'}
          />
          <KVBlock
            label="Stable Blockers"
            value={(report.strategy_decision?.stable_blockers || []).map(item => `- ${item.severity || 'unknown'}: ${item.description || 'unspecified'}${item.file ? ` (${item.file})` : ''}`).join('\n') || 'No stable blockers recorded yet.'}
          />
          <KVBlock
            label="Failing Checks"
            value={(report.strategy_decision?.failing_checks || []).map(item => `- ${item.criterion || 'check'}: ${item.result || 'unknown'}${item.detail ? ` — ${item.detail}` : ''}`).join('\n') || 'No failing checks recorded yet.'}
          />
          <KVBlock
            label="Similar Cluster Hints"
            value={(report.strategy_decision?.similar_cluster_hints || report.latest_failure_bundle?.similar_cluster_hints || []).map(item => `- ${item}`).join('\n') || 'No similar-cluster hints recorded yet.'}
          />
          <KVBlock
            label="Merge Recommendation"
            value={stringValue(report.strategy_decision?.merge_recommendation) || 'No merge recommendation recorded.'}
          />
          <KVBlock label="Root Message" value={stringValue(report.root_message_text) || stringValue(report.summary) || 'No root message artifact yet.'} />
          <KVBlock label="Interview Output" value={stringValue(report.interview_output) || 'No interview artifact yet.'} />
          <KVBlock label="Classification" value={stringValue(report.classification_summary) || prettyLabel(report.category)} />
          <KVBlock label="Expected vs Actual" value={buildExpectedActual(report)} />
          <KVBlock label="Affected Area" value={formatAffectedArea(report.affected_area)} />
          <KVBlock label="Validation Summary" value={stringValue(report.validation_summary) || 'No validation summary yet.'} />
          <KVBlock label="Resolution" value={resolutionSummary(report)} />
          <ProofLinkBlock label="Latest Proof" proof={report.latest_proof || null} />
          <ProofLinkBlock label="Terminal Proof" proof={report.terminal_proof || null} />
        </div>
      )}

      {tab === 'fix-history' && (
        <div className="bugflow-fix-history">
          {report.lane && (
            <div className="bugflow-fix-history-meta">
              <span className="bugflow-status-pill active_fix">{report.lane.lane_id}</span>
              <span>{prettyLabel(report.lane.status)}</span>
            </div>
          )}
          {fixAttempts.length > 0 && (
            <div className="timeline">
              {fixAttempts.slice().reverse().map((attempt, index) => (
                <FixAttemptItem
                  key={`${attempt.bug_id}-${index}`}
                  attempt={attempt as FixAttempt}
                  index={fixAttempts.length - index}
                  defaultOpen={index === 0}
                />
              ))}
            </div>
          )}
          {fixAttempts.length === 0 && observationVerdicts.length === 0 && (
            <div className="bugflow-empty bugflow-panel-empty">No fix artifacts yet.</div>
          )}
          {observationVerdicts.length > 0 && (
            <div className="bugflow-history-block">
              <div className="bugflow-mini-title">Observation Verdicts</div>
              <ArtifactTimeline sections={[{ name: 'Observation', entries: observationVerdicts }]} />
            </div>
          )}
          {report.lane && (
            <div className="bugflow-history-block">
              <div className="bugflow-mini-title">Lane Snapshots</div>
              <KVBlock label="Latest RCA" value={report.lane.latest_rca_summary || 'No RCA artifact yet.'} />
              <KVBlock label="Latest Fix" value={report.lane.latest_fix_summary || 'No fix attempt recorded yet.'} />
              <KVBlock label="Latest Verify" value={report.lane.latest_verify_summary || 'No reverify verdict yet.'} />
              <KVBlock label="Latest Regression" value={report.lane.latest_regression_summary || 'No regression verdict yet.'} />
              <KV label="Promotion" value={prettyLabel(report.lane.promotion_status) || '—'} />
            </div>
          )}
        </div>
      )}

      {tab === 'timeline' && (
        report.detail_timeline && report.detail_timeline.length > 0 ? (
          <ArtifactTimeline sections={[{ name: report.report_id, entries: report.detail_timeline }]} />
        ) : (
          <div className="bugflow-empty bugflow-panel-empty">No report-specific timeline entries yet.</div>
        )
      )}
    </>
  )
}

function DecisionCard({ decision }: { decision: BugflowDecision }) {
  return (
    <div className="bugflow-decision-card">
      <div className="bugflow-decision-header">
        <div className="bugflow-decision-title">{decision.title || decision.decision_id}</div>
        <span className={`bugflow-status-pill ${decision.approved ? 'resolved' : 'blocked'}`}>
          {decision.approved ? 'Approved' : 'Pending'}
        </span>
      </div>
      <div className="bugflow-decision-meta">
        <span>{decision.decision_id}</span>
        {decision.report_ids?.length > 0 && <span>{decision.report_ids.join(', ')}</span>}
        <span>{relTime(decision.created_at)}</span>
      </div>
      <KVBlock label="Prior expectation" value={decision.old_expectation || 'Not recorded'} />
      <KVBlock label="New decision" value={decision.new_decision || decision.summary || 'No decision text recorded'} />
    </div>
  )
}

function ArtifactTimeline({ sections }: { sections: BugflowTimelineSection[] }) {
  if (!sections.length) {
    return <div className="bugflow-empty bugflow-panel-empty">No artifact timeline yet.</div>
  }

  return (
    <div className="bugflow-artifact-sections">
      {sections.map(section => (
        <div key={section.name} className="bugflow-artifact-section">
          <div className="tl-section-label">{section.name}</div>
          <div className="timeline">
            {section.entries.map((entry, index) => (
              <ArtifactTimelineItem key={`${section.name}-${entry.key}-${index}`} entry={entry} />
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}

function ArtifactTimelineItem({ entry }: { entry: TimelineEntry }) {
  const [open, setOpen] = useState(false)

  return (
    <div className={`tl-item ${open ? 'expanded' : ''}`} onClick={() => setOpen(!open)}>
      <div className="tl-header">
        <span className={`tl-type ${entry.type}`}>{entry.type}</span>
        {entry.passed === true && <span className="tl-pass pass">PASS</span>}
        {entry.passed === false && <span className="tl-pass fail">FAIL</span>}
        <span className="tl-key">{entry.key}</span>
        <span className="tl-time">{relTime(entry.created_at)}</span>
      </div>
      {open && entry.summary && (
        entry.type === 'dispatch'
          ? <div className="tl-summary"><DispatchDetail raw={entry.summary} /></div>
          : <div className="tl-summary">{entry.summary}</div>
      )}
    </div>
  )
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="bugflow-kv-row">
      <span className="bugflow-kv-label">{label}</span>
      <span className="bugflow-kv-value">{value}</span>
    </div>
  )
}

function KVBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="bugflow-kv-block">
      <div className="bugflow-kv-label">{label}</div>
      <div className="bugflow-kv-body">{value}</div>
    </div>
  )
}

function KVCodeList({ label, values, empty }: { label: string; values: string[]; empty: string }) {
  return (
    <div className="bugflow-kv-block">
      <div className="bugflow-kv-label">{label}</div>
      {values.length === 0 ? (
        <div className="bugflow-kv-body">{empty}</div>
      ) : (
        <div className="dispatch-files">
          {values.map(value => <code key={value}>{value}</code>)}
        </div>
      )}
    </div>
  )
}

function ProofLinkBlock({
  label,
  proof,
}: {
  label: string
  proof: BugflowReport['latest_proof'] | BugflowReport['terminal_proof'] | null
}) {
  return (
    <div className="bugflow-kv-block">
      <div className="bugflow-kv-label">{label}</div>
      {!proof ? (
        <div className="bugflow-kv-body">No proof artifact recorded yet.</div>
      ) : (
        <div className="bugflow-kv-body">
          {proof.bundle_url ? <a href={proof.bundle_url} target="_blank" rel="noreferrer">Open proof bundle</a> : 'Proof bundle unavailable'}
          {proof.primary_artifact_url && (
            <>
              {' · '}
              <a href={proof.primary_artifact_url} target="_blank" rel="noreferrer">Key artifact</a>
            </>
          )}
          {proof.bundle?.summary && <div>{proof.bundle.summary}</div>}
        </div>
      )}
    </div>
  )
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function formatAffectedArea(value: unknown): string {
  if (Array.isArray(value)) return value.join(', ')
  return typeof value === 'string' ? value : 'No affected area recorded.'
}

function buildExpectedActual(report: BugflowReport): string {
  const expected = stringValue(report.expected_behavior)
  const actual = stringValue(report.actual_behavior)
  if (!expected && !actual) return 'No expected/actual artifact yet.'
  return `Expected: ${expected || '—'}\n\nActual: ${actual || '—'}`
}

function resolutionSummary(report: BugflowReport): string {
  const parts = [report.status, report.current_step, report.validation_summary]
    .map(part => `${part || ''}`.trim())
    .filter(Boolean)
  return parts.length > 0 ? parts.join('\n\n') : 'No resolution artifact yet.'
}

function formatRepoPushes(repos: Array<{ repo_name: string; last_pushed_commit: string; last_push_at: string | null }>): string {
  if (!repos.length) return 'None'
  const recent = repos
    .filter(repo => repo.last_pushed_commit || repo.last_push_at)
    .slice(0, 2)
    .map(repo => `${repo.repo_name}:${repo.last_pushed_commit || relTime(repo.last_push_at)}`)
  return recent.length > 0 ? recent.join(', ') : 'No pushes yet'
}

function verdictLabel(entry: TimelineEntry | undefined): string {
  if (!entry) return 'None'
  const outcome = entry.passed === true ? 'PASS' : entry.passed === false ? 'FAIL' : 'INFO'
  return `${outcome} • ${relTime(entry.created_at)}`
}

function findLatestEntry(entries: TimelineEntry[], type: string): TimelineEntry | undefined {
  return entries.find(entry => entry.type === type)
}

function prettyLabel(value: string | null | undefined): string {
  const text = `${value || ''}`.trim()
  if (!text) return '—'
  return text
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, match => match.toUpperCase())
}

function executionStateClass(value: string | null | undefined): string {
  const text = `${value || ''}`.trim().toLowerCase()
  if (text === 'recovering') return 'recovering'
  if (text === 'strategy_pending') return 'strategy_pending'
  if (text === 'stalled') return 'stalled'
  return 'queued'
}

function reportLane(status: string | null | undefined): string {
  const value = `${status || ''}`.trim().toLowerCase()
  if (value.startsWith('resolved') || value === 'complete' || value === 'closed') return 'resolved'
  if (value === 'blocked' || value === 'cancelled') return 'blocked'
  if (value === 'pending_retriage') return 'pending_retriage'
  if (['active_fix', 'active', 'fixing', 'triage', 'rca', 'reverify', 'regression', 'pushing'].includes(value)) return 'active_fix'
  if (['awaiting_confirmation', 'clarification_pending', 'waiting_for_confirmation'].includes(value)) return 'awaiting_confirmation'
  if (['intake_pending', 'classification_pending', 'validation_pending'].includes(value)) return 'intake_pending'
  return 'queued'
}

function clusterLane(status: string | null | undefined): string {
  const value = `${status || ''}`.trim().toLowerCase()
  if (!value) return 'queued'
  if (value.startsWith('resolved') || ['promoted', 'superseded'].includes(value)) return 'resolved'
  if (['blocked', 'cancelled'].includes(value)) return 'blocked'
  if (['verified_pending_promotion', 'promoting'].includes(value)) return 'active_fix'
  if (['active_fix', 'active_verify', 'fixing', 'reverify', 'regression', 'pushing'].includes(value)) return 'active_fix'
  if (['planned', 'queued'].includes(value)) return 'queued'
  return 'other'
}

function laneStatusClass(status: string | null | undefined): string {
  const value = `${status || ''}`.trim().toLowerCase()
  if (['verified_pending_promotion', 'promoting'].includes(value)) return 'active_fix'
  if (['promoted', 'superseded'].includes(value) || value.startsWith('resolved')) return 'resolved'
  return reportLane(status)
}

function laneWeight(lane: string): number {
  const index = BOARD_COLUMNS.findIndex(column => column.lane === lane)
  return index === -1 ? BOARD_COLUMNS.length : index
}
