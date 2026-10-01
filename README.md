# Wahoondrer

Builds Wahoo ELEMNT (Bolt v2) maps with [WahooMapsCreator](https://github.com/treee111/wahooMapsCreator) (WMC)
and bakes your **Wandrer** data (unexplored roads, achievement boundaries) into the base map.

- Source: Wandrer KML/KMZ exports (no OSM matching).
- Output: `.map.lzma` files per z8 tile, with a `highway=wandrer` layer (`wandrer=visited|unvisited`).
- Optional Squadrats layer (from a Squadrats KML export): outlines of the squares NOT yet explored, as thin red lines (big z14 squares solid, small z17 squares dashed).
- VTM theme `vtm-elemnt.xml`: unvisited roads in orange (dashed when unpaved), boundaries as a subtle violet dashed line.

## Status
- Proof of concept validated on tile 136/88 (Bolt v2).
- `wahoondrer/kmz.py`: reads and merges several exports with de-duplication (newest export wins, ordered by the timestamp inside the KML).
- `wahoondrer/process.py`: keeps unvisited roads only within a radius of traveled roads (default 250 m; options to keep every unpaved or every paved road), merges chained segments (always), simplifies geometry.
- `scripts/build_tiles.py`: classic WMC tiles + Wandrer tiles, output folder with a copy guide for the Bolt.
- `wahoondrer/squadrats.py`: rebuilds the explored squares from the Squadrats KML and outlines the unexplored ones in a band around them.
- Planned: GUI (options: keep traveled roads, boundaries on/off, radius, unpaved handling), simple installation.

## Quick use
```
# 1. merge exports into one OSM file (standard library only)
python3 scripts/kmz_to_osm.py -o ~/wandrer.osm path/to/exports/*.kmz

# 1b. optional: add the Squadrats outlines (unexplored squares)
python3 scripts/kmz_to_osm.py -o ~/wandrer.osm path/to/exports/*.kmz \
    --squadrats path/to/squadrats.kml [--squadrats-levels big,small] [--squadrats-buffer 3] \
    [--squadrats-small-scope explored-big|band|both]

# 2. build the tiles (WMC importable, Java and osmium installed)
python3 scripts/build_tiles.py \
    --xy 135/88,135/89,136/88,136/89 --wandrer ~/wandrer.osm --plan
```
Remove `--plan` for the real run. See `docs/PIPELINE.md` for details.

## Graphical interface (local web page)
```bash
wahoondrer-env                     # or any environment where WMC is installed
python3 scripts/gui.py             # opens http://127.0.0.1:8765/ in your browser
```
1. **Folders**: press "Set up my folders" and pick a location; the page creates `Wandrer_exports`,
   `Squadrats_exports`, `Output`, `Work_files` and `OSM_data` there (or choose your own folders; "Open" shows
   a folder in your file manager). Drop your `.kmz` / `.kml` files on the page or into those folders.
   `OSM_data` holds the OpenStreetMap extracts: WMC always reads `~/wahooMapsCreatorData`, so that path
   becomes a symbolic link to `OSM_data` (created automatically if WMC has no data yet; otherwise the page
   offers to move the existing data, never without asking).
2. **Exports**: each export is listed with its date, type (bike, foot, combined), the town where you ride
   most in it (looked up online, can be switched off), what it contains (coloured tags) and its tiles.
   "Which exports to use" picks one type: bike (default), foot or combined. Pro mode adds "all types" (mix)
   and "latest" (type of the newest export). Reading big files takes a while the first time
   (a progress bar is shown); results are cached.
3. **Tiles**: click the map (zoom-8 tiles), or use the tiles of your exports. Buttons: zoom to your data, my location.
4. **Wandrer layer** / 5. **Squadrats layer**: options. The **Simple / Pro** switch (top right) shows more
   options and the numbers inside each export.
6. **Map data from OpenStreetMap**: the regions WMC has downloaded (date, size, age); update or delete one,
   download the ones your tiles need, or add another region. A region Geofabrik no longer offers (old French
   regions such as Picardie) is reported and skipped; download its parent region instead. WMC renews regions
   older than 14 days by itself.
7. **Make the maps**: "Check the plan" lists the tiles and OSM regions needed; "Generate maps" runs
   `kmz_to_osm.py` then `build_tiles.py` and shows the log.

Settings are remembered in `~/.wahoondrer/settings.json` and a reading cache in `~/.wahoondrer/cache.json`
(outside the repository). The page only listens on your own computer (127.0.0.1). The map background needs
an internet connection; without it, type the tiles in the box. Copying to the Bolt is still manual: see
`README.txt` in the output folder.

## Contour lines (optional, experimental)
Tick **Add contour lines** in step 7 (Pro mode), or use `--contour` with `scripts/build_tiles.py`. The lines come
from the free Viewfinder Panoramas elevation data (3 arc-seconds: standard lines, not the finest SRTM 1 ones) and
need **no account**. Wahoondrer's theme draws them in thin grey/brown lines (100 m lines from zoom 12, 50 m from 13,
10 m from 14). Not tested on a Bolt yet.

One-time setup, in a terminal (it installs `pyhgtmap` in its own small environment in `~/.wahoondrer/`, so it
cannot disturb GDAL or shapely):

    python3 scripts/build_tiles.py --setup-contour

Why not WMC's own contour step? It calls `phyghtmap` 2.23, an old program that fails with current Python libraries.
Wahoondrer runs `pyhgtmap` (its maintained fork) itself with the same settings and hands the result to WMC.

The first run downloads the elevation data and computes the lines of every tile (slow); both are kept for the next
runs (an unfinished or empty contour file is detected and redone). Tiles become heavier: check `size_report.txt`.
With contour lines, the theme must be copied to the Bolt too (see `README.txt` in the output folder).

## Installation
Wahoondrer itself needs only Python 3 (standard library). `scripts/build_tiles.py` also needs
WMC and its tools: Java, osmium-tool, Osmosis and GDAL (`ogr2ogr` **and** the Python `osgeo`
module, from the same GDAL build).

Tested setup (clean install, 2026-09-30, macOS 13 Ventura, Intel):

| Component | Version | Source |
|---|---|---|
| WahooMapsCreator | 4.4.0 (`pip show wahoomc`) | pip (`requirements.txt`) |
| Python | 3.10.21 | conda-forge |
| GDAL (`ogr2ogr` + `osgeo`) | 3.6.4 | conda-forge |
| osmium-tool | 1.16.0 | conda-forge |
| shapely / geojson / requests | 2.0.2 / 3.3.0 / 2.34.2 | conda-forge |
| Java | Temurin 26.0.2.1 | any JRE that runs WMC |
| Osmosis | 0.49.2 | manual download, on PATH |

**Recommended: micromamba** (standalone binary, no Anaconda, no shell init):
```
mkdir -p ~/micromamba-bin && cd ~/micromamba-bin
curl -fsSL https://micro.mamba.pm/api/micromamba/osx-64/latest | tar -xj bin/micromamba   # osx-arm64 on Apple Silicon
export MAMBA_ROOT_PREFIX=~/micromamba-bin/root
~/micromamba-bin/bin/micromamba create -f environment.yml --override-channels -c conda-forge
export PATH="$MAMBA_ROOT_PREFIX/envs/wahoondrer-env/bin:$PATH"   # or: micromamba activate wahoondrer-env
```
Then install Java and Osmosis if missing (Osmosis: download from
<https://github.com/openstreetmap/osmosis/releases>, unpack, add its `bin/` to PATH), and check:
```
ogr2ogr --version; osmium --version; java -version; osmosis -v
python -c "from osgeo import gdal, ogr; import wahoomc, shapely, geojson, requests"
```
`environment.yml` (conda-forge part) and `requirements.txt` (pip part) hold the tested pins.
A plain Anaconda/Miniconda works the same way with `conda env create -f environment.yml`.

**Why not Homebrew + venv?** WMC 4.4.0 declares only `packaging` as a dependency but imports
`shapely`, `geojson`, `requests` and `osgeo`, and it calls `ogr2ogr`. On macOS 13, Homebrew no
longer provides bottles: `brew install gdal osmium-tool` would compile about 160 formulae
(llvm, ghc, pandoc...) from source, which takes hours and needs newer Command Line Tools. On a
macOS version that Homebrew still supports, `brew install gdal osmium-tool` plus a venv with
`pip install -r requirements.txt shapely geojson requests "GDAL==$(gdal-config --version)"`
should work, but it is untested here. pip wheels of GDAL do not ship the `ogr2ogr` command.

GDAL 3.6 is the tested version. Newer versions (3.13 is current) are not tested yet.
WMC must be importable (`PYTHONPATH=/path/to/wahooMapsCreator` also works for a clone).
Record the WMC version (`pip show wahoomc`) when reporting a problem.

## Suggested folder layout (outside the repository)
One data folder, one subfolder per source (any location, quote paths that contain spaces):
```
<data folder>/Wandrer_exports/   KMZ exports
<data folder>/Squadrats_exports/ Squadrats KML export
<data folder>/Work_files/        generated wandrer.osm(.pbf), wandrer.osm.tiles.json
<data folder>/Output/            generated tiles
<data folder>/OSM_data/          WMC data (downloaded OSM regions, `_config`, `_tiles`, `_output`)
```
WMC hard-codes `~/wahooMapsCreatorData`; the GUI makes it a symbolic link to `OSM_data`
(`ln -s <data folder>/OSM_data ~/wahooMapsCreatorData` does the same by hand).

## Notes
- Keep exports of one activity per run if you do not want to mix bike and foot (a warning is printed).
- Do not commit KMZ files or generated maps (personal data, not redistributable); `.gitignore` covers them, but keep the exports outside the repository.
- On the Bolt, copy files one by one (not whole folders) and back up `maps/tiles/8` first; `build_tiles.py` writes a README with the exact list.
- WMC is GPL-3: check licences before redistributing anything (this is not legal advice).

## History
`PROJECT_NOTES.md` holds the earlier project notes, kept for the project history.
