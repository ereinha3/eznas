import { useState, useEffect, useRef, useCallback } from 'react'
import type {
  StackConfig,
  SweepScanResponse,
  SweepStatusResponse,
} from '../components/types'
import {
  enrichmentStatus,
  sweepScan,
  sweepStart,
  sweepStatus,
} from '../api'
import { formatBytes, formatDuration } from '../utils'
import { LanguageChipPicker } from '../components/common/LanguageChipPicker'
import { usePolling } from '../hooks/usePolling'
import { useAuth } from '../contexts/AuthContext'

interface LibraryPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  isAdvanced: boolean
}

export function LibraryPage({ config, onChange, isAdvanced }: LibraryPageProps) {
  const { sudoActive } = useAuth()
  const locked = !sudoActive

  const policy = config.media_policy?.movies || { keep_audio: ['eng', 'und'], keep_subs: ['eng'] }
  const quality = config.quality || { preset: 'balanced', target_resolution: null, max_bitrate_mbps: null, preferred_container: 'mkv' }
  const enrichCfg = config.services?.pipeline?.enrichment

  const enrichDefaults = {
    enabled: false, max_concurrent_downloads: 1, queue_rescan_hours: 6,
    search_queries: ['dual audio', 'english dub', 'multi'], min_seeders: 3,
    correlation_threshold: 0.7, fingerprint_duration_seconds: 120,
    target_languages: [], upgrade_video: true, target_resolution: '1080p',
    prefer_hdr: true, prefer_hevc: true,
  }

  const updatePolicy = (field: 'keep_audio' | 'keep_subs', codes: string[]) => {
    if (locked) return
    onChange({ ...config, media_policy: { ...config.media_policy, movies: { ...policy, [field]: codes } } })
  }

  const updateQuality = (updates: Partial<typeof quality>) => {
    if (locked) return
    onChange({ ...config, quality: { ...quality, ...updates } })
  }

  const updateEnrichment = (key: string, value: unknown) => {
    if (locked) return
    onChange({
      ...config,
      services: { ...config.services, pipeline: { ...config.services.pipeline, enrichment: { ...enrichDefaults, ...enrichCfg, [key]: value } } },
    })
  }

  return (
    <div className="library-page">
      <div className="library-columns">
        {/* Left: Controls */}
        <div className={`library-controls ${locked ? 'locked' : ''}`}>
          {/* Languages */}
          <div className="card">
            <h3>Languages</h3>
            <div className="library-lang-row">
              <div className="library-lang-col">
                <span className="field-label">Audio</span>
                <div className={`chip-area ${locked ? 'chip-area-locked' : ''}`}>
                  {locked ? (
                    <div className="chip-readonly">{policy.keep_audio.join(', ') || 'none set'}</div>
                  ) : (
                    <LanguageChipPicker selected={policy.keep_audio} onChange={(c) => updatePolicy('keep_audio', c)} label="" />
                  )}
                </div>
              </div>
              <div className="library-lang-col">
                <span className="field-label">Subtitles</span>
                <div className={`chip-area ${locked ? 'chip-area-locked' : ''}`}>
                  {locked ? (
                    <div className="chip-readonly">{policy.keep_subs.join(', ') || 'none set'}</div>
                  ) : (
                    <LanguageChipPicker selected={policy.keep_subs} onChange={(c) => updatePolicy('keep_subs', c)} label="" />
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* Quality */}
          <div className="card">
            <h3>Quality</h3>
            <div className="quality-presets">
              {[
                { id: 'efficient', label: 'Good', desc: '1080p · x264' },
                { id: 'balanced', label: 'Better', desc: '1080p · x265' },
                { id: 'high', label: 'Best', desc: '4K HDR · x265' },
              ].map((p) => (
                <button
                  key={p.id}
                  className={`quality-preset ${quality.preset === p.id ? 'active' : ''}`}
                  onClick={() => updateQuality({ preset: p.id })}
                  disabled={locked}
                >
                  <span className="preset-label">{p.label}</span>
                  {quality.preset === p.id && <span className="preset-selected">selected</span>}
                  <span className="preset-desc">{p.desc}</span>
                </button>
              ))}
            </div>
            {isAdvanced && !locked && (
              <div className="quality-advanced">
                <div className="form-row inline">
                  <label>Container</label>
                  <select value={quality.preferred_container} onChange={(e) => updateQuality({ preferred_container: e.target.value })} className="input-field">
                    <option value="mkv">MKV</option><option value="mp4">MP4</option>
                  </select>
                </div>
                <div className="form-row inline">
                  <label>Max bitrate</label>
                  <input type="number" value={quality.max_bitrate_mbps ?? ''} onChange={(e) => updateQuality({ max_bitrate_mbps: e.target.value ? parseFloat(e.target.value) : null })} placeholder="No limit" className="input-field input-narrow" />
                </div>
              </div>
            )}
          </div>

          {/* Enhancer */}
          <div className="card">
            <div className="card-header-row">
              <div>
                <h3>Audio / Video enhancer</h3>
                <p className="card-description">Upgrades audio tracks and video quality in-place</p>
              </div>
              <label className="toggle-switch">
                <input type="checkbox" checked={enrichCfg?.enabled ?? false} onChange={(e) => updateEnrichment('enabled', e.target.checked)} disabled={locked} />
                <span className="toggle-slider" />
              </label>
            </div>
            {enrichCfg?.enabled && isAdvanced && !locked && (
              <div className="enhancer-rules">
                {[
                  ['Video upgrades', enrichCfg.upgrade_video, (v: boolean) => updateEnrichment('upgrade_video', v)],
                  ['Prefer HDR', enrichCfg.prefer_hdr, (v: boolean) => updateEnrichment('prefer_hdr', v)],
                  ['Prefer HEVC', enrichCfg.prefer_hevc, (v: boolean) => updateEnrichment('prefer_hevc', v)],
                ].map(([label, val, setter]) => (
                  <div key={label as string} className="enhancer-rule-row">
                    <span>{label as string}</span>
                    <label className="toggle-switch">
                      <input type="checkbox" checked={val as boolean} onChange={(e) => (setter as (v: boolean) => void)(e.target.checked)} />
                      <span className="toggle-slider" />
                    </label>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Library Sweep */}
          <SweepCard />
        </div>

        {/* Right: Live Pipeline Panel */}
        <div className="library-pipeline-panel">
          <div className="pipeline-panel-header">
            <span className="pipeline-live-dot" />
            <h3>Pipeline · live</h3>
          </div>
          <PipelinePanel />
        </div>
      </div>
    </div>
  )
}

// ── Pipeline Panel (right column) ──────────────────────────────────

function PipelinePanel() {
  const { data: status } = usePolling(enrichmentStatus, 8000)

  if (!status) return <p className="text-muted text-sm">Loading pipeline...</p>

  return (
    <>
      <div className="pipeline-kpi-grid">
        <div className="pipeline-kpi ok"><span className="pipeline-kpi-val">{status.succeeded}</span><span className="pipeline-kpi-label">succeeded</span></div>
        <div className="pipeline-kpi err"><span className="pipeline-kpi-val">{status.failed}</span><span className="pipeline-kpi-label">failed</span></div>
        <div className="pipeline-kpi warn"><span className="pipeline-kpi-val">{status.queue_size}</span><span className="pipeline-kpi-label">queued</span></div>
        <div className="pipeline-kpi accent"><span className="pipeline-kpi-val">{status.active_downloads.length}</span><span className="pipeline-kpi-label">active</span></div>
      </div>

      {status.active_downloads.length > 0 && (
        <div className="pipeline-active-section">
          <h4 className="pipeline-section-title">Active now</h4>
          <div className="pipeline-active-scroll">
            {status.active_downloads.map((dl, i) => (
              <div key={i} className="pipeline-active-item card">
                <div className="pipeline-active-header">
                  <span className="pipeline-active-name">{dl.target}</span>
                </div>
                <span className="pipeline-active-detail">{dl.language}{dl.video_upgrade ? ' + video upgrade' : ''}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {status.recent_failures && status.recent_failures.length > 0 && (
        <div className="pipeline-results-section">
          <h4 className="pipeline-section-title">Recent failures</h4>
          {status.recent_failures.slice(0, 5).map((f, i) => (
            <div key={i} className="pipeline-failure-row">
              <span className="pipeline-failure-name">{f.path}</span>
              <span className="pipeline-failure-reason">{f.reason}</span>
            </div>
          ))}
        </div>
      )}

      {status.recent.length > 0 && (
        <div className="pipeline-results-section">
          <h4 className="pipeline-section-title">Recent successes</h4>
          {status.recent.slice(0, 5).map((item, i) => (
            <div key={i} className="pipeline-success-row">
              <span>{item.path}</span>
              <span className="text-muted">{item.operation}{item.chromaprint_score != null ? ` · ${(item.chromaprint_score * 100).toFixed(0)}%` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </>
  )
}

// ── Sweep Card ─────────────────────────────────────────────────────

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
        if (s.status === 'completed' || s.status === 'failed' || s.status === 'idle') stopPolling()
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
      setStatus({ status: 'running', sweep_id: r.sweep_id, progress_current: 0, progress_total: r.total_files, current_file: null, succeeded: 0, failed: 0, errors: [] })
      startPolling()
    } catch (e) { setError(e instanceof Error ? e.message : 'Start failed') }
  }

  const isRunning = status?.status === 'scanning' || status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'
  const progressPct = status && status.progress_total > 0 ? Math.round((status.progress_current / status.progress_total) * 100) : 0

  return (
    <div className="card">
      <div className="card-header-row">
        <div>
          <h3>Library sweep</h3>
          <p className="card-description">Clean up unwanted audio tracks and subtitle files</p>
        </div>
        {!isRunning && !isCompleted && !isFailed && (
          <div className="sweep-actions">
            <button className="btn-secondary btn-sm" onClick={handleScan} disabled={isScanning}>{isScanning ? 'Scanning...' : 'Scan'}</button>
            {scanResult && scanResult.files_to_process > 0 && <button className="btn-primary btn-sm" onClick={handleStart}>Execute</button>}
          </div>
        )}
      </div>

      {error && <p className="field-error">{error}</p>}

      {scanResult && !isRunning && !isCompleted && !isFailed && (
        <div className="sweep-summary">
          <span>{scanResult.total_files_scanned} scanned · {scanResult.files_to_process} flagged · ~{formatBytes(scanResult.total_bytes_to_process)}</span>
          {scanResult.files_to_process > 0 && <span> · est. {formatDuration(scanResult.estimated_time_seconds)}</span>}
        </div>
      )}

      {isRunning && status && (
        <div className="sweep-progress">
          <div className="progress-bar-container"><div className="progress-bar" style={{ width: `${progressPct}%` }} /></div>
          <span className="text-muted text-sm">{status.progress_current}/{status.progress_total} ({progressPct}%)</span>
        </div>
      )}

      {isCompleted && status && <p className="text-sm">Complete: {status.succeeded} succeeded, {status.failed} failed <button className="btn-ghost btn-sm" onClick={() => { setStatus(null); setScanResult(null) }}>dismiss</button></p>}
      {isFailed && status && <p className="field-error">Failed{status.errors.length > 0 && `: ${status.errors[0]}`} <button className="btn-ghost btn-sm" onClick={() => { setStatus(null); setScanResult(null) }}>dismiss</button></p>}
    </div>
  )
}
