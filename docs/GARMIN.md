# Garmin maps (mkgmap) — findings and recipe

Status: experiments only, nothing wired into the app yet. Tested on a Forerunner 955.

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
   (`LineWidth=3`, `BorderWidth=1`) or a 32-pixel-wide bitmap pattern (dashes).
5. **POIs** work in the same overlay: a point type in the style (`[0x6411 resolution 16]`) plus a `[_point]`
   with a `DayXpm` icon (24x24, converted from our SVG icons) in the TYP. They sit over the Garmin map's own POIs.
6. Check a build is not empty before sending it to a device (the RGN sub-file should be more than a few hundred
   bytes); a style rule that matches nothing still produces a valid but empty `.img`.

## Display notes (Forerunner 955)
- Use the "Classic" map theme; the "high contrast" theme does not show these overlays.
- Map > Information: enable the overlay next to the Garmin map; one `gmapsupp.img` at a time.
- Orange (the Wahoo default) is hard to tell from the Garmin base map's own orange roads; red or magenta with a
  white edge stands out. Widths are whole pixels.

## To test later
Dashed pattern for unpaved roads, a full OSM base map instead of the overlay, Squadrats lines, contour lines,
other family ids, other devices (Edge).
