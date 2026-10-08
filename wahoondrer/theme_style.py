"""How the Wandrer roads look in the theme: colour, width and dark outline of the unvisited and of the
visited roads, once for the light styles (roam, bolt, bolt2) and once for the dark one (bolt2-dark).

The theme (themes/vtm/vtm-elemnt.xml) holds, for each style, a fill line and an outline line for the
unvisited roads ("wandrer-unvisited-*") and for the unpaved ones ("wandrer-unpaved-*", dashed, they follow
the unvisited setting), and the same for the visited roads ("wandrer-visited-*"). The outline is the fill
width plus OUTLINE_EXTRA. Without outline, its lines are not used by the rule; the visited outline is not
used by the repository theme, so it is added to the rule when asked for."""
import copy
import re

DEFAULT = {
    "light": {"unvisited": {"color": "#FF8800", "width": 1.5, "outline": True},
              "visited": {"color": "#2E9E4F", "width": 1.0, "outline": False}},
    "dark": {"unvisited": {"color": "#FF8800", "width": 1.5, "outline": True},
             "visited": {"color": "#4CCB7A", "width": 1.0, "outline": False}},
}
OUTLINE_EXTRA = 0.9
MIN_WIDTH, MAX_WIDTH = 0.1, 10.0
MODE_OF = {"roam": "light", "bolt": "light", "bolt2": "light", "bolt2-dark": "dark"}
EXPECTED_LINES = len(MODE_OF) * (2 * 2 + 2)   # unvisited + unpaved (fill, outline) and visited (fill, outline) per style

_CATS = "roam|bolt2-dark|bolt2|bolt"
_STYLE_LINE = re.compile(
    rf'<style-line id="wandrer-(?P<kind>unvisited|unpaved|visited)-(?P<outline>outline-)?(?P<cat>{_CATS})"[^>]*/>')
_UNVISITED_OUTLINE_USE = re.compile(
    rf'^[ \t]*<line use="wandrer-(?:unvisited|unpaved)-outline-(?P<cat>{_CATS})"\s*/>[ \t]*\r?\n', re.M)
_VISITED_USE = re.compile(rf'^(?P<indent>[ \t]*)<line use="wandrer-visited-(?P<cat>{_CATS})"\s*/>[ \t]*\r?\n', re.M)


def defaults():
    return copy.deepcopy(DEFAULT)


def _one(part, base, where):
    part = {**base, **(part if isinstance(part, dict) else {})}
    color = str(part["color"]).strip()
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        raise ValueError(f"{where}: colour {color!r} is not a #RRGGBB value")
    try:
        width = round(float(part["width"]), 2)
    except (TypeError, ValueError):
        raise ValueError(f"{where}: width {part['width']!r} is not a number")
    if not MIN_WIDTH <= width <= MAX_WIDTH:
        raise ValueError(f"{where}: width must be between {MIN_WIDTH:g} and {MAX_WIDTH:g}")
    return {"color": color.upper(), "width": width, "outline": bool(part["outline"])}


def clean(look):
    """The look with every value checked and completed with the defaults; raises ValueError with a readable message."""
    look = look if isinstance(look, dict) else {}
    out = {}
    for mode in DEFAULT:
        m = look.get(mode) if isinstance(look.get(mode), dict) else {}
        out[mode] = {kind: _one(m.get(kind), DEFAULT[mode][kind], f"{mode} {kind} roads") for kind in DEFAULT[mode]}
    return out


def is_default(look):
    return clean(look) == clean(DEFAULT)


def _set(line, name, value):
    """The line with attribute `name` set to `value`; left as written when it already means the same."""
    old = re.search(rf'\b{name}="([^"]*)"', line).group(1)
    same = float(old) == float(value) if name == "width" else old.upper() == value.upper()
    return line if same else re.sub(rf'\b{name}="[^"]*"', f'{name}="{value}"', line, count=1)


def apply(xml, look):
    """The theme text with the Wandrer roads drawn as `look` says."""
    look = clean(look)
    count = 0

    def change(m):
        nonlocal count
        count += 1
        kind = "visited" if m.group("kind") == "visited" else "unvisited"
        st = look[MODE_OF[m.group("cat")]][kind]
        line = m.group(0)
        if m.group("outline"):
            return _set(line, "width", f"{round(st['width'] + OUTLINE_EXTRA, 2):g}")
        return _set(_set(line, "stroke", st["color"]), "width", f"{st['width']:g}")

    out = _STYLE_LINE.sub(change, xml)
    if count != EXPECTED_LINES:
        raise ValueError(f"the theme has {count} Wandrer road lines instead of {EXPECTED_LINES}; "
                         "it changed and wahoondrer/theme_style.py must follow")
    out = _UNVISITED_OUTLINE_USE.sub(
        lambda m: "" if not look[MODE_OF[m.group("cat")]]["unvisited"]["outline"] else m.group(0), out)

    def visited_outline(m):
        if not look[MODE_OF[m.group("cat")]]["visited"]["outline"]:
            return m.group(0)
        i = m.group("indent")
        return f'{i}<line use="wandrer-visited-outline-{m.group("cat")}" />\n{m.group(0)}'

    return _VISITED_USE.sub(visited_outline, out)
