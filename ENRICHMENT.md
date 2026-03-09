# Jellyfin UI Enrichment — Netflix-like Experience on All Clients

> **Status**: Proposal / Future work
> **Goal**: Make Jellyfin feel like Netflix on Roku, Firestick, and all other clients — without relying on web-only CSS hacks or plugins that don't work on native TV apps.

---

## The Core Problem

Jellyfin's web client is fully customizable (CSS/JS injection, theme plugins), but **Roku, Firestick, Apple TV, and mobile apps are native clients with hardcoded UIs**. No server-side plugin or theme will change how those apps render.

| Client | Custom themes? | Custom JS/plugins? |
|--------|---------------|-------------------|
| Web browser | Yes | Yes |
| iOS / Android | No | No |
| Firestick (Android TV) | No | No |
| Roku | No | No |
| Apple TV | No | No |

The only server-side structure that **every client** respects is **Libraries**. Each Jellyfin Library appears as a top-level navigation item on all platforms, including Roku and Firestick.

---

## Proposed Solution: Curated Genre Libraries via Symlinks

Create genre-specific directories populated with symlinks to actual media files, then register each as a Jellyfin library. This gives Netflix-style genre browsing on every client with zero plugins, zero client hacks, and zero duplicate disk space.

### Example Library Structure

```
/mnt/pool/media/curated/
├── Movies: Sci-Fi/
│   ├── Interstellar (2014).mkv -> /mnt/pool/media/movies/Interstellar (2014)/Interstellar (2014).mkv
│   └── The Matrix (1999).mkv   -> /mnt/pool/media/movies/The Matrix (1999)/The Matrix (1999).mkv
├── Movies: Action/
│   └── ...
├── Movies: Animation/
│   └── ...
├── TV: Anime/
│   └── ...
├── TV: Comedy/
│   └── ...
├── TV: Drama/
│   └── ...
├── Award Winners/
│   └── ...
└── Recently Added/
    └── ...
```

Each directory becomes a Jellyfin library that shows up as its own section on Roku, Firestick, web, and every other client.

### Why This Works

- **Jellyfin already has genre metadata** for every movie/show (from TMDB/TVDB during library scan)
- Symlinks mean **zero duplicate disk space**
- Libraries are the **only reliable cross-client navigation structure**
- No plugins, no client-side hacks, no sideloading browsers

---

## Implementation Plan

### Phase 1 — Install Useful Plugins (Manual, One-Time)

Via Jellyfin Dashboard > Plugins:

| Plugin | Purpose | Notes |
|--------|---------|-------|
| **Playback Reporting** | Watch stats, "most played" data | Mature, official repo. Enables data-driven curation later. |
| **Skin Manager** | Theme switching for web UI | Web-only. Nice when browsing on laptop/phone. Zero effort to install. |

### Phase 2 — Automated Genre Library Generator

Build into the orchestrator as a new feature. The logic:

1. **Query Jellyfin API** for all movies/shows with their genre metadata
2. **For each configured genre**, create a directory under `/mnt/pool/media/curated/<Genre>/`
3. **Symlink** each matching movie/show into the genre directory
4. **Register** each curated directory as a Jellyfin library (via Jellyfin API)
5. **Run on schedule** (or trigger after sweep/pipeline completion) to keep libraries fresh

#### Potential Curated Collections

- **Pure genre rows**: Sci-Fi, Action, Comedy, Drama, Animation, Horror, Thriller, etc.
- **Smart collections**: "Recently Added" (last 30 days), "Trending" (most-watched via Playback Reporting data)
- **Media-type splits**: "TV: Anime", "TV: Drama", "Movies: Animation" — separate from the main Movies/TV libraries
- **Custom tags**: "Award Winners", "Family Movie Night", "4K Collection"

#### API Endpoints (Sketch)

```
POST /api/enrichment/sync      — Rebuild all curated libraries from current Jellyfin metadata
GET  /api/enrichment/status     — Current curated library state and last sync time
GET  /api/enrichment/config     — List configured genre mappings
PUT  /api/enrichment/config     — Update genre mappings and collection rules
```

---

## What Was Evaluated and Rejected

### ML/Embedding-Based Recommender (Overkill)

ChatGPT proposed collaborative filtering, sentence embeddings, and hybrid recommenders. This is impractical for a personal media server with a handful of users — not enough watch history data for CF to produce meaningful signals. Hand-curated genre libraries will feel better than any ML system at this scale.

### Kodi on Firestick (Nuclear Option)

Kodi offers deep UI customization on Firestick, but it's a completely different app with its own learning curve, skin ecosystem, and maintenance burden. Only worth considering if Jellyfin's native Android TV app is truly unusable.

### Silk Browser Workaround (Trade-off)

Sideloading a web browser on Firestick to load Jellyfin's themed web UI. You get the themed experience but lose native remote control integration, hardware decoding optimizations, and it's generally clunkier. Most people find it not worth it.

### Experimental Plugins

| Plugin | Status | Verdict |
|--------|--------|---------|
| **LocalRecs** | Very young, not in official repo | Worth experimenting with TF-IDF genre/cast similarity, but don't expect Netflix-quality recs |
| **JellyDiscover** | Barely exists (Reddit post, minimal GitHub traction) | Not production-ready |
| **AudioMuse-AI** | Music discovery | Irrelevant for movies/TV use case |

---

## References

- Jellyfin API: `GET /Items?Recursive=true&IncludeItemTypes=Movie&Fields=Genres`
- Jellyfin library registration: `POST /Library/VirtualFolders`
- Playback Reporting plugin: [official Jellyfin plugin repo](https://github.com/jellyfin/jellyfin-plugin-playback-reporting)
- Skin Manager plugin: [danieladov/jellyfin-plugin-skin-manager](https://github.com/danieladov/jellyfin-plugin-skin-manager)
