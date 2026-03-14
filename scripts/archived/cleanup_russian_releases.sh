#!/usr/bin/env bash
# Wave 6: Clean up Russian-only releases from qBittorrent + library
#
# Three operations:
#   A) Cancel Russian-only torrents still downloading in qBittorrent
#   B) Fix misnamed / unusable library files
#   C) Trigger Radarr rescan + re-search for affected movies
#
# Run from the NAS host (not inside a container):
#   sudo bash scripts/cleanup_russian_releases.sh
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────────────
LIBRARY="/mnt/pool/media/movies"
QB_URL="http://localhost:8081"
QB_USER="admin"
QB_PASS="${QB_PASSWORD:-}"  # Set via env or edit here
RADARR_URL="http://localhost:7878"
RADARR_API_KEY="${RADARR_API_KEY:-}"  # Set via env or edit here

DRY_RUN="${DRY_RUN:-false}"

if [ -z "$QB_PASS" ]; then
    echo "ERROR: Set QB_PASSWORD env var (qBittorrent password)"
    echo "  export QB_PASSWORD='yourpass'"
    exit 1
fi

if [ -z "$RADARR_API_KEY" ]; then
    echo "WARNING: RADARR_API_KEY not set — will skip Radarr operations"
fi

echo "========================================"
echo "  WAVE 6: Russian Release Cleanup"
echo "  Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Dry run: $DRY_RUN"
echo "========================================"

# ── Helper: qBittorrent API ──────────────────────────────────────────────────
QB_COOKIE=""

qb_login() {
    QB_COOKIE=$(curl -s -c - \
        -d "username=${QB_USER}&password=${QB_PASS}" \
        "${QB_URL}/api/v2/auth/login" 2>/dev/null | grep -o 'SID\s.*' || true)
    if [ -z "$QB_COOKIE" ]; then
        echo "ERROR: qBittorrent login failed"
        exit 1
    fi
    echo "[qbt] logged in"
}

qb_api() {
    local endpoint="$1"
    shift
    curl -s -b "SID=${QB_COOKIE##*	}" "${QB_URL}${endpoint}" "$@"
}

# ── Helper: Radarr API ──────────────────────────────────────────────────────
radarr_api() {
    local method="$1"
    local endpoint="$2"
    shift 2
    curl -s -X "$method" \
        -H "X-Api-Key: ${RADARR_API_KEY}" \
        -H "Content-Type: application/json" \
        "${RADARR_URL}/api/v3${endpoint}" "$@"
}

radarr_get_movie_id() {
    local title_fragment="$1"
    radarr_api GET "/movie" | python3 -c "
import json, sys
movies = json.load(sys.stdin)
for m in movies:
    if '${title_fragment}'.lower() in m.get('title', '').lower():
        print(m['id'])
        break
" 2>/dev/null || echo ""
}

radarr_refresh_movie() {
    local movie_id="$1"
    if [ -n "$movie_id" ] && [ -n "$RADARR_API_KEY" ]; then
        echo "[radarr] refreshing movie ID $movie_id"
        if [ "$DRY_RUN" != "true" ]; then
            radarr_api POST "/command" -d "{\"name\": \"RefreshMovie\", \"movieIds\": [$movie_id]}"
        fi
    fi
}

radarr_search_movie() {
    local movie_id="$1"
    if [ -n "$movie_id" ] && [ -n "$RADARR_API_KEY" ]; then
        echo "[radarr] triggering search for movie ID $movie_id"
        if [ "$DRY_RUN" != "true" ]; then
            radarr_api POST "/command" -d "{\"name\": \"MoviesSearch\", \"movieIds\": [$movie_id]}"
        fi
    fi
}

CLEANED=0
ERRORS=0

# ═════════════════════════════════════════════════════════════════════════════
# OPERATION A: Cancel Russian-only torrents from qBittorrent
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "── A) Cancelling Russian-only torrents ──────────────────────────"

qb_login

# Get all torrents and filter for Russian patterns
RUSSIAN_HASHES=$(qb_api "/api/v2/torrents/info" | python3 -c "
import json, sys, re
torrents = json.load(sys.stdin)
# Patterns that indicate pure-Russian releases
russian_patterns = [
    r'_RUS_BLUEBIRD',
    r'_RUS_',
    r'\bRUS\b.*BLUEBIRD',
]
for t in torrents:
    name = t.get('name', '')
    for pattern in russian_patterns:
        if re.search(pattern, name, re.IGNORECASE):
            size_gb = t.get('size', 0) / (1024**3)
            progress = t.get('progress', 0) * 100
            print(f'{t[\"hash\"]}|{name}|{size_gb:.1f}GB|{progress:.0f}%')
            break
" 2>/dev/null || true)

if [ -z "$RUSSIAN_HASHES" ]; then
    echo "  No Russian-only torrents found in qBittorrent"
else
    while IFS='|' read -r hash name size progress; do
        echo "  REMOVING: $name ($size, ${progress} complete)"
        if [ "$DRY_RUN" != "true" ]; then
            qb_api "/api/v2/torrents/delete" \
                -d "hashes=${hash}&deleteFiles=true" || {
                echo "  ERROR: failed to remove $name"
                ERRORS=$((ERRORS + 1))
                continue
            }
        fi
        CLEANED=$((CLEANED + 1))
    done <<< "$RUSSIAN_HASHES"
fi

# ═════════════════════════════════════════════════════════════════════════════
# OPERATION B: Fix misnamed / unusable library files
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "── B) Fixing misnamed library files ─────────────────────────────"

# --- B1: Kill Bill Vol 1 misnamed as "Ray (2004)" ---
KILL_BILL_SRC="$LIBRARY/Ray (2004)/Ray (2004).mkv"
KILL_BILL_DST_DIR="$LIBRARY/Kill Bill - Vol. 1 (2003)"
KILL_BILL_DST="$KILL_BILL_DST_DIR/Kill Bill - Vol. 1 (2003).mkv"

if [ -f "$KILL_BILL_SRC" ]; then
    # Verify it's actually Kill Bill by checking duration (~111 min, ~62 GB)
    SRC_SIZE=$(stat --format="%s" "$KILL_BILL_SRC" 2>/dev/null || echo 0)
    SRC_GB=$(echo "scale=1; $SRC_SIZE / 1073741824" | bc)

    if [ "$SRC_SIZE" -gt 30000000000 ]; then  # > 30 GB = definitely not Ray
        echo "  Kill Bill Vol 1 found misnamed as Ray (2004) (${SRC_GB} GB)"
        echo "    Moving to: $KILL_BILL_DST"
        if [ "$DRY_RUN" != "true" ]; then
            sudo mkdir -p "$KILL_BILL_DST_DIR"
            sudo mv "$KILL_BILL_SRC" "$KILL_BILL_DST"
            CLEANED=$((CLEANED + 1))
        fi
    else
        echo "  Ray (2004).mkv exists but is ${SRC_GB} GB — not Kill Bill, skipping move"
    fi
else
    echo "  Kill Bill/Ray fix: source not found (already fixed?)"
fi

# --- B2: Clean up old Ray (2004) Italian file (3.5 GB) ---
# After moving Kill Bill out, any remaining small file in Ray (2004)/ is the
# old Italian/English Ray that should be redownloaded
RAY_DIR="$LIBRARY/Ray (2004)"
if [ -d "$RAY_DIR" ]; then
    REMAINING=$(find "$RAY_DIR" -type f -name "*.mkv" -o -name "*.m2ts" -o -name "*.mp4" 2>/dev/null | head -1)
    if [ -n "$REMAINING" ]; then
        REM_SIZE=$(stat --format="%s" "$REMAINING" 2>/dev/null || echo 0)
        REM_GB=$(echo "scale=1; $REM_SIZE / 1073741824" | bc)
        echo "  Removing old Ray file: $(basename "$REMAINING") (${REM_GB} GB)"
        if [ "$DRY_RUN" != "true" ]; then
            sudo rm -rf "$RAY_DIR"
            CLEANED=$((CLEANED + 1))
        fi
    else
        # Empty dir — clean up
        echo "  Removing empty Ray (2004) directory"
        if [ "$DRY_RUN" != "true" ]; then
            sudo rm -rf "$RAY_DIR"
        fi
    fi
fi

# --- B3: Intouchables — French + Russian, zero English ---
INTOUCHABLES_PATTERN="$LIBRARY/INTOUCHABLES*"
for dir in $INTOUCHABLES_PATTERN; do
    if [ -d "$dir" ]; then
        echo "  Removing Intouchables (Russian/French only): $(basename "$dir")"
        if [ "$DRY_RUN" != "true" ]; then
            sudo rm -rf "$dir"
            CLEANED=$((CLEANED + 1))
        fi
    fi
done
# Also check canonical Radarr path
INTOUCHABLES_CANONICAL="$LIBRARY/The Intouchables (2011)"
if [ -d "$INTOUCHABLES_CANONICAL" ]; then
    # Verify it has no English audio before deleting
    INTOUCHABLES_FILE=$(find "$INTOUCHABLES_CANONICAL" -type f \( -name "*.mkv" -o -name "*.mp4" \) | head -1)
    if [ -n "$INTOUCHABLES_FILE" ]; then
        HAS_ENG=$(ffprobe -v quiet -print_format json -show_streams "$INTOUCHABLES_FILE" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
for s in data.get('streams', []):
    if s.get('codec_type') == 'audio':
        lang = s.get('tags', {}).get('language', 'und')
        if lang in ('eng', 'und'):
            print('yes')
            break
" 2>/dev/null || echo "")
        if [ -z "$HAS_ENG" ]; then
            echo "  Removing Intouchables canonical dir (no English audio)"
            if [ "$DRY_RUN" != "true" ]; then
                sudo rm -rf "$INTOUCHABLES_CANONICAL"
                CLEANED=$((CLEANED + 1))
            fi
        else
            echo "  Intouchables has English audio — keeping"
        fi
    fi
fi

# --- B4: Tokyo Story (1953) — Russian dubs + English commentary only ---
TOKYO_STORY_DIR="$LIBRARY/Tokyo Story (1953)"
if [ -d "$TOKYO_STORY_DIR" ]; then
    TOKYO_FILE=$(find "$TOKYO_STORY_DIR" -type f \( -name "*.mkv" -o -name "*.mp4" -o -name "*.m2ts" \) | head -1)
    if [ -n "$TOKYO_FILE" ]; then
        # Check audio: should find 4×rus + 1×eng (commentary)
        AUDIO_INFO=$(ffprobe -v quiet -print_format json -show_streams "$TOKYO_FILE" 2>/dev/null | python3 -c "
import json, sys
data = json.load(sys.stdin)
langs = []
for s in data.get('streams', []):
    if s.get('codec_type') == 'audio':
        lang = s.get('tags', {}).get('language', 'und')
        title = s.get('tags', {}).get('title', '')
        langs.append(f'{lang}:{title}')
print('|'.join(langs))
" 2>/dev/null || echo "")
        echo "  Tokyo Story audio tracks: $AUDIO_INFO"

        # Count non-commentary English tracks
        REAL_ENG=$(echo "$AUDIO_INFO" | tr '|' '\n' | grep -i '^eng:' | grep -iv 'commentary' | wc -l)
        if [ "$REAL_ENG" -eq 0 ]; then
            echo "  Removing Tokyo Story (no proper English audio)"
            if [ "$DRY_RUN" != "true" ]; then
                sudo rm -rf "$TOKYO_STORY_DIR"
                CLEANED=$((CLEANED + 1))
            fi
        else
            echo "  Tokyo Story has $REAL_ENG non-commentary English track(s) — keeping"
        fi
    fi
else
    echo "  Tokyo Story: not found (already fixed?)"
fi

# ═════════════════════════════════════════════════════════════════════════════
# OPERATION C: Trigger Radarr rescan + re-search
# ═════════════════════════════════════════════════════════════════════════════
echo ""
echo "── C) Triggering Radarr rescan / search ─────────────────────────"

if [ -n "$RADARR_API_KEY" ]; then
    # Refresh + search for movies we deleted or moved
    for title in "Kill Bill" "Ray" "Intouchables" "Tokyo Story"; do
        MOVIE_ID=$(radarr_get_movie_id "$title")
        if [ -n "$MOVIE_ID" ]; then
            radarr_refresh_movie "$MOVIE_ID"
            # Only search for movies we deleted (not Kill Bill which we just moved)
            if [ "$title" != "Kill Bill" ]; then
                sleep 2  # Let refresh settle
                radarr_search_movie "$MOVIE_ID"
            fi
        else
            echo "  [radarr] '$title' not found in Radarr library"
        fi
    done
else
    echo "  Skipping Radarr operations (no API key)"
fi

echo ""
echo "========================================"
echo "  WAVE 6 COMPLETE"
echo "  Items cleaned: $CLEANED"
echo "  Errors:        $ERRORS"
echo "  Finished:      $(date '+%Y-%m-%d %H:%M:%S')"
echo "========================================"

if [ "$DRY_RUN" = "true" ]; then
    echo ""
    echo "  *** DRY RUN — no changes were made ***"
    echo "  Re-run without DRY_RUN=true to execute"
fi
