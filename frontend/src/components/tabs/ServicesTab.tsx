import type { ServiceKey, StackConfig } from '../types'

const SERVICE_METADATA: Record<ServiceKey, { label: string; role: string; showPort: boolean; showProxy: boolean }> = {
  qbittorrent: { label: 'qBittorrent', role: 'Downloader', showPort: true, showProxy: true },
  radarr: { label: 'Radarr', role: 'Movies', showPort: true, showProxy: true },
  sonarr: { label: 'Sonarr', role: 'Series', showPort: true, showProxy: true },
  prowlarr: { label: 'Prowlarr', role: 'Indexers', showPort: true, showProxy: true },
  jellyseerr: { label: 'Jellyseerr', role: 'Requests', showPort: true, showProxy: true },
  jellyfin: { label: 'Jellyfin', role: 'Media server', showPort: true, showProxy: true },
  pipeline: { label: 'Pipeline worker', role: 'Post-processing', showPort: false, showProxy: false },
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

interface ServicesTabProps {
  config: StackConfig
  errors: Record<string, string>
  touched: Record<string, boolean>
  duplicatePorts: Set<number>
  onBlur: (field: string, value: string | number | null) => void
  onUpdateService: <K extends ServiceKey>(key: K, patch: Partial<StackConfig['services'][K]>) => void
  onServicePort: (key: ServiceKey, value: string) => void
  onServiceProxy: (key: ServiceKey, value: string) => void
  onUpdateCategory: (field: keyof StackConfig['download_policy']['categories'], value: string) => void
}

export function ServicesTab({
  config,
  errors,
  touched,
  duplicatePorts,
  onBlur,
  onUpdateService,
  onServicePort,
  onServiceProxy,
  onUpdateCategory,
}: ServicesTabProps) {
  return (
    <>
      <h2>Services</h2>
      <div className="service-grid service-grid--detailed">
        {SERVICE_ORDER.map((key) => {
          const metadata = SERVICE_METADATA[key]
          const service = config.services[key]
          return (
            <div key={key} className={`service-tile${service.enabled ? '' : ' disabled'}`}>
              <div className="service-heading">
                <div>
                  <span className="service-name">{metadata.label}</span>
                  <span>{metadata.role}</span>
                </div>
                <div className="service-toggle">
                  <input
                    type="checkbox"
                    role="switch"
                    aria-checked={service.enabled}
                    data-service={key}
                    checked={service.enabled}
                    onChange={(e) => onUpdateService(key, { enabled: e.target.checked })}
                  />
                </div>
              </div>

              <div className="service-fields">
                {metadata.showPort && (
                  <label htmlFor={`${key}-port`}>
                    {metadata.label} port
                    <input
                      id={`${key}-port`}
                      type="number"
                      className={touched[`${key}-port`] && errors[`${key}-port`] ? 'has-error' : ''}
                      value={service.port ?? ''}
                      placeholder="Disabled"
                      disabled={!service.enabled}
                      onChange={(e) => onServicePort(key, e.target.value)}
                      onBlur={() => onBlur(`${key}-port`, service.port)}
                    />
                    {touched[`${key}-port`] && errors[`${key}-port`] && (
                      <span className="field-error" role="alert">{errors[`${key}-port`]}</span>
                    )}
                    {service.port && duplicatePorts.has(service.port) && (
                      <span className="field-warning" role="alert">
                        Port conflict with another service
                      </span>
                    )}
                  </label>
                )}
                {metadata.showProxy && (
                  <label htmlFor={`${key}-proxy`}>
                    Proxy URL (Traefik host)
                    <input
                      id={`${key}-proxy`}
                      value={service.proxy_url ?? ''}
                      placeholder="media.example.com"
                      disabled={!service.enabled}
                      onChange={(e) => onServiceProxy(key, e.target.value)}
                    />
                  </label>
                )}

                {key === 'qbittorrent' && (() => {
                  const qb = config.services.qbittorrent
                  return (
                    <label className="checkbox-inline">
                      <input
                        type="checkbox"
                        checked={qb.stop_after_download}
                        disabled={!qb.enabled}
                        onChange={(e) =>
                          onUpdateService('qbittorrent', {
                            stop_after_download: e.target.checked,
                          })
                        }
                      />
                      <span>Stop seeding after completion</span>
                    </label>
                  )
                })()}

                {key === 'prowlarr' && (() => {
                  const prowlarr = config.services.prowlarr
                  return (
                    <label className="checkbox-inline">
                      <input
                        type="checkbox"
                        checked={prowlarr.language_filter}
                        disabled={!prowlarr.enabled}
                        onChange={(e) =>
                          onUpdateService('prowlarr', {
                            language_filter: e.target.checked,
                          })
                        }
                      />
                      <span>Filter indexers by language</span>
                      <small className="field-hint">
                        When enabled, only adds indexers matching your language preferences.
                        Disable to search all public indexers (slower but more comprehensive).
                      </small>
                    </label>
                  )
                })()}
              </div>
            </div>
          )
        })}
      </div>

      <h2>Download Categories</h2>
      <div className="grid two">
        <label htmlFor="category-radarr">
          Radarr category
          <input
            id="category-radarr"
            value={config.download_policy.categories.radarr}
            onChange={(e) => onUpdateCategory('radarr', e.target.value)}
          />
        </label>
        <label htmlFor="category-sonarr">
          Sonarr category
          <input
            id="category-sonarr"
            value={config.download_policy.categories.sonarr}
            onChange={(e) => onUpdateCategory('sonarr', e.target.value)}
          />
        </label>
      </div>
    </>
  )
}
