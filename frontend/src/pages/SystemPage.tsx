import { useState, useRef } from 'react'
import type { CredentialsResponse, HealthResponse, ServiceKey, StackConfig } from '../components/types'
import { validatePath } from '../api'
import { SERVICE_DISPLAY, SERVICE_ORDER } from '../constants'
import { ServiceLinkDropdown } from '../components/common/ServiceLinkDropdown'
import { CollapsibleSection } from '../components/common/CollapsibleSection'
import { useAuth } from '../contexts/AuthContext'

interface SystemPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  health: HealthResponse | null
  credentials: CredentialsResponse | null
  isAdvanced: boolean
}

interface PathValidation {
  valid: boolean | null
  message: string
  isValidating: boolean
}

export function SystemPage({ config, onChange, health, isAdvanced }: SystemPageProps) {
  const { sudoActive } = useAuth()
  const locked = !sudoActive

  const [pathValidation, setPathValidation] = useState<Record<string, PathValidation>>({})
  const debounceTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({})

  const handleServiceToggle = (key: ServiceKey, enabled: boolean) => {
    if (locked) return
    const services = { ...config.services }
    services[key] = { ...services[key], enabled } as any
    onChange({ ...config, services })
  }

  const handlePathChange = (field: 'pool' | 'scratch' | 'appdata', value: string) => {
    if (locked) return
    const paths = { ...config.paths }
    if (field === 'scratch') { paths.scratch = value || null } else { (paths as any)[field] = value }
    onChange({ ...config, paths })

    if (debounceTimers.current[field]) clearTimeout(debounceTimers.current[field])
    if (!value.trim()) {
      setPathValidation((prev) => ({ ...prev, [field]: { valid: null, message: '', isValidating: false } }))
      return
    }
    setPathValidation((prev) => ({ ...prev, [field]: { valid: null, message: '', isValidating: true } }))
    debounceTimers.current[field] = setTimeout(async () => {
      try {
        const result = await validatePath(value, true, false, false)
        setPathValidation((prev) => ({ ...prev, [field]: { valid: result.valid, message: result.warning || result.error || '', isValidating: false } }))
      } catch {
        setPathValidation((prev) => ({ ...prev, [field]: { valid: false, message: 'Validation failed', isValidating: false } }))
      }
    }, 500)
  }

  return (
    <div className={`system-page ${locked ? 'system-locked' : ''}`}>
      {/* 5x2 Service Grid */}
      <div className="system-services-section">
        <div className="section-header">
          <h3>Services <span className="text-muted">{SERVICE_ORDER.filter(k => config.services[k]?.enabled).length} running</span></h3>
        </div>
        <div className="system-service-grid">
          {SERVICE_ORDER.map((key) => {
            const svc = config.services[key]
            if (!svc) return null
            const display = SERVICE_DISPLAY[key]
            const healthStatus = health?.services.find((h) => h.name === key)
            const port = svc.port ? `:${svc.port}` : ''

            return (
              <div key={key} className={`system-service-card ${svc.enabled ? '' : 'disabled'}`}>
                <div className="svc-card-top">
                  <span className="svc-card-icon">{display.icon}</span>
                  <span className={`health-dot ${healthStatus?.healthy === true ? 'healthy' : healthStatus?.healthy === false ? 'unhealthy' : 'unknown'}`} />
                </div>
                <span className="svc-card-name">{display.name}</span>
                <span className="svc-card-tech">{display.technicalName}{port}</span>
                <div className="svc-card-bottom">
                  {svc.enabled && svc.port && (
                    <ServiceLinkDropdown serviceName={display.technicalName} port={svc.port} proxyUrl={svc.proxy_url} />
                  )}
                  {!svc.enabled && <span />}
                  <label className="toggle-switch">
                    <input type="checkbox" checked={svc.enabled} onChange={(e) => handleServiceToggle(key, e.target.checked)} disabled={locked || display.required} />
                    <span className="toggle-slider" />
                  </label>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Storage + VPN row */}
      <div className="system-infra-row">
        <div className="card system-storage-card">
          <h3>Storage paths</h3>
          {(['pool', 'scratch'] as const).map((field) => {
            const value = field === 'scratch' ? (config.paths.scratch ?? '') : (config.paths as any)[field] ?? ''
            const v = pathValidation[field]
            const label = field === 'pool' ? 'media library' : 'downloads'
            return (
              <div key={field} className="storage-path-row">
                <div className="storage-path-info">
                  <span className="storage-path-label">{label}</span>
                  <span className="storage-path-value">{value || 'not set'}</span>
                </div>
                <span className={`health-dot ${v?.valid === true ? 'healthy' : v?.valid === false ? 'unhealthy' : value ? 'unknown' : 'unknown'}`} />
                <span className={`storage-path-status ${v?.valid === true ? 'ok' : v?.valid === false ? 'err' : ''}`}>
                  {v?.isValidating ? 'checking...' : v?.valid === true ? 'valid' : v?.valid === false ? v.message : ''}
                </span>
              </div>
            )
          })}
          {isAdvanced && (
            <div className="storage-path-row">
              <div className="storage-path-info">
                <span className="storage-path-label">configs</span>
                <span className="storage-path-value">{config.paths.appdata || 'not set'}</span>
              </div>
              <span className="health-dot unknown" />
            </div>
          )}
        </div>

        <div className="card system-vpn-card">
          <div className="vpn-header">
            <h3>VPN · WireGuard</h3>
            <div className="vpn-status">
              <span className={`health-dot ${config.services.gluetun?.enabled ? 'healthy' : 'unknown'}`} />
              <span className="text-sm">{config.services.gluetun?.enabled ? 'connected' : 'disabled'}</span>
            </div>
          </div>
          <div className="vpn-config-preview">
            {(config.services.gluetun as any)?.wireguard_config
              ? (config.services.gluetun as any).wireguard_config.split('\n').slice(0, 8).map((line: string, i: number) => (
                  <div key={i}>{line}</div>
                ))
              : <span className="text-muted">no config set</span>
            }
          </div>
        </div>
      </div>

      {/* Advanced */}
      {isAdvanced && (
        <CollapsibleSection title="Advanced" subtitle="ports, UID/GID, timezone, proxy">
          <div className="system-advanced-grid">
            {[
              ['UID / GID', `${config.runtime.user_id} / ${config.runtime.group_id}`],
              ['Timezone', config.runtime.timezone],
              ['Proxy', config.proxy.enabled ? `HTTP :${config.proxy.http_port}` : 'none'],
              ['Backfill', config.services.pipeline.backfill?.enabled ? 'enabled' : 'disabled'],
              ['Recommender', config.services.pipeline.recommender?.enabled ? 'enabled' : 'disabled'],
            ].map(([k, v]) => (
              <div key={k} className="advanced-kv">
                <span className="advanced-key">{k}</span>
                <span className="advanced-val">{v}</span>
              </div>
            ))}
          </div>
        </CollapsibleSection>
      )}
    </div>
  )
}
