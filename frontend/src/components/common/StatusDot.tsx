interface StatusDotProps {
  healthy: boolean | undefined
  enabled: boolean
}

export function StatusDot({ healthy, enabled }: StatusDotProps) {
  if (!enabled) {
    return <span className="status-dot unknown">Disabled</span>
  }

  if (healthy === undefined) {
    return <span className="status-dot unknown">Unknown</span>
  }

  return (
    <span className={`status-dot ${healthy ? 'up' : 'down'}`}>
      {healthy ? 'Healthy' : 'Down'}
    </span>
  )
}
