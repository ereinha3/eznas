interface ApplyLogProps {
  entries: string[]
}

export function ApplyLog({ entries }: ApplyLogProps) {
  return (
    <div className="card log-card">
      <h3>Apply log</h3>
      <div className="log-output">
        {entries.length === 0 ? 'Apply output will appear here.' : entries.join('\n')}
      </div>
    </div>
  )
}
