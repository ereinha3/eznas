#!/usr/bin/env bash
# Wave 3: Remux bloated files to strip Russian/unwanted audio tracks
# Keeps only English + und audio and English subtitles
# Stages to /mnt/scratch, then moves to correct Radarr library path
set -euo pipefail

SCRATCH="/mnt/scratch/remux_staging"
LIBRARY="/mnt/pool/media/movies"
mkdir -p "$SCRATCH"

SUCCEEDED=0
FAILED=0

remux_file() {
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
    echo "  Staging: $staging"

    local src_size
    src_size=$(stat --format="%s" "$src" 2>/dev/null || echo 0)
    local src_gb
    src_gb=$(echo "scale=1; $src_size / 1073741824" | bc)
    echo "  Source size: ${src_gb} GB"
    echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================"

    # Run ffmpeg
    if ! ffmpeg -hide_banner -y -i "$src" "${map_args[@]}" -c copy "$staging" 2>&1 | tail -5; then
        echo "  FAILED: ffmpeg error"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    # Validate output
    if [ ! -f "$staging" ]; then
        echo "  FAILED: staging file not created"
        FAILED=$((FAILED + 1))
        return 1
    fi

    local out_size
    out_size=$(stat --format="%s" "$staging" 2>/dev/null || echo 0)
    local out_gb
    out_gb=$(echo "scale=1; $out_size / 1073741824" | bc)

    # Sanity check: output should be > 1 GB for a real movie
    if [ "$out_size" -lt 1073741824 ]; then
        echo "  FAILED: output too small (${out_gb} GB) — likely corrupt"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    echo "  Output size: ${out_gb} GB (was ${src_gb} GB)"
    local savings
    savings=$(echo "scale=1; ($src_size - $out_size) / 1073741824" | bc)
    echo "  Savings: ${savings} GB"

    # Move to final destination
    sudo mkdir -p "$final_dir"
    sudo mv "$staging" "$final_path"
    echo "  Moved to: $final_path"

    # Clean up old source directory (only if source is in a DIFFERENT directory)
    local src_dir
    src_dir=$(dirname "$src")
    if [ "$src_dir" != "$final_dir" ]; then
        sudo rm -rf "$src_dir"
        echo "  Cleaned up old directory: $src_dir"
    else
        # Same directory — just remove old file if different name
        local src_basename
        src_basename=$(basename "$src")
        if [ "$src_basename" != "$dst_name" ]; then
            sudo rm -f "$src"
            echo "  Cleaned up old file: $src_basename"
        fi
    fi

    echo "  Completed: $(date '+%Y-%m-%d %H:%M:%S')"
    SUCCEEDED=$((SUCCEEDED + 1))
}

echo "========================================"
echo "  WAVE 3: Remux Bloated Files"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# --- 1. LOTR: Fellowship of the Ring ---
# Keep: video(0), eng audio(11-16), eng subs(17-19)
# Drop: Russian audio(1-10)
remux_file \
    "$LIBRARY/1 Братство кольца (2001)/1 Братство кольца (2001).mkv" \
    "The Lord of the Rings - The Fellowship of the Ring (2001)" \
    "The Lord of the Rings - The Fellowship of the Ring (2001).mkv" \
    -map 0:0 -map 0:11 -map 0:12 -map 0:13 -map 0:14 -map 0:15 -map 0:16 \
    -map 0:17 -map 0:18 -map 0:19

# --- 2. LOTR: The Two Towers ---
# Keep: video(0), eng audio(9-14), eng subs(15-17)
# Drop: Russian audio(1-8)
remux_file \
    "$LIBRARY/2 Две крепости (2002)/2 Две крепости (2002).mkv" \
    "The Lord of the Rings - The Two Towers (2002)" \
    "The Lord of the Rings - The Two Towers (2002).mkv" \
    -map 0:0 -map 0:9 -map 0:10 -map 0:11 -map 0:12 -map 0:13 -map 0:14 \
    -map 0:15 -map 0:16 -map 0:17

# --- 3. LOTR: Return of the King ---
# Keep: video(0), eng audio(9-14), eng subs(15-17)
# Drop: Russian audio(1-8)
remux_file \
    "$LIBRARY/3 Возвращение Короля (2003)/3 Возвращение Короля (2003).mkv" \
    "The Lord of the Rings - The Return of the King (2003)" \
    "The Lord of the Rings - The Return of the King (2003).mkv" \
    -map 0:0 -map 0:9 -map 0:10 -map 0:11 -map 0:12 -map 0:13 -map 0:14 \
    -map 0:15 -map 0:16 -map 0:17

# --- 4. Eternal Sunshine of the Spotless Mind ---
# Keep: video(0), eng audio(13-14), eng subs(15-17)
# Drop: Russian audio(1-12)
remux_file \
    "$LIBRARY/Eternal Sunshine of the Spotless Mind Kino Lorber (2004)/Eternal Sunshine of the Spotless Mind Kino Lorber (2004).mkv" \
    "Eternal Sunshine of the Spotless Mind (2004)" \
    "Eternal Sunshine of the Spotless Mind (2004).mkv" \
    -map 0:0 -map 0:13 -map 0:14 \
    -map 0:15 -map 0:16 -map 0:17

# --- 5. Mulan ---
# Keep: video(0), eng audio(8-11), eng subs(17-19)
# Drop: Russian audio(1-6), Ukrainian audio(7), Russian/Ukr subs(12-16)
remux_file \
    "$LIBRARY/Mulan (1998)/Mulan [1998, UHD BDRemux 2160p, HDR10, Dolby Vision] [Hybrid] Dub + DVO + 3x AVO + VO + MVO (Ukr) + Original (Eng) + Sub (Rus, Ukr, Eng).mkv" \
    "Mulan (1998)" \
    "Mulan (1998).mkv" \
    -map 0:0 -map 0:8 -map 0:9 -map 0:10 -map 0:11 \
    -map 0:17 -map 0:18 -map 0:19

# --- 6. The Killing ---
# Keep: video(0), eng audio(5-7), eng subs(8-9)
# Drop: Russian audio(1-4)
remux_file \
    "$LIBRARY/The Killing US Kino Lorber (1956)/The Killing US Kino Lorber (1956).mkv" \
    "The Killing (1956)" \
    "The Killing (1956).mkv" \
    -map 0:0 -map 0:5 -map 0:6 -map 0:7 \
    -map 0:8 -map 0:9

# --- 7. The Godfather (.m2ts → .mkv) ---
# Keep: video(0), eng audio(4,6,7), und audio(5), eng subs(11-13)
# Drop: Russian audio(1-3), Russian subs(8-10)
# Note: .m2ts needs extra analysis
remux_file \
    "$LIBRARY/The Godfather (1972)/Крестный отец_The Godfather (1972).m2ts" \
    "The Godfather (1972)" \
    "The Godfather (1972).mkv" \
    -analyzeduration 10M -probesize 10M \
    -map 0:0 -map 0:4 -map 0:5 -map 0:6 -map 0:7 \
    -map 0:11 -map 0:12 -map 0:13

# --- 8. Monsters, Inc. (.m2ts → .mkv) ---
# Keep: video(0), eng audio(5-6), eng subs(8)
# Drop: Russian audio(1-4), Russian subs(7)
# Note: Not in Radarr — use sensible default path
remux_file \
    "$LIBRARY/Monsters, Inc. (2001)/Monsters, Inc [2001, BDRemux 1080p].m2ts" \
    "Monsters, Inc. (2001)" \
    "Monsters, Inc. (2001).mkv" \
    -analyzeduration 10M -probesize 10M \
    -map 0:0 -map 0:5 -map 0:6 \
    -map 0:8

echo ""
echo "========================================"
echo "  WAVE 3 COMPLETE"
echo "  Succeeded: $SUCCEEDED / 8"
echo "  Failed:    $FAILED / 8"
echo "  Finished:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# Clean up staging directory
rmdir "$SCRATCH" 2>/dev/null || true
