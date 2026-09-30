"""Area covered by an export = union of its boundary polygons.

Used to decide whether an older segment lies in the area that a newer export
describes. More reliable than the bounding box: two nearby cities have
overlapping rectangles even though their areas do not overlap.
"""

import math

CELL = 0.02  # degrees, spatial index cell size


def _cells(w, s, e, n):
    for i in range(math.floor(w / CELL), math.floor(e / CELL) + 1):
        for j in range(math.floor(s / CELL), math.floor(n / CELL) + 1):
            yield i, j


def _in_ring(x, y, ring):
    """Ray-casting point-in-polygon test."""
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


class Coverage:
    def __init__(self, rings):
        self.rings = []
        self.grid = {}
        for ring in rings:
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            bb = (min(xs), min(ys), max(xs), max(ys))
            idx = len(self.rings)
            self.rings.append((bb, ring))
            for c in _cells(*bb):
                self.grid.setdefault(c, []).append(idx)

    def __bool__(self):
        return bool(self.rings)

    def contains(self, x, y):
        for idx in self.grid.get((math.floor(x / CELL), math.floor(y / CELL)), ()):
            (w, s, e, n), ring = self.rings[idx]
            if w <= x <= e and s <= y <= n and _in_ring(x, y, ring):
                return True
        return False

    def contains_line(self, coords):
        """True if the middle vertex of the line lies inside the area."""
        x, y = coords[len(coords) // 2]
        return self.contains(x, y)
