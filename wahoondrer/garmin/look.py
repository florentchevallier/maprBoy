"""How the Wandrer roads look on a Garmin: colour, width in whole pixels, edge (and its colour), dashes; once for the
day display and once for the night one. Unpaved roads have their own entries: by default the same colour,
dashed (8 px drawn, 8 px empty). "dash": null gives a solid line instead (e.g. thinner, for small screens).
A dashed line has no edge. Every change of look means a new .img: the look is stored inside the file.
The default depends on the kind of device (MODELS): a watch and an Edge do not draw the track already ridden alike."""
import copy
import json
import re

KINDS = ("unvisited", "unvisited_unpaved", "visited", "visited_unpaved", "boundary", "squadrat_big", "squadrat_small")
MODES = ("day", "night")
MIN_WIDTH, MAX_WIDTH = 1, 8
PATTERN = 32    # a TYP line bitmap is 32 px wide: dash + gap must divide it

MODELS = ("watch", "edge")
WHITE, BLACK = "#FFFFFF", "#000000"

# Watch: cyan with a white edge stands out best on the Forerunner 955 (tested October 2026; orange looked like the
# Garmin roads there). Magenta for the roads already ridden.
_UNVISITED = {"color": "#00E1FF", "width": 4, "edge": True, "edge_color": WHITE, "dash": None}
_VISITED = {"color": "#FF00FF", "width": 4, "edge": False, "edge_color": WHITE, "dash": None}
_BOUNDARY = {"color": "#B000FF", "width": 8, "edge": False, "edge_color": WHITE, "dash": None}
_UNVISITED_UNPAVED = {"color": "#00E1FF", "width": 4, "edge": False, "edge_color": WHITE, "dash": [8, 8]}
_VISITED_UNPAVED = {"color": "#FF00FF", "width": 4, "edge": False, "edge_color": WHITE, "dash": [8, 8]}
# Squadrats: blue, not the red of the Wahoo theme, which would be read as unvisited roads here
_SQUADRAT_BIG = {"color": "#1E50FF", "width": 2, "edge": False, "edge_color": WHITE, "dash": None}
_SQUADRAT_SMALL = {"color": "#1E50FF", "width": 2, "edge": False, "edge_color": WHITE, "dash": [8, 8]}
DEFAULT = {mode: {"unvisited": dict(_UNVISITED), "unvisited_unpaved": dict(_UNVISITED_UNPAVED),
                  "visited": dict(_VISITED), "visited_unpaved": dict(_VISITED_UNPAVED),
                  "boundary": dict(_BOUNDARY),
                  "squadrat_big": dict(_SQUADRAT_BIG), "squadrat_small": dict(_SQUADRAT_SMALL)}
           for mode in MODES}
# Edge: on an Edge 830 outdoors (November 2026) the cyan roads could not be told from the track already ridden, so
# orange with a black edge, as on the Wahoo (not checked on the device yet). The rest as on the watch.
_EDGE = {"unvisited": {"color": "#FF8800", "edge_color": BLACK}, "unvisited_unpaved": {"color": "#FF8800"}}
DEFAULTS = {"watch": DEFAULT,
            "edge": {mode: {kind: {**part, **_EDGE.get(kind, {})} for kind, part in DEFAULT[mode].items()}
                     for mode in MODES}}


def model(name):
    """The kind of Garmin device ("watch" or "edge"); anything else is "watch", the look tested first."""
    return name if name in MODELS else "watch"


def defaults(device="watch"):
    return copy.deepcopy(DEFAULTS[model(device)])


def _one(part, base, where):
    part = {**base, **(part if isinstance(part, dict) else {})}
    color = str(part["color"]).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"{where}: colour {color!r} is not a #RRGGBB value")
    edge_color = str(part["edge_color"]).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", edge_color):
        raise ValueError(f"{where}: edge colour {edge_color!r} is not a #RRGGBB value")
    try:
        width = int(part["width"])
    except (TypeError, ValueError):
        raise ValueError(f"{where}: width {part['width']!r} is not a whole number")
    if width != float(part["width"]):
        raise ValueError(f"{where}: width must be a whole number of pixels")
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ValueError(f"{where}: width must be between {MIN_WIDTH} and {MAX_WIDTH}")
    dash = part.get("dash")
    if dash is not None:
        try:
            on, off = (int(v) for v in dash)
        except (TypeError, ValueError):
            raise ValueError(f"{where}: dash must be [drawn, empty] in pixels, or null for a solid line")
        if on < 1 or off < 1 or PATTERN % (on + off):
            raise ValueError(f"{where}: dash {on}+{off} px must be at least 1 px each and divide {PATTERN} px "
                             f"(e.g. [8, 8], [6, 10], [16, 16])")
        if part["edge"]:
            raise ValueError(f"{where}: a dashed line cannot have an edge")
        dash = [on, off]
    return {"color": color.upper(), "width": width, "edge": bool(part["edge"]), "edge_color": edge_color.upper(),
            "dash": dash}


def clean(look, device="watch"):
    """The look with every value checked and completed with the defaults of this kind of device; raises ValueError."""
    base = DEFAULTS[model(device)]
    look = look if isinstance(look, dict) else {}
    out = {}
    for mode in MODES:
        # the night look follows the day look unless it is given
        src = look.get(mode) if isinstance(look.get(mode), dict) else (
            look.get("day") if mode == "night" and isinstance(look.get("day"), dict) else {})
        out[mode] = {kind: _one(src.get(kind), base[mode][kind], f"{mode} {kind} roads") for kind in KINDS}
    for kind in KINDS:   # a TYP line has one shape: only the colour can change at night
        day = out["day"][kind]
        out["night"][kind].update(width=day["width"], edge=day["edge"], dash=day["dash"])
    return out


def load(path, device="watch"):
    """A look from a JSON file (only the values that differ from the default are needed)."""
    with open(path, encoding="utf-8") as f:
        return clean(json.load(f), device)
