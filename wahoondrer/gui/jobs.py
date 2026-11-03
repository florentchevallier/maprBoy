"""Turn the settings into command lines, and run them one after the other."""
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time

from .. import contour, poi_config, theme_style
from . import native
from . import settings as settings_mod

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TILE_OK = set("0123456789/")


def format_duration(seconds):
    """1 h 05 min 09 s / 5 min 09 s / 42 s"""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min {sec:02d} s"
    return f"{m} min {sec:02d} s" if m else f"{sec} s"


class SettingsError(ValueError):
    """A problem the user can fix (shown in the page)."""


# What the build step imports (WahooMapsCreator and the libraries it needs, see environment.yml).
# Checked in the Python that runs this page, which is also the one that runs the build.
REQUIRED_MODULES = ("wahoomc", "osgeo", "shapely", "geojson", "requests")
ENV_NAME = "wahoondrer-env"
WMC_VERSION = "4.4.0"       # the WahooMapsCreator version maprBoy was tested with (same pin as requirements.txt and environment.yml)
MIN_PYTHON = (3, 10)        # WMC 4.4.0 declares Python 3.10 or newer


def environment_message(missing):
    if not missing:
        return ""
    return (f"Python module{'s' if len(missing) > 1 else ''} not found: {', '.join(missing)}. "
            f"The maps cannot be built with this Python ({sys.executable}). "
            f"Stop the page (Ctrl+C in the terminal), activate the {ENV_NAME} environment "
            f"(see \"Installation\" in the README), then start the page again.")


def version_tuple(text):
    """'4.10.1' -> (4, 10, 1); stops at the first part that is not a number ('4.5.0rc1' -> (4, 5, 0))."""
    parts = []
    for piece in re.split(r"[.\-+]", str(text)):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group()))
        if m.end() != len(piece):
            break
    return tuple(parts)


def installed_version(name):
    """Version of an installed package, None if it cannot be read."""
    try:
        return importlib.metadata.version(name)
    except Exception:           # PackageNotFoundError, or damaged metadata: not worth stopping for
        return None


def check_environment():
    """Is everything the build needs installed in the Python running this page?
    -> {"python", "version", "wmc_version", "missing": [module names],
        "message": what stops a build ("" if all is well), "notice": a remark that does not stop anything}"""
    missing = []
    for name in REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            found = False
        if not found:
            missing.append(name)
    wmc = installed_version("wahoomc") if "wahoomc" not in missing else None
    problems, notice = [], ""
    if missing:
        problems.append(environment_message(missing))
    if sys.version_info[:2] < MIN_PYTHON:
        problems.append(f"This Python is {platform.python_version()} ({sys.executable}), maprBoy needs "
                        f"{'.'.join(map(str, MIN_PYTHON))} or newer: use the {ENV_NAME} environment "
                        f"(see \"Installation\" in the README).")
    if wmc and version_tuple(wmc) < version_tuple(WMC_VERSION):
        problems.append(f"WahooMapsCreator {wmc} is installed in this Python ({sys.executable}), but maprBoy needs "
                        f"{WMC_VERSION}: in the {ENV_NAME} environment run  pip install wahoomc=={WMC_VERSION}  "
                        f"(see \"Installation\" in the README).")
    elif wmc and version_tuple(wmc) > version_tuple(WMC_VERSION):
        notice = (f"WahooMapsCreator {wmc} is installed; maprBoy was tested with {WMC_VERSION}. "
                  f"It may work, but this is not guaranteed.")
    return {"python": sys.executable, "version": platform.python_version(), "wmc_version": wmc,
            "missing": missing, "message": " ".join(problems), "notice": notice}


def require_environment():
    """Refuse to start a build that would stop at its first import."""
    message = check_environment()["message"]
    if message:
        raise SettingsError(message)


# Failures a newcomer cannot read in a traceback: (pattern, short text for the notification, longer hint for the log).
HINTS = (
    (re.compile(r"No module named '?([\w.]+)"),
     "Python module '{0}' not found: the page was probably started outside the " + ENV_NAME + " environment.",
     "Hint: the Python module '{0}' is not installed in " + sys.executable + ". The page was probably started outside the "
     + ENV_NAME + " environment: stop it (Ctrl+C), activate that environment (see \"Installation\" in the README), "
     "start the page again."),
    (re.compile(r"cannot import name '\w+' from 'wahoomc"),
     "WahooMapsCreator is too old: maprBoy needs " + WMC_VERSION + ".",
     "Hint: the installed WahooMapsCreator is older than the " + WMC_VERSION + " that maprBoy needs. In the " + ENV_NAME
     + " environment (Python 3.10 or newer) run:  pip install wahoomc==" + WMC_VERSION + "  then start the page again."),
    (re.compile(r"XY coordinate '(\d+/\d+)' consists of no countries"),
     "Tile {0} has no land: no OpenStreetMap region covers it.",
     "Hint: tile {0} is in the open sea, no OpenStreetMap region covers it, so there is nothing to build. "
     "Take it out of the tiles (on the map) or choose a tile that touches land."),
    (re.compile(r"No space left on device|not enough space on the disk", re.I),
     "The disk is full.",
     "Hint: the disk is full. Delete OSM regions (Map data section) or old tiles, then run again: the finished steps are not redone."),
)


def hint_for(lines):
    """(short, long) hint for a failed run, from the end of its log; None if the cause is not a known one."""
    for line in reversed(list(lines)[-60:]):
        for pattern, short, long in HINTS:
            m = pattern.search(line)
            if m:
                return short.format(*m.groups()), long.format(*m.groups())
    return None


def parse_tiles(tiles):
    out = []
    for t in tiles:
        t = t.strip()
        if not t:
            continue
        parts = t.split("/")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or not set(t) <= TILE_OK:
            raise SettingsError(f"Invalid tile '{t}' (expected x/y, e.g. 136/88)")
        x, y = int(parts[0]), int(parts[1])
        if not (0 <= x < 256 and 0 <= y < 256):
            raise SettingsError(f"Tile {t} is outside the z8 grid (0-255)")
        out.append(f"{x}/{y}")
    return sorted(set(out), key=lambda s: tuple(map(int, s.split("/"))))


def _inside(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def look_args(w, q=None):
    """['--theme-look', json] when the look of the Wandrer roads is not the default one, and
    ['--theme-squadrats', json] when the look of the Squadrats outlines is not; else []."""
    out = []
    try:
        look = theme_style.clean(w.get("look"))
    except ValueError as exc:
        raise SettingsError(f"Look of the Wandrer roads: {exc}")
    if not theme_style.is_default(look):
        out += ["--theme-look", json.dumps(look)]
    try:
        sq = theme_style.sq_clean((q or {}).get("look"))
    except ValueError as exc:
        raise SettingsError(f"Look of the Squadrats: {exc}")
    if not theme_style.sq_is_default(sq):
        out += ["--theme-squadrats", json.dumps(sq)]
    return out


def poi_args(s):
    """['--poi-rules', file] when the user changed POIs (the effective list is written next to the settings),
    else [] (the defaults of config/poi-rules.json are used)."""
    user = s.get("pois") or {}
    if not poi_config.has_user_choices(user):
        return []
    try:
        pois = [p for p in poi_config.effective_pois(poi_config.read_rules(), user) if p.get("enabled", True)]
        poi_config.validate(pois, poi_config.Paths().icon_dir)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SettingsError(f"POI settings: {exc}")
    path = os.path.join(os.path.dirname(settings_mod.SETTINGS_PATH), "poi-rules.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pois": pois}, f, indent=2)
    return ["--poi-rules", path]


def _wandrer_covers(exports, tiles):
    """True if one export may touch one of the tiles (rough bounding box; unknown = yes)."""
    from . import scan
    wanted = {tuple(int(v) for v in t.split("/")) for t in tiles}
    for path in exports:
        try:
            info = scan.cached_info(path)
        except OSError:
            return True
        if not info or info.get("error") or "tiles" not in info:
            return True
        if wanted & {tuple(t) for t in info["tiles"]}:
            return True
    return False


def build_commands(s, exports, squadrats_file, plan=False):
    """Return [(title, argv), ...].

    s: settings dict; exports: the selected Wandrer export paths;
    squadrats_file: path of the Squadrats KML to use (or None).
    """
    py = sys.executable
    tiles = parse_tiles(s["tiles"])
    if not tiles:
        raise SettingsError("Choose at least one tile.")
    out = os.path.expanduser(s["folders"]["out"] or "")
    if not out:
        raise SettingsError("Choose an output folder.")
    if _inside(out, REPO):
        raise SettingsError("The output folder must be outside the repository.")
    for name in ("wandrer", "squadrats"):
        folder = os.path.expanduser(s["folders"][name] or "")
        if folder and os.path.realpath(out) == os.path.realpath(folder):
            raise SettingsError("The output folder must not be an export folder "
                                "(its wmc_classic and wahoondrer_maps sub-folders are deleted at each run).")

    w, q = s["wandrer"], s["squadrats"]
    use_wandrer = bool(w["enabled"] and exports)
    use_squadrats = bool(q["enabled"])          # independent of Wandrer
    cmds = []
    if use_squadrats and not squadrats_file:
        use_squadrats = False           # no KML: build without the layer (a note says so) instead of stopping
        cmds.append(("No Squadrats file: the layer is left out",
                     [sys.executable, "-c",
                      "print('! Squadrats: no KML file found in the Squadrats folder, so this layer is left out. '"
                      "'Put your Squadrats export there (Squadrats section) or untick the layer.')"]))
    if use_wandrer and not plan and not _wandrer_covers(exports, tiles):
        use_wandrer = False             # nothing to convert: no export touches the chosen tiles
        if not use_squadrats:
            cmds.append(("No Wandrer data on these tiles: conversion skipped",
                         [sys.executable, "-c",
                          "print('None of the selected Wandrer exports covers the chosen tiles: '"
                          "'the exports are not unzipped and only plain (classic) tiles are built.')"]))
        else:
            cmds.append(("No Wandrer data on these tiles: the exports are not unzipped",
                         [sys.executable, "-c", "print('Only the Squadrats layer is added.')"]))
    convert = (use_wandrer or use_squadrats) and not plan
    osm = os.path.join(os.path.expanduser(s["folders"]["work"] or out), "wandrer.osm")
    if convert:
        a = [py, os.path.join(REPO, "scripts", "kmz_to_osm.py"), "-o", osm,
             *(exports if use_wandrer else []),
             "--radius-m", str(w["radius_m"]), "--simplify-m", str(w["simplify_m"])]
        if use_squadrats:
            a += ["--tiles", ",".join(tiles)]       # Squadrats do not need Wandrer: draw them on every chosen tile
        if w["with_traveled"]:
            a.append("--with-traveled")
        if w["unpaved_always"]:
            a.append("--unpaved-always")
        if w["paved_always"]:
            a.append("--paved-always")
        if not w["boundaries"]:
            a.append("--no-boundaries")
        if use_squadrats:
            levels = [l for l in q["levels"] if l in ("big", "small")]
            if not levels:
                raise SettingsError("Squadrats: choose at least one level.")
            buffer = int(q["buffer"])
            if buffer < 0:
                raise SettingsError("Squadrats: the band width must be 0 or more.")
            a += ["--squadrats", squadrats_file, "--squadrats-levels", ",".join(levels),
                  "--squadrats-buffer", str(buffer),
                  "--squadrats-big-scope", q.get("big_scope", "all"),
                  "--squadrats-small-scope", q["small_scope"]]
        cmds.append(("Convert the " + " / ".join(n for n, on in (("Wandrer", use_wandrer), ("Squadrats", use_squadrats)) if on) + " exports", a))

    b = [py, os.path.join(REPO, "scripts", "build_tiles.py"), "--xy", ",".join(tiles),
         "--out", out, "--max-days", str(settings_mod.osm_max_days(s))]
    if convert:
        b += ["--wandrer", osm]
    if s["build"]["skip_classic"]:
        b.append("--skip-classic")
    if s["build"].get("contour"):
        spacing = s["build"].get("contour_spacing") or contour.DEFAULT_PRESET
        try:
            contour.preset(spacing)
        except ValueError as exc:
            raise SettingsError(str(exc))
        b += ["--contour", "--contour-spacing", spacing]
        if s["build"].get("contour_per_tile"):           # per-tile choices (menus on the map, Pro)
            chosen = s["build"].get("contour_tiles") or {}
            for k in tiles:
                v = chosen.get(k)
                if v and v != spacing:
                    try:
                        contour.choice(v)
                    except ValueError as exc:
                        raise SettingsError(f"Tile {k}: {exc}")
                    b += ["--contour-tile", f"{k}={v}"]
    if not plan:
        b += poi_args(s)
        b += look_args(w, q)
    if plan:
        b.append("--plan")
    cmds.append(("Plan: tiles and OSM regions" if plan else "Build the map tiles", b))
    if not plan and s.get("device", "wahoo") == "wahoo" and settings_mod.tile_format(s) == "gz":
        cmds.append(gz_command(s, tiles))
    return cmds


# ------------------------------------------------------------------ Garmin
GARMIN_DIR = "garmin"            # Output/garmin/: maprboy-<place>-<date>-overlay.img and ...-base.img


def garmin_missing(s):
    """What the Garmin build needs and does not find: [message, ...]."""
    import shutil
    out = []
    jar = os.path.expanduser((s.get("tools") or {}).get("mkgmap") or "")
    if not jar:
        out.append("mkgmap: give the path of mkgmap.jar in the settings (⚙), download it from mkgmap.org.uk")
    elif not os.path.isfile(jar):
        out.append(f"mkgmap: {jar} not found")
    if not shutil.which("java"):
        out.append("Java is not installed (mkgmap needs it)")
    if not shutil.which("osmium"):
        out.append("osmium is not found: start the page from the maprBoy environment")
    if importlib.util.find_spec("cairosvg") is None:
        out.append("the Python module cairosvg is missing (POI icons): "
                   "micromamba install -n " + ENV_NAME + " -c conda-forge cairosvg")
    return out


def garmin_bbox(s, export_infos):
    """(W, S, E, N) of the Garmin maps, from the area mode of the page:
    manual = the chosen z8 tiles; exports = the exports; exports_km = the exports plus the margin;
    regions = the chosen regions."""
    from ..tiles import tile_bbox
    from . import regions
    mode = (s.get("area") or {}).get("mode", "manual")
    boxes = []
    if mode in ("exports", "exports_km"):
        boxes = [tuple(i["bbox"]) for i in export_infos if i.get("bbox")]
        if not boxes:
            raise SettingsError("No ticked Wandrer export to set the area: tick exports or choose the tiles.")
    elif mode == "regions":
        cat = regions.catalog()
        for k in (s.get("area") or {}).get("regions", []):
            for ring in (cat.get(k) or {}).get("rings", []):
                xs, ys = [p[0] for p in ring], [p[1] for p in ring]
                boxes.append((min(xs), min(ys), max(xs), max(ys)))
        if not boxes:
            raise SettingsError("Choose at least one region.")
    else:
        tiles = parse_tiles(s.get("tiles") or [])
        if not tiles:
            raise SettingsError("Choose at least one tile.")
        boxes = [tile_bbox(*(int(v) for v in t.split("/"))) for t in tiles]
    w, so, e, n = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                   max(b[2] for b in boxes), max(b[3] for b in boxes))
    if mode == "exports_km":
        import math
        km = float((s.get("area") or {}).get("km") or 0)
        dlat, dlon = km / 111.2, km / (111.32 * math.cos(math.radians((so + n) / 2)))
        w, so, e, n = w - dlon, so - dlat, e + dlon, n + dlat
    return (round(w, 5), round(so, 5), round(e, 5), round(n, 5))


def garmin_place(s, export_infos, bbox):
    """A short place name for the Garmin file names and map list, whatever the area mode:
    1. the town that comes most often among the ticked exports whose extent overlaps the area;
    2. else the town at the centre of the area (online, if allowed);
    3. else the OSM region(s): the chosen ones, or those the area needs (e.g. "Greece"); else ""."""
    from . import regions, scan
    w, so, e, n = bbox
    places = [i.get("place") for i in export_infos
              if i.get("place") and i.get("bbox") and i["bbox"][0] <= e and i["bbox"][2] >= w
              and i["bbox"][1] <= n and i["bbox"][3] >= so]
    if places:
        return max(sorted(set(places)), key=places.count)
    if s.get("geocode", True):
        try:
            name = scan.lookup_place((w + e) / 2, (so + n) / 2)
        except Exception:
            name = ""
        if name:
            return name
    area = s.get("area") or {}
    keys = area.get("regions") if area.get("mode") == "regions" and area.get("regions") else regions.regions_for_bbox(bbox)
    return " ".join(regions.catalog().get(k, {}).get("name", k) for k in (keys or [])[:2])


def map_activity(export_infos):
    """bike / foot / combined of the exports used (from their inner file name), "all" when they differ (the "all
    types" choice), "" when unknown: put after the place in the names of the maps that hold Wandrer roads."""
    acts = {i.get("activity") for i in export_infos if i.get("activity") in settings_mod.ACTIVITIES}
    return "all" if len(acts) > 1 else "".join(acts)


def garmin_names(s, export_infos, bbox, now=None, activity=""):
    """{"overlay"|"base": (file name, name shown on the device, family id, first map number)}.
    `activity` (bike, foot...) goes after the place in the overlay's names; the base map does not depend on it."""
    from ..garmin import ids
    from . import scan
    now = time.time() if now is None else now
    import unicodedata
    place = garmin_place(s, export_infos, bbox)
    place = " ".join(unicodedata.normalize("NFKD", place).encode("ascii", "ignore").decode().split())   # Latin-1 maps
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    out = {}
    for kind, (fid, mapname) in ids.build_ids(now).items():
        act = activity if kind == "overlay" else ""
        tag = "-".join(p for p in (scan.slug(place), scan.slug(act), day) if p)
        label = " ".join(p for p in ("maprBoy", "Wandrer" if kind == "overlay" else "base", place, act, day) if p)
        out[kind] = (f"maprboy-{tag}-{kind}.img", label[:50], fid, mapname)
    return out


def garmin_commands(s, export_infos, squadrats_file):
    """[(title, argv), ...] for the Garmin maps of Output/garmin/: the overlay (Wandrer, Squadrats, and the POIs
    when there is no base map) and the OSM base map with the POIs. export_infos: the ticked exports (scan infos)."""
    from . import regions
    py = sys.executable
    out = os.path.expanduser(s["folders"]["out"] or "")
    if not out:
        raise SettingsError("Choose an output folder.")
    if _inside(out, REPO):
        raise SettingsError("The output folder must be outside the repository.")
    missing = garmin_missing(s)
    if missing:
        raise SettingsError("Garmin maps: " + "; ".join(missing) + ".")
    jar = os.path.expanduser(s["tools"]["mkgmap"])
    dest = os.path.join(out, GARMIN_DIR)
    w, q, b = s["wandrer"], s["squadrats"], s["build"]
    exports = [i["path"] for i in export_infos]
    use_wandrer = bool(w["enabled"] and exports)
    use_squadrats = bool(q["enabled"] and squadrats_file)
    use_base = bool(b.get("garmin_base", True))
    bbox = garmin_bbox(s, export_infos)
    box = ",".join(str(v) for v in bbox)
    names = garmin_names(s, export_infos, bbox, activity=map_activity(export_infos) if use_wandrer else "")
    cmds = []
    if q["enabled"] and not squadrats_file:
        cmds.append(("No Squadrats file: the layer is left out",
                     [py, "-c", "print('! Squadrats: no KML file found in the Squadrats folder, so this layer is left out.')"]))
    # the POIs need the OSM data, in the base map or in the overlay
    try:
        regions.ensure_catalog()
    except OSError as exc:
        raise SettingsError(f"Geofabrik's region list could not be downloaded: {exc}")
    keys = regions.regions_for_bbox(bbox)
    if not keys:
        raise SettingsError("No OpenStreetMap region covers this area.")
    osm = [regions.region_path(k) for k in keys]
    cmds.append(("Get the OpenStreetMap data (" + ", ".join(regions.pretty_name(k) for k in keys) + ")",
                 [py, "-m", "wahoondrer.gui.regions", "--fetch", *keys,
                  "--max-days", str(settings_mod.osm_max_days(s))]))
    pois = poi_args(s)
    if use_wandrer or use_squadrats or not use_base:
        f, label, fid, mapname = names["overlay"]
        a = [py, os.path.join(REPO, "scripts", "build_garmin.py"), "--mkgmap", jar,
             "-o", os.path.join(dest, f), "--bbox", box,
             "--name", label, "--family-id", str(fid), "--mapname", mapname]
        if use_wandrer:
            a += [*exports, "--radius-m", str(w["radius_m"]), "--simplify-m", str(w["simplify_m"])]
            if w["unpaved_always"]:
                a.append("--unpaved-always")
            if w["paved_always"]:
                a.append("--paved-always")
            if not w.get("merge", True):
                a.append("--no-merge")
            if not w["boundaries"]:
                a.append("--no-boundaries")
        else:
            a.append("--no-wandrer")
        if use_squadrats:
            levels = [l for l in q["levels"] if l in ("big", "small")]
            if not levels:
                raise SettingsError("Squadrats: choose at least one level.")
            a += ["--squadrats", squadrats_file, "--squadrats-levels", ",".join(levels),
                  "--squadrats-buffer", str(int(q["buffer"])),
                  "--squadrats-big-scope", q.get("big_scope", "all"), "--squadrats-small-scope", q["small_scope"]]
        if not use_base:
            a += ["--pois", *[x for f in osm for x in ("--osm", f)], *pois]
        what = " + ".join(n for n, on in (("Wandrer", use_wandrer), ("Squadrats", use_squadrats),
                                          ("POIs", not use_base)) if on)
        cmds.append((f"Make the Garmin overlay ({what})", a))
    if use_base:
        f, label, fid, mapname = names["base"]
        c = [py, os.path.join(REPO, "scripts", "build_garmin_base.py"), "--mkgmap", jar,
             "-o", os.path.join(dest, f), "--bbox", box,
             "--name", label, "--family-id", str(fid), "--mapname", mapname,
             *[x for f in osm for x in ("--osm", f)], *pois]
        if b.get("street_names"):
            c.append("--street-names")
        cmds.append(("Make the Garmin base map (OpenStreetMap + POIs)", c))
    made = [a[a.index("-o") + 1] for t, a in cmds if t.startswith("Make the Garmin")]
    if not made:
        raise SettingsError("Nothing to make: turn on Wandrer, Squadrats or the base map.")
    cmds.append(("Note the maps of this run", [py, "-m", "wahoondrer.garmin.device", "mark", "--out", dest,
                                               *[os.path.basename(m) for m in made]]))
    return cmds


def garmin_copy_command(s, names, target):
    """The step that copies the chosen maps of Output/garmin/ to the Garmin plugged in: target "disk:<volume>" or
    "mtp" (wahoondrer/garmin/device.py)."""
    if not s["folders"]["out"]:
        raise SettingsError("Choose the output folder first (⚙).")
    folder = os.path.join(os.path.abspath(os.path.expanduser(s["folders"]["out"])), GARMIN_DIR)
    if not names:
        raise SettingsError("Tick the maps to copy.")
    files = []
    for n in names:
        if not isinstance(n, str) or os.path.basename(n) != n or not n.endswith(".img"):
            raise SettingsError(f"Not a map of the garmin folder: {n}")
        files.append(os.path.join(folder, n))
    if not (target == "mtp" or (isinstance(target, str) and target.startswith("disk:/"))):
        raise SettingsError("No Garmin chosen.")
    return ("Copy the maps to the Garmin", [sys.executable, "-m", "wahoondrer.garmin.device", "copy",
                                            "--target", target, *files])


KAROO_DIR = "karoo"              # Output/karoo/: maprboy-<place>-<date>.map, maprboy-theme-block.xml, maprboy-icons/


def _osmosis(s):
    import shutil
    path = os.path.expanduser((s.get("tools") or {}).get("osmosis") or "")
    return (path if os.path.isfile(path) else None) if path else shutil.which("osmosis")


def _map_writer(osmosis):
    """The mapsforge MapWriter plugin of Osmosis: in ~/.openstreetmap/osmosis/plugins/ (where WMC puts it) or in
    the lib folder of the Osmosis installation."""
    import glob
    places = [os.path.join(os.path.expanduser("~"), ".openstreetmap", "osmosis", "plugins")]
    if osmosis:
        root = os.path.dirname(os.path.dirname(os.path.realpath(osmosis)))
        places += [os.path.join(root, "lib", "default"), os.path.join(root, "lib")]
    return next((f for p in places for f in glob.glob(os.path.join(p, "*map-writer*.jar"))), None)


def karoo_missing(s):
    """What the Karoo build needs and does not find: [message, ...]."""
    import shutil
    out = []
    osmosis = _osmosis(s)
    given = (s.get("tools") or {}).get("osmosis") or ""
    if not osmosis:
        out.append(f"Osmosis: {given} not found" if given else
                   "Osmosis is not found: start the page from the maprBoy environment, or give its path (⚙)")
    elif not _map_writer(osmosis):
        out.append("the mapsforge MapWriter plugin of Osmosis is not found (~/.openstreetmap/osmosis/plugins/)")
    if not shutil.which("osmium"):
        out.append("osmium is not found: start the page from the maprBoy environment")
    return out


def karoo_commands(s, export_infos, squadrats_file):
    """[(title, argv), ...] for the Karoo map of Output/karoo/: the Wandrer / Squadrats lines (kmz_to_osm.py), the
    OSM data for the POIs, then the map file, our theme rules and the icons (build_karoo.py)."""
    from ..tiles import tiles_for_bbox
    from . import regions, scan
    import unicodedata
    py = sys.executable
    out = os.path.expanduser(s["folders"]["out"] or "")
    if not out:
        raise SettingsError("Choose an output folder.")
    if _inside(out, REPO):
        raise SettingsError("The output folder must be outside the repository.")
    missing = karoo_missing(s)
    if missing:
        raise SettingsError("Karoo maps: " + "; ".join(missing) + ".")
    work = os.path.expanduser(s["folders"]["work"] or "") or os.path.join(out, "work")
    w, q = s["wandrer"], s["squadrats"]
    exports = [i["path"] for i in export_infos]
    use_wandrer = bool(w["enabled"] and exports)
    use_squadrats = bool(q["enabled"] and squadrats_file)
    pois = poi_args(s)
    use_pois = bool([p for p in poi_config.effective_pois(poi_config.read_rules(), s.get("pois") or {})
                     if p.get("enabled", True)])
    if not (use_wandrer or use_squadrats or use_pois):
        raise SettingsError("Nothing to make: turn on Wandrer, Squadrats or POIs.")
    bbox = garmin_bbox(s, export_infos)
    box = ",".join(str(v) for v in bbox)
    place = garmin_place(s, export_infos, bbox)
    place = " ".join(unicodedata.normalize("NFKD", place).encode("ascii", "ignore").decode().split())
    act = map_activity(export_infos) if use_wandrer else ""
    tag = "-".join(p for p in (scan.slug(place), scan.slug(act), time.strftime("%Y-%m-%d")) if p)
    cmds = []
    if q["enabled"] and not squadrats_file:
        cmds.append(("No Squadrats file: the layer is left out",
                     [py, "-c", "print('! Squadrats: no KML file found in the Squadrats folder, so this layer is left out.')"]))
    build = [py, os.path.join(REPO, "scripts", "build_karoo.py"), "-o", os.path.join(out, KAROO_DIR),
             "--bbox", box, "--name", tag, "--osmosis", _osmosis(s)]
    if use_wandrer or use_squadrats:
        lines = os.path.join(work, "karoo-lines.osm")
        a = [py, os.path.join(REPO, "scripts", "kmz_to_osm.py"), "-o", lines,
             "--tiles", ",".join(f"{x}/{y}" for x, y in tiles_for_bbox(*bbox))]
        if use_wandrer:
            a += [*exports, "--radius-m", str(w["radius_m"]), "--simplify-m", str(w["simplify_m"])]
            a += [f for f, on in (("--unpaved-always", w["unpaved_always"]), ("--paved-always", w["paved_always"]),
                                  ("--no-merge", not w.get("merge", True)), ("--no-boundaries", not w["boundaries"]),
                                  ("--with-traveled", w["with_traveled"])) if on]
        if use_squadrats:
            levels = [l for l in q["levels"] if l in ("big", "small")]
            if not levels:
                raise SettingsError("Squadrats: choose at least one level.")
            a += ["--squadrats", squadrats_file, "--squadrats-levels", ",".join(levels),
                  "--squadrats-buffer", str(int(q["buffer"])),
                  "--squadrats-big-scope", q.get("big_scope", "all"), "--squadrats-small-scope", q["small_scope"]]
        what = " + ".join(n for n, on in (("Wandrer", use_wandrer), ("Squadrats", use_squadrats)) if on)
        cmds.append((f"Prepare the lines ({what})", a))
        build += ["--lines", lines]
        if use_wandrer and w["with_traveled"]:
            build.append("--with-visited")
    if use_pois:
        try:
            regions.ensure_catalog()
        except OSError as exc:
            raise SettingsError(f"Geofabrik's region list could not be downloaded: {exc}")
        keys = regions.regions_for_bbox(bbox)
        if not keys:
            raise SettingsError("No OpenStreetMap region covers this area.")
        cmds.append(("Get the OpenStreetMap data (" + ", ".join(regions.pretty_name(k) for k in keys) + ")",
                     [py, "-m", "wahoondrer.gui.regions", "--fetch", *keys,
                      "--max-days", str(settings_mod.osm_max_days(s))]))
        build += ["--pois", *[x for k in keys for x in ("--osm", regions.region_path(k))], *pois]
    try:
        look, sq_look = theme_style.clean(w.get("look")), theme_style.sq_clean(q.get("look"))
    except ValueError as exc:
        raise SettingsError(f"Look: {exc}")
    build += ["--look", json.dumps(look), "--sq-look", json.dumps(sq_look)]
    cmds.append((f"Make the Karoo map (maprboy-{tag}.map)", build))
    return cmds


def karoo_copy_command(s, action="copy"):
    """The step that copies the Karoo maps to the Karoo plugged in, or takes them off (wahoondrer/karoo/device.py)."""
    if not s["folders"]["out"]:
        raise SettingsError("Choose the output folder first (⚙).")
    out = os.path.abspath(os.path.expanduser(s["folders"]["out"]))
    argv = [sys.executable, "-m", "wahoondrer.karoo.device", action, "--out", out]
    adb = (s.get("tools") or {}).get("adb") or ""
    if adb:
        argv += ["--adb", adb]
    return ("Copy the maps to the Karoo" if action == "copy" else "Take the maprBoy maps off the Karoo", argv)


def copy_command(s):
    """The step that copies the maps of the last run to the ELEMNT plugged in (adb, wahoondrer/devcopy.py)."""
    if not s["folders"]["out"]:
        raise SettingsError("Choose the output folder first (⚙).")
    out = os.path.abspath(os.path.expanduser(s["folders"]["out"]))
    argv = [sys.executable, "-m", "wahoondrer.devcopy", "--out", out,
            "--format", settings_mod.tile_format(s)]
    adb = (s.get("tools") or {}).get("adb") or ""
    if adb:
        argv += ["--adb", adb]
    return ("Copy the maps to the device", argv)


def gz_command(s, tiles=None):
    """The step that writes the .map.gz tiles (Output/wahoondrer_maps_gz/, laid out like the device)."""
    out = os.path.abspath(os.path.expanduser(s["folders"]["out"] or ""))
    if not s["folders"]["out"]:
        raise SettingsError("Choose the output folder first (⚙).")
    argv = [sys.executable, "-m", "wahoondrer.gztiles", "--out", out]
    if tiles:
        argv += ["--tiles", ",".join(tiles)]
    return ("Write the .map.gz tiles", argv)


# ------------------------------------------------------------------ run history (for the time estimate)
RUNS_PATH = os.path.join(os.path.expanduser("~"), ".wahoondrer", "runs.json")
RUNS_KEPT = 30
ESTIMATE_MIN_RUNS = 2        # comparable successful runs needed before showing an estimate
ESTIMATE_MAX_SPREAD = 2.0    # ...and their time per tile may differ by at most this factor


def run_profile(s):
    """What makes two runs comparable: the device, the number of tiles, Wandrer only or not, contour lines."""
    return {"device": s.get("device", "wahoo"), "tiles": len(s.get("tiles") or []),
            "skip_classic": bool(s["build"].get("skip_classic")), "contour": bool(s["build"].get("contour"))}


def load_runs(path=None):
    try:
        with open(path or RUNS_PATH, encoding="utf-8") as f:
            runs = json.load(f)
        return [r for r in runs if isinstance(r, dict)] if isinstance(runs, list) else []
    except (OSError, ValueError):
        return []


def record_run(profile, seconds, path=None):
    """Keep the duration of a successful run (the most recent RUNS_KEPT)."""
    if not profile or not profile.get("tiles") or seconds <= 0:
        return
    path = path or RUNS_PATH
    runs = load_runs(path) + [dict(profile, seconds=round(seconds), date=time.strftime("%Y-%m-%d %H:%M"))]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(runs[-RUNS_KEPT:], f, indent=1)
    except OSError:
        pass                                  # the estimate is a convenience: never fail a run for it


def per_tile_estimate(runs, device, skip_classic, contour_on):
    """{"seconds_per_tile", "runs"} from the comparable runs, or None when they are too few or too different
    (better nothing than a wrong figure)."""
    same = [r["seconds"] / r["tiles"] for r in runs
            if r.get("device", "wahoo") == device and bool(r.get("skip_classic")) == bool(skip_classic)
            and bool(r.get("contour")) == bool(contour_on) and r.get("tiles", 0) > 0 and r.get("seconds", 0) > 0][-5:]
    if len(same) < ESTIMATE_MIN_RUNS or max(same) > ESTIMATE_MAX_SPREAD * min(same):
        return None
    mid = sorted(same)[len(same) // 2] if len(same) % 2 else sum(sorted(same)[len(same) // 2 - 1:len(same) // 2 + 1]) / 2
    return {"seconds_per_tile": round(mid, 1), "runs": len(same)}


class Job:
    """Runs commands sequentially in a thread; the page polls for new log lines."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.lines, self.status, self.proc, self.cancelled = [], "idle", None, False
        self.title, self.started, self.finished, self.kind = "", 0.0, 0.0, ""
        self.notify, self.thread, self.profile = False, None, None
        self.plan = getattr(self, "plan", None)   # last "Check the plan" result, kept between jobs

    def running(self):
        return self.status == "running"

    def start(self, commands, kind="run", notify=False, profile=None):
        """notify: show a system notification when a "run" ends (done or failed; not when cancelled).
        profile: run_profile() of the settings; a successful run's duration is kept for the time estimate."""
        with self.lock:
            if self.running():
                raise SettingsError("A job is already running.")
            self.reset()
            self.status, self.started, self.kind, self.notify, self.profile = "running", time.time(), kind, notify, profile
            self.thread = threading.Thread(target=self._run, args=(commands,), daemon=True)
        self.thread.start()

    def _log(self, text):
        if text.startswith("PLAN-JSON "):        # machine-readable line from build_tiles.py --plan
            try:
                plan = json.loads(text[len("PLAN-JSON "):])
                with self.lock:
                    self.plan = plan
            except ValueError:
                pass
            return
        with self.lock:
            self.lines.append(text)

    def _run(self, commands):
        env = dict(os.environ, PYTHONUNBUFFERED="1", WAHOONDRER_GUI="1")
        ok = True
        for i, (title, argv) in enumerate(commands, 1):
            with self.lock:
                self.title = f"{i}/{len(commands)} {title}"
            self._log(f"\n=== {i}/{len(commands)} {title} ===")
            try:
                self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True,
                                             bufsize=1, env=env, cwd=REPO)
                for line in self.proc.stdout:
                    self._log(line.rstrip("\n"))
                code = self.proc.wait()
            except OSError as exc:
                self._log(f"ERROR: cannot start the command: {exc}")
                code = -1
            if self.cancelled:
                break
            if code != 0:
                self._log(f"\nStopped: this step failed (exit code {code}).")
                ok = False
                break
        hint = None
        if not ok and not self.cancelled:
            with self.lock:
                hint = hint_for(self.lines)
            if hint:
                self._log(f"\n{hint[1]}")
        with self.lock:
            self.finished = time.time()
            self.status = "cancelled" if self.cancelled else ("done" if ok else "failed")
        took = format_duration(self.finished - self.started) if self.started else ""
        if self.kind == "run" and self.status == "done":
            record_run(self.profile, self.finished - self.started)
        self._log({"done": f"\nFinished in {took}.", "failed": f"\nTime spent: {took}.", "cancelled": f"\nCancelled after {took}."}[self.status])
        self._notify(took, hint)

    def _notify(self, took, hint):
        """The page may be hidden behind other windows during a long build: tell the user how it ended."""
        if not self.notify or self.kind != "run" or self.status not in ("done", "failed"):
            return
        if self.status == "done":
            with self.lock:
                notes = sum(1 for l in self.lines if l.startswith("! "))
            more = (f"{notes} note{'s' if notes > 1 else ''} to read: open the page."
                    if notes else "Open the page to copy the files to your device.")
            native.notify("maprBoy: maps ready", f"Finished in {took}. {more}")
        else:
            step = re.sub(r"^\d+/\d+ ", "", self.title)
            native.notify("maprBoy: stopped", f"\u201c{step}\u201d failed. " + (hint[0] if hint else "Open the page for details."), ok=False)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def snapshot(self, since=0):
        with self.lock:
            return {"status": self.status, "kind": self.kind, "title": self.title, "next": len(self.lines),
                    "lines": self.lines[since:],
                    "seconds": round((self.finished or time.time()) - self.started) if self.started else 0}
