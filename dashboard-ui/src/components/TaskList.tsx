import { useState } from 'react'
import type { Task } from '../types'

export function TaskList({ tasks }: { tasks: Task[]; groupIndex: number }) {
  return (
    <div className="task-list">
      {tasks.map(t => <TaskItem key={t.id} task={t} />)}
    </div>
  )
}

function TaskItem({ task }: { task: Task }) {
  const [open, setOpen] = useState(false)
  const icon = task.status === 'complete' ? '✓' : task.status === 'in_progress' ? '◈' : '○'

  return (
    <div className={`task-item ${open ? 'expanded' : ''}`}>
      <div className="task-row" onClick={() => setOpen(!open)}>
        <div className={`task-icon ${task.status}`}>{icon}</div>
        <span className="task-id">{task.id}</span>
        <div className="task-name">{task.name}</div>
        {task.subfeature_id && <span className="task-sf">{task.subfeature_id}</span>}
        <span className="task-expand">▶</span>
      </div>
      {open && (
        <div className="task-detail">
          {task.summary && (
            <div className="task-detail-summary">{task.summary}</div>
          )}
          {task.description && (
            <>
              <h4>Description</h4>
              <div>{task.description}</div>
            </>
          )}
          {task.file_scope?.length > 0 && (
            <>
              <h4>File Scope</h4>
              {task.file_scope.map((f, i) => (
                <div key={i} className="file-item">
                  <span className={`file-action ${f.action}`}>{f.action}</span>
                  {f.path}
                </div>
              ))}
            </>
          )}
          {task.acceptance_criteria?.length > 0 && (
            <>
              <h4>Acceptance Criteria</h4>
              {task.acceptance_criteria.map((ac, i) => (
                <div key={i} className="ac-item">{ac}</div>
              ))}
            </>
          )}
          {task.repo_path && (
            <>
              <h4>Repo</h4>
              <div className="file-item">{task.repo_path}</div>
            </>
          )}
        </div>
      )}
    </div>
  )
}
