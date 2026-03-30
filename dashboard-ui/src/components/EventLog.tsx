import { useState } from 'react'
import type { EventEntry } from '../types'
import { relTime } from '../utils'

export function EventLog({ events }: { events: EventEntry[] }) {
  const [open, setOpen] = useState(false)

  if (!events.length) return null

  return (
    <div className="section">
      <div className="events-toggle" onClick={() => setOpen(!open)}>
        {open ? '▼' : '▶'} Event Log ({events.length})
      </div>
      {open && (
        <div className="events-table open">
          {events.map((e, i) => <EventRow key={i} event={e} />)}
        </div>
      )}
    </div>
  )
}

function EventRow({ event }: { event: EventEntry }) {
  const [expanded, setExpanded] = useState(false)
  const needsTruncation = event.content.length > 80

  return (
    <div
      className={`ev-row ${expanded ? 'expanded' : ''}`}
      onClick={needsTruncation ? () => setExpanded(!expanded) : undefined}
      style={needsTruncation ? { cursor: 'pointer' } : undefined}
    >
      <span>{relTime(event.created_at)}</span>
      <span>{event.event_type}</span>
      <span>{event.source}</span>
      <span>{expanded ? event.content : event.content.slice(0, 80)}{!expanded && needsTruncation ? '…' : ''}</span>
    </div>
  )
}
