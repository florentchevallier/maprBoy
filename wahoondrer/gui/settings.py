"""User settings, stored in settings.json of the app's own folder (wahoondrer/home.py; never in the repository).

One set of settings per device (Wahoo, Garmin Edge, Garmin watch, Karoo) plus settings shared by all. The
file holds
    {"version": 2, "device": "wahoo", <shared keys>, "squadrats_file": "...",
     "devices": {"wahoo": {<device keys>}, "garmin": {...}, "garmin-watch": {...}, "karoo": {...}}}
load() returns a flat view: the shared keys plus the keys of the current device (so the rest of the
code reads s["tiles"], s["wandrer"]... as before); save() writes the device keys back to that device only.
The two kinds of Garmin keep their own settings ("garmin" is the Edge, for the files written before they
were apart); a watch without its own block yet starts from the Edge's.
A file of the first version (no "devices") becomes the Wahoo settings."""
import copy
import json
import os

from .. import home, theme_style

SETTINGS_PATH = home.path("settings.json")

DEVICES = ("wahoo", "garmin", "karoo")        # what the page's buttons choose
BLOCKS = ("wahoo", "garmin", "garmin-watch", "karoo")   # one set of settings each (see block_of)
ACTIVITIES = ("bike", "foot", "combined")

DEFAULTS = {
    "device": "wahoo",               # the device the page works for: "wahoo" | "garmin" | "karoo"
    "mode": "simple",                # "simple" | "pro"
    "folders": {
        "data": "",                  # parent of the standard folders created by the page
        "wandrer": "",               # folder with the Wandrer KMZ/KML exports
        "squadrats": "",             # folder with the Squadrats KML exports
        "work": "",                  # temporary files (merged Wandrer data ready for WMC)
        "out": "",                   # where the generated tiles are written
    },
    "activity": None,                # "bike" | "foot" | "combined" | (pro) "any" (all types); None = type of the newest export
    "geocode": True,                 # look up the main place name of each export online
    "check_updates": True,           # ask GitHub once a day whether a newer maprBoy is out
    "excluded_exports": [],          # export file names the user unticked
    "tiles": [],                     # ["136/88", ...]
    "area": {"mode": "manual",       # how the tiles are chosen: "manual" | "exports" | "exports_km" | "regions"
             "km": 10,               # margin around the exports for "exports_km"
             "regions": [],          # Geofabrik regions (file keys, e.g. "europe_germany_bayern_oberbayern") for "regions"
             "box": None},           # [W, S, E, N] drawn on the map: the area of "manual" for Garmin and Karoo
    "wandrer": {
        "enabled": True,
        "boundaries": True,
        "unvisited": True,           # False = only the roads already visited (radius_m and the two "always" ignored)
        "radius_m": 250,
        "with_traveled": False,
        "unpaved_always": False,
        "paved_always": False,
        "simplify_m": 1.5,
        "merge": True,
        "look": theme_style.defaults(),   # colour, width and outline of the unvisited / visited roads, light and dark (Pro)
    },
    "squadrats": {
        "enabled": False,
        "file": "",                  # file name; "" = the most recent one
        "levels": ["big", "small"],
        "buffer": 3,                 # band width (squares) for the "band" choices
        "big_scope": "all",          # big squares: "all" unexplored ones of the tiles | "band" around the explored ones
        "small_scope": "all",        # small squares: "all" | "explored-big" | "band" | "both"
        "look": theme_style.sq_defaults(),   # colour, width and dashes of the outlines in the Wahoo theme, light and dark (Pro)
    },
    "build": {"skip_classic": False, "contour": False, "contour_spacing": "25-75-150",
              "contour_per_tile": False, "contour_tiles": {},
              "notify": True,            # system notification when a run ends
              "garmin_base": True,       # Garmin: also make the OSM base map with the POIs (maprboy-base.img)
              "street_names": False},    # Garmin base map: label the streets
    "ui": {"sections": {},           # per-section Simple/Pro choice: {"pois": "simple" | "pro"}; missing = follow `mode`
           "folded": {}},            # folded / open choices that differ from the default: {"sec:osm": false, "reg:Germany": true}
    "pois": {"overrides": {}, "custom": []},   # the user's POI choices, apart from the defaults in config/poi-rules.json
    "tools": {"mkgmap": "",          # path of mkgmap.jar (Garmin maps)
              "adb": "",             # path of adb (automatic copy to a Wahoo or a Karoo); "" = found by itself
              "osmosis": ""},        # path of osmosis (Karoo maps); "" = found in the PATH
    "tile_format": "lzma",           # Wahoo tiles: "lzma" (most ELEMNTs) | "gz" (Output/wahoondrer_maps_gz/, e.g. an old Roam v1)
    "garmin_model": "edge",          # Garmin: "edge" (bike computer) | "watch": the default look of the roads
    "osm_max_days": 14,              # WMC renews the OSM regions older than this (days); 0 = never (shared by the devices)
}


def _merge(base, new):
    """Return base updated with new, keeping only the keys that exist in base."""
    out = copy.deepcopy(base)
    for k, v in (new or {}).items():
        if k not in base:
            continue
        if isinstance(base[k], dict) and isinstance(v, dict):
            out[k] = _merge(base[k], v) if base[k] else copy.deepcopy(v)   # empty dict = free-form keys
        else:
            out[k] = v
    return out


# kept apart for each device; every other key of DEFAULTS is shared (the Squadrats file too, see below)
TILE_FORMATS = ("lzma", "gz")


def tile_format(s):
    """The tile format of the Wahoo ("lzma" or "gz"); anything unexpected is "lzma"."""
    v = s.get("tile_format")
    return v if v in TILE_FORMATS else "lzma"


GARMIN_MODELS = ("edge", "watch")


def garmin_model(s):
    """The kind of Garmin ("edge" or "watch"); anything unexpected is "edge"."""
    v = s.get("garmin_model")
    return v if v in GARMIN_MODELS else "edge"


def block_of(device, model):
    """The name of the settings block of a device: a Garmin watch has its own, apart from the Edge's."""
    if device != "garmin":
        return device if device in DEVICES else "wahoo"
    return "garmin-watch" if model == "watch" else "garmin"


OSM_MAX_DAYS = (7, 14, 30, 60, 90, 0)   # the choices of the page (0 = never renew)


def osm_max_days(s):
    """The OSM renewal age of the settings (days, 0 = never); anything unexpected falls back to 14."""
    v = s.get("osm_max_days", 14)
    return v if isinstance(v, int) and not isinstance(v, bool) and v in OSM_MAX_DAYS else 14


DEVICE_KEYS = ("activity", "excluded_exports", "tiles", "area", "wandrer", "squadrats", "build", "pois")


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def _split(raw):
    """(device, shared keys, {block: device keys}, squadrats file) of a stored file, either version."""
    if isinstance(raw.get("devices"), dict):
        devices = {d: v for d, v in raw["devices"].items() if d in BLOCKS and isinstance(v, dict)}
        sq_file = raw.get("squadrats_file", "")
    else:                                   # first version: everything was for the Wahoo
        devices = {"wahoo": {k: raw[k] for k in DEVICE_KEYS if k in raw}}
        sq_file = (raw.get("squadrats") or {}).get("file", "") if isinstance(raw.get("squadrats"), dict) else ""
    device = raw.get("device") if raw.get("device") in DEVICES else "wahoo"
    shared = {k: v for k, v in raw.items() if k not in DEVICE_KEYS and k not in ("devices", "device", "version")}
    return device, shared, devices, sq_file if isinstance(sq_file, str) else ""


def _view(device, shared, block, sq_file):
    sq = (block or {}).get("squadrats")
    if isinstance(sq, dict) and "big_scope" not in sq and sq.get("small_scope") == "explored-big":
        block = {**block, "squadrats": {**sq, "small_scope": "all"}}    # saved before "all" became the default
    if "tile_format" not in shared and shared.get("wahoo_model") == "roam1":   # the model menu of an earlier version
        shared = {**shared, "tile_format": "gz"}
    data = _merge(DEFAULTS, {**shared, **(block or {}), "device": device})
    data["squadrats"]["file"] = sq_file
    if data["activity"] not in ACTIVITIES + ("any",):    # "latest" / "auto" of earlier versions, or unknown
        data["activity"] = None
    return data


def _block(devices, device, shared):
    """The stored settings of this device; a Garmin watch that has none yet starts from the Edge's."""
    key = block_of(device, garmin_model(shared))
    return devices.get(key) if key in devices else (devices.get("garmin") if key == "garmin-watch" else None)


def load(path=SETTINGS_PATH):
    """The settings of the current device, with the shared ones (a flat dict shaped like DEFAULTS)."""
    device, shared, devices, sq_file = _split(_read(path))
    return _view(device, shared, _block(devices, device, shared), sq_file)


def save(data, path=SETTINGS_PATH):
    """Store the page's settings and return the view to use from now on.

    When data names another device than the stored one (or another kind of Garmin), the page is switching
    device: only the shared keys are kept from data, and the other device's own settings are returned."""
    stored_device, stored_shared, devices, _ = _split(_read(path))
    merged = _merge(DEFAULTS, data)
    device = merged["device"] if merged["device"] in DEVICES else "wahoo"
    shared = {k: v for k, v in merged.items() if k not in DEVICE_KEYS and k != "device"}
    sq_file = merged["squadrats"].get("file", "") or ""
    key = block_of(device, garmin_model(shared))
    if key == block_of(stored_device, garmin_model(stored_shared)):   # switching: the page sends the values of the
        block = {k: copy.deepcopy(merged[k]) for k in DEVICE_KEYS}    # device we leave, so they are kept out
        block["squadrats"].pop("file", None)
        devices[key] = block
    out = {"version": 2, "device": device, **shared, "squadrats_file": sq_file, "devices": devices}
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return _view(device, shared, _block(devices, device, shared), sq_file)


STANDARD_FOLDERS = {"wandrer": "Wandrer_exports", "squadrats": "Squadrats_exports",
                    "out": "Output", "work": "Work_files"}


def suggest_folders(wandrer_dir):
    """Default work/out folders next to the exports folder (used when empty)."""
    parent = os.path.dirname(os.path.abspath(os.path.expanduser(wandrer_dir)))
    return (os.path.join(parent, STANDARD_FOLDERS["work"]),
            os.path.join(parent, STANDARD_FOLDERS["out"]))


def make_standard_folders(base):
    """Create the standard sub-folders inside `base`; return {name: path}."""
    base = os.path.abspath(os.path.expanduser(base))
    paths = {}
    for key, name in STANDARD_FOLDERS.items():
        paths[key] = os.path.join(base, name)
        os.makedirs(paths[key], exist_ok=True)
    return paths
