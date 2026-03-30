import { useState } from 'react'
import type { TimelineEntry } from '../types'
import { relTime } from '../utils'

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return null

  return (
    <div className="section">
      <div className="section-title">Verify / Fix Timeline</div>
      <div className="timeline">
        {entries.map((t, i) => <TimelineItem key={`${t.key}-${i}`} entry={t} />)}
      </div>
    </div>
  )
}

function TimelineItem({ entry }: { entry: TimelineEntry }) {
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
        <div className="tl-summary">{entry.summary}</div>
      )}
    </div>
  )
}
