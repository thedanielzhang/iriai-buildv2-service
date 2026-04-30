import { useEffect, useRef } from 'react'
import { useStore } from './store/useStore'
import { TopBar } from './components/TopBar'
import { Overview } from './components/Overview'
import { WorkstreamView } from './components/WorkstreamView'
import { Terminal } from './components/Terminal'
import { XPCommandCenterMockup } from './components/XPCommandCenterMockup'

export default function App() {
  const searchParams = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : new URLSearchParams()
  const showXPMockup = searchParams.get('mockup') === 'xp'
  const mockupFeatureId = showXPMockup ? searchParams.get('feature') : null
  const legacyFeatureView = searchParams.get('legacy') === '1' || searchParams.get('view') === 'legacy'
  const tracked = useStore(s => s.tracked)
  const view = useStore(s => s.view)
  const currentFeatureData = useStore(s => s.data[s.view])
  const addFeature = useStore(s => s.addFeature)
  const setData = useStore(s => s.setData)
  const intervalRef = useRef<ReturnType<typeof setInterval>>(undefined)
  const etagsRef = useRef<Record<string, string>>({})

  const fetchFeature = async (id: string) => {
    const headers: HeadersInit = {}
    const etag = etagsRef.current[id]
    const hasCachedData = Boolean(useStore.getState().data[id])
    if (etag && hasCachedData) headers['If-None-Match'] = etag

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
    if (mockupFeatureId && !tracked.includes(mockupFeatureId)) {
      addFeature(mockupFeatureId)
    }
    if (mockupFeatureId) {
      fetchFeature(mockupFeatureId)
    }
  }, [mockupFeatureId, tracked.join(',')])

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

  if (showXPMockup) {
    return <XPCommandCenterMockup />
  }

  const isFeatureView = view !== 'overview' && view !== 'terminal'
  const isBugflowView = Boolean(isFeatureView && currentFeatureData?.workflow_name === 'bugfix-v2' && currentFeatureData?.bugflow)
  const showLegacyChrome = !isFeatureView || legacyFeatureView || isBugflowView

  return (
    <>
      {showLegacyChrome && <TopBar />}
      <div className={showLegacyChrome ? 'main' : 'feature-exhibit-main'}>
        {view === 'overview' ? <Overview /> :
         view === 'terminal' ? <Terminal /> :
         <WorkstreamView />}
      </div>
    </>
  )
}
