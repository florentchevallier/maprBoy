"""Our rules in the Karoo's theme (/sdcard/offline_vNN.xml).

The Karoo's own theme is kept: our block is put just before </rendertheme>, between two markers, so that it can
be replaced or removed later without touching the rest. The device's theme changes with its software (v7 to v15
seen on one device), so the block is added to the theme read from the device, not to a copy kept here.

Lines (seen on a Karoo 2, October 2026): widths fixed on screen (fix="true"); with the widths of the Wahoo theme,
which grow with the zoom, the roads of a city centre became one blob. The Wandrer roads from zoom 13 on, the big
Squadrats squares from 11, the small ones from 14. Colours: the light look of the Wahoo settings (theme_style).
POIs: our tag maprboy:<key> (see mapfile.py), so that the Karoo's own rules (amenity=... ) do not draw them a
second time; icons in /sdcard/<ICON_DIR>/.
"""
import re

from .. import poi_config, theme_style

BEGIN, END = "<!-- BEGIN maprboy -->", "<!-- END maprboy -->"
ICON_DIR = "maprboy-icons"
POI_PREFIX = "maprboy:"
ZOOM_WANDRER, ZOOM_SQ_BIG, ZOOM_SQ_SMALL = 13, 11, 14
UNVISITED_PX, OUTLINE_PX, VISITED_PX = 2.5, 4, 2
UNPAVED_DASH = "10,7"
BOUNDARY = {"color": "#7B2FBE", "halo": "#407B2FBE", "px": 2, "halo_px": 6, "dash": "10,14"}
SQ_PX = {"big": 2.5, "small": 2}
SQ_DASH = "6,6"


class ThemeError(ValueError):
    pass


def _lines(look, sq_look, visited):
    w = theme_style.clean(look)["light"]
    sq = theme_style.sq_clean(sq_look)["light"]
    un, vi = w["unvisited"], w["visited"]

    def road(color, px, outline, dash=""):
        d = f' dasharray="{dash}"' if dash else ""
        out = [f'<line stroke="#000000" width="{OUTLINE_PX}" fix="true"{d}/>'] if outline else []
        return out + [f'<line stroke="{color}" width="{px}" fix="true"{d}/>']

    rows = [f'<m e="way" k="wandrer" zoom-min="{ZOOM_WANDRER}">', '  <m v="unvisited">',
            '    <m k="surface" v="unpaved">']
    rows += ["      " + r for r in road(un["color"], UNVISITED_PX, un["outline"], UNPAVED_DASH)]
    rows += ['    </m>', '    <m k="surface" v="~">']
    rows += ["      " + r for r in road(un["color"], UNVISITED_PX, un["outline"])]
    rows += ['    </m>', '  </m>']
    if visited:
        rows += ['  <m v="visited">'] + ["    " + r for r in road(vi["color"], VISITED_PX, vi["outline"])] + ['  </m>']
    b = BOUNDARY
    rows += ['  <m v="boundary">', f'    <line stroke="{b["halo"]}" width="{b["halo_px"]}" fix="true"/>',
             f'    <line stroke="{b["color"]}" width="{b["px"]}" fix="true" dasharray="{b["dash"]}"/>', '  </m>', '</m>',
             '<m e="way" k="squadrat">']
    for level, zoom in (("big", ZOOM_SQ_BIG), ("small", ZOOM_SQ_SMALL)):
        s = sq[level]
        d = f' dasharray="{SQ_DASH}"' if s["dashed"] else ""
        rows += [f'  <m v="{level}" zoom-min="{zoom}">',
                 f'    <line stroke="{s["color"]}" width="{SQ_PX[level]}" fix="true"{d}/>', '  </m>']
    return rows + ['</m>']


def poi_rules(pois):
    """The POIs with their main key renamed to maprboy:<key> (as stored in our map file)."""
    return [{**p, "key": POI_PREFIX + p["key"]} for p in pois]


def block(pois=(), look=None, sq_look=None, visited=False):
    """Our rules, between the markers."""
    rows = [BEGIN, "<!-- maprBoy: Wandrer roads and Squadrats squares -->"] + _lines(look, sq_look, visited)
    if pois:
        rows += ["<!-- maprBoy: POIs -->"] + [
            r.replace('src="icons/', f'src="file:/{ICON_DIR}/') for r in poi_config.theme_block(poi_rules(pois))]
    return "\n".join("    " + r for r in rows + [END]) + "\n"


def without_block(text):
    """The theme without our block (as the device had it)."""
    if text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise ThemeError("the theme has a broken maprBoy block: put the Karoo's original theme back first")
    if BEGIN not in text:
        return text
    a = text.rindex("\n", 0, text.index(BEGIN)) + 1 if "\n" in text[:text.index(BEGIN)] else text.index(BEGIN)
    b = text.index(END) + len(END)
    if text[b:b + 1] == "\n":
        b += 1
    return text[:a] + text[b:]


def with_block(text, our_block):
    """The theme with our block (replacing an older one) just before </rendertheme>."""
    text = without_block(text)
    if not re.search(r"<rendertheme\b", text) or "</rendertheme>" not in text:
        raise ThemeError("this is not a VTM render theme (no <rendertheme>)")
    i = text.rindex("</rendertheme>")
    return text[:i] + our_block + text[i:]


def write_icons(pois, icon_dir, library=None):
    """Copy the icons the POIs use (library: themes/vtm/icons) and write their shape icons into icon_dir.
    Returns the file names."""
    import shutil
    from pathlib import Path
    library = Path(library or poi_config.ROOT / "themes" / "vtm" / "icons")
    Path(icon_dir).mkdir(parents=True, exist_ok=True)
    names = set()
    for p in pois:
        for icon in [p["icon"]] + [v.get("icon") for v in p.get("variants", [])]:
            if not icon:
                continue
            names.add(poi_config.icon_file(icon))
            if not poi_config.is_shape(icon):
                src = library / icon
                if not src.is_file():
                    raise ThemeError(f"icon not found: {src}")
                shutil.copyfile(src, Path(icon_dir) / icon)
    poi_config.write_shape_icons(pois, icon_dir)
    return sorted(names)
