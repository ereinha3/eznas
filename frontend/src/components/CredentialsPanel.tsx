import { useMemo, useState } from 'react'
import type {
  CredentialUser,
  CredentialsResponse,
  ServiceCredential,
} from './types'

interface CredentialsPanelProps {
  credentials: CredentialsResponse | null
  loading: boolean
  onRefresh: () => Promise<void>
  onUpdateQb: (username: string, password: string) => Promise<void>
  onAddJellyfinUser: (username: string, password: string) => Promise<void>
}

interface EditState {
  service: string
  username: string
  password: string
}

export function CredentialsPanel({
  credentials,
  loading,
  onRefresh,
  onUpdateQb,
  onAddJellyfinUser,
}: CredentialsPanelProps) {
  const [editState, setEditState] = useState<EditState | null>(null)
  const [showSecrets, setShowSecrets] = useState<Record<string, boolean>>({})
  const [pendingService, setPendingService] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [jellyfinFormVisible, setJellyfinFormVisible] = useState<Record<string, boolean>>({})
  const [jellyfinDraft, setJellyfinDraft] = useState<Record<string, { username: string; password: string }>>({})

  const entries = useMemo(() => credentials?.services ?? [], [credentials])

  const toggleSecret = (key: string) => {
    setShowSecrets((prev) => ({
      ...prev,
      [key]: !prev[key],
    }))
  }

  const handleStartEdit = (entry: ServiceCredential) => {
    setErrorMessage(null)
    setEditState({ service: entry.service, username: entry.username ?? '', password: entry.password ?? '' })
  }

  const handleCancelEdit = () => {
    setEditState(null)
    setPendingService(null)
  }

  const handleSaveEdit = async () => {
    if (!editState) {
      return
    }
    setErrorMessage(null)
    setPendingService(editState.service)
    try {
      await onUpdateQb(editState.username, editState.password)
      setEditState(null)
    } catch (error: any) {
      setErrorMessage(error?.message || 'Failed to update credentials')
    } finally {
      setPendingService(null)
    }
  }

  const handleToggleAddUser = (service: string) => {
    setJellyfinFormVisible((prev) => ({
      ...prev,
      [service]: !prev[service],
    }))
    setErrorMessage(null)
  }

  const handleDraftChange = (service: string, field: 'username' | 'password', value: string) => {
    setJellyfinDraft((prev) => ({
      ...prev,
      [service]: {
        username: field === 'username' ? value : prev[service]?.username ?? '',
        password: field === 'password' ? value : prev[service]?.password ?? '',
      },
    }))
  }

  const handleAddUser = async (service: string) => {
    const draft = jellyfinDraft[service] ?? { username: '', password: '' }
    if (!draft.username.trim()) {
      setErrorMessage('Username is required for new Jellyfin users')
      return
    }
    setErrorMessage(null)
    setPendingService(service)
    try {
      await onAddJellyfinUser(draft.username, draft.password)
      setJellyfinDraft((prev) => ({
        ...prev,
        [service]: { username: '', password: '' },
      }))
      setJellyfinFormVisible((prev) => ({
        ...prev,
        [service]: false,
      }))
    } catch (error: any) {
      setErrorMessage(error?.message || 'Failed to create Jellyfin user')
    } finally {
      setPendingService(null)
    }
  }

  const renderPasswordField = (value: string | null | undefined, key: string) => {
    if (!value) {
      return <span className="secret-placeholder">Not set</span>
    }
    const revealed = showSecrets[key]
    return (
      <div className="secret-value">
        <input type={revealed ? 'text' : 'password'} value={value} readOnly />
        <button
          type="button"
          className="icon-button"
          onClick={() => toggleSecret(key)}
        >
          {revealed ? 'Hide' : 'Show'}
        </button>
      </div>
    )
  }

  const renderManagedUser = (service: string, user: CredentialUser) => {
    const key = `${service}:${user.username}`
    return (
      <div className="credential-subuser" key={user.username}>
        <div className="subuser-row">
          <span className="subuser-name">{user.username}</span>
          {renderPasswordField(user.password ?? null, key)}
        </div>
      </div>
    )
  }

  return (
    <div className="card credentials-card">
      <div className="credentials-header">
        <h3>Service Credentials</h3>
        <button
          type="button"
          className="icon-button"
          onClick={async () => {
            setErrorMessage(null)
            try {
              await onRefresh()
            } catch (error: any) {
              setErrorMessage(error?.message || 'Failed to refresh credentials')
            }
          }}
          disabled={loading}
        >
          Refresh
        </button>
      </div>

      {errorMessage && <div className="credentials-error">{errorMessage}</div>}

      {loading && entries.length === 0 ? (
        <div className="credentials-empty">Loading credentials…</div>
      ) : entries.length === 0 ? (
        <div className="credentials-empty">No credential data available.</div>
      ) : (
        entries.map((entry) => {
          const adminSecretKey = `${entry.service}:primary`
          const isEditing = editState?.service === entry.service
          const isPending = pendingService === entry.service
          return (
            <div className="credential-entry" key={entry.service}>
              <div className="credential-heading">
                <div>
                  <span className="credential-label">{entry.label}</span>
                  {entry.multiUser && <span className="credential-badge">Multi-user</span>}
                </div>
                {entry.editable && !isEditing && (
                  <button
                    type="button"
                    className="icon-button"
                    onClick={() => handleStartEdit(entry)}
                    disabled={loading}
                  >
                    Edit
                  </button>
                )}
              </div>

              <div className="credential-fields">
                <div className="credential-field">
                  <span className="field-label">Username</span>
                  <span className="field-value">{entry.username ?? '—'}</span>
                </div>
                <div className="credential-field">
                  <span className="field-label">Password</span>
                  {renderPasswordField(entry.password ?? null, adminSecretKey)}
                </div>
              </div>

              {entry.editable && isEditing && (
                <div className="credential-edit">
                  <label>
                    Username
                    <input
                      value={editState?.username ?? ''}
                      onChange={(e) => setEditState((prev) => prev && { ...prev, username: e.target.value })}
                      disabled={isPending}
                    />
                  </label>
                  <label>
                    Password
                    <input
                      type="password"
                      value={editState?.password ?? ''}
                      onChange={(e) => setEditState((prev) => prev && { ...prev, password: e.target.value })}
                      disabled={isPending}
                    />
                  </label>
                  <div className="credential-edit-actions">
                    <button type="button" className="secondary" onClick={handleCancelEdit} disabled={isPending}>
                      Cancel
                    </button>
                    <button type="button" className="primary" onClick={handleSaveEdit} disabled={isPending}>
                      {isPending ? 'Saving…' : 'Save changes'}
                    </button>
                  </div>
                </div>
              )}

              {entry.multiUser && (
                <div className="credential-managed">
                  <div className="managed-heading">
                    <span>Managed users</span>
                    {entry.supportsUserCreation && (
                      <button
                        type="button"
                        className="icon-button"
                        onClick={() => handleToggleAddUser(entry.service)}
                        disabled={isPending}
                      >
                        {jellyfinFormVisible[entry.service] ? 'Cancel' : 'Add user'}
                      </button>
                    )}
                  </div>
                  {entry.users.length === 0 && <div className="credentials-empty">No additional users.</div>}
                  {entry.users.map((user) => renderManagedUser(entry.service, user))}
                  {jellyfinFormVisible[entry.service] && (
                    <div className="credential-edit">
                      <label>
                        Username
                        <input
                          value={jellyfinDraft[entry.service]?.username ?? ''}
                          onChange={(e) => handleDraftChange(entry.service, 'username', e.target.value)}
                          disabled={isPending}
                        />
                      </label>
                      <label>
                        Password
                        <input
                          type="password"
                          value={jellyfinDraft[entry.service]?.password ?? ''}
                          onChange={(e) => handleDraftChange(entry.service, 'password', e.target.value)}
                          disabled={isPending}
                        />
                      </label>
                      <div className="credential-edit-actions">
                        <button
                          type="button"
                          className="primary"
                          onClick={() => handleAddUser(entry.service)}
                          disabled={isPending}
                        >
                          {isPending ? 'Creating…' : 'Create user'}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )
        })
      )}
    </div>
  )
}
