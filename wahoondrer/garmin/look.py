"""How the Wandrer roads look on a Garmin: colour, width in whole pixels, white edge; once for the day
display and once for the night one. Unpaved roads are dashed (same colour, no edge). Every change of
look means a new .img: the look is stored inside the file."""
import copy
import json
import re

KINDS = ("unvisited", "visited", "boundary")
MODES = ("day", "night")
MIN_WIDTH, MAX_WIDTH = 1, 8

_UNVISITED = {"color": "#FF0000", "width": 4, "edge": True}
_VISITED = {"color": "#00E1FF", "width": 4, "edge": False}
_BOUNDARY = {"color": "#8000C0", "width": 1, "edge": False}
DEFAULT = {mode: {"unvisited": dict(_UNVISITED), "visited": dict(_VISITED), "boundary": dict(_BOUNDARY)}
           for mode in MODES}


def defaults():
    return copy.deepcopy(DEFAULT)


def _one(part, base, where):
    part = {**base, **(part if isinstance(part, dict) else {})}
    color = str(part["color"]).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"{where}: colour {color!r} is not a #RRGGBB value")
    try:
        width = int(part["width"])
    except (TypeError, ValueError):
        raise ValueError(f"{where}: width {part['width']!r} is not a whole number")
    if width != float(part["width"]):
        raise ValueError(f"{where}: width must be a whole number of pixels")
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ValueError(f"{where}: width must be between {MIN_WIDTH} and {MAX_WIDTH}")
    return {"color": color.upper(), "width": width, "edge": bool(part["edge"])}


def clean(look):
    """The look with every value checked and completed with the defaults; raises ValueError."""
    look = look if isinstance(look, dict) else {}
    out = {}
    for mode in MODES:
        # the night look follows the day look unless it is given
        src = look.get(mode) if isinstance(look.get(mode), dict) else (
            look.get("day") if mode == "night" and isinstance(look.get("day"), dict) else {})
        out[mode] = {kind: _one(src.get(kind), DEFAULT[mode][kind], f"{mode} {kind} roads") for kind in KINDS}
    for kind in KINDS:   # a TYP line has one width and one edge: only the colour can change at night
        out["night"][kind].update(width=out["day"][kind]["width"], edge=out["day"][kind]["edge"])
    return out


def load(path):
    """A look from a JSON file (only the values that differ from the default are needed)."""
    with open(path, encoding="utf-8") as f:
        return clean(json.load(f))
