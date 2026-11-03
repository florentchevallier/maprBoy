#!/usr/bin/env python3
"""Build a Hammerhead Karoo map: one extra mapsforge file with the Wandrer roads, the Squadrats squares and our
POIs, and our block of theme rules.

Usage:
    python3 scripts/kmz_to_osm.py -o wandrer.osm export.kmz [--squadrats squadrats.kml ...]   (the lines)
    python3 scripts/build_karoo.py -o OUT_DIR --bbox W,S,E,N [--name munich] [--theme offline_v15.xml]
            [--lines wandrer.osm] [--pois --osm region.osm.pbf [--osm other.osm.pbf] [--poi-rules rules.json]]
            [--with-visited] [--look JSON] [--sq-look JSON] [--osmosis osmosis]

OUT_DIR gets maprboy-<name>.map, our theme rules (maprboy-theme-block.xml) and, with --pois, the icons in
maprboy-icons/ (added to those already there: several maps can share the folder). The page copies them to the
device and adds the block to the device's theme (wahoondrer/karoo/device.py). By hand: with --theme (the theme
read from the device: adb pull /sdcard/offline_v15.xml, the number changes with the Karoo's software), the theme
with our block is also written to OUT_DIR, and then:
    adb push OUT_DIR/maprboy-<name>.map /sdcard/offline/maps/
    adb push OUT_DIR/offline_v15.xml /sdcard/
    adb push OUT_DIR/maprboy-icons /sdcard/
    adb reboot
Keep the original theme: pushing it back and removing the maprboy files gives the Karoo as it was.
--look / --sq-look: the look of the Wandrer roads / Squadrats squares, as the Wahoo settings hold it (JSON; the
light colours are used). Needs osmium and Osmosis with the mapsforge MapWriter plugin (both used by WMC).
See docs/DEVICES.md.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer import poi_config  # noqa: E402
from wahoondrer.garmin import tiling  # noqa: E402
from wahoondrer.karoo import mapfile, theme  # noqa: E402

BLOCK_FILE = "maprboy-theme-block.xml"
LAST_RUN = "last_run.json"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-o", "--out", required=True, help="output folder")
    ap.add_argument("--bbox", required=True, metavar="W,S,E,N", help="area of the map file (degrees)")
    ap.add_argument("--name", default="maprboy", help="map file name: maprboy-<name>.map")
    ap.add_argument("--theme", help="the theme read from the device (offline_vNN.xml): also write it with our block")
    ap.add_argument("--lines", help="Wandrer / Squadrats lines (OSM file written by kmz_to_osm.py)")
    ap.add_argument("--pois", action="store_true", help="add our POIs (from --osm extracts)")
    ap.add_argument("--osm", action="append", default=[], metavar="PBF", help="OSM extract for the POIs (repeat)")
    ap.add_argument("--poi-rules", metavar="JSON", help="POI rules (default: config/poi-rules.json)")
    ap.add_argument("--with-visited", action="store_true", help="also draw the visited roads (if --lines has them)")
    ap.add_argument("--look", help="look of the Wandrer roads (JSON, as in the Wahoo settings)")
    ap.add_argument("--sq-look", help="look of the Squadrats squares (JSON, as in the Wahoo settings)")
    ap.add_argument("--osmosis", default="osmosis")
    args = ap.parse_args()
    try:
        bbox = tiling.parse_bbox(args.bbox)
        if not args.lines and not args.pois:
            raise ValueError("nothing to draw: give --lines and/or --pois")
        if args.pois and not args.osm:
            raise ValueError("--pois needs OSM extracts (--osm)")
        look = json.loads(args.look) if args.look else None
        sq_look = json.loads(args.sq_look) if args.sq_look else None
        pois = poi_config.load_rules(args.poi_rules or poi_config.DEFAULT_RULES) if args.pois else []
        our_block = theme.block(pois, look, sq_look, visited=args.with_visited)
        os.makedirs(args.out, exist_ok=True)
        work = tempfile.mkdtemp(prefix="maprboy-karoo-")
        try:
            inputs = [args.lines] if args.lines else []
            if pois:
                poi_osm = os.path.join(work, "pois.osm")
                count = mapfile.extract_pois(args.osm, bbox, pois, poi_osm, work)
                print("POIs:", ", ".join(f"{v} {k}" for k, v in sorted(count.items(), key=lambda x: -x[1])) or "none")
                inputs.append(poi_osm)
            conf = os.path.join(work, "tag-maprboy.xml")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(mapfile.tag_mapping(pois, args.with_visited))
            mapfile.write_map(inputs, bbox, os.path.join(args.out, f"maprboy-{args.name}.map"), conf, work, args.osmosis)
            with open(os.path.join(args.out, LAST_RUN), "w", encoding="utf-8") as f:     # for the list of the page
                json.dump({"map": f"maprboy-{args.name}.map", "bbox": list(bbox)}, f)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        with open(os.path.join(args.out, BLOCK_FILE), "w", encoding="utf-8", newline="\n") as f:
            f.write(our_block)
        print("Wrote", os.path.join(args.out, BLOCK_FILE))
        if args.theme:
            with open(args.theme, encoding="utf-8") as f:
                device_theme = f.read()
            out_theme = os.path.join(args.out, os.path.basename(args.theme))
            with open(out_theme, "w", encoding="utf-8", newline="\n") as f:
                f.write(theme.with_block(device_theme, our_block))
            print("Wrote", out_theme)
        if pois:
            names = theme.write_icons(pois, os.path.join(args.out, theme.ICON_DIR))
            print(f"Wrote {len(names)} icons to", os.path.join(args.out, theme.ICON_DIR))
    except (ValueError, OSError, mapfile.MapError, theme.ThemeError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
