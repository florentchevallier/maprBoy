# Wandrer -> Wahoo Bolt map pipeline (WMC)

Proof of concept validated on tile 136/88 (Bolt v2): WMC base map + orange unvisited roads + violet dashed boundaries + POIs.

## Chain
1. `scripts/kmz_to_osm.py -o out.osm exports...` merges the exports, applies the radius filter, merges chains and simplifies; also writes `out.osm.tiles.json`.
2. `scripts/build_tiles.py --xy ... --wandrer out.osm` (WMC importable): converts to pbf with osmium, runs WMC for the requested tiles (classic), then redoes merge and map creation for the tiles touched by Wandrer. Output: `wmc_classique/`, `wmc_wandrer/`, `README.txt`, `size_report.txt`.
3. Copy the files listed in `README.txt` to the Bolt one by one (back up `maps/tiles/8` first).
4. Theme: `themes/vtm/vtm-elemnt.xml` (~50 KB) + `icons/` into `maps/vtm-elemnt/`.

## De-duplication across exports
Key = coordinates rounded to 1e-6, direction ignored. The most recent export wins; order comes from the timestamp in the KML (`<Document><name>Wandrer Export YYYYMMDD-HH:MM:SS`), not from the file name. The activity (`bike`/`foot`) is read from the inner KML file name (warning if mixed).
An old segment absent from a newer export is removed only if that export REPLACES it: at least 90 % of the old segment's points (sampled every 4 m) lie within a few metres of the newer export's roads (Wandrer redrew the road after an OpenStreetMap edit, or it was ridden and came back as a traveled segment with another geometry). Further conditions: same activity (bike/foot exports never remove each other's roads), the export has that class (unvisited only if it has Untraveled, visited only if it has Traveled), and the old segment lies in the export's bounding box (plus 100 m). Boundaries are de-duplicated by ring.
Absence alone proves nothing: the boundary polygons of an export cover far more than the roads it holds (checked on real exports: removing by boundary deleted about 40,000 valid roads). Segments that are absent but not redrawn are kept and counted ("older roads kept" in the merge log).

## Slimming the data (all options, to be exposed in the GUI)
Applied after merging, before writing OSM (`wahoondrer/process.py`):
1. **Radius around traveled roads** (`--radius-m`, default 250; 0 = keep everything): unvisited roads are kept only within R m of a traveled road. Elsewhere nothing from Wandrer is added (= everything is untraveled). `--unpaved-always` keeps all unpaved roads whatever the radius. 50 m grid, accuracy about +-50 m.
2. **Merge chained segments** of the same class (state + surface), at most 1000 points per way (`--no-merge` to disable).
3. **Simplification** Douglas-Peucker (`--simplify-m`, default 1.5 m; 0 = none). The overlay sits on top of OSM roads (it does not replace them): very small tolerance so it does not visibly drift from the road underneath.
Test with 6 exports (Munich, Augsburg, Southern Oberbayern, Innsbruck, Kufstein, Salzburg), 5 tiles: 749,489 segments -> 143,893 ways; 859,365 -> 546,342 points; 1,115,934 output nodes (over 3 million without filter and merge).

## Squadrats layer (optional)
`--squadrats file.kml` on `kmz_to_osm.py`. The Squadrats KML contains the EXPLORED squares, dissolved into polygons with holes: placemark `squadrats` = z14 grid (big squares, ~1.6 km), `squadratinhos` = z17 grid (small squares, ~200 m); `yard*` and `uber*` placemarks are ignored. The squares are the OSM slippy tiles of z14 / z17 (Web Mercator), so everything is computed in integer tile indices, without metric error; the rebuilt square counts match the `size` declared in the file.
`wahoondrer/squadrats.py` rebuilds the explored cell set (even-odd scan of the vertical edges), then draws the grid edges of the unexplored squares within `--squadrats-buffer` squares (default 3) of the explored ones, only in the z8 tiles touched by the Wandrer exports. Each edge is written once and collinear edges are merged into straight 2-node ways (~72k ways for 4 tiles, about 1 % of the tile size).
Small squares scope (`--squadrats-small-scope`): `explored-big` (default) outlines EVERY unexplored small square inside an explored big square (complete grid there, nothing in unexplored big squares); `band` keeps the band around explored small squares (it cuts the grid arbitrarily: a column 4 squares away from the nearest explored one is missing); `both` is the union. About 60k ways for 4 tiles in either case.
Tags: `squadrat=big|small` (no `highway`). Tag mapping: big `zoom-appear=11`, small `14`. Theme: big solid width 1.6 (zoom >= 11), small dashed `6,6` width 1.4 (zoom >= 14), red (`#E03030`, `#FF6060` in dark mode). Line width does not scale with zoom on the Bolt, so the small squares are dashed and only slightly thinner than the big ones to stay visible at 500 m.

## Data scheme
`highway=wandrer` + `wandrer=visited|unvisited` (+ `surface=unpaved`); boundaries `wandrer=boundary` (rings split into 2 open ways); Squadrats outlines `squadrat=big|small`. IDs: nodes 50e9+, ways 9e9+. Traveled segments are skipped by default (`--with-traveled`): they make tiles heavier and made the urban colour fills disappear on the Bolt.

## POI rules (one table, three generated files)
POIs are described once in `config/poi-rules.json` (one entry per POI: OSM key/value, zoom, icon, size,
optional `hide_if` tag values and optional `variants` = another icon depending on a secondary tag).
`python3 scripts/generate_poi_config.py` then writes the three places a tag must be declared:
- the tag-mapping XMLs `config/tag-wahoo-poi.xml` (classic tiles) and `config/tag-wahoo-poi-wandrer.xml`
  (only between the `generated:poi-nodes` / `generated:poi-ways` markers);
- the VTM theme (between the `generated:poi-theme` markers);
- `config/tags-to-keep.json` (add-only: missing values are added, nothing is removed).
Everything outside the markers is left untouched. `--check` only reports what is out of date.
Icons (SVG) go in `themes/vtm/icons/`; the script refuses an icon that does not exist.
Example: toilets use `toilets_free.svg` (`fee=no`), `toilets_paid.svg` (`fee=yes`), `toilets.svg` (no `fee`),
and are hidden when `access` is `customers` or `private`.
After a change: run the script, regenerate the tiles with `build_tiles.py`, copy the new icons and the theme
to the Bolt. `tag-wahoo-wandrer.xml` / `tag-wahoo-wandrer-poi-min.xml` are alternative full tag files and are not generated.

## Pitfalls
- Every new tag must be added in THREE places: the tag-mapping XMLs (`config/tag-wahoo*.xml`), `config/tags-to-keep.json` (WMC's osmium filter deletes everything not listed there) and the theme. `build_tiles.py` copies the first two into `~/wahooMapsCreatorData/_config`. Forgetting `tags-to-keep.json` silently removed the whole Squadrats layer (tile sizes did not change: use that as a check).
- Node IDs below 14e9 collide with real OSM nodes -> stray straight lines.
- The map writer drops tags that are not declared in the tag-mapping XML.
- The Wandrer VTM rule must sit at the root of the theme, with styles for bolt/roam/bolt2/bolt2-dark.
- `create_map_files()` only re-reads `merged.osm.pbf`: replay filter/split/merge.
- Cruiser v5 has no VTM engine: use the Mapsforge theme and an uncompressed `.map`.
- Bolt: use the 48 KB `vtm-elemnt.xml`, not the one from `theme_initial`.
- macOS has no native MTP (Android File Transfer / OpenMTP).
- WMC 4.4.0 declares only `packaging` as a pip dependency but imports `shapely`, `geojson`, `requests`; land generation needs the `ogr2ogr` command and `shape2osm.py` (started with a bare `python`) needs `osgeo`. A pip-only venv fails at import; the environment must be the one whose `python` comes first on PATH (see README, Installation).
- `ogr2ogr` and `osgeo` must come from the same GDAL. pip wheels do not provide `ogr2ogr`; conda-forge does.
- Homebrew on macOS 13 has no bottles (source build of ~160 formulae): use micromamba/conda-forge.
- `build_tiles.py --plan` still calls WMC's setup: it creates missing `~/wahooMapsCreatorData` folders and overwrites `_config` files with the repo's (identical files: no change). It does not download when the regions are already present.
- `build_tiles.py` deletes and rebuilds `<out>/wmc_classique` and `<out>/wmc_wandrer` on every real run; the default `--out` is inside `~/wahooMapsCreatorData/_output`, so pass another `--out` to keep a previous result.
- Comparing two builds of the same tile: the `.map` files differ only by the creation date in the header (bytes 41-44), so `cmp` reports a difference; sizes match to a few bytes.
