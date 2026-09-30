"""Processing applied to the merged segments before writing OSM.

1. filter_by_distance: keep unvisited roads only within a radius of ridden
   ("traveled") roads. With no traveled road nearby everything is untraveled,
   so nothing from Wandrer is added there.
2. merge_chains: merge chained segments of the same class.
3. simplify: Douglas-Peucker in metres (end points are kept).

Distances use a single local projection (cos(48.5 deg)): the error is about
2 % in Bavaria / Austria / Paris, negligible here. The distance filter works on
a 50 m grid: accuracy is about +-50 m.
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from .kmz import Segment

LAT0 = 48.5
M_LAT = 110540.0
M_LON = 111320.0 * math.cos(math.radians(LAT0))
CELL_M = 50.0


@dataclass
class Options:
    radius_m: float = 250.0        # 0 = no filter: keep all unvisited roads
    unpaved_always: bool = False   # keep unpaved roads whatever the radius
    paved_always: bool = False     # keep paved roads whatever the radius
    simplify_m: float = 1.5        # 0 = no simplification
    merge: bool = True
    max_way_points: int = 1000


def _xy(p):
    return p[0] * M_LON, p[1] * M_LAT


def _densify(coords, step):
    """Evenly spaced points along a line (projected metres)."""
    pts = [_xy(coords[0])]
    for a, b in zip(coords, coords[1:]):
        ax, ay = _xy(a)
        bx, by = _xy(b)
        d = math.hypot(bx - ax, by - ay)
        n = int(d // step)
        for k in range(1, n + 1):
            t = k * step / d
            pts.append((ax + (bx - ax) * t, ay + (by - ay) * t))
        pts.append((bx, by))
    return pts


def _cell(x, y):
    return int(x // CELL_M), int(y // CELL_M)


def filter_by_distance(segments, radius_m, unpaved_always=False, paved_always=False):
    """segments: list of Segment. Return (filtered list, stats)."""
    visited = [s for s in segments if s.state == "visited"]
    vcells = set()
    for s in visited:
        for x, y in _densify(s.coords, CELL_M / 2):
            vcells.add(_cell(x, y))

    r = int(math.ceil(radius_m / CELL_M))
    offsets = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1)
               if math.hypot(dx, dy) <= radius_m / CELL_M]
    near = set()
    for cx, cy in vcells:
        for dx, dy in offsets:
            near.add((cx + dx, cy + dy))

    kept = []
    dropped = 0
    for s in segments:
        if s.state == "visited":
            kept.append(s)
            continue
        if (unpaved_always and s.unpaved) or (paved_always and not s.unpaved):
            kept.append(s)
            continue
        if any(_cell(x, y) in near for x, y in _densify(s.coords, 100.0)):
            kept.append(s)
        else:
            dropped += 1
    return kept, dict(dropped=dropped, visited=len(visited))


def _rk(p):
    return round(p[0], 6), round(p[1], 6)


def merge_chains(segments, max_points=1000):
    """Merge segments of the same class that meet at a node where exactly two
    segments of that class end."""
    by_class = defaultdict(list)
    for s in segments:
        by_class[(s.state, s.unpaved)].append(s)

    out = []
    for (state, unpaved), lst in by_class.items():
        ends = defaultdict(list)
        for i, s in enumerate(lst):
            ends[_rk(s.coords[0])].append(i)
            ends[_rk(s.coords[-1])].append(i)
        used = [False] * len(lst)

        def other(node, i):
            l = ends[node]
            if len(l) != 2:
                return None
            j = l[0] if l[1] == i else l[1]
            if j == i or used[j]:
                return None
            return j

        def extend(chain, cur):
            while len(chain) < max_points:
                node = _rk(chain[-1])
                j = other(node, cur)
                if j is None:
                    break
                c = lst[j].coords
                c = c if _rk(c[0]) == node else c[::-1]
                chain.extend(c[1:])
                used[j] = True
                cur = j

        for i, s in enumerate(lst):
            if used[i]:
                continue
            used[i] = True
            chain = list(s.coords)
            extend(chain, i)
            chain.reverse()
            extend(chain, i)
            out.append(Segment(chain, state, unpaved))
    return out


def _dp(pts, tol):
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = pts[a]
        bx, by = pts[b]
        dx, dy = bx - ax, by - ay
        L2 = dx * dx + dy * dy
        best, idx = -1.0, -1
        for k in range(a + 1, b):
            px, py = pts[k]
            if L2 == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L2))
                d = math.hypot(px - ax - t * dx, py - ay - t * dy)
            if d > best:
                best, idx = d, k
        if best > tol:
            keep[idx] = True
            stack += [(a, idx), (idx, b)]
    return keep


def simplify(segments, tol_m):
    out = []
    for s in segments:
        if len(s.coords) <= 2:
            out.append(s)
            continue
        keep = _dp([_xy(p) for p in s.coords], tol_m)
        out.append(Segment([p for p, k in zip(s.coords, keep) if k],
                           s.state, s.unpaved))
    return out


def process(segments, opts, with_traveled=False):
    """segments: dict key -> Segment. Return (dict index -> Segment, stats)."""
    segs = list(segments.values())
    stats = dict(input=len(segs))
    if opts.radius_m and opts.radius_m > 0:
        segs, st = filter_by_distance(segs, opts.radius_m, opts.unpaved_always, opts.paved_always)
        stats["dropped_far"] = st["dropped"]
    if not with_traveled:
        segs = [s for s in segs if s.state != "visited"]
    stats["before_merge"] = len(segs)
    if opts.merge:
        segs = merge_chains(segs, opts.max_way_points)
    stats["after_merge"] = len(segs)
    nodes_before = sum(len(s.coords) for s in segs)
    if opts.simplify_m and opts.simplify_m > 0:
        segs = simplify(segs, opts.simplify_m)
    stats["nodes_before_simplify"] = nodes_before
    stats["nodes_after_simplify"] = sum(len(s.coords) for s in segs)
    return dict(enumerate(segs)), stats
