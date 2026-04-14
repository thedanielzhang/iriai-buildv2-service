import { useEffect, useRef, useState, useCallback, useMemo } from 'react'

interface BridgeStatus {
  running: boolean
  pid: number | null
  exit_code: number | null
  line_count: number
  buffer_size: number
}

export function Terminal() {
  const [lines, setLines] = useState<string[]>([])
  const [status, setStatus] = useState<BridgeStatus | null>(null)
  const [notConfigured, setNotConfigured] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const autoScrollRef = useRef(true)
  const cursorRef = useRef(0)
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Visibility-based poll throttling
  useEffect(() => {
    let active = true

    async function poll() {
      try {
        const r = await fetch(`/api/bridge/logs?after=${cursorRef.current}`)
        if (r.status === 404) { setNotConfigured(true); return }
        if (!r.ok) return
        const data = await r.json()
        cursorRef.current = data.cursor
        if (data.lines.length > 0 && active) {
          setLines(prev => {
            const next = [...prev, ...data.lines]
            return next.length > 5000 ? next.slice(-5000) : next
          })
        }
      } catch { /* ignore */ }
    }

    function startInterval(ms: number) {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current)
      }
      pollIntervalRef.current = setInterval(poll, ms)
    }

    function handleVisibilityChange() {
      if (document.hidden) {
        startInterval(5000)
      } else {
        poll() // immediate poll on return
        startInterval(1000)
      }
    }

    poll()
    startInterval(document.hidden ? 5000 : 1000)
    document.addEventListener('visibilitychange', handleVisibilityChange)

    return () => {
      active = false
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current)
      }
      document.removeEventListener('visibilitychange', handleVisibilityChange)
    }
  }, [])

  // Auto-scroll
  useEffect(() => {
    if (autoScrollRef.current && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [lines.length])

  const handleScroll = useCallback(() => {
    const el = containerRef.current
    if (!el) return
    autoScrollRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40
  }, [])

  // Poll status
  useEffect(() => {
    const poll = () => fetch('/api/bridge/status')
      .then(r => {
        if (r.status === 404) { setNotConfigured(true); return null }
        return r.ok ? r.json() : null
      })
      .then(d => { if (d) setStatus(d) })
      .catch(() => {})
    poll()
    const id = setInterval(poll, 5000)
    return () => clearInterval(id)
  }, [])

  const handleRestart = async () => {
    setRestarting(true)
    try {
      const r = await fetch('/api/bridge/restart', { method: 'POST' })
      if (r.ok) setStatus(await r.json())
    } catch { /* ignore */ }
    finally { setRestarting(false) }
  }

  // Line cap for rendering
  const cappedLines = useMemo(() => lines.slice(-1000), [lines])

  if (notConfigured) {
    return (
      <div className="terminal-view">
        <div className="terminal-empty">
          <span className="terminal-empty-icon">{'>_'}</span>
          <p>Bridge not configured</p>
          <p className="terminal-empty-hint">
            Start the dashboard with <code>--bridge-channel CHANNEL_ID</code> to enable bridge management.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="terminal-view">
      <div className="terminal-toolbar">
        <div className="terminal-status">
          <span className={`terminal-dot ${status?.running ? 'running' : 'stopped'}`} />
          <span>{status?.running ? 'Running' : status ? 'Stopped' : '...'}</span>
          {status?.pid && status.running && <span className="terminal-pid">PID {status.pid}</span>}
          {status && !status.running && status.exit_code !== null && (
            <span className="terminal-pid">exit {status.exit_code}</span>
          )}
        </div>
        <div className="terminal-toolbar-right">
          <button
            className="terminal-restart-btn"
            onClick={handleRestart}
            disabled={restarting}
          >
            {restarting ? 'Restarting...' : 'Restart Bridge'}
          </button>
        </div>
      </div>
      <div
        className="terminal-output"
        ref={containerRef}
        onScroll={handleScroll}
      >
        {cappedLines.length === 0 && (
          <div className="terminal-waiting">Waiting for output...</div>
        )}
        {cappedLines.map((line, i) => (
          <div key={i} className="terminal-line">{line}</div>
        ))}
      </div>
    </div>
  )
}
