"""KMZ exports -> .osm -> mkgmap -> gmapsupp.img, with the checks learned the hard way (docs/GARMIN.md)."""
import math
import os
import shutil
import struct
import subprocess
import tempfile

from . import look as looks
from . import style, typ

MIN_RGN_BYTES = 130     # an empty map (nothing matched the style) has an RGN of 125 bytes; one POI 143
DEFAULT_MAPNAME = "12990001"


class GarminError(RuntimeError):
    pass


def _inside(coords, area):
    """True when one point of the line is within area = (lat, lon, km) of its centre (flat-earth distance)."""
    lat0, lon0, km = area
    kx = math.cos(math.radians(lat0)) * 111.32
    return any(((x - lon0) * kx) ** 2 + ((y - lat0) * 111.2) ** 2 <= km * km for x, y in coords)


def exports_bbox(exports):
    """(W, S, E, N) around read exports."""
    return (min(e.bbox[0] for e in exports), min(e.bbox[1] for e in exports),
            max(e.bbox[2] for e in exports), max(e.bbox[3] for e in exports))


def area_bbox(area):
    """(W, S, E, N) around area = (lat, lon, km)."""
    lat, lon, km = area
    dlat, dlon = km / 111.2, km / (111.32 * math.cos(math.radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


def read_exports(paths, log=print):
    from ..kmz import read_export
    exps = []
    for p in paths:
        log(f"Reading {p} ...")
        e = read_export(p)
        log(f"  {len(e.segments)} segments, {len(e.boundaries)} boundaries, activity={e.activity or '?'}")
        exps.append(e)
    activities = {e.activity for e in exps if e.activity}
    if len(activities) > 1:
        log(f"WARNING: mixed activities ({', '.join(sorted(activities))}).")
    return exps


def squadrat_lines(kml, bbox, levels=("big", "small"), buffer_cells=3, scope="explored-big", area=None,
                   log=print):
    """{"big": [line, ...], "small": [...]}: outlines of the unexplored Squadrats squares in the z8 tiles that
    touch bbox (W, S, E, N), within buffer_cells of the explored ones (see squadrats.py); with `area`, only the
    lines that touch that circle."""
    from ..squadrats import read_squadrats, unexplored_outlines
    from ..tiles import tiles_for_bbox
    log(f"Reading Squadrats {kml} ...")
    explored = read_squadrats(kml)
    tiles = set(tiles_for_bbox(*bbox))
    out = {}
    for level in levels:
        if level not in explored:
            log(f"  level {level}: not found in the file, skipped")
            continue
        lines = unexplored_outlines(explored[level], level, buffer_cells, tiles,
                                    parent_explored=explored.get("big"), scope=scope)
        if area:
            lines = [l for l in lines if _inside(l, area)]
        out[level] = lines
        log(f"  {level}: {len(explored[level])} explored squares -> {len(lines)} outline lines")
    return out


def wandrer_osm(exports, osm_path, opts, boundaries=True, area=None, log=print, squadrats=None):
    """Merge the exports (paths or read exports, any order, the most recent wins) and write the .osm, with the
    Squadrats outlines when given. Returns the write_osm statistics.

    Visited roads are always written: the Garmin overlay draws them too (magenta by default).
    `opts` is a process.Options. `area` = (lat, lon, km) keeps only what touches that circle.
    No exports: only the Squadrats lines are written."""
    from ..kmz import merge_exports
    from ..osmxml import write_osm
    from ..process import process
    exps = [e for e in exports if not isinstance(e, str)] + read_exports([e for e in exports if isinstance(e, str)], log)
    if not exps and not squadrats:
        raise GarminError("no Wandrer export given")
    if not exps:
        stats = write_osm(osm_path, {}, {}, True, squadrats)
        log(f"Wrote {osm_path}: {stats['ways']} ways, {stats['nodes']} nodes")
        return stats
    segments, bounds, _ = merge_exports(exps)
    if area:
        before = len(segments)
        segments = {k: v for k, v in segments.items() if _inside(v.coords, area)}
        bounds = {k: v for k, v in bounds.items() if _inside(v, area)}
        log(f"Area {area[2]:g} km around {area[0]:.5f}, {area[1]:.5f}: {before} -> {len(segments)} segments")
    segments, ps = process(segments, opts, True)
    log(f"Processing: {ps['input']} -> {ps['after_merge']} segments, {ps['nodes_after_simplify']} points")
    stats = write_osm(osm_path, segments, bounds if boundaries else {}, True, squadrats)
    stats["bbox"] = exports_bbox(exps)
    log(f"Wrote {osm_path}: {stats['ways']} ways, {stats['nodes']} nodes")
    return stats


def read_img(path):
    """The sub-files of a Garmin .img: {"NAME.EXT": size}. Enough to check a build, not a full parser."""
    with open(path, "rb") as f:
        d = f.read()
    xor = d[0]
    if xor:
        d = bytes(b ^ xor for b in d)
    if d[0x10:0x17] != b"DSKIMG\0":
        raise GarminError(f"{path} is not a Garmin .img")
    out = {}
    # the FAT starts at 0x200 or later; its blank-name entry holds the size of header + FAT, i.e. where
    # the data starts. A large sub-file spans several entries: only the first one (part 0) holds its size.
    data_start = len(d)
    off = 0x200
    while off + 512 <= min(data_start, len(d)):
        e = d[off:off + 512]
        if e[0] == 1:
            base, ext = e[1:9].decode("latin1").strip(), e[9:12].decode("latin1").strip()
            size = struct.unpack("<I", e[12:16])[0]
            if not base and not ext:
                data_start = size
            elif struct.unpack("<H", e[0x10:0x12])[0] == 0:
                out[f"{base}.{ext}"] = size
        off += 512
    return out, d


def check_img(path):
    """Raise GarminError when the build is empty or the TYP was not compiled. Returns the sub-file sizes."""
    subs, d = read_img(path)
    rgn = sum(v for k, v in subs.items() if k.endswith(".RGN"))
    if rgn < MIN_RGN_BYTES:
        raise GarminError(f"the map is empty (RGN {rgn} bytes): nothing matched the style")
    typs = [v for k, v in subs.items() if k.endswith(".TYP")]
    if not typs:
        raise GarminError("no TYP in the .img: the roads would be drawn in default colours")
    if b"[_id]" in d:
        raise GarminError("the TYP was copied as text instead of being compiled (file must end with .txt)")
    return subs


NO_LABEL_TAG = "maprboy:nolabel"   # mkgmap reads labels from this tag only: it never exists, so no names


def build_img(osm_path, out_img, mkgmap_jar, look=None, mapname=DEFAULT_MAPNAME, description="maprBoy Wandrer",
              java="java", keep_dir=None, log=print, poi_rules="", poi_typ=()):
    """Run mkgmap on `osm_path` (a path or a list: Wandrer .osm, POI .osm.pbf) and put the checked
    gmapsupp.img at `out_img`. poi_rules / poi_typ: the points style and the TYP [_point] lines (pois.py)."""
    inputs = [osm_path] if isinstance(osm_path, str) else list(osm_path)
    if not os.path.isfile(mkgmap_jar):
        raise GarminError(f"mkgmap jar not found: {mkgmap_jar}")
    if shutil.which(java) is None:
        raise GarminError("Java not found (mkgmap needs it)")
    look = looks.clean(look)
    work = keep_dir or tempfile.mkdtemp(prefix="maprboy-garmin-")
    try:
        os.makedirs(work, exist_ok=True)
        style_dir = style.write_style(os.path.join(work, "style"), points=poi_rules)
        typ_file = os.path.join(work, "wandrer.txt")      # .txt: required, see typ.py
        with open(typ_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(typ.typ_text(look, points=poi_typ))
        out_dir = os.path.join(work, "out")
        cmd = [java, "-jar", os.path.abspath(mkgmap_jar), f"--style-file={style_dir}",
               f"--family-id={typ.FAMILY_ID}", f"--product-id={typ.PRODUCT_ID}", "--family-name=maprBoy",
               "--gmapsupp", "--latin1", "--transparent", "--draw-priority=10000",
               "--add-pois-to-areas", f"--name-tag-list={NO_LABEL_TAG}",
               f"--series-name={description}", f"--mapname={mapname}", f"--description={description}",
               f"--output-dir={out_dir}", typ_file] + [os.path.abspath(p) for p in inputs]
        log("Running mkgmap ...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise GarminError("mkgmap failed:\n" + (r.stderr or r.stdout)[-2000:])
        built = os.path.join(out_dir, "gmapsupp.img")
        if not os.path.isfile(built):
            raise GarminError("mkgmap produced no gmapsupp.img:\n" + (r.stdout or r.stderr)[-2000:])
        subs = check_img(built)
        os.makedirs(os.path.dirname(os.path.abspath(out_img)), exist_ok=True)
        shutil.copyfile(built, out_img)
        log(f"Wrote {out_img} ({os.path.getsize(out_img) // 1024} KB, RGN {sum(v for k, v in subs.items() if k.endswith('.RGN'))} bytes)")
        return out_img
    finally:
        if keep_dir is None:
            shutil.rmtree(work, ignore_errors=True)
