# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NAS Orchestrator (EZNAS) is an automated, zero-touch provisioning system for a NAS media stack. It provides a FastAPI backend with React frontend that renders Docker Compose from Jinja2 templates, deploys services, configures them via their APIs, and runs a media processing pipeline with subtitle enrichment.

### Services Managed
- **qBittorrent** — torrent client (VPN-routed via Gluetun)
- **Radarr** — movie management + quality profiles
- **Sonarr** — TV show management + quality profiles
- **Prowlarr** — indexer management (25 active indexers, FlareSolverr for CloudFlare bypass)
- **Jellyseerr** — user-facing request aggregator (approval workflow)
- **Jellyfin** — media server (16 plugins, Netflix-style UI)
- **Bazarr** — subtitle management (8 providers, AniDB integration)
- **Gluetun** — WireGuard VPN gateway
- **FlareSolverr** — CloudFlare bypass proxy
- **Pipeline Worker** — media processing, remux, health monitoring, nightly automation

## Development Workflows

### Option 1: Docker Dev Environment (Recommended)
```bash
./scripts/dev.sh up          # Hot reload (backend + frontend)
./scripts/dev.sh up-full     # All media services
./scripts/dev.sh up-pipeline # Pipeline worker
./scripts/dev.sh down        # Stop everything
```

### Option 2: Local Development (No Docker)
```bash
# Terminal 1: Backend with auto-reload
source .venv/bin/activate
uvicorn orchestrator.app:app --reload --port 8443

# Terminal 2: Frontend with HMR
cd frontend
VITE_API_ORIGIN=http://localhost:8443 npm run dev
```

### Testing
```bash
pytest                                    # Python tests
mypy orchestrator/                        # Type checking
ruff check orchestrator/                  # Linting
cd frontend && npm run lint               # Frontend linting
python test_pipeline.py --source /path/to/file.mkv --category movies --direct
```

### Docker Compose Files
- `docker-compose.dev.yml` — Development with hot reload
- `docker-compose.bootstrap.yml` — Production deployment
- `generated/docker-compose.yml` — Rendered media stack (created by UI)

## Architecture

### Data Flow
1. User requests media via Jellyseerr (embedded in Jellyfin) or EZNAS UI
2. Admin approves → Jellyseerr tells Sonarr/Radarr
3. Sonarr/Radarr search 25 indexers (min 5 seeders) → grab best release
4. qBittorrent downloads via WireGuard VPN (Gluetun)
5. Files land in `/mnt/scratch/complete/tv/` or `/movies/` (auto_tmm)
6. Pipeline worker detects completion → 5-layer metadata matching → ffmpeg remux
7. Output placed in `/mnt/pool/media/` with correct naming + ownership (uid 1000)
8. Sonarr/Radarr notified → Bazarr downloads subtitles → Jellyfin picks up file

### Pipeline Processing Phases (per tick, every 60s)
1. **Pre-tick cleanup** — Stale staging files + stale orphan sources (7-day TTL)
2. **Phase 1** — Process completed qBittorrent torrents (remux + import)
3. **Phase 1.5** — Health/stall detection (kill dead torrents, exponential backoff)
4. **Phase 1.9** — Cleanup processed sources
5. **Phase 2** — Scan orphans (untracked files in scratch, 7-day expiry)
6. **Phase 3** — Library refresh (trigger arr rescans)
7. **Phase 4** — Backfill engine (search for missing content)
8. **Phase 5** — Prowlarr direct-grab fallback (bypasses arr title matching)
9. **Phase 6** — Nightly automation (indexer discovery + missing content search)

### 5-Layer Metadata Matching
When the pipeline processes a torrent, it determines the correct service + media ID:
1. **Prowlarr fallback metadata** — exact match from fallback grab state
2. **Hash-based lookup** — torrent hash against arr download history
3. **Word-boundary name matching** — fuzzy against arr library
4. **Arr API lookup** — TMDb/TVDB search via arr lookup endpoint
5. **Cross-service fallback** — if primary service fails, try the other

### Key Directories
- `orchestrator/` — Python backend (FastAPI, Pydantic models, service clients)
- `orchestrator/clients/` — API clients (qb.py, radarr.py, sonarr.py, prowlarr.py, etc.)
- `orchestrator/converge/` — Orchestration pipeline (runner.py, services.py)
- `orchestrator/pipeline/` — Media processing (runner.py, worker.py, remux.py, health.py, prowlarr_fallback.py, sweep.py, backfill.py, bdmv.py, languages.py)
- `frontend/src/` — React UI (TypeScript)
- `templates/` — Jinja2 templates (docker-compose.yml.j2, env.j2, secrets/)
- `generated/` — Rendered output (docker-compose.yml, .env, .secrets/)
- `scripts/` — Production scripts and automation:
  - `dev.sh` — Docker dev environment launcher
  - `jellyfin_setup.py` — Scripted Jellyfin plugin configuration (idempotent)
  - `jellyfin_studio_collections.py` — Auto-sync studio-based collections
  - `targeted_sub_search.py` — Slow-drip subtitle fetcher for critical missing subs
- `scripts/archived/` — One-off remediation scripts (historical)
- `docs/archive/` — Completed session notes and incident reports

### Service Topology
```
Users ──→ Jellyfin (16 plugins, ElegantFin theme)
            ├── Jellyseerr (embedded via Jellyfin Enhanced + iframe tab)
            │     ├── Radarr ──→ Prowlarr (25 indexers) ──→ qBittorrent ──→ Gluetun (VPN)
            │     └── Sonarr ──→ Prowlarr                ──→ qBittorrent ──→ Gluetun
            └── Bazarr (8 subtitle providers, AniDB)

Pipeline Worker (runs every 60s):
  qBittorrent completed ──→ 5-layer metadata match ──→ ffmpeg remux ──→ /mnt/pool/media/
  Health monitor ──→ stall detection ──→ blocklist + re-search
  Nightly ──→ indexer discovery + missing search + studio collection sync

EZNAS Admin UI (React + FastAPI):
  Configure all services ──→ render Docker Compose ──→ deploy ──→ verify
```

### Jellyfin Plugins (16 active)
- **Jellyfin Enhanced** — quality/language/rating/genre tags on posters, Jellyseerr request integration (embedded modal), Elsewhere streaming links, ArrLinks (admin), calendar, downloads page, bookmarks, pause screen
- **Home Screen Sections** — Netflix-style modular home connected to Jellyseerr (Discover, Trending, My Requests), Sonarr (Upcoming), Radarr (Upcoming), plus Genre, Because You Watched, Watch Again
- **TMDb Box Sets** — auto-creates 35+ franchise collections (MCU, Star Wars, etc.)
- **Fanart** — ClearLogo, ClearArt, landscape thumbnails from fanart.tv
- **Custom Tabs** — "Requests" tab embedding Jellyseerr iframe
- **Intro Skipper** — auto-detect + skip intros, credits, recaps, previews, commercials
- **Subtitle Extract** — extracts embedded subs to external .srt (reduces transcoding)
- **Playback Reporting** — tracks watch history per user
- **Skin Manager**, **TMDb**, **OMDb**, **MusicBrainz**, **AudioDB**, **Studio Images**, **File Transformation**

Plugin configuration is scripted: `python3 scripts/jellyfin_setup.py --execute`
Studio collections auto-sync nightly via pipeline + standalone: `python3 scripts/jellyfin_studio_collections.py --execute`

### Key Files
- `stack.yaml` — Main configuration (user-editable via UI)
- `orchestrator/app.py` — FastAPI entrypoint + API routes (includes sweep endpoints)
- `orchestrator/models.py` — Pydantic config/state models (StackConfig, BackfillConfig, MediaPolicy, Sweep models)
- `orchestrator/storage.py` — ConfigRepository and state persistence
- `orchestrator/auth.py` — Authentication (Bearer + query-param token for SSE)
- `orchestrator/validators.py` — Path validation with container-aware mapping
- `orchestrator/converge/runner.py` — Main apply/converge logic
- `orchestrator/converge/verification_engine.py` — Service verification orchestration
- `orchestrator/pipeline/runner.py` — Pipeline worker loop + metadata matching + nightly automation
- `orchestrator/pipeline/worker.py` — Remux plan builder, episode parsing, output path computation
- `orchestrator/pipeline/remux.py` — FFmpeg command builder with language/codec filtering + audio track selection
- `orchestrator/pipeline/sweep.py` — Library sweep engine (scan + in-place remux for policy compliance)
- `orchestrator/pipeline/health.py` — Stall detection, exponential backoff, category-aware blocklisting
- `orchestrator/pipeline/backfill.py` — Backfill engine for missing content via arr APIs
- `orchestrator/pipeline/prowlarr_fallback.py` — Direct Prowlarr search + grab metadata tracking
- `orchestrator/pipeline/bdmv.py` — BDMV/Blu-ray detection and language extraction
- `orchestrator/pipeline/languages.py` — Language name to ISO 639 code mapping
- `orchestrator/clients/arr.py` — Common *arr API wrapper (ArrAPI base class, used by radarr/sonarr)
- `orchestrator/clients/qb.py` — qBittorrent provisioning (auto_tmm, seeding policy)
- `orchestrator/clients/bazarr.py` — Bazarr subtitle provider provisioning (8 providers, language profiles)

### Service Client Pattern
Each service has a dedicated client in `orchestrator/clients/`:
- `arr.py` — Common *arr API base class (shared by radarr/sonarr, includes GET/POST/DELETE + retry)
- `qb.py` — qBittorrent (auth, categories, preferences, auto_tmm)
- `radarr.py` / `sonarr.py` — Arr services (CDH management, download clients, root folders)
- `prowlarr.py` — Indexer management, FlareSolverr tagging, app linking, auto-populate
- `jellyseerr.py` — Request aggregator, language profiles, provider connections
- `jellyfin.py` — Media server wizard, users, libraries
- `bazarr.py` — Subtitle providers, language profiles, Radarr/Sonarr integration
- `base.py` — Common client base class
- `retry.py` — Retry/backoff logic for HTTP requests
- `util.py` — Shared client utilities

All clients use httpx and follow a consistent ensure/verify pattern.

## Important Design Decisions

### CDH (Completed Download Handling)
Disabled when pipeline is active to prevent duplicate imports. The pipeline is the sole importer. CDH auto-enables when pipeline is disabled as fallback. Code: `sonarr.py` + `radarr.py` CDH management methods.

### qBittorrent Seeding Policy
`max_ratio_enabled: false`, `max_seeding_time_enabled: false` — the pipeline controls torrent lifecycle (remove after successful import). `auto_tmm_enabled: true` — files go to category-specific save paths. Code: `qb.py` `_configure_preferences()`.

### Anime Absolute Episode Mapping
When Sonarr metadata is available for a TV series, the pipeline queries `/api/v3/episode` to build an absolute→season map. Anime packs using absolute numbering (e.g., "Show - 50") get correctly mapped to season episodes (e.g., S02E23). Code: `runner.py` `_build_absolute_episode_map()`.

### Bazarr Adaptive Searching
Disabled. Was causing false "not found" results due to provider rate limits, which locked items out for weeks. With 8 providers and rate limit management, items are retried every 6 hours. Configured via Bazarr's internal settings (provisioned by `bazarr.py`).

## Environment Variables
- `ORCH_ROOT` — Override config root directory (default: project root)
- `VITE_API_ORIGIN` — API origin for Vite dev server (default: `http://localhost:8443`)
- `PIPELINE_INTERVAL` — Pipeline worker polling interval in seconds (default: 60)
- `POOL_PATH` — Media library pool path (default: `/mnt/pool`)
- `SCRATCH_PATH` — Scratch space for downloads (default: `/mnt/scratch`)
- `APPDATA_PATH` — Application data directory (default: from `stack.yaml`)

## Tech Stack
- **Backend**: Python 3.10+, FastAPI, Pydantic, Jinja2, httpx, sse-starlette
- **Frontend**: TypeScript, React 19, Vite
- **Infrastructure**: Docker, Docker Compose, WireGuard (Gluetun)
- **Media**: FFmpeg (copy-mode remux), Jellyfin (16 plugins), Bazarr (8 subtitle providers)
- **Indexing**: Prowlarr (25 indexers), FlareSolverr (CloudFlare bypass)

## Known Issues / Outstanding Work
- Overwrite protection only compares file size (should also consider resolution, subtitle count, audio tracks)
- ~200 anime files missing English subtitles (free provider coverage gap)
- S00 special episodes not mapped in absolute numbering
- EZNAS UI needs redesign for better per-service settings

### Resolved Issues (for reference)
- ~~Cyrillic/Unicode filenames fail in ffmpeg~~ — Fixed via ASCII symlink approach in `_run_ffmpeg()`
- ~~Audio dedup / commentary stripping~~ — Implemented in `remux.py` audio track selection (commentary/descriptive filtering, best-per-language selection)
- ~~Audio language stripping~~ — Implemented in `remux.py` + `sweep.py` (keeps only preferred languages per MediaPolicy)

## Media Enrichment Pipeline

### Goal
Every media item in the library should have the **highest-quality video** combined with **all user-preferred audio languages** and **complete subtitle coverage**.

### Current Status — Phases 1-4 Complete ✅

**Phase 1: Track Management**

| Component | Status | Code |
|-----------|--------|------|
| Audio track selection (codec ranking, best-per-language) | ✅ Done | `remux.py` `_select_best_audio()` |
| Commentary/descriptive track filtering | ✅ Done | `remux.py` (disposition + keyword detection) |
| Audio language stripping to user preferences | ✅ Done | `remux.py` + `MediaPolicyEntry.keep_audio` |
| Subtitle language filtering (mov_text compat) | ✅ Done | `remux.py` subtitle mapping |
| Library sweep (scan + in-place remux) | ✅ Done | `sweep.py` (scan/execute + atomic replace) |
| Sweep API endpoints | ✅ Done | `app.py` POST /api/pipeline/sweep/{scan,start}, GET status |
| Bazarr subtitle gathering (8 providers) | ✅ Done | `bazarr.py` (7 free + optional premium) |
| Language validation on pipeline output | ✅ Done | `runner.py` `_validate_output()` (warning-level) |
| Non-ASCII filename handling | ✅ Done | `runner.py` ASCII symlink in `_run_ffmpeg()` |
| Original language lookup via arr APIs | ✅ Done | `sweep.py` + `runner.py` (avoids heuristic guessing) |
| BDMV/Blu-ray language override (CLPI) | ✅ Done | `bdmv.py` + `remux.py` |
| MediaPolicy configuration (keep_audio, keep_subs) | ✅ Done | `models.py` MediaPolicyEntry |

**Phases 2-4: Enrichment Pipeline**

| Component | Status | Code |
|-----------|--------|------|
| Chromaprint integration (fpcalc, two-pass correlation) | ✅ Done | `chromaprint.py` (fingerprint, validate_and_align, correlate) |
| Audio cross-mux (build_crossmux_command) | ✅ Done | `remux.py:877` |
| Video quality upgrades (build_video_upgrade_command) | ✅ Done | `remux.py:972` |
| Video quality probing (probe_video_quality) | ✅ Done | `remux.py:308` |
| Gap scanner (scan_library_gaps) | ✅ Done | `enrichment.py:360` |
| Duration guard (60s tolerance) | ✅ Done | `enrichment.py:727` |
| Failed entry retry (7-day cooldown) | ✅ Done | `enrichment.py:407-419` |
| State compaction (5000-entry cap) | ✅ Done | `enrichment.py:192` |
| Arr rescan after enrichment | ✅ Done | `enrichment.py:1426` (_refresh_arr_item) |
| EnrichmentConfig model | ✅ Done | `models.py:162` |
| libchromaprint-tools in Docker | ✅ Done | `Dockerfile:14`, `Dockerfile.dev:17` |
| qBittorrent enrichment category | ✅ Done | `qb.py:607` |

### Activation Required

The enrichment pipeline is fully implemented but needs activation:

1. **Rebuild Docker image** — picks up `libchromaprint-tools` (fpcalc binary)
2. **Apply Stack** — creates `enrichment` qBT category
3. **Restart pipeline worker** — picks up `enrichment.enabled: true` from stack.yaml
4. Verify `fpcalc -version` works inside the container

Current stack.yaml enrichment config:
```yaml
enrichment:
  enabled: true
  search_interval_hours: 24
  max_grabs_per_cycle: 2
  search_queries: [dual audio, english dub, multi]
  min_seeders: 3
  correlation_threshold: 0.7
  fingerprint_duration_seconds: 120
  target_languages: [eng, original]
  upgrade_video: true
  target_resolution: 1080p
  prefer_hdr: true
  prefer_hevc: true
```

### Enrichment Pipeline Flow (Phases 2-4)

1. **SEARCH** — Query Prowlarr for alternate releases with the missing audio language
2. **DOWNLOAD** — Grab the best candidate via qBittorrent
3. **GUARD** — Duration guard: reject if candidate duration differs by more than 60s from library file
4. **FINGERPRINT** — Run `fpcalc` (chromaprint) on both library and candidate audio tracks (always, no exceptions)
5. **CORRELATE** — Cross-correlate the two fingerprints to find alignment offset and match confidence
6. **VALIDATE** — Require at least 70% correlation score; reject below threshold
7. **MUX** — Use `ffmpeg -c copy` to merge the candidate audio track into the library file, applying the computed offset
8. **VERIFY** — Probe the output file to confirm all expected tracks are present and playable
9. **REPLACE** — Atomic file replacement: write to temp file, then `os.rename()` over the original
10. **CLEANUP** — Remove candidate download, temp files, and update state tracking

### Key Design Decisions

- **Always run chromaprint** — No exceptions, even when durations match exactly. The fingerprint correlation is the source of truth for alignment quality.
- **Atomic file replacement** — Output is written to a temp file alongside the original, then atomically renamed. Prevents corruption if the process is interrupted.
- **60-second duration guard** — Candidates with duration deltas exceeding 60s are rejected before fingerprinting. Catches fundamentally different cuts (theatrical vs director's, wrong content).
- **70% correlation threshold** — Fingerprint cross-correlation must exceed 0.70 to proceed with muxing. Below this, the candidate is considered a bad match and discarded.
- **ffmpeg `--enable-chromaprint`** — Already compiled into the pipeline container image. `libchromaprint1` is installed. No additional dependencies required.
