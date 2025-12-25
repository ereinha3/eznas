from pathlib import Path
import shutil
import textwrap

# Paths
root = Path(".")
old_ui = root / "ui"
frontend = root / "frontend"

if not frontend.exists():
    raise SystemExit("frontend project missing; run npm create vite@latest frontend")

# Move index.html into frontend/src as fallback? We'll embed components manually later.

# Create src/components directory structure
components = frontend / "src" / "components"
components.mkdir(exist_ok=True)

app_tsx = frontend / "src" / "App.tsx"
main_tsx = frontend / "src" / "main.tsx"
app_css = frontend / "src" / "App.css"
setup_proxy = frontend / "vite.config.ts"
api_ts = frontend / "src" / "api.ts"

# Write App.tsx content
app_tsx.write_text(textwrap.dedent("""
import { useEffect, useMemo, useState } from 'react';
import './App.css';
import { ConfigForm } from './components/ConfigForm';
import { ApplyLog } from './components/ApplyLog';
import { SummaryPanel } from './components/SummaryPanel';
import { loadConfig, saveConfig, validateConfig, renderConfig, applyConfig, fetchStatus } from './api';

import type { StackConfig, ValidationResult, ApplyResponse, ServiceStatus } from './components/types';

const DEFAULT_CONFIG: StackConfig = {
  version: 1,
  paths: {
    pool: '',
    scratch: '',
    appdata: '',
  },
  runtime: { user_id: 1000, group_id: 1000, timezone: 'UTC' },
  ports: {
    qbittorrent: 8080,
    radarr: 7878,
    sonarr: 8989,
    prowlarr: 9696,
    jellyseerr: 5055,
    jellyfin_http: 8096,
  },
  services: {
    qbittorrent: true,
    radarr: true,
    sonarr: true,
    prowlarr: true,
    jellyseerr: true,
    jellyfin: true,
    pipeline: true,
  },
  download_policy: {
    never_seed: true,
    categories: { radarr: 'movies', sonarr: 'tv', anime: 'anime' },
  },
  media_policy: {
    movies: { keep_audio: ['eng', 'und'], keep_subs: ['eng', 'forced'] },
    anime: { keep_audio: ['jpn', 'eng', 'und'], keep_subs: ['eng'] },
  },
  quality: { preset: 'balanced' },
  ui: { port: 8443 },
  credentials: { qbittorrent: { username: 'admin', password: null } },
  users: [],
};

function App() {
  const [config, setConfig] = useState<StackConfig>(DEFAULT_CONFIG);
  const [status, setStatus] = useState<string>('');
  const [statusVariant, setStatusVariant] = useState<'info' | 'success' | 'error'>('info');
  const [logEntries, setLogEntries] = useState<string[]>([]);
  const [isApplying, setIsApplying] = useState(false);
  const [serviceStatus, setServiceStatus] = useState<ServiceStatus[]>([]);

  const appendLog = (line: string) => {
    setLogEntries(prev => [...prev, line]);
  };

  const handleLoad = async () => {
    try {
      const loaded = await loadConfig();
      setConfig(loaded);
      setStatus('Configuration loaded.');
      setStatusVariant('success');
    } catch (error: any) {
      setStatus(error.message || 'Failed to load config');
      setStatusVariant('error');
    }
  };

  const handleSave = async (updated: StackConfig) => {
    try {
      const saved = await saveConfig(updated);
      setConfig(saved);
      setStatus('Configuration saved to stack.yaml.');
      setStatusVariant('success');
    } catch (error: any) {
      setStatus(error.message || 'Failed to save config');
      setStatusVariant('error');
    }
  };

  const handleValidate = async (cfg: StackConfig) => {
    try {
      const result = await validateConfig(cfg);
      const issues = Object.entries(result.checks).filter(([, v]) => v !== 'ok');
      if (issues.length) {
        setStatus(`Validation issues: ${issues.map(([k, v]) => `${k}=${v}`).join(', ')}`);
        setStatusVariant('error');
      } else {
        setStatus('All validations passed.');
        setStatusVariant('success');
      }
    } catch (error: any) {
      setStatus(error.message || 'Validation failed');
      setStatusVariant('error');
    }
  };

  const handleRender = async (cfg: StackConfig) => {
    try {
      const result = await renderConfig(cfg);
      setStatus(`Rendered compose to ${result.compose_path} and env to ${result.env_path}`);
      setStatusVariant('success');
    } catch (error: any) {
      setStatus(error.message || 'Render failed');
      setStatusVariant('error');
    }
  };

  const handleApply = async (cfg: StackConfig) => {
    setIsApplying(true);
    setLogEntries(['Running apply...']);
    setStatus('Applying stack configuration...');
    setStatusVariant('info');

    let eventSource: EventSource | undefined;
    try {
      const response = await applyConfig(cfg);
      appendLog(`Run ${response.run_id} started.`);

      eventSource = new EventSource(`/api/runs/${response.run_id}/events`);
      eventSource.addEventListener('stage', evt => {
        try {
          const data = JSON.parse(evt.data);
          appendLog(`${data.stage}: ${data.status}${data.detail ? ' - ' + data.detail : ''}`);
        } catch (err) {
          appendLog(`stage: ${evt.data}`);
        }
      });

      eventSource.addEventListener('status', evt => {
        try {
          const data = JSON.parse(evt.data);
          appendLog(`status: ${data.ok ? 'success' : 'failed'}${data.summary ? ' - ' + data.summary : ''}`);
          setStatus(data.ok ? 'Apply finished successfully.' : `Apply failed: ${data.summary || 'see log.'}`);
          setStatusVariant(data.ok ? 'success' : 'error');
        } catch (err) {
          appendLog(`status: ${evt.data}`);
          setStatus('Apply finished (status unknown).');
          setStatusVariant('info');
        } finally {
          eventSource?.close();
          setIsApplying(false);
          handleLoad();
          refreshStatus();
        }
      });

      eventSource.addEventListener('error', () => {
        appendLog('event stream closed');
        setStatus('Apply stream ended unexpectedly.');
        setStatusVariant('error');
        eventSource?.close();
        setIsApplying(false);
        refreshStatus();
      });
    } catch (error: any) {
      appendLog(`Error: ${error.message}`);
      setStatus(error.message || 'Apply failed to start');
      setStatusVariant('error');
      eventSource?.close();
      setIsApplying(false);
    }
  };

  const refreshStatus = async () => {
    try {
      const statusData = await fetchStatus();
      setServiceStatus(statusData.services);
    } catch (error) {
      console.warn('Failed to fetch status', error);
    }
  };

  useEffect(() => {
    handleLoad();
    refreshStatus();
  }, []);

  return (
    <div className="app-shell">
      <header className="hero">
        <div className="hero-content">
          <h1>NAS Stack Orchestrator</h1>
          <p>
            Orchestrate your entire media automation pipeline. Configure storage, toggle services,
            render Docker Compose, and monitor live apply runs in one beautiful interface.
          </p>
          <div className="pill-group">
            <span className="pill">Docker Compose</span>
            <span className="pill">FastAPI</span>
            <span className="pill">Zero-Touch Bootstrap</span>
            <span className="pill">Language-Aware Pipeline</span>
          </div>
        </div>
      </header>

      <main className="grid-layout">
        <section className="panel">
          <ConfigForm
            config={config}
            onChange={setConfig}
            onLoad={handleLoad}
            onSave={handleSave}
            onValidate={handleValidate}
            onRender={handleRender}
            onApply={handleApply}
            status={status}
            statusVariant={statusVariant}
            isApplying={isApplying}
          />
        </section>
        <aside className="sidebar">
          <SummaryPanel config={config} serviceStatus={serviceStatus} />
          <ApplyLog entries={logEntries} />
        </aside>
      </main>
    </div>
  );
}

export default App;
""")

print("App.tsx written")
PY
