export interface DownloadCategories {
  radarr: string
  sonarr: string
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
export interface BazarrConfig extends ServiceBaseConfig {}
export interface JellyseerrConfig extends ServiceBaseConfig {}
export interface JellyfinConfig extends ServiceBaseConfig {}
export interface FlareSolverrConfig extends ServiceBaseConfig {}
export interface BackfillConfig {
  enabled: boolean
  interval_minutes: number
  stall_detection_enabled: boolean
  stall_threshold_minutes: number
  min_seeders: number
  max_grabs_per_cycle: number
  max_size_gb: number
  prowlarr_fallback_enabled: boolean
  prowlarr_fallback_interval_hours: number
}
export interface RecommenderConfig {
  enabled: boolean
  refresh_interval_hours: number
  min_vote_count: number
  use_compressed_index: boolean
  min_watched_for_profile: number
  max_recommendations_per_user: number
  because_you_watched_count: number
  owned_weight: number
  collaborative_enabled: boolean
  collaborative_weight: number
  auto_request_enabled: boolean
  auto_request_max_per_day: number
}
export interface PipelineConfig extends ServiceBaseConfig {
  backfill?: BackfillConfig
  enrichment?: EnrichmentConfig
  recommender?: RecommenderConfig
}
export interface GluetunConfig extends ServiceBaseConfig {
  wireguard_config: string
}

export interface ServicesConfig {
  qbittorrent: QbittorrentConfig
  radarr: RadarrConfig
  sonarr: SonarrConfig
  prowlarr: ProwlarrConfig
  jellyseerr: JellyseerrConfig
  jellyfin: JellyfinConfig
  bazarr: BazarrConfig
  flaresolverr: FlareSolverrConfig
  pipeline: PipelineConfig
  gluetun: GluetunConfig
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

export interface StageEvent {
  stage: string
  status: 'started' | 'ok' | 'failed'
  detail?: string | null
}

export interface ApplyResponse {
  ok: boolean
  run_id: string
  events: StageEvent[]
}

export interface RunRecord {
  run_id: string
  ok: boolean | null
  events: StageEvent[]
  summary?: string | null
}

export interface RecentRunsResponse {
  runs: RunRecord[]
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

// Library Sweep types
export interface SweepActionDetail {
  path: string
  size: number
  category: string
  unwanted_audio: string[]
  unwanted_subtitles: string[]
}

export interface SweepScanResponse {
  total_files_scanned: number
  files_already_clean: number
  files_to_process: number
  total_bytes_to_process: number
  estimated_time_seconds: number
  actions: SweepActionDetail[]
}

export interface SweepStartResponse {
  sweep_id: string
  total_files: number
}

export interface SweepStatusResponse {
  status: 'idle' | 'scanning' | 'running' | 'completed' | 'failed'
  sweep_id: string | null
  progress_current: number
  progress_total: number
  current_file: string | null
  succeeded: number
  failed: number
  errors: string[]
}

// Enrichment types

export interface EnrichmentConfig {
  enabled: boolean
  search_interval_hours: number
  max_grabs_per_cycle: number
  search_queries: string[]
  min_seeders: number
  correlation_threshold: number
  fingerprint_duration_seconds: number
  target_languages: string[]
  upgrade_video: boolean
  target_resolution: string
  prefer_hdr: boolean
  prefer_hevc: boolean
}

export interface EnrichmentStatusResponse {
  enabled: boolean
  last_search: number
  total_processed: number
  succeeded: number
  failed: number
  active_downloads: EnrichmentActiveDownload[]
  recent: EnrichmentRecentItem[]
}

export interface EnrichmentActiveDownload {
  torrent_name: string
  target: string
  language: string
  video_upgrade: boolean
  timestamp: number
}

export interface EnrichmentRecentItem {
  path: string
  operation: string
  languages_added: string[]
  video_upgraded_to: string | null
  chromaprint_score: number | null
  offset_seconds: number | null
  timestamp: number
}

export interface EnrichmentScanResponse {
  total: number
  audio_gaps: number
  video_gaps: number
  both_gaps: number
  candidates: EnrichmentCandidate[]
}

export interface EnrichmentCandidate {
  path: string
  filename: string
  title: string
  year: number | null
  category: string
  missing_languages: string[]
  video_below_target: boolean
  current_resolution: string
  current_codec: string
  current_video_score: number
}

// Pipeline health

export interface PipelineHealthResponse {
  status: string
  last_tick: string | null
  last_error: string | null
  processed_count: number
  orphan_count: number
}

// Recommender

export interface AutoRequestEntry {
  title: string
  media_type: string
  score: number
  user_id: string
  timestamp: number
}

export interface RecommenderStatusResponse {
  last_run: number | null
  last_error: string | null
  index_stats: {
    movie_count: number
    tv_count: number
    index_type: string
  } | null
  user_count: number
  total_recommendations: number
  index_loaded: boolean
  auto_request?: {
    total_requested: number
    recent: AutoRequestEntry[]
  }
}

export interface RecommendationItem {
  tmdb_id: number
  title: string
  score: number
  in_library: boolean
  media_type: string
  genres?: string[]
  vote_average?: number
  release_date?: string
  poster_path?: string
  reason?: string
  jellyfin_id?: string
}

export interface BecauseYouWatchedSection {
  seed_title: string
  seed_tmdb_id: number
  items: RecommendationItem[]
}

export interface UserRecommendationsResponse {
  for_you: RecommendationItem[]
  because_you_watched: BecauseYouWatchedSection[]
  generated_at: number
}
