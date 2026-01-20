export interface DownloadCategories {
  radarr: string
  sonarr: string
  anime: string
}

export interface DownloadPolicy {
  categories: DownloadCategories
}

export interface MediaPolicyEntry {
  keep_audio: string[]
  keep_subs: string[]
}

export interface MediaPolicy {
  movies: MediaPolicyEntry
  anime: MediaPolicyEntry
}

export interface ServiceBaseConfig {
  enabled: boolean
  port: number | null
  proxy_url: string | null
}

export interface QbittorrentConfig extends ServiceBaseConfig {
  stop_after_download: boolean
  username: string
  password: string
}

export interface RadarrConfig extends ServiceBaseConfig {}
export interface SonarrConfig extends ServiceBaseConfig {}
export interface ProwlarrConfig extends ServiceBaseConfig {
  // When true, only add indexers matching user's language preferences
  // When false, add all public indexers with Movies/TV categories
  language_filter: boolean
}
export interface JellyseerrConfig extends ServiceBaseConfig {}
export interface JellyfinConfig extends ServiceBaseConfig {}
export interface PipelineConfig extends ServiceBaseConfig {}

export interface ServicesConfig {
  qbittorrent: QbittorrentConfig
  radarr: RadarrConfig
  sonarr: SonarrConfig
  prowlarr: ProwlarrConfig
  jellyseerr: JellyseerrConfig
  jellyfin: JellyfinConfig
  pipeline: PipelineConfig
}

export interface RuntimeConfig {
  user_id: number
  group_id: number
  timezone: string
}

export interface ProxyConfig {
  enabled: boolean
  image: string
  http_port: number
  https_port: number | null
  dashboard: boolean
  additional_args: string[]
}

export interface UIConfig {
  port: number
}

export interface PathConfig {
  pool: string
  scratch: string | null
  appdata: string
}

export interface StackConfig {
  version: number
  paths: PathConfig
  runtime: RuntimeConfig
  proxy: ProxyConfig
  services: ServicesConfig
  download_policy: DownloadPolicy
  media_policy: MediaPolicy
  quality: {
    preset: string
    target_resolution: string | null
    max_bitrate_mbps: number | null
    preferred_container: string
  }
  ui: UIConfig
  users: Array<{ username: string; email?: string; role: string }>
}

export type ServiceKey = keyof ServicesConfig

export interface ValidationResult {
  ok: boolean
  checks: Record<string, string>
}

export interface RenderResult {
  compose_path: string
  env_path: string
}

export interface ApplyResponse {
  ok: boolean
  run_id: string
  events: Array<{ stage: string; status: string; detail?: string | null }>
}

export interface ServiceStatus {
  name: string
  status: 'up' | 'down' | 'unknown'
  message?: string
}

export interface StatusResponse {
  services: ServiceStatus[]
}

export interface CredentialUser {
  username: string
  password?: string | null
}

export interface ServiceCredential {
  service: string
  label: string
  username?: string | null
  password?: string | null
  editable: boolean
  canViewPassword: boolean
  multiUser: boolean
  supportsUserCreation: boolean
  users: CredentialUser[]
}

export interface CredentialsResponse {
  services: ServiceCredential[]
}

export interface HealthCheck {
  name: string
  healthy: boolean
  port: number | null
  message: string | null
}

export interface HealthResponse {
  status: 'healthy' | 'degraded' | 'unhealthy'
  services: HealthCheck[]
}

// Indexer types
export interface IndexerSchema {
  id: number
  name: string
  description?: string | null
  encoding?: string | null
  language?: string | null
  privacy: string
  protocol: string
  categories: Array<{ id: number; name: string }>
  supportsRss: boolean
  supportsSearch: boolean
}

export interface IndexerInfo {
  id: number
  name: string
  implementation: string
  enable: boolean
  priority: number
  protocol: string
}

export interface AvailableIndexersResponse {
  indexers: IndexerSchema[]
}

export interface ConfiguredIndexersResponse {
  indexers: IndexerInfo[]
}

export interface AddIndexersResponse {
  added: string[]
  failed: string[]
}

export interface AutoPopulateIndexersResponse {
  added: string[]
  skipped: string[]
  failed: string[]
  message: string
}
