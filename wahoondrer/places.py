"""The biggest place of an area, from the OpenStreetMap extracts on disk: the name of the Garmin and Karoo maps.

Same order as the Wahoo tile names (scripts/build_tiles.py, parse_places): a city before a town, then the larger
population; a village only when the area holds no city or town. The English name when OSM has one ("Munich").
"""
import os
import re
import subprocess

RANK = {"city": 0, "town": 1, "village": 2}
_cache = {}                       # (path, mtime, size) -> [(rank, -population, name, lon, lat)]


def _dec(v):
    return re.sub(r"%([0-9a-fA-F]+)%", lambda m: chr(int(m.group(1), 16)), v)


def parse_opl(text):
    """[(rank, -population, name, lon, lat)] of the city / town / village nodes in OPL text."""
    out = []
    for line in text.splitlines():
        if not line.startswith("n"):
            continue
        tags, lon, lat = {}, None, None
        for tok in line.split(" "):
            if tok.startswith("T") and len(tok) > 1:
                for kv in tok[1:].split(","):
                    k, _, v = kv.partition("=")
                    tags[_dec(k)] = _dec(v)
            elif tok.startswith("x") and len(tok) > 1:
                lon = float(tok[1:])
            elif tok.startswith("y") and len(tok) > 1:
                lat = float(tok[1:])
        rank, name = RANK.get(tags.get("place")), tags.get("name:en") or tags.get("name")
        if rank is None or not name or lon is None or lat is None:
            continue
        try:
            pop = int(re.sub(r"[ ,.']", "", tags.get("population", "0")) or 0)
        except ValueError:
            pop = 0
        out.append((rank, -pop, name, lon, lat))
    return out


def _places(path):
    st = os.stat(path)
    key = (path, st.st_mtime, st.st_size)
    if key not in _cache:
        r = subprocess.run(["osmium", "tags-filter", "--no-progress", "-f", "opl", "-o", "-", path,
                            "n/place=city,town,village"], capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or "osmium tags-filter failed")
        if len(_cache) >= 8:                             # a few regions at a time: keep the memory small
            _cache.clear()
        _cache[key] = parse_opl(r.stdout)
    return _cache[key]


def biggest(pbf_files, bbox):
    """Name of the biggest place inside bbox (W, S, E, N) in the extracts that exist, or "" (none on disk, osmium
    missing, no place in the area)."""
    w, s, e, n = bbox
    found = []
    for f in pbf_files:
        if not os.path.isfile(f):
            continue
        try:
            found += [p for p in _places(f) if w <= p[3] <= e and s <= p[4] <= n]
        except (OSError, subprocess.SubprocessError):
            continue
    return min(found)[2] if found else ""
