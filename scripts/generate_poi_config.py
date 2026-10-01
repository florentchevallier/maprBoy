#!/usr/bin/env python3
"""Generate the POI parts of the WMC/VTM configuration from config/poi-rules.json.

A POI must be declared in three places (see docs/PIPELINE.md): the tag-mapping XML
(MapWriter), config/tags-to-keep.json (osmium filter) and the VTM theme (drawing).
This script writes all three from one rules file, so they cannot get out of sync.
POIs with "enabled": false in the rules file are known to the web page but not generated.
(The web page itself does not use this script: build_tiles.py writes a private copy from the
user's choices, see wahoondrer/poi_config.py.)

Only the parts between the "generated:" markers are rewritten, everything else is left alone;
tags-to-keep.json is add-only.

Usage:
    python3 scripts/generate_poi_config.py            # rewrite the files
    python3 scripts/generate_poi_config.py --check    # only report, exit 1 if out of date
Standard library only.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wahoondrer import poi_config  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rules", default=str(poi_config.DEFAULT_RULES))
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if a file is out of date")
    args = ap.parse_args(argv)
    paths = poi_config.Paths()
    try:
        pois = poi_config.load_rules(args.rules, paths.icon_dir)
        stale = poi_config.generate(paths, pois, write=not args.check)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    for path in stale:
        print(("out of date: " if args.check else "updated: ") + str(path.relative_to(paths.root)))
    if not stale:
        print("everything is up to date")
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
