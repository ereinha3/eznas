import { useState, useEffect, useRef, useCallback } from 'react'
import type { ChangeEvent } from 'react'
import type { StackConfig, SweepScanResponse, SweepStatusResponse } from '../components/types'
import { ActionBar } from '../components/ActionBar'
import { sweepScan, sweepStart, sweepStatus } from '../api'

const LANGUAGE_OPTIONS = [
  { code: 'eng', label: 'English' },
  { code: 'und', label: 'Undetermined' },
  { code: 'spa', label: 'Spanish' },
  { code: 'fra', label: 'French' },
  { code: 'deu', label: 'German' },
  { code: 'ita', label: 'Italian' },
  { code: 'jpn', label: 'Japanese' },
  { code: 'kor', label: 'Korean' },
  { code: 'chi', label: 'Chinese' },
  { code: 'por', label: 'Portuguese' },
  { code: 'rus', label: 'Russian' },
] as const

interface MediaPolicyPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
}

export function MediaPolicyPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
}: MediaPolicyPageProps) {
  const handleLanguageSelect = (
    field: 'keep_audio' | 'keep_subs',
    event: ChangeEvent<HTMLSelectElement>,
  ) => {
    const values = Array.from(event.target.selectedOptions).map((option) => option.value)
    onChange({
      ...config,
      media_policy: {
        ...config.media_policy,
        movies: {
          ...config.media_policy.movies,
          [field]: values,
        },
      },
    })
  }

  return (
    <div className="media-policy-page">
      <h1>Media Policy</h1>

      <div className="card">
        <h2>Media Language Policy</h2>
        <p className="field-hint">
          Original language is automatically preserved for foreign films and anime.
          Select additional languages to keep below.
        </p>
        <div className="grid two">
          <label htmlFor="movies-audio">
            Audio languages
            <select
              id="movies-audio"
              multiple
              size={6}
              value={config.media_policy.movies.keep_audio}
              onChange={(e) => handleLanguageSelect('keep_audio', e)}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label} ({opt.code})
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="movies-subs">
            Subtitle languages
            <select
              id="movies-subs"
              multiple
              size={6}
              value={config.media_policy.movies.keep_subs}
              onChange={(e) => handleLanguageSelect('keep_subs', e)}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label} ({opt.code})
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Quality &amp; Format Preferences</h2>
        <div className="grid three">
          <label htmlFor="quality-preset">
            Quality preset
            <select
              id="quality-preset"
              value={config.quality.preset}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    preset: e.target.value,
                  },
                })
              }
            >
              <option value="balanced">Balanced</option>
              <option value="1080p">1080p</option>
              <option value="4k">4K</option>
            </select>
          </label>
          <label htmlFor="target-resolution">
            Target resolution
            <select
              id="target-resolution"
              value={config.quality.target_resolution ?? ''}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    target_resolution: e.target.value === '' ? null : e.target.value,
                  },
                })
              }
            >
              <option value="">No preference</option>
              <option value="720p">720p</option>
              <option value="1080p">1080p</option>
              <option value="1440p">1440p</option>
              <option value="2160p">2160p (4K)</option>
            </select>
          </label>
          <label htmlFor="max-bitrate">
            Max bitrate (Mbps)
            <input
              id="max-bitrate"
              type="number"
              min={1}
              value={config.quality.max_bitrate_mbps ?? ''}
              placeholder="Optional"
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    max_bitrate_mbps: e.target.value === '' ? null : Number(e.target.value),
                  },
                })
              }
            />
          </label>
          <label htmlFor="preferred-container">
            Preferred container
            <select
              id="preferred-container"
              value={config.quality.preferred_container}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    preferred_container: e.target.value,
                  },
                })
              }
            >
              <option value="mkv">MKV</option>
              <option value="mp4">MP4</option>
            </select>
          </label>
        </div>
      </div>

      <SweepCard />

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


// ---------------------------------------------------------------------------
// Library Sweep Card
// ---------------------------------------------------------------------------

function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  return `${h}h ${m}m`
}

function SweepCard() {
  const [scanResult, setScanResult] = useState<SweepScanResponse | null>(null)
  const [status, setStatus] = useState<SweepStatusResponse | null>(null)
  const [isScanning, setIsScanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  // Poll sweep status when a sweep is active
  useEffect(() => {
    return () => stopPolling()
  }, [stopPolling])

  const startPolling = useCallback(() => {
    stopPolling()
    pollRef.current = setInterval(async () => {
      try {
        const s = await sweepStatus()
        setStatus(s)
        if (s.status === 'completed' || s.status === 'failed' || s.status === 'idle') {
          stopPolling()
        }
      } catch {
        // Silently ignore poll errors
      }
    }, 2000)
  }, [stopPolling])

  const handleScan = async () => {
    setIsScanning(true)
    setError(null)
    setScanResult(null)
    setStatus(null)
    try {
      const result = await sweepScan()
      setScanResult(result)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Scan failed')
    } finally {
      setIsScanning(false)
    }
  }

  const handleStart = async () => {
    setError(null)
    try {
      await sweepStart()
      // Start polling for status
      setStatus({
        status: 'scanning',
        sweep_id: null,
        progress_current: 0,
        progress_total: scanResult?.files_to_process ?? 0,
        current_file: null,
        succeeded: 0,
        failed: 0,
        errors: [],
      })
      setScanResult(null)
      startPolling()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start sweep')
    }
  }

  const isRunning = status?.status === 'scanning' || status?.status === 'running'
  const isCompleted = status?.status === 'completed'
  const isFailed = status?.status === 'failed'
  const progressPct = status && status.progress_total > 0
    ? Math.round((status.progress_current / status.progress_total) * 100)
    : 0

  return (
    <div className="card">
      <h2>Library Sweep</h2>
      <p className="field-hint">
        Scan your existing movie and TV libraries and strip unwanted audio/subtitle
        tracks that don't match your media policy above. Files that were imported
        before the pipeline existed, or migrated from another instance, may have
        extra language tracks.
      </p>

      {/* Scan button */}
      {!isRunning && !isCompleted && (
        <button
          onClick={handleScan}
          disabled={isScanning}
          style={{ marginTop: '0.5rem' }}
        >
          {isScanning ? 'Scanning...' : 'Scan Library'}
        </button>
      )}

      {/* Error display */}
      {error && (
        <p style={{ color: 'var(--red, #e74c3c)', marginTop: '0.5rem' }}>
          {error}
        </p>
      )}

      {/* Scan results preview */}
      {scanResult && !isRunning && !isCompleted && (
        <div style={{ marginTop: '1rem' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.9rem' }}>
            <tbody>
              <tr>
                <td style={{ padding: '0.25rem 0.5rem' }}>Files scanned</td>
                <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right' }}>
                  {scanResult.total_files_scanned}
                </td>
              </tr>
              <tr>
                <td style={{ padding: '0.25rem 0.5rem' }}>Already clean</td>
                <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right' }}>
                  {scanResult.files_already_clean}
                </td>
              </tr>
              <tr>
                <td style={{ padding: '0.25rem 0.5rem' }}>
                  <strong>Files to process</strong>
                </td>
                <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right' }}>
                  <strong>{scanResult.files_to_process}</strong>
                </td>
              </tr>
              <tr>
                <td style={{ padding: '0.25rem 0.5rem' }}>Data to remux</td>
                <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right' }}>
                  {formatBytes(scanResult.total_bytes_to_process)}
                </td>
              </tr>
              <tr>
                <td style={{ padding: '0.25rem 0.5rem' }}>Estimated time</td>
                <td style={{ padding: '0.25rem 0.5rem', textAlign: 'right' }}>
                  {formatDuration(scanResult.estimated_time_seconds)}
                </td>
              </tr>
            </tbody>
          </table>

          {scanResult.files_to_process > 0 ? (
            <button
              onClick={handleStart}
              style={{ marginTop: '0.75rem' }}
            >
              Start Sweep ({scanResult.files_to_process} file{scanResult.files_to_process !== 1 ? 's' : ''})
            </button>
          ) : (
            <p style={{ marginTop: '0.75rem', color: 'var(--green, #27ae60)' }}>
              All library files already match your media policy.
            </p>
          )}
        </div>
      )}

      {/* Progress display */}
      {isRunning && status && (
        <div style={{ marginTop: '1rem' }}>
          <div style={{
            width: '100%',
            height: '8px',
            background: 'var(--bg-secondary, #2a2a2a)',
            borderRadius: '4px',
            overflow: 'hidden',
          }}>
            <div style={{
              width: `${progressPct}%`,
              height: '100%',
              background: 'var(--accent, #3498db)',
              borderRadius: '4px',
              transition: 'width 0.3s ease',
            }} />
          </div>
          <p style={{ fontSize: '0.85rem', marginTop: '0.5rem' }}>
            {status.status === 'scanning' ? 'Scanning library...' : (
              <>
                Processing {status.progress_current} / {status.progress_total} files
                ({progressPct}%)
              </>
            )}
          </p>
          {status.current_file && (
            <p style={{
              fontSize: '0.8rem',
              opacity: 0.7,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}>
              {status.current_file.split('/').slice(-2).join('/')}
            </p>
          )}
        </div>
      )}

      {/* Completed display */}
      {isCompleted && status && (
        <div style={{ marginTop: '1rem' }}>
          <p style={{ color: 'var(--green, #27ae60)' }}>
            Sweep complete: {status.succeeded} succeeded
            {status.failed > 0 && `, ${status.failed} failed`}
          </p>
          {status.errors.length > 0 && (
            <details style={{ marginTop: '0.5rem', fontSize: '0.85rem' }}>
              <summary style={{ cursor: 'pointer' }}>
                {status.errors.length} error{status.errors.length !== 1 ? 's' : ''}
              </summary>
              <ul style={{ marginTop: '0.25rem', paddingLeft: '1.25rem' }}>
                {status.errors.map((err, i) => (
                  <li key={i} style={{ color: 'var(--red, #e74c3c)', marginBottom: '0.25rem' }}>
                    {err}
                  </li>
                ))}
              </ul>
            </details>
          )}
          <button
            onClick={() => { setStatus(null); setScanResult(null) }}
            style={{ marginTop: '0.75rem' }}
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Failed display */}
      {isFailed && status && (
        <div style={{ marginTop: '1rem' }}>
          <p style={{ color: 'var(--red, #e74c3c)' }}>
            Sweep failed{status.errors.length > 0 ? `: ${status.errors[0]}` : ''}
          </p>
          <button
            onClick={() => { setStatus(null); setScanResult(null) }}
            style={{ marginTop: '0.5rem' }}
          >
            Dismiss
          </button>
        </div>
      )}
    </div>
  )
}
