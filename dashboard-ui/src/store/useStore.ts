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

export const useStore = create<Store>((set, get) => ({
  tracked: saved,
  data: {},
  view: 'overview',

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
    set({ tracked: next, data: rest, view: view === id ? 'overview' : view })
  },

  setView: (v) => set({ view: v }),

  setData: (id, d) => set(s => ({ data: { ...s.data, [id]: d } })),
}))
