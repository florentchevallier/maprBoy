"""The extra Karoo map file (maprboy-<place>.map): Wandrer roads, Squadrats squares and our POIs, written by
Osmosis with the mapsforge MapWriter plugin (the one WMC uses).

- The lines are given whole to MapWriter, which cuts them to the area: cutting them first with osmium keeps only
  their nodes inside the area, and a long Squadrats line with a single node inside was lost (seen on a Karoo 2).
- The POIs are taken from OSM extracts, with their main tag renamed maprboy:<key> (see theme.py); an area (a
  building drawn as a polygon) becomes a point at the mean of its outline's points. The secondary tags the rules
  use (fee, access...) are kept as they are.
"""
import json
import os
import subprocess

from .. import poi_config
from .theme import POI_PREFIX, ZOOM_SQ_BIG, ZOOM_SQ_SMALL, poi_rules

LINE_ZOOM = 10            # stored from zoom 10, as for the Wahoo; the theme decides when they are drawn


class MapError(RuntimeError):
    pass


def _run(cmd, what):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise MapError(f"{cmd[0]} not found ({what})")
    if r.returncode != 0:
        raise MapError(f"{what} failed:\n{(r.stderr or r.stdout)[-1500:]}")
    return r.stdout


def tag_mapping(pois=(), visited=False):
    """The MapWriter tag-mapping XML: our lines, and the POIs (main tag renamed, secondary tags kept)."""
    renamed = poi_rules(pois)
    ways = [("wandrer", "unvisited", LINE_ZOOM), ("wandrer", "boundary", LINE_ZOOM), ("surface", "unpaved", LINE_ZOOM),
            ("squadrat", "big", ZOOM_SQ_BIG), ("squadrat", "small", ZOOM_SQ_SMALL)]
    if visited:
        ways.insert(1, ("wandrer", "visited", LINE_ZOOM))
    rows = ['<?xml version="1.0" encoding="UTF-8"?>',
            '<tag-mapping xmlns="http://mapsforge.org/tag-mapping" default-zoom-appear="16" profile-name="maprboy-karoo">',
            '  <pois>']
    rows += [f'    <osm-tag key="{k}" value="{v}" zoom-appear="{z}"/>' for (k, v), z in poi_config._main_tags(renamed).items()]
    rows += [f'    <osm-tag key="{k}" value="{v}" zoom-appear="{z}" renderable="false"/>'
             for (k, v), z in poi_config._sub_tag_zooms(renamed).items()]
    rows += ['  </pois>', '  <ways>']
    rows += [f'    <osm-tag key="{k}" value="{v}" zoom-appear="{z}"/>' for k, v, z in ways]
    return "\n".join(rows + ['  </ways>', '</tag-mapping>']) + "\n"


def _centre(geometry):
    t, c = geometry["type"], geometry["coordinates"]
    pts = ([c] if t == "Point" else c if t == "LineString" else c[0] if t == "Polygon"
           else c[0][0] if t == "MultiPolygon" else None)
    if not pts:
        return None
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]                        # a closed outline: its first point once
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def extract_pois(osm_files, bbox, pois, out_osm, work, log=print):
    """Write the POIs of `pois` found in the OSM extracts within bbox (W, S, E, N) to out_osm (OSM XML).
    Returns {value: count}."""
    mains = sorted({(p["key"], p["value"]) for p in pois})
    subs = {}
    for p in pois:
        for k, v in poi_config.sub_tags(p):
            subs.setdefault(k, set()).add(v)
    box = ",".join(f"{v:.6f}" for v in bbox)
    w, s, e, n = bbox
    seen, count, nodes = set(), {}, []
    for i, f in enumerate(osm_files):
        if not os.path.isfile(f):
            raise MapError(f"OSM extract not found: {f}")
        log(f"POIs from {os.path.basename(f)} ...")
        cut, sel, geo = (os.path.join(work, f"poi{i}.{x}") for x in ("cut.osm.pbf", "sel.osm.pbf", "geojsonseq"))
        _run(["osmium", "extract", "-b", box, "-s", "smart", f, "-o", cut, "--overwrite"], "osmium extract")
        _run(["osmium", "tags-filter", cut, *[f"nwr/{k}={v}" for k, v in mains], "-o", sel, "--overwrite"],
             "osmium tags-filter")
        _run(["osmium", "export", sel, "-f", "geojsonseq", "-o", geo, "--overwrite",
              "--geometry-types=point,linestring,polygon", "-a", "type,id"], "osmium export")
        with open(geo, encoding="utf-8") as g:
            for line in g:
                line = line.strip("\x1e\n ")
                if not line:
                    continue
                feature = json.loads(line)
                props = feature["properties"]
                tags = {POI_PREFIX + k: v for k, v in mains if props.get(k) == v}
                c = _centre(feature["geometry"]) if tags else None
                if not c or not (w <= c[0] <= e and s <= c[1] <= n):
                    continue
                key = (round(c[0], 6), round(c[1], 6), tuple(sorted(tags.items())))
                if key in seen:                       # the same object in two neighbouring extracts
                    continue
                seen.add(key)
                tags.update({k: props[k] for k, vals in subs.items() if props.get(k) in vals})
                for k, v in tags.items():
                    if k.startswith(POI_PREFIX):
                        count[v] = count.get(v, 0) + 1
                nodes.append((c[1], c[0], tags))
    with open(out_osm, "w", encoding="utf-8") as out:
        out.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6" generator="maprboy">\n')
        for i, (lat, lon, tags) in enumerate(nodes, 1):
            out.write(f'  <node id="{i}" version="1" timestamp="2026-01-01T00:00:00Z" lat="{lat:.7f}" lon="{lon:.7f}">'
                      + "".join(f'<tag k="{k}" v="{v}"/>' for k, v in tags.items()) + "</node>\n")
        out.write("</osm>\n")
    return count


def write_map(inputs, bbox, out_map, tag_conf, work, osmosis="osmosis", log=print):
    """Merge the inputs (OSM files: our lines, our POIs) and write the mapsforge file of bbox (W, S, E, N)."""
    merged = os.path.join(work, "all.osm.pbf")
    pbfs = []
    for i, f in enumerate(inputs):
        p = os.path.join(work, f"in{i}.osm.pbf")
        _run(["osmium", "cat", f, "-o", p, "--overwrite"], "osmium cat")
        pbfs.append(p)
    if len(pbfs) > 1:
        _run(["osmium", "merge", *pbfs, "-o", merged, "--overwrite"], "osmium merge")
    else:
        merged = pbfs[0]
    w, s, e, n = bbox
    log("Writing the map file (Osmosis, MapWriter) ...")
    _run([osmosis, "-q", "--rbf", merged, "--mapfile-writer", f"file={out_map}", f"bbox={s:.6f},{w:.6f},{n:.6f},{e:.6f}",
          f"tag-conf-file={tag_conf}", "type=ram"], "Osmosis MapWriter")
    if not os.path.isfile(out_map) or os.path.getsize(out_map) < 100:
        raise MapError("Osmosis wrote no map file (is the MapWriter plugin installed?)")
    log(f"Wrote {out_map} ({os.path.getsize(out_map) // 1024} KB)")
    return out_map
