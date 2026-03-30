import { useState } from 'react'

export function DispatchDetail({ raw }: { raw: string }) {
  let data: any
  try { data = JSON.parse(raw) } catch { return <div className="tl-summary">{raw}</div> }

  return (
    <div className="dispatch-detail">
      <div className="dispatch-meta">
        <span>Source: <strong>{data.source}</strong></span>
        <span>Attempt: <strong>{data.attempt_number}</strong></span>
        <span>Issues: <strong>{data.total_issues}</strong></span>
        <span>Rounds: <strong>{data.total_rounds}</strong></span>
      </div>
      {data.schedule?.map((r: any) => (
        <div key={r.round} className="dispatch-round">
          <div className="dispatch-round-title">Round {r.round} — {r.group_ids.join(', ')}</div>
        </div>
      ))}
      {data.groups?.map((g: any) => (
        <div key={g.group_id} className="dispatch-group">
          <div className="dispatch-group-header">
            <strong>{g.group_id}</strong>
            <span className={`dispatch-severity ${g.severity}`}>{g.severity}</span>
            <span>{g.issue_count} issue{g.issue_count !== 1 ? 's' : ''}</span>
          </div>
          <div className="dispatch-root-cause">{g.likely_root_cause}</div>
          {g.rca && (
            <div className="dispatch-rca">
              <div><strong>RCA ({g.rca.confidence}):</strong> {g.rca.hypothesis}</div>
              {g.rca.evidence.length > 0 && (
                <ul>{g.rca.evidence.map((e: string, i: number) => <li key={i}>{e}</li>)}</ul>
              )}
              <div><strong>Fix approach:</strong> {g.rca.proposed_approach}</div>
              <div className="dispatch-files">
                {g.rca.affected_files.map((f: string) => <code key={f}>{f}</code>)}
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}

export function FixAttemptsDetail({ raw }: { raw: string }) {
  const chunks = raw.split(/\n\n+/).filter(Boolean)
  const attempts: any[] = []
  for (const chunk of chunks) {
    try { attempts.push(JSON.parse(chunk)) } catch { /* skip */ }
  }

  if (!attempts.length) return <div className="tl-summary">{raw}</div>

  // Most recent first
  const reversed = [...attempts].reverse()

  return (
    <div className="timeline">
      {reversed.map((a, i) => <FixAttemptItem key={i} attempt={a} index={attempts.length - i} />)}
    </div>
  )
}

function FixAttemptItem({ attempt: a, index }: { attempt: any; index: number }) {
  const [open, setOpen] = useState(false)
  const passed = a.re_verify_result === 'PASS'
  const pending = !a.re_verify_result

  return (
    <div className={`tl-item ${open ? 'expanded' : ''}`} onClick={() => setOpen(!open)}>
      <div className="tl-header">
        <span className={`tl-type ${passed ? 'verify' : 'fix'}`}>{a.bug_id}</span>
        {!pending && (
          <span className={`tl-pass ${passed ? 'pass' : 'fail'}`}>
            {a.re_verify_result}
          </span>
        )}
        {a.group_id && <span className="tl-key">{a.group_id}</span>}
        <span className="tl-time">attempt #{index}</span>
      </div>
      {open && (
        <div className="tl-summary">
          <div>{a.description}</div>
          {a.root_cause && <div><strong>Root cause:</strong> {a.root_cause}</div>}
          {a.fix_applied && <div><strong>Fix applied:</strong> {a.fix_applied}</div>}
          {a.source_verdict && <div><strong>Source:</strong> {a.source_verdict}</div>}
          {a.files_modified?.length > 0 && (
            <div className="dispatch-files" style={{ marginTop: 6 }}>
              {a.files_modified.map((f: string) => <code key={f}>{f}</code>)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
