import { useEffect, useState } from 'react'
import {
  fetchAvailableIndexers,
  fetchConfiguredIndexers,
  addIndexers,
  removeIndexer,
  autoPopulateIndexers,
} from '../api'
import type { IndexerSchema, IndexerInfo } from './types'

// Popular public indexers that work well for media automation
const RECOMMENDED_INDEXERS = [
  '1337x',
  'EZTV',
  'LimeTorrents',
  'Nyaa.si',
  'YTS',
  'Knaben',
  'TorrentGalaxy',
  'The Pirate Bay',
]

interface IndexerPanelProps {
  prowlarrEnabled: boolean
}

export function IndexerPanel({ prowlarrEnabled }: IndexerPanelProps) {
  const [available, setAvailable] = useState<IndexerSchema[]>([])
  const [configured, setConfigured] = useState<IndexerInfo[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [successMessage, setSuccessMessage] = useState<string | null>(null)
  const [showAddForm, setShowAddForm] = useState(false)
  const [selectedIndexers, setSelectedIndexers] = useState<Set<string>>(new Set())
  const [addingIndexers, setAddingIndexers] = useState(false)
  const [autoPopulating, setAutoPopulating] = useState(false)

  const loadData = async () => {
    if (!prowlarrEnabled) return

    setLoading(true)
    setError(null)
    try {
      const [availableRes, configuredRes] = await Promise.all([
        fetchAvailableIndexers(),
        fetchConfiguredIndexers(),
      ])
      setAvailable(availableRes.indexers)
      setConfigured(configuredRes.indexers)
    } catch (err: any) {
      setError(err?.message || 'Failed to load indexers')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [prowlarrEnabled])

  const handleToggleIndexer = (name: string) => {
    setSelectedIndexers((prev) => {
      const next = new Set(prev)
      if (next.has(name)) {
        next.delete(name)
      } else {
        next.add(name)
      }
      return next
    })
  }

  const handleAddSelected = async () => {
    if (selectedIndexers.size === 0) return

    setAddingIndexers(true)
    setError(null)
    try {
      const result = await addIndexers(Array.from(selectedIndexers))
      if (result.failed.length > 0) {
        setError(`Failed to add: ${result.failed.join(', ')}`)
      }
      setSelectedIndexers(new Set())
      setShowAddForm(false)
      await loadData()
    } catch (err: any) {
      setError(err?.message || 'Failed to add indexers')
    } finally {
      setAddingIndexers(false)
    }
  }

  const handleRemove = async (indexer: IndexerInfo) => {
    if (!confirm(`Remove indexer "${indexer.name}"?`)) return

    setError(null)
    setSuccessMessage(null)
    try {
      await removeIndexer(indexer.id)
      await loadData()
    } catch (err: any) {
      setError(err?.message || 'Failed to remove indexer')
    }
  }

  const handleAutoPopulate = async () => {
    setAutoPopulating(true)
    setError(null)
    setSuccessMessage(null)
    try {
      const result = await autoPopulateIndexers()
      if (result.failed.length > 0 && result.added.length === 0) {
        setError(`Failed to add indexers: ${result.failed.join(', ')}`)
      } else if (result.failed.length > 0) {
        setError(`Some indexers failed: ${result.failed.join(', ')}`)
        setSuccessMessage(result.message)
      } else {
        setSuccessMessage(result.message)
      }
      await loadData()
    } catch (err: any) {
      setError(err?.message || 'Failed to auto-populate indexers')
    } finally {
      setAutoPopulating(false)
    }
  }

  // Filter available indexers to show recommended ones first, then others
  const configuredNames = new Set(configured.map((i) => i.name.toLowerCase()))
  const availableNotConfigured = available.filter(
    (i) => !configuredNames.has(i.name.toLowerCase())
  )
  const recommendedAvailable = availableNotConfigured.filter((i) =>
    RECOMMENDED_INDEXERS.some((r) => r.toLowerCase() === i.name.toLowerCase())
  )
  const otherAvailable = availableNotConfigured.filter(
    (i) => !RECOMMENDED_INDEXERS.some((r) => r.toLowerCase() === i.name.toLowerCase())
  )

  if (!prowlarrEnabled) {
    return (
      <div className="card indexer-card">
        <div className="indexer-header">
          <h3>Indexers</h3>
        </div>
        <div className="indexer-empty">Enable Prowlarr to manage indexers.</div>
      </div>
    )
  }

  return (
    <div className="card indexer-card">
      <div className="indexer-header">
        <h3>Indexers</h3>
        <button
          type="button"
          className="icon-button"
          onClick={loadData}
          disabled={loading}
        >
          Refresh
        </button>
      </div>

      {error && <div className="indexer-error">{error}</div>}
      {successMessage && <div className="indexer-success">{successMessage}</div>}

      {loading && configured.length === 0 ? (
        <div className="indexer-empty">Loading indexers...</div>
      ) : configured.length === 0 ? (
        <div className="indexer-empty">No indexers configured.</div>
      ) : (
        <div className="indexer-list">
          {configured.map((indexer) => (
            <div className="indexer-entry" key={indexer.id}>
              <div className="indexer-info">
                <span className="indexer-name">{indexer.name}</span>
                <span className={`indexer-status ${indexer.enable ? 'enabled' : 'disabled'}`}>
                  {indexer.enable ? 'Enabled' : 'Disabled'}
                </span>
              </div>
              <button
                type="button"
                className="icon-button danger"
                onClick={() => handleRemove(indexer)}
              >
                Remove
              </button>
            </div>
          ))}
        </div>
      )}

      {!showAddForm ? (
        <div className="indexer-actions">
          <button
            type="button"
            className="auto-populate-button primary"
            onClick={handleAutoPopulate}
            disabled={loading || autoPopulating}
            title="Automatically add public indexers matching your language preferences for Movies and TV"
          >
            {autoPopulating ? 'Auto-populating...' : 'Auto-populate Indexers'}
          </button>
          <button
            type="button"
            className="add-indexer-button"
            onClick={() => setShowAddForm(true)}
            disabled={loading || availableNotConfigured.length === 0}
          >
            + Add Manually
          </button>
        </div>
      ) : (
        <div className="indexer-add-form">
          <div className="indexer-add-header">
            <span>Select indexers to add</span>
            <button
              type="button"
              className="icon-button"
              onClick={() => {
                setShowAddForm(false)
                setSelectedIndexers(new Set())
              }}
            >
              Cancel
            </button>
          </div>

          {recommendedAvailable.length > 0 && (
            <>
              <div className="indexer-section-label">Recommended</div>
              <div className="indexer-checkbox-list">
                {recommendedAvailable.map((indexer) => (
                  <label key={indexer.name} className="indexer-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedIndexers.has(indexer.name)}
                      onChange={() => handleToggleIndexer(indexer.name)}
                      disabled={addingIndexers}
                    />
                    <span className="indexer-checkbox-name">{indexer.name}</span>
                    {indexer.language && (
                      <span className="indexer-checkbox-lang">{indexer.language}</span>
                    )}
                  </label>
                ))}
              </div>
            </>
          )}

          {otherAvailable.length > 0 && (
            <>
              <div className="indexer-section-label">
                Other Public Indexers ({otherAvailable.length})
              </div>
              <div className="indexer-checkbox-list scrollable">
                {otherAvailable.slice(0, 50).map((indexer) => (
                  <label key={indexer.name} className="indexer-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedIndexers.has(indexer.name)}
                      onChange={() => handleToggleIndexer(indexer.name)}
                      disabled={addingIndexers}
                    />
                    <span className="indexer-checkbox-name">{indexer.name}</span>
                    {indexer.language && (
                      <span className="indexer-checkbox-lang">{indexer.language}</span>
                    )}
                  </label>
                ))}
                {otherAvailable.length > 50 && (
                  <div className="indexer-more">
                    And {otherAvailable.length - 50} more...
                  </div>
                )}
              </div>
            </>
          )}

          <div className="indexer-add-actions">
            <button
              type="button"
              className="primary"
              onClick={handleAddSelected}
              disabled={selectedIndexers.size === 0 || addingIndexers}
            >
              {addingIndexers
                ? 'Adding...'
                : `Add ${selectedIndexers.size} Indexer${selectedIndexers.size !== 1 ? 's' : ''}`}
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
