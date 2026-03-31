export function Gates({ gates }: { gates: Record<string, boolean> }) {
  return (
    <div className="gates-row">
      {Object.entries(gates).map(([name, passed]) => (
        <div key={name} className={`gate-pill ${passed ? 'passed' : 'pending'}`}>
          {passed ? '✓' : '○'} {name}
        </div>
      ))}
    </div>
  )
}
