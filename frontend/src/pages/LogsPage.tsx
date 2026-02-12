interface LogsPageProps {
  logEntries: string[]
}

export function LogsPage({ logEntries }: LogsPageProps) {
  return (
    <div className="logs-page">
      <h1>Apply Logs</h1>

      <div className="card log-card">
        <div className="log-output">
          {logEntries.length === 0 ? (
            <p className="empty-state">No logs yet. Run "Apply Stack" to see output.</p>
          ) : (
            logEntries.map((entry, idx) => (
              <div key={idx}>
                {entry}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
