# Session Handoff — Pipeline Audit & Fixes

> **Read this first if you are picking up cold.** Everything you need to
> understand the in-flight pipeline work is here. CLAUDE.md describes the
> *steady-state* system; this doc describes what was recently broken, what
> got fixed, and what remains.
>
> Last updated: 2026-05-27.

---

## 1. The 30-second summary

A multi-cycle audit of the media pipeline turned up **five bugs** affecting
the enrichment engine and the SRT-embed sweep. Three are fixed and live in
the codebase; two are still pending. There is also a one-shot repair script
ready for an existing data-corruption issue, which has been deliberately
deferred (cost too high vs. cosmetic-only impact, prevention is in place).

| # | Bug | Status | Where |
|---|-----|--------|-------|
| 1 | Cross-show enrichment matching | **Fixed** | `orchestrator/pipeline/enrichment.py` `_find_all_pack_targets` (~line 2363) |
| 2 | Subtitle dedup + idempotency guard | **Fixed** | `orchestrator/pipeline/sweep.py` `_embed_srt_for_video` (~line 751) |
| 2b | Existing duplicate subtitle tracks (data corruption from leaked sweep threads) | **Repair deferred** | `scripts/dedup_subtitle_tracks.py` (one-shot, ready to run) |
| 3 | `cooldown=0d` cleanup-path bug (`failed_torrents` writes) | **Code fixed, stale state remains** | `orchestrator/pipeline/enrichment.py` lines 439-451, 650-662 |
| 4 | Per-(show, language) blacklist for alt-title whack-a-mole | **Pending** | New state field `failed_show_langs` proposed |
| 5 | Prowlarr-fallback score floor / year match | **Pending** | `orchestrator/pipeline/prowlarr_fallback.py` |

There's also a separate **plan file** (`zazzy-hatching-hammock.md` in
`~/.claude/plans/`) for a CRITICAL fix that already shipped — background
gap-scan thread + prowlarr-fallback re-grab fix. **Both halves of that
plan are live** (verify in section 5).

---

## 2. Live fixes — what was applied this session

### Fix A: SRT-embed thread leak (the cause of all the duplicate subtitle tracks)

**File:** `orchestrator/pipeline/runner.py:507-524`

The old watchdog reset `self._srt_embed_thread = None` after 4 hours but
never actually killed the thread. The next tick happily spawned a new sweep
thread, and they raced — both calling `mkvmerge` on the same MKV with the
same tmp filename. The losing thread's `mkvmerge` output was lost; the
winning thread embedded its own copy. On a long-running library, multiple
threads stacked up.

The fix removes the watchdog entirely. Python can't safely kill a thread,
and a multi-hour sweep is steady state on a large library, not a hang.
We now only ever have **one** sweep thread alive at a time:

```python
if self._srt_embed_thread is not None and self._srt_embed_thread.is_alive():
    log.debug("srt-embed: previous run still active (%.1fh), skipping", age_h)
elif time.time() - self._last_srt_embed > self._SRT_EMBED_INTERVAL:
    # spawn new thread
```

### Fix B: Cross-show enrichment pack matching (Bug #1)

**File:** `orchestrator/pipeline/enrichment.py:2371-2398`

`_find_all_pack_targets` was walking `original_target.parent.parent` blindly
to find the show directory. That works for the typical TV layout
(`/data/tv/Show/Season 1/Show - S01E01.mkv` → `/data/tv/Show`), but for:

- **Movies** (`/data/movies/Movie/Movie.mkv`): resolved to `/data/movies`
  (the entire library), then fanned the pack across every movie.
- **Anime without `Season N` subfolders** (`/data/tv/Show/file.mkv`):
  resolved to `/data/tv` — same problem.

Fix:
1. Bail immediately for `category != "tv"`. A movie grab targets exactly
   one library file.
2. Detect the no-Season layout: if `parent.parent` is a library root
   (`tv`/`movies`), fall back to `parent`.
3. Validate: require `show_dir.parent.name in ("tv", "movies")` — anything
   else (orphan files at the category root) skips pack matching with a
   warning rather than iterating the whole library.

All six test cases pass (TV normal, TV no-season, TV orphan, movie nested,
movie flatfile, TV with Specials subfolder).

### Fix C: Subtitle dedup idempotency guard (Bug #2)

**File:** `orchestrator/pipeline/sweep.py:722-786`

Even with the thread-leak fix in Fix A, Bazarr will keep re-downloading
external `.srt` files after they get embedded (it doesn't know they're
inside the MKV now). Each sweep cycle would append yet another duplicate
SubRip track — same language, same content. `mkvmerge` has no native dedup.

Fix: new method `_existing_subrip_languages(video_path)` probes the MKV
with `mkvmerge -J` and returns the set of languages already present as
SubRip tracks. The top of `_embed_srt_for_video` now:

1. Probes existing langs.
2. For each pending `.srt`, if its language is already embedded, **deletes
   the orphan `.srt`** and removes it from the work list.
3. If nothing remains, skips the file entirely.

Identifying signature for "is this a duplicate?":
`(codec, language, track_name, forced_track, flag_hearing_impaired)` — same
key used by the repair script.

### Fix D: Background gap scan thread (from `zazzy-hatching-hammock.md` plan)

**File:** `orchestrator/pipeline/enrichment.py:279-281, 680-743, 1205-1213`

The enrichment gap scan (`scan_library_gaps`) probes every video file with
ffprobe and used to run on the main tick thread, blocking the entire
pipeline for 7+ hours per scan. Now it runs in a background thread with
the same "single thread at a time" pattern as the SRT sweep. Result is
swapped into the queue atomically when complete:

```python
self._scan_thread: Optional[threading.Thread] = None
self._pending_queue: Optional[List[EnrichmentCandidate]] = None
_SCAN_WATCHDOG_HOURS = 2
```

`maybe_grab_next` now never blocks; it collects pending results, launches
a new scan if needed, and proceeds with whatever queue exists.

### Fix E: Prowlarr-fallback no longer drops grab entries when torrents vanish

**File:** `orchestrator/pipeline/prowlarr_fallback.py:851-864`

Old behaviour: when a previously-grabbed torrent disappeared from qBT (e.g.
after a successful import + cleanup), `_is_grabbed` deleted the grab entry
and treated the title as available to re-grab. This caused re-download
loops for movies that were already in the library.

Now: the grab entry persists for the full `_GRAB_COOLDOWN` (48h). The
`hasFile` filter in Radarr/Sonarr is the primary guard; the cooldown is
the reliable secondary guard.

---

## 3. The deferred repair — existing subtitle track duplicates

**Why deferred:** Damage is cosmetic (extra entries in the player's subtitle
menu, same content), and the idempotency guard above prevents any new
duplicates. The repair would have taken ~24h for TV alone, 50-100h for
the full library, at the measured 13 MB/s mkvmerge throughput. **You can
run it later — it's safe to repeat and won't conflict with the live pipeline.**

### Measurements (collected 2026-05-26/27)

- 199/201 sampled TV files affected (99%).
- Average affected file: 0.28 GB; median 0.22 GB; max 2.47 GB.
- 160 files <500 MB, 38 in 500 MB-1 GB, 1 file >2 GB, none over 5 GB.
- Verified clean on Gankutsuou S01E12: 5 subtitle tracks (1 SSA + 4 duplicate
  SubRip) → 2 tracks (1 SSA + 1 SubRip), 84 s for 1.1 GB.

### How to run the repair script

`scripts/dedup_subtitle_tracks.py` — detects duplicates by signature
`(codec, language, track_name, forced, hi)`, rewrites the MKV with
`mkvmerge --subtitle-tracks <keep_ids>` excluding duplicates, atomic-rename
over the original.

```bash
# Damage report only (read-only scan)
docker exec pipeline-worker python3 /config/scripts/dedup_subtitle_tracks.py

# Fix everything (long-running — start in tmux/screen on host with sudo)
sudo python3 /home/ethan/eznas/nas_orchestrator/scripts/dedup_subtitle_tracks.py \
    --root /mnt/pool/media --fix

# Skip giant files (e.g. >5GB)
... --fix --max-size-gb 5.0

# Filter to a single show
... --fix --only "Gankutsuou"
```

The script:
- Prints per-file progress with `flush=True` so it survives `tail`-style monitoring.
- Skips files starting with `.` (hidden tmp files).
- Scales mkvmerge timeout by file size (50 MB/s floor, 1800 s minimum).

**Gotchas from past runs:**
- Forrest Gump 29 GB single file consumed an entire smoke-test window —
  prefer `--max-size-gb` on first run.
- Killing a host-side `bash` wrapper does NOT kill an `mkvmerge` orphan
  inside the container; it re-parents to PID 1. Track PIDs separately.
- To kill a container-internal Python: `docker exec pipeline-worker sh -c 'kill -9 <container_pid>'`.

---

## 4. Outstanding bugs — context for whoever picks this up

### Bug #3 — `cooldown=0d` cleanup-path bug (code fixed, stale state remains)

**Status:** The code paths that write `failed_torrents` (lines 439-451 in
`_cleanup_stale_grabs` and 650-662 in `process_completed`) **do** now set
`cooldown` correctly. The audit observation that ~11/31 entries had
`count=1, cooldown=0` was capturing **historical state**, written before
the cooldown field was added.

**What still needs doing:** Decide whether to retroactively repair the
stale state entries. Two options:

1. **Leave alone** — they'll be evicted by `_compact_failed_torrents` on
   their own once `count > 1` or when an explicit retry succeeds.
2. **One-shot state fix** — load `pipeline.json`, walk
   `enrichment.failed_torrents`, and for any entry with `count >= 1` and
   `cooldown == 0`, set `cooldown = 3600 * (4 ** count)` (the same
   exponential-backoff formula in `_cleanup_stale_grabs`). Save.

Recommended: option 1 unless these entries are causing re-grab churn.
Inspect the live state with `jq '.enrichment.failed_torrents' pipeline.json`
from the config root (`ORCH_ROOT`; `/config/pipeline.json` inside containers)
before deciding.

### Bug #4 — Per-(show, language) blacklist for alt-title whack-a-mole

**Symptom:** Hunter x Hunter (one example) had 5+ alternate-title releases
land over 6 days, each tracked as a *new* `failed_torrents` entry because
the torrent name differs slightly (`"Hunter x Hunter (2011)"` vs
`"HunterxHunter Remaster"` vs `"Hunter.x.Hunter.2011.Complete"`).
Per-torrent cooldown does nothing here — every new alt-title bypasses it.

**Proposed fix (~30 lines):** Add a sibling state field
`failed_show_langs: dict[str, dict]` keyed by `f"{candidate.title}:{lang}"`
(or, better, by `arr_id:lang` once we have it). When a candidate fails
enrichment, increment that counter alongside `failed_torrents`. In the
search loop, skip candidates whose `(title, lang)` pair is in cooldown.

**Where to wire it in:**
- Write site: `enrichment.py` lines 650-662 (`process_completed` failure path).
- Read site: the search filter that already consults `failed_torrents`
  around line 946-958 (`maybe_grab_next`).
- Key: prefer arr `tvdbId`/`imdbId` (stable) over `candidate.title` (volatile).

### Bug #5 — Prowlarr-fallback grabs wrong show

**Symptoms:**
- `"Rio 3"` search grabbed *Rio Rainbow Gate* (different anime, different decade).
- `"Rurouni Kenshin S03"` search grabbed the **2023 reboot**, not the
  original ’99 series.

**Root cause:** The Prowlarr direct-grab fallback (`prowlarr_fallback.py`)
ranks candidates by seeders+age, but does not require:
1. A minimum **fuzzy-title score** against the requested show title.
2. A **year match** against the arr release year (when arr metadata is available).

**Proposed fix (~10 lines):** In the candidate scoring loop, compute
`fuzz.ratio(candidate.title, expected_title)` (already have `rapidfuzz`)
and reject below ~70. If an arr-side `year` is in the grab info, reject
candidates whose detected year differs by more than ±1.

Look for the candidate-scoring block in `prowlarr_fallback.py` — search
for the `r.seeders` ranking expression.

---

## 5. How to verify everything is live

```bash
# All five fixes should show:
grep -n "Never spawn a new sweep" orchestrator/pipeline/runner.py
grep -n "_existing_subrip_languages" orchestrator/pipeline/sweep.py
grep -n "Pack matching only makes sense for TV" orchestrator/pipeline/enrichment.py
grep -n "_run_gap_scan\|_pending_queue" orchestrator/pipeline/enrichment.py
grep -n "_GRAB_COOLDOWN" orchestrator/pipeline/prowlarr_fallback.py
```

Pipeline runtime smoke check:

```bash
docker logs --tail 100 pipeline-worker 2>&1 | grep -E "srt-embed|enrichment:"
# Expect: "srt-embed: started background thread" / "previous run still active"
#         "enrichment: launching background gap scan" / "background scan complete"
```

Pipeline state is stored in `pipeline.json` at the config root (`ORCH_ROOT`;
`/config/pipeline.json` inside containers). Top-level keys:
- `enrichment.grabbed` — active grabs (key → torrent_name + target_path)
- `enrichment.processed` — per-target outcomes (`status: ok|failed`)
- `enrichment.failed_torrents` — torrent-name-keyed blacklist with cooldown
- `enrichment.search_misses` — query-keyed search-miss cooldowns
- `prowlarr_fallback.grabbed` — same shape, separate cooldown (48h)
- `events` — ring buffer for the UI Activity page

`jq '.enrichment | keys' pipeline.json` to enumerate from the config root.

---

## 6. Pipeline-worker container — quick reference

| Operation | Command |
|-----------|---------|
| Restart pipeline | `docker restart pipeline-worker` |
| Tail logs | `docker logs -f pipeline-worker` |
| Exec shell | `docker exec -it pipeline-worker bash` |
| Force tick (just wait — 60s loop) | n/a |
| Apply config changes | UI "Apply Stack" or `curl POST /api/apply` |

The pool root inside the container is `/data` (bind-mounted from
`/mnt/pool/media` on the host). Scripts live at `/config/scripts/`.

Host-side files in `/mnt/pool/media` are owned by `root` (the media data),
directories by `ethan` (the operator). The `pipeline-worker` container
runs as uid 1000 (matches `ethan`), so writes to existing files succeed
because they happen via atomic-rename in the parent directory.

---

## 7. What was *not* changed (and why)

- **No new tests** were added for any of these fixes. The cross-show
  matching fix had ad-hoc inline test cases run in `python -c` but those
  weren't checked in. If you have time, port them to
  `tests/unit/test_enrichment.py` against `_find_all_pack_targets`.
- **No state migration** for the `cd=0d` historical entries (see Bug #3).
- **No config knob** for the dedup script — it's a one-shot and the user
  chose to defer running it.

---

## 8. The "if you only have 5 minutes" version

1. The pipeline is healthy. Three structural bugs got fixed, one
   architectural rewrite (background gap scan) shipped.
2. Two enrichment bugs remain (`#4` alt-title blacklist and `#5`
   prowlarr-fallback scoring). They're independent — pick either.
3. The subtitle-dup data corruption is contained (no new dups will form)
   but historical extras remain. Repair script is ready, deferred until
   the user wants to spend the ~24h-of-mkvmerge runtime.
4. **Don't** re-run the dedup script's `--fix` mode against the full
   library without first verifying the pipeline is healthy and you've
   reviewed `--max-size-gb` to skip outliers.
