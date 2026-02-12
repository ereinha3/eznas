# Development vs Production Setup Guide

## Overview

This project has two Docker setups for different purposes:

### 1. **Dev Setup** (`docker-compose.dev.yml` + `Dockerfile.dev`)
**Purpose**: Active development with hot reload

**Features**:
- ✅ **Hot reload**: Code changes reflect immediately (no rebuild needed)
- ✅ **Separate frontend**: Vite dev server with HMR on port 5173
- ✅ **Fast iteration**: Edit code → see changes instantly
- ✅ **Debug-friendly**: Mounts source code as volumes
- ✅ **Optional media services**: Can include test media services with `--profile full`

**When to use**: 
- You're actively coding/testing
- You want to see changes immediately
- You're iterating on features

**How to use**:
```bash
# Start orchestrator + frontend (hot reload)
./scripts/dev.sh up

# Or with all test media services
./scripts/dev.sh up-full

# Stop everything
./scripts/dev.sh down
```

**Container names**: `orchestrator-dev`, `frontend-dev`, `qbittorrent-dev`, etc.

---

### 2. **Production Setup** (`docker-compose.bootstrap.yml` + `Dockerfile`)
**Purpose**: Stable deployment, production use

**Features**:
- ✅ **Optimized image**: Frontend pre-built and baked into image
- ✅ **Single container**: Everything in one place
- ✅ **No hot reload**: Stable, production-ready
- ✅ **Smaller footprint**: No dev dependencies

**When to use**:
- You're deploying to a server
- You want a stable, production-ready setup
- You're done with active development

**How to use**:
```bash
# First time setup
docker compose -f docker-compose.bootstrap.yml up -d

# Access UI at http://localhost:8443
```

**Container names**: `nas-orchestrator`, `nas-pipeline`

---

### 3. **Generated Stack** (`generated/docker-compose.yml`)
**Purpose**: The actual media services you're managing

**Features**:
- ✅ **Auto-generated**: Created by the orchestrator UI
- ✅ **Your media stack**: qBittorrent, Radarr, Sonarr, etc.
- ✅ **Managed by UI**: Configure and deploy through the web interface

**When to use**: Always (this is what you're orchestrating!)

**How to use**:
1. Start orchestrator (dev or prod)
2. Open UI at http://localhost:8443
3. Configure your stack
4. Click "Apply" → generates and starts `generated/docker-compose.yml`

**Container names**: `qbittorrent`, `radarr`, `sonarr`, `jellyfin`, etc. (no `-dev` suffix)

---

## Recommended Workflow for Testing/Development

Since you're in a testing phase, here's the best approach:

### **Use Dev Setup for Now** ✅

**Why**:
1. **Hot reload** = faster iteration when fixing bugs
2. **Separate frontend** = see UI changes instantly
3. **Easy debugging** = can exec into containers, see logs easily
4. **Conflict detection** = we just fixed it to auto-stop dev services when applying

**Workflow**:
```bash
# 1. Start dev orchestrator (for coding/testing)
./scripts/dev.sh up

# 2. In another terminal, start test media services (optional)
docker compose -f docker-compose.dev.yml --profile full up -d

# 3. Make code changes → they auto-reload
# 4. Test in UI at http://localhost:8443
# 5. When ready to test "Apply", click it in UI
#    → Dev services auto-stop, generated stack starts

# 6. When done testing
./scripts/dev.sh down
docker compose -f generated/docker-compose.yml down  # Stop generated stack
```

### **Ignore Prod Setup for Now** ⏸️

**Why**:
- You're not deploying yet
- Dev setup is better for testing
- Prod setup is for final deployment
- No need to maintain both during active development

**When to switch to prod**:
- You're ready to deploy to a server
- You want a stable, production-ready setup
- You're done with active development

---

## Key Differences Summary

| Feature | Dev Setup | Prod Setup |
|---------|-----------|-----------|
| **Hot Reload** | ✅ Yes | ❌ No |
| **Frontend** | Separate Vite server | Pre-built in image |
| **Code Changes** | Instant | Requires rebuild |
| **Container Names** | `*-dev` suffix | `nas-orchestrator` |
| **Use Case** | Development/Testing | Production Deployment |
| **Rebuild Needed** | Only for dependencies | Every code change |

---

## Common Scenarios

### Scenario 1: "I'm actively coding and testing"
→ **Use dev setup** (`./scripts/dev.sh up`)

### Scenario 2: "I want to test the full provisioning flow"
→ **Use dev setup** + click "Apply" in UI (dev services auto-stop)

### Scenario 3: "I'm deploying to my NAS server"
→ **Use prod setup** (`docker-compose.bootstrap.yml`)

### Scenario 4: "I just want to run the orchestrator, not develop"
→ **Use prod setup** (simpler, no dev overhead)

---

## Troubleshooting

### "Port conflicts between dev and generated stack"
✅ **Fixed!** The conflict detection now auto-stops dev services before applying.

### "I see duplicate containers"
→ Make sure you're not running both dev and prod orchestrator at the same time
→ Use `docker ps` to check what's running
→ Stop dev services: `./scripts/dev.sh down`

### "Changes aren't reflecting"
→ Dev setup: Check that volumes are mounted correctly
→ Prod setup: Rebuild image: `docker compose -f docker-compose.bootstrap.yml build`

---

## Quick Reference

```bash
# Dev setup
./scripts/dev.sh up          # Start dev orchestrator + frontend
./scripts/dev.sh up-full     # Start with test media services
./scripts/dev.sh down        # Stop everything
./scripts/dev.sh logs        # View logs

# Prod setup
docker compose -f docker-compose.bootstrap.yml up -d    # Start
docker compose -f docker-compose.bootstrap.yml down      # Stop

# Generated stack (managed by UI)
# Start/stop via UI "Apply" button, or manually:
docker compose -f generated/docker-compose.yml up -d
docker compose -f generated/docker-compose.yml down
```
