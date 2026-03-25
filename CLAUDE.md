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
1. **Phase 1** — Process completed qBittorrent torrents (remux + import)
2. **Phase 1.5** — Health/stall detection (kill dead torrents, exponential backoff)
3. **Phase 1.9** — Cleanup processed sources
4. **Phase 2** — Scan orphans (untracked files in scratch)
5. **Phase 3** — Stale staging file cleanup
6. **Phase 3.5** — Stale orphan source cleanup (3-day TTL)
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
- `orchestrator/pipeline/` — Media processing (runner.py, worker.py, remux.py, health.py, prowlarr_fallback.py)
- `frontend/src/` — React UI (TypeScript)
- `templates/` — Jinja2 templates (docker-compose.yml.j2, env.j2, secrets/)
- `generated/` — Rendered output (docker-compose.yml, .env, .secrets/)
- `scripts/` — Production scripts (dev.sh)
- `scripts/archived/` — One-off remediation scripts (historical)
- `docs/archive/` — Completed session notes and incident reports

### Key Files
- `stack.yaml` — Main configuration (user-editable via UI)
- `orchestrator/app.py` — FastAPI entrypoint + API routes
- `orchestrator/models.py` — Pydantic config/state models (StackConfig, BackfillConfig)
- `orchestrator/validators.py` — Path validation with container-aware mapping
- `orchestrator/converge/runner.py` — Main apply/converge logic
- `orchestrator/pipeline/runner.py` — Pipeline worker loop + metadata matching + nightly automation
- `orchestrator/pipeline/worker.py` — Remux plan builder, episode parsing, output path computation
- `orchestrator/pipeline/remux.py` — FFmpeg command builder with language/codec filtering
- `orchestrator/pipeline/health.py` — Stall detection, exponential backoff, category-aware blocklisting
- `orchestrator/pipeline/prowlarr_fallback.py` — Direct Prowlarr search + grab metadata tracking
- `orchestrator/clients/qb.py` — qBittorrent provisioning (auto_tmm, seeding policy)
- `orchestrator/clients/bazarr.py` — Bazarr subtitle provider provisioning

### Service Client Pattern
Each service has a dedicated client in `orchestrator/clients/`:
- `qb.py` — qBittorrent (auth, categories, preferences, auto_tmm)
- `radarr.py` / `sonarr.py` — Arr services (CDH management, download clients, root folders)
- `prowlarr.py` — Indexer management, FlareSolverr tagging, app linking, auto-populate
- `jellyseerr.py` — Request aggregator, language profiles, provider connections
- `jellyfin.py` — Media server wizard, users, libraries
- `bazarr.py` — Subtitle providers, language profiles, Radarr/Sonarr integration

All clients use httpx and follow a consistent ensure/verify pattern.

## Important Design Decisions

### CDH (Completed Download Handling)
Disabled when pipeline is active to prevent duplicate imports. The pipeline is the sole importer. CDH auto-enables when pipeline is disabled as fallback. Code: `sonarr.py` + `radarr.py` lines ~176.

### qBittorrent Seeding Policy
`max_ratio_enabled: false`, `max_seeding_time_enabled: false` — the pipeline controls torrent lifecycle (remove after successful import). `auto_tmm_enabled: true` — files go to category-specific save paths. Code: `qb.py` `_configure_preferences()`.

### Anime Absolute Episode Mapping
When Sonarr metadata is available for a TV series, the pipeline queries `/api/v3/episode` to build an absolute→season map. Anime packs using absolute numbering (e.g., "Show - 50") get correctly mapped to season episodes (e.g., S02E23). Code: `runner.py` `_build_absolute_episode_map()`.

### Bazarr Adaptive Searching
Disabled. Was causing false "not found" results due to provider rate limits, which locked items out for weeks. With 8 providers and rate limit management, items are retried every 6 hours. Code: `config.yaml` `adaptive_searching: false`.

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
- Cyrillic/Unicode filenames fail in ffmpeg subprocess (Russian-titled releases)
- Overwrite protection only compares file size (should consider resolution, subs, audio)
- ~200 anime files missing English subtitles (free provider coverage gap)
- S00 special episodes not mapped in absolute numbering
- Audio dedup (keep best per language, strip commentaries) — design pending
- Cross-mux English audio from dual-audio releases — future enrichment pipeline
- EZNAS UI needs redesign for better per-service settings

## Future: Media Enrichment Pipeline

A planned audio cross-mux system that finds and merges missing-language audio tracks from alternate releases into existing library files. Uses chromaprint-based audio fingerprinting for frame-accurate alignment.

### Approach: Chromaprint Audio Sync

The enrichment pipeline uses acoustic fingerprinting (chromaprint/fpcalc) to align audio tracks between different releases of the same content. This handles cases where releases have different cuts, intros, or slight timing offsets. FFmpeg is already compiled with `--enable-chromaprint` and `libchromaprint1` is installed in the pipeline container.

### 4-Phase Implementation Plan

**Phase 1: Subtitle extraction** — Extract and catalog subtitle tracks from existing library files. Build a metadata index of what each file already contains (audio languages, subtitle languages, codecs). Low risk, read-only operations.

**Phase 2: Duration-matched cross-mux** — Search for alternate releases that have the desired audio language (e.g., English dub for anime). Apply a 60-second duration guard: if the candidate's duration differs by more than 60s from the library file, reject it. Mux matching audio tracks into the library file using `ffmpeg -c copy`.

**Phase 3: Active dub searching** — Proactively search indexers (via Prowlarr) for dual-audio or specifically-dubbed releases when the library file is missing a configured language. Download candidates, extract the needed audio, mux it in, then clean up the source.

**Phase 4: Chromaprint sync** — Full acoustic fingerprint alignment for cases where duration-matching is insufficient. Generate chromaprint fingerprints for both the library file and the candidate, correlate them to find the precise offset, then use that offset when muxing to produce frame-accurate audio sync.

### 10-Step Pipeline Flow

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
