"""POIs on the Garmin overlay: the same rules as for Wahoo (config/poi-rules.json, or the effective file the
web page writes from the user's choices), turned into mkgmap point rules and TYP icons.

- One Garmin point type per distinct icon (0x6401, 0x6402...), its bitmap drawn from our SVG icons.
- Variants (fee=yes...) come first; the default icon is used only when no variant tag matches.
- only_if / hide_if become conditions of the rule.
- The rule's zoom keeps its order on the device: resolution = zoom + RESOLUTION_OFFSET.
- No names: build.py tells mkgmap to read the label from a tag that never exists.
- The OSM data: a Geofabrik extract cut to the area and filtered to the POI tags with osmium.
"""
import io
import os
import subprocess

from .. import poi_config

TYPE_BASE = 0x6400          # 0x64 with subtypes 0x01..0x1f: checked on a Forerunner 955 with 0x6411..0x6413
MAX_ICONS = 0x1f
RESOLUTION_OFFSET = 7        # Wahoo zoom 13 -> resolution 20, 14 -> 21, 15 -> 22
RES_MIN, RES_MAX = 16, 24
ICON_PX = 24                 # icon size at 100 %
ICON_MIN_PX, ICON_MAX_PX = 8, 48
MAX_COLORS = 32              # an icon is reduced to this many colours (plus transparency)


class PoiError(ValueError):
    pass


def load(rules_path=None):
    """The enabled, validated POIs of a rules file (the repository one by default)."""
    return poi_config.load_rules(rules_path or poi_config.DEFAULT_RULES)


def resolution(zoom):
    return max(RES_MIN, min(RES_MAX, int(zoom) + RESOLUTION_OFFSET))


def icon_px(poi):
    return max(ICON_MIN_PX, min(ICON_MAX_PX, round(ICON_PX * poi.get("size", 100) / 100)))


def _values(key, vals):
    vals = list(vals)
    if len(vals) == 1:
        return f"{key}={vals[0]}"
    return "(" + " | ".join(f"{key}={v}" for v in vals) + ")"


def _base(poi):
    terms = [f"{poi['key']}={poi['value']}"]
    terms += [_values(k, vals) for k, vals in poi.get("only_if", {}).items()]
    terms += [f"{k}!={v}" for k, vals in poi.get("hide_if", {}).items() for v in vals]
    return terms


def _icon_key(icon, px):
    return (icon, px)


def plan(pois):
    """(rules, icons): rules = [(condition, type, resolution)] in the order mkgmap must try them;
    icons = {(icon, px): type}. Raises PoiError when there are too many distinct icons."""
    icons = {}
    rules = []

    def type_for(icon, px):
        k = _icon_key(icon, px)
        if k not in icons:
            if len(icons) >= MAX_ICONS:
                raise PoiError(f"too many different POI icons for the Garmin map (at most {MAX_ICONS})")
            icons[k] = TYPE_BASE + len(icons) + 1
        return icons[k]

    for p in pois:
        px, res, base = icon_px(p), resolution(p["zoom"]), _base(p)
        variants = p.get("variants", [])
        for v in variants:
            if v.get("enabled", True):
                rules.append((" & ".join(base + [f"{v['tag']}={v['value']}"]), type_for(v["icon"], px), res))
        if p.get("default_enabled", True):
            # the default icon only where no variant applies (a variant switched off must not fall back here)
            rules.append((" & ".join(base + [f"{v['tag']}!={v['value']}" for v in variants]),
                          type_for(p["icon"], px), res))
    return rules, icons


def points_file(rules):
    return "".join(f"{cond} [0x{t:04x} resolution {res}]\n" for cond, t, res in rules)


def main_tags(pois):
    """The osmium tags-filter expressions that keep every POI of the rules (nodes, ways and relations)."""
    return sorted({f"nwr/{p['key']}={p['value']}" for p in pois})


# ------------------------------------------------------------------ icons
def _svg(icon, icon_dir):
    if poi_config.is_shape(icon):
        return poi_config.shape_svg(*poi_config.shape_parts(icon)).encode("utf-8")
    with open(os.path.join(icon_dir, icon), "rb") as f:
        return f.read()


def icon_xpm(icon, px, icon_dir=None):
    """The DayXpm lines of an icon: px x px, at most MAX_COLORS colours, '.' for transparent pixels."""
    import cairosvg                 # only needed when POIs are drawn
    from PIL import Image
    icon_dir = icon_dir or str(poi_config.Paths().icon_dir)
    png = cairosvg.svg2png(bytestring=_svg(icon, icon_dir), output_width=px, output_height=px)
    img = Image.open(io.BytesIO(png)).convert("RGBA")
    alpha = img.getchannel("A")
    rgb = img.convert("RGB").quantize(colors=MAX_COLORS).convert("RGB")
    letters = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    colors, rows = {}, []
    for y in range(px):
        row = ""
        for x in range(px):
            if alpha.getpixel((x, y)) < 128:
                row += "."
                continue
            c = "#%02X%02X%02X" % rgb.getpixel((x, y))
            if c not in colors:
                colors[c] = letters[len(colors)]
            row += colors[c]
        rows.append(row)
    head = f'DayXpm="{px} {px} {len(colors) + 1} 1"'
    return [head, '". c none"'] + [f'"{ch} c {c}"' for c, ch in colors.items()] + [f'"{r}"' for r in rows]


def typ_points(icons, icon_dir=None):
    """The [_point] sections of the TYP for icons = {(icon, px): type}."""
    out = []
    for (icon, px), t in sorted(icons.items(), key=lambda kv: kv[1]):
        out += ["[_point]", f"Type=0x{t:04x}", f"String={poi_config.icon_file(icon)[:-4]}"]
        out += icon_xpm(icon, px, icon_dir)
        out.append("[end]")
    return out


# ------------------------------------------------------------------ OSM data
def extract(osm_files, bbox, pois, work_dir, osmium="osmium", log=print):
    """Cut every extract to bbox (W, S, E, N), keep the POI tags, merge. Returns the .osm.pbf path."""
    if not osm_files:
        raise PoiError("POIs need an OSM extract (--osm file.osm.pbf, e.g. the Geofabrik file WMC downloaded)")
    for f in osm_files:
        if not os.path.isfile(f):
            raise PoiError(f"OSM extract not found: {f}")
    os.makedirs(work_dir, exist_ok=True)
    box = ",".join(f"{v:.6f}" for v in bbox)
    parts = []
    for i, f in enumerate(osm_files):
        cut = os.path.join(work_dir, f"cut{i}.osm.pbf")
        kept = os.path.join(work_dir, f"pois{i}.osm.pbf")
        log(f"Cutting {os.path.basename(f)} to the area and keeping the POIs (osmium) ...")
        _run([osmium, "extract", "-b", box, "-s", "smart", f, "-o", cut, "--overwrite"])
        _run([osmium, "tags-filter", cut, *main_tags(pois), "-o", kept, "--overwrite"])
        parts.append(kept)
    if len(parts) == 1:
        return parts[0]
    merged = os.path.join(work_dir, "pois.osm.pbf")
    _run([osmium, "merge", *parts, "-o", merged, "--overwrite"])
    return merged


def _run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise PoiError("osmium not found (it comes with the maprBoy environment)")
    if r.returncode != 0:
        raise PoiError(f"{os.path.basename(cmd[0])} {cmd[1]} failed:\n{(r.stderr or r.stdout)[-1500:]}")
