import { useState, type ReactNode } from 'react'

export function CollapsibleSection({ title, children, defaultOpen = false }: {
  title: string
  children: ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className="section">
      <div className="section-title collapsible" onClick={() => setOpen(!open)}>
        {open ? '▼' : '▶'} {title}
      </div>
      {open && children}
    </div>
  )
}
