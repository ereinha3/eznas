# NAS Orchestrator

Automated, zero-touch provisioning for a NAS media stack (qBittorrent, Radarr,
Sonarr, Prowlarr, Jellyseerr, Jellyfin, optional Traefik). A FastAPI backend
renders docker compose from templates, brings services up, configures them via
API, and performs a post-apply verification pass to confirm state.

## Progress Report (Detailed)

### What Is Working

**Core orchestration**
- FastAPI backend exposes `/api/config`, `/api/validate`, `/api/render`,
  `/api/apply`, `/api/status`, `/api/credentials`, and service-specific credential endpoints.
- Converge pipeline sequence: validate -> prepare dirs -> render -> compose up ->
  configure -> verify -> finalize.
- Config/state persistence uses `stack.yaml` and `state.json`. `ORCH_ROOT` can
  override where these live (container friendly).
- SSE (Server-Sent Events) streaming for live apply logs in the UI.

**Service automation (configure)**
- qBittorrent: auth repair, preferences, categories, category save paths.
- Radarr: API key discovery, UI auth, root folder, qBittorrent download client, quality profiles, custom formats.
- Sonarr: API key discovery, UI auth, root folders (TV + anime), qBittorrent download client, quality profiles, custom formats.
- Prowlarr: app linking to Radarr/Sonarr.
- Jellyseerr: initial setup + Radarr/Sonarr links.
- Jellyfin: startup wizard, admin user, libraries, user creation endpoint.

**Verification (post-apply)**
- qBittorrent: auth + categories + category paths.
- Radarr: download client present and matches expected host/port/category/user.
- Sonarr: download client present and matches expected host/port/category/user.
- Prowlarr: Radarr/Sonarr applications exist and point to expected URLs.
- Jellyseerr: initialized and Radarr/Sonarr entries exist.

**UI**
- React UI in `frontend/` with API bindings in `frontend/src/api.ts`.
- FastAPI serves the built UI from `frontend/dist` (fallback to `ui/`).
- Vite dev server supports `/api` proxy via `VITE_API_ORIGIN` environment variable.
- Tabbed interface: Setup, Services, Preferences.
- Compact sidebar design for better space utilization.
- Service credentials panel with edit capabilities.
- Live apply logs via SSE.

**Media preferences & quality**
- Language preferences: configurable audio/subtitle language allowlists for movies and anime.
- Quality preferences: preset (balanced/1080p/4K), target resolution, max bitrate, preferred container (MKV/MP4).
- Quality preferences integrated into Radarr/Sonarr custom formats (internal, not exposed in UI).

**Pipeline worker**
- Continuous polling loop for completed qBittorrent downloads.
- Lossless remuxing using `ffmpeg -c copy` (no re-encoding).
- Language track stripping based on user preferences.
- Container format standardization (e.g., to MKV).
- Automatic file movement from staging to final library locations.
- Torrent cleanup (removes from qBittorrent after processing).
- Processed torrent tracking in `state.json`.
- Integrated into Docker Compose stack as `pipeline-worker` service.

**Docker**
- Local multi-stage Dockerfile builds UI + backend into one container.
- Compose template includes `orchestrator` service (local build, no registry).
- Pipeline worker runs as separate service in compose.
- `ffmpeg` included in orchestrator Docker image.

**Testing**
- Test script (`test_pipeline.py`) for simulating downloads and testing remux pipeline.
- Direct mode for testing without qBittorrent.
- Documentation in `TESTING.md`.

### Status Summary

- Backend core: **stable**
- UI (built, served via FastAPI): **stable**
- UI (Vite dev server): **stable** (requires `VITE_API_ORIGIN` if not localhost)
- Service configure automation: **stable**
- Post-apply verification: **stable**
- Pipeline worker: **implemented and integrated**
- Quality preferences: **implemented** (UI + backend, Radarr/Sonarr integration)
- Language preferences: **implemented** (UI + backend, pipeline integration)
- Bootstrap "one command" compose: **pending**
- Frontend dev container in compose: **pending**

### Known Issues / Constraints

- `.env` files are blocked in this workspace; use `VITE_API_ORIGIN` when running
  Vite manually.
- First-run still requires either running FastAPI locally or a bootstrap compose
  to render the main compose bundle.
- Verification failures are logged but not yet surfaced as rich UI badges.
- Custom formats in Radarr/Sonarr are created internally but not exposed in UI
  (simplified UX approach).

## How To Run (Current)

### Development (manual)
Backend:
```bash
cd /home/ethan/eznas/nas_orchestrator
source .venv/bin/activate
uvicorn orchestrator.app:app --host 0.0.0.0 --port 8443
```

Frontend (optional dev UI):
```bash
cd /home/ethan/eznas/nas_orchestrator/frontend
npm install
VITE_API_ORIGIN=http://localhost:8443 npm run dev -- --host 0.0.0.0 --port 5173
```

### Built UI (no Vite)
```bash
cd /home/ethan/eznas/nas_orchestrator/frontend
npm install
npm run build
```
Then run the backend and hit `http://<host>:8443/`.

### Docker Compose (current)
Render the compose bundle (once) then bring everything up:
```bash
cd /home/ethan/eznas/nas_orchestrator
source .venv/bin/activate
uvicorn orchestrator.app:app --host 0.0.0.0 --port 8443
```
Use the UI to `Render` or `Apply`, then:
```bash
cd /home/ethan/eznas/nas_orchestrator/generated
docker compose up -d --build
```

### Testing the Pipeline
See `TESTING.md` for detailed instructions. Quick example:
```bash
cd /home/ethan/eznas/nas_orchestrator
python test_pipeline.py --source /mnt/raid/data/media/movies/Some.Movie.mkv --category movies --direct
```

## Architecture

### Components

- **FastAPI Backend** (`orchestrator/app.py`): Main API server, serves UI, handles config/apply/status.
- **Converge Engine** (`orchestrator/converge/`): Orchestrates validation, deployment, configuration, verification.
- **Service Clients** (`orchestrator/clients/`): Python modules for each service API (qBittorrent, Radarr, Sonarr, etc.).
- **Config Repository** (`orchestrator/storage.py`): Manages `stack.yaml` and `state.json` persistence.
- **Compose Renderer** (`orchestrator/rendering.py`): Jinja2-based template rendering for docker-compose.yml.
- **Pipeline Worker** (`orchestrator/pipeline/`): Continuous loop for processing completed downloads (remux, move, cleanup).
- **Remux Engine** (`orchestrator/pipeline/remux.py`): Builds ffmpeg commands for lossless remuxing with language filtering.

### Data Flow

1. User configures stack via UI → `stack.yaml` saved.
2. User clicks "Apply" → FastAPI triggers `ApplyRunner`.
3. `ApplyRunner` validates → renders compose → `docker compose up` → configures services → verifies.
4. SSE streams events to UI in real-time.
5. Pipeline worker polls qBittorrent → processes completed downloads → remuxes → moves to library.

### Configuration Model

- **Paths**: Library pool, scratch volume, appdata root.
- **Runtime**: User/group IDs, timezone.
- **Services**: Enable/disable, ports, proxy URLs.
- **Download Policy**: Category names (radarr, sonarr, anime).
- **Media Policy**: Language preferences (audio/subtitle allowlists) for movies and anime.
- **Quality**: Preset, target resolution, max bitrate, preferred container.
- **Proxy**: Traefik configuration (optional).
- **Users**: Jellyfin user definitions.

## Current Objectives

- Create a bootstrap compose so users can run one `docker compose up` and land
  on the admin UI immediately.
- Add a frontend dev container (Vite) to the stack for UI development without
  running npm manually.
- Add richer verification UI and a "Verify only" action.
- Surface verification results per service in the UI.
- Improve diagnostics when verification fails (actionable hints).

## Recent Changes

- **Pipeline worker fully integrated**: Continuous service that processes completed downloads.
- **Quality preferences**: UI fields and backend integration for resolution, bitrate, container format.
- **Language preferences**: Configurable audio/subtitle filtering in UI and pipeline.
- **UI improvements**: Tabbed interface (Setup/Services/Preferences), compact sidebar.
- **Testing tools**: `test_pipeline.py` script for simulating downloads and testing remux pipeline.

## Quick Reference

### Key Files
- `stack.yaml`: Main configuration file (user-editable).
- `state.json`: Runtime state, secrets, run history (auto-generated).
- `orchestrator/app.py`: FastAPI entrypoint.
- `orchestrator/converge/runner.py`: Main apply/converge logic.
- `orchestrator/pipeline/runner.py`: Pipeline worker loop.
- `templates/docker-compose.yml.j2`: Docker Compose template.
- `frontend/src/App.tsx`: React UI main component.
- `test_pipeline.py`: Test script for pipeline testing.

### Environment Variables
- `ORCH_ROOT`: Override config root directory (default: project root).
- `VITE_API_ORIGIN`: API origin for Vite dev server (default: `http://localhost:8443`).
- `PIPELINE_INTERVAL`: Pipeline worker polling interval in seconds (default: 60).

### Important Paths
- Config root: `ORCH_ROOT` or project root.
- Generated files: `generated/` directory (docker-compose.yml, .env files).
- Frontend build: `frontend/dist/` (served by FastAPI).
- Templates: `templates/` directory (Jinja2 templates).

### Testing
See `TESTING.md` for detailed pipeline testing instructions.
