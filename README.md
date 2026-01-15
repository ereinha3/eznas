# NAS Orchestrator

Automated, zero-touch provisioning for a NAS media stack (qBittorrent, Radarr,
Sonarr, Prowlarr, Jellyseerr, Jellyfin, optional Traefik). A FastAPI backend
renders docker compose from templates, brings services up, configures them via
API, and performs a post-apply verification pass to confirm state.

## Progress Report (Detailed)

### What Is Working

**Core orchestration**
- FastAPI backend exposes `/api/config`, `/api/validate`, `/api/render`,
  `/api/apply`, `/api/status`, and secrets endpoints.
- Converge pipeline sequence: validate -> prepare dirs -> render -> compose up ->
  configure -> verify -> finalize.
- Config/state persistence uses `stack.yaml` and `state.json`. `ORCH_ROOT` can
  override where these live (container friendly).

**Service automation (configure)**
- qBittorrent: auth repair, preferences, categories.
- Radarr: API key discovery, UI auth, root folder, qBittorrent download client.
- Sonarr: API key discovery, UI auth, root folders (TV + anime), qBittorrent
  download client.
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
- Vite dev server supports `/api` proxy via `VITE_API_ORIGIN`.

**Docker**
- Local multi-stage Dockerfile builds UI + backend into one container.
- Compose template includes `orchestrator` service (local build, no registry).

### Status Summary

- Backend core: **stable**
- UI (built, served via FastAPI): **stable**
- UI (Vite dev server): **stable** (requires `VITE_API_ORIGIN` if not localhost)
- Service configure automation: **stable**
- Post-apply verification: **stable**
- Pipeline worker: **stub only**
- Bootstrap "one command" compose: **pending**
- Frontend dev container in compose: **pending**

### Known Issues / Constraints
- `.env` files are blocked in this workspace; use `VITE_API_ORIGIN` when running
  Vite manually.
- First-run still requires either running FastAPI locally or a bootstrap compose
  to render the main compose bundle.
- Verification failures are logged but not yet surfaced as rich UI badges.

## How To Run (Current)

### Development (manual)
Backend:
```
cd /home/ethan/eznas/nas_orchestrator
source .venv/bin/activate
uvicorn orchestrator.app:app --host 0.0.0.0 --port 8443
```

Frontend (optional dev UI):
```
cd /home/ethan/eznas/nas_orchestrator/frontend
npm install
VITE_API_ORIGIN=http://localhost:8443 npm run dev -- --host 0.0.0.0 --port 5173
```

### Built UI (no Vite)
```
cd /home/ethan/eznas/nas_orchestrator/frontend
npm install
npm run build
```
Then run the backend and hit `http://<host>:8443/`.

### Docker Compose (current)
Render the compose bundle (once) then bring everything up:
```
cd /home/ethan/eznas/nas_orchestrator
source .venv/bin/activate
uvicorn orchestrator.app:app --host 0.0.0.0 --port 8443
```
Use the UI to `Render` or `Apply`, then:
```
cd /home/ethan/eznas/nas_orchestrator/generated
docker compose up -d --build
```

## Current Objectives

- Create a bootstrap compose so users can run one `docker compose up` and land
  on the admin UI immediately.
- Add a frontend dev container (Vite) to the stack for UI development without
  running npm manually.
- Add richer verification UI and a "Verify only" action.

