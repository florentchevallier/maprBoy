"""KMZ exports -> .osm -> mkgmap -> gmapsupp.img, with the checks learned the hard way (docs/GARMIN.md)."""
import math
import os
import shutil
import struct
import subprocess
import tempfile

from . import look as looks
from . import style, typ

MIN_RGN_BYTES = 160     # an empty map (nothing matched the style) has an RGN of ~125 bytes; two short roads ~240
DEFAULT_MAPNAME = "12990001"


class GarminError(RuntimeError):
    pass


def _inside(coords, area):
    """True when one point of the line is within area = (lat, lon, km) of its centre (flat-earth distance)."""
    lat0, lon0, km = area
    kx = math.cos(math.radians(lat0)) * 111.32
    return any(((x - lon0) * kx) ** 2 + ((y - lat0) * 111.2) ** 2 <= km * km for x, y in coords)


def wandrer_osm(exports, osm_path, opts, boundaries=True, area=None, log=print):
    """Merge the exports (any order, the most recent wins) and write the .osm. Returns the write_osm statistics.

    Visited roads are always written: the Garmin overlay draws them too (cyan by default).
    `opts` is a process.Options. `area` = (lat, lon, km) keeps only what touches that circle."""
    from ..kmz import merge_exports, read_export
    from ..osmxml import write_osm
    from ..process import process
    exps = []
    for p in exports:
        log(f"Reading {p} ...")
        e = read_export(p)
        log(f"  {len(e.segments)} segments, {len(e.boundaries)} boundaries, activity={e.activity or '?'}")
        exps.append(e)
    if not exps:
        raise GarminError("no Wandrer export given")
    activities = {e.activity for e in exps if e.activity}
    if len(activities) > 1:
        log(f"WARNING: mixed activities ({', '.join(sorted(activities))}).")
    segments, bounds, _ = merge_exports(exps)
    if area:
        before = len(segments)
        segments = {k: v for k, v in segments.items() if _inside(v.coords, area)}
        bounds = {k: v for k, v in bounds.items() if _inside(v, area)}
        log(f"Area {area[2]:g} km around {area[0]:.5f}, {area[1]:.5f}: {before} -> {len(segments)} segments")
    segments, ps = process(segments, opts, True)
    log(f"Processing: {ps['input']} -> {ps['after_merge']} segments, {ps['nodes_after_simplify']} points")
    stats = write_osm(osm_path, segments, bounds if boundaries else {}, True)
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
        raise GarminError(f"the map is empty (RGN {rgn} bytes): no road matched the style")
    typs = [v for k, v in subs.items() if k.endswith(".TYP")]
    if not typs:
        raise GarminError("no TYP in the .img: the roads would be drawn in default colours")
    if b"[_id]" in d:
        raise GarminError("the TYP was copied as text instead of being compiled (file must end with .txt)")
    return subs


def build_img(osm_path, out_img, mkgmap_jar, look=None, mapname=DEFAULT_MAPNAME, description="maprBoy Wandrer",
              java="java", keep_dir=None, log=print):
    """Run mkgmap on `osm_path` and put the checked gmapsupp.img at `out_img`."""
    if not os.path.isfile(mkgmap_jar):
        raise GarminError(f"mkgmap jar not found: {mkgmap_jar}")
    if shutil.which(java) is None:
        raise GarminError("Java not found (mkgmap needs it)")
    look = looks.clean(look)
    work = keep_dir or tempfile.mkdtemp(prefix="maprboy-garmin-")
    try:
        os.makedirs(work, exist_ok=True)
        style_dir = style.write_style(os.path.join(work, "style"))
        typ_file = os.path.join(work, "wandrer.txt")      # .txt: required, see typ.py
        with open(typ_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(typ.typ_text(look))
        out_dir = os.path.join(work, "out")
        cmd = [java, "-jar", os.path.abspath(mkgmap_jar), f"--style-file={style_dir}",
               f"--family-id={typ.FAMILY_ID}", f"--product-id={typ.PRODUCT_ID}", "--family-name=maprBoy",
               "--gmapsupp", "--latin1", "--transparent", "--draw-priority=10000",
               f"--series-name={description}", f"--mapname={mapname}", f"--description={description}",
               f"--output-dir={out_dir}", typ_file, os.path.abspath(osm_path)]
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
