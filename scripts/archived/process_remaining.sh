#!/usr/bin/env bash
# Wave 5: Process remaining ISO and .m2ts files
# - Megamind (2010) — BD3D ISO → mount → BDMV → MKV
# - Monsters vs Aliens (2009) — BD3D ISO → mount → BDMV → MKV
# - Borat (2006) — raw .m2ts → strip Russian audio → MKV
set -euo pipefail

LIBRARY="/mnt/pool/media/movies"
SCRATCH="/mnt/scratch/remux_staging"
mkdir -p "$SCRATCH"

SUCCEEDED=0
FAILED=0

# ─── Helper: Process an ISO file ─────────────────────────────────────────────
process_iso() {
    local iso_path="$1"
    local title="$2"
    local year="$3"

    local mount_dir="/tmp/iso_$(echo "$iso_path" | md5sum | cut -d' ' -f1)"
    local final_dir="$LIBRARY/${title} (${year})"
    local final_file="${title} (${year}).mkv"
    local staging="$SCRATCH/${final_file}"

    echo ""
    echo "================================================================"
    echo "PROCESSING ISO: $(basename "$iso_path")"
    echo "  Source:  $iso_path"
    echo "  Target:  $final_dir/$final_file"
    echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================"

    local src_size
    src_size=$(stat --format="%s" "$iso_path" 2>/dev/null || echo 0)
    local src_gb
    src_gb=$(echo "scale=1; $src_size / 1073741824" | bc)
    echo "  ISO size: ${src_gb} GB"

    # Mount ISO
    mkdir -p "$mount_dir"
    if ! sudo mount -o loop,ro "$iso_path" "$mount_dir" 2>&1; then
        echo "  FAILED: Could not mount ISO"
        rmdir "$mount_dir" 2>/dev/null || true
        FAILED=$((FAILED + 1))
        return 1
    fi
    echo "  Mounted at: $mount_dir"

    # Find BDMV structure
    local bdmv_dir=""
    if [ -d "$mount_dir/BDMV" ]; then
        bdmv_dir="$mount_dir"
    elif [ -d "$mount_dir/CERTIFICATE" ]; then
        # Some BD3D ISOs have BDMV at root level alongside CERTIFICATE
        bdmv_dir="$mount_dir"
    fi

    # Search one level deeper if not found
    if [ -z "$bdmv_dir" ]; then
        for d in "$mount_dir"/*/; do
            if [ -d "${d}BDMV" ]; then
                bdmv_dir="${d%/}"
                break
            fi
        done
    fi

    if [ -z "$bdmv_dir" ] || [ ! -d "$bdmv_dir/BDMV/STREAM" ]; then
        echo "  FAILED: No BDMV/STREAM structure found in ISO"
        echo "  Contents: $(ls "$mount_dir" 2>/dev/null)"
        sudo umount "$mount_dir" 2>/dev/null || true
        rmdir "$mount_dir" 2>/dev/null || true
        FAILED=$((FAILED + 1))
        return 1
    fi
    echo "  BDMV found at: $bdmv_dir/BDMV"

    # Find the largest .m2ts (main feature)
    local main_m2ts=""
    local max_size=0
    for m2ts in "$bdmv_dir/BDMV/STREAM/"*.m2ts; do
        local fsize
        fsize=$(stat --format="%s" "$m2ts" 2>/dev/null || echo 0)
        if [ "$fsize" -gt "$max_size" ]; then
            max_size=$fsize
            main_m2ts="$m2ts"
        fi
    done

    if [ -z "$main_m2ts" ]; then
        echo "  FAILED: No .m2ts files in BDMV/STREAM"
        sudo umount "$mount_dir" 2>/dev/null || true
        rmdir "$mount_dir" 2>/dev/null || true
        FAILED=$((FAILED + 1))
        return 1
    fi

    local main_gb
    main_gb=$(echo "scale=1; $max_size / 1073741824" | bc)
    echo "  Main feature: $(basename "$main_m2ts") (${main_gb} GB)"

    # Probe streams
    echo "  Probing streams..."
    ffprobe -v quiet -print_format json -show_streams "$main_m2ts" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data.get('streams', []):
    ct = s.get('codec_type', '?')
    lang = s.get('tags', {}).get('language', 'und')
    codec = s.get('codec_name', '?')
    idx = s.get('index', 0)
    if ct in ('audio', 'subtitle', 'video'):
        print(f'    [{idx}] {ct:8s} {codec:12s} lang={lang}')
"

    # Build ffmpeg with language filter: keep eng + und audio, eng subs
    # Use ffmpeg map to select streams
    local map_args=()
    map_args+=(-analyzeduration 10M -probesize 10M)
    map_args+=(-map "0:v:0?")  # Video

    # Map audio: keep eng and und
    local audio_maps
    audio_maps=$(ffprobe -v quiet -print_format json -show_streams "$main_m2ts" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data.get('streams', []):
    if s.get('codec_type') == 'audio':
        lang = s.get('tags', {}).get('language', 'und')
        if lang in ('eng', 'und'):
            print(f'-map 0:{s[\"index\"]}')
")
    if [ -z "$audio_maps" ]; then
        # No eng/und audio found — keep all audio as fallback
        echo "  WARNING: No eng/und audio found, keeping all audio"
        map_args+=(-map "0:a?")
    else
        while IFS= read -r line; do
            map_args+=($line)
        done <<< "$audio_maps"
    fi

    # Map subtitles: keep eng only
    local sub_maps
    sub_maps=$(ffprobe -v quiet -print_format json -show_streams "$main_m2ts" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data.get('streams', []):
    if s.get('codec_type') == 'subtitle':
        lang = s.get('tags', {}).get('language', 'und')
        if lang in ('eng', 'und'):
            print(f'-map 0:{s[\"index\"]}')
")
    if [ -n "$sub_maps" ]; then
        while IFS= read -r line; do
            map_args+=($line)
        done <<< "$sub_maps"
    fi

    echo "  ffmpeg args: ${map_args[*]}"

    # Run ffmpeg
    if ! ffmpeg -hide_banner -y -i "$main_m2ts" "${map_args[@]}" -c copy "$staging" 2>&1 | tail -5; then
        echo "  FAILED: ffmpeg error"
        rm -f "$staging"
        sudo umount "$mount_dir" 2>/dev/null || true
        rmdir "$mount_dir" 2>/dev/null || true
        FAILED=$((FAILED + 1))
        return 1
    fi

    # Unmount
    sudo umount "$mount_dir" 2>/dev/null || true
    rmdir "$mount_dir" 2>/dev/null || true
    echo "  Unmounted ISO"

    # Validate
    if [ ! -f "$staging" ]; then
        echo "  FAILED: output not created"
        FAILED=$((FAILED + 1))
        return 1
    fi

    local out_size
    out_size=$(stat --format="%s" "$staging" 2>/dev/null || echo 0)
    local out_gb
    out_gb=$(echo "scale=1; $out_size / 1073741824" | bc)

    if [ "$out_size" -lt 536870912 ]; then  # < 512 MB
        echo "  FAILED: output too small (${out_gb} GB)"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    echo "  Output: ${out_gb} GB"

    # Move to library
    sudo mkdir -p "$final_dir"
    sudo mv "$staging" "$final_dir/$final_file"

    # Remove old ISO directory contents
    local iso_dir
    iso_dir=$(dirname "$iso_path")
    local iso_basename
    iso_basename=$(basename "$iso_path")
    if [ "$iso_dir" = "$final_dir" ]; then
        # Same directory — remove old ISO file
        sudo rm -f "$iso_path"
        echo "  Cleaned up old ISO: $iso_basename"
    else
        # Different directory — remove entire old dir
        sudo rm -rf "$iso_dir"
        echo "  Cleaned up old directory: $iso_dir"
    fi

    echo "  Completed: $(date '+%Y-%m-%d %H:%M:%S')"
    SUCCEEDED=$((SUCCEEDED + 1))
}

# ─── Helper: Remux .m2ts to .mkv with language filtering ─────────────────────
remux_m2ts() {
    local src="$1"
    local dst_dir="$2"
    local dst_name="$3"
    shift 3
    local map_args=("$@")

    local staging="$SCRATCH/${dst_name}"
    local final_dir="$LIBRARY/${dst_dir}"
    local final_path="$final_dir/${dst_name}"

    echo ""
    echo "================================================================"
    echo "REMUXING: $(basename "$src")"
    echo "  Source:  $src"
    echo "  Target:  $final_path"
    echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================"

    local src_size
    src_size=$(stat --format="%s" "$src" 2>/dev/null || echo 0)
    local src_gb
    src_gb=$(echo "scale=1; $src_size / 1073741824" | bc)
    echo "  Source size: ${src_gb} GB"

    if ! ffmpeg -hide_banner -y -i "$src" "${map_args[@]}" -c copy "$staging" 2>&1 | tail -5; then
        echo "  FAILED: ffmpeg error"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    if [ ! -f "$staging" ]; then
        echo "  FAILED: output not created"
        FAILED=$((FAILED + 1))
        return 1
    fi

    local out_size
    out_size=$(stat --format="%s" "$staging" 2>/dev/null || echo 0)
    local out_gb
    out_gb=$(echo "scale=1; $out_size / 1073741824" | bc)
    echo "  Output: ${out_gb} GB (was ${src_gb} GB)"

    sudo mkdir -p "$final_dir"
    sudo mv "$staging" "$final_path"

    # Clean up old file
    local src_dir
    src_dir=$(dirname "$src")
    if [ "$src_dir" = "$final_dir" ]; then
        sudo rm -f "$src"
        echo "  Cleaned up old .m2ts"
    else
        sudo rm -rf "$src_dir"
        echo "  Cleaned up old directory"
    fi

    echo "  Completed: $(date '+%Y-%m-%d %H:%M:%S')"
    SUCCEEDED=$((SUCCEEDED + 1))
}


echo "========================================"
echo "  WAVE 5: ISOs + .m2ts Processing"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# --- 1. Megamind (BD3D ISO) ---
process_iso \
    "$LIBRARY/Megamind (2010)/Megamind 3D [2010, BDRemux 1080p] BD3D.iso" \
    "Megamind" \
    "2010"

# --- 2. Monsters vs Aliens (BD3D ISO) ---
process_iso \
    "$LIBRARY/Monsters vs Aliens (2009)/Monsters vs Aliens 3D [2009, BDRemux 1080p] BD3D.iso" \
    "Monsters vs Aliens" \
    "2009"

# --- 3. Borat (.m2ts → .mkv, strip Russian audio/subs) ---
# Keep: video(0), eng audio(3), eng subs(8)
# Drop: Russian audio(1,2), Russian subs(4-6), Ukrainian subs(7)
remux_m2ts \
    "$LIBRARY/Borat - Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan (2006)/Borat - Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan [2006, BDRemux 1080p].m2ts" \
    "Borat - Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan (2006)" \
    "Borat - Cultural Learnings of America for Make Benefit Glorious Nation of Kazakhstan (2006).mkv" \
    -analyzeduration 10M -probesize 10M \
    -map 0:0 -map 0:3 -map 0:8

echo ""
echo "========================================"
echo "  WAVE 5 COMPLETE"
echo "  Succeeded: $SUCCEEDED / 3"
echo "  Failed:    $FAILED / 3"
echo "  Finished:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

rmdir "$SCRATCH" 2>/dev/null || true
