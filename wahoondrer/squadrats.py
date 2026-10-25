"""Squadrats layer: outlines of the squares NOT yet explored.

Input: a Squadrats KML export. It contains the *explored* squares, dissolved
into polygons (with holes), in several placemarks:
    squadrats      z14 grid ("big" squares, ~1.6 km at 48 N)
    squadratinhos  z17 grid ("small" squares, ~200 m)
(the other placemarks - yard, ubersquadrat... - are ignored). The declared
`size` of each placemark equals the number of squares rebuilt here.

Output: polylines that outline the unexplored squares. By default ("all") every
unexplored square of the chosen z8 tiles is outlined, so that each new square is
seen when riding somewhere new (as the Squadrats overlay of the Karoo does); the
small squares only show from zoom 14 on the Wahoo (tag-mapping) and from a closer
zoom on a Garmin. "band" keeps only the squares within `buffer` squares of the
explored ones. Every grid edge is written once, and collinear edges are merged into
one long straight way (2 nodes), so the layer stays light.
"""

import math
import xml.etree.ElementTree as ET

LEVELS = {"big": ("squadrats", 14), "small": ("squadratinhos", 17)}
NS = "{http://www.opengis.net/kml/2.2}"
TILE_ZOOM = 8


def _tx(lon, z):
    return (lon + 180.0) / 360.0 * 2 ** z


def _ty(lat, z):
    a = math.radians(lat)
    return (1 - math.log(math.tan(a) + 1 / math.cos(a)) / math.pi) / 2 * 2 ** z


def _lon(x, z):
    return x / 2 ** z * 360.0 - 180.0


def _lat(y, z):
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / 2 ** z))))


def _explored_cells(pm, z):
    """Rebuild the set of explored (x, y) cells from the dissolved polygons.

    Vertices lie on the grid, so an even-odd scan over the vertical edges of
    all rings (outer rings and holes) gives the cells row by row.
    """
    toggles = {}
    for ring in pm.iter(NS + "LinearRing"):
        pts = []
        for item in ring.find(NS + "coordinates").text.split():
            lon, lat = item.split(",")[:2]
            pts.append((round(_tx(float(lon), z)), round(_ty(float(lat), z))))
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if x1 == x2 and y1 != y2:
                for y in range(min(y1, y2), max(y1, y2)):
                    toggles.setdefault(y, []).append(x1)
    cells = set()
    for y, xs in toggles.items():
        xs.sort()
        for a, b in zip(xs[0::2], xs[1::2]):
            cells.update((x, y) for x in range(a, b))
    return cells


def read_squadrats(path):
    """Return {"big": set(cells z14), "small": set(cells z17)} (explored)."""
    root = ET.parse(str(path)).getroot()
    by_name = {lv: name for lv, (name, _) in LEVELS.items()}
    out = {}
    for pm in root.iter(NS + "Placemark"):
        name = pm.find(NS + "name")
        name = name.text if name is not None else ""
        for lv, (kml_name, z) in LEVELS.items():
            if name == kml_name:
                out[lv] = _explored_cells(pm, z)
    return out


BIG_SCOPES = ("all", "band")
SMALL_SCOPES = ("all", "explored-big", "band", "both")


def unexplored_outlines(explored, level, buffer_cells, tiles,
                        parent_explored=None, scope="all"):
    """Polylines [(lon, lat), (lon, lat)] outlining unexplored squares.

    explored        set of explored cells at this level
    buffer_cells    band width (in squares) around the explored squares
    tiles           set of allowed z8 (x, y) tiles (None = no restriction)
    parent_explored explored cells of the big level (z14), used by the
                    "explored-big" scope of the small level
    scope           which unexplored squares get an outline:
                    "all"           every unexplored square of the tiles (default;
                                    without tiles, the band is used instead)
                    "band"          squares within buffer_cells of an explored one
                    small level only:
                    "explored-big"  every unexplored small square inside an
                                    explored big square (complete grid there)
                    "both"          "explored-big" and "band"
    """
    if scope not in (BIG_SCOPES if level == "big" else SMALL_SCOPES):
        raise ValueError(f"unknown scope {scope!r} for the {level} squares")
    if scope == "all" and tiles is None:
        scope = "band"
    z = LEVELS[level][1]
    shift = z - TILE_ZOOM

    def allowed(c):
        return tiles is None or (c[0] >> shift, c[1] >> shift) in tiles

    if scope == "all":                     # millions of small squares: scan the grid, no set of cells
        horiz, vert = _all_edges(explored, tiles, shift)
        return _merged_lines(horiz, vert, z)
    band = set()
    if scope in ("band", "both"):
        seed = {c for c in explored if allowed(c)}
        k = buffer_cells
        for (x, y) in seed:
            for dx in range(-k, k + 1):
                for dy in range(-k, k + 1):
                    band.add((x + dx, y + dy))
    if level == "small" and scope in ("explored-big", "both"):
        if parent_explored is None:
            raise ValueError("scope 'explored-big' needs the explored big squares")
        n = 2 ** (LEVELS["small"][1] - LEVELS["big"][1])
        for (px, py) in parent_explored:
            if not allowed((px * n, py * n)):
                continue
            for dx in range(n):
                for dy in range(n):
                    band.add((px * n + dx, py * n + dy))
    band = {c for c in band if allowed(c)} - explored

    horiz, vert = {}, {}   # horizontal edges by row y -> x list; vertical by x -> y list
    hset, vset = set(), set()
    for (x, y) in band:
        hset.update(((x, y), (x, y + 1)))
        vset.update(((x, y), (x + 1, y)))
    for x, y in hset:
        horiz.setdefault(y, []).append(x)
    for x, y in vset:
        vert.setdefault(x, []).append(y)
    return _merged_lines(horiz, vert, z)


def _all_edges(explored, tiles, shift):
    """Edges of every unexplored square of the z8 tiles: {y: [x...]} (horizontal), {x: [y...]} (vertical).
    An edge is drawn when one of the two squares it separates is unexplored and inside the tiles."""
    n = 2 ** shift

    def unexplored(x, y):
        return (x >> shift, y >> shift) in tiles and (x, y) not in explored

    horiz, vert = {}, {}
    for (tx, ty) in tiles:
        x0, y0 = tx * n, ty * n
        last_y = y0 + n + (0 if (tx, ty + 1) in tiles else 1)    # a shared border belongs to the tile below
        for y in range(y0, last_y):
            row = horiz.setdefault(y, [])
            row.extend(x for x in range(x0, x0 + n) if unexplored(x, y - 1) or unexplored(x, y))
        last_x = x0 + n + (0 if (tx + 1, ty) in tiles else 1)
        for x in range(x0, last_x):
            col = vert.setdefault(x, [])
            col.extend(y for y in range(y0, y0 + n) if unexplored(x - 1, y) or unexplored(x, y))
    return horiz, vert


def _merged_lines(horiz, vert, z):
    """Polylines of the edges, collinear neighbours merged into one straight way (2 nodes)."""
    lines = []

    def runs(values):
        values.sort()
        start = prev = values[0]
        for v in values[1:]:
            if v != prev + 1:
                yield start, prev + 1
                start = v
            prev = v
        yield start, prev + 1

    for y, xs in horiz.items():
        if not xs:
            continue
        for a, b in runs(xs):
            lines.append([(_lon(a, z), _lat(y, z)), (_lon(b, z), _lat(y, z))])
    for x, ys in vert.items():
        if not ys:
            continue
        for a, b in runs(ys):
            lines.append([(_lon(x, z), _lat(a, z)), (_lon(x, z), _lat(b, z))])
    return lines
