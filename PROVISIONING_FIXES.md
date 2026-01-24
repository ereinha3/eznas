# Prowlarr Authentication Provisioning Fixes

## Summary

Fixed critical issues with Prowlarr authentication provisioning that prevented automatic user setup and indexer population. The orchestrator now properly configures Prowlarr's UI authentication programmatically and handles edge cases like stale API keys and network isolation.

## Problems Identified

1. **Authentication not configured**: Prowlarr was showing the initial setup wizard even though credentials existed in the orchestrator UI. The provisioning code wasn't running or was failing silently.

2. **API key sync issues**: The stored API key in `state.json` could become stale if Prowlarr regenerated its API key, causing authentication failures.

3. **Network connectivity**: When the orchestrator runs in a container (`orchestrator-dev`), it couldn't reach Prowlarr by container name (`prowlarr:9696`) because they're on different Docker networks.

4. **Health check failures**: Health checks were failing because the orchestrator container couldn't resolve service container names.

## Solutions Implemented

### 1. API Key Synchronization (`orchestrator/clients/prowlarr.py`)

**Before**: Only read API key from config.xml if it wasn't already stored.

**After**: Always read API key from `config.xml` on every ensure run and sync it with stored state:
```python
stored_api_key = prowlarr_secrets.get("api_key")
config_api_key = read_arr_api_key(config_dir)
api_key = config_api_key
if stored_api_key != config_api_key:
    prowlarr_secrets["api_key"] = config_api_key
    state_dirty = True
    detail_messages.append("refreshed API key from config.xml")
```

### 2. Dual Authentication Configuration

**Before**: Only configured authentication via `_ensure_host_settings()` before the ArrAPI context.

**After**: Configure authentication both before AND inside the ArrAPI context (matching Radarr's pattern):
- First attempt: `_ensure_host_settings()` before ArrAPI context
- Fallback: Configure authentication inside ArrAPI context if first attempt fails
- This ensures authentication is configured even if the initial attempt fails

### 3. Retry Logic for Stale API Keys

**Before**: If API calls failed with 401/403, provisioning would fail.

**After**: Catch 401/403 errors, refresh API key from config.xml, and retry:
```python
except httpx.HTTPStatusError as exc:
    if exc.response.status_code in (401, 403):
        refreshed_key = read_arr_api_key(config_dir)
        if refreshed_key and refreshed_key != api_key:
            # Update state and retry
            _provision(refreshed_key)
```

### 4. Network Connectivity Fixes

**Before**: All Prowlarr client methods used `http://prowlarr:9696` (container name).

**After**: Changed to use `http://127.0.0.1:{configured_port}` to work when orchestrator is in a container:
- `get_available_indexers()`
- `get_configured_indexers()`
- `add_indexers()`
- `remove_indexer()`
- `auto_populate_indexers()`

### 5. Health Check Improvements (`orchestrator/app.py`)

**Before**: Only tried container name and `127.0.0.1`.

**After**: Added fallback to `host.docker.internal` for Docker Desktop scenarios:
```python
is_healthy = (
    _check_port(name, internal_port) 
    or _check_port("127.0.0.1", port)
    or _check_port("host.docker.internal", port)  # Docker Desktop
)
```

## Code Changes

### Files Modified

1. **`orchestrator/clients/prowlarr.py`**:
   - Updated `ensure()` method to always sync API key from config.xml
   - Added dual authentication configuration (before + inside ArrAPI context)
   - Added retry logic for 401/403 errors
   - Updated all API methods to use `127.0.0.1:{port}` instead of container names
   - Added debug logging for troubleshooting

2. **`orchestrator/app.py`**:
   - Enhanced health check to try `host.docker.internal` as fallback

## Testing & Verification

### Manual Testing Steps

1. **Stop conflicting services**:
   ```bash
   docker stop radarr-dev prowlarr-dev jellyseerr-dev
   ```

2. **Run Apply Stack** in the UI - should complete successfully

3. **Check logs** for provisioning activity:
   ```bash
   docker logs orchestrator-dev -f | grep -i prowlarr
   ```

4. **Verify Prowlarr**:
   - Should NOT show setup wizard
   - Should be accessible with stored credentials
   - Indexers should be auto-populated (if `indexers_populated` flag not set)

### Expected Log Messages

When provisioning works correctly, you should see:
- `"Provisioning Prowlarr with API key: ..."`
- `"Configuring host settings for Prowlarr at ..."`
- `"Host settings updated successfully"` or `"Host settings already configured"`
- `"Updating host settings: authMethod=None -> forms, username=prowlarr-admin"`
- `"added N indexers"` (on first run)

## Known Limitations

1. **Port conflicts**: Dev compose services (`*-dev` containers) can conflict with generated stack services. Stop dev services before running Apply.

2. **Network isolation**: Health checks may still fail in some Docker network configurations. The orchestrator container must be able to reach services on host ports (`127.0.0.1`).

3. **First-run timing**: If Prowlarr isn't fully ready when provisioning runs, authentication might not be configured. Running Apply again should complete the configuration.

## Migration Notes

When migrating to a new machine:

1. **Ensure code is up to date**: All changes are in `orchestrator/clients/prowlarr.py` and `orchestrator/app.py`

2. **Check state.json**: Verify `secrets.prowlarr.api_key` exists and matches `config.xml` in Prowlarr's appdata directory

3. **Verify network setup**: If orchestrator runs in a container, ensure it can reach services on host ports

4. **Test provisioning**: Run Apply Stack and verify Prowlarr authentication is configured without manual intervention

## Related Issues

- Port conflicts between dev compose and generated stack
- Network isolation when orchestrator runs in container
- Health check accuracy in containerized environments

## Next Steps

1. ✅ API key sync - **COMPLETE**
2. ✅ Dual authentication configuration - **COMPLETE**
3. ✅ Retry logic - **COMPLETE**
4. ✅ Network connectivity fixes - **COMPLETE**
5. 🔄 End-to-end testing after migration - **IN PROGRESS**
6. 📝 Add troubleshooting documentation - **PENDING**
