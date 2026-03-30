import { useEffect, useRef } from 'react'
import { useStore } from './store/useStore'
import { TopBar } from './components/TopBar'
import { Overview } from './components/Overview'
import { WorkstreamView } from './components/WorkstreamView'

export default function App() {
  const { tracked, view, setData } = useStore()
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined)

  const pollAll = async () => {
    await Promise.all(
      tracked.map(id =>
        fetch(`/api/feature/${id}`)
          .then(r => r.ok ? r.json() : null)
          .then(d => { if (d) setData(id, d) })
          .catch(() => {})
      )
    )
  }

  useEffect(() => {
    pollAll()
    intervalRef.current = setInterval(pollAll, 10000)
    return () => clearInterval(intervalRef.current)
  }, [tracked.join(',')])

  useEffect(() => {
    if (view !== 'overview' && tracked.includes(view)) {
      fetch(`/api/feature/${view}`)
        .then(r => r.ok ? r.json() : null)
        .then(d => { if (d) setData(view, d) })
        .catch(() => {})
    }
  }, [view])

  return (
    <>
      <TopBar />
      <div className="main">
        {view === 'overview' ? <Overview /> : <WorkstreamView />}
      </div>
    </>
  )
}
