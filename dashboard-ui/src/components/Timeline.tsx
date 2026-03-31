import { useState } from 'react'
import type { TimelineEntry } from '../types'
import { relTime } from '../utils'
import { DispatchDetail, FixAttemptItem, parseFixAttempts } from './BugDetail'

const VERIFY_TYPES = new Set(['verify', 're-verify'])

export function Timeline({ entries }: { entries: TimelineEntry[] }) {
  if (!entries.length) return null

  const verifyEntries = entries.filter(e => VERIFY_TYPES.has(e.type))
  const fixEntries = entries.filter(e => !VERIFY_TYPES.has(e.type) && e.type !== 'fix-attempts')

  // Flatten fix-attempts into individual items
  const fixAttemptsEntry = entries.find(e => e.type === 'fix-attempts')
  const parsedAttempts = fixAttemptsEntry ? parseFixAttempts(fixAttemptsEntry.summary) : []

  return (
    <>
      {verifyEntries.length > 0 && (
        <>
          <div className="tl-section-label">Verify</div>
          <div className="timeline">
            {verifyEntries.map((t, i) => <TimelineItem key={`${t.key}-${i}`} entry={t} />)}
          </div>
        </>
      )}
      {(fixEntries.length > 0 || parsedAttempts.length > 0) && (
        <>
          <div className="tl-section-label" style={{ marginTop: verifyEntries.length > 0 ? 12 : 0 }}>Fix</div>
          <div className="timeline">
            {fixEntries.map((t, i) => <TimelineItem key={`${t.key}-${i}`} entry={t} />)}
            {parsedAttempts.length > 0 && (
              <div id="fix-attempts-section" className="fix-attempts-anchor">
                <div className="tl-section-label">Fix Attempts — {parsedAttempts.length} total</div>
                {parsedAttempts.slice().reverse().map((a, i) => (
                  <FixAttemptItem key={`attempt-${i}`} attempt={a} index={parsedAttempts.length - i} />
                ))}
              </div>
            )}
          </div>
        </>
      )}
    </>
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
        entry.type === 'dispatch'
          ? <div className="tl-summary"><DispatchDetail raw={entry.summary} /></div>
          : <div className="tl-summary">{entry.summary}</div>
      )}
    </div>
  )
}
