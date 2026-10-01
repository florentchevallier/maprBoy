"""POI configuration: from a list of POI rules to the three places where a POI must be declared.

A POI must be declared in three places (see docs/PIPELINE.md): the tag-mapping XML (MapWriter),
config/tags-to-keep.json (osmium filter) and the VTM theme (drawing). This module writes all three
from one list of rules, so they cannot get out of sync. Used by scripts/generate_poi_config.py (rewrites
the repository files from config/poi-rules.json) and by scripts/build_tiles.py (writes a private copy
from the user's choices made in the web page, so the repository is never touched). Standard library only.

Only the parts between these markers are rewritten, everything else is left alone:
    <!-- BEGIN generated:poi-nodes --> ... <!-- END generated:poi-nodes -->   (tag-mapping <pois>)
    <!-- BEGIN generated:poi-ways -->  ... <!-- END generated:poi-ways -->    (tag-mapping <ways>)
    <!-- BEGIN generated:poi-theme -->  ... <!-- END generated:poi-theme -->  (VTM theme)
tags-to-keep.json is add-only: missing tag values are added, nothing is ever removed.
"""
import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = ROOT / "config" / "poi-rules.json"

# ----------------------------------------------------------------- shapes
SHAPES = ("circle", "square", "triangle", "diamond")
COLORS = {"red": "#d32f2f", "blue": "#1565c0", "green": "#2e7d32", "yellow": "#f9a825",
          "purple": "#7b1fa2", "orange": "#ef6c00", "black": "#212121", "white": "#ffffff"}
_LIGHT = {"yellow", "orange", "white"}          # these get a dark outline, the others a white one
_SHAPE_RE = re.compile(r"^shape:([a-z]+):([a-z]+)$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_:.\-]+$")    # OSM keys/values go into XML attributes: keep them plain
ZOOM_MIN, ZOOM_MAX = 8, 17


def is_shape(icon):
    return bool(_SHAPE_RE.match(icon or ""))


def shape_parts(icon):
    m = _SHAPE_RE.match(icon or "")
    if not m or m.group(1) not in SHAPES or m.group(2) not in COLORS:
        raise ValueError(f"unknown shape icon '{icon}' (shapes: {', '.join(SHAPES)}; colours: {', '.join(COLORS)})")
    return m.group(1), m.group(2)


def icon_file(icon):
    """File name used in the theme for an icon setting ('fuel.svg' or 'shape:circle:red')."""
    if is_shape(icon):
        shape, color = shape_parts(icon)
        return f"shape-{shape}-{color}.svg"
    return icon


def shape_svg(shape, color):
    fill = COLORS[color]
    edge = "#212121" if color in _LIGHT else "#ffffff"
    style = f'fill="{fill}" stroke="{edge}" stroke-width="36" stroke-linejoin="round"'
    body = {
        "circle": f'<circle cx="290" cy="290" r="220" {style}/>',
        "square": f'<rect x="80" y="80" width="420" height="420" {style}/>',
        "triangle": f'<polygon points="290,70 510,490 70,490" {style}/>',
        "diamond": f'<polygon points="290,60 520,290 290,520 60,290" {style}/>',
    }[shape]
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<svg xmlns="http://www.w3.org/2000/svg" width="580" height="580" viewBox="0 0 580 580">'
            f'{body}</svg>\n')


def write_shape_icons(pois, icon_dir):
    """Write the SVG of every shape icon the POIs use into icon_dir."""
    Path(icon_dir).mkdir(parents=True, exist_ok=True)
    for p in pois:
        for icon in [p["icon"]] + [v.get("icon") for v in p.get("variants", [])]:
            if is_shape(icon):
                shape, color = shape_parts(icon)
                (Path(icon_dir) / icon_file(icon)).write_text(shape_svg(shape, color), encoding="utf-8")


# ------------------------------------------------------------------ paths
class Paths:
    """Where the generated files live, for a repository root or a private copy."""
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.tag_files = [self.root / "config" / "tag-wahoo-poi.xml",
                          self.root / "config" / "tag-wahoo-poi-wandrer.xml"]
        self.theme = self.root / "themes" / "vtm" / "vtm-elemnt.xml"
        self.tags_to_keep = self.root / "config" / "tags-to-keep.json"
        self.icon_dir = self.root / "themes" / "vtm" / "icons"


def poi_id(p):
    return f"{p['key']}={p['value']}"


# ------------------------------------------------------------------ rules
def read_rules(path=DEFAULT_RULES):
    """All POIs of a rules file (enabled or not), unvalidated."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pois = data.get("pois")
    if not isinstance(pois, list) or not pois:
        raise ValueError("'pois' must be a non-empty list")
    return pois


def library_icons(icon_dir):
    return sorted(p.name for p in Path(icon_dir).glob("*.svg") if not p.name.startswith("shape-"))


def validate(pois, icon_dir):
    """Validate the enabled POIs. Raises ValueError with a clear message."""
    seen = set()
    for i, p in enumerate(pois, 1):
        name = f"POI #{i} ({p.get('key')}={p.get('value')})"
        for field in ("key", "value", "zoom", "icon"):
            if field not in p:
                raise ValueError(f"{name}: missing '{field}'")
        for field in ("key", "value"):
            if not isinstance(p[field], str) or not _TAG_RE.match(p[field]):
                raise ValueError(f"{name}: '{field}' may only contain letters, digits and _ : . -")
        if not isinstance(p["zoom"], int) or isinstance(p["zoom"], bool) or not ZOOM_MIN <= p["zoom"] <= ZOOM_MAX:
            raise ValueError(f"{name}: 'zoom' must be an integer between {ZOOM_MIN} and {ZOOM_MAX}")
        if "size" in p and (not isinstance(p["size"], int) or isinstance(p["size"], bool) or not 20 <= p["size"] <= 400):
            raise ValueError(f"{name}: 'size' must be a percentage between 20 and 400")
        if (p["key"], p["value"]) in seen:
            raise ValueError(f"{name}: declared twice")
        seen.add((p["key"], p["value"]))
        variants = p.get("variants", [])
        if len({v.get("tag") for v in variants}) > 1:
            raise ValueError(f"{name}: all variants must use the same secondary tag")
        for icon in [p["icon"]] + [v.get("icon") for v in variants]:
            if is_shape(icon):
                shape_parts(icon)
            elif not icon or not re.match(r"^[A-Za-z0-9_\-]+\.svg$", icon) or not (Path(icon_dir) / icon).is_file():
                raise ValueError(f"{name}: icon '{icon}' not found in {icon_dir}")
        for v in variants:
            if not v.get("tag") or not v.get("value"):
                raise ValueError(f"{name}: a variant needs 'tag' and 'value'")
            if not _TAG_RE.match(v["tag"]) or not _TAG_RE.match(v["value"]):
                raise ValueError(f"{name}: variant tag/value may only contain letters, digits and _ : . -")
        for k, vals in p.get("hide_if", {}).items():
            if not isinstance(vals, list) or not vals:
                raise ValueError(f"{name}: hide_if['{k}'] must be a non-empty list")
            if not _TAG_RE.match(k) or not all(isinstance(v, str) and _TAG_RE.match(v) for v in vals):
                raise ValueError(f"{name}: hide_if tags/values may only contain letters, digits and _ : . -")
    return pois


def load_rules(path=DEFAULT_RULES, icon_dir=None):
    """Read a rules file and return the validated, enabled POIs."""
    icon_dir = icon_dir or Paths().icon_dir
    return validate([p for p in read_rules(path) if p.get("enabled", True)], icon_dir)


def effective_pois(base, user):
    """Base rules + the user's choices ({"overrides": {id: {enabled, zoom, size, icon}}, "custom": [...]}).
    Returns every POI (disabled ones included) with an 'enabled' flag."""
    user = user or {}
    overrides = user.get("overrides") or {}
    out = []
    for p in base:
        q = dict(p)
        q.setdefault("enabled", True)
        o = overrides.get(poi_id(p)) or {}
        for field in ("enabled", "zoom", "size", "icon"):
            if field in o:
                if o[field] is None:
                    q.pop(field, None)
                else:
                    q[field] = o[field]
        if "icon" in o:
            q.pop("variants", None)            # a chosen icon replaces the paid/free toilet variants
        out.append(q)
    for c in user.get("custom") or []:
        q = dict(c)
        q.setdefault("enabled", True)
        q["custom"] = True
        out.append(q)
    return out


def has_user_choices(user):
    return bool((user or {}).get("overrides") or (user or {}).get("custom"))


# ----------------------------------------------------------- secondary tags
def sub_tags(poi):
    """Secondary (key, value) pairs a POI needs stored in the map."""
    pairs = [(v["tag"], v["value"]) for v in poi.get("variants", [])]
    for k, vals in poi.get("hide_if", {}).items():
        pairs += [(k, v) for v in vals]
    return pairs


def _declared(text):
    return set(re.findall(r'key="([^"]+)"\s+value="([^"]+)"', text))


def _sub_tag_zooms(pois):
    zooms = {}
    for p in pois:
        for pair in sub_tags(p):
            zooms[pair] = min(zooms.get(pair, 99), p["zoom"])
    return zooms


def nodes_block(pois, skip):
    lines = [f'<osm-tag key="{p["key"]}" value="{p["value"]}" zoom-appear="{p["zoom"]}" />'
             for p in pois]
    extra = [f'<osm-tag key="{k}" value="{v}" zoom-appear="{z}" />'
             for (k, v), z in _sub_tag_zooms(pois).items() if (k, v) not in skip]
    if extra:
        lines += ["<!-- secondary tags (variants / hidden values) -->"] + extra
    return lines


def ways_block(pois, skip):
    lines = [f'<osm-tag key="{p["key"]}" label-position="true" value="{p["value"]}" '
             f'zoom-appear="{p["zoom"]}" />' for p in pois]
    lines += [f'<osm-tag key="{k}" value="{v}" zoom-appear="{z}" />'
              for (k, v), z in _sub_tag_zooms(pois).items() if (k, v) not in skip]
    return lines


def theme_block(pois):
    out = []
    for p in pois:
        out.append(f'<m k="{p["key"]}" v="{p["value"]}" zoom-min="{p["zoom"]}">')
        depth = 1
        for k, vals in p.get("hide_if", {}).items():
            out.append("   " * depth + f'<m k="{k}" v="-|{"|".join(vals)}">')
            depth += 1
        size = f' symbol-percent="{p["size"]}"' if "size" in p else ""
        variants = p.get("variants", [])
        if variants:
            tag = variants[0]["tag"]
            for v in variants:
                out.append("   " * depth + f'<m k="{tag}" v="{v["value"]}">')
                out.append("   " * (depth + 1) + f'<symbol src="icons/{icon_file(v["icon"])}"{size}/>')
                out.append("   " * depth + "</m>")
            others = "|".join(v["value"] for v in variants)
            out.append("   " * depth + f'<m k="{tag}" v="-|{others}">')
            out.append("   " * (depth + 1) + f'<symbol src="icons/{icon_file(p["icon"])}"{size}/>')
            out.append("   " * depth + "</m>")
        else:
            out.append("   " * depth + f'<symbol src="icons/{icon_file(p["icon"])}"{size}/>')
        for depth in range(depth - 1, 0, -1):
            out.append("   " * depth + "</m>")
        out.append("</m>")
    return out


# ------------------------------------------------------------ rewriting
def replace_block(text, name, lines, file_label):
    pattern = re.compile(
        rf"(?P<indent>[ \t]*)<!-- BEGIN generated:{name}[^>]*-->\n.*?"
        rf"(?P=indent)<!-- END generated:{name} -->", re.DOTALL)
    m = pattern.search(text)
    if not m:
        raise ValueError(f"{file_label}: markers 'generated:{name}' not found")
    ind = m.group("indent")
    body = "".join(f"{ind}{l}\n" for l in lines)
    new = (f"{ind}<!-- BEGIN generated:{name} (from config/poi-rules.json, do not edit by hand) -->\n"
           f"{body}{ind}<!-- END generated:{name} -->")
    return text[:m.start()] + new + text[m.end():]


def outside_block(text, name):
    return re.sub(rf"<!-- BEGIN generated:{name}.*?<!-- END generated:{name} -->", "",
                  text, flags=re.DOTALL)


def update_tag_file(path, pois):
    text = Path(path).read_text(encoding="utf-8")
    pois_section = re.search(r"<pois>.*?</pois>", text, re.DOTALL)
    if not pois_section:
        raise ValueError(f"{Path(path).name}: no <pois> section")
    skip_nodes = _declared(outside_block(pois_section.group(0), "poi-nodes"))
    text = replace_block(text, "poi-nodes", nodes_block(pois, skip_nodes), Path(path).name)
    after_ways = text.split("<!-- END generated:poi-ways -->", 1)
    skip_ways = _declared(after_ways[1]) if len(after_ways) == 2 else set()
    text = replace_block(text, "poi-ways", ways_block(pois, skip_ways), Path(path).name)
    return text


def update_theme(paths, pois):
    return replace_block(paths.theme.read_text(encoding="utf-8"), "poi-theme",
                         theme_block(pois), paths.theme.name)


def update_tags_to_keep(paths, pois):
    """Add-only: returns the new data (dict) for config/tags-to-keep.json."""
    data = json.loads(paths.tags_to_keep.read_text(encoding="utf-8"))
    universal = data["TAGS_TO_KEEP_UNIVERSAL"]
    wanted = [(p["key"], p["value"]) for p in pois]
    wanted += [pair for p in pois for pair in sub_tags(p)]
    for key, value in wanted:
        current = universal.get(key)
        if current == "":          # empty string = "keep every value"
            continue
        values = [] if current is None else ([current] if isinstance(current, str) else list(current))
        if value not in values:
            values.append(value)
        universal[key] = values
    return data


def generate(paths, pois, write=True):
    """Compute (and write unless write=False) every generated file. Returns the list of changed paths."""
    results = {p: update_tag_file(p, pois) for p in paths.tag_files}
    results[paths.theme] = update_theme(paths, pois)
    stale = []
    for path, new_text in results.items():
        if new_text != path.read_text(encoding="utf-8"):
            stale.append(path)
            if write:
                path.write_text(new_text, encoding="utf-8")
    old_keep = json.loads(paths.tags_to_keep.read_text(encoding="utf-8"))
    new_keep = update_tags_to_keep(paths, pois)
    if new_keep != old_keep:
        stale.append(paths.tags_to_keep)
        if write:
            paths.tags_to_keep.write_text(json.dumps(new_keep, indent=4) + "\n", encoding="utf-8")
    if write:
        write_shape_icons(pois, paths.icon_dir)
    return stale


def prepare_tree(dst_root, pois, src_root=ROOT):
    """Copy the repository's config and theme to dst_root and write the POI parts for `pois` there.
    Returns the Paths of the copy. The repository itself is not touched."""
    dst_root, src_root = Path(dst_root), Path(src_root)
    if dst_root.exists():
        shutil.rmtree(dst_root)
    for sub in ("config", "themes/vtm"):
        shutil.copytree(src_root / sub, dst_root / sub,
                        ignore=shutil.ignore_patterns("__pycache__", ".DS_Store"))
    paths = Paths(dst_root)
    validate(pois, paths.icon_dir)
    generate(paths, pois)
    return paths
