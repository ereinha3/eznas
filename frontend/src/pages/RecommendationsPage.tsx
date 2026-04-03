import { useState, useEffect, useCallback } from 'react'
import type {
  RecommenderStatusResponse,
  UserRecommendationsResponse,
  RecommendationItem,
  BecauseYouWatchedSection,
} from '../components/types'
import { recommenderStatus, recommenderForUser, recommenderRebuild } from '../api'
import { timeAgo } from '../utils'

const TMDB_POSTER_BASE = 'https://image.tmdb.org/t/p/w300'

interface UserOption {
  user_id: string
  username: string
  watched_count: number
  has_profile: boolean
}

function PosterCard({ item }: { item: RecommendationItem }) {
  const posterUrl = item.poster_path
    ? `${TMDB_POSTER_BASE}${item.poster_path}`
    : null

  return (
    <div className="rec-poster-card">
      <div className="rec-poster-image-wrap">
        {posterUrl ? (
          <img
            src={posterUrl}
            alt={item.title}
            className="rec-poster-image"
            loading="lazy"
          />
        ) : (
          <div className="rec-poster-placeholder">
            <span>{item.title.charAt(0)}</span>
          </div>
        )}
        <div className="rec-poster-overlay">
          <span className={`rec-badge ${item.in_library ? 'owned' : 'requestable'}`}>
            {item.in_library ? 'In Library' : 'Request'}
          </span>
          <span className="rec-score">{Math.min(100, Math.round(item.score * 100))}%</span>
        </div>
      </div>
      <div className="rec-poster-info">
        <span className="rec-poster-title" title={item.title}>{item.title}</span>
        <span className="rec-poster-meta">
          {item.release_date?.slice(0, 4) || ''}
          {item.media_type === 'tv' ? ' (TV)' : ''}
          {item.vote_average ? ` \u2022 ${item.vote_average.toFixed(1)}` : ''}
        </span>
        {item.genres && item.genres.length > 0 && (
          <span className="rec-poster-genres">
            {item.genres.slice(0, 3).join(', ')}
          </span>
        )}
      </div>
    </div>
  )
}

function Carousel({ title, items }: { title: string; items: RecommendationItem[] }) {
  if (!items.length) return null

  return (
    <div className="rec-carousel-section">
      <h3 className="rec-carousel-title">{title}</h3>
      <div className="rec-carousel">
        {items.map((item) => (
          <PosterCard key={item.tmdb_id} item={item} />
        ))}
      </div>
    </div>
  )
}

export function RecommendationsPage() {
  const [status, setStatus] = useState<RecommenderStatusResponse | null>(null)
  const [users, setUsers] = useState<UserOption[]>([])
  const [selectedUser, setSelectedUser] = useState<string>('')
  const [recs, setRecs] = useState<UserRecommendationsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [rebuilding, setRebuilding] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const loadStatus = useCallback(async () => {
    try {
      const s = await recommenderStatus()
      setStatus(s)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load recommender status')
    }
  }, [])

  // Load users list
  useEffect(() => {
    const loadUsers = async () => {
      try {
        const response = await fetch('/api/pipeline/recommender/users', {
          headers: {
            'Authorization': `Bearer ${localStorage.getItem('nas_orchestrator_token')}`,
          },
        })
        if (response.ok) {
          const data = await response.json()
          setUsers(data.users || [])
          // Auto-select first user with a profile
          const withProfile = (data.users || []).find((u: UserOption) => u.has_profile)
          if (withProfile) {
            setSelectedUser(withProfile.user_id)
          }
        }
      } catch {
        // Users endpoint may not be available
      } finally {
        setLoading(false)
      }
    }
    loadStatus()
    loadUsers()
  }, [loadStatus])

  // Load recommendations when user changes
  useEffect(() => {
    if (!selectedUser) {
      setRecs(null)
      return
    }
    const loadRecs = async () => {
      try {
        const data = await recommenderForUser(selectedUser)
        setRecs(data)
      } catch {
        setRecs(null)
      }
    }
    loadRecs()
  }, [selectedUser])

  const handleRebuild = async () => {
    setRebuilding(true)
    try {
      await recommenderRebuild()
      await loadStatus()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Rebuild failed')
    } finally {
      setRebuilding(false)
    }
  }

  const selectedUsername = users.find(u => u.user_id === selectedUser)?.username || ''

  return (
    <div className="page-content recommendations-page">
      <div className="rec-header">
        <h2>Discover</h2>
        <div className="rec-header-controls">
          {users.length > 0 && (
            <select
              className="rec-user-select"
              value={selectedUser}
              onChange={(e) => setSelectedUser(e.target.value)}
            >
              <option value="">Select User</option>
              {users.map((u) => (
                <option key={u.user_id} value={u.user_id}>
                  {u.username} ({u.watched_count} watched)
                </option>
              ))}
            </select>
          )}
          <button
            className="btn btn-secondary"
            onClick={handleRebuild}
            disabled={rebuilding}
          >
            {rebuilding ? 'Rebuilding...' : 'Rebuild Index'}
          </button>
        </div>
      </div>

      {error && <div className="status-alert error">{error}</div>}

      {/* Status summary */}
      {status && (
        <div className="rec-status-bar">
          <span>
            {status.index_stats
              ? `${status.index_stats.movie_count.toLocaleString()} movies`
              : 'No index'}
            {status.index_stats && status.index_stats.tv_count > 0
              ? ` + ${status.index_stats.tv_count.toLocaleString()} TV shows`
              : ''}
          </span>
          <span>{status.user_count} user{status.user_count !== 1 ? 's' : ''} profiled</span>
          <span>{status.total_recommendations} recommendations</span>
          {status.last_run && (
            <span>Updated {timeAgo(new Date(status.last_run * 1000).toISOString())}</span>
          )}
        </div>
      )}

      {loading && <p>Loading...</p>}

      {!loading && !recs && selectedUser && (
        <div className="rec-empty-state">
          <p>No recommendations yet for {selectedUsername}.</p>
          <p>The recommender needs at least 3 watched items to generate recommendations. Click "Rebuild Index" to trigger a cycle.</p>
        </div>
      )}

      {!loading && !selectedUser && users.length > 0 && (
        <div className="rec-empty-state">
          <p>Select a user to view their personalized recommendations.</p>
        </div>
      )}

      {!loading && users.length === 0 && status?.index_stats == null && (
        <div className="rec-empty-state">
          <p>The recommendation engine hasn't run yet.</p>
          <p>Enable it in stack.yaml (<code>services.pipeline.recommender.enabled: true</code>) and click "Rebuild Index" to start.</p>
        </div>
      )}

      {/* Recommendations */}
      {recs && (
        <div className="rec-content">
          {/* Recommended for you */}
          <Carousel
            title={`Recommended for ${selectedUsername}`}
            items={recs.for_you}
          />

          {/* Because you watched sections */}
          {recs.because_you_watched.map((section: BecauseYouWatchedSection) => (
            <Carousel
              key={section.seed_tmdb_id}
              title={`Because you watched ${section.seed_title}`}
              items={section.items}
            />
          ))}
        </div>
      )}

      {/* Auto-requested items */}
      {status?.auto_request && status.auto_request.total_requested > 0 && (
        <div className="card" style={{ marginTop: '1.5rem' }}>
          <h3>Auto-Requested via Jellyseerr</h3>
          <p className="card-description">
            {status.auto_request.total_requested} total items auto-requested based on recommendations.
          </p>
          {status.auto_request.recent.length > 0 && (
            <div className="rec-auto-request-list">
              {status.auto_request.recent.map((item, i) => (
                <div key={i} className="rec-auto-request-item">
                  <span className="rec-auto-title">{item.title}</span>
                  <span className="rec-auto-meta">
                    {item.media_type === 'tv' ? 'TV' : 'Movie'}
                    {' \u2022 '}
                    {Math.round(item.score * 100)}% match
                    {' \u2022 '}
                    {timeAgo(new Date(item.timestamp * 1000).toISOString())}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
