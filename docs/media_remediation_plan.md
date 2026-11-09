# Media Remediation System Plan

## Summary

EZNAS will treat media cleanup as a staged remediation workflow, not a one-off script. The audit identifies playback risks, groups files into operational batches, and the remediation layer executes only the lossless steps automatically. Codec changes are handled as replacement-first work because changing video codecs is not a lossless remux.

## Implementation Shape

1. Make the media audit reusable from the pipeline package.
   - Keep the CLI entrypoint at `scripts/media_audit.py`.
   - Move the implementation into `orchestrator/pipeline/media_audit.py`.
   - Persist the latest audit path and summary in `pipeline.json`.
   - Classify every audited file into remediation batches.

2. Add durable remediation state.
   - Store `pipeline.remediation` in `pipeline.json`.
   - Track `job_id`, `kind`, `status`, timestamps, `batch_name`, `mode`, `dry_run`, counters, current file, errors, and input audit path.
   - Allow one active rewrite job at a time.
   - Support cancellation between files.

3. Execute lossless track hygiene by target batch.
   - Extend `LibrarySweeper` with target-list scans and execution.
   - Reuse the existing `build_ffmpeg_command()` remux policy.
   - Roll out in this order:
     - `pilot_known`: 28 Days Later plus the Austin Powers files.
     - `critical_subtitle`: files with critical default subtitle burn-in risk.
     - `track_hygiene`: remaining files that violate audio/subtitle policy.
   - Re-audit after each batch.

4. Handle codec risks separately.
   - `codec_replace` and `streaming_pressure` are surfaced in the plan but not rewritten by the first automatic remediation pass.
   - Prefer Radarr/Sonarr replacement searches or manual replacement for VC-1, H.264 High10, extreme bitrate, and problematic HDR/Dolby Vision cases.
   - Only encode optimized candidates when replacement is unavailable, keeping the original until verification passes.

5. Prevent regression.
   - Bazarr should acquire subtitles, not extract embedded subtitles back into external files.
   - EZNAS remains the single writer for embedding, deduping, and track cleanup.
   - Bazarr language profiles should come from the union of configured media subtitle policies.
   - Jellyfin subtitle defaults stay forced-only and should not remember stale subtitle selections.

6. Expose the workflow in API and UI.
   - Add audit scan/latest endpoints.
   - Add remediation plan/start/status/cancel endpoints.
   - Add Media page batch cards for pilot, critical subtitle cleanup, track hygiene, codec replacement, and streaming pressure.

## Batch Definitions

- `pilot_known`: known playback-problem titles used to validate behavior before broad writes.
- `critical_subtitle`: critical default subtitle tracks, especially image-based subtitles, that can force burn-in or stall playback startup.
- `track_hygiene`: duplicate/default/unwanted audio or subtitle tracks that can be fixed with copy-mode remux.
- `codec_replace`: VC-1, MPEG-2/MPEG-4 legacy video, H.264 10-bit, and other video formats that need replacement or encoding.
- `streaming_pressure`: very high bitrate, risky audio codecs, Dolby Vision compatibility, or image subtitle cases that may still require client-specific handling.

## Verification

- Unit test audit batch classification.
- Unit test target-list sweep planning.
- Unit test durable remediation state transitions.
- Unit test Bazarr provider/language policy.
- Compile Python modules and run focused tests.
- Dry-run the pilot batch before running broad remediation.
