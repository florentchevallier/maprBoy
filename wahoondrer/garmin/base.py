"""OSM base map for Garmin: mkgmap's own default style, with our POIs instead of its POIs.

Built as a separate, opaque map (maprboy-base.img) next to the transparent Wandrer/Squadrats overlay
(gmapsupp.img). Seen on an Edge 530 (October 2026), see docs/GARMIN.md:
- the default style draws the standard Garmin types, which the device draws with its own look;
- our POIs replace the default style's POIs; they appear 3 resolution steps later than on the overlay
  (resolution 22 showed from the 800 m scale, 24 from about 200 m) and carry no name, as on the Wahoo;
- only the stations of the trains: railway=stop marks every tram/S-Bahn/U-Bahn stop, often once per direction;
- POI icons at ICON_SCALE of the overlay's size (judged readable while riding at 60-70 %);
- labels only for places (cities, villages, suburbs...), water, parks, gardens and forests, and road numbers;
  street names only on request; no default names ("Fence", "Building"...) and no names on other areas;
- a map tile holds at most 16 MB of drawing data (RGN), so the area is cut into a grid of tiles
  (a 10 km square of central Munich took 2.4 MB).
"""
import os
import re
import shutil
import subprocess
import tempfile
import zipfile

from . import build, ids, pois as garmin_pois, tiling

FAMILY_ID = 6324               # another id than the overlay's (each map with a TYP has its own family)
PRODUCT_ID = 1
MAPNAME = "63240001"           # mkgmap numbers the next tiles 63240002, ...
POI_SHIFT = 3                  # resolution steps added to the POI rules (see above)
DROPPED_POIS = {("railway", "stop")}
ICON_SCALE = 0.65             # POI icons: 65 % of the overlay's (24 px -> 16 px)
NAMED_AREAS = ("natural!=water & water!=* & waterway!=* & leisure!=park & leisure!=garden"
               " & landuse!=forest & natural!=wood")
STREET_RULE = "highway=* & name=* {set mkgmap:street='${name}'}"   # kept: address search
BARRIER_RULE = "barrier=wall | barrier=fence | barrier=hedge | barrier=ditch {add name='${barrier|subst:\"_=> \"}'}"


# ------------------------------------------------------------------ style
def default_style(mkgmap_jar, dest):
    """Copy mkgmap's default style (styles/default/ inside the jar) to dest."""
    with zipfile.ZipFile(mkgmap_jar) as z:
        names = [n for n in z.namelist() if n.startswith("styles/default/") and not n.endswith("/")]
        if not names:
            raise build.GarminError(f"no default style in {mkgmap_jar}")
        for n in names:
            p = os.path.join(dest, n[len("styles/default/"):])
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as f:
                f.write(z.read(n))
    return dest


def _read(style_dir, name):
    with open(os.path.join(style_dir, name), encoding="utf-8") as f:
        return f.read()


def _write(style_dir, name, text):
    with open(os.path.join(style_dir, name), "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def no_labels(lines, polygons, points, street_names=False):
    """(lines, polygons, points) without default names, barrier names and names of most areas;
    without street names unless street_names (the road numbers stay)."""
    drop = re.compile(r"\s*default_name\s+'[^']*'")
    lines, polygons, points = (drop.sub("", t) for t in (lines, polygons, points))
    if BARRIER_RULE not in lines:
        raise build.GarminError("mkgmap default style: the barrier rule changed, update garmin/base.py")
    lines = lines.replace(BARRIER_RULE, BARRIER_RULE.split(" {")[0] + " {delete name}")
    if not street_names:
        if STREET_RULE not in lines:
            raise build.GarminError("mkgmap default style: the street rule changed, update garmin/base.py")
        lines = lines.replace(STREET_RULE, STREET_RULE + "\nhighway=* & name=* {delete name}", 1)
    inc = "include 'inc/name';"
    if inc not in polygons:
        raise build.GarminError("mkgmap default style: no inc/name in polygons, update garmin/base.py")
    polygons = polygons.replace(
        inc, f"name=* & {NAMED_AREAS} {{delete name; delete brand; delete operator; delete ref}}\n{inc}", 1)
    return lines, polygons, points


def our_points(points, rules):
    """The default points file with its preamble (actions), its places and peaks, our POIs instead of its POIs.
    Our rules come before the naming rules and delete the name: icons only."""
    rows = points.split("\n")
    try:
        name_inc = next(i for i, r in enumerate(rows) if r.startswith("include 'inc/name'"))
        last_place = max(i for i, r in enumerate(rows) if r.startswith("place="))
        fin = next(i for i, r in enumerate(rows) if r.startswith("<finalize>"))
    except (StopIteration, ValueError):
        raise build.GarminError("mkgmap default style: points file changed, update garmin/base.py")
    peaks = [r for r in rows if r.startswith(("natural=peak", "natural=volcano"))]
    ours = [f"{cond} {{delete name}} [0x{t:04x} resolution {min(garmin_pois.RES_MAX, res + POI_SHIFT)}]"
            for cond, t, res in rules]
    return "\n".join(rows[:name_inc] + ["# maprBoy POIs"] + ours + rows[name_inc:last_place + 1] + peaks
                     + rows[fin:])


def base_pois(poi_list):
    """The POIs of the base map: without DROPPED_POIS, icons at ICON_SCALE."""
    return [{**p, "size": p.get("size", 100) * ICON_SCALE} for p in poi_list
            if (p["key"], p["value"]) not in DROPPED_POIS]


def write_style(mkgmap_jar, style_dir, rules, street_names=False):
    default_style(mkgmap_jar, style_dir)
    lines, polygons, points = no_labels(*(_read(style_dir, n) for n in ("lines", "polygons", "points")),
                                        street_names=street_names)
    _write(style_dir, "lines", lines)
    _write(style_dir, "polygons", polygons)
    _write(style_dir, "points", our_points(points, rules))
    return style_dir


def typ_text(icons, family_id=FAMILY_ID):
    return "\n".join(["[_id]", f"FID={family_id}", f"ProductCode={PRODUCT_ID}", "CodePage=1252", "[end]"]
                     + garmin_pois.typ_points(icons)) + "\n"


# ------------------------------------------------------------------ data
def tiles(osm_files, bbox, work, log=print):
    """Cut the OSM extracts to bbox, merge them, split into grid cells. Returns the cell .osm.pbf paths."""
    for f in osm_files:
        if not os.path.isfile(f):
            raise build.GarminError(f"OSM extract not found: {f}")
    box = ",".join(f"{v:.6f}" for v in bbox)
    parts = []
    try:
        for i, f in enumerate(osm_files):
            log(f"Cutting {os.path.basename(f)} to the area (osmium) ...")
            part = os.path.join(work, f"cut{i}.osm.pbf")
            tiling.osmium(["osmium", "extract", "-b", box, "-s", "smart", f, "-o", part, "--overwrite"])
            parts.append(part)
        area = parts[0]
        if len(parts) > 1:
            area = os.path.join(work, "area.osm.pbf")
            tiling.osmium(["osmium", "merge", *parts, "-o", area, "--overwrite"])
        log(f"Splitting the area into tiles of about 11 km ({len(tiling.grid(bbox))} cells) ...")
        return tiling.split(area, bbox, work)
    except tiling.TilingError as e:
        raise build.GarminError(str(e))


# ------------------------------------------------------------------ build
def build_base(osm_files, bbox, out_img, mkgmap_jar, poi_list, street_names=False, java="java", log=print,
               family_id=FAMILY_ID, mapname=MAPNAME, description="maprBoy base"):
    """Write the base map (OSM + our POIs) of bbox (W, S, E, N) to out_img. Returns out_img.
    family_id / mapname / description: see ids.py (several maprBoy maps on one device)."""
    if not os.path.isfile(mkgmap_jar):
        raise build.GarminError(f"mkgmap jar not found: {mkgmap_jar}")
    if shutil.which(java) is None:
        raise build.GarminError("Java not found (mkgmap needs it)")
    if not osm_files:
        raise build.GarminError("the base map needs OSM extracts (--osm file.osm.pbf)")
    work = tempfile.mkdtemp(prefix="maprboy-base-")
    try:
        rules, icons = garmin_pois.plan(base_pois(poi_list))
        style_dir = write_style(mkgmap_jar, os.path.join(work, "style"), rules, street_names)
        typ_file = os.path.join(work, "base.txt")          # .txt: required, see typ.py
        with open(typ_file, "w", encoding="utf-8", newline="\n") as f:
            f.write(typ_text(icons, family_id))
        inputs = tiles(osm_files, bbox, work, log)
        if not inputs:
            raise build.GarminError("no OSM data in the area: is it covered by the OSM extracts?")
        ids.check_tiles(len(inputs))
        out_dir = os.path.join(work, "out")
        cmd = [java, "-jar", os.path.abspath(mkgmap_jar), f"--style-file={style_dir}",
               f"--family-id={family_id}", f"--product-id={PRODUCT_ID}", f"--family-name={description}",
               f"--series-name={description}", f"--description={description}", f"--mapname={mapname}",
               "--gmapsupp", "--latin1", "--add-pois-to-areas", f"--output-dir={out_dir}", typ_file] + inputs
        log(f"Running mkgmap ({len(inputs)} tile(s)) ...")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise build.GarminError("mkgmap failed:\n" + (r.stderr or r.stdout)[-2000:])
        built = os.path.join(out_dir, "gmapsupp.img")
        if not os.path.isfile(built):
            raise build.GarminError("mkgmap produced no map:\n" + (r.stdout or r.stderr)[-2000:])
        subs = build.check_img(built)
        os.makedirs(os.path.dirname(os.path.abspath(out_img)) or ".", exist_ok=True)
        shutil.copyfile(built, out_img)
        rgn = [v for k, v in subs.items() if k.endswith(".RGN")]
        log(f"Wrote {out_img} ({os.path.getsize(out_img) // 1024} KB, {len(rgn)} tile(s), "
            f"largest RGN {max(rgn)} bytes, {len(icons)} POI icons)")
        return out_img
    finally:
        shutil.rmtree(work, ignore_errors=True)
