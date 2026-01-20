import { useState, useCallback, useMemo } from 'react'
import type { ServiceKey, StackConfig } from './types'
import { SetupTab } from './tabs/SetupTab'
import { ServicesTab } from './tabs/ServicesTab'
import { PreferencesTab } from './tabs/PreferencesTab'

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
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  const validateField = useCallback((field: string, value: string | number | null): string | undefined => {
    const strValue = value === null ? '' : String(value)
    if (field === 'pool' && !strValue.trim()) return 'Library pool path is required'
    if (field === 'appdata' && !strValue.trim()) return 'Appdata path is required'
    if (field.endsWith('-port')) {
      const port = Number(strValue)
      if (strValue.trim() !== '' && (isNaN(port) || port < 1 || port > 65535)) {
        return 'Port must be 1-65535'
      }
    }
    return undefined
  }, [])

  const handleBlur = useCallback((field: string, value: string | number | null) => {
    setTouched(t => ({ ...t, [field]: true }))
    const err = validateField(field, value)
    setErrors(e => {
      if (err) return { ...e, [field]: err }
      const { [field]: _, ...rest } = e
      return rest
    })
  }, [validateField])

  const duplicatePorts = useMemo(() => {
    const portMap = new Map<number, string[]>()
    Object.entries(config.services).forEach(([name, svc]) => {
      if (svc.enabled && svc.port) {
        const list = portMap.get(svc.port) || []
        list.push(name)
        portMap.set(svc.port, list)
      }
    })
    const dupes = new Set<number>()
    portMap.forEach((services, port) => {
      if (services.length > 1) dupes.add(port)
    })
    return dupes
  }, [config.services])

  const updatePaths = useCallback((field: keyof StackConfig['paths'], value: string) => {
    const nextValue =
      field === 'scratch' ? (value.trim() === '' ? null : value) : value
    onChange({
      ...config,
      paths: {
        ...config.paths,
        [field]: nextValue,
      },
    })
  }, [config, onChange])

  const updateRuntime = useCallback((field: 'user_id' | 'group_id' | 'timezone', value: string) => {
    onChange({
      ...config,
      runtime: {
        ...config.runtime,
        [field]: field === 'timezone' ? value : Number(value || '0'),
      },
    })
  }, [config, onChange])

  const updateProxy = useCallback((patch: Partial<StackConfig['proxy']>) => {
    onChange({
      ...config,
      proxy: {
        ...config.proxy,
        ...patch,
      },
    })
  }, [config, onChange])

  const updateUiPort = useCallback((value: string) => {
    onChange({
      ...config,
      ui: {
        ...config.ui,
        port: Number(value || '0'),
      },
    })
  }, [config, onChange])

  const updateService = useCallback(<K extends ServiceKey>(
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
  }, [config, onChange])

  const handleServicePort = useCallback((key: ServiceKey, value: string) => {
    const trimmed = value.trim()
    const parsed = Number(trimmed)
    const nextPort = trimmed === '' || Number.isNaN(parsed) ? null : parsed
    updateService(key, { port: nextPort })
  }, [updateService])

  const handleServiceProxy = useCallback((key: ServiceKey, value: string) => {
    const trimmed = value.trim()
    updateService(key, { proxy_url: trimmed === '' ? null : trimmed })
  }, [updateService])

  const handleProxyHttpPort = useCallback((value: string) => {
    const trimmed = value.trim()
    const parsed = Number(trimmed)
    if (trimmed === '') {
      updateProxy({ http_port: 80 })
      return
    }
    if (!Number.isNaN(parsed)) {
      updateProxy({ http_port: parsed })
    }
  }, [updateProxy])

  const handleProxyHttpsPort = useCallback((value: string) => {
    const trimmed = value.trim()
    if (trimmed === '') {
      updateProxy({ https_port: null })
      return
    }
    const parsed = Number(trimmed)
    if (!Number.isNaN(parsed)) {
      updateProxy({ https_port: parsed })
    }
  }, [updateProxy])

  const handleProxyArgs = useCallback((value: string) => {
    const entries = value
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => line.length > 0)
    updateProxy({ additional_args: entries })
  }, [updateProxy])

  const updateCategory = useCallback((
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
  }, [config, onChange])

  const handleSave = () => onSave(config)
  const handleValidate = () => onValidate(config)
  const handleRender = () => onRender(config)
  const handleApply = () => onApply(config)

  return (
    <div className="form-stack">
      {activeTab === 'setup' && (
        <SetupTab
          config={config}
          errors={errors}
          touched={touched}
          onBlur={handleBlur}
          onUpdatePaths={updatePaths}
          onUpdateRuntime={updateRuntime}
          onUpdateProxy={updateProxy}
          onUpdateUiPort={updateUiPort}
          onProxyHttpPort={handleProxyHttpPort}
          onProxyHttpsPort={handleProxyHttpsPort}
          onProxyArgs={handleProxyArgs}
        />
      )}

      {activeTab === 'services' && (
        <ServicesTab
          config={config}
          errors={errors}
          touched={touched}
          duplicatePorts={duplicatePorts}
          onBlur={handleBlur}
          onUpdateService={updateService}
          onServicePort={handleServicePort}
          onServiceProxy={handleServiceProxy}
          onUpdateCategory={updateCategory}
        />
      )}

      {activeTab === 'preferences' && (
        <PreferencesTab config={config} onChange={onChange} />
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
          {isApplying ? 'Applying...' : 'Apply stack'}
        </button>
      </div>

      {status && <div className={`status-alert ${statusVariant} visible`}>{status}</div>}
    </div>
  )
}
