import { useMemo } from 'react'
import type { HealthResponse, ServiceStatus, StackConfig } from './types'

interface SummaryPanelProps {
  config: StackConfig
  serviceStatus: ServiceStatus[]
  health: HealthResponse | null
}

const SERVICE_ORDER: Array<keyof StackConfig['services']> = [
  'qbittorrent',
  'radarr',
  'sonarr',
  'prowlarr',
  'jellyseerr',
  'jellyfin',
  'pipeline',
]

const SERVICE_LABELS: Record<keyof StackConfig['services'], string> = {
  qbittorrent: 'Downloader',
  radarr: 'Movies',
  sonarr: 'Series',
  prowlarr: 'Indexers',
  jellyseerr: 'Requests',
  jellyfin: 'Library',
  pipeline: 'Post-processing',
}

const SERVICE_ICONS: Record<string, string> = {
  qbittorrent: 'qB',
  radarr: 'Ra',
  sonarr: 'So',
  prowlarr: 'Pr',
  jellyseerr: 'Js',
  jellyfin: 'Jf',
}

export function SummaryPanel({ config, serviceStatus, health }: SummaryPanelProps) {
  const links = useMemo(() => {
    const host = typeof window !== 'undefined' ? window.location.hostname : 'localhost'
    const preferHttps = config.proxy.https_port !== null
    const services = [
      { id: 'qbittorrent' as const },
      { id: 'radarr' as const },
      { id: 'sonarr' as const },
      { id: 'prowlarr' as const },
      { id: 'jellyseerr' as const },
      { id: 'jellyfin' as const },
    ]
    return services
      .map((service) => {
        const svc = config.services[service.id]
        const proxy = svc.proxy_url ?? undefined
        const port = svc.port ?? undefined
        let url: string | null = null
        if (proxy) {
          if (proxy.startsWith('http://') || proxy.startsWith('https://')) {
            url = proxy
          } else {
            url = `${preferHttps ? 'https' : 'http'}://${proxy}`
          }
        } else if (port) {
          url = `http://${host}:${port}`
        }
        return {
          name: service.id,
          enabled: svc.enabled,
          url,
          port,
          proxy,
        }
      })
      .filter((service) => service.enabled && service.url)
      .map((service) => ({
        name: service.name,
        url: service.url as string,
        port: service.port ?? undefined,
        proxy: service.proxy,
      }))
  }, [config])

  const serviceRows = useMemo(() => {
    return SERVICE_ORDER.map((key) => {
      const label = SERVICE_LABELS[key]
      const enabled = config.services[key].enabled
      // Prefer health endpoint data, fall back to legacy serviceStatus
      const healthCheck = health?.services.find((h) => h.name === key)
      let status: 'up' | 'down' | 'unknown'
      if (healthCheck) {
        status = healthCheck.healthy ? 'up' : 'down'
      } else {
        status = serviceStatus.find((s) => s.name === key)?.status ??
          (enabled ? 'unknown' : 'down')
      }
      return { key, label, enabled, status }
    })
  }, [config.services, serviceStatus, health])

  return (
    <section className="summary-column">
      <div className="card">
        <h3>Service summary</h3>
        <ul className="summary-list">
          {serviceRows.map(({ key, label, enabled, status }) => (
            <li key={key} className={enabled ? 'on' : 'off'}>
              <div>
                <strong>{key.charAt(0).toUpperCase() + key.slice(1)}</strong>
                <span className={`badge${enabled ? ' on' : ''}`}>{label}</span>
              </div>
              <span className={`status-dot ${enabled ? status : 'unknown'}`}>
                {enabled ? status : 'off'}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h3>Storage &amp; mounts</h3>
        <ul className="storage-list">
          <li>
            <span>Library pool</span>
            <span>{config.paths.pool || '—'}</span>
          </li>
          <li>
            <span>Scratch</span>
            <span>{config.paths.scratch ?? '—'}</span>
          </li>
          <li>
            <span>Appdata</span>
            <span>{config.paths.appdata || '—'}</span>
          </li>
        </ul>
      </div>

      <div className="card">
        <h3>Quick links</h3>
        <ul className="quicklinks">
          {links.length === 0 && <li className="empty-state">Enable a service to see its quick link.</li>}
          {links.map((link) => (
            <li key={link.name}>
              <a href={link.url} target="_blank" rel="noopener noreferrer" className="quicklink-item">
                <span className="service-icon">{SERVICE_ICONS[link.name] || '??'}</span>
                <span className="service-name">{link.name.charAt(0).toUpperCase() + link.name.slice(1)}</span>
              </a>
            </li>
          ))}
        </ul>
      </div>
    </section>
  )
}
