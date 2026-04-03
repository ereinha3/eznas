import { useState, useEffect, useRef, useCallback } from 'react'
import type {
  StackConfig,
  EnrichmentStatusResponse,
  EnrichmentScanResponse,
  SweepScanResponse,
  SweepStatusResponse,
} from '../components/types'
import {
  enrichmentStatus,
  enrichmentScan,
  sweepScan,
  sweepStart,
  sweepStatus,
} from '../api'
import { formatBytes, formatDuration, timeAgo } from '../utils'

interface MediaPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  isApplying: boolean
}

// ── Enrichment Status Card ──────────────────────────────────────────

function EnrichmentStatusCard() {
  const [status, setStatus] = useState<EnrichmentStatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await enrichmentStatus()
      setStatus(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch')
    }
  }, [])

  useEffect(() => {
    refresh()
    pollRef.current = setInterval(refresh, 10000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [refresh])

  if (error) return <div className="card"><p className="field-error">{error}</p></div>
  if (!status) return <div className="card"><p>Loading enrichment status...</p></div>

  return (
    <div className="card">
      <h3>Enrichment Status</h3>
      <div className="stats-grid">
        <div className="stat-item">
          <span className="stat-value">{status.succeeded}</span>
          <span className="stat-label">Enriched</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{status.failed}</span>
          <span className="stat-label">Failed</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">{status.active_downloads.length}</span>
          <span className="stat-label">Active Downloads</span>
        </div>
        <div className="stat-item">
          <span className="stat-value">
            {status.last_search > 0 ? timeAgo(status.last_search) : 'Never'}
          </span>
          <span className="stat-label">Last Search</span>
        </div>
      </div>

      {status.active_downloads.length > 0 && (
        <div className="subsection">
          <h4>Active Downloads</h4>
          <div className="enrichment-list">
            {status.active_downloads.map((d, i) => (
              <div key={i} className="enrichment-item">
                <span className="enrichment-badge">
                  {d.video_upgrade ? 'VIDEO' : d.language.toUpperCase()}
                </span>
                <span className="enrichment-target">{d.target}</span>
                <span className="enrichment-torrent">{d.torrent_name}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {status.recent.length > 0 && (
        <div className="subsection">
          <h4>Recent Enrichments</h4>
          <div className="enrichment-list">
            {status.recent.slice(0, 10).map((r, i) => (
              <div key={i} className="enrichment-item">
                <span className={`enrichment-badge ${r.video_upgraded_to ? 'video' : 'audio'}`}>
                  {r.video_upgraded_to
                    ? r.video_upgraded_to.toUpperCase()
                    : r.languages_added.join(', ').toUpperCase() || 'OK'}
                </span>
                <span className="enrichment-target">{r.path}</span>
                <span className="enrichment-meta">
                  {r.chromaprint_score !== null && `score: ${(r.chromaprint_score * 100).toFixed(1)}%`}
                  {r.offset_seconds !== null && ` | offset: ${r.offset_seconds.toFixed(2)}s`}
                  {r.timestamp && ` | ${timeAgo(r.timestamp)}`}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// ── Gap Scanner Card ────────────────────────────────────────────────

function GapScannerCard() {
  const [scanResult, setScanResult] = useState<EnrichmentScanResponse | null>(null)
  const [isScanning, setIsScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleScan = async () => {
    setIsScanning(true)
    setError(null)
    try {
      const data = await enrichmentScan()
      setScanResult(data)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setIsScanning(false)
    }
  }

  return (
    <div className="card">
      <h3>Library Gap Analysis</h3>
      <p className="card-description">
        Scan the media library to identify files missing target audio languages or
        below the target video resolution.
      </p>

      <button
        className="btn btn-secondary"
        onClick={handleScan}
        disabled={isScanning}
      >
        {isScanning ? 'Scanning Library...' : 'Scan for Gaps'}
      </button>

      {error && <p className="field-error">{error}</p>}

      {scanResult && (
        <div className="scan-results">
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-value">{scanResult.total}</span>
              <span className="stat-label">Total Gaps</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{scanResult.audio_gaps}</span>
              <span className="stat-label">Audio Only</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{scanResult.video_gaps}</span>
              <span className="stat-label">Video Only</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{scanResult.both_gaps}</span>
              <span className="stat-label">Both</span>
            </div>
          </div>

          {scanResult.candidates.length > 0 && (
            <div className="subsection">
              <h4>Candidates (showing first {Math.min(scanResult.candidates.length, 50)})</h4>
              <div className="gap-table">
                <div className="gap-header">
                  <span>Title</span>
                  <span>Category</span>
                  <span>Resolution</span>
                  <span>Missing Audio</span>
                  <span>Issue</span>
                </div>
                {scanResult.candidates.slice(0, 50).map((c, i) => (
                  <div key={i} className="gap-row">
                    <span className="gap-title">
                      {c.title} {c.year && `(${c.year})`}
                    </span>
                    <span className="gap-category">{c.category}</span>
                    <span className="gap-resolution">
                      {c.current_resolution || 'N/A'} {c.current_codec && `/ ${c.current_codec}`}
                    </span>
                    <span className="gap-languages">
                      {c.missing_languages.length > 0
                        ? c.missing_languages.join(', ')
                        : '-'}
                    </span>
                    <span className="gap-issue">
                      {c.video_below_target && c.missing_languages.length > 0
                        ? 'Video + Audio'
                        : c.video_below_target
                        ? 'Video'
                        : 'Audio'}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          <button
            className="btn btn-ghost"
            onClick={() => setScanResult(null)}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}

// ── Enrichment Config Card ──────────────────────────────────────────

function EnrichmentConfigCard({ config, onChange, onSave }: {
  config: StackConfig
  onChange: (c: StackConfig) => void
  onSave: (c: StackConfig) => void
}) {
  const defaults = {
    enabled: false,
    search_interval_hours: 24,
    max_grabs_per_cycle: 2,
    search_queries: ['dual audio', 'english dub', 'multi'],
    min_seeders: 3,
    correlation_threshold: 0.7,
    fingerprint_duration_seconds: 120,
    target_languages: ['eng', 'original'],
    upgrade_video: true,
    target_resolution: '1080p',
    prefer_hdr: true,
    prefer_hevc: true,
  }
  const enrich = { ...defaults, ...(config.services?.pipeline?.enrichment || {}) }

  const update = (field: string, value: unknown) => {
    const newConfig = structuredClone(config)
    if (!newConfig.services.pipeline) return
    const current = newConfig.services.pipeline.enrichment || { ...defaults }
    ;(current as unknown as Record<string, unknown>)[field] = value
    newConfig.services.pipeline.enrichment = current
    onChange(newConfig)
  }

  return (
    <div className="card">
      <h3>Enrichment Configuration</h3>

      <div className="form-row">
        <label className="toggle-label">
          <input
            type="checkbox"
            checked={enrich.enabled}
            onChange={(e) => update('enabled', e.target.checked)}
          />
          <span>Enable Enrichment Pipeline</span>
        </label>
      </div>

      <div className="form-section">
        <h4>Target Audio Languages</h4>
        <p className="card-description">
          Languages every file should have. "original" resolves to each media item's
          native language from Radarr/Sonarr.
        </p>
        <input
          type="text"
          value={(enrich.target_languages || []).join(', ')}
          onChange={(e) => update('target_languages',
            e.target.value.split(',').map((s: string) => s.trim()).filter(Boolean)
          )}
          placeholder="eng, original"
          className="input-field"
        />
      </div>

      <div className="form-section">
        <h4>Video Quality</h4>
        <div className="form-row">
          <label className="toggle-label">
            <input
              type="checkbox"
              checked={enrich.upgrade_video}
              onChange={(e) => update('upgrade_video', e.target.checked)}
            />
            <span>Enable video quality upgrades</span>
          </label>
        </div>

        <div className="form-row inline">
          <label>Target Resolution</label>
          <select
            value={enrich.target_resolution}
            onChange={(e) => update('target_resolution', e.target.value)}
          >
            <option value="720p">720p</option>
            <option value="1080p">1080p</option>
            <option value="1440p">1440p</option>
            <option value="2160p">2160p / 4K</option>
          </select>
        </div>

        <div className="form-row">
          <label className="toggle-label">
            <input
              type="checkbox"
              checked={enrich.prefer_hdr}
              onChange={(e) => update('prefer_hdr', e.target.checked)}
            />
            <span>Prefer HDR</span>
          </label>
        </div>
        <div className="form-row">
          <label className="toggle-label">
            <input
              type="checkbox"
              checked={enrich.prefer_hevc}
              onChange={(e) => update('prefer_hevc', e.target.checked)}
            />
            <span>Prefer HEVC / H.265</span>
          </label>
        </div>
      </div>

      <div className="form-section">
        <h4>Search Settings</h4>
        <div className="form-row inline">
          <label>Search Interval</label>
          <input
            type="number"
            min={1}
            value={enrich.search_interval_hours}
            onChange={(e) => update('search_interval_hours', parseInt(e.target.value) || 24)}
            className="input-field input-narrow"
          />
          <span className="input-suffix">hours</span>
        </div>
        <div className="form-row inline">
          <label>Max Grabs Per Cycle</label>
          <input
            type="number"
            min={1}
            max={10}
            value={enrich.max_grabs_per_cycle}
            onChange={(e) => update('max_grabs_per_cycle', parseInt(e.target.value) || 2)}
            className="input-field input-narrow"
          />
        </div>
        <div className="form-row inline">
          <label>Min Seeders</label>
          <input
            type="number"
            min={1}
            value={enrich.min_seeders}
            onChange={(e) => update('min_seeders', parseInt(e.target.value) || 3)}
            className="input-field input-narrow"
          />
        </div>
      </div>

      <div className="form-section">
        <h4>Chromaprint</h4>
        <div className="form-row inline">
          <label>Correlation Threshold</label>
          <input
            type="number"
            min={0.1}
            max={1.0}
            step={0.05}
            value={enrich.correlation_threshold}
            onChange={(e) => update('correlation_threshold', parseFloat(e.target.value) || 0.7)}
            className="input-field input-narrow"
          />
        </div>
        <div className="form-row inline">
          <label>Fingerprint Duration</label>
          <input
            type="number"
            min={30}
            max={300}
            value={enrich.fingerprint_duration_seconds}
            onChange={(e) => update('fingerprint_duration_seconds', parseInt(e.target.value) || 120)}
            className="input-field input-narrow"
          />
          <span className="input-suffix">seconds</span>
        </div>
      </div>

      <div className="card-actions">
        <button className="btn btn-primary" onClick={() => onSave(config)}>
          Save Configuration
        </button>
      </div>
    </div>
  )
}

// ── Sweep Card (moved from MediaPolicyPage) ─────────────────────────

function SweepCard() {
  const [scanResult, setScanResult] = useState<SweepScanResponse | null>(null)
  const [status, setStatus] = useState<SweepStatusResponse | null>(null)
  const [isScanning, setIsScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null }
  }, [])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await sweepStatus()
        setStatus(s)
        if (s.status === 'completed' || s.status === 'failed' || s.status === 'idle') {
          stopPolling()
        }
      } catch { stopPolling() }
    }, 2000)
  }, [stopPolling])

  useEffect(() => () => stopPolling(), [stopPolling])

  const handleScan = async () => {
    setIsScanning(true); setError(null)
    try { setScanResult(await sweepScan()) }
    catch (e) { setError(e instanceof Error ? e.message : 'Scan failed') }
    finally { setIsScanning(false) }
  }

  const handleStart = async () => {
    try {
      const r = await sweepStart()
      setStatus({ status: 'running', sweep_id: r.sweep_id, progress_current: 0,
        progress_total: r.total_files, current_file: null, succeeded: 0, failed: 0, errors: [] })
      startPolling()
    } catch (e) { setError(e instanceof Error ? e.message : 'Start failed') }
  }

  const isRunning = status?.status === 'scanning' || status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'
  const progressPct = status && status.progress_total > 0
    ? Math.round((status.progress_current / status.progress_total) * 100) : 0

  return (
    <div className="card">
      <h3>Library Sweep</h3>
      <p className="card-description">
        Scan existing library files and strip unwanted audio/subtitle tracks
        based on your media language policy.
      </p>

      {!isRunning && !isCompleted && !isFailed && (
        <button className="btn btn-secondary" onClick={handleScan} disabled={isScanning}>
          {isScanning ? 'Scanning...' : 'Scan Library'}
        </button>
      )}

      {error && <p className="field-error">{error}</p>}

      {scanResult && !isRunning && !isCompleted && !isFailed && (
        <div className="scan-results">
          <div className="stats-grid">
            <div className="stat-item">
              <span className="stat-value">{scanResult.total_files_scanned}</span>
              <span className="stat-label">Scanned</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{scanResult.files_already_clean}</span>
              <span className="stat-label">Already Clean</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{scanResult.files_to_process}</span>
              <span className="stat-label">To Process</span>
            </div>
            <div className="stat-item">
              <span className="stat-value">{formatBytes(scanResult.total_bytes_to_process)}</span>
              <span className="stat-label">Data Size</span>
            </div>
          </div>
          {scanResult.files_to_process > 0 && (
            <>
              <p>Estimated time: {formatDuration(scanResult.estimated_time_seconds)}</p>
              <button className="btn btn-primary" onClick={handleStart}>Start Sweep</button>
            </>
          )}
        </div>
      )}

      {isRunning && status && (
        <div className="sweep-progress">
          <div className="progress-bar-container">
            <div className="progress-bar" style={{ width: `${progressPct}%` }} />
          </div>
          <p>{status.progress_current} / {status.progress_total} ({progressPct}%)
            {status.current_file && <> &mdash; {status.current_file.split('/').pop()}</>}
          </p>
        </div>
      )}

      {isCompleted && status && (
        <div className="sweep-complete">
          <p>Sweep complete: {status.succeeded} succeeded, {status.failed} failed</p>
          <button className="btn btn-ghost" onClick={() => { setStatus(null); setScanResult(null) }}>
            Dismiss
          </button>
        </div>
      )}

      {isFailed && status && (
        <div className="sweep-failed">
          <p className="field-error">Sweep failed{status.errors.length > 0 && `: ${status.errors[0]}`}</p>
          <button className="btn btn-ghost" onClick={() => { setStatus(null); setScanResult(null) }}>
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}

// ── Main Media Page ─────────────────────────────────────────────────

type MediaTab = 'enrichment' | 'sweep' | 'policy'

const MEDIA_TABS: { id: MediaTab; label: string }[] = [
  { id: 'enrichment', label: 'Enrichment' },
  { id: 'sweep', label: 'Library Sweep' },
  { id: 'policy', label: 'Language Policy' },
]

// ISO 639-2/B language codes (matching ffmpeg/ffprobe output)
const LANGUAGES: { code: string; label: string }[] = [
  { code: 'eng', label: 'English' },
  { code: 'jpn', label: 'Japanese' },
  { code: 'kor', label: 'Korean' },
  { code: 'chi', label: 'Chinese' },
  { code: 'fre', label: 'French' },
  { code: 'ger', label: 'German' },
  { code: 'spa', label: 'Spanish' },
  { code: 'ita', label: 'Italian' },
  { code: 'por', label: 'Portuguese' },
  { code: 'rus', label: 'Russian' },
  { code: 'ara', label: 'Arabic' },
  { code: 'hin', label: 'Hindi' },
  { code: 'tha', label: 'Thai' },
  { code: 'vie', label: 'Vietnamese' },
  { code: 'pol', label: 'Polish' },
  { code: 'dut', label: 'Dutch' },
  { code: 'swe', label: 'Swedish' },
  { code: 'nor', label: 'Norwegian' },
  { code: 'dan', label: 'Danish' },
  { code: 'fin', label: 'Finnish' },
  { code: 'und', label: 'Undetermined' },
]

export function MediaPage({ config, onChange, onSave, onApply, isApplying }: MediaPageProps) {
  const [activeTab, setActiveTab] = useState<MediaTab>('enrichment')
  const policy = config.media_policy?.movies || { keep_audio: ['eng', 'und'], keep_subs: ['eng'] }
  const quality = config.quality || { preset: 'balanced', target_resolution: null, max_bitrate_mbps: null, preferred_container: 'mkv' }

  const toggleLang = (field: 'keep_audio' | 'keep_subs', code: string) => {
    const current = [...(policy[field] || [])]
    const idx = current.indexOf(code)
    if (idx >= 0) current.splice(idx, 1)
    else current.push(code)
    const newConfig = structuredClone(config)
    if (!newConfig.media_policy) newConfig.media_policy = { movies: { keep_audio: [], keep_subs: [] } }
    newConfig.media_policy.movies[field] = current
    onChange(newConfig)
  }

  const updateQuality = (field: string, value: unknown) => {
    const newConfig = structuredClone(config)
    if (!newConfig.quality) newConfig.quality = { preset: 'balanced', target_resolution: null, max_bitrate_mbps: null, preferred_container: 'mkv' }
    ;(newConfig.quality as Record<string, unknown>)[field] = value
    onChange(newConfig)
  }

  return (
    <div className="page-content">
      <h2>Media</h2>

      <div className="tabs">
        {MEDIA_TABS.map(tab => (
          <button
            key={tab.id}
            className={`tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {activeTab === 'enrichment' && (
        <div className="tab-content">
          <EnrichmentStatusCard />
          <GapScannerCard />
          <EnrichmentConfigCard config={config} onChange={onChange} onSave={onSave} />
        </div>
      )}

      {activeTab === 'sweep' && (
        <div className="tab-content">
          <SweepCard />
        </div>
      )}

      {activeTab === 'policy' && (
        <div className="tab-content">
          <div className="card">
            <h3>Audio Languages</h3>
            <p className="card-description">
              Languages to keep during remux. Tracks not in this list are stripped.
            </p>
            <div className="language-chips">
              {LANGUAGES.map(({ code, label }) => (
                <button
                  key={code}
                  className={`chip${policy.keep_audio.includes(code) ? ' active' : ''}`}
                  onClick={() => toggleLang('keep_audio', code)}
                  title={code}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Subtitle Languages</h3>
            <p className="card-description">
              Subtitle languages to keep. Others are stripped during remux.
            </p>
            <div className="language-chips">
              {LANGUAGES.map(({ code, label }) => (
                <button
                  key={code}
                  className={`chip${policy.keep_subs.includes(code) ? ' active' : ''}`}
                  onClick={() => toggleLang('keep_subs', code)}
                  title={code}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="card">
            <h3>Quality Preferences</h3>
            <div className="form-row inline">
              <label>Quality Preset</label>
              <select
                value={quality.preset}
                onChange={(e) => updateQuality('preset', e.target.value)}
              >
                <option value="balanced">Balanced</option>
                <option value="1080p">1080p</option>
                <option value="4k">4K</option>
              </select>
            </div>
            <div className="form-row inline">
              <label>Preferred Container</label>
              <select
                value={quality.preferred_container}
                onChange={(e) => updateQuality('preferred_container', e.target.value)}
              >
                <option value="mkv">MKV</option>
                <option value="mp4">MP4</option>
              </select>
            </div>
            <div className="form-row inline">
              <label>Max Bitrate</label>
              <input
                type="number"
                min={1}
                value={quality.max_bitrate_mbps || ''}
                onChange={(e) => updateQuality('max_bitrate_mbps',
                  e.target.value ? parseInt(e.target.value) : null
                )}
                placeholder="No limit"
                className="input-field input-narrow"
              />
              <span className="input-suffix">Mbps</span>
            </div>
          </div>

          <div className="card-actions">
            <button className="btn btn-primary" onClick={() => onSave(config)} disabled={isApplying}>
              Save
            </button>
            <button className="btn btn-secondary" onClick={() => onApply(config)} disabled={isApplying}>
              Save &amp; Apply
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
