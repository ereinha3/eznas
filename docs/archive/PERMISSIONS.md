# NAS Orchestrator - Permissions Guide

## Overview

NAS Orchestrator uses a **group-based permission model** to ensure all Docker services can safely access your shared storage.

## The Permission Model

### 1. Shared Group: `nas-users`

All services run as a shared group (`nas-users`, GID 1001 by default). This allows:
- **Multiple services** to read/write the same files
- **Proper security** through group membership instead of world-writable permissions
- **Easy management** - add/remove services from the group

### 2. Directory Structure

**Recommended Approach: Use a Subdirectory**

Instead of NAS Orchestrator owning the entire `/mnt/pool`, create a dedicated subdirectory:

```
/mnt/pool/                    (existing pool - owned by you/other services)
├── backups/                  (your existing backups - FileExplorer, etc.)
├── documents/                (your existing documents)
└── media/                    (dedicated for NAS Orchestrator)
    ├── movies/               (775, nas-users)
    ├── tv/                   (775, nas-users)
    ├── music/                (775, nas-users)
    └── downloads/            (775, nas-users)
```

**Why use a subdirectory?**
- ✅ NAS Orchestrator only manages its own media files
- ✅ Existing services (FileExplorer, backups) continue working unchanged
- ✅ Better isolation and security
- ✅ Easier to backup/restore just the media portion

**Alternative: Full Pool Ownership**

If you want NAS Orchestrator to manage the entire pool:
```
/mnt/pool/                    (775, nas-users)
├── media/
├── downloads/
└── appdata/
```

⚠️ **Warning**: This will require changing permissions on your entire pool.

### 3. Permission Levels

- **Owner (root)**: Full control for system administration
- **Group (nas-users)**: Read/Write for all NAS services
- **Others**: Read-only (prevents accidental deletion by non-service users)

## Initial Setup

### Option 1: Automatic Setup (Recommended)

Run the provided setup script:

```bash
sudo ./scripts/setup-permissions.sh /mnt/pool
```

This will:
1. Create the `nas-users` group
2. Add your user to the group
3. Set correct ownership on `/mnt/pool`
4. Configure Docker to use the group

### Option 2: Manual Setup

If you prefer manual control:

```bash
# 1. Create the shared group
sudo groupadd -g 1001 nas-users

# 2. Add your user to the group
sudo usermod -aG nas-users $USER

# 3. Create and configure the pool directory
sudo mkdir -p /mnt/pool
sudo chown -R root:nas-users /mnt/pool
sudo chmod -R 775 /mnt/pool

# 4. Apply group changes (log out and back in, or use newgrp)
newgrp nas-users
```

## Verifying Permissions

To check if permissions are correct:

```bash
# Check pool directory
ls -la /mnt/pool
# Should show: drwxrwxr-x ... root nas-users ...

# Check group membership
groups $USER
# Should include: nas-users

# Test write access
touch /mnt/pool/test-file && rm /mnt/pool/test-file
```

## Troubleshooting

### Issue: "Permission denied" when applying configuration

**Cause**: The Docker containers can't write to the pool directory.

**Solution**:
```bash
# Fix ownership
sudo chown -R :nas-users /mnt/pool
sudo chmod -R 775 /mnt/pool

# Restart containers to pick up group changes
docker compose restart
```

### Issue: Group changes not taking effect

**Cause**: Group membership is cached in your shell session.

**Solution**:
```bash
# Log out and back in, or use:
newgrp nas-users

# Verify with:
id
# Should show: gid=...(...), groups=...,1001(nas-users),...
```

### Issue: New files created with wrong permissions

**Cause**: Default umask is too restrictive.

**Solution**: Set default ACL for new files:
```bash
sudo setfacl -R -m g:nas-users:rwx /mnt/pool
sudo setfacl -d -m g:nas-users:rwx /mnt/pool
```

## Docker Compose Configuration

Services are configured to use the shared group:

```yaml
services:
  qbittorrent:
    user: "1000:1001"  # uid:nas-users-gid
    group_add:
      - "1001"
    volumes:
      - /mnt/pool:/data/pool
```

## Security Considerations

1. **Don't use root**: Services should not run as root inside containers
2. **Don't use 777**: World-writable permissions are a security risk
3. **Group isolation**: Only services in the `nas-users` group can access pool data
4. **Regular audits**: Check permissions periodically with `find /mnt/pool -not -group nas-users`

## Advanced: Multiple Pools

If you have multiple storage pools:

```bash
# Create additional pools with same group
sudo mkdir -p /mnt/fast-ssd
sudo chown -R :nas-users /mnt/fast-ssd
sudo chmod -R 775 /mnt/fast-ssd
```

Then add them in the NAS Orchestrator UI under "Additional Storage".

## Migration from Existing Setup

If you already have files in your pool:

```bash
# Fix existing files
sudo chown -R :nas-users /mnt/pool
sudo chmod -R 775 /mnt/pool

# If you have specific service folders, ensure they're accessible
sudo find /mnt/pool -type d -exec chmod 775 {} \;
sudo find /mnt/pool -type f -exec chmod 664 {} \;
```
