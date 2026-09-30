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
    * an old segment that is absent from a newer export COVERING it is removed
      (it was ridden, or the OSM geometry changed), but only for the states the
      newer export can describe:
        - an "unvisited" segment is removed only if the export has Untraveled;
        - a "visited" segment is removed only if the export has Traveled;
- an export's coverage = union of its boundary polygons (falls back to the
  bounding box if the export has no boundaries);
- boundaries are deduplicated by ring key; two neighbouring sectors sharing a
  border each keep their own trace (the overlap is intended).

The activity (bike / foot) is read from the inner KML file name; mixing
activities is left to the user (a warning is printed).
"""

import os
import re
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .coverage import Coverage

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
    coverage: object = None  # Coverage (boundaries) or None


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
    exp.coverage = Coverage(exp.boundaries.values())
    return exp


def _covers(exp, coords):
    if exp.coverage:
        return exp.coverage.contains_line(coords)
    return _inside(coords, exp.bbox)


def _inside(coords, bbox):
    w, s, e, n = bbox
    return all(w <= x <= e and s <= y <= n for x, y in coords)


def merge_exports(exports):
    """Merge exports, sorted here from oldest to newest
    (KML timestamp; given order as a fallback).

    Return (segments, boundaries, report).
    """
    segments = {}
    boundaries = {}
    report = []
    exports = sorted(exports, key=lambda e: e.timestamp)  # stable sort
    for exp in exports:
        replaced = removed = added = 0
        # 1) remove old segments that are covered by this export but absent from it
        for k in list(segments):
            if k in exp.segments:
                continue
            seg = segments[k]
            if not _covers(exp, seg.coords):
                continue
            knows = exp.has_untraveled if seg.state == "unvisited" else exp.has_traveled
            if knows:
                del segments[k]
                removed += 1
        # 2) add / replace
        for k, seg in exp.segments.items():
            old = segments.get(k)
            if old is None:
                added += 1
            elif (old.state, old.unpaved) != (seg.state, seg.unpaved):
                replaced += 1
            segments[k] = seg
        # 3) boundaries
        nb = 0
        for k, ring in exp.boundaries.items():
            if k not in boundaries:
                nb += 1
            boundaries[k] = ring
        report.append(
            dict(export=exp.name, segments=len(exp.segments), added=added,
                 state_changed=replaced, removed_stale=removed,
                 new_boundaries=nb))
    return segments, boundaries, report
