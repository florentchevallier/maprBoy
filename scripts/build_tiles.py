#!/usr/bin/env python3
"""Build Wahoo z8 map tiles: classic WMC + WMC with the Wandrer layer.

Phase 1 (classic): a normal WMC run on the requested tiles (--xy). WMC works out
  and downloads the needed OSM regions itself (Bavaria, Austria...). Result:
  all tiles, without Wandrer.
Phase 2 (Wandrer): only for the tiles touched by the exports (the
  <osm>.tiles.json file written by kmz_to_osm.py), merge and map creation are
  redone with the Wandrer pbf added.

Output (--out):
  wmc_classique/maps/...   all tiles (POIs, no Wandrer)
  wmc_wandrer/maps/...     ONLY the modified tiles + theme
  README.txt               files to copy to the Bolt, one by one
  size_report.txt          size of each tile with / without Wandrer

Usage (WMC installed and importable; Java + osmium required):
  python3 scripts/build_tiles.py --xy 135/88,135/89,136/88,136/89 \\
          --wandrer wandrer.osm [--plan] [--skip-classic]
--plan: print the tiles and the OSM regions needed, produce nothing.
--contour: also add contour lines (WMC option; needs the phyghtmap program and a free
  USGS EarthExplorer account, see README). --srtm1: use SRTM 1 arc-second data for them.
--setup-contour: ask once for the USGS login and store it for WMC (run it in a terminal).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

from wahoomc.constants import USER_CONFIG_DIR, USER_OUTPUT_DIR, USER_WAHOO_MC  # noqa: E402
from wahoomc.downloader import download_tooling  # noqa: E402
from wahoomc.input import InputData  # noqa: E402
from wahoomc.osm_data import XYOsmData  # noqa: E402
from wahoomc.osm_maps_functions import OsmMaps  # noqa: E402
from wahoomc.setup_functions import (  # noqa: E402
    adjustments_due_to_breaking_changes, ask_for_and_write_earthexplorer_credentials,
    check_installation_of_required_programs, copy_jsons_from_repo_to_user,
    initialize_work_directories, read_earthexplorer_credentials)


class FakeOsmData:
    """Minimal interface OsmMaps expects for a list of tiles."""

    def __init__(self, tiles, border_countries):
        self.force_processing = True
        self.country_name = "wahoondrer"
        self.tiles = tiles
        self.border_countries = border_countries


def sync_config():
    """Copy the Wandrer tag files and tags-to-keep.json from the repo to the
    WMC config folder (WMC's osmium filter drops every tag not listed there)."""
    dst = os.path.join(USER_CONFIG_DIR, "tag_wahoo_adjusted")
    os.makedirs(dst, exist_ok=True)
    src = os.path.join(REPO, "config")
    for name in os.listdir(src):
        if name.startswith("tag-wahoo") and name.endswith(".xml"):
            shutil.copy2(os.path.join(src, name), os.path.join(dst, name))
    shutil.copy2(os.path.join(src, "tags-to-keep.json"),
                 os.path.join(USER_CONFIG_DIR, "tags-to-keep.json"))


def to_pbf(osm_path):
    """Return (pbf path, tiles sidecar path); convert with osmium if needed."""
    if osm_path.endswith(".pbf"):
        pbf, base = osm_path, osm_path[:-4]
    else:
        pbf, base = osm_path + ".pbf", osm_path
    if not osm_path.endswith(".pbf") and (
            not os.path.isfile(pbf) or os.path.getmtime(pbf) < os.path.getmtime(osm_path)):
        print(f"Converting {osm_path} -> {pbf} (osmium)")
        subprocess.run(["osmium", "cat", osm_path, "-o", pbf, "--overwrite"], check=True)
    return os.path.abspath(pbf), base + ".tiles.json"


def check_contour_prerequisites():
    """phyghtmap installed + USGS login stored. Never asks a question when there is no terminal
    (the web interface runs this script without one: a question would hang it)."""
    if shutil.which("phyghtmap") is None:
        sys.exit("Contour lines need the phyghtmap program, which is not installed (see the README, "
                 "section 'Contour lines'). Untick the contour option or install it.")
    user, password = read_earthexplorer_credentials()
    if user and password:
        return
    if sys.stdin is not None and sys.stdin.isatty():
        ask_for_and_write_earthexplorer_credentials()
        return
    sys.exit("Contour lines need your free USGS EarthExplorer login, which is not stored yet. "
             "Run once in a terminal:  python3 scripts/build_tiles.py --xy 136/88 --setup-contour")


def copy_theme(dst_root):
    vtm_dst = os.path.join(dst_root, "maps", "vtm-elemnt")
    os.makedirs(vtm_dst, exist_ok=True)
    shutil.copy2(os.path.join(REPO, "themes", "vtm", "vtm-elemnt.xml"), vtm_dst)
    shutil.copytree(os.path.join(REPO, "themes", "vtm", "icons"),
                    os.path.join(vtm_dst, "icons"), dirs_exist_ok=True)


def prepare(xy, args):
    inp = InputData()
    inp.xy_coordinates = xy
    inp.tag_wahoo_xml = args.tags_classic
    inp.save_cruiser = True
    inp.process_border_countries = True
    inp.force_download = args.force_download
    data = XYOsmData(inp)
    data.process_input_of_the_tool()
    return data


def copy_tile(src_dir, x, y, dst_root):
    tdir = os.path.join(dst_root, "maps", "tiles", "8", str(x))
    os.makedirs(tdir, exist_ok=True)
    base = os.path.join(src_dir, str(x), str(y))
    for ext in (".map.lzma", ".map.lzma.17"):
        shutil.copy2(base + ext, os.path.join(tdir, f"{y}{ext}"))
    if os.path.isfile(base + ".map"):
        cdir = os.path.join(dst_root, "cruiser")
        os.makedirs(cdir, exist_ok=True)
        shutil.copy2(base + ".map", os.path.join(cdir, f"z8x{x}y{y}.map"))


def size(path):
    return os.path.getsize(path) if os.path.isfile(path) else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--xy", required=True, help="z8 tiles, e.g. 135/88,136/88")
    ap.add_argument("--wandrer", help="the .osm (or .osm.pbf) file written by kmz_to_osm.py")
    ap.add_argument("--out", default=os.path.join(USER_WAHOO_MC, "_output"))
    ap.add_argument("--tags-classic", default="tag-wahoo-poi.xml")
    ap.add_argument("--tags-wandrer", default="tag-wahoo-poi-wandrer.xml")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--skip-classic", action="store_true",
                    help="reuse the classic tiles already built in _tiles")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--contour", action="store_true", help="add contour lines (needs phyghtmap, USGS login)")
    ap.add_argument("--srtm1", action="store_true", help="with --contour: use SRTM 1 arc-second data")
    ap.add_argument("--setup-contour", action="store_true",
                    help="ask for the USGS EarthExplorer login once (in a terminal) and exit")
    args = ap.parse_args()
    if args.setup_contour:
        if shutil.which("phyghtmap") is None:
            print("Note: phyghtmap is not installed yet (see the README).")
        ask_for_and_write_earthexplorer_credentials()
        print("Login stored for WMC.")
        return

    initialize_work_directories()
    adjustments_due_to_breaking_changes()
    download_tooling()
    check_installation_of_required_programs()
    # WMC's init helper asks "Overwrite? (y/n)" for every existing file, so
    # only call it when the default files are missing from the user config.
    if not os.path.isfile(os.path.join(
            USER_CONFIG_DIR, "tag_wahoo_adjusted", "tag-wahoo-poi.xml")):
        copy_jsons_from_repo_to_user("tag_wahoo_adjusted")
    if not os.path.isfile(os.path.join(USER_CONFIG_DIR, "tags-to-keep.json")):
        copy_jsons_from_repo_to_user(".", "tags-to-keep.json")
    sync_config()

    if args.contour and not args.plan:
        check_contour_prerequisites()
    data = prepare(args.xy, args)
    print("\nTiles and OSM regions:")
    for t in data.tiles:
        print(f"  {t['x']}/{t['y']} : {', '.join(t['countries'])}")
    if args.plan:
        # machine-readable copy for the web interface (one line, read by wahoondrer/gui/jobs.py)
        print("PLAN-JSON " + json.dumps({
            "tiles": [{"x": t["x"], "y": t["y"], "countries": list(t["countries"])} for t in data.tiles],
            "regions": sorted(data.border_countries)}))
        return

    out_c = os.path.join(args.out, "wmc_classique")
    out_w = os.path.join(args.out, "wmc_wandrer")

    # --- contour lines (one file per tile, reused when already computed) ---
    if args.contour:
        print("\n=== Contour lines (this can take a long time the first time) ===")
        OsmMaps(data).generate_elevation(args.srtm1)

    # --- Phase 1: classic WMC ---
    if not args.skip_classic:
        print("\n=== PHASE 1: classic WMC ===")
        data.get_downloader().download_files_if_needed()
        maps = OsmMaps(data)
        maps.filter_tags_from_country_osm_pbf_files()
        maps.generate_land()
        maps.generate_sea()
        maps.split_filtered_country_files_to_tiles()
        maps.merge_splitted_tiles_with_land_and_sea(True, args.contour, False)
        maps.create_map_files(True, args.tags_classic, False, False)
    if os.path.isdir(args.out):
        for d in (out_c, out_w):
            shutil.rmtree(d, ignore_errors=True)
    for t in data.tiles:
        copy_tile(USER_OUTPUT_DIR, t["x"], t["y"], out_c)

    # --- Phase 2: Wandrer ---
    report = []
    changed = []
    if args.wandrer:
        pbf, sidecar = to_pbf(args.wandrer)
        with open(sidecar, encoding="utf-8") as f:
            wtiles = {tuple(t) for t in json.load(f)["tiles"]}
        sel = []
        for t in data.tiles:
            if (t["x"], t["y"]) in wtiles:
                t2 = dict(t)
                t2["countries"] = list(t["countries"]) + ["wandrer"]
                sel.append(t2)
        print(f"\n=== PHASE 2: Wandrer on {len(sel)} tile(s) ===")
        if sel:
            maps = OsmMaps(FakeOsmData(sel, {"wandrer": {"map_file": pbf}}))
            maps.filter_tags_from_country_osm_pbf_files()
            maps.split_filtered_country_files_to_tiles()
            maps.merge_splitted_tiles_with_land_and_sea(True, args.contour, False)
            maps.create_map_files(True, args.tags_wandrer, False, False)
            for t in sel:
                copy_tile(USER_OUTPUT_DIR, t["x"], t["y"], out_w)
                changed.append((t["x"], t["y"]))
                c = size(os.path.join(out_c, "maps", "tiles", "8", str(t["x"]), f"{t['y']}.map.lzma"))
                w = size(os.path.join(out_w, "maps", "tiles", "8", str(t["x"]), f"{t['y']}.map.lzma"))
                report.append((t["x"], t["y"], c, w))
        # Bolt theme: always goes with the Wandrer tiles
        copy_theme(out_w)
    if args.contour:                  # contour lines are in the classic tiles too: they need the theme
        copy_theme(out_c)

    write_docs(args.out, data.tiles, changed, report, args.contour)
    print(f"\nDone. Output folder: {args.out}")


def mo(n):
    return f"{n / 1e6:.1f} Mo"


def write_docs(out, tiles, changed, report, contour=False):
    lines = ["Tile sizes (.map.lzma)", ""]
    lines.append(f"{'tile':10}{'classic':>12}{'wandrer':>12}{'change':>10}")
    for x, y, c, w in report:
        pct = f"{100 * (w - c) / c:+.0f} %" if c else "-"
        lines.append(f"{x}/{y:<7}{mo(c):>12}{mo(w):>12}{pct:>10}")
    with open(os.path.join(out, "size_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n" + "\n".join(lines))

    L = []
    L.append("COPYING TO THE WAHOO BOLT - read before copying")
    L.append("")
    L.append("1. BACKUP: first copy the Bolt's maps/tiles/8 folder to your computer.")
    L.append("2. Copy FILES one by one (or one tile folder x at a time). Do not replace a")
    L.append("   whole folder: the Bolt may then delete neighbouring tiles that are not here.")
    L.append("")
    L.append("A. Tiles with Wandrer  (wmc_wandrer/maps/tiles/8/...)  -> Bolt: maps/tiles/8/...")
    for x, y in changed:
        L.append(f"   {x}/{y}.map.lzma  and  {x}/{y}.map.lzma.17")
    L.append("")
    L.append("B. Theme  (wmc_wandrer/maps/vtm-elemnt/)  -> Bolt: maps/vtm-elemnt/")
    L.append("   vtm-elemnt.xml (directly in that folder, NOT in a subfolder).")
    L.append("   icons/: only needed if the Bolt has none yet; do not overwrite icons that already work.")
    L.append("")
    L.append("C. Other classic WMC tiles (wmc_classique/maps/tiles/8/...): optional,")
    L.append("   only for tiles that are missing on the Bolt.")
    if contour:
        L.append("   These tiles contain contour lines: copy the theme from wmc_classique/maps/vtm-elemnt/")
        L.append("   as in B if you use them (the theme draws the contour lines).")
    L.append("")
    L.append("Cruiser (desktop preview): uncompressed .map files are in cruiser/.")
    L.append("Then restart the Bolt.")
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
