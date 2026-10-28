#!/usr/bin/env python3
"""Build a Garmin OSM base map (maprboy-base.img): mkgmap's default style, with our POIs instead of its POIs.

Usage:
    python3 scripts/build_garmin_base.py --mkgmap /path/to/mkgmap.jar -o out/maprboy-base.img \\
            --osm region.osm.pbf [--osm other.osm.pbf] (--area LAT,LON,KM | --bbox W,S,E,N | export1.kmz ...)
            [--poi-rules rules.json] [--street-names] [--name TEXT --family-id N --mapname NNNNNNNN]

An opaque map, separate from the Wandrer/Squadrats overlay (gmapsupp.img, scripts/build_garmin.py): copy
both into the Garmin folder of the device, and build the overlay WITHOUT --pois (the POIs are in the base map).
In the device's map settings (activity profile > Navigation > Map > Map Information) switch the Garmin maps
off and maprBoy base on. The area is the square around the circle of --area, the rectangle of --bbox, or
the area of the given Wandrer exports; it is cut into tiles of about 11 km (a map tile holds at most 16 MB of
drawing data).
Labels: places, water, parks, forests and road numbers; --street-names adds the street names.
POIs: config/poi-rules.json, or --poi-rules (e.g. the file the web page writes from the user's choices),
without the train stops (stations only), icons at 65 % of the overlay's size.
Needs Java, the mkgmap jar and osmium. See docs/GARMIN.md.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.garmin import base, build, pois as garmin_pois, tiling  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_garmin import check_ids, parse_area  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kmz", nargs="*", help="Wandrer exports: the base map covers their area (instead of --area)")
    ap.add_argument("-o", "--output", required=True, help="the .img to write, e.g. maprboy-base.img")
    ap.add_argument("--mkgmap", required=True, help="path of mkgmap.jar")
    ap.add_argument("--java", default="java")
    ap.add_argument("--osm", action="append", default=[], metavar="PBF", required=True,
                    help="OSM extract (repeat for several regions), e.g. the Geofabrik files WMC downloaded")
    ap.add_argument("--area", metavar="LAT,LON,KM", help="the square around this circle, e.g. 48.136,11.555,20")
    ap.add_argument("--bbox", metavar="W,S,E,N", help="this rectangle (degrees), e.g. 11.2,47.9,11.9,48.3")
    ap.add_argument("--poi-rules", metavar="JSON", help="POI rules (default: config/poi-rules.json)")
    ap.add_argument("--name", default="maprBoy base", help="name shown in the device's map list")
    ap.add_argument("--family-id", type=int, default=base.FAMILY_ID,
                    help=f"Garmin family id (default {base.FAMILY_ID}); give each map on a device its own")
    ap.add_argument("--mapname", default=base.MAPNAME, help="number of the first map tile (8 digits)")
    ap.add_argument("--street-names", action="store_true", help="also label the streets (off by default)")
    args = ap.parse_args()
    try:
        check_ids(args.family_id, args.mapname)
        if not args.kmz and not args.area and not args.bbox:
            raise ValueError("give --area or --bbox, or exports to set the area")
        if args.area and args.bbox:
            raise ValueError("give --area or --bbox, not both")
        if args.bbox:
            bbox = tiling.parse_bbox(args.bbox)
        elif args.area:
            bbox = build.area_bbox(parse_area(args.area))
        else:
            bbox = build.exports_bbox(build.read_exports(args.kmz))
        base.build_base(args.osm, bbox, args.output, args.mkgmap, garmin_pois.load(args.poi_rules),
                        street_names=args.street_names, java=args.java, family_id=args.family_id,
                        mapname=args.mapname, description=args.name)
    except (build.GarminError, garmin_pois.PoiError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
