#!/usr/bin/env bash
# Strip Russian audio tracks from 4 library files
set -uo pipefail

SCRATCH="/mnt/scratch/remux_staging"
LIBRARY="/mnt/pool/media/movies"
mkdir -p "$SCRATCH"

SUCCEEDED=0
FAILED=0

remux_strip() {
    local src="$1"
    local final_dir="$2"
    local final_name="$3"
    shift 3
    local map_args=("$@")

    local staging="$SCRATCH/$final_name"
    local final_path="$final_dir/$final_name"

    echo ""
    echo "================================================================"
    echo "REMUXING: $(basename "$src")"
    echo "  Target: $final_path"
    echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "================================================================"

    local src_size src_gb
    src_size=$(stat --format="%s" "$src" 2>/dev/null || echo 0)
    src_gb=$(echo "scale=1; $src_size / 1073741824" | bc)
    echo "  Source: ${src_gb} GB"

    ffmpeg -hide_banner -y -i "$src" "${map_args[@]}" -c copy "$staging" 2>&1 | tail -3
    local rc=${PIPESTATUS[0]}

    if [ "$rc" -ne 0 ]; then
        echo "  FAILED: ffmpeg error (exit $rc)"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    local out_size out_gb
    out_size=$(stat --format="%s" "$staging" 2>/dev/null || echo 0)
    out_gb=$(echo "scale=1; $out_size / 1073741824" | bc)

    if [ "$out_size" -lt 1073741824 ]; then
        echo "  FAILED: output too small (${out_gb} GB)"
        rm -f "$staging"
        FAILED=$((FAILED + 1))
        return 1
    fi

    local savings
    savings=$(echo "scale=1; ($src_size - $out_size) / 1073741824" | bc)
    echo "  Output: ${out_gb} GB (saved ${savings} GB)"

    sudo mkdir -p "$final_dir"
    sudo rm -f "$src"
    sudo mv "$staging" "$final_path"
    echo "  Replaced original"
    echo "  Completed: $(date '+%Y-%m-%d %H:%M:%S')"
    SUCCEEDED=$((SUCCEEDED + 1))
}

echo "========================================"
echo "  Strip Russian Audio from 4 Files"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

# 1. Dazed and Confused — drop streams 1(rus),2(rus),4(rus sub), keep 0(video),3(eng),5(eng sub)
remux_strip \
    "$LIBRARY/Dazed and Confused (1993)/Dazed.and.Confused.1993.1080p.Bluray.AVC.Remux.mkv" \
    "$LIBRARY/Dazed and Confused (1993)" \
    "Dazed and Confused (1993).mkv" \
    -map 0:0 -map 0:3 -map 0:5

# 2. Marriage Story — drop streams 1-4(rus audio),6(rus sub), keep rest
remux_strip \
    "$LIBRARY/Marriage Story (2019)/Marriage.Story.2019.Criterion.Collection-BDRemux.1080p.mkv" \
    "$LIBRARY/Marriage Story (2019)" \
    "Marriage Story (2019).mkv" \
    -map 0:0 -map 0:5 -map 0:7 -map 0:8 -map 0:9 -map 0:10 -map 0:11 -map 0:12

# 3. Mishima — drop streams 1(rus),2(rus),6(rus sub), keep video+eng+jpn+commentary+eng sub
remux_strip \
    "$LIBRARY/Mishima - A Life in Four Chapters (1985)/Mishima.A.Life.in.Four.Chapters.1985.Criterion.Collection.BDRemux.1080p.mkv" \
    "$LIBRARY/Mishima - A Life in Four Chapters (1985)" \
    "Mishima - A Life in Four Chapters (1985).mkv" \
    -map 0:0 -map 0:3 -map 0:4 -map 0:5 -map 0:7

# 4. The Social Network — drop stream 1(rus audio),6(rus sub),8(rus sub), keep rest
remux_strip \
    "$LIBRARY/The Social Network (2010)/Social Network.2010.BD.Remux.1080p.h264.Rus.Eng.2xCommentary.mkv" \
    "$LIBRARY/The Social Network (2010)" \
    "The Social Network (2010).mkv" \
    -map 0:0 -map 0:2 -map 0:3 -map 0:4 -map 0:5 -map 0:7

echo ""
echo "========================================"
echo "  COMPLETE"
echo "  Succeeded: $SUCCEEDED / 4"
echo "  Failed:    $FAILED / 4"
echo "  Finished:  $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

rmdir "$SCRATCH" 2>/dev/null || true
