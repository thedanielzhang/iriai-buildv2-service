import { memo } from 'react'

export function Gates({ gates }: { gates: Record<string, boolean> }) {
  return (
    <div className="gates-row">
      {Object.entries(gates).map(([name, passed]) => (
        <GatePill key={name} name={name} passed={passed} />
      ))}
    </div>
  )
}

const GatePill = memo(function GatePill({ name, passed }: { name: string; passed: boolean }) {
  return (
    <div className={`gate-pill ${passed ? 'passed' : 'pending'}`}>
      {passed ? '✓' : '○'} {name}
    </div>
  )
})
