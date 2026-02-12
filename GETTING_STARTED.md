# Getting Started with NAS Orchestrator

This guide walks you through setting up NAS Orchestrator from a fresh clone, whether you're a developer or an end user.

## Table of Contents

1. [Quick Start (End Users)](#quick-start-end-users)
2. [Development Setup](#development-setup)
3. [First Login & Configuration](#first-login--configuration)
4. [Understanding the Setup Flow](#understanding-the-setup-flow)
5. [Troubleshooting](#troubleshooting)

---

## Quick Start (End Users)

### Prerequisites

- Docker and Docker Compose installed
- A Linux host (Ubuntu/Debian recommended) with:
  - At least 4GB RAM
  - 20GB free disk space (for containers and media)
  - Ports 80, 443, 8443, and service ports available

### Step 1: Clone the Repository

```bash
git clone <repository-url>
cd nas_orchestrator
```

### Step 2: Bootstrap the Orchestrator

Run the bootstrap compose file to start the orchestrator with its web UI:

```bash
docker compose -f docker-compose.bootstrap.yml up -d
```

This will:
- Build the NAS Orchestrator container
- Start the web UI on port 8443
- Create necessary Docker volumes for persistence

### Step 3: Access the Web UI

Open your browser and navigate to:

```
http://localhost:8443
```

Or if accessing remotely:

```
http://<your-server-ip>:8443
```

### Step 4: Login with Default Credentials

On first startup, the system automatically creates default admin credentials. These are displayed prominently on the login page:

**Example:**
- **Username:** `admin`
- **Password:** `bCUrCpWoD0m0hqFj` (randomly generated, yours will be different)

⚠️ **Important:** The default password is shown only once on the login screen. Copy it immediately!

### Step 5: Configure Your Stack

After logging in:

1. **Storage Paths** - Set your media library locations:
   - **Pool Path:** Where your media library lives (e.g., `/mnt/pool`)
   - **Scratch Path:** Where downloads are staged (e.g., `/mnt/pool/downloads`)
   - **AppData Path:** Where service configs are stored (e.g., `/home/user/appdata`)

2. **Services** - Choose which services to enable:
   - qBittorrent (torrent client)
   - Radarr (movie management)
   - Sonarr (TV show management)
   - Prowlarr (indexer management)
   - Jellyseerr (request management)
   - Jellyfin (media server)
   - Pipeline (post-processing)

3. **Network** - Configure proxy settings (optional):
   - Enable Traefik for reverse proxy
   - Set custom domains for services

4. **Policies** - Set media preferences:
   - Audio/subtitle language preferences
   - Quality settings (1080p, 4K, etc.)
   - Container format (MKV, MP4)

### Step 6: Apply Configuration

Click **"Apply"** to deploy your stack. The orchestrator will:

1. Validate your configuration
2. Generate Docker Compose files
3. Start all services
4. Configure each service automatically
5. Verify everything is working

Watch the live logs to see progress!

---

## Development Setup

### Option 1: Local Development (Recommended for UI work)

**Terminal 1 - Backend:**
```bash
cd nas_orchestrator
source .venv/bin/activate
uvicorn orchestrator.app:app --reload --host 0.0.0.0 --port 8443
```

**Terminal 2 - Frontend:**
```bash
cd nas_orchestrator/frontend
npm install
VITE_API_ORIGIN=http://localhost:8443 npm run dev -- --host 0.0.0.0
```

Access the dev UI at `http://localhost:5173`

### Option 2: Docker Development (Full Stack)

```bash
docker compose -f docker-compose.dev.yml up -d
```

This starts:
- Orchestrator backend on port 8443
- Frontend dev server on port 5173
- All media services (with `--profile full`)

---

## First Login & Configuration

### What Happens on First Startup?

When the orchestrator starts for the first time:

1. **Default Admin Creation:** The system automatically creates an `admin` user with a randomly generated secure password
2. **Credential Display:** The password is displayed on the login page in a yellow notification box
3. **Auto-hide:** The notification disappears after 30 seconds for security
4. **One-time Display:** The password is logged to the container logs and shown in the UI only until first login

### Locating Your Default Password

If you missed the password on the login screen:

**Method 1: Check Container Logs**
```bash
docker logs nas-orchestrator 2>&1 | grep "Created default admin"
# Output: Created default admin user (admin / YOUR_PASSWORD_HERE)
```

**Method 2: API Endpoint**
```bash
curl http://localhost:8443/api/setup/status
# Returns: {"needs_setup":false,"has_config":true,"default_password":"YOUR_PASSWORD"}
```

**Method 3: State File** (Advanced)
```bash
cat generated/state.json | jq '.auth._setup.default_password'
```

### Changing the Default Password

After your first login:

1. Go to **Settings** or **User Management**
2. Click **Change Password**
3. Enter your current password (the default)
4. Set your new secure password

⚠️ **Security Note:** Always change the default password immediately after first login!

---

## Understanding the Setup Flow

### Architecture Overview

```
User → Web UI (React) → FastAPI Backend → Docker Compose → Services
                           ↓
                    Configuration Files
                    (stack.yaml, state.json)
```

### File Locations

| File | Purpose | Location |
|------|---------|----------|
| `stack.yaml` | User configuration | Project root or `/config` in container |
| `state.json` | Runtime state & secrets | Project root or `/config` in container |
| `generated/docker-compose.yml` | Rendered compose file | `generated/` directory |
| `generated/.env` | Environment variables | `generated/` directory |
| `generated/.secrets/` | Service secrets | `generated/.secrets/` directory |

### Bootstrap vs. Generated Compose

**Bootstrap Compose** (`docker-compose.bootstrap.yml`):
- Runs only the orchestrator UI
- Used for initial setup
- Minimal footprint

**Generated Compose** (`generated/docker-compose.yml`):
- Created by the orchestrator based on your configuration
- Includes all enabled services
- Deployed after you click "Apply"

### Data Persistence

The orchestrator uses Docker volumes for persistence:

```yaml
volumes:
  orchestrator_config:/config  # Configuration and state
  ./generated:/config/generated  # Generated compose files
```

---

## Troubleshooting

### Issue: Can't Access Web UI

**Check if container is running:**
```bash
docker ps | grep orchestrator
```

**Check container logs:**
```bash
docker logs nas-orchestrator
```

**Verify port binding:**
```bash
docker port nas-orchestrator
# Should show: 8443/tcp -> 0.0.0.0:8443
```

### Issue: Default Credentials Not Showing

**Check setup status:**
```bash
curl http://localhost:8443/api/setup/status
```

If `default_password` is null, the password may have been cleared after first login. Check container logs for the original password.

### Issue: Permission Denied on Paths

Ensure the paths you configure in the UI:
- Exist on the host filesystem
- Are accessible by the container (mounted via `- /:/host:ro`)
- Have correct permissions (the orchestrator runs as your user by default)

### Issue: Services Won't Start

**Check for port conflicts:**
```bash
sudo netstat -tlnp | grep -E '80|443|8080|7878|8989|9696|5055|8096'
```

**Verify Docker socket access:**
The orchestrator needs access to Docker socket to manage containers:
```bash
ls -la /var/run/docker.sock
```

### Reset to Factory Defaults

To completely reset and start over:

```bash
# Stop all containers
docker compose -f docker-compose.bootstrap.yml down

# Clear configuration
rm -f stack.yaml state.json
rm -rf generated/

# Restart
sudo docker compose -f docker-compose.bootstrap.yml up -d
```

⚠️ **Warning:** This deletes all configuration and service data!

---

## Next Steps

After initial setup:

1. **Configure Services:** Use the Services tab to fine-tune each service
2. **Add Users:** Create additional Jellyfin users in the Users tab
3. **Set Up Indexers:** Configure Prowlarr with your preferred torrent indexers
4. **Test Downloads:** Add a test torrent to verify the pipeline works
5. **Monitor Logs:** Check the Logs tab for real-time activity

For detailed information about specific features, see:
- `README.md` - Architecture and feature overview
- `TESTING.md` - Testing the media pipeline
- `CLAUDE.md` - Development guidelines

---

## Support

If you encounter issues:

1. Check the container logs: `docker logs nas-orchestrator`
2. Verify your configuration: `curl http://localhost:8443/api/setup/status`
3. Review the troubleshooting section above
4. Open an issue with logs and configuration details

---

**Happy automating!** 🚀
