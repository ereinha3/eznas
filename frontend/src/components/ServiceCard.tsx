import { useState } from 'react'
import type {
  HealthCheck,
  ServiceBaseConfig,
  ServiceCredential,
  ServiceKey,
  QbittorrentConfig,
  ProwlarrConfig,
} from './types'
import { StatusDot } from './common/StatusDot'
import { QuickLinkButton } from './common/QuickLinkButton'

const SERVICE_METADATA: Record<
  ServiceKey,
  { label: string; role: string; icon: string; showPort: boolean; showProxy: boolean }
> = {
  qbittorrent: { label: 'qBittorrent', role: 'Downloader', icon: 'QB', showPort: true, showProxy: true },
  radarr: { label: 'Radarr', role: 'Movies', icon: 'RD', showPort: true, showProxy: true },
  sonarr: { label: 'Sonarr', role: 'Series', icon: 'SN', showPort: true, showProxy: true },
  prowlarr: { label: 'Prowlarr', role: 'Indexers', icon: 'PR', showPort: true, showProxy: true },
  jellyseerr: { label: 'Jellyseerr', role: 'Requests', icon: 'JS', showPort: true, showProxy: true },
  jellyfin: { label: 'Jellyfin', role: 'Media server', icon: 'JF', showPort: true, showProxy: true },
  pipeline: { label: 'Pipeline worker', role: 'Post-processing', icon: 'PP', showPort: false, showProxy: false },
}

interface ServiceCardProps<T extends ServiceBaseConfig = ServiceBaseConfig> {
  serviceKey: ServiceKey
  service: T
  credentials?: ServiceCredential
  health?: HealthCheck
  duplicatePorts: Set<number>
  errors: Record<string, string>
  touched: Record<string, boolean>
  onUpdate: (patch: Partial<T>) => void
  onBlur: (field: string, value: string | number | null) => void
  onServicePort: (value: string) => void
  onServiceProxy: (value: string) => void
  onRefreshIndexers?: () => void
}

export function ServiceCard({
  serviceKey,
  service,
  credentials,
  health,
  duplicatePorts,
  errors,
  touched,
  onUpdate,
  onBlur,
  onServicePort,
  onServiceProxy,
  onRefreshIndexers,
}: ServiceCardProps) {
  const [credentialsExpanded, setCredentialsExpanded] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const metadata = SERVICE_METADATA[serviceKey]

  const getServiceUrl = (): string => {
    if (!service.enabled || !service.port) return '#'
    return `http://localhost:${service.port}`
  }

  return (
    <div className="service-card">
      {/* Header: icon, name, status, toggle */}
      <div className="service-card-header">
        <div className="service-info">
          <span className="service-icon">{metadata.icon}</span>
          <div>
            <h3>{metadata.label}</h3>
            <span className="service-role">{metadata.role}</span>
          </div>
        </div>

        <div className="service-actions">
          {health && <StatusDot healthy={health.healthy} enabled={service.enabled} />}
          {service.enabled && service.port && (
            <QuickLinkButton url={getServiceUrl()} />
          )}
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={service.enabled}
              onChange={(e) => onUpdate({ enabled: e.target.checked } as Partial<typeof service>)}
            />
            <span className="toggle-slider"></span>
          </label>
        </div>
      </div>

      {/* Configuration section (when enabled) */}
      {service.enabled && (
        <div className="service-config">
          {metadata.showPort && (
            <label htmlFor={`${serviceKey}-port`}>
              Port
              <input
                id={`${serviceKey}-port`}
                type="number"
                className={touched[`${serviceKey}-port`] && errors[`${serviceKey}-port`] ? 'has-error' : ''}
                value={service.port ?? ''}
                placeholder="Disabled"
                onChange={(e) => onServicePort(e.target.value)}
                onBlur={() => onBlur(`${serviceKey}-port`, service.port)}
              />
              {touched[`${serviceKey}-port`] && errors[`${serviceKey}-port`] && (
                <span className="field-error" role="alert">
                  {errors[`${serviceKey}-port`]}
                </span>
              )}
              {service.port && duplicatePorts.has(service.port) && (
                <span className="field-warning" role="alert">
                  Port conflict with another service
                </span>
              )}
            </label>
          )}
          {metadata.showProxy && (
            <label htmlFor={`${serviceKey}-proxy`}>
              Proxy URL
              <input
                id={`${serviceKey}-proxy`}
                value={service.proxy_url ?? ''}
                placeholder="media.example.com"
                onChange={(e) => onServiceProxy(e.target.value)}
              />
            </label>
          )}

          {/* qBittorrent-specific options */}
          {serviceKey === 'qbittorrent' && (
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={(service as QbittorrentConfig).stop_after_download}
                onChange={(e) =>
                  onUpdate({ stop_after_download: e.target.checked } as Partial<typeof service>)
                }
              />
              <span>Stop seeding after completion</span>
            </label>
          )}

          {/* Prowlarr-specific options */}
          {serviceKey === 'prowlarr' && (
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={(service as ProwlarrConfig).language_filter}
                onChange={(e) =>
                  onUpdate({ language_filter: e.target.checked } as Partial<typeof service>)
                }
              />
              <span>Filter indexers by language</span>
            </label>
          )}
        </div>
      )}

      {/* Credentials section (collapsible) */}
      {credentials && (
        <div className="credentials-section">
          <button
            className="credentials-toggle"
            onClick={() => setCredentialsExpanded(!credentialsExpanded)}
          >
            <span>Credentials</span>
            <span>{credentialsExpanded ? '▼' : '▶'}</span>
          </button>

          {credentialsExpanded && (
            <div className="credentials-content">
              {credentials.username && (
                <div className="credential-field">
                  <label>Username</label>
                  <input type="text" value={credentials.username} readOnly />
                </div>
              )}
              {credentials.password && credentials.canViewPassword && (
                <div className="credential-field">
                  <label>Password</label>
                  <div className="secret-value">
                    <input
                      type={showPassword ? 'text' : 'password'}
                      value={credentials.password}
                      readOnly
                    />
                    <button
                      className="icon-button"
                      onClick={() => setShowPassword(!showPassword)}
                    >
                      {showPassword ? 'Hide' : 'Show'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Prowlarr-specific: Refresh Indexers button */}
      {serviceKey === 'prowlarr' && service.enabled && onRefreshIndexers && (
        <button className="refresh-indexers-btn" onClick={onRefreshIndexers}>
          Refresh Indexers
        </button>
      )}
    </div>
  )
}
