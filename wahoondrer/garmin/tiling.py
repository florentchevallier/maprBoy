"""Cut an area into map tiles: mkgmap stops when one tile holds more than 16 MB of drawing data (RGN).

The overlay of four large Wandrer exports (about 230 x 120 km) went over it in one tile; a 10 km square of
central Munich took 2.4 MB in the base map. Cells of 0.15 x 0.1 degrees (about 11 x 11 km at 48 N) stay far
below the limit. A way crossing a cell border is kept whole in each cell it touches ("smart" strategy of
osmium extract): drawn twice at the same place, which does not show."""
import math
import os
import subprocess

TILE_LAT, TILE_LON = 0.1, 0.15


class TilingError(RuntimeError):
    pass


def parse_bbox(text):
    """(W, S, E, N) from "W,S,E,N"."""
    try:
        w, s, e, n = (float(v) for v in text.split(","))
    except ValueError:
        raise ValueError("--bbox wants W,S,E,N in degrees, e.g. 11.2,47.9,11.9,48.3")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("--bbox: west < east and south < north, in degrees")
    return w, s, e, n


def grid(bbox):
    """[(W, S, E, N)] cells of TILE_LAT x TILE_LON covering bbox, aligned on the grid."""
    w, s, e, n = bbox

    def edges(lo, hi, step):
        out, k = [lo], math.floor(lo / step + 1e-9) + 1
        while round(k * step, 9) < hi:
            out.append(round(k * step, 9))
            k += 1
        return out + [hi]

    xs, ys = edges(w, e, TILE_LON), edges(s, n, TILE_LAT)
    return [(xs[i], ys[j], xs[i + 1], ys[j + 1]) for j in range(len(ys) - 1) for i in range(len(xs) - 1)]


def osmium(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise TilingError("osmium not found (it comes with the maprBoy environment)")
    if r.returncode != 0:
        raise TilingError(f"osmium {cmd[1]} failed:\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def _empty(path):
    out = osmium(["osmium", "fileinfo", "-e", "-g", "data.count.nodes", path])
    return out.strip() in ("", "0")


BATCH = 32      # cells per reading of the file: about 12 MB each once renumbered (a 230 x 120 km overlay = 264 cells)


def split(path, bbox, work, prefix="tile"):
    """Cut the OSM file `path` into the grid cells of bbox (osmium extract with a config file, BATCH cells per
    reading). The ids are first renumbered from 1: with the ids of real OSM data (up to 13 billion) or of our
    lines (from 50 billion) osmium needs gigabytes for EACH cell; renumbered, a few megabytes. The map does
    not use the ids. Returns the non-empty cell files."""
    import json
    cells = grid(bbox)
    names = [f"{prefix}{i:03d}.osm.pbf" for i in range(len(cells))]
    src = os.path.join(work, f"{prefix}-all.osm.pbf")
    osmium(["osmium", "renumber", path, "-o", src, "--overwrite"])
    for start in range(0, len(cells), BATCH):
        conf = os.path.join(work, f"{prefix}-cells{start}.json")
        with open(conf, "w", encoding="utf-8") as f:
            json.dump({"directory": work, "extracts": [{"output": n, "bbox": list(c)} for n, c in
                                                       zip(names[start:start + BATCH], cells[start:start + BATCH])]}, f)
        osmium(["osmium", "extract", "-c", conf, "-s", "smart", src, "--overwrite"])
    return [p for p in (os.path.join(work, n) for n in names) if not _empty(p)]
