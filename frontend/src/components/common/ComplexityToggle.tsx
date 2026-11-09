interface ComplexityToggleProps {
  isAdvanced: boolean
  onToggle: () => void
}

export function ComplexityToggle({ isAdvanced, onToggle }: ComplexityToggleProps) {
  return (
    <button className="complexity-toggle" onClick={onToggle} title={isAdvanced ? 'Switch to Simple mode' : 'Switch to Advanced mode'}>
      <span className={`toggle-option ${!isAdvanced ? 'active' : ''}`}>Simple</span>
      <span className={`toggle-option ${isAdvanced ? 'active' : ''}`}>Advanced</span>
    </button>
  )
}
