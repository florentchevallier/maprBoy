#!/usr/bin/env python3
"""Build a Garmin overlay map (gmapsupp.img) from 1..n Wandrer exports (KMZ/KML).

Usage:
    python3 scripts/build_garmin.py --mkgmap /path/to/mkgmap.jar -o out/gmapsupp.img export1.kmz [export2.kmz ...]
            [--look look.json] [--radius-m 250] [--unpaved-always] [--paved-always]
            [--simplify-m 1.5] [--no-merge] [--no-boundaries] [--area LAT,LON,KM] [--keep-osm file.osm]

The map is transparent: it is drawn over the Garmin map already on the device, which stays visible.
Unvisited roads red 4 px with a white edge, visited roads cyan 4 px, unpaved roads the same colour dashed
(8 px drawn, 8 px empty), boundaries purple 2 px
(defaults; change them with --look, a JSON file, see docs/GARMIN.md). Copy the result to the device as
Garmin/gmapsupp.img (one such file at a time) and enable it in Map > Information.
Needs Java and the mkgmap jar (https://www.mkgmap.org.uk/). The exports are merged like for Wahoo
(most recent wins), then filtered around the traveled roads.
"""
import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.garmin import build, look  # noqa: E402
from wahoondrer.process import Options  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kmz", nargs="+", help="Wandrer KMZ/KML exports (any order)")
    ap.add_argument("-o", "--output", required=True, help="the .img to write, usually gmapsupp.img")
    ap.add_argument("--mkgmap", required=True, help="path of mkgmap.jar")
    ap.add_argument("--java", default="java")
    ap.add_argument("--look", metavar="JSON", help="colours, widths and edge (only what differs from the default)")
    ap.add_argument("--radius-m", type=float, default=250.0,
                    help="keep unvisited roads only within this radius (m) of traveled roads; 0 = keep all")
    ap.add_argument("--unpaved-always", action="store_true")
    ap.add_argument("--paved-always", action="store_true")
    ap.add_argument("--simplify-m", type=float, default=1.5)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--no-boundaries", action="store_true", help="do not draw the Wandrer boundaries")
    ap.add_argument("--area", metavar="LAT,LON,KM",
                    help="only the roads within KM km of this point, e.g. 48.136,11.555,5")
    ap.add_argument("--keep-osm", metavar="FILE", help="also keep the intermediate .osm here")
    args = ap.parse_args()

    try:
        area = None
        if args.area:
            try:
                lat, lon, km = (float(v) for v in args.area.split(","))
            except ValueError:
                raise ValueError("--area wants LAT,LON,KM, e.g. 48.136,11.555,5")
            if not (-90 <= lat <= 90 and -180 <= lon <= 180 and km > 0):
                raise ValueError("--area: latitude, longitude or radius out of range")
            area = (lat, lon, km)
        chosen = look.load(args.look) if args.look else look.defaults()
        opts = Options(radius_m=args.radius_m, unpaved_always=args.unpaved_always,
                       paved_always=args.paved_always, simplify_m=args.simplify_m, merge=not args.no_merge)
        with tempfile.TemporaryDirectory(prefix="maprboy-osm-") as tmp:
            osm = args.keep_osm or os.path.join(tmp, "wandrer.osm")
            build.wandrer_osm(args.kmz, osm, opts, boundaries=not args.no_boundaries, area=area)
            build.build_img(osm, args.output, args.mkgmap, chosen, java=args.java)
    except (build.GarminError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
