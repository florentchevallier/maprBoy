"""The mkgmap style: which tags written by osmxml.py become which Garmin line type.

Types 0x01-0x07 were checked on a device. A device draws a lower type OVER a higher one, so the boundaries
(a wide band meant to show around the roads) get the highest type. Rules are tried in order, the first match wins."""
import os

# Garmin line type of each kind of road
TYPE_UNVISITED = 0x06
TYPE_VISITED = 0x05
TYPE_UNVISITED_UNPAVED = 0x04
TYPE_VISITED_UNPAVED = 0x03
TYPE_BOUNDARY = 0x07           # highest type: drawn under every road
TYPE_SQUADRAT_BIG = 0x01
TYPE_SQUADRAT_SMALL = 0x02

RESOLUTION = 16   # visible at every zoom the device offers for an overlay

LINES = f"""wandrer=unvisited & surface=unpaved [0x{TYPE_UNVISITED_UNPAVED:02x} resolution {RESOLUTION}]
wandrer=unvisited [0x{TYPE_UNVISITED:02x} resolution {RESOLUTION}]
wandrer=visited & surface=unpaved [0x{TYPE_VISITED_UNPAVED:02x} resolution {RESOLUTION}]
wandrer=visited [0x{TYPE_VISITED:02x} resolution {RESOLUTION}]
wandrer=boundary [0x{TYPE_BOUNDARY:02x} resolution {RESOLUTION}]
squadrat=big [0x{TYPE_SQUADRAT_BIG:02x} resolution {RESOLUTION}]
squadrat=small [0x{TYPE_SQUADRAT_SMALL:02x} resolution {RESOLUTION}]
"""

FILES = {
    "version": "1\n",
    "info": "version: 1\nsummary: maprBoy Wandrer overlay\ndescription { Wandrer roads for mkgmap }\n",
    "lines": LINES,
    "points": "",
    "polygons": "",
}


def write_style(directory, points=""):
    """Write the style folder mkgmap reads (--style-file); `points` = the POI rules (see pois.py)."""
    os.makedirs(directory, exist_ok=True)
    for name, text in {**FILES, "points": points}.items():
        with open(os.path.join(directory, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return directory
