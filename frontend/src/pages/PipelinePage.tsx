import { useState, useEffect, useRef, useCallback } from 'react'
import type { PipelineHealthResponse, RecommenderStatusResponse, StackConfig } from '../components/types'
import { pipelineHealth, recommenderStatus } from '../api'
import { timeAgo } from '../utils'

interface PipelinePageProps {
  config: StackConfig
}

export function PipelinePage({ config }: PipelinePageProps) {
  const [health, setHealth] = useState<PipelineHealthResponse | null>(null)
  const [recStatus, setRecStatus] = useState<RecommenderStatusResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [h, r] = await Promise.all([
        pipelineHealth(),
        recommenderStatus().catch(() => null),
      ])
      setHealth(h)
      setRecStatus(r)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to fetch pipeline health')
    }
  }, [])

  useEffect(() => {
    refresh()
    pollRef.current = setInterval(refresh, 10000)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [refresh])

  const pipelineEnabled = config.services?.pipeline?.enabled ?? false
  const backfillCfg = config.services?.pipeline?.backfill
  const enrichCfg = config.services?.pipeline?.enrichment

  return (
    <div className="page-content">
      <h2>Pipeline</h2>

      <div className="card">
        <h3>Worker Status</h3>
        {error && <p className="field-error">{error}</p>}
        {health ? (
          <div className="pipeline-status">
            <div className="status-row">
              <span className="status-label">Status</span>
              <span className={`status-badge ${health.status}`}>
                {health.status.toUpperCase()}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Last Tick</span>
              <span>{health.last_tick ? timeAgo(health.last_tick) : 'Never'}</span>
            </div>
            {health.last_error && (
              <div className="status-row">
                <span className="status-label">Last Error</span>
                <span className="field-error">{health.last_error}</span>
              </div>
            )}
            <div className="status-row">
              <span className="status-label">Processed Items</span>
              <span>{health.processed_count}</span>
            </div>
            <div className="status-row">
              <span className="status-label">Orphans Tracked</span>
              <span>{health.orphan_count}</span>
            </div>
          </div>
        ) : (
          <p>Loading pipeline health...</p>
        )}
      </div>

      <div className="card">
        <h3>Pipeline Phases</h3>
        <p className="card-description">
          The pipeline runs every 60 seconds. Each tick executes these phases in order:
        </p>
        <div className="phase-list">
          <div className="phase-item">
            <span className="phase-badge active">Pre-tick</span>
            <span>Stale staging + orphan source cleanup (7-day TTL)</span>
          </div>
          <div className="phase-item">
            <span className="phase-badge active">Phase 1</span>
            <span>Process completed qBittorrent torrents (remux + import)</span>
          </div>
          <div className="phase-item">
            <span className="phase-badge active">Phase 1.5</span>
            <span>Health/stall detection (kill dead torrents, exponential backoff)</span>
          </div>
          <div className="phase-item">
            <span className="phase-badge active">Phase 2</span>
            <span>Scan orphans (untracked files in scratch)</span>
          </div>
          <div className="phase-item">
            <span className="phase-badge active">Phase 3</span>
            <span>Library refresh (trigger arr rescans)</span>
          </div>
          <div className={`phase-item${backfillCfg?.enabled ? '' : ' disabled'}`}>
            <span className={`phase-badge${backfillCfg?.enabled ? ' active' : ''}`}>Phase 4</span>
            <span>Backfill engine (search for missing content)</span>
            {!backfillCfg?.enabled && <span className="phase-tag">Disabled</span>}
          </div>
          <div className={`phase-item${backfillCfg?.prowlarr_fallback_enabled ? '' : ' disabled'}`}>
            <span className={`phase-badge${backfillCfg?.prowlarr_fallback_enabled ? ' active' : ''}`}>Phase 5</span>
            <span>Prowlarr direct-grab fallback</span>
            {!backfillCfg?.prowlarr_fallback_enabled && <span className="phase-tag">Disabled</span>}
          </div>
          <div className="phase-item">
            <span className="phase-badge active">Phase 6</span>
            <span>Nightly automation (indexer discovery + missing search)</span>
          </div>
          <div className={`phase-item${enrichCfg?.enabled ? '' : ' disabled'}`}>
            <span className={`phase-badge${enrichCfg?.enabled ? ' active' : ''}`}>Phase 7</span>
            <span>Media enrichment (chromaprint cross-mux + video upgrades)</span>
            {!enrichCfg?.enabled && <span className="phase-tag">Disabled</span>}
          </div>
        </div>
      </div>

      <div className="card">
        <h3>Configuration Summary</h3>
        <div className="config-summary">
          <div className="config-row">
            <span>Pipeline Enabled</span>
            <span className={pipelineEnabled ? 'text-success' : 'text-muted'}>
              {pipelineEnabled ? 'Yes' : 'No'}
            </span>
          </div>
          <div className="config-row">
            <span>Backfill Enabled</span>
            <span className={backfillCfg?.enabled ? 'text-success' : 'text-muted'}>
              {backfillCfg?.enabled ? 'Yes' : 'No'}
            </span>
          </div>
          {backfillCfg?.enabled && (
            <>
              <div className="config-row">
                <span>Backfill Interval</span>
                <span>{backfillCfg.interval_minutes}m</span>
              </div>
              <div className="config-row">
                <span>Stall Detection</span>
                <span>{backfillCfg.stall_detection_enabled ? 'On' : 'Off'}</span>
              </div>
            </>
          )}
          <div className="config-row">
            <span>Enrichment Enabled</span>
            <span className={enrichCfg?.enabled ? 'text-success' : 'text-muted'}>
              {enrichCfg?.enabled ? 'Yes' : 'No'}
            </span>
          </div>
          {enrichCfg?.enabled && (
            <>
              <div className="config-row">
                <span>Target Languages</span>
                <span>{(enrichCfg.target_languages || []).join(', ')}</span>
              </div>
              <div className="config-row">
                <span>Video Upgrades</span>
                <span>{enrichCfg.upgrade_video ? 'On' : 'Off'}</span>
              </div>
              <div className="config-row">
                <span>Target Resolution</span>
                <span>{enrichCfg.target_resolution}</span>
              </div>
            </>
          )}
        </div>
      </div>
      {recStatus && (
        <div className="card">
          <h3>Recommender Engine</h3>
          <div className="pipeline-status">
            <div className="status-row">
              <span className="status-label">Status</span>
              <span className={`status-badge ${recStatus.index_loaded ? 'running' : 'stopped'}`}>
                {recStatus.index_loaded ? 'LOADED' : 'NOT LOADED'}
              </span>
            </div>
            <div className="status-row">
              <span className="status-label">Last Run</span>
              <span>{recStatus.last_run ? timeAgo(new Date(recStatus.last_run * 1000).toISOString()) : 'Never'}</span>
            </div>
            {recStatus.last_error && (
              <div className="status-row">
                <span className="status-label">Last Error</span>
                <span className="field-error">{recStatus.last_error}</span>
              </div>
            )}
            {recStatus.index_stats && (
              <>
                <div className="status-row">
                  <span className="status-label">Movies Indexed</span>
                  <span>{recStatus.index_stats.movie_count.toLocaleString()}</span>
                </div>
                <div className="status-row">
                  <span className="status-label">Index Type</span>
                  <span>{recStatus.index_stats.index_type}</span>
                </div>
              </>
            )}
            <div className="status-row">
              <span className="status-label">Users with Profiles</span>
              <span>{recStatus.user_count}</span>
            </div>
            <div className="status-row">
              <span className="status-label">Total Recommendations</span>
              <span>{recStatus.total_recommendations}</span>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
