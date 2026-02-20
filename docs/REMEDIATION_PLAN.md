# NAS Orchestrator Remediation Plan

## Overview
Full audit and phased remediation of the NAS Orchestrator project.
Goal: Transform from brittle setup-focused tool into a robust, dashboard-first media stack orchestrator.

---

## Phase 0: Foundation Cleanup ✅ COMPLETE
*Make the existing code trustworthy before adding anything new.*

### 0.1 — Fix secrets leakage ✅
- [x] Remove `state.json` from git tracking (`git rm --cached`)
- [x] Remove `generated/` contents from git tracking (including `.secrets/`)
- [x] Remove screenshots and test artifacts from git (80+ PNG/WebM files)
- [x] Create `.dockerignore` (prevents secrets from leaking into Docker builds)
- [x] Create `state.json.example` (template for fresh installs)
- [x] Update `.gitignore` with comprehensive patterns (auth.json, secrets.json, runs.json, test artifacts, screenshots)

### 0.2 — Kill dead code ✅
- [x] Delete `EnhancedSetupWizard.tsx` (998 lines of unused, crash-prone code)
- [x] Remove its import from `App.tsx` line 14
- [x] Remove commented-out admin creation in `app.py` (13 lines of dead code)
- [x] Replace with clean log message when no users found

### 0.3 — Fix hardcoded credentials bomb ✅
- [x] Fix `runner.py` `_ensure_secrets()` — now reads admin credentials from state auth section
- [x] Remove "adminadmin" default from `QbittorrentConfig` model (set to empty string)
- [x] Remove "adminadmin" default from frontend `DEFAULT_CONFIG`
- [x] Remove "adminadmin" from `stack.yaml`
- [x] Setup wizard (`/api/setup/initialize`) now stores admin password in secrets for Jellyfin/Jellyseerr
- [x] Converge pipeline never hardcodes fallback passwords — only uses what setup wizard stored

### 0.4 — Atomic state writes ✅
- [x] Implement atomic writes via `_atomic_write()` (tmpfile + `os.replace` + `fsync`)
- [x] Both `save_state()` and `save_stack()` now use atomic writes
- [x] Added `load_secrets()` / `save_secrets()` section accessors
- [x] Added run history trimming (`MAX_RUN_HISTORY = 20`, auto-prune old runs)
- [x] Note: Full state.json split into separate files deferred to Phase 1 (30+ consumer touchpoints)

### 0.5 — Centralize constants ✅
- [x] Created `orchestrator/constants.py` with:
  - `INTERNAL_PORTS` — single source of truth for all container ports
  - `DEFAULT_HOST_PORTS` — default host-mapped ports
  - `CONTAINER_PATHS` — all hardcoded /data/movies, /downloads, etc.
  - `SERVICE_DEPENDENCY_ORDER` — converge pipeline ordering
  - `CONTAINER_NAMES` — Docker service names
  - `API_PREFIXES` — per-service API path prefixes
- [x] Updated `app.py` to use `INTERNAL_PORTS` from constants
- [x] Updated `runner.py` to use `INTERNAL_PORTS` from constants
- [x] Updated `services.py` to use `SERVICE_DEPENDENCY_ORDER` from constants

### Files Changed in Phase 0:
- `orchestrator/constants.py` — **NEW** (centralized constants)
- `orchestrator/storage.py` — atomic writes, run trimming, section accessors
- `orchestrator/app.py` — removed dead code, fixed credential flow, use constants
- `orchestrator/models.py` — removed default password
- `orchestrator/converge/runner.py` — fixed credential bomb, use constants
- `orchestrator/converge/services.py` — use centralized dependency order
- `frontend/src/App.tsx` — removed dead import, removed default password
- `frontend/src/components/EnhancedSetupWizard.tsx` — **DELETED**
- `.gitignore` — comprehensive update
- `.dockerignore` — **NEW**
- `state.json.example` — **NEW**
- `stack.yaml` — removed hardcoded password
- `docs/REMEDIATION_PLAN.md` — **NEW** (this file)

---

## Phase 1: Robustness ✅ COMPLETE
*Make the converge pipeline reliable.*

### 1.1 — Add retry with exponential backoff ✅
- [x] Created `orchestrator/clients/retry.py` with `retry_on_failure` decorator and `retry_request()` function
- [x] Exponential backoff: 1s → 2s → 4s → ... capped at 30s
- [x] Only retries connection errors and 5xx responses (never 4xx)
- [x] Integrated into `ArrAPI` class (`arr.py`) — covers Radarr, Sonarr, Prowlarr

### 1.2 — Dependency-aware service configuration ✅
- [x] Added `SERVICE_DEPENDENCIES` graph to `services.py`
- [x] If a service fails during `ensure()`, all dependents are automatically skipped
- [x] Clear messages: `"skipped (dependency failed: radarr, sonarr)"`
- [x] `verify()` intentionally does NOT skip — shows full health picture

### 1.3 — Config diff engine ✅
- [x] Created `orchestrator/converge/diff.py` with `compute_diff()` function
- [x] Full `CHANGE_IMPACT` mapping: which config fields affect which services
- [x] Longest-prefix matching for impact resolution (e.g., `services.radarr.port`)
- [x] `ConfigDiff` result with `services_to_restart` vs `services_to_reconfigure`
- [x] Integrated into `ApplyRunner.run()` — records diff at start of every apply
- [x] New `POST /api/config/preview` endpoint for frontend "BIOS-like" settings
- [x] `runner.preview()` method for lightweight diff without side effects

### 1.4 — Fix hardcoded paths in all clients ✅
- [x] All clients now use `CONTAINER_PATHS` from `constants.py`
- [x] `radarr.py`: `"/data/movies"` → `CONTAINER_PATHS["movies"]`
- [x] `sonarr.py`: `"/data/tv"` → `CONTAINER_PATHS["tv"]`
- [x] `jellyfin.py`: `"/data/movies"`, `"/data/tv"` → `CONTAINER_PATHS[...]`
- [x] `jellyseerr.py`: `"/data/media/movies"`, `"/data/media/tv"` → `CONTAINER_PATHS[...]`
- [x] `qb.py`: 5 occurrences of `/downloads/*` → `CONTAINER_PATHS[...]`
- [x] Zero hardcoded paths remaining in `orchestrator/clients/`

### 1.5 — Upgrade password hashing ✅
- [x] Upgraded from SHA-256 to scrypt (memory-hard KDF, stdlib `hashlib.scrypt`)
- [x] Zero new dependencies — uses Python 3.10+ stdlib
- [x] New hash format: `scrypt${salt_hex}${hash_hex}`
- [x] Backward compatible — still verifies legacy `{salt}${sha256}` hashes
- [x] Progressive rehashing — old hashes upgraded to scrypt on successful login
- [x] Constant-time comparison via `secrets.compare_digest()`

### 1.6 — State file split ✅
- [x] State split into separate files: `auth.json`, `secrets.json`, `services.json`, `runs.json`, `pipeline.json`
- [x] Auto-migration from legacy `state.json` on first access (renamed to `.migrated`)
- [x] Each section file has its own atomic write (crash in one can't corrupt another)
- [x] `load_state()`/`save_state()` compose from section files (backward compatible)
- [x] New direct section accessors: `load_services_state()`, `save_services_state()`, `load_pipeline_state()`, `save_pipeline_state()`
- [x] Updated `.gitignore` for all new section files

### Files Changed in Phase 1:
- `orchestrator/clients/retry.py` — **NEW** (retry utilities)
- `orchestrator/converge/diff.py` — **NEW** (config diff engine)
- `orchestrator/converge/services.py` — dependency-aware ensure/verify
- `orchestrator/converge/runner.py` — diff integration, preview method
- `orchestrator/clients/arr.py` — retry integration in ArrAPI
- `orchestrator/clients/radarr.py` — CONTAINER_PATHS usage
- `orchestrator/clients/sonarr.py` — CONTAINER_PATHS usage
- `orchestrator/clients/jellyfin.py` — CONTAINER_PATHS usage
- `orchestrator/clients/jellyseerr.py` — CONTAINER_PATHS usage
- `orchestrator/clients/qb.py` — CONTAINER_PATHS usage
- `orchestrator/storage.py` — state file split with migration
- `orchestrator/auth.py` — scrypt password hashing
- `orchestrator/app.py` — added `/api/config/preview` endpoint
- `.gitignore` — added new section file patterns

---

## Phase 2: UI Redesign — Setup Wizard (Future)
- [ ] Expand wizard to 5 steps (Welcome, Admin, Storage, Services, Review & Deploy)
- [ ] Guard route post-setup (wizard not accessible after initialization)
- [ ] Extract inline CSS to separate file
- [ ] Service selection step with visual grid and port config

## Phase 3: UI Redesign — Dashboard (Future)
- [ ] New dashboard layout (service status cards, storage usage, activity feed, quick links)
- [ ] Move all settings behind dedicated Settings page with internal tabs
- [ ] State management overhaul (Zustand for global store, eliminate prop drilling)
- [ ] CSS architecture (modules + design tokens, split App.css monolith)

## Phase 4: Polish & Hardening (Future)
- [ ] Error handling overhaul (toast/notification queue, error categories)
- [ ] Hot reload validation tests (port change → cascade → verify)
- [ ] Docker improvements (pinned image versions, healthchecks, resource limits)
- [ ] Testing (unit + integration + CI pipeline)
- [ ] Logging & observability (structured JSON logs, log rotation)
