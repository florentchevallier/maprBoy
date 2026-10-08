#!/usr/bin/env python3
"""Build Wahoo z8 map tiles: classic WMC + WMC with the Wandrer layer.

Phase 1 (classic): a normal WMC run on the requested tiles (--xy). WMC works out
  and downloads the needed OSM regions itself (Bavaria, Austria...). Result:
  all tiles, without Wandrer.
Phase 2 (Wandrer): only for the tiles touched by the exports (the
  <osm>.tiles.json file written by kmz_to_osm.py), merge and map creation are
  redone with the Wandrer pbf added.

Output (--out):
  wmc_classic/maps/...        all tiles (POIs, no Wandrer)
  wahoondrer_maps/maps/...    ONLY the modified tiles + theme
  README.txt               files to copy to the Bolt, one by one
  size_report.txt          size of each tile with / without Wandrer

Usage (WMC installed and importable; Java + osmium required):
  python3 scripts/build_tiles.py --xy 135/88,135/89,136/88,136/89 \\
          --wandrer wandrer.osm [--plan] [--skip-classic]
--plan: print the tiles and the OSM regions needed, produce nothing.
--contour: also add contour lines (needs pyhgtmap, see README; no account needed). Uses the free
  Viewfinder 3 arc-second elevation data (coarser and lighter than SRTM 1).
--contour-spacing: minor-medium-major line spacing in metres: 10-50-100, 25-75-150 (default) or 30-90-180.
  Changing it recomputes the contour lines of the tiles.
--contour-tile X/Y=none|SPACING: another spacing (or none) for one tile; repeat for several tiles.
--setup-contour: install pyhgtmap once, in its own small environment, and exit.
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import time
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from wahoondrer import contour as contour_lines  # noqa: E402
from wahoondrer import poi_config  # noqa: E402
from wahoondrer import progress  # noqa: E402
from wahoondrer import reuse  # noqa: E402
from wahoondrer import theme_style  # noqa: E402
from wahoondrer.tiles import label_file_name, split_covered  # noqa: E402

# Where config/ and themes/ are read from: the repository, or a private copy written for the user's POIs.
ASSETS = REPO

from wahoomc.constants import USER_CONFIG_DIR, USER_DL_DIR, USER_OUTPUT_DIR, USER_WAHOO_MC  # noqa: E402
from wahoomc.downloader import download_tooling  # noqa: E402
import wahoomc.downloader as wmc_downloader  # noqa: E402

wmc_downloader.download_url_to_file = progress.download      # WMC's downloads now show a progress bar
from wahoomc.input import InputData  # noqa: E402
from wahoomc.geofabrik import XYCombinationHasNoCountries, XYGeofabrik  # noqa: E402
from wahoomc.osm_data import XYOsmData  # noqa: E402
from wahoomc.osm_maps_functions import OsmMaps  # noqa: E402
from wahoomc.setup_functions import (  # noqa: E402
    adjustments_due_to_breaking_changes, check_installation_of_required_programs,
    copy_jsons_from_repo_to_user, initialize_work_directories)


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
    src = os.path.join(ASSETS, "config")
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


def use_poi_rules(path):
    """Write config/ and the theme for the user's POIs into a private copy and read them from there."""
    global ASSETS
    with open(path, encoding="utf-8") as f:
        pois = json.load(f)["pois"]
    dst = os.path.join(os.path.expanduser("~"), ".wahoondrer", "generated")
    try:
        ASSETS = str(poi_config.prepare_tree(dst, pois, REPO).root)
    except ValueError as exc:
        sys.exit(f"! POI settings: {exc}")
    print(f"POIs: {len(pois)} type(s) enabled ({', '.join(p['key'] + '=' + p['value'] for p in pois) or 'none'})")


def use_theme_look(look):
    """Draw the Wandrer roads as `look` says (see wahoondrer/theme_style.py), in the private copy of config/
    and the theme (the one made for the POIs if there is one, else a new one). The repository is not touched."""
    global ASSETS
    try:
        look = theme_style.clean(look)
        dst = os.path.join(os.path.expanduser("~"), ".wahoondrer", "generated")
        if ASSETS == REPO:
            if os.path.isdir(dst):
                shutil.rmtree(dst)
            for sub in ("config", os.path.join("themes", "vtm")):
                shutil.copytree(os.path.join(REPO, sub), os.path.join(dst, sub),
                                ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
            ASSETS = dst
        path = os.path.join(ASSETS, "themes", "vtm", "vtm-elemnt.xml")
        with open(path, encoding="utf-8", newline="") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(theme_style.apply(text, look))
    except ValueError as exc:
        sys.exit(f"! Look of the Wandrer roads: {exc}")
    print("Look of the Wandrer roads changed in the theme: " + "; ".join(
        f"{mode} {kind} {v['color']} {v['width']:g}{' outlined' if v['outline'] else ''}"
        for mode, kinds in look.items() for kind, v in kinds.items()))


def contour_choices(items):
    """['136/88=none', ...] -> {'136/88': 'none'}; stops with a clear message on a bad item."""
    out = {}
    for item in items:
        tile, _, value = item.partition("=")
        try:
            out[tile.strip()] = contour_lines.choice(value.strip())
        except ValueError as exc:
            sys.exit(f"! --contour-tile {item}: {exc}")
    return out


def check_contour_prerequisites():
    if not contour_lines.pyhgtmap_path():
        sys.exit("Contour lines need pyhgtmap. Install it once with:  python3 scripts/build_tiles.py --setup-contour")


def generate_contours(tiles, spacing=contour_lines.DEFAULT_PRESET, per_tile=None):
    """Contour lines of every tile (one .osm file per tile, reused when already complete and made
    with the same line spacing). per_tile: {"x/y": "none" | preset} overrides the default spacing."""
    per_tile = per_tile or {}
    exe = contour_lines.pyhgtmap_path()
    hgt_dir = os.path.join(USER_DL_DIR, "hgt")
    os.makedirs(hgt_dir, exist_ok=True)
    jobs = max(1, min(4, os.cpu_count() or 1))
    for t in tiles:
        tdir = os.path.join(USER_OUTPUT_DIR, str(t["x"]), str(t["y"]))
        os.makedirs(tdir, exist_ok=True)
        want = per_tile.get(f"{t['x']}/{t['y']}", spacing)
        if want == "none":
            contour_lines.park(tdir)
            print(f"  tile {t['x']}/{t['y']}: no contour lines (your choice)")
            continue
        contour_lines.unpark(tdir)
        spacing_t = want
        found = sorted(glob.glob(os.path.join(tdir, "elevation*.osm")))
        if len(found) == 1 and contour_lines.file_ok(found[0]):
            if contour_lines.read_marker(tdir) == spacing_t:
                print(f"  tile {t['x']}/{t['y']}: contour lines ({spacing_t} m) already computed, reused")
                continue
            print(f"  tile {t['x']}/{t['y']}: existing contour lines use another spacing "
                  f"({contour_lines.read_marker(tdir)}), computing them again with {spacing_t}")
        for f in found:                      # empty, unfinished or other-spacing leftovers
            os.remove(f)
        print(f"  tile {t['x']}/{t['y']}: computing contour lines {spacing_t} m (the elevation data is downloaded the first time)")
        cmd = contour_lines.build_command(exe, t, os.path.join(tdir, "elevation"), hgt_dir, jobs, spacing_t)
        result = subprocess.run(cmd, check=False)
        made = sorted(glob.glob(os.path.join(tdir, "elevation*.osm")))
        if result.returncode != 0 or not made:
            for f in made:
                os.remove(f)
            if result.returncode == 0:      # it ran, but there is no elevation data for this tile
                sys.exit(f"! pyhgtmap found no elevation data for tile {t['x']}/{t['y']} (open sea?). "
                         "Choose \"no contour lines\" for this tile (Pro: contour lines tile by tile) "
                         "or untick \"Add contour lines\".")
            sys.exit(f"! pyhgtmap failed for tile {t['x']}/{t['y']} (exit code {result.returncode}): "
                     "its own message is just above.")
        if not all(contour_lines.file_ok(f) for f in made):
            print(f"  ! tile {t['x']}/{t['y']}: almost no elevation data found (open sea?).")
        contour_lines.write_marker(tdir, spacing_t)


def file_hash(path):
    """SHA-256 of a file, or "" when it cannot be read."""
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def copy_theme(dst_root):
    vtm_dst = os.path.join(dst_root, "maps", "vtm-elemnt")
    os.makedirs(vtm_dst, exist_ok=True)
    shutil.copy2(os.path.join(ASSETS, "themes", "vtm", "vtm-elemnt.xml"), vtm_dst)
    shutil.copytree(os.path.join(ASSETS, "themes", "vtm", "icons"),
                    os.path.join(vtm_dst, "icons"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".*", "Thumbs.db", "ehthumbs.db", "desktop.ini"))


class TolerantXYOsmData(XYOsmData):
    """XYOsmData, except that a tile no OSM region covers (open sea) is left out and reported instead of
    stopping the whole run (WMC refuses the entire list as soon as one tile has no country)."""

    skipped = []

    def calc_tiles(self):
        geofabrik = XYGeofabrik(self.input_xy_coordinates)
        self.tiles, self.skipped = split_covered(geofabrik.wanted_maps, geofabrik.get_tiles_of_wanted_map_single,
                                                 XYCombinationHasNoCountries)
        if not self.tiles and self.skipped:        # nothing left to build: stop, with WMC's own message
            sys.exit(self.skipped[0][1])


def prepare(xy, args):
    inp = InputData()
    inp.xy_coordinates = xy
    inp.tag_wahoo_xml = args.tags_classic
    inp.save_cruiser = True
    inp.process_border_countries = True
    inp.force_download = args.force_download
    data = TolerantXYOsmData(inp)
    data.process_input_of_the_tool()
    return data


def parse_places(opl_text):
    """Names of the cities/towns in OPL text, biggest first (city before town, then population)."""
    import re
    def dec(v):
        return re.sub(r"%([0-9a-fA-F]+)%", lambda m: chr(int(m.group(1), 16)), v)
    found = []
    for line in opl_text.splitlines():
        if not line.startswith("n"):
            continue
        tags = {}
        for tok in line.split(" "):
            if tok.startswith("T") and len(tok) > 1:
                for kv in tok[1:].split(","):
                    k, _, v = kv.partition("=")
                    tags[dec(k)] = dec(v)
        rank = {"city": 0, "town": 1}.get(tags.get("place"))
        name = tags.get("name:en") or tags.get("name")
        if rank is None or not name:
            continue
        try:
            pop = int(tags.get("population", "0"))
        except ValueError:
            pop = 0
        found.append((rank, -pop, name))
    found.sort()
    out = []
    for rank, _, name in found:
        if name not in out:
            out.append(name)
    cities = [n for r, _, n in found if r == 0]
    return (list(dict.fromkeys(cities))[:3]) or out[:2]


def tile_label(src_dir, x, y):
    """Landmark text of a tile (e.g. 'Munich, Freising') from the place names WMC kept; '' if unknown."""
    import glob
    text = ""
    for f in sorted(glob.glob(os.path.join(src_dir, str(x), str(y), "split-*-names.osm.pbf"))):
        try:
            r = subprocess.run(["osmium", "cat", f, "-f", "opl", "-o", "-"],
                               capture_output=True, text=True, timeout=120)
            text += r.stdout
        except (OSError, subprocess.SubprocessError):
            pass
    return ", ".join(parse_places(text))


def copy_tile(src_dir, x, y, dst_root, label=""):
    tdir = os.path.join(dst_root, "maps", "tiles", "8", str(x))
    os.makedirs(tdir, exist_ok=True)
    for old in glob.glob(os.path.join(tdir, f"{y} - *.txt")) + glob.glob(os.path.join(tdir, f"{y}_*.txt")):
        os.remove(old)                      # a previous run (other name or other label)
    name = label_file_name(y, label) if label else None
    if name:
        with open(os.path.join(tdir, name), "w", encoding="utf-8") as f:
            f.write(f"Tile 8/{x}/{y}: {label}\nInfo file only: the Bolt ignores it, so copying it is harmless (it helps to tell the tiles apart).\n")
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
    ap.add_argument("--xy", help="z8 tiles, e.g. 135/88,136/88")
    ap.add_argument("--wandrer", help="the .osm (or .osm.pbf) file written by kmz_to_osm.py")
    ap.add_argument("--out", default=os.path.join(USER_WAHOO_MC, "_output"))
    ap.add_argument("--tags-classic", default="tag-wahoo-poi.xml")
    ap.add_argument("--tags-wandrer", default="tag-wahoo-poi-wandrer.xml")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--skip-classic", action="store_true",
                    help="reuse the classic tiles of the last run (only the Wandrer part is redone); ignored with a message "
                         "if tiles, POIs, contour lines or OSM data changed since")
    ap.add_argument("--force-download", action="store_true")
    ap.add_argument("--contour", action="store_true", help="add contour lines (needs pyhgtmap, see --setup-contour)")
    ap.add_argument("--poi-rules", help="JSON file with the POIs to enable (written by the web page); "
                                        "default: config/poi-rules.json")
    ap.add_argument("--theme-look", metavar="JSON",
                    help="how the Wandrer roads look in the theme (written by the web page, see wahoondrer/theme_style.py); "
                         "default: the theme as it is")
    ap.add_argument("--contour-spacing", default=contour_lines.DEFAULT_PRESET, choices=list(contour_lines.PRESETS),
                    help="contour lines minor-medium-major spacing in metres (default %(default)s)")
    ap.add_argument("--contour-tile", action="append", default=[], metavar="X/Y=SPACING",
                    help="contour lines of one tile: 'none' or a spacing, e.g. 136/88=none (repeatable)")
    ap.add_argument("--setup-contour", action="store_true",
                    help="install pyhgtmap (once, in its own environment) and exit")
    args = ap.parse_args()
    if args.setup_contour:
        contour_lines.setup()
        return
    if not args.xy:
        ap.error("--xy is required")

    if args.poi_rules:
        use_poi_rules(args.poi_rules)
    if args.theme_look:
        try:
            custom_look = not theme_style.is_default(json.loads(args.theme_look))
        except ValueError as exc:
            sys.exit(f"! Look of the Wandrer roads: {exc}")
        if custom_look:
            use_theme_look(json.loads(args.theme_look))
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
    for w, _ in data.skipped:
        print(f"! Tile {w['x']}/{w['y']} left out: no OpenStreetMap region covers it (open sea?).")
    print("\nTiles and OSM regions:")
    for t in data.tiles:
        print(f"  {t['x']}/{t['y']} : {', '.join(t['countries'])}")
    if args.plan:
        # machine-readable copy for the web interface (one line, read by wahoondrer/gui/jobs.py)
        print("PLAN-JSON " + json.dumps({
            "tiles": [{"x": t["x"], "y": t["y"], "countries": list(t["countries"])} for t in data.tiles],
            "regions": sorted(data.border_countries)}))
        return

    out_c = os.path.join(args.out, "wmc_classic")
    out_w = os.path.join(args.out, "wahoondrer_maps")

    # --- contour lines (one file per tile, reused when already computed) ---
    if args.contour:
        print("\n=== Contour lines (this can take a long time the first time) ===")
        generate_contours(data.tiles, args.contour_spacing, contour_choices(args.contour_tile))

    # --- can the classic tiles of the last run be reused? ---
    state_path = os.path.join(USER_OUTPUT_DIR, "wahoondrer_phase1.json")
    config_files = [os.path.join(USER_CONFIG_DIR, "tag_wahoo_adjusted", args.tags_classic),
                    os.path.join(USER_CONFIG_DIR, "tags-to-keep.json")]
    sig = reuse.signature(data.tiles, args.contour, args.contour_spacing, contour_choices(args.contour_tile),
                          config_files, USER_DL_DIR)
    if args.skip_classic:
        try:
            with open(state_path, encoding="utf-8") as f:
                old_sig = json.load(f)
        except (OSError, ValueError):
            old_sig = None
        why = reuse.stale_reason(sig, old_sig)
        if not why:
            missing = [f"{t['x']}/{t['y']}" for t in data.tiles
                       if not size(os.path.join(USER_OUTPUT_DIR, str(t["x"]), f"{t['y']}.map.lzma"))]
            if missing:
                why = "the classic files are missing for " + ", ".join(missing)
        if why:
            print(f"\n! The classic tiles cannot be reused ({why}): they are rebuilt (slower, but up to date).")
            args.skip_classic = False

    steps = progress.Steps((0 if args.skip_classic else 7) + (4 if args.wandrer else 0))
    # --- Phase 1: classic WMC ---
    if not args.skip_classic:
        print("\n=== PHASE 1: classic WMC ===")
        print("  Missing OSM regions are downloaded automatically (a few minutes, up to several hundred MB;"
              " the lines below show it). Regions already on disk and younger than 14 days are kept.")
        steps.run("Download missing OSM data", data.get_downloader().download_files_if_needed)
        maps = OsmMaps(data)
        steps.run("Filter the OSM data", maps.filter_tags_from_country_osm_pbf_files)
        steps.run("Land", maps.generate_land)
        steps.run("Sea", maps.generate_sea)
        steps.run("Split into tiles", maps.split_filtered_country_files_to_tiles)
        steps.run("Merge with land and sea", maps.merge_splitted_tiles_with_land_and_sea, True, args.contour, False)
        steps.run("Create the map files", maps.create_map_files, True, args.tags_classic, False, False)
        with open(state_path, "w", encoding="utf-8") as f:       # what these classic tiles were built from
            json.dump(sig, f)
    labels = {(t["x"], t["y"]): tile_label(USER_OUTPUT_DIR, t["x"], t["y"]) for t in data.tiles}
    for t in data.tiles:
        copy_tile(USER_OUTPUT_DIR, t["x"], t["y"], out_c, labels[(t["x"], t["y"])])

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
        print(f"\n=== PHASE 2: Wandrer / Squadrats layer on {len(sel)} tile(s) ===")
        if sel:
            maps = OsmMaps(FakeOsmData(sel, {"wandrer": {"map_file": pbf}}))
            steps.run("Layer: filter", maps.filter_tags_from_country_osm_pbf_files)
            steps.run("Layer: split into tiles", maps.split_filtered_country_files_to_tiles)
            steps.run("Layer: merge", maps.merge_splitted_tiles_with_land_and_sea, True, args.contour, False)
            steps.run("Layer: create the map files", maps.create_map_files, True, args.tags_wandrer, False, False)
            for t in sel:
                copy_tile(USER_OUTPUT_DIR, t["x"], t["y"], out_w, labels[(t["x"], t["y"])])
                changed.append((t["x"], t["y"]))
                c = size(os.path.join(out_c, "maps", "tiles", "8", str(t["x"]), f"{t['y']}.map.lzma"))
                w = size(os.path.join(out_w, "maps", "tiles", "8", str(t["x"]), f"{t['y']}.map.lzma"))
                report.append((t["x"], t["y"], c, w))
        if not sel:
            steps.total -= 4
            print("! None of your tiles is covered by Wandrer or Squadrats data: no tile with that layer was made.")
    else:
        print("\n! No --wandrer file given: PHASE 2 (Wandrer / Squadrats layer) skipped, only the classic tiles were made.")
    # Tiles without Wandrer data (or without any Wandrer file): the classic tile has the same POIs and
    # contour lines, so it goes into wahoondrer_maps too: that folder is then the complete set for the Bolt.
    nowandrer = [(t["x"], t["y"]) for t in data.tiles if (t["x"], t["y"]) not in changed]
    for x, y in nowandrer:
        copy_tile(USER_OUTPUT_DIR, x, y, out_w, labels[(x, y)])
        changed.append((x, y))
    if nowandrer and args.wandrer:
        print("\n! No Wandrer or Squadrats data in these tiles, so they are plain tiles (no extra lines): "
              + ", ".join(f"{x}/{y}" for x, y in nowandrer))
    steps.done()
    copy_theme(out_w)                 # the Bolt theme always goes with the tiles of wahoondrer_maps
    if args.contour:                  # contour lines are in the classic tiles too: they need the theme
        copy_theme(out_c)

    write_docs(args.out, data.tiles, changed, report, args.contour, nowandrer)
    last_run = os.path.join(args.out, "last_run.json")
    theme_hash = file_hash(os.path.join(out_w, "maps", "vtm-elemnt", "vtm-elemnt.xml"))
    try:
        with open(last_run, encoding="utf-8") as f:
            before = json.load(f).get("theme_hash")
    except (OSError, ValueError, AttributeError):
        before = None
    with open(last_run, "w", encoding="utf-8") as f:   # read by the web interface
        json.dump({"time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                   "selected": [f"{t['x']}/{t['y']}" for t in data.tiles],
                   "built": [f"{x}/{y}" for x, y in changed],
                   "theme_hash": theme_hash,
                   "theme_new": bool(theme_hash) and theme_hash != before}, f)    # first run, or a theme that differs from the last run's
    info_path = os.path.join(args.out, "tiles_info.json")      # what each built tile contains (kept between runs)
    try:
        with open(info_path, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    choices = contour_choices(args.contour_tile)
    for x, y in changed:
        k = f"{x}/{y}"
        info[k] = {"contour": choices.get(k, args.contour_spacing) if args.contour else "none",
                   "wandrer": (x, y) not in nowandrer}
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f)
    print(f"\nDone. Output folder: {args.out}")


def mo(n):
    return f"{n / 1e6:.1f} MB"


def write_docs(out, tiles, changed, report, contour=False, nowandrer=()):
    lines = ["Tile sizes (.map.lzma)", ""]
    lines.append(f"{'tile':10}{'classic':>12}{'with layer':>12}{'change':>10}")
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
    L.append("   Files named like '88_Munich.txt' only tell you which city a tile covers. The Bolt ignores them: copy them or not.")
    L.append("")
    L.append("A. Tiles  (wahoondrer_maps/maps/tiles/8/...)  -> Bolt: maps/tiles/8/...")
    for x, y in changed:
        L.append(f"   {x}/{y}.map.lzma  and  {x}/{y}.map.lzma.17" + ("   (no Wandrer / Squadrats data here: plain tile)" if (x, y) in nowandrer else ""))
    L.append("")
    L.append("B. Theme  (wahoondrer_maps/maps/vtm-elemnt/)  -> Bolt: maps/vtm-elemnt/")
    L.append("   vtm-elemnt.xml (directly in that folder, NOT in a subfolder).")
    L.append("   icons/: only needed if the Bolt has none yet; do not overwrite icons that already work.")
    L.append("")
    L.append("C. Other classic WMC tiles (wmc_classic/maps/tiles/8/...): optional,")
    L.append("   only for tiles that are missing on the Bolt.")
    if contour:
        L.append("   These tiles contain contour lines: copy the theme from wmc_classic/maps/vtm-elemnt/")
        L.append("   as in B if you use them (the theme draws the contour lines).")
    L.append("")
    L.append("Cruiser (desktop preview): uncompressed .map files are in cruiser/.")
    L.append("Then restart the Bolt.")
    with open(os.path.join(out, "README.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
