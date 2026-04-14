import { useState, useRef, useEffect } from 'react'
import { useStore } from '../store/useStore'
import { phaseCls, getHealthState, healthColor, minutesSince } from '../utils'
import type { SearchResult, FeatureData } from '../types'

export function TopBar() {
  const tracked = useStore(s => s.tracked)
  const data = useStore(s => s.data)
  const view = useStore(s => s.view)
  const setView = useStore(s => s.setView)
  const addFeature = useStore(s => s.addFeature)
  const removeFeature = useStore(s => s.removeFeature)

  const [modalOpen, setModalOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [results, setResults] = useState<SearchResult[]>([])
  const inputRef = useRef<HTMLInputElement>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  useEffect(() => {
    if (modalOpen) inputRef.current?.focus()
  }, [modalOpen])

  useEffect(() => {
    clearTimeout(timerRef.current)
    if (!query.trim()) { setResults([]); return }
    timerRef.current = setTimeout(async () => {
      try {
        const r = await fetch(`/api/search?q=${encodeURIComponent(query)}`)
        if (r.ok) setResults(await r.json())
      } catch { /* ignore */ }
    }, 250)
  }, [query])

  const handleAdd = (id: string) => {
    addFeature(id)
    setModalOpen(false)
    setQuery('')
    setView(id)
  }

  return (
    <>
      <div className="topbar">
        <div className="topbar-brand" onClick={() => setView('overview')}>
          IRIAI BUILD
        </div>
        <div className="topbar-tabs">
          {tracked.map(id => {
            const d = data[id] as FeatureData | undefined
            const health = d ? getHealthState(d) : 'idle'
            const staleMin = health === 'stuck' ? Math.round(minutesSince(d?.last_activity_at)) : 0
            return (
              <div
                key={id}
                className={`tab ${view === id ? 'active' : ''}`}
                onClick={() => setView(id)}
              >
                <span className="phase-dot" style={{ background: healthColor(health) }} />
                <span>{d ? (d.name.length > 24 ? d.name.slice(0, 22) + '..' : d.name) : id}</span>
                {health === 'stuck' && staleMin > 0 && (
                  <span
                    className="tab-stale-badge"
                    style={{
                      fontSize: 9,
                      fontFamily: 'var(--mono)',
                      color: 'var(--red)',
                      background: 'rgba(239,68,68,0.12)',
                      padding: '0 4px',
                      borderRadius: 3,
                      marginLeft: 4,
                      lineHeight: '16px',
                    }}
                  >
                    {staleMin >= 60 ? `${Math.floor(staleMin / 60)}h` : `${staleMin}m`}
                  </span>
                )}
                <span className="close-tab" onClick={e => { e.stopPropagation(); removeFeature(id) }}>
                  &times;
                </span>
              </div>
            )
          })}
        </div>
        <div
          className={`tab ${view === 'terminal' ? 'active' : ''}`}
          onClick={() => setView('terminal')}
          style={{ flexShrink: 0 }}
        >
          <span style={{ fontFamily: 'var(--mono)', fontSize: 11 }}>{'>_'}</span>
          <span>Terminal</span>
        </div>
        <div className="add-btn" onClick={() => setModalOpen(true)} title="Add feature">+</div>
      </div>

      {modalOpen && (
        <div className="modal-overlay" onClick={e => { if (e.target === e.currentTarget) setModalOpen(false) }}>
          <div className="modal">
            <input
              ref={inputRef}
              placeholder="Enter feature ID or search by name..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === 'Escape') setModalOpen(false)
                if (e.key === 'Enter' && query.trim()) handleAdd(query.trim())
              }}
            />
            <div className="modal-results">
              {results.map(f => (
                <div key={f.id} className="modal-result" onClick={() => handleAdd(f.id)}>
                  <span className="mr-id">{f.id}</span>
                  <span className="mr-name">{f.name}</span>
                  <span className={`phase-badge ${phaseCls(f.phase)}`}>{f.phase}</span>
                </div>
              ))}
              {query && !results.length && (
                <div style={{ padding: 16, color: 'var(--text-2)', textAlign: 'center' }}>
                  Press Enter to add "{query}" directly
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </>
  )
}
