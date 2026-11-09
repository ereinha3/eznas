import { useEffect, useRef, useState } from 'react'
import './App.css'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import {
  applyConfig,
  loadConfig,
  saveConfig,
  validateConfig,
  fetchServiceCredentials,
  fetchHealth,
  fetchActiveRun,
} from './api'
import { LeftNavigation, type PageKey } from './components/LeftNavigation'
import { HomePage } from './pages/HomePage'
import { LibraryPage } from './pages/LibraryPage'
import { SystemPage } from './pages/SystemPage'
import { ActivityPage } from './pages/ActivityPage'
import { LoginPage } from './pages/LoginPage'
import { WizardPage } from './pages/WizardPage'
import { DeployBar } from './components/common/DeployBar'
import { ComplexityToggle } from './components/common/ComplexityToggle'
// SettingsLock will be used on the dedicated Settings page
import { useComplexityMode } from './hooks/useComplexityMode'
import type { CredentialsResponse, HealthResponse, StackConfig } from './components/types'

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
      password: '',
    },
    radarr: { enabled: true, port: 7878, proxy_url: null },
    sonarr: { enabled: true, port: 8989, proxy_url: null },
    prowlarr: { enabled: true, port: 9696, proxy_url: null, language_filter: false },
    jellyseerr: { enabled: true, port: 5055, proxy_url: null },
    jellyfin: { enabled: true, port: 8096, proxy_url: null },
    bazarr: { enabled: true, port: 6767, proxy_url: null },
    flaresolverr: { enabled: false, port: 8191, proxy_url: null },
    pipeline: { enabled: true, port: null, proxy_url: null, recommender: { enabled: false, refresh_interval_hours: 24, min_vote_count: 10, use_compressed_index: true, min_watched_for_profile: 3, max_recommendations_per_user: 50, because_you_watched_count: 10, owned_weight: 1.5, collaborative_enabled: false, collaborative_weight: 0.3, auto_request_enabled: false, auto_request_max_per_day: 2 } },
    gluetun: { enabled: false, port: null, proxy_url: null, wireguard_config: '' },
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

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

// Main authenticated app content
function AuthenticatedApp() {
  const { logout, user, sudoActive } = useAuth()
  const [config, setConfig] = useState<StackConfig>(DEFAULT_CONFIG)
  const [savedConfig, setSavedConfig] = useState<StackConfig>(DEFAULT_CONFIG)
  const [logEntries, setLogEntries] = useState<string[]>([])
  const [isApplying, setIsApplying] = useState(false)
  const [credentials, setCredentials] = useState<CredentialsResponse | null>(null)
  const [activePage, setActivePage] = useState<PageKey>('home')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [statusMsg, setStatusMsg] = useState<{ text: string; variant: 'info' | 'success' | 'error' } | null>(null)
  const activeEventSourceRef = useRef<{ runId: string; es: EventSource } | null>(null)
  const { isAdvanced, toggleMode } = useComplexityMode()

  const hasChanges = !deepEqual(config, savedConfig)

  const loadCredentials = async () => {
    try {
      const data = await fetchServiceCredentials()
      setCredentials(data)
    } catch { /* ignore */ }
  }

  const refreshConfig = async () => {
    try {
      const loaded = await loadConfig()
      setConfig(loaded)
      setSavedConfig(loaded)
      await loadCredentials()
    } catch (error: any) {
      setStatusMsg({ text: error.message || 'Failed to load config', variant: 'error' })
    }
  }

  useEffect(() => { refreshConfig() }, [])

  useEffect(() => {
    const refreshHealth = async () => {
      try { setHealth(await fetchHealth()) } catch { /* ignore */ }
    }
    refreshHealth()
    const id = setInterval(refreshHealth, 10000)
    return () => clearInterval(id)
  }, [])

  const appendLog = (line: string) => {
    setLogEntries((prev) => [...prev, line])
  }

  const connectToRun = (runId: string, isReconnect: boolean = false) => {
    if (activeEventSourceRef.current?.runId === runId) return
    activeEventSourceRef.current?.es.close()

    setIsApplying(true)
    localStorage.setItem('nas_active_run_id', runId)
    if (!isReconnect) {
      setLogEntries(['Deploying...'])
      setStatusMsg({ text: 'Deploying stack configuration...', variant: 'info' })
    } else {
      appendLog(`Reconnected to deployment ${runId}...`)
    }

    const token = localStorage.getItem('nas_orchestrator_token')
    const eventUrl = token
      ? `/api/runs/${runId}/events?token=${encodeURIComponent(token)}`
      : `/api/runs/${runId}/events`
    const eventSource = new EventSource(eventUrl)
    activeEventSourceRef.current = { runId, es: eventSource }

    eventSource.addEventListener('stage', (evt) => {
      try {
        const data = JSON.parse(evt.data)
        appendLog(`${data.stage}: ${data.status}${data.detail ? ' - ' + data.detail : ''}`)
      } catch {
        appendLog(`stage: ${evt.data}`)
      }
    })

    eventSource.addEventListener('status', (evt) => {
      try {
        const data = JSON.parse(evt.data)
        appendLog(`Deploy ${data.ok ? 'succeeded' : 'failed'}${data.summary ? ' - ' + data.summary : ''}`)
        setStatusMsg({
          text: data.ok ? 'Deploy finished successfully.' : `Deploy failed: ${data.summary || 'see log.'}`,
          variant: data.ok ? 'success' : 'error',
        })
      } catch {
        appendLog(`status: ${evt.data}`)
      } finally {
        eventSource.close()
        activeEventSourceRef.current = null
        setIsApplying(false)
        localStorage.removeItem('nas_active_run_id')
        refreshConfig()
      }
    })

    eventSource.addEventListener('error', () => {
      appendLog('Deploy stream closed.')
      setStatusMsg({ text: 'Deploy stream ended unexpectedly.', variant: 'error' })
      eventSource.close()
      activeEventSourceRef.current = null
      setIsApplying(false)
      localStorage.removeItem('nas_active_run_id')
      loadCredentials()
    })
  }

  // Reconnect to in-progress run on mount
  useEffect(() => {
    const checkActiveRun = async () => {
      try {
        const { active, run_id } = await fetchActiveRun()
        if (active && run_id) {
          connectToRun(run_id, true)
        } else {
          localStorage.removeItem('nas_active_run_id')
        }
      } catch { /* ignore */ }
    }
    checkActiveRun()
  }, [])

  const handleDeploy = async (cfg: StackConfig) => {
    try {
      // Validate first
      const result = await validateConfig(cfg)
      const issues = Object.entries(result.checks).filter(([, value]) => value !== 'ok')
      if (issues.length) {
        setStatusMsg({
          text: `Cannot deploy: ${issues.map(([k, v]) => `${k}: ${v}`).join(', ')}`,
          variant: 'error',
        })
        return
      }

      // Save config
      const saved = await saveConfig(cfg)
      setConfig(saved)
      setSavedConfig(saved)

      // Apply
      const response = await applyConfig(saved)
      connectToRun(response.run_id, !!response.already_running)
    } catch (error: any) {
      appendLog(`Error: ${error.message}`)
      setStatusMsg({ text: error.message || 'Deploy failed to start', variant: 'error' })
      setIsApplying(false)
    }
  }

  const handleDiscard = () => {
    setConfig(savedConfig)
    setStatusMsg(null)
  }

  return (
    <div className="app-shell">
      <div className="left-nav-column">
        <LeftNavigation activePage={activePage} onNavigate={setActivePage} />
      </div>

      <div className="main-column">
        <header className="top-bar">
          <div className="top-bar-left">
            {health && (
              <div className={`health-indicator ${health.status}`}>
                <span className="health-indicator-dot" />
                <span>{health.status === 'healthy' ? 'all systems healthy' : health.status}</span>
              </div>
            )}
          </div>
          <div className="top-bar-right">
            <ComplexityToggle isAdvanced={isAdvanced} onToggle={toggleMode} />
            <div className="user-controls">
              <span className="username">{user?.username}</span>
              {sudoActive && <span className="sudo-badge">SUDO</span>}
              <button onClick={logout} className="logout-button">Logout</button>
            </div>
          </div>
        </header>

        <div className="page-container">
          {statusMsg && (
            <div className={`status-alert ${statusMsg.variant}`}>
              {statusMsg.text}
              <button className="status-dismiss" onClick={() => setStatusMsg(null)}>&times;</button>
            </div>
          )}

          {activePage === 'home' && (
            <HomePage config={config} health={health} credentials={credentials} />
          )}
          {activePage === 'library' && (
            <LibraryPage config={config} onChange={setConfig} isAdvanced={isAdvanced} />
          )}
          {activePage === 'system' && (
            <SystemPage config={config} onChange={setConfig} health={health} credentials={credentials} isAdvanced={isAdvanced} />
          )}
          {activePage === 'activity' && (
            <ActivityPage logEntries={logEntries} />
          )}
          {activePage === 'users' && (
            <div className="page-placeholder">
              <h2>Users</h2>
              <p className="page-description">User management and device enrollment. Coming soon.</p>
            </div>
          )}
          {activePage === 'settings' && (
            <div className="page-placeholder">
              <h2>Settings</h2>
              <p className="page-description">Low-level configuration. Coming soon.</p>
            </div>
          )}
        </div>
      </div>

      <DeployBar
        hasChanges={hasChanges}
        onDeploy={handleDeploy}
        onDiscard={handleDiscard}
        config={config}
        isApplying={isApplying}
      />
    </div>
  )
}

// Wrapper that handles auth state and routing
function AppWithAuth() {
  const { isAuthenticated, isLoading, checkSession } = useAuth()
  const [defaultCreds, setDefaultCreds] = useState<{ username: string; password: string } | undefined>(undefined)
  const [needsSetup, setNeedsSetup] = useState(false)
  const [isCheckingSetup, setIsCheckingSetup] = useState(true)

  useEffect(() => {
    const checkSetup = async () => {
      try {
        const response = await fetch('/api/setup/status')
        const data = await response.json()
        if (data.needs_setup) setNeedsSetup(true)
        if (data.default_password) setDefaultCreds({ username: 'admin', password: data.default_password })
      } catch { /* ignore */ }
      finally { setIsCheckingSetup(false) }
    }
    checkSetup()
  }, [])

  if (isLoading || isCheckingSetup) {
    return (
      <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', background: '#1a1a2e' }}>
        <div style={{ color: '#888' }}>Loading...</div>
      </div>
    )
  }

  if (needsSetup) return <WizardPage onSetupComplete={() => window.location.reload()} />
  if (!isAuthenticated) return <LoginPage onLoginSuccess={checkSession} defaultCredentials={defaultCreds} />
  return <AuthenticatedApp />
}

function App() {
  return (
    <AuthProvider>
      <AppWithAuth />
    </AuthProvider>
  )
}

export default App
