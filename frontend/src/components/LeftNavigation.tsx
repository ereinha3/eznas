export type PageKey = 'home' | 'library' | 'system' | 'activity' | 'users' | 'settings'

interface NavItem {
  id: PageKey
  label: string
  glyph: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'home', label: 'Home', glyph: '\u2302' },
  { id: 'library', label: 'Library', glyph: '\u25A4' },
  { id: 'system', label: 'System', glyph: '\u2699' },
  { id: 'activity', label: 'Activity', glyph: '\u007E' },
  { id: 'users', label: 'Users', glyph: '\u2641' },
  { id: 'settings', label: 'Settings', glyph: '\u2692' },
]

interface LeftNavigationProps {
  activePage: PageKey
  onNavigate: (page: PageKey) => void
}

export function LeftNavigation({ activePage, onNavigate }: LeftNavigationProps) {
  return (
    <nav className="left-navigation">
      <div className="nav-brand">
        <span className="nav-brand-text">EZNAS</span>
      </div>
      <div className="nav-items">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`nav-item${activePage === item.id ? ' active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="nav-item-glyph">{item.glyph}</span>
            <span className="nav-item-label">{item.label}</span>
          </button>
        ))}
      </div>
    </nav>
  )
}
