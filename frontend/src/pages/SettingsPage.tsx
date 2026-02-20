import { useState, useRef, useEffect, useCallback } from 'react'
import type { StackConfig } from '../components/types'
import { previewConfigChanges, type ConfigPreview } from '../api'
import { SetupPage } from './SetupPage'
import { ProxyPage } from './ProxyPage'
import { MediaPolicyPage } from './MediaPolicyPage'

type SettingsTab = 'general' | 'proxy' | 'media'

interface TabDef {
  id: SettingsTab
  label: string
  icon: string
}

const TABS: TabDef[] = [
  { id: 'general', label: 'General', icon: '⚙\uFE0F' },
  { id: 'proxy', label: 'Proxy', icon: '🔀' },
  { id: 'media', label: 'Media', icon: '🎬' },
]

/** Deep-equal two JSON-serializable objects. */
function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b)
}

interface SettingsPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
}

export function SettingsPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
}: SettingsPageProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>('general')

  // Snapshot config on mount — this is "what the server has"
  const savedConfigRef = useRef<StackConfig>(config)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)

  // Change preview state
  const [preview, setPreview] = useState<ConfigPreview | null>(null)
  const [showPreview, setShowPreview] = useState(false)
  const [previewLoading, setPreviewLoading] = useState(false)

  // Update savedConfig when a successful save/apply happens
  const originalOnSave = onSave
  const originalOnApply = onApply

  const handleSave = useCallback(
    (cfg: StackConfig) => {
      originalOnSave(cfg)
      // After save, update snapshot so "unsaved" bar disappears
      savedConfigRef.current = cfg
      setHasUnsavedChanges(false)
      setShowPreview(false)
      setPreview(null)
    },
    [originalOnSave],
  )

  const handleApply = useCallback(
    (cfg: StackConfig) => {
      originalOnApply(cfg)
      savedConfigRef.current = cfg
      setHasUnsavedChanges(false)
      setShowPreview(false)
      setPreview(null)
    },
    [originalOnApply],
  )

  const handleDiscard = useCallback(() => {
    onChange(savedConfigRef.current)
    setHasUnsavedChanges(false)
    setShowPreview(false)
    setPreview(null)
  }, [onChange])

  // Detect unsaved changes by comparing current config to snapshot
  useEffect(() => {
    setHasUnsavedChanges(!deepEqual(config, savedConfigRef.current))
  }, [config])

  const handlePreview = useCallback(async () => {
    if (previewLoading) return
    setPreviewLoading(true)
    try {
      const result = await previewConfigChanges(config)
      setPreview(result)
      setShowPreview(true)
    } catch {
      // If preview fails (e.g. no server connection), compute a local summary
      setPreview(null)
      setShowPreview(true)
    } finally {
      setPreviewLoading(false)
    }
  }, [config, previewLoading])

  return (
    <div className="settings-page">
      <h1>Settings</h1>

      <div className="settings-tabs">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            className={`settings-tab${activeTab === tab.id ? ' active' : ''}`}
            onClick={() => setActiveTab(tab.id)}
          >
            <span className="settings-tab-icon">{tab.icon}</span>
            <span>{tab.label}</span>
          </button>
        ))}
      </div>

      <div className="settings-tab-content">
        {activeTab === 'general' && (
          <SetupPage
            config={config}
            onChange={onChange}
            onSave={handleSave}
            onValidate={onValidate}
            onApply={handleApply}
            onBuild={onBuild}
            isApplying={isApplying}
          />
        )}
        {activeTab === 'proxy' && (
          <ProxyPage
            config={config}
            onChange={onChange}
            onSave={handleSave}
            onValidate={onValidate}
            onApply={handleApply}
            onBuild={onBuild}
            isApplying={isApplying}
          />
        )}
        {activeTab === 'media' && (
          <MediaPolicyPage
            config={config}
            onChange={onChange}
            onSave={handleSave}
            onValidate={onValidate}
            onApply={handleApply}
            onBuild={onBuild}
            isApplying={isApplying}
          />
        )}
      </div>

      {/* Pending Changes Bar */}
      {hasUnsavedChanges && (
        <div className="pending-changes-bar">
          <div className="pending-changes-info">
            <span className="pending-changes-dot" />
            <span className="pending-changes-label">Unsaved changes</span>
          </div>
          <div className="pending-changes-actions">
            <button
              className="pending-btn preview"
              onClick={handlePreview}
              disabled={previewLoading}
            >
              {previewLoading ? 'Loading...' : 'Preview Impact'}
            </button>
            <button className="pending-btn discard" onClick={handleDiscard}>
              Discard
            </button>
            <button
              className="pending-btn save"
              onClick={() => handleSave(config)}
              disabled={isApplying}
            >
              Save
            </button>
            <button
              className="pending-btn apply"
              onClick={() => handleApply(config)}
              disabled={isApplying}
            >
              {isApplying ? 'Applying...' : 'Save & Apply'}
            </button>
          </div>
        </div>
      )}

      {/* Change Preview Panel */}
      {showPreview && (
        <div className="change-preview-overlay" onClick={() => setShowPreview(false)}>
          <div className="change-preview-panel" onClick={(e) => e.stopPropagation()}>
            <div className="change-preview-header">
              <h3>Change Preview</h3>
              <button
                className="change-preview-close"
                onClick={() => setShowPreview(false)}
              >
                &times;
              </button>
            </div>

            {preview ? (
              <div className="change-preview-body">
                {preview.changes.length === 0 ? (
                  <p className="change-preview-empty">
                    No changes detected compared to the saved configuration.
                  </p>
                ) : (
                  <>
                    <div className="change-list">
                      {preview.changes.map((change, i) => (
                        <div key={i} className="change-item">
                          <div className="change-path">{change.path}</div>
                          <div className="change-values">
                            <span className="change-old">
                              {formatValue(change.old_value)}
                            </span>
                            <span className="change-arrow">&rarr;</span>
                            <span className="change-new">
                              {formatValue(change.new_value)}
                            </span>
                          </div>
                          {change.affected_services.length > 0 && (
                            <div className="change-affected">
                              Affects: {change.affected_services.join(', ')}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>

                    {(preview.services_to_restart.length > 0 ||
                      preview.services_to_reconfigure.length > 0) && (
                      <div className="change-impact-summary">
                        {preview.services_to_restart.length > 0 && (
                          <div className="impact-row restart">
                            <span className="impact-label">Will restart:</span>
                            <span className="impact-services">
                              {preview.services_to_restart.join(', ')}
                            </span>
                          </div>
                        )}
                        {preview.services_to_reconfigure.length > 0 && (
                          <div className="impact-row reconfigure">
                            <span className="impact-label">Will reconfigure:</span>
                            <span className="impact-services">
                              {preview.services_to_reconfigure.join(', ')}
                            </span>
                          </div>
                        )}
                      </div>
                    )}
                  </>
                )}
              </div>
            ) : (
              <div className="change-preview-body">
                <p className="change-preview-empty">
                  Unable to fetch preview from server. Your unsaved changes will be
                  visible after saving.
                </p>
              </div>
            )}

            <div className="change-preview-footer">
              <button
                className="pending-btn discard"
                onClick={() => {
                  handleDiscard()
                  setShowPreview(false)
                }}
              >
                Discard Changes
              </button>
              <button
                className="pending-btn apply"
                onClick={() => handleApply(config)}
                disabled={isApplying}
              >
                {isApplying ? 'Applying...' : 'Confirm & Apply'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function formatValue(val: unknown): string {
  if (val === null || val === undefined) return 'null'
  if (typeof val === 'boolean') return val ? 'true' : 'false'
  if (typeof val === 'string') return `"${val}"`
  if (Array.isArray(val)) {
    if (val.length === 0) return '[]'
    if (val.length <= 3) return `[${val.map(formatValue).join(', ')}]`
    return `[${val.length} items]`
  }
  return String(val)
}
