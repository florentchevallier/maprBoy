# Devices — what has been tested, and what we know

This page gathers what was learned on each device: where the files go, what works, what breaks, and what is
still unknown. It is the reference before writing per-device instructions in the README. Recipes for building
the maps are in [PIPELINE.md](PIPELINE.md) (Wahoo) and [GARMIN.md](GARMIN.md) (Garmin).

| Device | Status | Map files | Theme |
|---|---|---|---|
| Wahoo ELEMNT BOLT v2 | in daily use | `maps/tiles/8/<x>/<y>.map.lzma` + `.map.lzma.17` | `maps/vtm-elemnt/` |
| Wahoo ELEMNT ROAM v1 | tested once (borrowed device) | `maps/tiles/8/<x>/<y>.map.gz` | `maps/vtm-elemnt/` + switch file |
| Garmin Forerunner 955 | tested (overlay, base map) | `GARMIN/*.img` | TYP inside the `.img` |
| Garmin Edge 830 | tested (overlay) | `Garmin/*.img` | TYP inside the `.img` |
| Garmin Edge 530 | tested (overlay) | `Garmin/gmapsupp.img` | TYP inside the `.img` |

---

## Wahoo: common points

- Tiles are **zoom-8 slippy-map tiles** (`x/y`), each a mapsforge v3 binary map. WahooMapsCreator writes them
  LZMA-compressed (`<y>.map.lzma`) with an empty marker file `<y>.map.lzma.17`.
- The VTM theme and its icons live in `maps/vtm-elemnt/` (`vtm-elemnt.xml`, `icons/*.svg`). The theme has one
  style layer per device family (BOLT, ROAM...), chosen by the device itself.
- Copy **file by file**, never a whole folder: replacing a folder can remove the tiles already in it.
  Back up `maps/tiles/8` first.
- After changing the theme, empty `maps/temp` (a cache), then restart the device.
- The small `<y>_<place>.txt` files maprBoy writes next to the tiles are for the user only; the device ignores them.
- **No `highway=*` tag on the Wandrer roads.** A tile whose Wandrer roads were tagged `highway=wandrer` showed a
  blank map on the ROAM v1, so the roads carry only `wandrer=<state>` on every device.

### Which tile format?

The page offers ".map.lzma devices (default)" and ".map.gz devices". The user checks one numbered folder in
`maps/tiles/8/` on the device: files ending in `.map.lzma` → default; `.map.gz` → choose `.map.gz`.
maprBoy then writes a gzip of the same map next to the normal output (`Output/wahoondrer_maps_gz/`).

### Copy with adb (from the page)

Tested on the BOLT v2. adb must be installed (macOS: `brew install android-platform-tools`).

- **Authorising the device:** USB unplugged, press the **power (on/off) button** 3 or 4 times quickly, plug in again.
  `adb devices` then shows `device` instead of `unauthorized`. The three-button method described for WahooMapsCreator
  did not work (it restarted the device). Android File Transfer can stay open.
- The device storage is `/sdcard/`, laid out like `Output/wahoondrer_maps/` (`maps/tiles/8/...`, `maps/vtm-elemnt/...`).
- The copy (`python3 -m wahoondrer.devcopy`) skips files already up to date (size, then md5), saves the files it
  replaces in `Output/backup/<date>/`, pushes each file to a temporary name then renames it, and empties `maps/temp`.
- **A restart is needed** to see the new maps. The `BMapManager.PURGE` / `RELOAD_MAP` broadcasts mentioned by
  WahooMapsCreator were tried: the map stayed blank until the restart, so the page offers a Restart button instead.

---

## Wahoo ELEMNT BOLT v2

- Tiles `.map.lzma` (+ `.map.lzma.17`), theme in `maps/vtm-elemnt/`. Everything maprBoy makes was checked on it:
  Wandrer roads (unvisited, visited, unpaved), Squadrats, POIs and their colour variants, contour lines.
- Contour lines are readable; 25 m spacing is enough for a bike computer.
- Dark mode: the base map is hard to read, the Wandrer lines stay very readable; the page has a separate dark look.
- Tile size: Wandrer roads add little (for example 24.5 MB instead of 23.9 MB for one tile with visited roads).

## Wahoo ELEMNT ROAM v1

Tested in October 2026 on a borrowed device, firmware **WR24-17071**.

- Tiles are `maps/tiles/8/<x>/<y>.map.gz`: the same mapsforge v3 binary as the `.map.lzma` tiles, only
  gzip-compressed (file name inside the gzip: `<y>.map`). No `.17` marker. The factory tiles held OSM data from 2015.
- VTM rendering is switched on by an **empty file `cfg_BHomeActivity_VtmMaps` at the device root** (next to `maps`
  and `factory`). The theme in `maps/vtm-elemnt/` is then used, with its ROAM style layer (no building fills there).
  maprBoy writes this file into `Output/wahoondrer_maps_gz/`.
- A restart is enough to read new tiles; adb was not needed.
- Gave a **blank map**: `highway=wandrer` on the Wandrer roads (dropped for every device), and a theme variant using
  pure magenta `#FF00FF` (avoid it on this device).
- Not a problem: tiles of 26–28 MB gzipped (36–39 MB unpacked), 8-digit colours with transparency, dashed lines.
- The screen shows few colours (orange looks beige); POIs crowd the map at the 500 m zoom. Free space was about
  250 MB on that device: enough for a few tiles only.
- Open questions: WahooMapsCreator says the ROAM reads `.map.lzma`; this firmware did not. A test on a more recent
  firmware would tell whether it is a question of version.
- Ideas: ROAM colours, fewer POIs at wide zoom.

---

## Garmin: common points

Details and the build recipe are in [GARMIN.md](GARMIN.md).

- `.img` files built with mkgmap, in the `Garmin` folder. maprBoy's map is a **transparent overlay**: the Garmin map
  stays visible under it. Other names than `gmapsupp.img` work on the Forerunner 955 and the Edge 830 (one entry
  per file in the map list).
- In the map settings, enable the overlay next to the Garmin map.
- The file goes in the `Garmin` folder itself: copied into `Garmin/CustomMaps`, it is not read (Edge 530 and 830).
- The map theme is set per activity profile (Navigation > Map > Map Theme): use **Classic**.
- Default look: unvisited roads cyan `#00E1FF` with a white edge, visited roads magenta `#FF00FF`, unpaved roads
  dashed, boundaries a purple band under the roads, Squadrats blue. Visited roads are always drawn.

## Garmin Forerunner 955

- Use the **"Classic" map theme**: the "high contrast" theme does not show the overlay.
- Orange is hard to tell from the Garmin map's own roads, and red from the track drawn during an activity: hence
  cyan for the unvisited roads.
- Checked: Wandrer roads, boundaries (a 1 px line is almost invisible, an 8 px band under the roads works), POIs
  with our own icons over the Garmin POIs, Squadrats. Dashes of 8 + 8 px are readable on the small screen.
- Not checked yet: night colours, the small Squadrats at resolution 21.

## Garmin Edge 830

Firmware 9.75, with the same `gmapsupp.img` as on the Forerunner 955 (foot export, Wandrer roads, POIs, Squadrats).

- The overlay shows over the Garmin map: the Garmin map's own POIs (restaurants, bike parking...) stay visible
  next to ours.
- Works in day and night mode.
- The Garmin map draws paths as red dashed lines; our layers stay distinct from them: the small Squadrats squares
  as blue dashed outlines, the Wandrer boundaries as a purple band.

## Garmin Edge 530

Borrowed device, same generation as the Edge 830 without a touch screen.

- With the **"High Contrast"** map theme, our lines are drawn in grey (the TYP colours are not used) and Wandrer's
  own `.img` overlays do not show; with **"Classic"** our colours show. Our POIs show in both themes.
- Two other maps already on the device (`gmapsupp-alps.img`, `gmapsupp-germany.img`) changed nothing: the overlay
  showed the same with and without them.
- maprBoy's OSM base map (`maprboy-base.img`, see GARMIN.md) shows with the Garmin maps switched off in Map
  Information, and the overlay is drawn over it. POIs at resolution 22 showed from the 800 m scale, at 24 from
  about 200 m.
