# TODO

## Current Known Issues

### Pipeline Bugs
- **Overwrite protection is size-only** — Compares file size but not resolution, codec,
  audio tracks, or subtitle count. A bloated re-encode could replace a high-quality remux.
- **~200 anime files missing English subtitles** — Free subtitle providers don't cover niche
  titles (Paranoia Agent, Legend of the Galactic Heroes). Partially mitigated by 8 Bazarr
  providers + AniDB integration + targeted search script.
- **S00 special episodes not mapped** in absolute numbering. Sonarr maps specials to S00
  but the absolute episode map doesn't handle them.
- **Season 1 Phineas and Ferb has mislabeled files** — 8 files with wrong episode ranges
  (e.g., `S01E09-E22.mkv` is actually a single ~200MB file). These are the only copies of
  those episodes. Should be re-downloaded as individual episodes via Sonarr backfill.

### Enrichment Pipeline — Accepted Risks
- **Race condition on concurrent ticks** — If ffmpeg runs longer than the tick interval (60s),
  a second tick could theoretically process the same enrichment torrent. Mitigated by the
  pipeline runner being single-threaded. Would need a file lock or in-memory mutex if the
  architecture changes to multi-threaded.
- **Fragile grab key matching** — `_find_grab_key()` uses prefix matching (`torrent_name[:100]`)
  which could cross-match unrelated torrents sharing a long title prefix. Low probability in
  practice. Should switch to hash-based matching for robustness.
- **No `.rar` extraction** — Scene releases packaged as `.rar` archives are not extracted.
  The enrichment engine logs "no media file found" and marks as failed. The 7-day retry
  cooldown means manual extraction would allow re-enrichment on next cycle.
- **Offset sign convention** in `build_video_upgrade_command()` is mathematically correct but
  confusing (double negation). Needs a real-world test to confirm audio stays in sync.
- **Alphabetical candidate bias** — `scan_library_gaps()` walks the filesystem alphabetically,
  so files starting with 'A' are always enriched first. Should shuffle or prioritize by
  quality gap size.
- **Forced subtitle distinction** — `find_useful_candidate_subtitles()` doesn't distinguish
  forced vs full subtitles when checking library coverage. If library has "eng" full subs but
  not "eng" forced subs, the candidate's forced subs are skipped.
- **Word-order title matching** — Scoring uses word overlap, not word order. A search for
  "The Dark Knight" could match "The Knight Rider Dark Side" (3/3 = 100% word overlap).
- **Path normalization** — Enrichment success records use `str(library_path)` as key. If
  Radarr renames the folder, the old key becomes stale and the file could be re-enriched.
- **Cross-season combined files** — Episode overlap detection in `worker.py` doesn't handle
  files spanning seasons (e.g., `S01E13-S02E01`). Rare edge case.

## Completed Work ✅

### Pipeline Core
- ✅ Pipeline worker service (continuous 60s tick loop)
- ✅ Lossless remux (`-c copy`) with language track stripping
- ✅ Container format standardization (MKV/MP4)
- ✅ User-configurable remux preferences (audio/subtitle language allowlists)
- ✅ Health/stall detection with exponential backoff and category-aware blocklisting
- ✅ Orphan scan (untracked files in scratch, 7-day expiry)
- ✅ Stale staging and orphan source cleanup (7-day TTL)
- ✅ Backfill engine (search for missing content via arr APIs)
- ✅ Prowlarr direct-grab fallback (bypasses arr title matching)
- ✅ Nightly automation (indexer discovery + missing content search + studio collections)
- ✅ 5-layer metadata matching (Prowlarr fallback → hash → word-boundary → arr API → cross-service)
- ✅ Anime absolute episode mapping (absolute → season/episode, including multi-episode end_episode)
- ✅ Bazarr subtitle provider provisioning (8 providers, AniDB integration)
- ✅ Per-item arr notifications on import (RefreshSeries/RescanSeries + RescanMovie/RefreshMovie)
- ✅ Non-ASCII filename handling (ASCII symlink approach in `_run_ffmpeg()`)
- ✅ Multi-episode overlap detection (prefers individual episodes over combined files)
- ✅ Pre-existing library file check (skips planning for already-imported episodes)
- ✅ Orphan double-processing prevention (Phase 1 writes alias entries for Phase 2 recognition)

### Media Enrichment Pipeline
- ✅ **Phase 1: Track management** — Audio/subtitle stripping, library sweep, Bazarr integration
- ✅ **Chromaprint integration** — `fpcalc` fingerprinting, two-pass correlation (coarse+fine),
  stream-specific extraction via ffmpeg for multi-audio files
- ✅ **Audio cross-mux** — `build_crossmux_command()` merges audio from candidate into library
  file with chromaprint-aligned offset. Extracts ALL matching target languages + subtitles.
- ✅ **Video quality upgrades** — `build_video_upgrade_command()` takes candidate video +
  library audio (preserves enriched tracks). `probe_video_quality()` scores resolution/codec/HDR.
  Thumbnail stream filtering (skips MJPEG/PNG cover art).
- ✅ **Enrichment engine** — Full scan→search→download→fingerprint→mux→verify→replace pipeline.
  Gap scanner checks both audio languages AND video quality. Smart search queries adapt to
  upgrade type. TV episode matching from season packs via `parse_tv_episode()`.
- ✅ **Duration guard** — 60-second tolerance before chromaprint. Catches wrong content/different cuts.
- ✅ **Failed entry retry** — 7-day cooldown on failed enrichments (not permanently blocked).
- ✅ **State compaction** — 5000-entry cap on processed state, evicts oldest failures first.
- ✅ **Arr rescan after enrichment** — Triggers RefreshMovie/RefreshSeries + Rescan on success.

### Configuration
- ✅ `EnrichmentConfig` with: `enabled`, `search_interval_hours`, `max_grabs_per_cycle`,
  `search_queries`, `min_seeders`, `correlation_threshold`, `fingerprint_duration_seconds`,
  `target_languages` (supports `"original"` sentinel), `upgrade_video`, `target_resolution`,
  `prefer_hdr`, `prefer_hevc`
- ✅ qBittorrent `enrichment` category auto-created on Apply Stack
- ✅ `libchromaprint-tools` in Dockerfile + Dockerfile.dev

### Infrastructure
- ✅ Bootstrap compose (`docker-compose.bootstrap.yml`)
- ✅ Frontend dev container (`docker-compose.dev.yml`)
- ✅ Health endpoint (`/api/health`)
- ✅ Structured logging
- ✅ Quality presets (balanced/1080p/4K) in UI and backend
- ✅ Jellyfin plugin configuration scripted (`jellyfin_setup.py`)
- ✅ Studio collection auto-sync (`jellyfin_studio_collections.py`)

## Activation Required

### Enrichment Pipeline — Ready but Not Running
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

## Future Work

### High Priority
- **EZNAS UI redesign** — Single pane of glass for all service configuration. Per-service
  settings pages. Enrichment status/progress dashboard. Media policy editor with preview.
- **Enrichment API endpoints** — `POST /api/pipeline/enrichment/scan` (dry-run gap analysis),
  `POST .../start` (manual trigger), `GET .../status` (progress/active downloads).
- **Disable Radarr/Sonarr quality upgrades** — Arr services should not overwrite enriched
  files. EZNAS owns all quality upgrades to preserve cross-muxed audio. Currently the arr
  cutoff is already met immediately (no upgrades fire), but this should be explicitly enforced.

### Medium Priority
- **qBT connection pooling** — Enrichment engine creates/closes httpx clients per operation.
  Should reuse a single authenticated session per tick cycle.
- **Enrichment candidate prioritization** — Rank candidates by quality gap (720p→1080p
  upgrade is higher priority than adding English dub to a 1080p file). Currently alphabetical.
- **Grab key hardening** — Switch from prefix matching to content-hash-based matching for
  enrichment torrent→target mapping.
- **Forced subtitle awareness** — Track forced vs full subtitles separately when checking
  library coverage. Forced subs are critical for foreign dialogue in dubbed content.
- **Season pack multi-episode enrichment** — When enrichment downloads a season pack, process
  ALL episodes that need enrichment (not just the target). Currently handles single episode
  matching but doesn't batch-process the full pack.

### Low Priority
- **qBittorrent webhooks** — Replace polling with push notifications for better responsiveness.
- **Optional transcoding** — Lossy transcode option for files that can't be copy-remuxed.
- **Re-seeding support** — Option to keep original files for seeding after remux.
- **Pipeline queue** — Queue system for handling multiple downloads simultaneously.
- **Progress tracking** — Show remux/enrichment progress in UI.
- **Recommender system** — Netflix-style recommendation engine using embeddings.

### Testing
- **Integration tests** for each service client (qBittorrent, Radarr, Sonarr, etc.)
- **Chromaprint unit tests** — Test correlation with known-good audio pairs
- **Enrichment integration tests** — Mock Prowlarr search + qBT download + cross-mux
- **Pipeline E2E tests** — Full torrent→remux→import→enrichment flow
- **Fix 19 pre-existing test failures** — Tests reference old behavior (anime categories,
  ffmpeg command format changes, API schema changes from prior sessions)

### Documentation
- **Install guide** — Consumer-friendly step-by-step installation
- **Troubleshooting matrix** — Common errors + fixes organized by service/issue
- **API documentation** — OpenAPI/Swagger for the FastAPI backend
- **Architecture diagram** — Visual representation of the system

### Docker / Packaging
- **Multi-arch builds** — ARM64 support for different NAS hardware
- **Build tags** — dev vs prod (debug logs, hot reload, etc.)
- **Document required ports** and permissions
