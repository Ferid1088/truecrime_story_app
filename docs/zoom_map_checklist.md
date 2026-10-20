# Zoom-in map shot — checklist

Owner decision (20 Oct 2026): the old map cards are not clear or informative; replace them with a cinematic
zoom (globe -> region -> street) built by the app from open data. No commit, no push for this task.

## M0 audit (read-only, before any change)

**Where the old map cards live**

| Part | Where |
|---|---|
| Geocoding (Nominatim), raster tiles, zoom chain, darkening, marker | `app/documentary/visuals/maps.py` (338 lines) |
| One library asset per zoom level (`asset_type="map"`, provider `generated`) | `app/documentary/visuals/generated.py` `map_chain` |
| Director: `SHOW_MAP`, `map_place`, "maps by geography", one map per place and film, not before `first_map_not_before_seconds` (20 s) | `app/documentary/visuals/director.py`, `app/agents/visual_director.py` (`places_already_mapped`) |
| Shot built from the zoom levels (`kind: "map"`, `map_paths`, credit + credit overlay) | `app/documentary/production/script.py` (`SHOW_MAP`, ~l. 1162) |
| Render: crossfade through the stored stills, slow push | `app/documentary/render/engine.py` (`kind == "map"`, `_map_frame`) |
| Config | `config/parameters/visuals.json` `maps` (tile URL, attribution, geocoder URL, darken), `visual_direction.first_map_not_before_seconds`; `documentary.json` `max_map_seconds` |
| Tests | `tests/test_visual_direction.py`, `tests/test_visual_production.py`, `tests/test_coverage_fill.py`, `tests/test_phase15_gate_coverage.py`, `tests/test_media_coverage.py`, `tests/test_media_library.py`, `tests/test_stage_director_agent.py`, `tests/test_shortform_phase2.py` |

**Imagery and tile data today:** only raster tiles of the *public* OpenStreetMap tile server
(`tile.openstreetmap.org`, cached under `data/map_cache`, 1 request per second) and Nominatim. No satellite imagery,
no vector data, no offline extract. The public tile server is what the owner's brief (M1) rules out.

**Case 8 today (script `pilot_v158_8`, 518 s):** 2 map shots, 156.3 s = **30.2 % of the film**
(Fall River 224–229 s, Rocky Point 349–500 s, motion `MAP_ZOOM`, 3–4 still levels each).

**Tooling found:** `ffmpeg`, Node + npm, Playwright browsers in `~/Library/Caches/ms-playwright` (Chromium), network OK
(`build.protomaps.com`, NASA GIBS, npm, PyPI, GitHub releases reachable). Not found: the Python `playwright` package in
the venv, the `pmtiles` CLI, MapLibre.

## Requirements

| # | Requirement | Status | Evidence |
|---|---|---|---|
| M1.1 | Vector map data (OSM, Protomaps/PMTiles) as a regional extract under `data/maps/<case>/` | TODO | |
| M1.2 | Bounding box from config around the case's places | TODO | |
| M1.3 | Never the public OSM tile servers | TODO | |
| M1.4 | Building footprints from the same data for 3D extrusion | TODO | |
| M2.1 | Imagery registry in config (id, coverage, zoom range, resolution, licence, attribution, access URL, allowed) | TODO | |
| M2.2 | Start entries: NASA GIBS, Sentinel-2, USGS NAIP / MassGIS, German state portals, others verified open | TODO | |
| M2.3 | Test fails on an entry without licence/attribution or with a forbidden provider | TODO | |
| M2.4 | No coverage at a zoom: end at best level and cross-fade to the vector map, never fake detail | TODO | |
| M2.5 | Tiles cached under `data/maps/<case>/imagery/` with source, date, licence per file | TODO | |
| M3.1 | MapLibre in headless Chromium (Playwright), frame by frame, deterministic | TODO | |
| M3.2 | ffmpeg encode at the film's resolution and frame rate | TODO | |
| M3.3 | Camera path globe -> region -> city -> street, fast then decelerating; optional pitch/bearing | TODO | |
| M3.4 | Marker pulse at the target | TODO | |
| M3.5 | Place name (+ optional date) overlay in the film's font and safe areas | TODO | |
| M3.6 | `name:<lang>` labels with fallback chain, never mixed scripts; fa/ar overlays right-to-left | TODO | |
| M3.7 | Dark documentary style | TODO | |
| M3.8 | Variants from config: `zoom_in`, `route`, `pitched_3d`; durations 6-10 s from config | TODO | |
| M4.1 | Map beat uses the zoom shot: first mention, move between places, "where was X" | TODO | |
| M4.2 | Only places the story already told (spoiler firewall) | TODO | |
| M4.3 | Limits per film and per act; a returning place gets another start level/direction | TODO | |
| M4.4 | No usable data: fall back to existing behaviour and report it | TODO | |
| M4.5 | Remove old map-card code/config/tests only after a search proves nothing uses them; list removals | TODO | |
| M5.1 | On-screen attribution while the shot plays + film credits; stored with the asset | TODO | |
| M6 | Tests: determinism, attribution, forbidden providers, imagery fallback, label chain (en/de/fa/ar), RTL, firewall, limits, caching, render-time budget, no call to a forbidden host | TODO | |
| M7 | Live proof on case 8: establishing zoom, route, pitched 3D; contact sheets; render time; size; imagery source per zoom; old card vs new; film map share before/after | TODO | |
| M8 | Sentence-by-sentence audit of this text; diff self-review; list of what is unproven | TODO | |
