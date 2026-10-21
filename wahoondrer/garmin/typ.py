"""The TYP text file: how each Garmin line type is drawn.

Solid roads: LineWidth (+ BorderWidth for the white edge) and a "0 0 N 0" colour list, the form that was
checked on a Forerunner 955. Unpaved roads: a 32 px wide bitmap with dashes (no edge possible).
The night look is added to the colour list (day colours first, then night colours: 4 colours with an edge,
2 without) only when it differs from the day look, so the default file stays the one that was tested.
mkgmap has no NightXpm for lines. Dashed roads use the day colour at night (a bitmap has no night colours). The file given to mkgmap MUST end with .txt (see docs/GARMIN.md)."""
from . import style

FAMILY_ID = 1299    # as Wandrer's own maps; other ids were not tested
PRODUCT_ID = 1
DASH = 16           # pixels drawn, then the same number left empty
EDGE_COLOR = "#FFFFFF"
LABELS = {
    style.TYPE_UNVISITED: "Unvisited",
    style.TYPE_VISITED: "Visited",
    style.TYPE_UNVISITED_UNPAVED: "Unvisited (unpaved)",
    style.TYPE_VISITED_UNPAVED: "Visited (unpaved)",
    style.TYPE_BOUNDARY: "Boundary",
}


def _solid(day, night):
    """The Xpm lines of a solid road: day colour (+ edge), then the night ones when they differ."""
    colors = [day["color"]] + ([EDGE_COLOR] if day["edge"] else [])
    if night != day:
        colors += [night["color"]] + ([EDGE_COLOR] if night["edge"] else [])
    return ['Xpm="0 0 %d 0"' % len(colors)] + [f'"{chr(97 + i)} c {c}"' for i, c in enumerate(colors)]


def _dashed(part):
    row = "a" * DASH + "." * DASH
    return ['Xpm="%d %d 2 1"' % (2 * DASH, part["width"]), f'"a c {part["color"]}"', '". c none"'] + \
           [f'"{row}"'] * part["width"]


def _element(type_, label, day, night, dashed):
    out = ["[_line]", f"Type=0x{type_:02x}", f"String={label}"]
    if dashed:
        out += _dashed(day)
    else:
        out.append(f"LineWidth={day['width']}")
        if day["edge"]:
            out.append("BorderWidth=1")
        out += _solid(day, night)
    out.append("[end]")
    return out


def typ_text(look, family_id=FAMILY_ID, product_id=PRODUCT_ID):
    """The TYP text for `look` (a checked look, see look.clean)."""
    out = ["[_id]", f"FID={family_id}", f"ProductCode={product_id}", "CodePage=1252", "[end]"]
    for type_, kind, dashed in (
            (style.TYPE_UNVISITED, "unvisited", False),
            (style.TYPE_UNVISITED_UNPAVED, "unvisited", True),
            (style.TYPE_VISITED, "visited", False),
            (style.TYPE_VISITED_UNPAVED, "visited", True),
            (style.TYPE_BOUNDARY, "boundary", False)):
        out += _element(type_, LABELS[type_], look["day"][kind], look["night"][kind], dashed)
    return "\n".join(out) + "\n"
