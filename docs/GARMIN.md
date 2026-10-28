# Garmin maps (mkgmap) — findings and recipe

Status: the command lines `scripts/build_garmin.py` (Wandrer/Squadrats/POI overlay) and `scripts/build_garmin_base.py`
(OSM base map), code in `wahoondrer/garmin/`, also run by the web page (Garmin pill, "Make the maps").
Tested on a Forerunner 955, Edge 830 and 530.

## From the page
With the Garmin pill on, "Make the maps" writes `Output/garmin/maprboy-<place>-<date>-overlay.img` and, unless
"Make an OpenStreetMap base map" is unticked in the Basemap & POIs section, `...-base.img`; the POIs go into the
base map, or into the overlay without it. The place is the town most ticked exports are near, else the chosen
regions, else the town at the centre of the area (online lookup, if allowed). Each build gets its own family id
and map numbers (`wahoondrer/garmin/ids.py`) and shows as "maprBoy base <place> <date>" in the device's map list,
so that maps of several places can stay on one device: not checked on a device yet. A file name other than
gmapsupp.img and a family id other than 1299 were seen working on the Edge 530; on the Forerunner 955 only
gmapsupp.img was tried. The command lines keep the tested defaults unless `--name`, `--family-id` and `--mapname`
are given. It needs the path of mkgmap.jar (settings ⚙), Java, osmium and
cairosvg: the page says what is missing. The area follows the tile choice of the page: the chosen z8 tiles, the
area of the ticked exports (plus the margin of "exports + km"), or the chosen regions; it is given to the scripts
as `--bbox W,S,E,N`. The OSM extracts are the smallest Geofabrik regions touching that rectangle, in WMC's folder;
missing ones (and those older than the renewal age) are downloaded first (`python3 -m wahoondrer.gui.regions
--fetch KEY ...`). The look (colours, widths) is the default one for now.

## Principle
Garmin devices read vector maps as `.img` files (`Garmin/gmapsupp.img`). They are built from OSM data with
[mkgmap](https://www.mkgmap.org.uk/) (Java), a style (which OSM tag becomes which Garmin type) and a TYP file
(how each type is drawn: colour, width, border, bitmap pattern, icon).
KML/KMZ on a Garmin is only raster "Custom Maps", so the Wandrer KMZ exports stay what they are for the Wahoo
chain: the INPUT. The existing steps (`kmz.py` merge, `process.py`, `osmxml.py`) already produce the `.osm` that
mkgmap needs; only the output stage is new.

## What a Wandrer `.img` looks like (reverse-engineered from their downloads)
- Built with mkgmap, a tiny overlay map (a few tens of KB for the area in view), no base map.
- Family id 1299, product id 1, an embedded ~300-byte TYP with 4 line types
  (traveled / untraveled / traveled unpaved / untraveled unpaved).
- Transparent map with draw priority 10000, so it is drawn over the Garmin map without hiding it.
- Colours: untraveled red (#FF0000, 4 px solid), traveled light blue (#00E1FF, 4 px solid), unpaved = bitmap pattern.
- "Dots" vs "lines" differ only in the TYP (a circle bitmap instead of a solid line).

## Recipe that works
```
java -jar mkgmap.jar --style-file=style --family-id=1299 --product-id=1 \
  --transparent --draw-priority=10000 --gmapsupp --latin1 \
  --series-name="..." --mapname=NNNNNNNN --description="..." \
  --output-dir=out  style.txt  data.osm
```
Everything below was needed, each point cost an experiment:
1. **The TYP must be a `.txt` file.** mkgmap only compiles a text TYP when the input file ends with `.txt`. With
   `.typ` it silently copies the text into the `.img` as if it were a binary TYP and the device falls back to its
   default colours. Check: the compiled TYP in the `.img` starts with `GARMIN TYP`, not with `[_id]`.
2. **`--transparent` and `--draw-priority`.** Without them the map is opaque inside its area and hides the Garmin
   map. The values written in the TRE header (offset 0x3f..0x41) can be compared with a known-good file.
3. **Family id.** 1299 (as Wandrer) was kept; whether another id works too has not been tested.
4. **Line types 0x01 to 0x06** all work with a custom TYP; a TYP can define widths, a white border
   (`LineWidth=3`, `BorderWidth=1`) or a 32-pixel-wide bitmap pattern (dashes, as Wandrer uses for unpaved roads;
   no edge, and no night colours, for a bitmap line).
5. **POIs** work in the same overlay: a point type in the style (`[0x6411 resolution 16]`) plus a `[_point]`
   with a `DayXpm` icon (24x24, converted from our SVG icons) in the TYP. They sit over the Garmin map's own POIs.
6. Check a build is not empty before sending it to a device (an empty RGN is ~125 bytes); a style rule that
   matches nothing still produces a valid but empty `.img`. To read the sizes, stop at the end of the FAT (the
   blank-name entry gives where data starts) and only count the first entry of a sub-file: a large RGN spans
   several FAT entries and the next ones have size 0.

## Display notes (Forerunner 955)
- Use the "Classic" map theme; the "high contrast" theme does not show these overlays.
- Map > Information: enable the overlay next to the Garmin map; one `gmapsupp.img` at a time.
- Orange (the Wahoo default) is hard to tell from the Garmin base map's own orange roads, and red from the track
  the watch draws during an activity. Cyan with a white edge stands out best for the unvisited roads (first magenta;
  cyan was preferred after a test on the watch, October 2026), magenta is used for the visited ones. Widths are whole pixels.
- Draw order: a lower line type is drawn OVER a higher one (as major roads over minor ones). The boundaries, a wide
  band meant to show around the roads, therefore have the highest type (0x07); with 0x02 they covered the roads.

## To test later
Routing with the base map, contour lines, other family ids, other devices.

## Command line
```
python3 scripts/build_garmin.py --mkgmap /path/to/mkgmap.jar -o out/gmapsupp.img export1.kmz [export2.kmz ...]
```
Same options as `kmz_to_osm.py` for the exports (`--radius-m`, `--unpaved-always`, `--paved-always`, `--simplify-m`,
`--no-merge`), `--no-boundaries`, `--area LAT,LON,KM` (only the roads within KM km of a point, e.g.
`48.136,11.555,5`) or `--bbox W,S,E,N` (only this rectangle), and `--keep-osm FILE`. Visited roads are always drawn.
The Wandrer and Squadrats lines are cut into tiles of about 11 km like the base map (`wahoondrer/garmin/tiling.py`):
the four large exports of a 230 x 120 km area went over mkgmap's 16 MB per tile in one piece.
Default look: unvisited cyan (#00E1FF) 4 px with white edge, visited magenta (#FF00FF) 4 px, unpaved roads the same colour
dashed (8 px drawn, 8 px empty), boundaries a purple (#B000FF) 8 px band under the roads. Unpaved roads have their own entries in the look
(`unvisited_unpaved`, `visited_unpaved`); `"dash": [drawn, empty]` must add up to a divisor of 32 px, and
`"dash": null` gives a solid line instead (for instance thinner, for a small watch screen).
`--look look.json` changes it; only what differs is needed, widths are whole pixels, and at night only the colour
can differ from the day look:
```
{"day": {"unvisited": {"color": "#FF00AA", "width": 3}, "visited": {"color": "#00FFFF"}},
 "night": {"unvisited": {"color": "#FF77CC"}}}
```
The build refuses an empty map, a missing TYP or a TYP left as text. Tests: `MKGMAP_JAR=/path/mkgmap.jar` also runs
the tests that call mkgmap (skipped otherwise).
Seen on the Forerunner 955 (Munich walk export): a 1 px boundary is almost invisible (hence 2 px); 16+16 px
dashes are drawn but hard to read on the small screen; 6+2 px leave too little gap, 6+10 px dashes are too short:
half and half works best with long enough dashes (hence 8+8 px, accepted). A 2 px boundary drawn over a road hides
in it: an 8 px band under the roads shows on both sides (accepted). Not checked yet: the night colours (4-colour
list in the TYP line).

## POIs
```
python3 scripts/build_garmin.py ... --pois --osm oberbayern-latest.osm.pbf [--osm other.osm.pbf] [--poi-rules FILE]
python3 scripts/build_garmin.py ... --no-wandrer --pois --osm ... --area 48.136,11.555,5      # POIs only
```
Same rules as for Wahoo: `config/poi-rules.json`, or the effective file the web page writes from the user's choices
(the one passed to `build_tiles.py --poi-rules`). The Geofabrik extracts WMC downloaded are cut with osmium to the
square around the exports (or `--area`) and filtered to the POI tags; several regions are merged.
- One Garmin point type per distinct icon (0x6401...0x641f, so at most 31 icons); the icon is our SVG drawn by
  cairosvg at 24 px x the POI size in %, reduced to 32 colours, in a `[_point]` `DayXpm` of the TYP.
- Variants come first (`amenity=toilets & fee=yes`), the default icon only where no variant value matches, so a
  variant switched off is not drawn with the default icon. `only_if` / `hide_if` become `k=v` / `k!=v` terms
  (in mkgmap, `k!=v` is also true when the tag is missing).
- Zoom order kept: mkgmap resolution = Wahoo zoom + 7 (13 -> 20, 14 -> 21, 15 -> 22).
- No names: `--name-tag-list=maprboy:nolabel`, a tag that never exists. A POI drawn as an area (a station building)
  gets its icon in the middle (`--add-pois-to-areas`).
- The Wandrer roads and the POIs are two tiles of the same `gmapsupp.img`.
Needs osmium and cairosvg (both in `environment.yml`). Checked on the Forerunner 955 (Munich walk export + POIs).

## Squadrats
```
python3 scripts/build_garmin.py ... --squadrats squadrats.kml [--squadrats-levels big,small] [--squadrats-buffer 3]
                                    [--squadrats-big-scope all|band] [--squadrats-small-scope all|explored-big|band|both]
python3 scripts/build_garmin.py ... --no-wandrer --squadrats squadrats.kml --area 48.136,11.555,5   # Squadrats only
```
Same outlines as for Wahoo (`squadrats.py`): by default every unexplored square of the z8 tiles that touch the area
of the exports (or `--area`; then only the lines inside the circle's square); `band` keeps only a band of
`--squadrats-buffer` squares around the explored ones. They are written into the
same `.osm` as the Wandrer roads (`squadrat=big|small`) and drawn as line types 0x01 (big) and 0x02 (small), over
the roads. Default look: blue #1E50FF 2 px, big solid, small dashed 8+8 px (`squadrat_big`, `squadrat_small` in the
look); the red of the Wahoo theme would be read as unvisited roads. Checked on the Forerunner 955.

## OSM base map (maprboy-base.img)
```
python3 scripts/build_garmin_base.py --mkgmap /path/to/mkgmap.jar -o out/maprboy-base.img \
        --osm oberbayern-latest.osm.pbf [--osm other.osm.pbf] --area 48.136,11.555,20 [--poi-rules FILE] [--street-names]
```
An opaque map built from OSM with mkgmap's own default style (copied from the jar), so the device draws the
standard Garmin types with its own look. It is a separate file next to the overlay: copy both into the `Garmin`
folder, build the overlay without `--pois`, and in the activity profile (Navigation > Map > Map Information)
switch the Garmin maps off and "maprBoy base" on. Changes to the default style (`wahoondrer/garmin/base.py`):
- Our POIs replace the default style's POIs (places and peaks stay). They come 3 resolution steps later than on
  the overlay: on an Edge 530, resolution 22 showed from the 800 m scale, 24 from about 200 m. Icons are 65 %
  of the overlay's size, without names. Train stops (`railway=stop`) are left out, stations only.
- Labels: places (cities, villages, suburbs...), water, parks, gardens, forests and road numbers; street names
  only with `--street-names`. No default names (mkgmap labels fences "Fence", areas "Building"...).
- The area is cut with osmium into tiles of 0.15 x 0.1 degrees (about 11 km): a map tile holds at most 16 MB of
  drawing data (RGN) and mkgmap stops with "RGN section ... too big" above it. 10 km of central Munich took 2.4 MB.
- Family id 6324 (the overlay keeps 1299), map names 63240001, 63240002... one per tile.
Seen on the Edge 530 with the Garmin maps switched off: the base map shows, the overlay is drawn over it.
Not tested yet: keeping the Garmin map switched on under the base map (for routing).
