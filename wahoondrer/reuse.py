"""Can the classic tiles of the last run be reused ("--skip-classic")?

They can only be if nothing that goes into them has changed. After every full run, build_tiles.py stores a
small "signature" of what the tiles were built from; the next run with --skip-classic compares it with the
current settings and falls back to a full run, with a clear message, when they differ.
"""
import glob
import hashlib
import os

VERSION = 1


def config_hash(paths):
    """One hash for the content of the POI / tag files (missing files count as empty)."""
    h = hashlib.sha1()
    for p in sorted(paths):
        h.update(os.path.basename(p).encode())
        try:
            with open(p, "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(b"<missing>")
    return h.hexdigest()


def osm_files(dl_dir, countries):
    """Downloaded OSM files of the regions the tiles need: {name: [size, mtime]}. Falls back to all of them."""
    found = {}
    every = {}
    for p in glob.glob(os.path.join(dl_dir, "**", "*.pbf"), recursive=True):
        st = os.stat(p)
        entry = [st.st_size, int(st.st_mtime)]
        every[os.path.relpath(p, dl_dir)] = entry
        base = os.path.basename(p).lower()
        if any(base.startswith(c.split("/")[-1].lower()) for c in countries):
            found[os.path.relpath(p, dl_dir)] = entry
    return found or every


def signature(tiles, contour_on, spacing, per_tile, config_paths, dl_dir):
    keys = {f"{t['x']}/{t['y']}": ("off" if not contour_on else per_tile.get(f"{t['x']}/{t['y']}", spacing))
            for t in tiles}
    countries = {c for t in tiles for c in t["countries"]}
    return {"version": VERSION, "tiles": keys, "config": config_hash(config_paths),
            "osm": osm_files(dl_dir, countries)}


def stale_reason(cur, old):
    """Why the earlier classic tiles cannot be reused, or None when they can."""
    if not old or old.get("version") != VERSION:
        return "no earlier full run is recorded"
    new_tiles = sorted(k for k in cur["tiles"] if k not in old["tiles"])
    if new_tiles:
        return "tile(s) never built before: " + ", ".join(new_tiles)
    diff = sorted(k for k in cur["tiles"] if cur["tiles"][k] != old["tiles"][k])
    if diff:
        return "the contour lines changed for: " + ", ".join(diff)
    if cur["config"] != old["config"]:
        return "the POI settings changed"
    changed = sorted(n for n, v in cur["osm"].items() if old["osm"].get(n) != v)
    if changed:
        return "the OpenStreetMap data changed (" + ", ".join(changed[:3]) + (", ..." if len(changed) > 3 else "") + ")"
    return None
