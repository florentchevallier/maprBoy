#!/usr/bin/env python3
"""Build a Garmin overlay map (gmapsupp.img): Wandrer roads from 1..n exports (KMZ/KML), Squadrats and/or POIs.

Usage:
    python3 scripts/build_garmin.py --mkgmap /path/to/mkgmap.jar -o out/gmapsupp.img export1.kmz [export2.kmz ...]
            [--device watch|edge] [--look look.json] [--radius-m 250] [--unpaved-always] [--paved-always]
            [--simplify-m 1.5] [--no-merge] [--no-boundaries] [--area LAT,LON,KM | --bbox W,S,E,N] [--keep-osm file.osm]
            [--name TEXT --family-id N --mapname NNNNNNNN]
            [--pois --osm region.osm.pbf [--osm other.osm.pbf] [--poi-rules rules.json]] [--no-wandrer]
            [--squadrats squadrats.kml [--squadrats-levels big,small] [--squadrats-buffer 3]
             [--squadrats-big-scope all|band] [--squadrats-small-scope all|explored-big|band|both]]

The map is transparent: it is drawn over the Garmin map already on the device, which stays visible.
Unvisited roads cyan 4 px with a white edge on a watch (--device watch, the default), orange with a black edge on
an Edge (--device edge); visited roads magenta 4 px, unpaved roads the same colour dashed (8 px drawn, 8 px empty),
boundaries a purple 8 px band under the roads (defaults; change them with --look, a JSON file, see docs/GARMIN.md). Copy the result to the device as
Garmin/gmapsupp.img (one such file at a time) and enable it in Map > Information.
Needs Java and the mkgmap jar (https://www.mkgmap.org.uk/). The exports are merged like for Wahoo
(most recent wins), then filtered around the traveled roads.

--pois adds the POIs of config/poi-rules.json (or of --poi-rules, e.g. the file the web page writes from
the user's choices), without names, taken from OSM extracts (--osm, e.g. the Geofabrik files WMC downloaded)
cut to the area of the exports (or to --area). Needs osmium and cairosvg (both in environment.yml).
--squadrats adds the outlines of the unexplored Squadrats squares (blue by default: big solid, small dashed),
as for Wahoo: by default every unexplored square of the area of the exports (or --area); "band" keeps only
those within --squadrats-buffer squares of the explored ones. The small squares only show when zoomed in.
--no-wandrer: no Wandrer roads, only POIs and/or Squadrats (give --area, or exports to set the area).
"""
import argparse
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.garmin import build, look, pois as garmin_pois, tiling, typ  # noqa: E402
from wahoondrer.process import Options  # noqa: E402
from wahoondrer.squadrats import BIG_SCOPES, SMALL_SCOPES  # noqa: E402

BBOX_MARGIN = 0.002   # degrees added around the exports for the POIs (about 200 m)


def parse_area(text):
    try:
        lat, lon, km = (float(v) for v in text.split(","))
    except ValueError:
        raise ValueError("--area wants LAT,LON,KM, e.g. 48.136,11.555,5")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180 and km > 0):
        raise ValueError("--area: latitude, longitude or radius out of range")
    return lat, lon, km


def check_ids(family_id, mapname):
    if not 1 <= family_id <= 65535:
        raise ValueError("--family-id: 1 to 65535")
    if not (mapname.isdigit() and len(mapname) == 8):
        raise ValueError("--mapname: 8 digits")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kmz", nargs="*", help="Wandrer KMZ/KML exports (any order)")
    ap.add_argument("-o", "--output", required=True, help="the .img to write, usually gmapsupp.img")
    ap.add_argument("--mkgmap", required=True, help="path of mkgmap.jar")
    ap.add_argument("--java", default="java")
    ap.add_argument("--device", choices=look.MODELS, default="watch",
                    help="the kind of Garmin: its default look (the track already ridden is not drawn alike)")
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
    ap.add_argument("--bbox", metavar="W,S,E,N",
                    help="only this rectangle (degrees), e.g. 11.2,47.9,11.9,48.3 (instead of --area)")
    ap.add_argument("--name", default="maprBoy Wandrer",
                    help="name shown in the device's map list (default: maprBoy Wandrer)")
    ap.add_argument("--family-id", type=int, default=typ.FAMILY_ID,
                    help=f"Garmin family id (default {typ.FAMILY_ID}); give each map on a device its own")
    ap.add_argument("--mapname", default=build.DEFAULT_MAPNAME, help="number of the first map tile (8 digits)")
    ap.add_argument("--keep-osm", metavar="FILE", help="also keep the intermediate Wandrer .osm here")
    ap.add_argument("--pois", action="store_true", help="add the POIs (needs --osm)")
    ap.add_argument("--osm", action="append", default=[], metavar="PBF",
                    help="OSM extract for the POIs (repeat for several regions)")
    ap.add_argument("--poi-rules", metavar="JSON", help="POI rules (default: config/poi-rules.json)")
    ap.add_argument("--no-wandrer", action="store_true", help="no Wandrer roads (POIs and/or Squadrats only)")
    ap.add_argument("--squadrats", metavar="KML", help="Squadrats KML export: outlines of the unexplored squares")
    ap.add_argument("--squadrats-levels", default="big,small", help="big (z14), small (z17), comma separated")
    ap.add_argument("--squadrats-buffer", type=int, default=3,
                    help="band width in squares around the explored ones (default 3)")
    ap.add_argument("--squadrats-big-scope", choices=BIG_SCOPES, default="all",
                    help="big squares: every unexplored one of the area (default) or a band around the explored ones")
    ap.add_argument("--squadrats-small-scope", choices=SMALL_SCOPES, default="all",
                    help="small squares: every unexplored one inside an explored big square (default), "
                         "a band around the explored small squares, or both")
    args = ap.parse_args()

    try:
        if args.no_wandrer and not (args.pois or args.squadrats):
            raise ValueError("--no-wandrer leaves nothing to draw: add --pois or --squadrats")
        if not args.no_wandrer and not args.kmz:
            raise ValueError("give at least one Wandrer export (or --no-wandrer with --pois / --squadrats)")
        check_ids(args.family_id, args.mapname)
        if not args.kmz and not args.area and not args.bbox:
            raise ValueError("give --area or --bbox, or exports to set the area")
        if args.area and args.bbox:
            raise ValueError("give --area or --bbox, not both")
        area = parse_area(args.area) if args.area else None
        box = tiling.parse_bbox(args.bbox) if args.bbox else None
        chosen = look.load(args.look, args.device) if args.look else look.defaults(args.device)
        poi_list = garmin_pois.load(args.poi_rules) if args.pois else []
        opts = Options(radius_m=args.radius_m, unpaved_always=args.unpaved_always,
                       paved_always=args.paved_always, simplify_m=args.simplify_m, merge=not args.no_merge)
        with tempfile.TemporaryDirectory(prefix="maprboy-garmin-") as tmp:
            exps = build.read_exports(args.kmz) if args.kmz else []
            bbox = box or (build.area_bbox(area) if area else build.exports_bbox(exps))
            squadrats = None
            if args.squadrats:
                levels = [l.strip() for l in args.squadrats_levels.split(",") if l.strip()]
                squadrats = build.squadrat_lines(args.squadrats, bbox, levels, args.squadrats_buffer,
                                                 args.squadrats_small_scope, area,
                                                 big_scope=args.squadrats_big_scope)
                if box:                         # the lines run across whole z8 tiles: keep the rectangle only
                    squadrats = {k: build._clip_straight(v, box) for k, v in squadrats.items()}
            inputs = []
            if not args.no_wandrer or squadrats:
                osm = args.keep_osm or os.path.join(tmp, "wandrer.osm")
                build.wandrer_osm([] if args.no_wandrer else exps, osm, opts, boundaries=not args.no_boundaries,
                                  area=area, squadrats=squadrats)
                inputs.append(osm)
            poi_rules, poi_typ = "", []
            if poi_list:
                w, s, e, n = bbox
                if not area and not box:
                    w, s, e, n = w - BBOX_MARGIN, s - BBOX_MARGIN, e + BBOX_MARGIN, n + BBOX_MARGIN
                inputs.append(garmin_pois.extract(args.osm, (w, s, e, n), poi_list, os.path.join(tmp, "pois")))
                rules, icons = garmin_pois.plan(poi_list)
                poi_rules, poi_typ = garmin_pois.points_file(rules), garmin_pois.typ_points(icons)
                print(f"POIs: {len(poi_list)} kinds, {len(icons)} icons")
            build.build_img(inputs, args.output, args.mkgmap, chosen, java=args.java,
                            poi_rules=poi_rules, poi_typ=poi_typ, family_id=args.family_id,
                            mapname=args.mapname, description=args.name,
                            split_bbox=bbox if (not args.no_wandrer or squadrats) else None)
    except (build.GarminError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
