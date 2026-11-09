import type { ServiceKey } from './components/types'

export const SERVICE_DISPLAY: Record<ServiceKey, {
  name: string
  technicalName: string
  description: string
  icon: string
  required?: boolean
}> = {
  qbittorrent:  { name: 'Downloader',       technicalName: 'qBittorrent',   description: 'Downloads media via torrents',       icon: 'DL', required: true },
  radarr:       { name: 'Movie Manager',     technicalName: 'Radarr',        description: 'Tracks and grabs movies',            icon: 'MV' },
  sonarr:       { name: 'TV Manager',        technicalName: 'Sonarr',        description: 'Tracks and grabs TV shows',          icon: 'TV' },
  prowlarr:     { name: 'Indexers',          technicalName: 'Prowlarr',      description: 'Searches torrent sites',             icon: 'IX' },
  jellyseerr:   { name: 'Requests',          technicalName: 'Jellyseerr',    description: 'Request new movies and shows',       icon: 'RQ' },
  jellyfin:     { name: 'Media Server',      technicalName: 'Jellyfin',      description: 'Stream and watch your library',      icon: 'MS' },
  bazarr:       { name: 'Subtitles',         technicalName: 'Bazarr',        description: 'Automatically downloads subtitles',  icon: 'SB' },
  flaresolverr: { name: 'CloudFlare Bypass', technicalName: 'FlareSolverr',  description: 'Bypasses CloudFlare protections',    icon: 'CF' },
  pipeline:     { name: 'Auto-Organizer',    technicalName: 'Pipeline',      description: 'Processes, remuxes, and organizes',  icon: 'AO', required: true },
  gluetun:      { name: 'VPN',              technicalName: 'Gluetun',       description: 'Routes downloads through VPN',       icon: 'VP' },
}

export const SERVICE_ORDER: ServiceKey[] = [
  'jellyfin',
  'radarr',
  'sonarr',
  'qbittorrent',
  'prowlarr',
  'jellyseerr',
  'bazarr',
  'pipeline',
  'gluetun',
  'flaresolverr',
]

export const LANGUAGES: { code: string; label: string }[] = [
  { code: 'eng', label: 'English' },
  { code: 'jpn', label: 'Japanese' },
  { code: 'kor', label: 'Korean' },
  { code: 'chi', label: 'Chinese' },
  { code: 'fre', label: 'French' },
  { code: 'ger', label: 'German' },
  { code: 'spa', label: 'Spanish' },
  { code: 'ita', label: 'Italian' },
  { code: 'por', label: 'Portuguese' },
  { code: 'rus', label: 'Russian' },
  { code: 'ara', label: 'Arabic' },
  { code: 'hin', label: 'Hindi' },
  { code: 'tha', label: 'Thai' },
  { code: 'vie', label: 'Vietnamese' },
  { code: 'pol', label: 'Polish' },
  { code: 'dut', label: 'Dutch' },
  { code: 'swe', label: 'Swedish' },
  { code: 'nor', label: 'Norwegian' },
  { code: 'dan', label: 'Danish' },
  { code: 'fin', label: 'Finnish' },
  { code: 'und', label: 'Undetermined' },
  { code: 'forced', label: 'Forced' },
]

export const PHASE_LABELS: Record<string, { color: string; label: string }> = {
  process:    { color: '#4caf50', label: 'Import' },
  enrichment: { color: '#2196f3', label: 'Enhance' },
  health:     { color: '#ff9800', label: 'Health Check' },
  backfill:   { color: '#9c27b0', label: 'Auto-Fill' },
  nightly:    { color: '#607d8b', label: 'Nightly' },
  orphan:     { color: '#795548', label: 'Cleanup' },
  sweep:      { color: '#00bcd4', label: 'Library Sweep' },
  apply:      { color: '#03a9f4', label: 'Deploy' },
}
