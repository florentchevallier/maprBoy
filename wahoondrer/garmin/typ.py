"""The TYP text file: how each Garmin line type is drawn.

Solid roads: LineWidth (+ BorderWidth for the white edge) and a "0 0 N 0" colour list, the form that was
checked on a Forerunner 955. Dashed roads: a 32 px wide bitmap, as high as the width (checked on the same
watch with 16+16 px dashes); it has no edge and uses the day colour at night.
The night look of a solid road is added to the colour list (day colours first, then night colours: 4 colours with an edge,
2 without) only when it differs from the day look, so the default file stays the one that was tested.
mkgmap has no NightXpm for lines. The file given to mkgmap MUST end with .txt (see docs/GARMIN.md)."""
from . import style

FAMILY_ID = 1299    # as Wandrer's own maps; other ids were not tested
PRODUCT_ID = 1
EDGE_COLOR = "#FFFFFF"
LABELS = {
    style.TYPE_UNVISITED: "Unvisited",
    style.TYPE_VISITED: "Visited",
    style.TYPE_UNVISITED_UNPAVED: "Unvisited (unpaved)",
    style.TYPE_VISITED_UNPAVED: "Visited (unpaved)",
    style.TYPE_BOUNDARY: "Boundary",
    style.TYPE_SQUADRAT_BIG: "Squadrat",
    style.TYPE_SQUADRAT_SMALL: "Squadratino",
}


def _solid(day, night):
    """The Xpm lines of a solid road: day colour (+ edge), then the night ones when they differ."""
    colors = [day["color"]] + ([EDGE_COLOR] if day["edge"] else [])
    if night != day:
        colors += [night["color"]] + ([EDGE_COLOR] if night["edge"] else [])
    return ['Xpm="0 0 %d 0"' % len(colors)] + [f'"{chr(97 + i)} c {c}"' for i, c in enumerate(colors)]


def _dashed(day):
    on, off = day["dash"]
    row = ("a" * on + "." * off) * (32 // (on + off))
    return ['Xpm="32 %d 2 1"' % day["width"], f'"a c {day["color"]}"', '". c none"'] + [f'"{row}"'] * day["width"]


def _element(type_, label, day, night):
    out = ["[_line]", f"Type=0x{type_:02x}", f"String={label}"]
    if day["dash"]:
        out += _dashed(day)
    else:
        out.append(f"LineWidth={day['width']}")
        if day["edge"]:
            out.append("BorderWidth=1")
        out += _solid(day, night)
    out.append("[end]")
    return out


def typ_text(look, family_id=FAMILY_ID, product_id=PRODUCT_ID, points=()):
    """The TYP text for `look` (a checked look, see look.clean); `points` = [_point] lines (see pois.py)."""
    out = ["[_id]", f"FID={family_id}", f"ProductCode={product_id}", "CodePage=1252", "[end]"]
    for type_, kind in (
            (style.TYPE_UNVISITED, "unvisited"),
            (style.TYPE_UNVISITED_UNPAVED, "unvisited_unpaved"),
            (style.TYPE_VISITED, "visited"),
            (style.TYPE_VISITED_UNPAVED, "visited_unpaved"),
            (style.TYPE_BOUNDARY, "boundary"),
            (style.TYPE_SQUADRAT_BIG, "squadrat_big"),
            (style.TYPE_SQUADRAT_SMALL, "squadrat_small")):
        out += _element(type_, LABELS[type_], look["day"][kind], look["night"][kind])
    out += list(points)
    return "\n".join(out) + "\n"
