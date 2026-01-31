import { useState, useCallback } from 'react'
import type { StackConfig } from '../components/types'
import { ActionBar } from '../components/ActionBar'

interface SetupPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
}

export function SetupPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
}: SetupPageProps) {
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [touched, setTouched] = useState<Record<string, boolean>>({})

  const validateField = useCallback(
    (field: string, value: string | number | null): string | undefined => {
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

  const updatePaths = useCallback(
    (field: keyof StackConfig['paths'], value: string) => {
      const nextValue = field === 'scratch' ? (value.trim() === '' ? null : value) : value
      onChange({
        ...config,
        paths: {
          ...config.paths,
          [field]: nextValue,
        },
      })
    },
    [config, onChange],
  )

  const updateRuntime = useCallback(
    (field: 'user_id' | 'group_id' | 'timezone', value: string) => {
      onChange({
        ...config,
        runtime: {
          ...config.runtime,
          [field]: field === 'timezone' ? value : Number(value || '0'),
        },
      })
    },
    [config, onChange],
  )

  const updateUiPort = useCallback(
    (value: string) => {
      onChange({
        ...config,
        ui: {
          ...config.ui,
          port: Number(value || '0'),
        },
      })
    },
    [config, onChange],
  )

  return (
    <div className="setup-page">
      <h1>Setup</h1>

      <div className="card">
        <h2>Storage Paths</h2>
        <div className="grid two">
          <label htmlFor="pool-path">
            Library pool path
            <input
              id="pool-path"
              className={touched.pool && errors.pool ? 'has-error' : ''}
              value={config.paths.pool}
              placeholder="/mnt/pool"
              onChange={(e) => updatePaths('pool', e.target.value)}
              onBlur={() => handleBlur('pool', config.paths.pool)}
            />
            {touched.pool && errors.pool && (
              <span className="field-error" role="alert">
                {errors.pool}
              </span>
            )}
          </label>
          <label htmlFor="scratch-path">
            Scratch volume (optional)
            <input
              id="scratch-path"
              value={config.paths.scratch ?? ''}
              placeholder="/mnt/scratch"
              onChange={(e) => updatePaths('scratch', e.target.value)}
            />
          </label>
          <label htmlFor="appdata-path">
            Appdata path
            <input
              id="appdata-path"
              className={touched.appdata && errors.appdata ? 'has-error' : ''}
              value={config.paths.appdata}
              placeholder="/srv/appdata"
              onChange={(e) => updatePaths('appdata', e.target.value)}
              onBlur={() => handleBlur('appdata', config.paths.appdata)}
            />
            {touched.appdata && errors.appdata && (
              <span className="field-error" role="alert">
                {errors.appdata}
              </span>
            )}
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Runtime</h2>
        <div className="grid three">
          <label htmlFor="puid">
            PUID
            <input
              id="puid"
              type="number"
              value={config.runtime.user_id}
              onChange={(e) => updateRuntime('user_id', e.target.value)}
            />
          </label>
          <label htmlFor="pgid">
            PGID
            <input
              id="pgid"
              type="number"
              value={config.runtime.group_id}
              onChange={(e) => updateRuntime('group_id', e.target.value)}
            />
          </label>
          <label htmlFor="timezone">
            Timezone
            <input
              id="timezone"
              value={config.runtime.timezone}
              placeholder="UTC"
              onChange={(e) => updateRuntime('timezone', e.target.value)}
            />
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Orchestrator UI</h2>
        <div className="grid one">
          <label htmlFor="ui-port">
            UI port
            <input
              id="ui-port"
              type="number"
              className={touched['ui-port'] && errors['ui-port'] ? 'has-error' : ''}
              value={config.ui.port}
              onChange={(e) => updateUiPort(e.target.value)}
              onBlur={() => handleBlur('ui-port', config.ui.port)}
            />
            {touched['ui-port'] && errors['ui-port'] && (
              <span className="field-error" role="alert">
                {errors['ui-port']}
              </span>
            )}
          </label>
        </div>
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
