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
                    <>
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
                      <div className="grid two">
                        <label>
                          Username
                          <input
                            value={qb.username}
                            disabled={!qb.enabled}
                            onChange={(e) =>
                              updateService('qbittorrent', { username: e.target.value })
                            }
                          />
                        </label>
                        <label>
                          Password
                          <input
                            type="text"
                            value={qb.password}
                            disabled={!qb.enabled}
                            onChange={(e) =>
                              updateService('qbittorrent', { password: e.target.value })
                            }
                          />
                        </label>
                      </div>
                    </>
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
