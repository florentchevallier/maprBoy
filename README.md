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
- `wahoondrer/process.py`: keeps unvisited roads only within a radius of traveled roads (default 250 m), merges chained segments, simplifies geometry.
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
<data folder>/Wandrer-exports/   KMZ exports
<data folder>/Wandrer data/      generated wandrer.osm(.pbf), wandrer.osm.tiles.json
<data folder>/Squadrat exports/  Squadrats KML export
```
WMC hard-codes `~/wahooMapsCreatorData` (downloaded OSM regions, `_config`, `_tiles`,
`_output`); a symlink is the clean way to move it.

## Notes
- Keep exports of one activity per run if you do not want to mix bike and foot (a warning is printed).
- Do not commit KMZ files or generated maps (personal data, not redistributable); `.gitignore` covers them, but keep the exports outside the repository.
- On the Bolt, copy files one by one (not whole folders) and back up `maps/tiles/8` first; `build_tiles.py` writes a README with the exact list.
- WMC is GPL-3: check licences before redistributing anything (this is not legal advice).

## History
`PROJECT_NOTES.md` holds the earlier project notes, kept for the project history.
