import { create } from 'zustand'
import type { FeatureData } from '../types'

interface Store {
  tracked: string[]
  data: Record<string, FeatureData>
  view: 'overview' | string // 'overview' or feature_id
  addFeature: (id: string) => void
  removeFeature: (id: string) => void
  setView: (v: string) => void
  setData: (id: string, d: FeatureData) => void
}

const saved = JSON.parse(localStorage.getItem('iriai_tracked') || '[]') as string[]

function viewFromPath(): string {
  const seg = window.location.pathname.replace(/^\/+|\/+$/g, '')
  if (!seg) return 'overview'

  const parts = seg.split('/')
  if (parts[0] === 'feature' && parts[1]) return parts[1]
  if (parts[0] === 'terminal') return 'terminal'
  return parts[0]
}

function pushView(v: string) {
  let path = '/'
  if (v === 'terminal') path = '/terminal'
  else if (v !== 'overview') path = `/feature/${v}`
  if (window.location.pathname !== path) {
    window.history.pushState(null, '', path)
  }
}

const initialView = viewFromPath()
const initialTracked = initialView !== 'overview' && !saved.includes(initialView)
  ? [...saved, initialView]
  : saved
if (initialTracked !== saved) {
  localStorage.setItem('iriai_tracked', JSON.stringify(initialTracked))
}

export const useStore = create<Store>((set, get) => ({
  tracked: initialTracked,
  data: {},
  view: initialView,

  addFeature: (id) => {
    const { tracked } = get()
    if (!tracked.includes(id)) {
      const next = [...tracked, id]
      localStorage.setItem('iriai_tracked', JSON.stringify(next))
      set({ tracked: next })
    }
  },

  removeFeature: (id) => {
    const { tracked, data, view } = get()
    const next = tracked.filter(f => f !== id)
    localStorage.setItem('iriai_tracked', JSON.stringify(next))
    const { [id]: _, ...rest } = data
    const nextView = view === id ? 'overview' : view
    pushView(nextView)
    set({ tracked: next, data: rest, view: nextView })
  },

  setView: (v) => {
    pushView(v)
    set({ view: v })
  },

  setData: (id, d) => set(s => ({ data: { ...s.data, [id]: d } })),
}))

// Handle browser back/forward
window.addEventListener('popstate', () => {
  const v = viewFromPath()
  const { view, addFeature } = useStore.getState()
  if (v !== view) {
    if (v !== 'overview') addFeature(v)
    useStore.setState({ view: v })
  }
})
