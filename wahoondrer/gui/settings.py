"""User settings, stored in ~/.wahoondrer/settings.json (never in the repository)."""
import copy
import json
import os

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".wahoondrer", "settings.json")

DEFAULTS = {
    "mode": "simple",                # "simple" | "pro"
    "folders": {
        "data": "",                  # parent of the standard folders created by the page
        "wandrer": "",               # folder with the Wandrer KMZ/KML exports
        "squadrats": "",             # folder with the Squadrats KML exports
        "work": "",                  # temporary files (merged Wandrer data ready for WMC)
        "out": "",                   # where the generated tiles are written
    },
    "activity": "bike",              # "bike" | "foot" | "combined" | (pro) "any" (all types) | "latest" (type of newest export)
    "geocode": True,                 # look up the main place name of each export online
    "excluded_exports": [],          # export file names the user unticked
    "tiles": [],                     # ["136/88", ...]
    "wandrer": {
        "enabled": True,
        "boundaries": True,
        "radius_m": 250,
        "with_traveled": False,
        "unpaved_always": False,
        "paved_always": False,
        "simplify_m": 1.5,
        "merge": True,
    },
    "squadrats": {
        "enabled": False,
        "file": "",                  # file name; "" = the most recent one
        "levels": ["big", "small"],
        "buffer": 3,
        "small_scope": "explored-big",
    },
    "build": {"skip_classic": False, "contour": False},
    "ui": {"sections": {}},          # per-section Simple/Pro choice: {"pois": "simple" | "pro"}; missing = follow `mode`
    "pois": {"overrides": {}, "custom": []},   # the user's POI choices, apart from the defaults in config/poi-rules.json
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


def load(path=SETTINGS_PATH):
    try:
        with open(path, encoding="utf-8") as f:
            data = _merge(DEFAULTS, json.load(f))
            if data["activity"] == "auto":          # old name of "latest"
                data["activity"] = "latest"
            return data
    except (OSError, ValueError):
        return copy.deepcopy(DEFAULTS)


def save(data, path=SETTINGS_PATH):
    merged = _merge(DEFAULTS, data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return merged


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
