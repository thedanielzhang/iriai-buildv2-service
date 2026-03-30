import { useState } from 'react'
import type { TimelineEntry } from '../types'
import { relTime } from '../utils'
import { DispatchDetail, FixAttemptsDetail } from './BugDetail'

const VERIFY_TYPES = new Set(['verify', 're-verify'])

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return null

  const verifyEntries = entries.filter(e => VERIFY_TYPES.has(e.type))
  const fixEntries = entries.filter(e => !VERIFY_TYPES.has(e.type))

  return (
    <>
      {verifyEntries.length > 0 && (
        <div className="section">
          <div className="section-title">Verify Timeline</div>
          <div className="timeline">
            {verifyEntries.map((t, i) => <TimelineItem key={`${t.key}-${i}`} entry={t} />)}
          </div>
        </div>
      )}
      {fixEntries.length > 0 && (
        <div className="section">
          <div className="section-title">Fix Timeline</div>
          <div className="timeline">
            {fixEntries.map((t, i) => <TimelineItem key={`${t.key}-${i}`} entry={t} />)}
          </div>
        </div>
      )}
    </>
  )
}

function TimelineItem({ entry }: { entry: TimelineEntry }) {
  const [open, setOpen] = useState(false)

  const renderDetail = () => {
    if (!entry.summary) return null
    if (entry.type === 'dispatch') return <div className="tl-summary"><DispatchDetail raw={entry.summary} /></div>
    if (entry.type === 'fix-attempts') return <div className="tl-summary"><FixAttemptsDetail raw={entry.summary} /></div>
    return <div className="tl-summary">{entry.summary}</div>
  }

  return (
    <div className={`tl-item ${open ? 'expanded' : ''}`} onClick={() => setOpen(!open)}>
      <div className="tl-header">
        <span className={`tl-type ${entry.type}`}>{entry.type}</span>
        {entry.passed === true && <span className="tl-pass pass">PASS</span>}
        {entry.passed === false && <span className="tl-pass fail">FAIL</span>}
        <span className="tl-key">{entry.key}</span>
        <span className="tl-time">{relTime(entry.created_at)}</span>
      </div>
      {open && renderDetail()}
    </div>
  )
}
