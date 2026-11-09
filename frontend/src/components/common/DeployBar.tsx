import type { StackConfig } from '../types'

interface DeployBarProps {
  hasChanges: boolean
  onDeploy: (config: StackConfig) => void
  onDiscard: () => void
  config: StackConfig
  isApplying: boolean
}

export function DeployBar({ hasChanges, onDeploy, onDiscard, config, isApplying }: DeployBarProps) {
  if (!hasChanges && !isApplying) return null

  return (
    <div className="deploy-bar">
      <div className="deploy-bar-content">
        {isApplying ? (
          <span className="deploy-bar-text">Deploying changes...</span>
        ) : (
          <>
            <span className="deploy-bar-text">You have unsaved changes</span>
            <div className="deploy-bar-actions">
              <button className="btn-secondary" onClick={onDiscard}>
                Discard
              </button>
              <button className="btn-primary" onClick={() => onDeploy(config)}>
                Deploy
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
