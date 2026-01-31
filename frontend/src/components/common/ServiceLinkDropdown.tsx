import { useState, useRef, useEffect } from 'react'

interface ServiceLinkDropdownProps {
  serviceName: string
  port: number
  proxyUrl?: string | null
}

export function ServiceLinkDropdown({ serviceName, port, proxyUrl }: ServiceLinkDropdownProps) {
  const [isOpen, setIsOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Get current hostname (could be LAN IP, Tailscale IP, or localhost)
  const currentHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost'

  const links = [
    {
      label: 'Localhost',
      url: `http://localhost:${port}`,
      description: 'Local access only',
    },
  ]

  // Add current host if different from localhost
  if (currentHost !== 'localhost' && currentHost !== '127.0.0.1') {
    links.push({
      label: 'Current Host',
      url: `http://${currentHost}:${port}`,
      description: `Access via ${currentHost}`,
    })
  }

  // Add proxy URL if configured
  if (proxyUrl) {
    const proxyProtocol = proxyUrl.includes('://') ? '' : 'http://'
    links.push({
      label: 'Proxy URL',
      url: `${proxyProtocol}${proxyUrl}`,
      description: 'Traefik reverse proxy',
    })
  }

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setIsOpen(false)
      }
    }

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [isOpen])

  const handleLinkClick = (url: string) => {
    window.open(url, '_blank', 'noopener,noreferrer')
    setIsOpen(false)
  }

  return (
    <div className="service-link-dropdown" ref={dropdownRef}>
      <button
        className="service-link-dropdown-trigger"
        onClick={() => setIsOpen(!isOpen)}
        aria-expanded={isOpen}
        aria-label={`Access ${serviceName}`}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"></path>
          <polyline points="15 3 21 3 21 9"></polyline>
          <line x1="10" y1="14" x2="21" y2="3"></line>
        </svg>
        <span>Access</span>
        <svg
          width="12"
          height="12"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          className="dropdown-arrow"
        >
          <polyline points="6 9 12 15 18 9"></polyline>
        </svg>
      </button>

      {isOpen && (
        <div className="service-link-dropdown-menu">
          {links.map((link, index) => (
            <button
              key={index}
              className="service-link-dropdown-item"
              onClick={() => handleLinkClick(link.url)}
            >
              <div className="dropdown-item-content">
                <span className="dropdown-item-label">{link.label}</span>
                <span className="dropdown-item-url">{link.url}</span>
              </div>
              <span className="dropdown-item-description">{link.description}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
