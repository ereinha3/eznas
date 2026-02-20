import { useEffect, useState } from 'react'
import './App.css'
import { AuthProvider, useAuth } from './contexts/AuthContext'
import {
  applyConfig,
  loadConfig,
  saveConfig,
  validateConfig,
  fetchServiceCredentials,
  fetchHealth,
} from './api'
import { LeftNavigation, type PageKey } from './components/LeftNavigation'
import { DashboardPage } from './pages/DashboardPage'
import { ServicesPage } from './pages/ServicesPage'
import { SettingsPage } from './pages/SettingsPage'
import { LogsPage } from './pages/LogsPage'
import { LoginPage } from './pages/LoginPage'
import { WizardPage } from './pages/WizardPage'
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

// Main authenticated app content
function AuthenticatedApp() {
  const { logout, user, sudoActive } = useAuth()
  const [config, setConfig] = useState<StackConfig>(DEFAULT_CONFIG)
  const [status, setStatus] = useState('')
  const [statusVariant, setStatusVariant] = useState<'info' | 'success' | 'error'>('info')
  const [logEntries, setLogEntries] = useState<string[]>([])
  const [isApplying, setIsApplying] = useState(false)
  const [credentials, setCredentials] = useState<CredentialsResponse | null>(null)
  const [activePage, setActivePage] = useState<PageKey>('dashboard')
  const [health, setHealth] = useState<HealthResponse | null>(null)

  const setStatusMessage = (message: string, variant: 'info' | 'success' | 'error' = 'info') => {
    setStatus(message)
    setStatusVariant(variant)
  }

  const loadCredentials = async () => {
    try {
      const data = await fetchServiceCredentials()
      setCredentials(data)
    } catch (error: any) {
      setStatusMessage(error.message || 'Failed to load credentials.', 'error')
      throw error
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

  useEffect(() => {
    refreshConfig()
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

  const handleBuild = async () => {
    try {
      setStatusMessage('Building orchestrator image...', 'info')
      const response = await fetch('/api/build', { method: 'POST' })
      const result = await response.json()

      if (result.ok) {
        setStatusMessage('Image built successfully!', 'success')
      } else {
        setStatusMessage(`Build failed: ${result.message}`, 'error')
      }
    } catch (error: any) {
      setStatusMessage(error.message || 'Build failed', 'error')
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
        }
      })

      eventSource.addEventListener('error', () => {
        appendLog('event stream closed')
        setStatusMessage('Apply stream ended unexpectedly.', 'error')
        eventSource?.close()
        setIsApplying(false)
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

  const handleLogout = async () => {
    await logout()
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
        <div className="user-controls">
          <div className="user-info">
            <span className="username">{user?.username}</span>
            {sudoActive && <span className="sudo-badge">SUDO</span>}
          </div>
          <button onClick={handleLogout} className="logout-button">
            Logout
          </button>
        </div>
      </header>

      <main className="main-layout">
        <div className="left-nav-column">
          <LeftNavigation activePage={activePage} onNavigate={setActivePage} />
        </div>

        <div className="page-container">
          {status && (
            <div className={`status-alert ${statusVariant}`}>
              {status}
            </div>
          )}

          {activePage === 'dashboard' && (
            <DashboardPage
              config={config}
              health={health}
            />
          )}
          {activePage === 'services' && (
            <ServicesPage
              config={config}
              onChange={setConfig}
              onSave={handleSave}
              onValidate={handleValidate}
              onApply={handleApply}
              onBuild={handleBuild}
              isApplying={isApplying}
              credentials={credentials}
              health={health}
            />
          )}
          {activePage === 'settings' && (
            <SettingsPage
              config={config}
              onChange={setConfig}
              onSave={handleSave}
              onValidate={handleValidate}
              onApply={handleApply}
              onBuild={handleBuild}
              isApplying={isApplying}
            />
          )}
          {activePage === 'logs' && (
            <LogsPage logEntries={logEntries} />
          )}
        </div>
      </main>
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
    // Check if first-time setup is needed
    const checkSetup = async () => {
      try {
        const response = await fetch('/api/setup/status')
        const data = await response.json()
        
        if (data.needs_setup) {
          setNeedsSetup(true)
        }

        // Show default credentials if available (regardless of needs_setup)
        if (data.default_password) {
          setDefaultCreds({ username: 'admin', password: data.default_password })
        }
      } catch {
        // Ignore errors
      } finally {
        setIsCheckingSetup(false)
      }
    }
    
    // Always check setup status on mount
    checkSetup()
  }, [])

  const handleLoginSuccess = () => {
    checkSession()
  }

  const handleSetupComplete = () => {
    // Reload to clear setup state and show login
    window.location.reload()
  }

  if (isLoading || isCheckingSetup) {
    return (
      <div style={{ 
        minHeight: '100vh', 
        display: 'flex', 
        alignItems: 'center', 
        justifyContent: 'center',
        background: '#1a1a2e'
      }}>
        <div style={{ color: '#888' }}>Loading...</div>
      </div>
    )
  }

  if (needsSetup) {
    return <WizardPage onSetupComplete={handleSetupComplete} />
  }

  if (!isAuthenticated) {
    return (
      <LoginPage 
        onLoginSuccess={handleLoginSuccess}
        defaultCredentials={defaultCreds}
      />
    )
  }

  return <AuthenticatedApp />
}

// Root app with provider
function App() {
  return (
    <AuthProvider>
      <AppWithAuth />
    </AuthProvider>
  )
}

export default App
