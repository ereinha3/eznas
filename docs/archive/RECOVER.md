# Library Recovery Plan

**Date:** 2026-03-02
**Library:** /mnt/pool/media/movies/ (139 directories, ~2.8 TB)
**Radarr:** 224 movies tracked, only 35 recognized as having files

## The Core Problem

The pipeline worker was placing remuxed files at regex-parsed paths (e.g. `HEREDITARY RUS BLUEBIRD (2018)/`) instead of Radarr's expected paths (e.g. `Hereditary (2018)/`). This caused:

- **~104 movies exist on disk but Radarr can't find them** — Radarr reports them as MISSING
- **Jellyseerr lets you re-download movies you already have** — because it checks Radarr, which doesn't see the files
- **Upgrades don't work** — Radarr can't upgrade what it doesn't know it has

This has been fixed in code (Issue #6 — Radarr/Sonarr API paths for naming). All future downloads will land at the correct path. This plan addresses the existing library.

---

## Wave 1: Delete Corrupt & Junk Files

**Risk: None. These files are completely unusable.**

### Corrupt stubs (0 bytes / empty)

| File | Size | Problem |
|------|------|---------|
| `SCHINDLERS LIST (1993)/SCHINDLERS LIST (1993).mkv` | 15 KB | Empty stub |
| `Indiana Jones and the Raiders of the Lost Ark (1981)/Indiana Jones and the Raiders of the Lost Ark (1981).mkv` | 15 KB | Empty stub |
| `Spiderman Into Spider Verse MULTI COMPLETE (2018)/Spiderman Into Spider Verse MULTI COMPLETE (2018).mkv` | 43 KB | Empty stub |
| `Requiem for a Dream Blu-Ray FREEDONOR (2000)/` | 0 bytes | Empty directory |

### No audio (broken import)

| File | Size | Problem |
|------|------|---------|
| `Your Name. (2016)/your_name._2016.mkv` | 4.3 GB | 0 audio streams |

### Junk / samples

| File | Size | Problem |
|------|------|---------|
| `american history x uncut sample (1998)/` | 456 MB | Sample file; full 49 GB copy exists separately |
| `Shutter Island (2010)/shutter_island_2010.mkv` | 227 MB | YIFY rip or sample — unusable quality |

**Action:**
```bash
# Delete corrupt stubs
rm -rf "/mnt/pool/media/movies/SCHINDLERS LIST (1993)"
rm -rf "/mnt/pool/media/movies/Indiana Jones and the Raiders of the Lost Ark (1981)"
rm -rf "/mnt/pool/media/movies/Spiderman Into Spider Verse MULTI COMPLETE (2018)"
rm -rf "/mnt/pool/media/movies/Requiem for a Dream Blu-Ray FREEDONOR (2000)"

# Delete broken / junk
rm -rf "/mnt/pool/media/movies/Your Name. (2016)"
rm -rf "/mnt/pool/media/movies/american history x uncut sample (1998)"
rm -rf "/mnt/pool/media/movies/Shutter Island (2010)"
```

**After deletion:** Trigger Radarr re-search for these 7 movies so they get re-downloaded with proper releases.

---

## Wave 2: Rename Script (Fix Tracking for ~104 Movies)

**Goal:** Match every misnamed directory on disk to its correct Radarr path, rename/move it, then trigger a full Radarr library rescan. This immediately fixes Jellyseerr tracking.

### Examples of mismatches

| On Disk | Radarr Expects |
|---------|---------------|
| `1 Братство кольца (2001)` | `The Lord of the Rings: The Fellowship of the Ring (2001)` |
| `2 Две крепости (2002)` | `The Lord of the Rings: The Two Towers (2002)` |
| `3 Возвращение Короля (2003)` | `The Lord of the Rings: The Return of the King (2003)` |
| `SCHINDLERS LIST (1993)` | `Schindler's List (1993)` |
| `THE ROYAL TENENBAUMS (2001)` | `The Royal Tenenbaums (2001)` |
| `HARAKIRI` | `Harakiri (1962)` |
| `CATCH ME IYC` | `Catch Me If You Can (2002)` |
| `THE HUNT BLUEBIRD` | `The Hunt (2012)` |
| `american history x uncut (1998)` | `American History X (1998)` |
| `Eternal Sunshine of the Spotless Mind Kino Lorber (2004)` | `Eternal Sunshine of the Spotless Mind (2004)` |
| `Tokyo Drifter Criterion Collection BDRemux (1966)` | `Tokyo Drifter (1966)` |
| `The Silence of the Lambs PROPER (1991)` | `The Silence of the Lambs (1991)` |
| `Mad Max- Fury Road (2015)` | `Mad Max: Fury Road (2015)` |

### Script approach

1. Query Radarr API for all movies — get `title`, `year`, `path` for each
2. List all directories under `/mnt/pool/media/movies/`
3. For each directory on disk, try to match it to a Radarr movie:
   - Exact match on Radarr path basename (already correct — skip)
   - Fuzzy title match (strip scene tags, normalize case, compare)
   - Year match as secondary signal
4. For confirmed matches, `mv` the directory to Radarr's expected path
5. Also rename the primary video file inside to match the folder name
6. Trigger `RescanMovie` on Radarr after all renames complete

### Safety

- Dry-run mode first (print proposed renames without executing)
- Never overwrite an existing directory
- Log every rename for audit trail
- Skip ambiguous matches (flag for manual review)

---

## Wave 3: Remux Bloated Files (Reclaim ~540 GB)

**Goal:** Strip unwanted Russian/duplicate audio tracks, keeping only English + original language per media policy.

### Files to remux

| File | Current Size | Audio Tracks | Russian | Est. After |
|------|-------------|-------------|---------|------------|
| 1 Братство кольца (2001) | 181 GB | 16 | 10 Russian | ~50 GB |
| 2 Две крепости (2002) | 159 GB | 14 | 8 Russian | ~45 GB |
| 3 Возвращение Короля (2003) | 184 GB | 14 | 8 Russian | ~55 GB |
| Eternal Sunshine of the Spotless Mind (2004) | 74 GB | 14 | 12 Russian | ~20 GB |
| Mulan (1998) | 59 GB | 11 | 6 Russian + 1 Ukrainian | ~20 GB |
| The Killing (1956) | 49 GB | 7 | 4 Russian | ~20 GB |
| The Godfather (1972) | 46 GB | 7 | 4 Russian | ~20 GB |
| Monsters, Inc. (2001) | 21 GB | 6 | 4 Russian | ~10 GB |

**Estimated savings: ~540 GB**

### Approach

Use the existing pipeline remux logic (`build_ffmpeg_command` with `TrackSelection`) to copy-mux each file, keeping only `eng` + `und` + original language audio and `eng` subtitles. This is a lossless operation (no re-encoding).

### Note on .m2ts files

The Godfather (47 GB .m2ts) and Monsters Inc (21 GB .m2ts) are raw Blu-ray transport streams that also need remuxing to MKV. They overlap with the bloated list — both operations (strip audio + convert to MKV) happen in a single remux pass.

Borat (16 GB .m2ts) is not bloated but still needs MKV remux for compatibility.

---

## Wave 4: Re-download Bad Releases

**Goal:** Delete movies that have no usable audio and trigger Radarr to find better releases.

### No English or original language audio

| File | Size | Audio | Problem |
|------|------|-------|---------|
| HARAKIRI (1962) | 32.7 GB | Russian only | Japanese film with no Japanese or English |
| Spoorloos (1988) | 26.2 GB | 2x Portuguese | Dutch film with no Dutch or English |
| Tokyo Drifter (1966) | 20.7 GB | 2x Russian only | Japanese film with no Japanese or English |
| Drunken Master (1978) | 20.3 GB | 9x Russian only | Cantonese film with no Cantonese or English |

**Action:**
```bash
# Delete after Wave 2 renames (paths may change)
# Then trigger Radarr re-search for each title
```

**Note:** These were grabbed from Russian trackers (Knaben indexer, now disabled). With proper quality profiles (Wave 5) and better indexers, replacements should have correct audio.

---

## Wave 5: ISO + Raw .m2ts Processing

### ISO files (automatic after container restart)

| File | Size |
|------|------|
| Monsters vs Aliens (2009) — BD3D .iso | 29 GB |
| Megamind (2010) — BD3D .iso | 30 GB |

The ISO support code is already implemented. These will be processed automatically once the pipeline-worker container is restarted with `cap_add: SYS_ADMIN`.

### Raw .m2ts files

| File | Size |
|------|------|
| Borat (2006) | 16 GB |

Monsters Inc and Godfather are handled in Wave 3 (they need audio stripping too). Borat just needs a straight MKV remux.

---

## Wave 6: Quality Profile Configuration (Prevent Future Issues)

### Current state (broken)

All 7 Radarr quality profiles have `upgradeAllowed: false`. The active profile ("Any") allows everything from WORKPRINT to Remux-2160p with no quality floor.

### Target state

| Setting | Value |
|---------|-------|
| Minimum quality | Bluray-720p (exclude WORKPRINT, CAM, TELESYNC, TELECINE, DVDSCR, REGIONAL) |
| Upgrade allowed | Yes |
| Upgrade cutoff | Remux-2160p |
| Custom formats | Block YIFY/YTS (score: -10000) |
| Custom formats | Prefer Remux (positive score) |

### Implementation

The orchestrator's converge step will push profile configurations to Radarr via API (`PUT /api/v3/qualityprofile/{id}`) based on the UI preset selection. The "Balanced" preset will map to the configuration above. Advanced UI controls will allow overriding individual settings.

---

## TV Shows

| Show | Size | Status |
|------|------|--------|
| Avatar: The Last Airbender | 80 GB | Verify S02/S03 aren't Spider-Verse contaminated |
| The Legend of Korra | 25 GB | Likely OK |
| Gravity Falls | 6.2 GB | OK |

**Action:** Spot-check Avatar S02/S03 episode files to confirm they're real Avatar content, not Spider-Verse.

---

## Summary

| Wave | Action | Files | Space Impact |
|------|--------|-------|-------------|
| 1 | Delete corrupt/junk | 7 | Free ~5 GB |
| 2 | Rename ~104 directories | ~104 | Fix Jellyseerr tracking |
| 3 | Remux bloated files | 8 | Free ~540 GB |
| 4 | Re-download bad releases | 4 | Free ~100 GB (then re-download) |
| 5 | Process ISOs + .m2ts | 3 | Convert to usable MKV |
| 6 | Quality profiles | Config | Prevent future bad grabs |

**Total estimated space savings from Waves 1-4: ~645 GB**
