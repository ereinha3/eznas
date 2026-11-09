import { useState } from 'react'
import { fetchPipelineEvents, fetchRecentRuns } from '../api'
import type { PipelineEvent } from '../api'
import type { RunRecord } from '../components/types'
import { PHASE_LABELS } from '../constants'
import { usePolling } from '../hooks/usePolling'
import { useSSE } from '../hooks/useSSE'

const PHASES = Object.entries(PHASE_LABELS)

interface ActivityPageProps {
  logEntries: string[]
}

export function ActivityPage({ logEntries }: ActivityPageProps) {
  const [events, setEvents] = useState<PipelineEvent[]>([])
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null)
  const [phaseFilters, setPhaseFilters] = useState<Set<string>>(new Set(PHASES.map(([k]) => k)))
  const [levelFilters, setLevelFilters] = useState<Set<string>>(new Set(['info', 'warn', 'error']))
  const [searchText, setSearchText] = useState('')
  const [activeTab, setActiveTab] = useState<'pipeline' | 'deploy'>('pipeline')

  // Load historical events
  usePolling(async () => {
    const data = await fetchPipelineEvents(200)
    setEvents(data.events)
    return data
  }, 30000)

  // SSE for live updates
  const { connected } = useSSE(
    '/api/pipeline/events/stream?since=0',
    (_type, data) => {
      const event = data as PipelineEvent
      setEvents((prev) => {
        const updated = [event, ...prev]
        if (updated.length > 500) updated.length = 500
        return updated
      })
    },
  )

  const { data: runsData } = usePolling(() => fetchRecentRuns(10), 30000)

  // Filtering
  const filtered = events.filter((e) => {
    if (!phaseFilters.has(e.phase)) return false
    if (!levelFilters.has(e.level)) return false
    if (searchText && !e.msg.toLowerCase().includes(searchText.toLowerCase())) return false
    return true
  })

  const selected = selectedIdx !== null ? filtered[selectedIdx] : null

  const togglePhase = (phase: string) => {
    setPhaseFilters((prev) => {
      const next = new Set(prev)
      if (next.has(phase)) next.delete(phase); else next.add(phase)
      return next
    })
  }

  const toggleLevel = (level: string) => {
    setLevelFilters((prev) => {
      const next = new Set(prev)
      if (next.has(level)) next.delete(level); else next.add(level)
      return next
    })
  }

  // Phase counts
  const phaseCounts: Record<string, number> = {}
  for (const e of events) { phaseCounts[e.phase] = (phaseCounts[e.phase] || 0) + 1 }

  function formatTime(ts: number) {
    const d = new Date(ts * 1000)
    return d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  }

  function formatTimeAgo(ts: number) {
    const diff = Math.floor((Date.now() / 1000) - ts)
    if (diff < 60) return 'just now'
    if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
    if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
    return `${Math.floor(diff / 86400)}d ago`
  }

  return (
    <div className="activity-page-v2">
      {/* Tab bar */}
      <div className="activity-tab-bar">
        <button className={`tab-btn ${activeTab === 'pipeline' ? 'active' : ''}`} onClick={() => setActiveTab('pipeline')}>Pipeline</button>
        <button className={`tab-btn ${activeTab === 'deploy' ? 'active' : ''}`} onClick={() => setActiveTab('deploy')}>Deploy logs</button>
      </div>

      {activeTab === 'pipeline' && (
        <div className="activity-three-pane">
          {/* Left: Filters */}
          <div className="activity-filters">
            <div className="filter-group">
              <span className="filter-group-label">PHASE</span>
              {PHASES.map(([key, { color, label }]) => (
                <label key={key} className="filter-checkbox">
                  <input type="checkbox" checked={phaseFilters.has(key)} onChange={() => togglePhase(key)} />
                  <span className="filter-phase-dot" style={{ background: color }} />
                  <span className="filter-phase-name">{label}</span>
                  <span className="filter-phase-count">{phaseCounts[key] || 0}</span>
                </label>
              ))}
            </div>

            <div className="filter-divider" />

            <div className="filter-group">
              <span className="filter-group-label">LEVEL</span>
              <div className="filter-chips">
                {['info', 'warn', 'error'].map((lvl) => (
                  <button key={lvl} className={`filter-chip ${levelFilters.has(lvl) ? 'active' : ''}`} onClick={() => toggleLevel(lvl)}>
                    {lvl}
                  </button>
                ))}
              </div>
            </div>

            <div className="filter-divider" />

            <div className="filter-group">
              <span className="filter-group-label">SEARCH</span>
              <input type="text" value={searchText} onChange={(e) => setSearchText(e.target.value)} placeholder="filter events..." className="filter-search-input" />
            </div>
          </div>

          {/* Center: Timeline */}
          <div className="activity-timeline">
            <div className="timeline-header">
              <span className={`live-dot ${connected ? 'on' : 'off'}`} />
              <span className="timeline-live-label">{connected ? 'LIVE' : 'RECONNECTING'}</span>
              <span className="timeline-count">{filtered.length} events</span>
            </div>

            <div className="timeline-events">
              {filtered.map((event, idx) => {
                const phase = PHASE_LABELS[event.phase] || { color: '#999', label: event.phase }
                return (
                  <div
                    key={`${event.ts}-${idx}`}
                    className={`timeline-event ${selectedIdx === idx ? 'selected' : ''} ${event.level}`}
                    onClick={() => setSelectedIdx(idx)}
                  >
                    <span className="timeline-event-time">{formatTime(event.ts)}</span>
                    <span className="timeline-event-badge" style={{ background: phase.color }}>{phase.label}</span>
                    <div className="timeline-event-body">
                      <span className="timeline-event-title">{event.msg}</span>
                    </div>
                    {event.detail && <span className="timeline-event-chevron">{'\u25B8'}</span>}
                  </div>
                )
              })}
            </div>
          </div>

          {/* Right: Detail Panel */}
          <div className="activity-detail">
            {selected ? (
              <>
                <div className="detail-header">
                  <span className="detail-badge" style={{ background: (PHASE_LABELS[selected.phase] || { color: '#999' }).color }}>
                    {(PHASE_LABELS[selected.phase] || { label: selected.phase }).label}
                  </span>
                  <span className="detail-time">{formatTime(selected.ts)} · {formatTimeAgo(selected.ts)}</span>
                </div>
                <h3 className="detail-title">{selected.msg}</h3>
                {selected.detail && (
                  <div className="detail-metadata">
                    {Object.entries(selected.detail).map(([k, v]) => (
                      <div key={k} className="detail-kv">
                        <span className="detail-key">{k}</span>
                        <span className="detail-val">{typeof v === 'object' ? JSON.stringify(v) : String(v)}</span>
                      </div>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <div className="detail-empty">
                <p className="text-muted">Select an event to view details</p>
              </div>
            )}
          </div>
        </div>
      )}

      {activeTab === 'deploy' && (
        <div className="deploy-logs-pane">
          {logEntries.length > 0 && (
            <div className="card">
              <h3>Current session</h3>
              <div className="deploy-log-output">
                {logEntries.map((entry, i) => <div key={i} className="deploy-log-line">{entry}</div>)}
              </div>
            </div>
          )}
          {runsData?.runs?.map((run: RunRecord) => (
            <details key={run.run_id} className="deploy-run">
              <summary>
                <span className={`run-status-badge ${run.ok === true ? 'success' : run.ok === false ? 'failed' : 'running'}`}>
                  {run.ok === true ? 'OK' : run.ok === false ? 'FAILED' : 'RUNNING'}
                </span>
                {run.run_id.slice(0, 8)}
                {run.summary && <span className="text-muted"> — {run.summary}</span>}
              </summary>
              <div className="deploy-run-events">
                {run.events.map((evt, i) => <div key={i}>{evt.stage}: {evt.status}{evt.detail ? ` — ${evt.detail}` : ''}</div>)}
              </div>
            </details>
          ))}
          {logEntries.length === 0 && !runsData?.runs?.length && (
            <p className="text-muted">No deploy logs yet.</p>
          )}
        </div>
      )}
    </div>
  )
}
