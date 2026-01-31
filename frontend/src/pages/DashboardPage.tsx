import type { HealthResponse, ServiceKey, StackConfig } from '../components/types'
import { StatusDot } from '../components/common/StatusDot'
import { ServiceLinkDropdown } from '../components/common/ServiceLinkDropdown'

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

export function DashboardPage({ config, health }: DashboardPageProps) {
  return (
    <div className="dashboard-page">
      <h1>Dashboard</h1>

      {/* Service status grid */}
      <div className="dashboard-grid">
        {SERVICE_ORDER.map((key) => {
          const service = config.services[key]
          const healthStatus = health?.services.find((h) => h.name === key)
          const metadata = SERVICE_METADATA[key]

          return (
            <div key={key} className="status-card">
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

      {/* Storage summary */}
      <div className="card">
        <h2>Storage</h2>
        <div className="storage-grid">
          <div className="storage-item">
            <label>Library Pool</label>
            <span>{config.paths.pool || <em className="empty-state">Not configured</em>}</span>
          </div>
          <div className="storage-item">
            <label>Scratch</label>
            <span>{config.paths.scratch || <em className="empty-state">Not configured</em>}</span>
          </div>
          <div className="storage-item">
            <label>Appdata</label>
            <span>{config.paths.appdata || <em className="empty-state">Not configured</em>}</span>
          </div>
        </div>
      </div>
    </div>
  )
}
