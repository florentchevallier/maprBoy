#!/usr/bin/env python3
"""Generate the POI parts of the WMC/VTM configuration from config/poi-rules.json.

A POI must be declared in three places (see docs/PIPELINE.md): the tag-mapping XML
(MapWriter), config/tags-to-keep.json (osmium filter) and the VTM theme (drawing).
This script writes all three from one rules file, so they cannot get out of sync.

Only the parts between these markers are rewritten, everything else is left alone:
    <!-- BEGIN generated:poi-nodes --> ... <!-- END generated:poi-nodes -->   (tag-mapping <pois>)
    <!-- BEGIN generated:poi-ways -->  ... <!-- END generated:poi-ways -->    (tag-mapping <ways>)
    <!-- BEGIN generated:poi-theme -->  ... <!-- END generated:poi-theme -->  (VTM theme)
tags-to-keep.json is add-only: missing tag values are added, nothing is ever removed.

Usage:
    python3 scripts/generate_poi_config.py            # rewrite the files
    python3 scripts/generate_poi_config.py --check    # only report, exit 1 if out of date
Standard library only.
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RULES = ROOT / "config" / "poi-rules.json"
TAG_FILES = [ROOT / "config" / "tag-wahoo-poi.xml",
             ROOT / "config" / "tag-wahoo-poi-wandrer.xml"]
THEME = ROOT / "themes" / "vtm" / "vtm-elemnt.xml"
TAGS_TO_KEEP = ROOT / "config" / "tags-to-keep.json"
ICON_DIR = ROOT / "themes" / "vtm" / "icons"


# ----------------------------------------------------------------- rules
def load_rules(path, icon_dir=ICON_DIR):
    """Read and validate the rules file. Raises ValueError with a clear message."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pois = data.get("pois")
    if not isinstance(pois, list) or not pois:
        raise ValueError("'pois' must be a non-empty list")
    seen = set()
    for i, p in enumerate(pois, 1):
        name = f"POI #{i} ({p.get('key')}={p.get('value')})"
        for field in ("key", "value", "zoom", "icon"):
            if field not in p:
                raise ValueError(f"{name}: missing '{field}'")
        if not isinstance(p["zoom"], int) or not 0 <= p["zoom"] <= 21:
            raise ValueError(f"{name}: 'zoom' must be an integer between 0 and 21")
        if (p["key"], p["value"]) in seen:
            raise ValueError(f"{name}: declared twice")
        seen.add((p["key"], p["value"]))
        variants = p.get("variants", [])
        if len({v.get("tag") for v in variants}) > 1:
            raise ValueError(f"{name}: all variants must use the same secondary tag")
        icons = [p["icon"]] + [v.get("icon") for v in variants]
        for icon in icons:
            if not icon or not (Path(icon_dir) / icon).is_file():
                raise ValueError(f"{name}: icon '{icon}' not found in {icon_dir}")
        for v in variants:
            if not v.get("tag") or not v.get("value"):
                raise ValueError(f"{name}: a variant needs 'tag' and 'value'")
        for k, vals in p.get("hide_if", {}).items():
            if not isinstance(vals, list) or not vals:
                raise ValueError(f"{name}: hide_if['{k}'] must be a non-empty list")
    return pois


def sub_tags(poi):
    """Secondary (key, value) pairs a POI needs stored in the map."""
    pairs = [(v["tag"], v["value"]) for v in poi.get("variants", [])]
    for k, vals in poi.get("hide_if", {}).items():
        pairs += [(k, v) for v in vals]
    return pairs


# ---------------------------------------------------------- tag-mapping
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


# ---------------------------------------------------------------- theme
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
                out.append("   " * (depth + 1) + f'<symbol src="icons/{v["icon"]}"{size}/>')
                out.append("   " * depth + "</m>")
            others = "|".join(v["value"] for v in variants)
            out.append("   " * depth + f'<m k="{tag}" v="-|{others}">')
            out.append("   " * (depth + 1) + f'<symbol src="icons/{p["icon"]}"{size}/>')
            out.append("   " * depth + "</m>")
        else:
            out.append("   " * depth + f'<symbol src="icons/{p["icon"]}"{size}/>')
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
    text = path.read_text(encoding="utf-8")
    pois_section = re.search(r"<pois>.*?</pois>", text, re.DOTALL)
    if not pois_section:
        raise ValueError(f"{path.name}: no <pois> section")
    skip_nodes = _declared(outside_block(pois_section.group(0), "poi-nodes"))
    text = replace_block(text, "poi-nodes", nodes_block(pois, skip_nodes), path.name)
    after_ways = text.split("<!-- END generated:poi-ways -->", 1)
    skip_ways = _declared(after_ways[1]) if len(after_ways) == 2 else set()
    text = replace_block(text, "poi-ways", ways_block(pois, skip_ways), path.name)
    return text


def update_theme(pois):
    return replace_block(THEME.read_text(encoding="utf-8"), "poi-theme",
                         theme_block(pois), THEME.name)


def update_tags_to_keep(pois):
    """Add-only: returns the new data (dict) for config/tags-to-keep.json."""
    data = json.loads(TAGS_TO_KEEP.read_text(encoding="utf-8"))
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


# ----------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    ap.add_argument("--check", action="store_true",
                    help="do not write; exit 1 if a file is out of date")
    args = ap.parse_args(argv)
    try:
        pois = load_rules(args.rules)
        results = {p: update_tag_file(p, pois) for p in TAG_FILES}
        results[THEME] = update_theme(pois)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    stale = []
    for path, new_text in results.items():
        if new_text != path.read_text(encoding="utf-8"):
            stale.append(path)
            if not args.check:
                path.write_text(new_text, encoding="utf-8")
    old_keep = json.loads(TAGS_TO_KEEP.read_text(encoding="utf-8"))
    new_keep = update_tags_to_keep(pois)
    if new_keep != old_keep:
        stale.append(TAGS_TO_KEEP)
        if not args.check:
            TAGS_TO_KEEP.write_text(json.dumps(new_keep, indent=4) + "\n", encoding="utf-8")

    for path in stale:
        print(("out of date: " if args.check else "updated: ") + str(path.relative_to(ROOT)))
    if not stale:
        print("everything is up to date")
    return 1 if (args.check and stale) else 0


if __name__ == "__main__":
    sys.exit(main())
