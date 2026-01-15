import type { ChangeEvent } from 'react'
import type { ServiceKey, StackConfig } from './types'

const LANGUAGE_OPTIONS = [
  { code: 'eng', label: 'English' },
  { code: 'und', label: 'Undetermined' },
  { code: 'spa', label: 'Spanish' },
  { code: 'fra', label: 'French' },
  { code: 'deu', label: 'German' },
  { code: 'ita', label: 'Italian' },
  { code: 'jpn', label: 'Japanese' },
  { code: 'kor', label: 'Korean' },
  { code: 'chi', label: 'Chinese' },
  { code: 'por', label: 'Portuguese' },
  { code: 'rus', label: 'Russian' },
] as const

interface ConfigFormProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onLoad: () => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onRender: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  status: string
  statusVariant: 'info' | 'success' | 'error'
  isApplying: boolean
  activeTab: 'setup' | 'services' | 'preferences'
}

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

export function ConfigForm({
  config,
  onChange,
  onLoad,
  onSave,
  onValidate,
  onRender,
  onApply,
  status,
  statusVariant,
  isApplying,
  activeTab,
}: ConfigFormProps) {
  const updatePaths = (field: keyof StackConfig['paths'], value: string) => {
    const nextValue =
      field === 'scratch' ? (value.trim() === '' ? null : value) : value
    onChange({
      ...config,
      paths: {
        ...config.paths,
        [field]: nextValue,
      },
    })
  }

  const updateRuntime = (field: 'user_id' | 'group_id' | 'timezone', value: string) => {
    onChange({
      ...config,
      runtime: {
        ...config.runtime,
        [field]: field === 'timezone' ? value : Number(value || '0'),
      },
    })
  }

  const updateProxy = (patch: Partial<StackConfig['proxy']>) => {
    onChange({
      ...config,
      proxy: {
        ...config.proxy,
        ...patch,
      },
    })
  }

  const updateUiPort = (value: string) => {
    onChange({
      ...config,
      ui: {
        ...config.ui,
        port: Number(value || '0'),
      },
    })
  }

  const updateService = <K extends ServiceKey>(
    key: K,
    patch: Partial<StackConfig['services'][K]>,
  ) => {
    onChange({
      ...config,
      services: {
        ...config.services,
        [key]: {
          ...config.services[key],
          ...patch,
        },
      },
    })
  }

  const handleServicePort = (key: ServiceKey, value: string) => {
    const trimmed = value.trim()
    const parsed = Number(trimmed)
    const nextPort = trimmed === '' || Number.isNaN(parsed) ? null : parsed
    updateService(key, { port: nextPort })
  }

  const handleServiceProxy = (key: ServiceKey, value: string) => {
    const trimmed = value.trim()
    updateService(key, { proxy_url: trimmed === '' ? null : trimmed })
  }

  const handleProxyHttpPort = (value: string) => {
    const trimmed = value.trim()
    const parsed = Number(trimmed)
    if (trimmed === '') {
      updateProxy({ http_port: 80 })
      return
    }
    if (!Number.isNaN(parsed)) {
      updateProxy({ http_port: parsed })
    }
  }

  const handleProxyHttpsPort = (value: string) => {
    const trimmed = value.trim()
    if (trimmed === '') {
      updateProxy({ https_port: null })
      return
    }
    const parsed = Number(trimmed)
    if (!Number.isNaN(parsed)) {
      updateProxy({ https_port: parsed })
    }
  }

  const handleProxyArgs = (value: string) => {
    const entries = value
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
    updateProxy({ additional_args: entries })
  }

  const updateCategory = (
    field: keyof StackConfig['download_policy']['categories'],
    value: string,
  ) => {
    onChange({
      ...config,
      download_policy: {
        ...config.download_policy,
        categories: {
          ...config.download_policy.categories,
          [field]: value,
        },
      },
    })
  }

  const updateMediaPolicy = (
    target: 'movies' | 'anime',
    field: 'keep_audio' | 'keep_subs',
    values: string[],
  ) => {
    onChange({
      ...config,
      media_policy: {
        ...config.media_policy,
        [target]: {
          ...config.media_policy[target],
          [field]: values,
        },
      },
    })
  }

  const handleSave = () => onSave(config)
  const handleValidate = () => onValidate(config)
  const handleRender = () => onRender(config)
  const handleApply = () => onApply(config)

  const handleLanguageSelect = (
    target: 'movies' | 'anime',
    field: 'keep_audio' | 'keep_subs',
    event: ChangeEvent<HTMLSelectElement>,
  ) => {
    const values = Array.from(event.target.selectedOptions).map((option) => option.value)
    updateMediaPolicy(target, field, values)
  }

  return (
    <div className="form-stack">
      {activeTab === 'setup' && (
        <>
          <h2>Storage Paths</h2>
          <div className="grid two">
            <label>
              Library pool
              <input
                value={config.paths.pool}
                placeholder="/mnt/pool"
                onChange={(e) => updatePaths('pool', e.target.value)}
              />
            </label>
            <label>
              Scratch volume (optional)
              <input
                value={config.paths.scratch ?? ''}
                placeholder="/mnt/scratch"
                onChange={(e) => updatePaths('scratch', e.target.value)}
              />
            </label>
            <label>
              Appdata root
              <input
                value={config.paths.appdata}
                placeholder="/srv/appdata"
                onChange={(e) => updatePaths('appdata', e.target.value)}
              />
            </label>
          </div>

          <h2>Runtime</h2>
          <div className="grid three">
            <label>
              PUID
              <input
                type="number"
                value={config.runtime.user_id}
                onChange={(e) => updateRuntime('user_id', e.target.value)}
              />
            </label>
            <label>
              PGID
              <input
                type="number"
                value={config.runtime.group_id}
                onChange={(e) => updateRuntime('group_id', e.target.value)}
              />
            </label>
            <label>
              Timezone
              <input
                value={config.runtime.timezone}
                placeholder="UTC"
                onChange={(e) => updateRuntime('timezone', e.target.value)}
              />
            </label>
          </div>

          <h2>Traefik Proxy</h2>
          <div className={`service-tile${config.proxy.enabled ? '' : ' disabled'}`}>
            <div className="service-heading">
              <div>
                <span className="service-name">Traefik</span>
                <span>Reverse proxy & routing</span>
              </div>
              <div className="service-toggle">
                <input
                  type="checkbox"
                  checked={config.proxy.enabled}
                  onChange={(e) => updateProxy({ enabled: e.target.checked })}
                />
              </div>
            </div>
            <div className="service-fields">
              <label>
                Image
                <input
                  value={config.proxy.image}
                  disabled={!config.proxy.enabled}
                  onChange={(e) => updateProxy({ image: e.target.value })}
                />
              </label>
              <label>
                HTTP port
                <input
                  type="number"
                  value={config.proxy.http_port}
                  disabled={!config.proxy.enabled}
                  onChange={(e) => handleProxyHttpPort(e.target.value)}
                />
              </label>
              <label>
                HTTPS port (optional)
                <input
                  type="number"
                  value={config.proxy.https_port ?? ''}
                  placeholder="Disabled"
                  disabled={!config.proxy.enabled}
                  onChange={(e) => handleProxyHttpsPort(e.target.value)}
                />
              </label>
              <label className="checkbox-inline">
                <input
                  type="checkbox"
                  checked={config.proxy.dashboard}
                  disabled={!config.proxy.enabled}
                  onChange={(e) => updateProxy({ dashboard: e.target.checked })}
                />
                <span>Expose Traefik dashboard (insecure)</span>
              </label>
              <label>
                Extra command arguments (one per line)
                <textarea
                  rows={3}
                  value={config.proxy.additional_args.join('\n')}
                  placeholder="--certificatesresolvers.myresolver.acme.email=you@example.com"
                  disabled={!config.proxy.enabled}
                  onChange={(e) => handleProxyArgs(e.target.value)}
                />
              </label>
            </div>
          </div>

          <h2>Orchestrator UI</h2>
          <div className="grid one">
            <label>
              UI port
              <input
                type="number"
                value={config.ui.port}
                onChange={(e) => updateUiPort(e.target.value)}
              />
            </label>
          </div>
        </>
      )}

      {activeTab === 'services' && (
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
                        checked={service.enabled}
                        onChange={(e) => updateService(key, { enabled: e.target.checked })}
                      />
                    </div>
                  </div>

                  <div className="service-fields">
                    {metadata.showPort && (
                      <label>
                        Port
                        <input
                          type="number"
                          value={service.port ?? ''}
                          placeholder="Disabled"
                          disabled={!service.enabled}
                          onChange={(e) => handleServicePort(key, e.target.value)}
                        />
                      </label>
                    )}
                    {metadata.showProxy && (
                      <label>
                        Proxy URL (Traefik host)
                        <input
                          value={service.proxy_url ?? ''}
                          placeholder="media.example.com"
                          disabled={!service.enabled}
                          onChange={(e) => handleServiceProxy(key, e.target.value)}
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
                              updateService('qbittorrent', {
                                stop_after_download: e.target.checked,
                              })
                            }
                          />
                          <span>Stop seeding after completion</span>
                        </label>
                      )
                    })()}
                  </div>
                </div>
              )
            })}
          </div>

          <h2>Download Categories</h2>
          <div className="grid three">
            <label>
              Radarr category
              <input
                value={config.download_policy.categories.radarr}
                onChange={(e) => updateCategory('radarr', e.target.value)}
              />
            </label>
            <label>
              Sonarr category
              <input
                value={config.download_policy.categories.sonarr}
                onChange={(e) => updateCategory('sonarr', e.target.value)}
              />
            </label>
            <label>
              Anime category
              <input
                value={config.download_policy.categories.anime}
                onChange={(e) => updateCategory('anime', e.target.value)}
              />
            </label>
          </div>
        </>
      )}

      {activeTab === 'preferences' && (
        <>
          <h2>Media Language Policy</h2>
          <div className="grid two">
        <label>
          Movies / TV audio
          <select
            multiple
            size={6}
            value={config.media_policy.movies.keep_audio}
            onChange={(e) => handleLanguageSelect('movies', 'keep_audio', e)}
          >
            {LANGUAGE_OPTIONS.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.code})
              </option>
            ))}
          </select>
        </label>
        <label>
          Movies / TV subtitles
          <select
            multiple
            size={6}
            value={config.media_policy.movies.keep_subs}
            onChange={(e) => handleLanguageSelect('movies', 'keep_subs', e)}
          >
            {LANGUAGE_OPTIONS.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.code})
              </option>
            ))}
          </select>
        </label>
        <label>
          Anime audio
          <select
            multiple
            size={6}
            value={config.media_policy.anime.keep_audio}
            onChange={(e) => handleLanguageSelect('anime', 'keep_audio', e)}
          >
            {LANGUAGE_OPTIONS.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.code})
              </option>
            ))}
          </select>
        </label>
        <label>
          Anime subtitles
          <select
            multiple
            size={6}
            value={config.media_policy.anime.keep_subs}
            onChange={(e) => handleLanguageSelect('anime', 'keep_subs', e)}
          >
            {LANGUAGE_OPTIONS.map((opt) => (
              <option key={opt.code} value={opt.code}>
                {opt.label} ({opt.code})
              </option>
            ))}
          </select>
        </label>
      </div>
      <h2>Quality &amp; Format Preferences</h2>
      <div className="grid three">
        <label>
          Quality preset
          <select
            value={config.quality.preset}
            onChange={(e) =>
              onChange({
                ...config,
                quality: {
                  ...config.quality,
                  preset: e.target.value,
                },
              })
            }
          >
            <option value="balanced">Balanced</option>
            <option value="1080p">1080p</option>
            <option value="4k">4K</option>
          </select>
        </label>
        <label>
          Target resolution
          <select
            value={config.quality.target_resolution ?? ''}
            onChange={(e) =>
              onChange({
                ...config,
                quality: {
                  ...config.quality,
                  target_resolution: e.target.value === '' ? null : e.target.value,
                },
              })
            }
          >
            <option value="">No preference</option>
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
            <option value="1440p">1440p</option>
            <option value="2160p">2160p (4K)</option>
          </select>
        </label>
        <label>
          Max bitrate (Mbps)
          <input
            type="number"
            min={1}
            value={config.quality.max_bitrate_mbps ?? ''}
            placeholder="Optional"
            onChange={(e) =>
              onChange({
                ...config,
                quality: {
                  ...config.quality,
                  max_bitrate_mbps: e.target.value === '' ? null : Number(e.target.value),
                },
              })
            }
          />
        </label>
        <label>
          Preferred container
          <select
            value={config.quality.preferred_container}
            onChange={(e) =>
              onChange({
                ...config,
                quality: {
                  ...config.quality,
                  preferred_container: e.target.value,
                },
              })
            }
          >
            <option value="mkv">MKV</option>
            <option value="mp4">MP4</option>
          </select>
        </label>
      </div>
        </>
      )}

      <div className="button-row">
        <button type="button" className="secondary" onClick={onLoad} disabled={isApplying}>
          Load current
        </button>
        <button type="button" className="secondary" onClick={handleSave} disabled={isApplying}>
          Save config
        </button>
        <button type="button" className="secondary" onClick={handleValidate} disabled={isApplying}>
          Validate
        </button>
        <button type="button" className="secondary" onClick={handleRender} disabled={isApplying}>
          Render compose
        </button>
        <button type="button" className="primary" onClick={handleApply} disabled={isApplying}>
          {isApplying ? 'Applying…' : 'Apply stack'}
        </button>
      </div>

      {status && <div className={`status-alert ${statusVariant} visible`}>{status}</div>}
    </div>
  )
}
