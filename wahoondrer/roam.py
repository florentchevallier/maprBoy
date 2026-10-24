"""ELEMNT ROAM v1: the same tiles as the other ELEMNTs, only gzip-compressed.

Tested in October 2026 on a borrowed ROAM v1: its tiles are maps/tiles/8/<x>/<y>.map.gz, the same
mapsforge binary as our .map.lzma (the name inside the gzip is <y>.map), without the .17 marker. The VTM
rendering (our theme) is switched on by an empty file cfg_BHomeActivity_VtmMaps at the device root.

prepare() writes, next to wahoondrer_maps/, a folder laid out like the device:
    roam_v1/cfg_BHomeActivity_VtmMaps        (empty, copied once)
    roam_v1/maps/tiles/8/<x>/<y>.map.gz
    roam_v1/maps/vtm-elemnt/...              (the same theme)
so the .map.lzma and .map.gz files are never mixed. A tile is converted again only when its .map.lzma is
newer (the .map.gz gets the date of its .map.lzma).

Usage: python3 -m wahoondrer.roam --out OUTPUT_FOLDER [--tiles 136/88,135/88]
"""
import argparse
import gzip
import lzma
import os
import shutil
import sys

DIR = "roam_v1"
CFG = "cfg_BHomeActivity_VtmMaps"
JUNK = (".DS_Store", "Thumbs.db")


def src_maps(out):
    return os.path.join(out, "wahoondrer_maps", "maps")


def dst_root(out):
    return os.path.join(out, DIR)


def paths(out, tile):
    x, y = tile.split("/")
    return (os.path.join(src_maps(out), "tiles", "8", x, f"{y}.map.lzma"),
            os.path.join(dst_root(out), "maps", "tiles", "8", x, f"{y}.map.gz"))


def tiles_in(out):
    """The tiles ("x/y") that have a .map.lzma in wahoondrer_maps."""
    base = os.path.join(src_maps(out), "tiles", "8")
    found = []
    if os.path.isdir(base):
        for x in sorted((d for d in os.listdir(base) if d.isdigit()), key=int):
            for f in os.listdir(os.path.join(base, x)):
                if f.endswith(".map.lzma") and f.split(".")[0].isdigit():
                    found.append(f"{x}/{f.split('.')[0]}")
    return sorted(found, key=lambda t: tuple(int(v) for v in t.split("/")))


def missing(out, tiles=None):
    """The tiles whose .map.gz is absent or older than their .map.lzma."""
    todo = []
    for t in tiles if tiles is not None else tiles_in(out):
        src, dst = paths(out, t)
        if os.path.isfile(src) and (not os.path.isfile(dst) or os.path.getmtime(dst) < os.path.getmtime(src)):
            todo.append(t)
    return todo


def convert(src, dst):
    """.map.lzma -> .map.gz (same content), written to a temporary name first."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    tmp = dst + ".part"
    mtime = os.path.getmtime(src)
    with lzma.open(src, "rb") as fi, open(tmp, "wb") as raw, \
            gzip.GzipFile(filename=os.path.basename(dst)[:-3], mode="wb", fileobj=raw, compresslevel=9,
                          mtime=int(mtime)) as fo:
        shutil.copyfileobj(fi, fo, 1 << 20)
    os.replace(tmp, dst)
    os.utime(dst, (mtime, mtime))


def prepare(out, tiles=None, log=print):
    """Convert the tiles that need it, copy the theme and write the cfg file. Returns the converted tiles."""
    out = os.path.abspath(os.path.expanduser(out))
    todo = missing(out, tiles)
    for i, t in enumerate(todo, 1):
        log(f"Roam v1: tile {t} -> .map.gz ({i}/{len(todo)})")
        convert(*paths(out, t))
    theme = os.path.join(src_maps(out), "vtm-elemnt")
    if os.path.isdir(theme):
        shutil.copytree(theme, os.path.join(dst_root(out), "maps", "vtm-elemnt"), dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*JUNK))
    os.makedirs(dst_root(out), exist_ok=True)
    cfg = os.path.join(dst_root(out), CFG)
    if not os.path.exists(cfg):
        open(cfg, "wb").close()
    log(f"Roam v1: {len(todo)} tile(s) converted, theme copied, files ready in {dst_root(out)}")
    return todo


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the output folder (the one holding wahoondrer_maps)")
    ap.add_argument("--tiles", default="", help="x/y,x/y... (default: every tile of wahoondrer_maps)")
    args = ap.parse_args(argv)
    tiles = [t for t in args.tiles.split(",") if t] or None
    try:
        prepare(args.out, tiles)
    except (OSError, lzma.LZMAError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
