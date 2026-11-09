import { useState } from 'react'

interface CollapsibleSectionProps {
  title: string
  subtitle?: string
  defaultOpen?: boolean
  children: React.ReactNode
}

export function CollapsibleSection({ title, subtitle, defaultOpen = false, children }: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen)

  return (
    <div className={`collapsible-section ${open ? 'open' : ''}`}>
      <button className="collapsible-header" onClick={() => setOpen(!open)}>
        <div className="collapsible-title-group">
          <span className="collapsible-title">{title}</span>
          {subtitle && <span className="collapsible-subtitle">{subtitle}</span>}
        </div>
        <span className="collapsible-chevron">{open ? '\u25B2' : '\u25BC'}</span>
      </button>
      {open && <div className="collapsible-body">{children}</div>}
    </div>
  )
}
