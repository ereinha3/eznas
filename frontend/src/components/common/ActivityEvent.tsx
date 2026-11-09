import { useState } from 'react'
import type { PipelineEvent } from '../../api'
import { PHASE_LABELS } from '../../constants'

function formatTimestamp(ts: number): string {
  const date = new Date(ts * 1000)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMin = Math.floor(diffMs / 60000)

  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  if (diffMin < 1440) return `${Math.floor(diffMin / 60)}h ago`

  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ' ' + date.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

interface ActivityEventProps {
  event: PipelineEvent
  compact?: boolean
}

export function ActivityEvent({ event, compact }: ActivityEventProps) {
  const [expanded, setExpanded] = useState(false)
  const phase = PHASE_LABELS[event.phase] || { color: '#999', label: event.phase }

  return (
    <div
      className={`activity-event ${event.level} ${compact ? 'compact' : ''}`}
      onClick={() => !compact && event.detail && setExpanded(!expanded)}
      style={{ cursor: !compact && event.detail ? 'pointer' : 'default' }}
    >
      <span className="event-time">{formatTimestamp(event.ts)}</span>
      <span
        className="event-phase-badge"
        style={{ backgroundColor: phase.color }}
      >
        {phase.label}
      </span>
      <span className={`event-level-dot ${event.level}`} />
      <span className="event-msg">{event.msg}</span>
      {!compact && event.detail && (
        <span className="event-expand-hint">{expanded ? '\u25B2' : '\u25BC'}</span>
      )}
      {expanded && event.detail && (
        <div className="event-detail">
          <pre>{JSON.stringify(event.detail, null, 2)}</pre>
        </div>
      )}
    </div>
  )
}
