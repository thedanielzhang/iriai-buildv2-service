export type HealthState =
  | 'idle'
  | 'running'
  | 'fix-loop'
  | 'degraded'
  | 'stuck'
  | 'complete'
  | 'awaiting-user'
  | 'blocked'
  | 'complete-ish'
export type PhaseMode = 'planning' | 'implementing' | 'fix-loop' | 'gates' | 'complete'

export interface FeatureData {
  id: string
  name: string
  phase: string
  workflow_name: string
  updated_at: string
  last_activity_at: string | null
  dag: DagInfo | null
  groups: Group[]
  gates: Record<string, boolean>
  active_gate: string | null
  active_gate_steps: TimelineEntry[]
  timeline: TimelineEntry[]
  workstreams: Workstream[]
  dag_repair?: DagRepairMetrics | null
  public_exhibit?: PublicExhibit | null
  events: EventEntry[]
  active_agent: string | null
  source_feature_id?: string | null
  dashboard_url?: string | null
  bugflow?: BugflowData | null
}

export interface DagInfo {
  total_tasks: number
  total_groups: number
  execution_order: string[][]
}

export interface Group {
  index: number
  task_count: number
  completed_count: number
  status: 'complete' | 'active' | 'pending'
  tasks: Task[]
  verify_steps: VerifyStep[]
  fix_steps: TimelineEntry[]
  is_enhancement?: boolean
}

export interface Task {
  id: string
  name: string
  status: 'complete' | 'in_progress' | 'pending'
  summary: string
  description: string
  subfeature_id: string
  repo_path: string
  file_scope: FileScope[]
  acceptance_criteria: string[]
  dependencies?: string[]
}

export interface FileScope {
  path: string
  action: string
}

export interface VerifyStep {
  key: string
  type: string
  passed: boolean | null
  summary: string
  created_at: string
}

export interface TimelineEntry {
  key: string
  type: string
  passed: boolean | null
  summary: string
  created_at: string
}

export interface DagRepairMetrics {
  active_group_index: number | null
  latest_checkpoint_group: number | null
  current_cycle: DagRepairCycle | null
  cycles: DagRepairCycle[]
  summary: {
    completed_groups: number
    total_groups: number
    active_group_elapsed_seconds: number | null
    retry_count_for_active_group: number
    expanded_verify_runs: number
    fix_groups_scheduled: number
    fix_groups_applied: number
    final_preflight_failures: number
    sanitizer_ignored_paths: number
    sanitizer_rewritten_paths: number
    sanitizer_invalid_paths: number
  }
}

export interface DagRepairCycle {
  group_idx: number
  retry: string
  started_at: string | null
  ended_at: string | null
  duration_seconds: number | null
  status: 'running' | 'passed' | 'failed' | 'waiting'
  stage_durations: Record<string, number>
  lens_count: number
  rca_group_count: number
  fixable_group_count: number
  scheduled_round_count: number
  applied_fix_count: number
  contradiction_count: number
  rejected_contradiction_count: number
  final_blocker_summary: string
}

export interface PublicExhibit {
  public_summary: PublicSummary | null
  dag_exhibit: DagExhibit | null
  agent_exhibit: AgentExhibit | null
  current_work?: CurrentWork | null
  artifact_exhibit: ArtifactExhibit | null
  workstream_exhibit: WorkstreamExhibit | null
  milestone_feed: PublicMilestone[]
  operations?: OperationsSnapshot | null
}

export interface PublicSummary {
  title: string
  tagline: string
  description: string
  phase_label: string
  status_label: string
  progress_narrative: string
  current_focus: string
  next_checkpoint: string
  health: string
  percent_complete: number
  completed_groups: number
  total_groups: number
  completed_tasks: number
  total_tasks: number
  updated_at: string | null
  source: string
  provenance?: Record<string, unknown>
}

export interface DagExhibit {
  narrative: string
  total_groups: number
  total_tasks: number
  completed_groups: number
  active_group: {
    index: number
    task_count: number
    completed_count: number
    status: string
  } | null
  next_groups: Array<{
    index: number
    task_count: number
    status: string
  }>
  repair?: DagRepairMetrics | null
  source: string
}

export interface AgentActivity {
  name: string
  role: string
  runtime: string
  status: string
  started_at?: string | null
  ended_at?: string | null
  summary?: string
  task_id?: string | null
  group_idx?: number | null
  duration_seconds?: number | null
  prompt_preview?: string
  output_preview?: string
  related_artifact_keys?: string[]
  related_files?: string[]
}

export interface AgentExhibit {
  headline: string
  active_agents: AgentActivity[]
  recent_agents: AgentActivity[]
  round_summaries: Array<{
    key: string
    created_at: string
    summary: string
  }>
  current_repair_cycle?: DagRepairCycle | null
}

export interface ArtifactCard {
  artifact_id?: string
  key: string
  title: string
  family: string
  summary: string
  created_at: string
  status: string
  public_safe: boolean
  source: string
  content_url?: string
  render_mode?: 'markdown' | 'text' | 'html' | 'json' | 'image' | 'video' | string
  sha256?: string
  safety_status?: string
  url?: string
  href?: string
  review_url?: string
  provenance?: Record<string, unknown>
}

export interface ArtifactExhibit {
  cards: ArtifactCard[]
  total_count: number
  generated: boolean
}

export interface CurrentWork {
  active_group: {
    index: number
    task_count: number
    completed_count: number
    status: string
  } | null
  active_tasks: Array<{
    id: string
    name: string
    status: string
    summary: string
    repo_path: string
    subfeature_id: string
    acceptance_criteria: string[]
    file_scope: FileScope[]
    dependencies?: string[]
  }>
  active_agents: AgentActivity[]
  recent_outcomes: Array<{
    key: string
    type: string
    passed: boolean | null
    summary: string
    created_at: string
  }>
  next_checkpoint: string
}

export interface WorkstreamExhibit {
  summary: string
  source: string
  workstreams: Array<{
    id: string
    name: string
    summary: string
    status: string
    completed_tasks: number
    total_tasks: number
    subfeature_slugs: string[]
    depends_on: string[]
  }>
}

export interface PublicMilestone {
  title: string
  summary: string
  kind: string
  created_at: string
  source: string
}

export interface OperationsSnapshot {
  dag_repair?: DagRepairMetrics | null
  gates: Record<string, boolean>
  active_gate: string | null
  timeline_count: number
  event_count: number
  artifact_count: number
}

export interface BugflowData {
  source_feature_id: string | null
  dashboard_url: string | null
  dashboard_message_ts?: string | null
  health: HealthState
  status_text: string
  active_step: string
  active_report_id: string
  active_cluster_id: string
  active_lane_ids: string[]
  verified_pending_promotion_ids: string[]
  promoting_lane_id: string
  promotion_status_text: string
  active_round: number | null
  total_rounds: number | null
  active_attempt: number | null
  counts: Record<string, number>
  pending_retriage_ids: string[]
  blocked_ids: string[]
  recovering_lane_ids: string[]
  stalled_lane_ids: string[]
  proof_capture_retry_lane_ids: string[]
  strategy_pending_cluster_ids: string[]
  last_transition_at: string | null
  reports: BugflowReport[]
  lanes: BugflowLane[]
  clusters: BugflowCluster[]
  active_lanes: BugflowLane[]
  verified_pending_promotion: BugflowLane[]
  promoting_lane: BugflowLane | null
  decisions: BugflowDecision[]
  repo_status: RepoStatusSummary | null
  timeline_sections: BugflowTimelineSection[]
  artifact_timeline: TimelineEntry[]
}

export interface BugflowProof {
  key: string
  report_id: string
  stage: string
  bundle_url: string
  primary_artifact_url: string
  created_at: string
  bundle?: {
    summary?: string
    ui_involved?: boolean
    evidence_modes?: string[]
    environment_notes?: string
    principal_context?: string
    state_change?: boolean
  }
}

export interface BugflowIssue {
  severity?: string
  description?: string
  file?: string
  line?: number
}

export interface BugflowCheck {
  criterion?: string
  result?: string
  detail?: string
}

export interface BugflowStrategyDecision {
  key?: string
  strategy_mode: string
  reasoning: string
  stable_failure_family?: string
  bundle_summary?: string
  stable_blockers?: BugflowIssue[]
  new_blockers?: BugflowIssue[]
  failing_checks?: BugflowCheck[]
  scope_expansion?: string[]
  required_files?: string[]
  required_checks?: string[]
  required_evidence_modes?: string[]
  similar_cluster_hints?: string[]
  why_not_ordinary_retry?: string
  merge_recommendation?: string
}

export interface BugflowFailureBundle {
  key?: string
  strategy_round?: number
  failure_kind?: string
  failure_reason?: string
  bundle_summary?: string
  stable_failure_family?: string
  history_summary?: string
  stable_blockers?: BugflowIssue[]
  new_blockers?: BugflowIssue[]
  failing_checks?: BugflowCheck[]
  required_evidence_modes?: string[]
  proof_keys?: string[]
  similar_cluster_hints?: string[]
  detailed_attempts?: Array<Record<string, unknown>>
  [key: string]: unknown
}

export interface BugflowReport {
  report_id: string
  root_message_ts: string
  thread_ts: string
  title: string
  category: string
  severity: string
  status: string
  cluster_id: string | null
  lane_id?: string | null
  current_step: string
  summary: string
  validation_summary: string
  decision_id: string | null
  promotion_status?: string
  pending_retriage_for_lane?: string | null
  updated_at: string
  created_at?: string | null
  thread_status?: string
  ui_involved?: boolean
  evidence_modes?: string[]
  latest_proof_key?: string
  terminal_proof_key?: string
  terminal_proof_summary?: string
  strategy_mode?: string
  strategy_reason?: string
  strategy_round?: number
  stable_failure_family?: string
  strategy_decision_key?: string
  latest_failure_bundle_key?: string
  latest_strategy_notice_key?: string
  strategy_required_evidence_modes?: string[]
  terminal_reason_kind?: string
  terminal_reason_summary?: string
  root_message_text?: string
  interview_output?: string
  classification_summary?: string
  expected_behavior?: string
  actual_behavior?: string
  affected_area?: string | string[]
  detail_timeline?: TimelineEntry[]
  fix_attempts?: FixAttempt[]
  observation_verdicts?: TimelineEntry[]
  decision?: BugflowDecision | null
  cluster?: BugflowCluster | null
  lane?: BugflowLane | null
  latest_proof?: BugflowProof | null
  terminal_proof?: BugflowProof | null
  strategy_decision?: BugflowStrategyDecision | null
  latest_failure_bundle?: BugflowFailureBundle | null
  [key: string]: unknown
}

export interface BugflowCluster {
  cluster_id: string
  report_ids: string[]
  lane_id?: string | null
  status: string
  likely_root_cause: string
  affected_files: string[]
  repo_paths: string[]
  schedule_round: number | null
  schedule_total_rounds: number | null
  attempt_number: number | null
  latest_rca_key: string | null
  latest_dispatch_key: string | null
  latest_reverify_key: string | null
  latest_regression_key: string | null
  last_push_at: string | null
  current_phase?: string
  wait_reason?: string
  latest_rca_summary?: string
  latest_fix_summary?: string
  latest_reverify_summary?: string
  latest_regression_summary?: string
  latest_reverify_passed?: boolean | null
  latest_regression_passed?: boolean | null
  strategy_mode?: string
  strategy_reason?: string
  strategy_round?: number
  stable_failure_family?: string
  strategy_decision_key?: string
  stable_bundle_key?: string
  similar_cluster_ids?: string[]
  strategy_status?: string
  strategy_started_at?: string | null
  strategy_decided_at?: string | null
  strategy_applied_at?: string | null
  strategy_decision?: BugflowStrategyDecision | null
  stable_bundle?: BugflowFailureBundle | null
  round_plan?: string[]
  last_push_result?: string
  updated_at?: string | null
  created_at?: string | null
  [key: string]: unknown
}

export interface BugflowLane {
  lane_id: string
  lane_attempt: number | null
  report_ids: string[]
  category: string
  source_cluster_id: string | null
  status: string
  current_phase?: string
  lock_scope: string[]
  repo_paths: string[]
  workspace_root?: string
  branch_names_by_repo?: Record<string, string>
  base_main_commits_by_repo?: Record<string, string>
  latest_rca_keys?: string[]
  latest_verify_keys?: string[]
  latest_regression_keys?: string[]
  latest_dispatch_key?: string | null
  latest_rca_summary?: string
  latest_fix_summary?: string
  latest_verify_summary?: string
  latest_regression_summary?: string
  issue_summary?: string
  modified_files?: string[]
  verification_actor?: string
  promotion_status?: string
  promotion_attempt?: number | null
  promotion_proof_capture_attempt?: number | null
  supersedes_lane_id?: string | null
  wait_reason?: string
  execution_state?: string
  execution_nonce?: string | null
  execution_kind?: string | null
  execution_owner?: string | null
  execution_started_at?: string | null
  last_progress_at?: string | null
  execution_failure_kind?: string | null
  execution_failure_reason?: string | null
  updated_at?: string | null
  created_at?: string | null
  [key: string]: unknown
}

export interface BugflowDecision {
  decision_id: string
  report_ids: string[]
  title: string
  old_expectation: string
  new_decision: string
  approved: boolean
  created_at: string
  summary: string
  source_key?: string
}

export interface RepoStatusSummary {
  branch_name: string
  repos: RepoStatus[]
  has_unpushed_verified_work: boolean
  unpromoted_lane_ids?: string[]
}

export interface RepoStatus {
  repo_path: string
  repo_name: string
  last_pushed_commit: string
  status: string
  touched: boolean
  last_push_at: string | null
}

export interface BugflowTimelineSection {
  name: string
  entries: TimelineEntry[]
}

export interface FixAttempt {
  bug_id: string
  group_id?: string
  source_verdict?: string
  description?: string
  root_cause?: string
  fix_applied?: string
  files_modified?: string[]
  re_verify_result?: string
  attempt_number?: number
}

export interface Workstream {
  id: string
  name: string
  subfeature_slugs: string[]
  depends_on: string[]
  total_tasks: number
  completed_tasks: number
}

export interface EventEntry {
  event_type: string
  source: string
  content: string
  created_at: string
}

export interface SearchResult {
  id: string
  name: string
  phase: string
  updated_at: string
}
