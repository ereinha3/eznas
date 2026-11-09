import { useState } from 'react'
import { useAuth } from '../../contexts/AuthContext'

interface SettingsLockProps {
  children: React.ReactNode
}

export function SettingsLock({ children }: SettingsLockProps) {
  const { sudoActive, verifySudo } = useAuth()
  const [showModal, setShowModal] = useState(false)
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [verifying, setVerifying] = useState(false)

  const handleUnlock = async () => {
    if (!password.trim()) return
    setVerifying(true)
    setError('')
    try {
      const result = await verifySudo(password)
      if (result?.success) {
        setShowModal(false)
        setPassword('')
      } else {
        setError(result?.message || 'Incorrect password')
      }
    } catch {
      setError('Incorrect password')
    } finally {
      setVerifying(false)
    }
  }

  if (sudoActive) {
    return (
      <>
        <div className="settings-unlocked-banner">
          <span className="unlock-icon">{'\uD83D\uDD13'}</span>
          <span>Settings unlocked. Changes will require deployment.</span>
        </div>
        {children}
      </>
    )
  }

  return (
    <>
      <div className="settings-locked-banner">
        <div className="locked-banner-content">
          <span className="lock-icon">{'\uD83D\uDD12'}</span>
          <div>
            <strong>Settings are locked</strong>
            <p>These settings affect your running stack. Enter your password to make changes.</p>
          </div>
        </div>
        <button className="btn-primary" onClick={() => setShowModal(true)}>
          Unlock to Edit
        </button>
      </div>

      {/* Read-only view */}
      <div className="settings-readonly">
        {children}
      </div>

      {/* Password modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => setShowModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <h3>Confirm your password</h3>
            <p className="modal-description">
              To protect your stack, settings changes require password confirmation.
            </p>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleUnlock()}
              placeholder="Enter your password"
              className="input-field modal-input"
              autoFocus
            />
            {error && <p className="field-error">{error}</p>}
            <div className="modal-actions">
              <button className="btn-secondary" onClick={() => setShowModal(false)}>
                Cancel
              </button>
              <button className="btn-primary" onClick={handleUnlock} disabled={verifying}>
                {verifying ? 'Verifying...' : 'Unlock'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}
