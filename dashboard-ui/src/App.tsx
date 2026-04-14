import { useEffect, useRef } from 'react'
import { useStore } from './store/useStore'
import { TopBar } from './components/TopBar'
import { Overview } from './components/Overview'
import { WorkstreamView } from './components/WorkstreamView'
import { Terminal } from './components/Terminal'

export default function App() {
  const tracked = useStore(s => s.tracked)
  const view = useStore(s => s.view)
  const setData = useStore(s => s.setData)
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined)
  const etagsRef = useRef<Record<string, string>>({})

  const fetchFeature = async (id: string) => {
    const headers: HeadersInit = {}
    const etag = etagsRef.current[id]
    if (etag) headers['If-None-Match'] = etag

    const r = await fetch(`/api/feature/${id}`, { headers }).catch(() => null)
    if (!r || r.status === 304) return // unchanged
    if (!r.ok) return

    const newEtag = r.headers.get('etag')
    if (newEtag) etagsRef.current[id] = newEtag

    const d = await r.json()
    setData(id, d)
  }

  const pollAll = () => Promise.all(tracked.map(fetchFeature))

  useEffect(() => {
    pollAll()
    intervalRef.current = setInterval(pollAll, 10000)
    return () => clearInterval(intervalRef.current)
  }, [tracked.join(',')])

  useEffect(() => {
    if (view !== 'overview' && view !== 'terminal' && tracked.includes(view)) {
      fetchFeature(view)
    }
  }, [view])

  return (
    <>
      <TopBar />
      <div className="main">
        {view === 'overview' ? <Overview /> :
         view === 'terminal' ? <Terminal /> :
         <WorkstreamView />}
      </div>
    </>
  )
}
