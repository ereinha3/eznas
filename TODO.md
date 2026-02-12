# TODO

## In Progress / Current Work

### Prowlarr Authentication & Provisioning (Recently Fixed)
- ✅ **Fixed**: API key sync from config.xml on every ensure run
- ✅ **Fixed**: Dual authentication configuration (before and inside ArrAPI context)
- ✅ **Fixed**: Retry logic for stale API keys (401/403 errors trigger refresh)
- ✅ **Fixed**: Network connectivity when orchestrator runs in container (use `127.0.0.1:{port}`)
- ✅ **Fixed**: Health check fallback for containerized orchestrator
- 🔄 **Testing**: Verify provisioning works end-to-end after migration
- 🔄 **Documentation**: Add troubleshooting guide for Prowlarr setup issues

### Known Issues to Address
- **Port conflicts**: Dev compose services conflict with generated stack. Need better
  isolation or clear documentation on when to use which.
- **Network isolation**: Health checks may still fail in some Docker network configurations.
  Consider adding more robust network detection.

## Immediate Essentials (High Priority)

- **Bootstrap compose**: Single `docker compose up` that brings up orchestrator +
  UI, then allows generating the main stack without manual steps. This is critical
  for zero-touch deployment.
- **Frontend dev container**: Vite hot-reload in compose, no local npm required.
  Makes UI development seamless.
- **"Verify only" endpoint and UI action**: Quick re-checks without redeploy.
  Useful for troubleshooting configuration issues.

## UX / Product Improvements

- **Per-service verification badges**: Show verification status (pass/fail/unknown)
  for each service in the UI sidebar or service list.
- **Clear verification error hints**: When verification fails, show actionable
  hints (auth mismatch, API key missing, URL mismatch, etc.).
- **First-run wizard hints**: Guide users through initial setup with helpful
  hints for missing paths/permissions.
- **UI toggle for enabling/disabling services**: Allow toggling services before
  apply without editing YAML directly.
- **Save-and-apply flow with diff preview**: Show what will change in compose/env
  before applying.
- **Pipeline status tab**: Show pipeline worker status, processed files, errors,
  etc. (currently not needed per user feedback, but may be useful later).

## Core Orchestrator Hardening

- **Add retry/backoff to verification**: Reduce flakiness on slow service boot.
  Some services take time to be ready after container start.
- **Add health endpoint**: `/api/health` for orchestrator container readiness
  checks.
- **Persist verification outcomes**: Store verification results in `state.json`
  for history and UI display (currently only logged).
- **Add structured logging**: JSON logs for external monitoring (e.g., ELK, Loki).
- **Validate host paths are writable**: Check all configured paths before compose up
  to fail fast with clear errors.
- **Better error messages**: More actionable error messages throughout the stack.

## Service Automation Improvements

- **qBittorrent**: Verify preferences (save path + temp path + ratio limits) in
  addition to auth and categories.
- **Radarr/Sonarr**: Verify root folders and UI auth config. Currently only
  download client is verified.
- **Prowlarr**: Verify API key + tags + app sync settings + UI authentication.
  Currently only app links are verified. Authentication provisioning was recently
  fixed but verification should confirm it's properly configured.
- **Jellyseerr**: Verify Jellyfin settings and media server connection. Currently
  only initialization and Radarr/Sonarr links are verified.
- **Jellyfin**: Verify libraries and media paths. Currently only user creation
  is automated.

## Pipeline Enhancements

### Completed ✅
- ✅ Real pipeline worker service (continuous loop, polling, processing).
- ✅ Lossless remux path (`-c copy`) with language track stripping.
- ✅ Container format standardization (MKV/MP4).
- ✅ User-configurable remux preferences (audio/subtitle language allowlists).
- ✅ User-configurable quality targets (1080p/2k/4k) and bitrate caps (UI + backend).
- ✅ Integration into Docker Compose stack.
- ✅ Test script for simulating downloads.

### Future Work
- **Hooks from qBittorrent**: Use qBittorrent's webhook/notification system instead
  of polling for better responsiveness.
- **Media rename policies**: Automatic renaming based on Radarr/Sonarr conventions.
- **Optional transcoding**: Add option for lossy transcoding (currently only
  lossless remuxing).
- **Library refresh on completion**: Trigger Jellyfin library scan after file
  processing.
- **Fallback handling for unusual formats**: Better error handling for files that
  can't be remuxed with `-c copy`.
- **Re-seeding support**: Option to re-upload remuxed media back to the same
  client (currently disabled, original files are deleted).
- **Pipeline queue**: Queue system for handling multiple downloads simultaneously.
- **Progress tracking**: Show remux progress in UI or logs.

## Quality & Format Preferences

### Completed ✅
- ✅ Quality preset (balanced/1080p/4K) in UI and backend.
- ✅ Target resolution selector in UI.
- ✅ Max bitrate input in UI.
- ✅ Preferred container (MKV/MP4) selector.
- ✅ Integration into Radarr/Sonarr custom formats (internal).

### Future Work
- **Expose custom formats in UI**: Allow users to see/edit custom formats directly
  (currently simplified - formats created internally based on preferences).
- **Quality profile mapping**: Better mapping of quality preferences to Radarr/Sonarr
  quality profiles and cutoffs.
- **Codec preferences**: Allow users to prefer specific codecs (H.264, H.265, etc.).
- **HDR preferences**: Handle HDR content preferences.

## Docker / Packaging

- **Add `docker compose` example**: Single command example for local builds.
- **Document required ports**: Clear documentation of all ports used by services.
- **Document permissions**: Required permissions (docker socket, paths, etc.).
- **Optional Traefik config presets**: HTTP only vs HTTPS presets for easier setup.
- **Build tags for dev vs prod**: Debug logs, hot reload, etc. based on build target.
- **Multi-arch builds**: Support for ARM64, etc. for different NAS hardware.

## Testing

- **Integration tests**: Add tests for each service client (qBittorrent, Radarr, etc.).
- **Smoke tests**: End-to-end tests for apply + verify flow.
- **Mock service suite**: Mock service APIs for CI testing without real services.
- **Pipeline tests**: More comprehensive tests for remux pipeline with various
  file formats and edge cases.
- **UI tests**: E2E tests for the React UI.

## Documentation

- **Install guide**: Consumer-friendly step-by-step installation guide.
- **Troubleshooting matrix**: Common errors + fixes organized by service/issue.
- **Security guide**: Best practices for secrets, TLS, least privilege.
- **API documentation**: OpenAPI/Swagger docs for the FastAPI backend.
- **Architecture diagram**: Visual representation of the system architecture.

## Code Quality

- **Type hints**: Ensure all Python code has proper type hints.
- **Linting**: Fix any remaining linting issues.
- **Code organization**: Review and improve code organization as the project grows.
- **Error handling**: More consistent error handling patterns throughout.

## Performance

- **Optimize pipeline worker**: Reduce polling interval intelligently, batch operations.
- **UI performance**: Optimize React rendering, reduce unnecessary re-renders.
- **API response times**: Optimize slow endpoints, add caching where appropriate.
