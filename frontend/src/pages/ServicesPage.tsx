import { useState, useCallback, useMemo } from 'react'
import type {
  CredentialsResponse,
  HealthResponse,
  ServiceKey,
  StackConfig,
} from '../components/types'
import { ServiceCard } from '../components/ServiceCard'
import { ActionBar } from '../components/ActionBar'
import { autoPopulateIndexers } from '../api'

const SERVICE_ORDER: ServiceKey[] = [
  'qbittorrent',
  'radarr',
  'sonarr',
  'prowlarr',
  'jellyseerr',
  'jellyfin',
  'pipeline',
]

interface ServicesPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
  credentials: CredentialsResponse | null
  health: HealthResponse | null
}

export function ServicesPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
  credentials,
  health,
}: ServicesPageProps) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [touched, setTouched] = useState<Record<string, boolean>>({})
  const [refreshing, setRefreshing] = useState(false)
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

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

  /** Map of port → list of service names using that port (only entries with conflicts). */
  const portConflictMap = useMemo(() => {
    const portMap = new Map<number, string[]>()
    Object.entries(config.services).forEach(([name, svc]) => {
      if (svc.enabled && svc.port) {
        const list = portMap.get(svc.port) || []
        list.push(name)
        portMap.set(svc.port, list)
      }
    })
    // Also check UI port and proxy ports for conflicts
    if (config.ui.port) {
      const list = portMap.get(config.ui.port) || []
      list.push('ui')
      portMap.set(config.ui.port, list)
    }
    if (config.proxy.enabled && config.proxy.http_port) {
      const list = portMap.get(config.proxy.http_port) || []
      list.push('proxy-http')
      portMap.set(config.proxy.http_port, list)
    }
    if (config.proxy.enabled && config.proxy.https_port) {
      const list = portMap.get(config.proxy.https_port) || []
      list.push('proxy-https')
      portMap.set(config.proxy.https_port, list)
    }
    const conflicts = new Map<number, string[]>()
    portMap.forEach((services, port) => {
      if (services.length > 1) conflicts.set(port, services)
    })
    return conflicts
  }, [config.services, config.ui.port, config.proxy])

  const handleServiceUpdate = useCallback(
    <K extends ServiceKey>(key: K, patch: Partial<StackConfig['services'][K]>) => {
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
    },
    [config, onChange],
  )

  const handleServicePort = useCallback(
    (key: ServiceKey, value: string) => {
      const trimmed = value.trim()
      const parsed = Number(trimmed)
      const nextPort = trimmed === '' || Number.isNaN(parsed) ? null : parsed
      handleServiceUpdate(key, { port: nextPort })
    },
    [handleServiceUpdate],
  )

  const handleServiceProxy = useCallback(
    (key: ServiceKey, value: string) => {
      const trimmed = value.trim()
      handleServiceUpdate(key, { proxy_url: trimmed === '' ? null : trimmed })
    },
    [handleServiceUpdate],
  )

  const handleRefreshIndexers = async () => {
    setRefreshing(true)
    setMessage(null)
    try {
      const result = await autoPopulateIndexers()
      setMessage({
        type: 'success',
        text: `Success: ${result.added.length} indexers added, ${result.skipped.length} skipped`,
      })
    } catch (error: any) {
      setMessage({
        type: 'error',
        text: error.message || 'Failed to refresh indexers',
      })
    } finally {
      setRefreshing(false)
    }
  }

  return (
    <div className="services-page">
      <h1>Services</h1>

      {message && (
        <div className={`status-alert ${message.type}`}>{message.text}</div>
      )}

      <div className="services-grid">
        {SERVICE_ORDER.map((key) => {
          // For *arr services, look for UI credentials (e.g., "radarr-ui" instead of "radarr")
          const credKey = ['radarr', 'sonarr', 'prowlarr'].includes(key) ? `${key}-ui` : key
          return (
            <ServiceCard
              key={key}
              serviceKey={key}
              service={config.services[key]}
              credentials={credentials?.services.find((c) => c.service === credKey)}
              health={health?.services.find((h) => h.name === key)}
              portConflictMap={portConflictMap}
            errors={errors}
            touched={touched}
            onUpdate={(patch) => handleServiceUpdate(key, patch)}
            onBlur={handleBlur}
            onServicePort={(value) => handleServicePort(key, value)}
            onServiceProxy={(value) => handleServiceProxy(key, value)}
            onRefreshIndexers={key === 'prowlarr' && !refreshing ? handleRefreshIndexers : undefined}
          />
        )})}
      </div>

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
