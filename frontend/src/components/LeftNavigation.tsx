export type PageKey = 'dashboard' | 'media' | 'services' | 'pipeline' | 'recommendations' | 'settings' | 'logs'

interface NavItem {
  id: PageKey
  label: string
  icon: string
}

const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Overview', icon: '📊' },
  { id: 'media', label: 'Media', icon: '🎬' },
  { id: 'services', label: 'Services', icon: '🔲' },
  { id: 'pipeline', label: 'Pipeline', icon: '⚡' },
  { id: 'recommendations', label: 'Discover', icon: '🔮' },
  { id: 'settings', label: 'Settings', icon: '⚙️' },
  { id: 'logs', label: 'Logs', icon: '📄' },
]

interface LeftNavigationProps {
  activePage: PageKey
  onNavigate: (page: PageKey) => void
}

export function LeftNavigation({ activePage, onNavigate }: LeftNavigationProps) {
  return (
    <nav className="left-navigation">
      <div className="nav-items">
        {NAV_ITEMS.map((item) => (
          <button
            key={item.id}
            className={`nav-item${activePage === item.id ? ' active' : ''}`}
            onClick={() => onNavigate(item.id)}
          >
            <span className="nav-item-icon">{item.icon}</span>
            <span className="nav-item-label">{item.label}</span>
          </button>
        ))}
      </div>
    </nav>
  )
}
