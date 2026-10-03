"""Read Wandrer KMZ/KML exports and merge several exports.

Model
-----
An export contains *segments* (LineStrings, one per Wandrer road section) with
a state, and *boundaries* (Polygons without styleUrl = achievement boundaries).

Merging several exports (exports overlap):
- a segment key is its coordinate sequence rounded to 1e-6, direction ignored;
  on real exports, 99.97 % of the geometries are identical from one export to
  the next, so this key is sufficient;
- the MOST RECENT export wins; an export's age comes from the timestamp in the
  KML (<Document><name>Wandrer Export YYYYMMDD-HH:MM:SS</name>), not from the
  file name:
    * same key -> the state of the newer export wins;
    * an old segment that is absent from a newer export is removed ONLY when the
      newer export REPLACES it, i.e. holds roads that lie on the old segment
      (Wandrer redrew the road after an OpenStreetMap update). Absence alone
      proves nothing: an export lists the boundaries of a much larger area than
      the roads it really contains, so "inside its boundaries" is not "described
      by it". Such segments are kept and counted (kept_absent);
    * removal also needs the same activity (bike / foot exports never remove each
      other's segments) and only concerns the states the newer export can
      describe (an "unvisited" segment only if the export has Untraveled, a
      "visited" one only if it has Traveled);
- boundaries are deduplicated by ring key; two neighbouring sectors sharing a
  border each keep their own trace (the overlap is intended).

The activity (bike / foot) is read from the inner KML file name; mixing
activities is left to the user (a warning is printed).
"""

import math
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

KML_STYLE_STATE = {
    "Traveled": ("visited", False),
    "Untraveled": ("unvisited", False),
    "UntraveledUnpaved": ("unvisited", True),
}

ROUND = 6


@dataclass
class Segment:
    coords: list          # [(lon, lat), ...] original coordinates
    state: str            # "visited" | "unvisited"
    unpaved: bool = False


@dataclass
class Export:
    name: str
    segments: dict = field(default_factory=dict)   # key -> Segment
    boundaries: dict = field(default_factory=dict)  # key -> [(lon, lat), ...]
    bbox: tuple = (1e9, 1e9, -1e9, -1e9)            # W, S, E, N
    has_traveled: bool = False
    has_untraveled: bool = False
    activity: str = ""       # "bike" | "foot" | "combined" ... | "" (from the inner KML name)
    timestamp: str = ""      # "AAAAMMJJ-HH:MM:SS" (nom du Document)


def _key(coords):
    c = tuple((round(x, ROUND), round(y, ROUND)) for x, y in coords)
    return min(c, c[::-1])


def _parse_coords(text):
    out = []
    for item in (text or "").split():
        parts = item.split(",")
        if len(parts) >= 2:
            out.append((float(parts[0]), float(parts[1])))
    return out


def _kml_bytes(path):
    path = str(path)
    if path.lower().endswith(".kml"):
        with open(path, "rb") as f:
            return f.read(), os.path.basename(path)
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".kml")]
        if not names:
            raise RuntimeError(f"No KML file in {path}")
        return z.read(names[0]), names[0]


def read_export(path):
    """Read a Wandrer KMZ/KML file and return an Export."""
    exp = Export(name=str(path))
    data, inner = _kml_bytes(path)
    root = ET.fromstring(data)
    m = re.search(r"wandrer-([a-z]+)-", inner.lower())   # bike, foot, combined...
    exp.activity = m.group(1) if m else ""
    doc_name = root.find("{*}Document/{*}name")
    m = re.search(r"(\d{8}-\d{2}:\d{2}:\d{2})", (doc_name.text or "") if doc_name is not None else "")
    exp.timestamp = m.group(1) if m else ""
    w, s, e, n = exp.bbox
    for pm in root.iterfind(".//{*}Placemark"):
        style = pm.find("{*}styleUrl")
        style = style.text.strip().lstrip("#") if style is not None else None
        if style in KML_STYLE_STATE:
            state, unpaved = KML_STYLE_STATE[style]
            if state == "visited":
                exp.has_traveled = True
            else:
                exp.has_untraveled = True
            for ls in pm.iterfind(".//{*}LineString/{*}coordinates"):
                c = _parse_coords(ls.text)
                if len(c) < 2:
                    continue
                exp.segments[_key(c)] = Segment(c, state, unpaved)
                for x, y in c:
                    w, s, e, n = min(w, x), min(s, y), max(e, x), max(n, y)
        elif style is None:
            for ring in pm.iterfind(".//{*}Polygon//{*}LinearRing/{*}coordinates"):
                c = _parse_coords(ring.text)
                if len(c) >= 4:
                    exp.boundaries[_key(c)] = c
    exp.bbox = (w, s, e, n)
    return exp


# --- does a newer export REPLACE an older segment? ---------------------------
# Local projection (same constants as process.py, ~2 % error in Bavaria).
_M_LAT = 110540.0
_M_LON = 111320.0 * math.cos(math.radians(48.5))
FINE_M = 4.0        # cell of the "roads of the newer export" index
MATCH_STEP_M = 4.0  # sampling step along the old segment
COARSE_M = 100.0    # cell used to skip the parts of the newer export far from any candidate
MATCH_FRACTION = 0.9
BBOX_MARGIN_M = 100.0   # candidates are searched in the bbox of the newer export + this margin


def _cell(lon, lat, size):
    return int(lon * _M_LON // size), int(lat * _M_LAT // size)


def _samples(coords, step):
    """Points every `step` metres along a line, ends and the middle included."""
    out = [coords[0]]
    for (ax, ay), (bx, by) in zip(coords, coords[1:]):
        d = math.hypot((bx - ax) * _M_LON, (by - ay) * _M_LAT)
        for k in range(1, int(d // step) + 1):
            t = k * step / d
            out.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        out.append((bx, by))
    if len(out) < 3:
        out.append(((coords[0][0] + coords[-1][0]) / 2, (coords[0][1] + coords[-1][1]) / 2))
    return out


def _near_cells(cells, lon, lat):
    cx, cy = _cell(lon, lat, FINE_M)
    return any((cx + i, cy + j) in cells for i in (-1, 0, 1) for j in (-1, 0, 1))


def _road_index(exp, candidates):
    """Fine cells covered by the roads of `exp`, only near the candidate segments."""
    coarse = set()
    for seg in candidates:
        for x, y in _samples(seg.coords, 50.0):
            cx, cy = _cell(x, y, COARSE_M)
            coarse.update((cx + i, cy + j) for i in (-1, 0, 1) for j in (-1, 0, 1))
    cells = set()
    for seg in exp.segments.values():
        if not any(_cell(x, y, COARSE_M) in coarse for x, y in _samples(seg.coords, 50.0)):
            continue
        for x, y in _samples(seg.coords, FINE_M / 2):
            cells.add(_cell(x, y, FINE_M))
    return cells


def _replaced(seg, cells):
    pts = _samples(seg.coords, MATCH_STEP_M)
    hit = sum(1 for x, y in pts if _near_cells(cells, x, y))
    need = len(pts) if len(pts) <= 4 else math.ceil(MATCH_FRACTION * len(pts))
    return hit >= need


def _compatible(a, b):
    return not a or not b or a == b


def merge_exports(exports):
    """Merge exports, sorted here from oldest to newest
    (KML timestamp; given order as a fallback).

    Return (segments, boundaries, report).
    """
    segments = {}
    origin = {}      # key -> activity of the export the segment comes from
    boundaries = {}
    report = []
    exports = sorted(exports, key=lambda e: e.timestamp)  # stable sort
    for exp in exports:
        replaced = removed = added = absent = 0
        # 1) old segments absent from this export: remove them only if the export replaces them
        w, s_, e_, n = exp.bbox
        w, e_ = w - BBOX_MARGIN_M / _M_LON, e_ + BBOX_MARGIN_M / _M_LON
        s_, n = s_ - BBOX_MARGIN_M / _M_LAT, n + BBOX_MARGIN_M / _M_LAT
        candidates = []
        for k, seg in segments.items():
            if k in exp.segments or not _compatible(origin.get(k), exp.activity):
                continue
            knows = exp.has_untraveled if seg.state == "unvisited" else exp.has_traveled
            if not knows:
                continue
            x, y = seg.coords[len(seg.coords) // 2]
            if w <= x <= e_ and s_ <= y <= n:
                candidates.append((k, seg))
        if candidates:
            cells = _road_index(exp, [seg for _, seg in candidates])
            for k, seg in candidates:
                if _replaced(seg, cells):
                    del segments[k]
                    origin.pop(k, None)
                    removed += 1
                else:
                    absent += 1
        # 2) add / replace
        for k, seg in exp.segments.items():
            old = segments.get(k)
            if old is None:
                added += 1
            elif (old.state, old.unpaved) != (seg.state, seg.unpaved):
                replaced += 1
            segments[k] = seg
            origin[k] = exp.activity
        # 3) boundaries
        nb = 0
        for k, ring in exp.boundaries.items():
            if k not in boundaries:
                nb += 1
            boundaries[k] = ring
        report.append(
            dict(export=exp.name, segments=len(exp.segments), added=added,
                 state_changed=replaced, removed_stale=removed, kept_absent=absent,
                 new_boundaries=nb))
    return segments, boundaries, report
