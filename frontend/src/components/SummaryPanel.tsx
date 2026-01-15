import { useMemo } from 'react'
import type { ServiceStatus, StackConfig } from './types'

interface SummaryPanelProps {
  config: StackConfig
  serviceStatus: ServiceStatus[]
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

export function SummaryPanel({ config, serviceStatus }: SummaryPanelProps) {
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

  const dnsRecords = useMemo(() => {
    const entries: Array<{ name: keyof StackConfig['services']; host: string }> = []
    for (const key of SERVICE_ORDER) {
      const host = config.services[key].proxy_url
      if (host) {
        entries.push({ name: key, host })
      }
    }
    return entries
  }, [config.services])

  const serviceRows = useMemo(() => {
    return SERVICE_ORDER.map((key) => {
      const label = SERVICE_LABELS[key]
      const enabled = config.services[key].enabled
      const status =
        serviceStatus.find((s) => s.name === key)?.status ??
        (enabled ? 'unknown' : 'down')
      return { key, label, enabled, status }
    })
  }, [config.services, serviceStatus])

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
              <a href={link.url} target="_blank" rel="noopener noreferrer">
                {link.name.charAt(0).toUpperCase() + link.name.slice(1)}
              </a>
              {link.proxy ? (
                <span className="badge on">{link.proxy}</span>
              ) : (
                <span className="badge on">:{link.port}</span>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="card">
        <h3>DNS suggestions</h3>
        <ul className="storage-list">
          {dnsRecords.length === 0 ? (
            <li className="empty-state">Set proxy URLs to generate DNS records.</li>
          ) : (
            dnsRecords.map((record) => (
              <li key={record.name}>
                <span>{record.host}</span>
                <span className="badge">CNAME -&gt; traefik</span>
              </li>
            ))
          )}
        </ul>
      </div>
    </section>
  )
}
