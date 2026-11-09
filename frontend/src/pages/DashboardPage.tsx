import { useState, useEffect, useCallback } from 'react'
import type { HealthResponse, ServiceKey, StackConfig } from '../components/types'
import { StatusDot } from '../components/common/StatusDot'
import { ServiceLinkDropdown } from '../components/common/ServiceLinkDropdown'
import { ActivityEvent } from '../components/common/ActivityEvent'
import { fetchPipelineEvents, pipelineHealth } from '../api'
import type { PipelineEvent } from '../api'

const SERVICE_METADATA: Record<ServiceKey, { label: string; icon: string }> = {
  qbittorrent: { label: 'qBittorrent', icon: 'QB' },
  radarr: { label: 'Radarr', icon: 'RD' },
  sonarr: { label: 'Sonarr', icon: 'SN' },
  prowlarr: { label: 'Prowlarr', icon: 'PR' },
  jellyseerr: { label: 'Jellyseerr', icon: 'JS' },
  jellyfin: { label: 'Jellyfin', icon: 'JF' },
  bazarr: { label: 'Bazarr', icon: 'BZ' },
  flaresolverr: { label: 'FlareSolverr', icon: 'FS' },
  pipeline: { label: 'Pipeline', icon: 'PP' },
  gluetun: { label: 'VPN Gateway', icon: 'VPN' },
}

const SERVICE_ORDER: ServiceKey[] = [
  'qbittorrent',
  'radarr',
  'sonarr',
  'prowlarr',
  'jellyseerr',
  'jellyfin',
  'bazarr',
  'flaresolverr',
  'pipeline',
]

interface DashboardPageProps {
  config: StackConfig
  health: HealthResponse | null
}

export function DashboardPage({ config, health }: DashboardPageProps) {
  const [recentEvents, setRecentEvents] = useState<PipelineEvent[]>([])
  const [eventsLoading, setEventsLoading] = useState(true)
  const [pipelineStatus, setPipelineStatus] = useState<{ status: string; last_tick: string | null } | null>(null)

  const loadEvents = useCallback(async () => {
    try {
      const data = await fetchPipelineEvents(10)
      setRecentEvents(data.events)
    } catch {
      // Events endpoint may not be available
    } finally {
      setEventsLoading(false)
    }
  }, [])

  const loadPipelineStatus = useCallback(async () => {
    try {
      const data = await pipelineHealth()
      setPipelineStatus(data)
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    loadEvents()
    loadPipelineStatus()
    const id = setInterval(() => { loadEvents(); loadPipelineStatus() }, 10000)
    return () => clearInterval(id)
  }, [loadEvents, loadPipelineStatus])

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
        {/* Pipeline activity feed */}
        <div className="dashboard-section">
          <h2 className="dashboard-section-title">
            Pipeline Activity
            {pipelineStatus && (
              <span className={`pipeline-status-inline ${pipelineStatus.status}`}>
                {pipelineStatus.status}
              </span>
            )}
          </h2>
          <div className="activity-feed">
            {eventsLoading ? (
              <div className="activity-empty">Loading activity...</div>
            ) : recentEvents.length === 0 ? (
              <div className="activity-empty">
                No pipeline events yet. Events appear as the pipeline processes media.
              </div>
            ) : (
              recentEvents.map((event, idx) => (
                <ActivityEvent key={`${event.ts}-${idx}`} event={event} compact />
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
