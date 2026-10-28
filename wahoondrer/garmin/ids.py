"""Ids and names that let several maprBoy maps sit on one Garmin device (e.g. Munich and Athens).

Each map built from the page gets its own family id (with its own TYP: the POI icons of two maps may differ),
its own map numbers and a name with the place and the date, shown in the device's map list. The ids come
from the time of the build: overlay 10000 + 2 n, base map 10000 + 2 n + 1 (n = minutes since 1970, modulo
44000, so about 30 days before an id comes back), map numbers <family id>001, <family id>002... (mkgmap
counts one per tile). The command lines keep the tested defaults (1299 / 6324) when no id is given.
Seen on an Edge 530: a family id other than Wandrer's 1299 (6324) and a file name other than gmapsupp.img
(maprboy-base.img) work. Not checked yet: two maprBoy maps on one device, other names on a Forerunner."""
import time

MAX_TILES = 999            # the map numbers of a family id: <id>001 ... <id>999


def build_ids(now=None):
    """{"overlay": (family id, first map number), "base": (...)} for a build started at `now` (seconds)."""
    n = int((time.time() if now is None else now) // 60) % 44000
    fid = 10000 + 2 * n
    return {"overlay": (fid, f"{fid}001"), "base": (fid + 1, f"{fid + 1}001")}


def check_tiles(count):
    if count > MAX_TILES:
        raise ValueError(f"the area needs {count} map tiles, more than {MAX_TILES}: choose a smaller area")
