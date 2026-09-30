#!/usr/bin/env python3
"""Merge 1..n Wandrer exports (KMZ/KML) and write an OSM XML file.

Usage:
    python3 scripts/kmz_to_osm.py -o wandrer.osm export1.kmz export2.kmz ...
            [--with-traveled] [--radius-m 250] [--unpaved-always] [--paved-always]
            [--simplify-m 1.5] [--no-merge] [--no-boundaries]
            [--squadrats squadrats.kml] [--squadrats-levels big,small]
            [--squadrats-buffer 3]
            [--squadrats-small-scope explored-big|band|both]

Exports are ordered automatically by the timestamp stored in the KML (the most
recent wins), whatever the file names. A warning is printed if bike and foot
exports are mixed.
By default already-traveled roads are not written (they make the map heavier
and are not displayed); --with-traveled keeps them.
Unvisited roads are kept only within --radius-m (250 m) of traveled roads;
--radius-m 0 disables this filter.

--squadrats adds the outlines of the UNEXPLORED Squadrats squares (big = z14,
small = z17), only in a band of --squadrats-buffer squares around the explored
ones and only in the z8 tiles touched by the Wandrer exports. Small squares: by
default every unexplored small square inside an explored big square is outlined
(--squadrats-small-scope explored-big); "band" uses the buffer instead.

Also writes <output>.tiles.json (z8 tiles touched by the exports).
Next step:  osmium cat wandrer.osm -o wandrer.osm.pbf --overwrite
(build_tiles.py does this conversion itself).
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.kmz import read_export, merge_exports  # noqa: E402
from wahoondrer.osmxml import write_osm  # noqa: E402
from wahoondrer.process import Options, process  # noqa: E402
from wahoondrer.tiles import tiles_for_bbox  # noqa: E402
from wahoondrer.squadrats import SMALL_SCOPES, read_squadrats, unexplored_outlines  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kmz", nargs="+", help="KMZ/KML exports (any order)")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--with-traveled", action="store_true")
    ap.add_argument("--radius-m", type=float, default=250.0,
                    help="keep unvisited roads only within this radius (m) of "
                         "traveled roads; 0 = keep all (default 250)")
    ap.add_argument("--unpaved-always", action="store_true",
                    help="keep all unpaved roads, even outside the radius")
    ap.add_argument("--paved-always", action="store_true",
                    help="keep all paved (unvisited) roads, even outside the radius")
    ap.add_argument("--simplify-m", type=float, default=1.5,
                    help="simplification tolerance in m; 0 = none")
    ap.add_argument("--no-merge", action="store_true",
                    help="do not merge chained segments")
    ap.add_argument("--no-boundaries", action="store_true",
                    help="do not write the Wandrer achievement boundaries")
    ap.add_argument("--squadrats", metavar="KML",
                    help="Squadrats KML export: add outlines of unexplored squares")
    ap.add_argument("--squadrats-levels", default="big,small",
                    help="comma list of levels to draw: big (z14), small (z17)")
    ap.add_argument("--squadrats-buffer", type=int, default=3,
                    help="band width in squares around explored squares (default 3)")
    ap.add_argument("--squadrats-small-scope", choices=SMALL_SCOPES,
                    default="explored-big",
                    help="small squares to outline: every unexplored one inside an "
                         "explored big square (default), a band around explored "
                         "small squares, or both")
    args = ap.parse_args()

    exports = []
    for p in args.kmz:
        print(f"Reading {p} ...")
        exp = read_export(p)
        print(f"  {len(exp.segments)} segments, {len(exp.boundaries)} boundaries, "
              f"traveled={exp.has_traveled} untraveled={exp.has_untraveled} "
              f"activity={exp.activity or '?'} export={exp.timestamp or '?'}")
        exports.append(exp)

    activities = {e.activity for e in exports if e.activity}
    if len(activities) > 1:
        print(f"WARNING: mixed activities ({', '.join(sorted(activities))}).")

    segments, boundaries, report = merge_exports(exports)

    print("\nMerge (most recent wins):")
    for r in report:
        print(f"  {os.path.basename(r['export'])}: +{r['added']} new, "
              f"{r['state_changed']} state changes, {r['removed_stale']} stale "
              f"removed, +{r['new_boundaries']} boundaries")

    opts = Options(radius_m=args.radius_m, unpaved_always=args.unpaved_always,
                   paved_always=args.paved_always,
                   simplify_m=args.simplify_m, merge=not args.no_merge)
    segments, ps = process(segments, opts, args.with_traveled)
    print(f"\nProcessing: {ps['input']} segments -> "
          f"{ps.get('dropped_far', 0)} dropped (outside radius), "
          f"{ps['before_merge']} -> {ps['after_merge']} after merging; "
          f"{ps['nodes_before_simplify']} -> {ps['nodes_after_simplify']} points after simplification")
    tiles = sorted({t for e in exports for t in tiles_for_bbox(*e.bbox)})

    squadrats = None
    if args.squadrats:
        print(f"\nReading Squadrats {args.squadrats} ...")
        explored = read_squadrats(args.squadrats)
        squadrats = {}
        for level in [l.strip() for l in args.squadrats_levels.split(",") if l.strip()]:
            if level not in explored:
                print(f"  level {level}: not found in the file, skipped")
                continue
            lines = unexplored_outlines(explored[level], level,
                                        args.squadrats_buffer, set(tiles),
                                        parent_explored=explored.get("big"),
                                        scope=args.squadrats_small_scope)
            squadrats[level] = lines
            print(f"  {level}: {len(explored[level])} explored squares -> "
                  f"{len(lines)} outline ways")

    if args.no_boundaries:
        boundaries = {}
    stats = write_osm(args.output, segments, boundaries, args.with_traveled, squadrats)
    print(f"\nWrote {args.output}: {stats['ways']} ways, {stats['nodes']} nodes"
          f" ({stats['skipped_visited']} traveled segments skipped)")

    print("Touched z8 tiles:", ", ".join(f"{x}/{y}" for x, y in tiles))
    with open(args.output + ".tiles.json", "w", encoding="utf-8") as f:
        json.dump({"tiles": [list(t) for t in tiles]}, f)


if __name__ == "__main__":
    main()
