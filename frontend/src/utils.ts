/** Format bytes to human-readable string (e.g., "1.5 GB"). */
export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

/** Format seconds to human-readable duration (e.g., "2h 15m"). */
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

/** Format a Unix timestamp or ISO string to relative time (e.g., "5m ago"). */
export function timeAgo(value: string | number | null): string {
  if (!value) return 'Never'
  const ts = typeof value === 'string'
    ? new Date(value).getTime() / 1000
    : value
  const seconds = Math.floor(Date.now() / 1000 - ts)
  if (seconds < 0) return 'just now'
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`
  return `${Math.floor(seconds / 86400)}d ago`
}
