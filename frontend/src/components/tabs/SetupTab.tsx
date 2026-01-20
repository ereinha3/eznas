import type { StackConfig } from '../types'

interface SetupTabProps {
  config: StackConfig
  errors: Record<string, string>
  touched: Record<string, boolean>
  onBlur: (field: string, value: string | number | null) => void
  onUpdatePaths: (field: keyof StackConfig['paths'], value: string) => void
  onUpdateRuntime: (field: 'user_id' | 'group_id' | 'timezone', value: string) => void
  onUpdateProxy: (patch: Partial<StackConfig['proxy']>) => void
  onUpdateUiPort: (value: string) => void
  onProxyHttpPort: (value: string) => void
  onProxyHttpsPort: (value: string) => void
  onProxyArgs: (value: string) => void
}

export function SetupTab({
  config,
  errors,
  touched,
  onBlur,
  onUpdatePaths,
  onUpdateRuntime,
  onUpdateProxy,
  onUpdateUiPort,
  onProxyHttpPort,
  onProxyHttpsPort,
  onProxyArgs,
}: SetupTabProps) {
  return (
    <>
      <h2>Storage Paths</h2>
      <div className="grid two">
        <label htmlFor="pool-path">
          Library pool path
          <input
            id="pool-path"
            className={touched.pool && errors.pool ? 'has-error' : ''}
            value={config.paths.pool}
            placeholder="/mnt/pool"
            onChange={(e) => onUpdatePaths('pool', e.target.value)}
            onBlur={() => onBlur('pool', config.paths.pool)}
          />
          {touched.pool && errors.pool && (
            <span className="field-error" role="alert">{errors.pool}</span>
          )}
        </label>
        <label htmlFor="scratch-path">
          Scratch volume (optional)
          <input
            id="scratch-path"
            value={config.paths.scratch ?? ''}
            placeholder="/mnt/scratch"
            onChange={(e) => onUpdatePaths('scratch', e.target.value)}
          />
        </label>
        <label htmlFor="appdata-path">
          Appdata path
          <input
            id="appdata-path"
            className={touched.appdata && errors.appdata ? 'has-error' : ''}
            value={config.paths.appdata}
            placeholder="/srv/appdata"
            onChange={(e) => onUpdatePaths('appdata', e.target.value)}
            onBlur={() => onBlur('appdata', config.paths.appdata)}
          />
          {touched.appdata && errors.appdata && (
            <span className="field-error" role="alert">{errors.appdata}</span>
          )}
        </label>
      </div>

      <h2>Runtime</h2>
      <div className="grid three">
        <label htmlFor="puid">
          PUID
          <input
            id="puid"
            type="number"
            value={config.runtime.user_id}
            onChange={(e) => onUpdateRuntime('user_id', e.target.value)}
          />
        </label>
        <label htmlFor="pgid">
          PGID
          <input
            id="pgid"
            type="number"
            value={config.runtime.group_id}
            onChange={(e) => onUpdateRuntime('group_id', e.target.value)}
          />
        </label>
        <label htmlFor="timezone">
          Timezone
          <input
            id="timezone"
            value={config.runtime.timezone}
            placeholder="UTC"
            onChange={(e) => onUpdateRuntime('timezone', e.target.value)}
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
              role="switch"
              aria-checked={config.proxy.enabled}
              data-service="traefik"
              checked={config.proxy.enabled}
              onChange={(e) => onUpdateProxy({ enabled: e.target.checked })}
            />
          </div>
        </div>
        <div className="service-fields">
          <label htmlFor="proxy-image">
            Image
            <input
              id="proxy-image"
              value={config.proxy.image}
              disabled={!config.proxy.enabled}
              onChange={(e) => onUpdateProxy({ image: e.target.value })}
            />
          </label>
          <label htmlFor="proxy-http-port">
            HTTP port
            <input
              id="proxy-http-port"
              type="number"
              className={touched['proxy-http-port'] && errors['proxy-http-port'] ? 'has-error' : ''}
              value={config.proxy.http_port}
              disabled={!config.proxy.enabled}
              onChange={(e) => onProxyHttpPort(e.target.value)}
              onBlur={() => onBlur('proxy-http-port', config.proxy.http_port)}
            />
            {touched['proxy-http-port'] && errors['proxy-http-port'] && (
              <span className="field-error" role="alert">{errors['proxy-http-port']}</span>
            )}
          </label>
          <label htmlFor="proxy-https-port">
            HTTPS port (optional)
            <input
              id="proxy-https-port"
              type="number"
              className={touched['proxy-https-port'] && errors['proxy-https-port'] ? 'has-error' : ''}
              value={config.proxy.https_port ?? ''}
              placeholder="Disabled"
              disabled={!config.proxy.enabled}
              onChange={(e) => onProxyHttpsPort(e.target.value)}
              onBlur={() => onBlur('proxy-https-port', config.proxy.https_port)}
            />
            {touched['proxy-https-port'] && errors['proxy-https-port'] && (
              <span className="field-error" role="alert">{errors['proxy-https-port']}</span>
            )}
          </label>
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={config.proxy.dashboard}
              disabled={!config.proxy.enabled}
              onChange={(e) => onUpdateProxy({ dashboard: e.target.checked })}
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
              disabled={!config.proxy.enabled}
              onChange={(e) => onProxyArgs(e.target.value)}
            />
          </label>
        </div>
      </div>

      <h2>Orchestrator UI</h2>
      <div className="grid one">
        <label htmlFor="ui-port">
          UI port
          <input
            id="ui-port"
            type="number"
            className={touched['ui-port'] && errors['ui-port'] ? 'has-error' : ''}
            value={config.ui.port}
            onChange={(e) => onUpdateUiPort(e.target.value)}
            onBlur={() => onBlur('ui-port', config.ui.port)}
          />
          {touched['ui-port'] && errors['ui-port'] && (
            <span className="field-error" role="alert">{errors['ui-port']}</span>
          )}
        </label>
      </div>
    </>
  )
}
