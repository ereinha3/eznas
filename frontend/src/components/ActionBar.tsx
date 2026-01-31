import { useState } from 'react'
import type { StackConfig } from './types'

interface ActionBarProps {
  config: StackConfig
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying?: boolean
}

export function ActionBar({
  config,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying = false,
}: ActionBarProps) {
  const [isBuilding, setIsBuilding] = useState(false)

  const handleBuild = async () => {
    if (!onBuild) return
    setIsBuilding(true)
    try {
      await onBuild()
    } finally {
      setIsBuilding(false)
    }
  }

  return (
    <div className="action-bar">
      <button
        className="secondary"
        onClick={() => onSave(config)}
        disabled={isApplying || isBuilding}
      >
        Save Config
      </button>
      <button
        className="secondary"
        onClick={() => onValidate(config)}
        disabled={isApplying || isBuilding}
      >
        Validate
      </button>
      {onBuild && (
        <button
          className="secondary"
          onClick={handleBuild}
          disabled={isApplying || isBuilding}
        >
          {isBuilding ? 'Building...' : 'Build Image'}
        </button>
      )}
      <button
        className="primary"
        onClick={() => onApply(config)}
        disabled={isApplying || isBuilding}
      >
        {isApplying ? 'Applying...' : 'Apply Stack'}
      </button>
    </div>
  )
}
