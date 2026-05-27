# Subtitle Embedding Pipeline — Implementation Walkthrough

> Status note (2026-05-27): this is a historical implementation walkthrough.
> The SRT embedding work has since been integrated into `sweep.py` and the
> pipeline runner. The current handoff in `docs/SESSION_HANDOFF.md` documents
> the live idempotency guard, single-thread sweep behavior, and deferred
> duplicate-track repair script.

## Overview
Created a system to embed external `.srt` subtitle files into MKV containers, solving the "orphaned SRT" problem where Bazarr downloads subtitles that sit next to the video but aren't embedded, causing sync issues and portability problems.

---

## Files Created/Modified

### 1. New File: `orchestrator/pipeline/subtitle_align.py` (669 lines)

**Purpose:** Standalone library for SRT manipulation and MKV embedding.

**Functions:**
- `parse_srt(path)` — Parse SRT file into `SubtitleEntry` objects
- `write_srt(path, entries)` — Write entries back to SRT file
- `validate_timing(entries, video_duration)` — Validate subtitle timing against video
- `apply_offset(entries, offset_ms)` — Shift timestamps by offset (for chromaprint alignment)
- `probe_video_duration(path)` — Get video duration via ffprobe
- `remux_subtitle_into_mkv(video_path, srt_path, output_path, language, title, forced)` — Embed SRT into MKV
- `align_and_remux(video_path, srt_path, output_path, offset_ms)` — Full pipeline (validate → offset → remux)
- `detect_offset_from_chromaprint(library_audio_path, srt_audio_path)` — Stub for future audio-based offset detection

**Key Fix:** `_count_subtitle_streams()` — Dynamically determines subtitle stream index so metadata applies to the correct stream regardless of how many subs already exist.

---

### 2. Modified: `orchestrator/pipeline/enrichment.py`

**Added:** `_remux_orphaned_srt_files(library_path, staging, offset_seconds)` method (lines 1688-1834)

**Purpose:** Called during enrichment cross-mux (when adding missing audio). Scans for external `.srt` files next to the video, validates timing, applies chromaprint offset, and remuxes them into the staging file.

**Integration Point:** Called at line ~974 in `_process_candidate()` after ffmpeg creates staging, before atomic replacement.

**Also Fixed:** Subtitle language bug at line ~785-799 — was using `target_langs` (audio languages) instead of `config.media_policy.movies.keep_subs` (subtitle languages).

---

### 3. Modified: `orchestrator/pipeline/remux.py`

**Fixed Bug:** `find_useful_candidate_subtitles()` function signature has `target_langs` but was incorrectly used in enrichment.py. The function itself is correct; the call site was fixed in enrichment.py.

---

### 4. New File: `tests/unit/test_subtitle_align.py` (565 lines)

**Purpose:** Unit tests for subtitle_align module. All 33 tests passing.

**Test Coverage:**
- SRT parsing (valid, malformed, empty, multiline, UTF-8 BOM, Windows line endings)
- SRT writing and roundtrip
- Timestamp parsing/formatting
- Timing validation (start delay, end overflow, coverage, monotonicity)
- Offset application (positive, negative, clamping)
- Video duration probing
- MKV remux (success, failure)
- Full pipeline (valid subtitle, invalid, empty, remux failure)

---

## Changes to Validation Logic

### Original Problem
`validate_timing()` flagged ANY overlapping subtitles as invalid, even individual cases. Evangelion had 5-10 overlapping entries (out of 639) that were valid (OP/ED transitions, multiple speakers).

### Fix Applied
Changed validation to only fail if **>50%** of entries have extreme overlaps (>30s). This allows legitimate individual overlaps while catching fundamentally broken files.

---

## Changes to Remux Logic

### Original Problem
`remux_subtitle_into_mkv()` hardcoded `s:s:0` for metadata, so second subtitle's language tag overwrote the first one instead of applying to the new stream.

### Fix Applied
Added `_count_subtitle_streams()` to auto-detect the correct index:
```python
sub_index = _count_subtitle_streams(video_path)  # 0, 1, 2...
args.extend([f"-metadata:s:s:{sub_index}", f"language={language}"])
```

---

## What Works

### Tested & Verified ✓
- **Single file remux** — Successfully embedded 2 subtitles into Evangelion S01E01
- **Language tagging** — Both subtitles correctly show as `eng` (not `und`)
- **Atomic replacement** — Original file replaced safely
- **External SRT deletion** — Redundant `.srt` files removed after embed

### Files Modified
- `/home/ethan/eznas/nas_orchestrator/orchestrator/pipeline/subtitle_align.py`
- `/home/ethan/eznas/nas_orchestrator/orchestrator/pipeline/enrichment.py`
- `/home/ethan/eznas/nas_orchestrator/tests/unit/test_subtitle_align.py`

---

## Current Deployment Notes

### After Pipeline Code Changes
Rebuild/restart the pipeline container after editing subtitle or sweep code:

1. **Rebuild Docker image** to pick up current `subtitle_align.py`, `sweep.py`, and pipeline code
2. **Restart pipeline worker** container
3. Watch logs for `srt-embed: started background thread` and follow-up summary messages

### Now Implemented
- **Full library sweep mode** — `orchestrator/pipeline/sweep.py`
- **SRT embedding pass** — runs from `orchestrator/pipeline/runner.py` as a background thread
- **API endpoints** — `POST /api/pipeline/sweep/srt/scan`, `POST /api/pipeline/sweep/srt/start`, and sweep status endpoints
- **Duplicate prevention** — existing embedded SubRip languages are detected before embedding, and redundant orphan `.srt` files are removed

---

## Test File
The test file used: `Neon Genesis Evangelion - S01E01.mkv` in `/data/tv/Neon Genesis Evangelion/Season 1/`

Backup exists at: `Neon Genesis Evangelion - S01E01.mkv.BACKUP`

---

## Next Steps for Another Agent

1. **Verify tests pass:** `python -m pytest tests/unit/test_subtitle_align.py -v`
2. **Deploy to container:** Rebuild Docker image and restart pipeline worker
3. **Implement sweep mode:** Add standalone sweep operation to `sweep.py` that scans library for orphaned SRTs
4. **Test on new file:** Use Adventure Time S01E01 or Berserk S01E01 as test candidate
