# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

NAS Orchestrator is an automated, zero-touch provisioning system for a NAS media stack (qBittorrent, Radarr, Sonarr, Prowlarr, Jellyseerr, Jellyfin, optional Traefik). It provides a FastAPI backend with React frontend that renders Docker Compose from Jinja2 templates, deploys services, configures them via their APIs, and runs a media processing pipeline.

## Development Workflows

### Option 1: Docker Dev Environment (Recommended)
```bash
# Start with hot reload (backend + frontend)
./scripts/dev.sh up

# Start with all media services
./scripts/dev.sh up-full

# Start with pipeline worker
./scripts/dev.sh up-pipeline

# Stop everything
./scripts/dev.sh down
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

### Production Deployment
```bash
# Bootstrap the orchestrator (first time)
cp .env.example .env  # Edit paths
docker compose -f docker-compose.bootstrap.yml up -d

# Access UI at http://localhost:8443
# Configure and deploy media stack through UI
```

### Testing
```bash
# Python tests
pytest

# Type checking
mypy orchestrator/

# Linting
ruff check orchestrator/
cd frontend && npm run lint

# Pipeline testing (direct mode, no qBittorrent needed)
python test_pipeline.py --source /path/to/file.mkv --category movies --direct
```

### Docker Compose Files
- `docker-compose.dev.yml` - Development with hot reload
- `docker-compose.bootstrap.yml` - Production deployment
- `generated/docker-compose.yml` - Rendered media stack (created by UI)

## Architecture

### Data Flow
1. User configures stack via React UI → `stack.yaml` saved
2. User clicks "Apply" → FastAPI triggers `ApplyRunner`
3. `ApplyRunner`: validate → render compose (Jinja2) → `docker compose up` → configure services via API clients → verify
4. SSE streams real-time events to UI
5. `PipelineWorker` polls qBittorrent → remuxes completed downloads (ffmpeg -c copy) → moves to library

### Key Directories
- `orchestrator/` - Python backend (FastAPI, Pydantic models, service clients)
- `orchestrator/clients/` - API clients for each service (qb.py, radarr.py, sonarr.py, etc.)
- `orchestrator/converge/` - Orchestration pipeline (runner.py, services.py)
- `orchestrator/pipeline/` - Media processing (worker.py, remux.py)
- `frontend/src/` - React UI (TypeScript)
- `templates/` - Jinja2 templates (docker-compose.yml.j2, env.j2)
- `generated/` - Rendered output (docker-compose.yml, .env, .secrets/)

### Key Files
- `stack.yaml` - Main configuration (user-editable)
- `state.json` - Runtime state, secrets, processed torrents (auto-generated)
- `orchestrator/app.py` - FastAPI entrypoint
- `orchestrator/models.py` - Pydantic config/state models
- `orchestrator/converge/runner.py` - Main apply/converge logic
- `orchestrator/pipeline/runner.py` - Pipeline worker loop + qBittorrent API
- `orchestrator/pipeline/remux.py` - FFmpeg command builder with language filtering

### Service Client Pattern
Each service has a dedicated client in `orchestrator/clients/`:
- `qb.py` - qBittorrent (auth, categories, preferences)
- `radarr.py` / `sonarr.py` - Arr services (root folders, download clients, quality profiles)
- `prowlarr.py` - Indexer management, app linking
- `jellyseerr.py` - Request aggregator setup
- `jellyfin.py` - Media server wizard, users, libraries

All clients use async httpx and follow a consistent pattern for API calls.

## Environment Variables
- `ORCH_ROOT` - Override config root directory (default: project root)
- `VITE_API_ORIGIN` - API origin for Vite dev server (default: `http://localhost:8443`)
- `PIPELINE_INTERVAL` - Pipeline worker polling interval in seconds (default: 60)

## Tech Stack
- **Backend**: Python 3.10+, FastAPI, Pydantic, Jinja2, httpx, sse-starlette
- **Frontend**: TypeScript, React 19, Vite
- **Infrastructure**: Docker, Docker Compose, optional Traefik
