# TODO

## Immediate Essentials (Committed)
- Bootstrap compose: single `docker compose up` that brings up orchestrator +
  UI, then allows generating the main stack without manual steps.
- Frontend dev container: Vite hot-reload in compose, no local npm required.
- "Verify only" endpoint and UI action for quick re-checks without redeploy.

## UX / Product
- Per-service verification badges in the UI.
- Clear verification error hints (auth mismatch, API key missing, URL mismatch).
- First-run wizard hints for missing paths/permissions.
- UI toggle for enabling/disabling services before apply.
- Save-and-apply flow with "diff" preview of compose/env changes.

## Core Orchestrator Hardening
- Add retry/backoff to verification to reduce flakiness on slow boot.
- Add health endpoint for orchestrator container readiness.
- Persist verification outcomes in `state.json` for history and UI display.
- Add structured logging (JSON logs) for external monitoring.
- Validate that all host paths are writable before compose up.

## Service Automation Improvements
- qBittorrent: verify preferences (save path + temp path + ratio limits).
- Radarr/Sonarr: verify root folders and UI auth config.
- Prowlarr: verify API key + tags + app sync settings.
- Jellyseerr: verify Jellyfin settings and media server connection.
- Jellyfin: verify libraries and media paths.

## Pipeline (Future)
- Real pipeline worker service (queue, ffmpeg, move, refresh).
- Hooks from qBittorrent (post-download) into pipeline.
- Media rename policies + optional transcoding.
- Library refresh on completion (Jellyfin).
- User-configurable remux preferences (audio/subtitle language allowlists).
- User-configurable quality targets (1080p/2k/4k) and bitrate caps.
- Lossless remux path (`-c copy`) with fallback handling for unusual formats.

## Docker / Packaging
- Add `docker compose` example for local builds (single command).
- Document required ports and permissions (docker socket, paths).
- Optional Traefik config presets (HTTP only vs HTTPS).
- Build tags for dev vs prod (debug logs, hot reload, etc).

## Testing
- Add integration tests for each service client.
- Add smoke tests for apply + verify flow.
- Add "mock service" suite for CI.

## Docs
- Install guide (consumer-friendly).
- Troubleshooting matrix (common errors + fixes).
- Security guide (secrets, TLS, least privilege).

