export interface FeatureData {
  id: string
  name: string
  phase: string
  workflow_name: string
  updated_at: string
  dag: DagInfo | null
  groups: Group[]
  gates: Record<string, boolean>
  timeline: TimelineEntry[]
  workstreams: Workstream[]
  events: EventEntry[]
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
}

export interface FileScope {
  path: string
  action: string
}

export interface VerifyStep {
  key: string
  type: string
  passed: boolean
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
