import { useState, useEffect, useCallback } from 'react'
import type { HealthResponse, RunRecord, ServiceKey, StackConfig } from '../components/types'
import { StatusDot } from '../components/common/StatusDot'
import { ServiceLinkDropdown } from '../components/common/ServiceLinkDropdown'
import { fetchRecentRuns } from '../api'

const SERVICE_METADATA: Record<ServiceKey, { label: string; icon: string }> = {
  qbittorrent: { label: 'qBittorrent', icon: 'QB' },
  radarr: { label: 'Radarr', icon: 'RD' },
  sonarr: { label: 'Sonarr', icon: 'SN' },
  prowlarr: { label: 'Prowlarr', icon: 'PR' },
  jellyseerr: { label: 'Jellyseerr', icon: 'JS' },
  jellyfin: { label: 'Jellyfin', icon: 'JF' },
  pipeline: { label: 'Pipeline', icon: 'PP' },
}

const SERVICE_ORDER: ServiceKey[] = [
  'qbittorrent',
  'radarr',
  'sonarr',
  'prowlarr',
  'jellyseerr',
  'jellyfin',
  'pipeline',
]

interface DashboardPageProps {
  config: StackConfig
  health: HealthResponse | null
}

/** Format a run_id (typically a UUID or timestamp-based ID) into a short display string. */
function shortRunId(runId: string): string {
  // If it looks like a UUID, show first 8 chars
  if (runId.length > 12) return runId.slice(0, 8)
  return runId
}

/** Derive a relative timestamp from a run_id (many run IDs embed a timestamp). */
function runTimestamp(runId: string): string {
  // run_ids are often UUIDs or timestamps; just show the ID for now
  return shortRunId(runId)
}

export function DashboardPage({ config, health }: DashboardPageProps) {
  const [recentRuns, setRecentRuns] = useState<RunRecord[]>([])
  const [runsLoading, setRunsLoading] = useState(true)

  const loadRuns = useCallback(async () => {
    try {
      const data = await fetchRecentRuns(5)
      setRecentRuns(data.runs)
    } catch {
      // Runs endpoint may not be available
    } finally {
      setRunsLoading(false)
    }
  }, [])

  useEffect(() => {
    loadRuns()
    // Refresh every 15 seconds
    const id = setInterval(loadRuns, 15000)
    return () => clearInterval(id)
  }, [loadRuns])

  // Compute health summary
  const enabledServices = SERVICE_ORDER.filter((key) => config.services[key].enabled)
  const healthyCount = enabledServices.filter((key) => {
    const h = health?.services.find((s) => s.name === key)
    return h?.healthy === true
  }).length
  const unhealthyCount = enabledServices.filter((key) => {
    const h = health?.services.find((s) => s.name === key)
    return h?.healthy === false
  }).length
  const unknownCount = enabledServices.length - healthyCount - unhealthyCount

  const overallStatus = health?.status ?? 'unknown'

  return (
    <div className="dashboard-page">
      <h1>Dashboard</h1>

      {/* System health overview */}
      <div className="dashboard-health-overview">
        <div className={`health-badge ${overallStatus}`}>
          <span className="health-badge-dot" />
          <span className="health-badge-label">
            {overallStatus === 'healthy'
              ? 'All Systems Healthy'
              : overallStatus === 'degraded'
                ? 'Degraded'
                : overallStatus === 'unhealthy'
                  ? 'Unhealthy'
                  : 'Checking...'}
          </span>
        </div>
        <div className="health-summary-stats">
          <div className="health-stat healthy">
            <span className="health-stat-count">{healthyCount}</span>
            <span className="health-stat-label">Healthy</span>
          </div>
          {unhealthyCount > 0 && (
            <div className="health-stat unhealthy">
              <span className="health-stat-count">{unhealthyCount}</span>
              <span className="health-stat-label">Down</span>
            </div>
          )}
          {unknownCount > 0 && (
            <div className="health-stat unknown">
              <span className="health-stat-count">{unknownCount}</span>
              <span className="health-stat-label">Unknown</span>
            </div>
          )}
          <div className="health-stat total">
            <span className="health-stat-count">{enabledServices.length}</span>
            <span className="health-stat-label">Enabled</span>
          </div>
        </div>
      </div>

      {/* Service status grid */}
      <div className="dashboard-section">
        <h2 className="dashboard-section-title">Services</h2>
        <div className="dashboard-grid">
          {SERVICE_ORDER.map((key) => {
            const service = config.services[key]
            const healthStatus = health?.services.find((h) => h.name === key)
            const metadata = SERVICE_METADATA[key]

            return (
              <div key={key} className={`status-card${!service.enabled ? ' disabled' : ''}`}>
                <div className="status-card-header">
                  <span className="service-icon">{metadata.icon}</span>
                  <div className="status-card-info">
                    <h4>{metadata.label}</h4>
                    <StatusDot healthy={healthStatus?.healthy} enabled={service.enabled} />
                  </div>
                </div>
                {service.enabled && service.port && (
                  <div className="status-card-footer">
                    <span className="status-port">Port: {service.port}</span>
                    <ServiceLinkDropdown
                      serviceName={metadata.label}
                      port={service.port}
                      proxyUrl={service.proxy_url}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>

      {/* Two-column bottom: Activity + Storage */}
      <div className="dashboard-bottom-grid">
        {/* Activity feed */}
        <div className="dashboard-section">
          <h2 className="dashboard-section-title">Recent Activity</h2>
          <div className="activity-feed">
            {runsLoading ? (
              <div className="activity-empty">Loading activity...</div>
            ) : recentRuns.length === 0 ? (
              <div className="activity-empty">
                No apply runs yet. Deploy your stack from Settings to get started.
              </div>
            ) : (
              recentRuns.map((run) => (
                <ActivityItem key={run.run_id} run={run} />
              ))
            )}
          </div>
        </div>

        {/* Storage + Config summary */}
        <div className="dashboard-section">
          <h2 className="dashboard-section-title">Configuration</h2>
          <div className="config-summary-card">
            <div className="config-summary-group">
              <h4>Storage Paths</h4>
              <div className="config-summary-items">
                <ConfigSummaryItem
                  label="Library Pool"
                  value={config.paths.pool}
                />
                <ConfigSummaryItem
                  label="Scratch"
                  value={config.paths.scratch}
                />
                <ConfigSummaryItem
                  label="Appdata"
                  value={config.paths.appdata}
                />
              </div>
            </div>

            <div className="config-summary-group">
              <h4>Runtime</h4>
              <div className="config-summary-items">
                <ConfigSummaryItem
                  label="Timezone"
                  value={config.runtime.timezone}
                />
                <ConfigSummaryItem
                  label="UID / GID"
                  value={`${config.runtime.user_id} / ${config.runtime.group_id}`}
                />
              </div>
            </div>

            <div className="config-summary-group">
              <h4>Proxy</h4>
              <div className="config-summary-items">
                <ConfigSummaryItem
                  label="Status"
                  value={config.proxy.enabled ? 'Enabled' : 'Disabled'}
                  variant={config.proxy.enabled ? 'success' : 'muted'}
                />
                {config.proxy.enabled && (
                  <ConfigSummaryItem
                    label="HTTP Port"
                    value={String(config.proxy.http_port)}
                  />
                )}
              </div>
            </div>

            <div className="config-summary-group">
              <h4>Quality</h4>
              <div className="config-summary-items">
                <ConfigSummaryItem
                  label="Preset"
                  value={config.quality.preset}
                />
                <ConfigSummaryItem
                  label="Container"
                  value={config.quality.preferred_container}
                />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** Renders a single activity item from a run record. */
function ActivityItem({ run }: { run: RunRecord }) {
  const isRunning = run.ok === null
  const isSuccess = run.ok === true

  // Count event stages
  const totalStages = run.events.length
  const okStages = run.events.filter((e) => e.status === 'ok').length
  const failedStages = run.events.filter((e) => e.status === 'failed').length

  // Find the last meaningful event for the summary
  const lastEvent = run.events.length > 0 ? run.events[run.events.length - 1] : null

  return (
    <div className={`activity-item ${isRunning ? 'running' : isSuccess ? 'success' : 'failed'}`}>
      <div className="activity-item-icon">
        {isRunning ? (
          <span className="activity-spinner" />
        ) : isSuccess ? (
          <span className="activity-check">&#10003;</span>
        ) : (
          <span className="activity-x">&#10007;</span>
        )}
      </div>
      <div className="activity-item-content">
        <div className="activity-item-header">
          <span className="activity-item-title">
            {isRunning ? 'Applying...' : isSuccess ? 'Apply Succeeded' : 'Apply Failed'}
          </span>
          <span className="activity-item-id">{runTimestamp(run.run_id)}</span>
        </div>
        <div className="activity-item-detail">
          {run.summary ? (
            <span>{run.summary}</span>
          ) : lastEvent ? (
            <span>
              {lastEvent.stage}: {lastEvent.detail || lastEvent.status}
            </span>
          ) : (
            <span>Starting...</span>
          )}
        </div>
        <div className="activity-item-progress">
          <span className="activity-stage-count">
            {okStages}/{totalStages} stages
          </span>
          {failedStages > 0 && (
            <span className="activity-failed-count">
              {failedStages} failed
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

/** A single config summary key-value row. */
function ConfigSummaryItem({
  label,
  value,
  variant,
}: {
  label: string
  value: string | null | undefined
  variant?: 'success' | 'muted'
}) {
  return (
    <div className="config-summary-item">
      <span className="config-summary-label">{label}</span>
      <span className={`config-summary-value ${variant || ''}`}>
        {value || <em className="empty-state">Not set</em>}
      </span>
    </div>
  )
}
