#!/usr/bin/env python3
"""Build a Garmin overlay map (gmapsupp.img): Wandrer roads from 1..n exports (KMZ/KML) and/or POIs.

Usage:
    python3 scripts/build_garmin.py --mkgmap /path/to/mkgmap.jar -o out/gmapsupp.img export1.kmz [export2.kmz ...]
            [--look look.json] [--radius-m 250] [--unpaved-always] [--paved-always]
            [--simplify-m 1.5] [--no-merge] [--no-boundaries] [--area LAT,LON,KM] [--keep-osm file.osm]
            [--pois --osm region.osm.pbf [--osm other.osm.pbf] [--poi-rules rules.json]] [--no-wandrer]

The map is transparent: it is drawn over the Garmin map already on the device, which stays visible.
Unvisited roads red 4 px with a white edge, visited roads cyan 4 px, unpaved roads the same colour dashed
(8 px drawn, 8 px empty), boundaries purple 2 px
(defaults; change them with --look, a JSON file, see docs/GARMIN.md). Copy the result to the device as
Garmin/gmapsupp.img (one such file at a time) and enable it in Map > Information.
Needs Java and the mkgmap jar (https://www.mkgmap.org.uk/). The exports are merged like for Wahoo
(most recent wins), then filtered around the traveled roads.

--pois adds the POIs of config/poi-rules.json (or of --poi-rules, e.g. the file the web page writes from
the user's choices), without names, taken from OSM extracts (--osm, e.g. the Geofabrik files WMC downloaded)
cut to the area of the exports (or to --area). Needs osmium and cairosvg (both in environment.yml).
--no-wandrer: POIs only (give --area, or exports to set the area).
"""
import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.garmin import build, look, pois as garmin_pois  # noqa: E402
from wahoondrer.process import Options  # noqa: E402

BBOX_MARGIN = 0.002   # degrees added around the exports for the POIs (about 200 m)


def parse_area(text):
    try:
        lat, lon, km = (float(v) for v in text.split(","))
    except ValueError:
        raise ValueError("--area wants LAT,LON,KM, e.g. 48.136,11.555,5")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and km > 0):
        raise ValueError("--area: latitude, longitude or radius out of range")
    return lat, lon, km


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kmz", nargs="*", help="Wandrer KMZ/KML exports (any order)")
    ap.add_argument("-o", "--output", required=True, help="the .img to write, usually gmapsupp.img")
    ap.add_argument("--mkgmap", required=True, help="path of mkgmap.jar")
    ap.add_argument("--java", default="java")
    ap.add_argument("--look", metavar="JSON", help="colours, widths, edge, dashes (only what differs from the default)")
    ap.add_argument("--radius-m", type=float, default=250.0,
                    help="keep unvisited roads only within this radius (m) of traveled roads; 0 = keep all")
    ap.add_argument("--unpaved-always", action="store_true")
    ap.add_argument("--paved-always", action="store_true")
    ap.add_argument("--simplify-m", type=float, default=1.5)
    ap.add_argument("--no-merge", action="store_true")
    ap.add_argument("--no-boundaries", action="store_true", help="do not draw the Wandrer boundaries")
    ap.add_argument("--area", metavar="LAT,LON,KM",
                    help="only what lies within KM km of this point, e.g. 48.136,11.555,5")
    ap.add_argument("--keep-osm", metavar="FILE", help="also keep the intermediate Wandrer .osm here")
    ap.add_argument("--pois", action="store_true", help="add the POIs (needs --osm)")
    ap.add_argument("--osm", action="append", default=[], metavar="PBF",
                    help="OSM extract for the POIs (repeat for several regions)")
    ap.add_argument("--poi-rules", metavar="JSON", help="POI rules (default: config/poi-rules.json)")
    ap.add_argument("--no-wandrer", action="store_true", help="no Wandrer roads (POIs only)")
    args = ap.parse_args()

    try:
        if args.no_wandrer and not args.pois:
            raise ValueError("--no-wandrer leaves nothing to draw: add --pois")
        if not args.no_wandrer and not args.kmz:
            raise ValueError("give at least one Wandrer export (or --no-wandrer --pois)")
        area = parse_area(args.area) if args.area else None
        chosen = look.load(args.look) if args.look else look.defaults()
        poi_list = garmin_pois.load(args.poi_rules) if args.pois else []
        opts = Options(radius_m=args.radius_m, unpaved_always=args.unpaved_always,
                       paved_always=args.paved_always, simplify_m=args.simplify_m, merge=not args.no_merge)
        with tempfile.TemporaryDirectory(prefix="maprboy-garmin-") as tmp:
            inputs, bbox = [], build.area_bbox(area) if area else None
            if not args.no_wandrer:
                osm = args.keep_osm or os.path.join(tmp, "wandrer.osm")
                stats = build.wandrer_osm(args.kmz, osm, opts, boundaries=not args.no_boundaries, area=area)
                inputs.append(osm)
                bbox = bbox or stats["bbox"]
            elif not bbox:
                if not args.kmz:
                    raise ValueError("POIs only: give --area, or exports to set the area")
                from wahoondrer.kmz import read_export
                bbox = build.exports_bbox([read_export(p) for p in args.kmz])
            poi_rules, poi_typ = "", []
            if poi_list:
                w, s, e, n = bbox
                if not area:
                    w, s, e, n = w - BBOX_MARGIN, s - BBOX_MARGIN, e + BBOX_MARGIN, n + BBOX_MARGIN
                inputs.append(garmin_pois.extract(args.osm, (w, s, e, n), poi_list, os.path.join(tmp, "pois")))
                rules, icons = garmin_pois.plan(poi_list)
                poi_rules, poi_typ = garmin_pois.points_file(rules), garmin_pois.typ_points(icons)
                print(f"POIs: {len(poi_list)} kinds, {len(icons)} icons")
            build.build_img(inputs, args.output, args.mkgmap, chosen, java=args.java,
                            poi_rules=poi_rules, poi_typ=poi_typ)
    except (build.GarminError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
