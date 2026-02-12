import { useState, useCallback } from 'react'
import type { StackConfig } from '../components/types'
import { ActionBar } from '../components/ActionBar'

interface ProxyPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
  onNavigate?: (page: 'services') => void
}

const SERVICE_LABELS: Record<string, string> = {
  qbittorrent: 'qBittorrent',
  radarr: 'Radarr',
  sonarr: 'Sonarr',
  prowlarr: 'Prowlarr',
  jellyseerr: 'Jellyseerr',
  jellyfin: 'Jellyfin',
}

export function ProxyPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
  onNavigate,
}: ProxyPageProps) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  const validateField = useCallback(
    (field: string, value: string | number | null): string | undefined => {
      const strValue = value === null ? '' : String(value)
      if (field.endsWith('-port')) {
        const port = Number(strValue)
        if (strValue.trim() !== '' && (isNaN(port) || port < 1 || port > 65535)) {
          return 'Port must be 1-65535'
        }
      }
      return undefined
    },
    [],
  )

  const handleBlur = useCallback(
    (field: string, value: string | number | null) => {
      setTouched((t) => ({ ...t, [field]: true }))
      const err = validateField(field, value)
      setErrors((e) => {
        if (err) return { ...e, [field]: err }
        const { [field]: _, ...rest } = e
        return rest
      })
    },
    [validateField],
  )

  const updateProxy = useCallback(
    (patch: Partial<StackConfig['proxy']>) => {
      onChange({
        ...config,
        proxy: {
          ...config.proxy,
          ...patch,
        },
      })
    },
    [config, onChange],
  )

  const handleProxyHttpPort = useCallback(
    (value: string) => {
      const trimmed = value.trim()
      const parsed = Number(trimmed)
      if (trimmed === '') {
        updateProxy({ http_port: 80 })
      } else if (!Number.isNaN(parsed)) {
        updateProxy({ http_port: parsed })
      }
    },
    [updateProxy],
  )

  const handleProxyHttpsPort = useCallback(
    (value: string) => {
      const trimmed = value.trim()
      const parsed = Number(trimmed)
      if (trimmed === '') {
        updateProxy({ https_port: null })
      } else if (!Number.isNaN(parsed)) {
        updateProxy({ https_port: parsed })
      }
    },
    [updateProxy],
  )

  const handleProxyArgs = useCallback(
    (value: string) => {
      const lines = value
        .split('\n')
        .map((l) => l.trim())
        .filter(Boolean)
      updateProxy({ additional_args: lines })
    },
    [updateProxy],
  )

  const enabledServices = Object.entries(config.services).filter(
    ([, svc]) => svc.enabled && svc.port,
  )

  return (
    <div className="proxy-page">
      <h1>Reverse Proxy Configuration</h1>

      <div className="info-card">
        <h3>What is Traefik?</h3>
        <p>
          Traefik is a reverse proxy that provides custom URLs, HTTPS certificates, and centralized
          routing for your services.
        </p>
        <ul>
          <li>
            <strong>Custom URLs:</strong> radarr.example.com instead of localhost:7878
          </li>
          <li>
            <strong>HTTPS/SSL:</strong> Automatic Let's Encrypt certificates
          </li>
          <li>
            <strong>Security:</strong> Hide service ports from public exposure
          </li>
        </ul>
        <div className="warning-box">
          <strong>Note:</strong> Proxy URLs require DNS configuration. Services remain accessible
          via ports even when proxy is enabled.
        </div>
      </div>

      <div className="card">
        <h2>Traefik Settings</h2>
        <label className="checkbox-inline">
          <input
            type="checkbox"
            checked={config.proxy.enabled}
            onChange={(e) => updateProxy({ enabled: e.target.checked })}
          />
          <span>Enable Traefik reverse proxy</span>
        </label>

        {config.proxy.enabled && (
          <div className="form-stack">
            <label htmlFor="proxy-image">
              Image
              <input
                id="proxy-image"
                value={config.proxy.image}
                onChange={(e) => updateProxy({ image: e.target.value })}
              />
            </label>
            <label htmlFor="proxy-http-port">
              HTTP port
              <input
                id="proxy-http-port"
                type="number"
                className={
                  touched['proxy-http-port'] && errors['proxy-http-port'] ? 'has-error' : ''
                }
                value={config.proxy.http_port}
                onChange={(e) => handleProxyHttpPort(e.target.value)}
                onBlur={() => handleBlur('proxy-http-port', config.proxy.http_port)}
              />
              {touched['proxy-http-port'] && errors['proxy-http-port'] && (
                <span className="field-error" role="alert">
                  {errors['proxy-http-port']}
                </span>
              )}
            </label>
            <label htmlFor="proxy-https-port">
              HTTPS port (optional)
              <input
                id="proxy-https-port"
                type="number"
                className={
                  touched['proxy-https-port'] && errors['proxy-https-port'] ? 'has-error' : ''
                }
                value={config.proxy.https_port ?? ''}
                placeholder="Disabled"
                onChange={(e) => handleProxyHttpsPort(e.target.value)}
                onBlur={() => handleBlur('proxy-https-port', config.proxy.https_port)}
              />
              {touched['proxy-https-port'] && errors['proxy-https-port'] && (
                <span className="field-error" role="alert">
                  {errors['proxy-https-port']}
                </span>
              )}
            </label>
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={config.proxy.dashboard}
                onChange={(e) => updateProxy({ dashboard: e.target.checked })}
              />
              <span>Expose Traefik dashboard (insecure)</span>
            </label>
            <label htmlFor="proxy-args">
              Extra command arguments (one per line)
              <textarea
                id="proxy-args"
                rows={3}
                value={config.proxy.additional_args.join('\n')}
                placeholder="--certificatesresolvers.myresolver.acme.email=you@example.com"
                onChange={(e) => handleProxyArgs(e.target.value)}
              />
            </label>
          </div>
        )}
      </div>

      {enabledServices.length > 0 && (
        <div className="card">
          <h2>Service Proxy URL Mapping</h2>
          <p className="field-hint">
            Configure proxy URLs for each service on the{' '}
            {onNavigate ? (
              <button
                type="button"
                className="inline-link"
                onClick={() => onNavigate('services')}
              >
                Services page
              </button>
            ) : (
              'Services page'
            )}
            .
          </p>
          <div className="proxy-mapping-table">
            <table>
              <thead>
                <tr>
                  <th>Service</th>
                  <th>Port</th>
                  <th>Proxy URL</th>
                </tr>
              </thead>
              <tbody>
                {enabledServices.map(([key, svc]) => (
                  <tr key={key}>
                    <td>{SERVICE_LABELS[key] || key}</td>
                    <td>{svc.port}</td>
                    <td>{svc.proxy_url || <em className="empty-state">Not configured</em>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <ActionBar
        config={config}
        onSave={onSave}
        onValidate={onValidate}
        onApply={onApply}
        onBuild={onBuild}
        isApplying={isApplying}
      />
    </div>
  )
}
