import { LANGUAGES } from '../../constants'

interface LanguageChipPickerProps {
  selected: string[]
  onChange: (codes: string[]) => void
  label: string
  description?: string
}

export function LanguageChipPicker({ selected, onChange, label, description }: LanguageChipPickerProps) {
  const toggle = (code: string) => {
    if (selected.includes(code)) {
      onChange(selected.filter((c) => c !== code))
    } else {
      onChange([...selected, code])
    }
  }

  return (
    <div className="language-chip-picker">
      <div className="chip-picker-header">
        <span className="chip-picker-label">{label}</span>
        {description && <span className="chip-picker-description">{description}</span>}
      </div>
      <div className="chip-grid">
        {LANGUAGES.map(({ code, label: langLabel }) => (
          <button
            key={code}
            className={`language-chip ${selected.includes(code) ? 'selected' : ''}`}
            onClick={() => toggle(code)}
            type="button"
          >
            {langLabel}
          </button>
        ))}
      </div>
    </div>
  )
}
