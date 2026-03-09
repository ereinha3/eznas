import { useEffect, useState } from 'react'
import { fetchRecentRuns } from '../api'
import type { RunRecord } from '../components/types'

interface LogsPageProps {
  logEntries: string[]
}

export function LogsPage({ logEntries }: LogsPageProps) {
  const [pastRuns, setPastRuns] = useState<RunRecord[]>([])

  useEffect(() => {
    fetchRecentRuns(5)
      .then((data) => setPastRuns(data.runs))
      .catch(() => {})
  }, [])

  return (
    <div className="logs-page">
      <h1>Apply Logs</h1>

      {/* Current / most recent apply */}
      <div className="card log-card">
        <h3>Current Session</h3>
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

      {/* Past runs from backend */}
      {pastRuns.length > 0 && (
        <div className="card log-card" style={{ marginTop: '1rem' }}>
          <h3>Recent Runs</h3>
          {pastRuns.map((run) => (
            <details key={run.run_id} className="run-details">
              <summary>
                <span className={`run-status ${run.ok === true ? 'success' : run.ok === false ? 'failed' : 'running'}`}>
                  {run.ok === true ? 'OK' : run.ok === false ? 'FAILED' : 'RUNNING'}
                </span>
                {' '}{run.run_id.slice(0, 8)}
                {run.summary && <span className="run-summary"> — {run.summary}</span>}
              </summary>
              <div className="log-output">
                {run.events.map((evt, idx) => (
                  <div key={idx}>
                    {evt.stage}: {evt.status}{evt.detail ? ` - ${evt.detail}` : ''}
                  </div>
                ))}
              </div>
            </details>
          ))}
        </div>
      )}
    </div>
  )
}
