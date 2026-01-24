import { useEffect, useState } from 'react'
import './App.css'
import {
  applyConfig,
  fetchStatus,
  loadConfig,
  renderConfig,
  saveConfig,
  validateConfig,
  fetchServiceCredentials,
  updateQbCredentials,
  createJellyfinUser,
  fetchHealth,
} from './api'
import { ConfigForm } from './components/ConfigForm'
import { Sidebar } from './components/Sidebar'
import type { CredentialsResponse, HealthResponse, ServiceStatus, StackConfig } from './components/types'

const DEFAULT_CONFIG: StackConfig = {
  version: 1,
  paths: { pool: '', scratch: null, appdata: '' },
  runtime: { user_id: 1000, group_id: 1000, timezone: 'UTC' },
  proxy: {
    enabled: false,
    image: 'traefik:v3.1',
    http_port: 80,
    https_port: null,
    dashboard: false,
    additional_args: [],
  },
  services: {
    qbittorrent: {
      enabled: true,
      port: 8080,
      proxy_url: null,
      stop_after_download: true,
      username: 'admin',
      password: 'adminadmin',
    },
    radarr: { enabled: true, port: 7878, proxy_url: null },
    sonarr: { enabled: true, port: 8989, proxy_url: null },
    prowlarr: { enabled: true, port: 9696, proxy_url: null, language_filter: false },
    jellyseerr: { enabled: true, port: 5055, proxy_url: null },
    jellyfin: { enabled: true, port: 8096, proxy_url: null },
    pipeline: { enabled: true, port: null, proxy_url: null },
  },
  download_policy: {
    categories: { radarr: 'movies', sonarr: 'tv' },
  },
  media_policy: {
    movies: { keep_audio: ['eng', 'und'], keep_subs: ['eng', 'forced'] },
  },
  quality: {
    preset: 'balanced',
    target_resolution: null,
    max_bitrate_mbps: null,
    preferred_container: 'mkv',
  },
  ui: { port: 8443 },
  users: [],
}

type TabKey = 'setup' | 'services' | 'preferences'

function App() {
  const [config, setConfig] = useState<StackConfig>(DEFAULT_CONFIG)
  const [status, setStatus] = useState('')
  const [statusVariant, setStatusVariant] = useState<'info' | 'success' | 'error'>('info')
  const [logEntries, setLogEntries] = useState<string[]>([])
  const [isApplying, setIsApplying] = useState(false)
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus[]>([])
  const [credentials, setCredentials] = useState<CredentialsResponse | null>(null)
  const [credentialsLoading, setCredentialsLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<TabKey>('setup')
  const [health, setHealth] = useState<HealthResponse | null>(null)


  const setStatusMessage = (message: string, variant: 'info' | 'success' | 'error' = 'info') => {
    setStatus(message)
    setStatusVariant(variant)
  }

  const loadCredentials = async () => {
    setCredentialsLoading(true)
    try {
      const data = await fetchServiceCredentials()
      setCredentials(data)
    } catch (error: any) {
      setStatusMessage(error.message || 'Failed to load credentials.', 'error')
      throw error
    } finally {
      setCredentialsLoading(false)
    }
  }

  const refreshConfig = async () => {
    try {
      const loaded = await loadConfig()
      setConfig(loaded)
      setStatusMessage('Configuration loaded.', 'success')
      await loadCredentials()
    } catch (error: any) {
      setStatusMessage(error.message || 'Failed to load config', 'error')
    }
  }

  const refreshStatus = async () => {
    try {
      const data = await fetchStatus()
      setServiceStatus(data.services)
    } catch (error) {
      // backend may not expose /api/status yet: ignore
    }
  }

  const handleUpdateQbCredentials = async (username: string, password: string) => {
    try {
      await updateQbCredentials({ username, password })
      setStatusMessage('qBittorrent credentials updated.', 'success')
      const updated = await loadConfig()
      setConfig(updated)
      await loadCredentials()
    } catch (error: any) {
      setStatusMessage(error.message || 'Failed to update qBittorrent credentials.', 'error')
      throw error
    }
  }

  const handleAddJellyfinUser = async (username: string, password: string) => {
    try {
      await createJellyfinUser({ username, password })
      setStatusMessage(`Created Jellyfin user ${username}.`, 'success')
      await loadCredentials()
    } catch (error: any) {
      setStatusMessage(error.message || 'Failed to create Jellyfin user.', 'error')
      throw error
    }
  }

  useEffect(() => {
    refreshConfig()
    refreshStatus()
  }, [])

  useEffect(() => {
    const refreshHealth = async () => {
      try {
        setHealth(await fetchHealth())
      } catch {
        // Health endpoint may not be available yet
      }
    }
    refreshHealth()
    const id = setInterval(refreshHealth, 10000)
    return () => clearInterval(id)
  }, [])

  const handleSave = async (cfg: StackConfig) => {
    try {
      const saved = await saveConfig(cfg)
      setConfig(saved)
      setStatusMessage('Configuration saved to stack.yaml.', 'success')
    } catch (error: any) {
      setStatusMessage(error.message || 'Save failed', 'error')
    }
  }

  const handleValidate = async (cfg: StackConfig) => {
    try {
      const result = await validateConfig(cfg)
      const issues = Object.entries(result.checks).filter(([, value]) => value !== 'ok')
      if (issues.length) {
        setStatusMessage(
          `Validation issues: ${issues.map(([k, v]) => `${k}=${v}`).join(', ')}`,
          'error',
        )
      } else {
        setStatusMessage('All validations passed.', 'success')
      }
    } catch (error: any) {
      setStatusMessage(error.message || 'Validation failed', 'error')
    }
  }

  const handleRender = async (cfg: StackConfig) => {
    try {
      const result = await renderConfig(cfg)
      setStatusMessage(
        `Rendered compose to ${result.compose_path} and env to ${result.env_path}`,
        'success',
      )
    } catch (error: any) {
      setStatusMessage(error.message || 'Render failed', 'error')
    }
  }

  const handleApply = async (cfg: StackConfig) => {
    setIsApplying(true)
    setLogEntries(['Running apply...'])
    setStatusMessage('Applying stack configuration...', 'info')
    let eventSource: EventSource | undefined

    try {
      const response = await applyConfig(cfg)
      appendLog(`Run ${response.run_id} started.`)

      eventSource = new EventSource(`/api/runs/${response.run_id}/events`)
      eventSource.addEventListener('stage', (evt) => {
        try {
          const data = JSON.parse(evt.data)
          appendLog(`${data.stage}: ${data.status}${data.detail ? ' - ' + data.detail : ''}`)
        } catch (err) {
          appendLog(`stage: ${evt.data}`)
        }
      })

      eventSource.addEventListener('status', (evt) => {
        try {
          const data = JSON.parse(evt.data)
          appendLog(
            `status: ${data.ok ? 'success' : 'failed'}${data.summary ? ' - ' + data.summary : ''}`,
          )
          setStatusMessage(
            data.ok ? 'Apply finished successfully.' : `Apply failed: ${data.summary || 'see log.'}`,
            data.ok ? 'success' : 'error',
          )
        } catch (err) {
          appendLog(`status: ${evt.data}`)
          setStatusMessage('Apply finished (status unknown).', 'info')
        } finally {
          eventSource?.close()
          setIsApplying(false)
          refreshConfig()
          refreshStatus()
        }
      })

      eventSource.addEventListener('error', () => {
        appendLog('event stream closed')
        setStatusMessage('Apply stream ended unexpectedly.', 'error')
        eventSource?.close()
        setIsApplying(false)
        refreshStatus()
        loadCredentials()
      })
    } catch (error: any) {
      appendLog(`Error: ${error.message}`)
      setStatusMessage(error.message || 'Apply failed to start', 'error')
      eventSource?.close()
      setIsApplying(false)
    }
  }

  const appendLog = (line: string) => {
    setLogEntries((prev) => [...prev, line])
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-content">
          <h1>NAS Stack Orchestrator</h1>
          <p>
            Configure storage, ports, service toggles, and policy—then let the orchestrator render,
            deploy, and converge the entire media automation stack.
          </p>
          <div className="pill-group">
            <span className="pill">Docker Compose</span>
            <span className="pill">Zero-touch bootstrap</span>
            <span className="pill">Live apply logs</span>
            <span className="pill">Language-aware pipeline</span>
          </div>
        </div>
      </header>

      <main className="grid-layout">
        <section className="panel">
          <nav className="tab-nav" role="tablist" aria-label="Configuration sections">
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'setup'}
              className={`tab-button${activeTab === 'setup' ? ' active' : ''}`}
              onClick={() => setActiveTab('setup')}
            >
              Setup
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'services'}
              className={`tab-button${activeTab === 'services' ? ' active' : ''}`}
              onClick={() => setActiveTab('services')}
            >
              Services
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeTab === 'preferences'}
              className={`tab-button${activeTab === 'preferences' ? ' active' : ''}`}
              onClick={() => setActiveTab('preferences')}
            >
              Preferences
            </button>
          </nav>
          <ConfigForm
            config={config}
            onChange={setConfig}
            onLoad={refreshConfig}
            onSave={handleSave}
            onValidate={handleValidate}
            onRender={handleRender}
            onApply={handleApply}
            status={status}
            statusVariant={statusVariant}
            isApplying={isApplying}
            activeTab={activeTab}
          />
        </section>
        <Sidebar
          config={config}
          serviceStatus={serviceStatus}
          health={health}
          credentials={credentials}
          credentialsLoading={credentialsLoading}
          logEntries={logEntries}
          onRefreshCredentials={loadCredentials}
          onUpdateQb={handleUpdateQbCredentials}
          onAddJellyfinUser={handleAddJellyfinUser}
        />
      </main>
    </div>
  )
}

export default App
