"""OpenStreetMap regions (Geofabrik extracts) that WahooMapsCreator keeps on disk.

WMC stores them in ~/wahooMapsCreatorData/_download/maps as <id>-latest.osm.pbf and
deletes/re-downloads a file by itself when it is older than the chosen age (14 days by default,
settings "osm_max_days"; 0 = never). This module lists
them (name, size, date) and downloads or updates them, using the same Geofabrik index
(_download/geofabrik.json) and the same file names, so WMC finds them afterwards.
"""
import json
import os
import re
import shutil
import threading
import time
import unicodedata
import urllib.error
import urllib.request

WMC_DIR = os.path.join(os.path.expanduser("~"), "wahooMapsCreatorData")   # hard-coded by WMC
DL_DIR = os.path.join(WMC_DIR, "_download")
MAPS_DIR = os.path.join(DL_DIR, "maps")
GEOFABRIK = os.path.join(DL_DIR, "geofabrik.json")
SUFFIX = "-latest.osm.pbf"
OSM_FOLDER = "OSM_data"
MAX_TRIES = 4                      # attempts per region before giving up
RETRY_DELAYS = (2, 5, 10)          # seconds to wait when only failing regions are left
USER_AGENT = "maprBoy/0.1 (local tool for personal use)"

_catalog = {"mtime": None, "items": {}}
_TAG = re.compile(r"<[^>]*>")


def clean_name(name):
    """Geofabrik names sometimes contain HTML ('Østlandet<br />(Eastern Norway)'): make plain text."""
    return re.sub(r"\s+", " ", _TAG.sub(" ", name or "")).strip()


def sort_key(text):
    """Accent-insensitive key: Ø->O, Î->I, Ú->U, ... (and case-insensitive)."""
    text = text.replace("Ø", "O").replace("ø", "o").replace("Đ", "D").replace("đ", "d") \
               .replace("Ł", "L").replace("ł", "l").replace("ß", "ss").replace("Æ", "AE").replace("æ", "ae")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).casefold()


def file_key(region_id):
    """File name part used by WMC for a Geofabrik id ('a/b' -> 'a_b')."""
    return region_id.replace("/", "_")


def catalog():
    """{file_key: {id, name, parent, url}} read from Geofabrik's index (empty if not downloaded yet)."""
    try:
        mtime = os.path.getmtime(GEOFABRIK)
    except OSError:
        return {}
    if _catalog["mtime"] != mtime:
        with open(GEOFABRIK, encoding="utf-8") as f:
            feats = json.load(f).get("features", [])
        items = {}
        for ft in feats:
            p = ft.get("properties", {})
            url = (p.get("urls") or {}).get("pbf")
            if p.get("id") and url:
                items[file_key(p["id"])] = {"id": p["id"], "name": clean_name(p.get("name", p["id"])),
                                            "parent": p.get("parent"), "url": url,
                                            "rings": _outer_rings(ft.get("geometry"))}
        _catalog.update(mtime=mtime, items=items)
    return _catalog["items"]


def _outer_rings(geom):
    """Outer rings [[(lon, lat), ...], ...] of a GeoJSON Polygon / MultiPolygon (holes are left out)."""
    if not isinstance(geom, dict):
        return []
    polys = geom.get("coordinates") or []
    if geom.get("type") == "Polygon":
        polys = [polys]
    elif geom.get("type") != "MultiPolygon":
        return []
    return [[(round(p[0], 4), round(p[1], 4)) for p in poly[0]] for poly in polys if poly and len(poly[0]) >= 3]


def shapes(keys):
    """{key: {"name", "rings"}} of the regions (for the map); unknown keys are left out."""
    cat = catalog()
    return {k: {"name": pretty_name(k), "rings": cat[k]["rings"]} for k in keys if k in cat and cat[k]["rings"]}


def _in_ring(x, y, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _segment_hits_box(a, b, box):
    """Liang-Barsky: does the segment a-b cross the box (w, s, e, n)?"""
    (x0, y0), (x1, y1) = a, b
    w, s, e, n = box
    t0, t1, dx, dy = 0.0, 1.0, x1 - x0, y1 - y0
    for p, q in ((-dx, x0 - w), (dx, e - x0), (-dy, y0 - s), (dy, n - y0)):
        if p == 0:
            if q < 0:
                return False
        else:
            t = q / p
            if p < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
            if t0 > t1:
                return False
    return True


def _ring_touches_box(ring, box):
    w, s, e, n = box
    if any(_in_ring(x, y, ring) for x, y in ((w, s), (w, n), (e, s), (e, n), ((w + e) / 2, (s + n) / 2))):
        return True
    return any(_segment_hits_box(ring[i - 1], ring[i], box) for i in range(1, len(ring)))


def tiles_of(keys):
    """Zoom-8 tiles ["x/y", ...] touched by the regions."""
    from ..tiles import tile_bbox, tiles_for_bbox
    cat, out = catalog(), set()
    for k in keys:
        for ring in (cat.get(k) or {}).get("rings", []):
            xs, ys = [p[0] for p in ring], [p[1] for p in ring]
            for x, y in tiles_for_bbox(min(xs), min(ys), max(xs), max(ys)):
                if f"{x}/{y}" not in out and _ring_touches_box(ring, tile_bbox(x, y)):
                    out.add(f"{x}/{y}")
    return sorted(out, key=lambda t: tuple(int(v) for v in t.split("/")))


def pretty_name(key):
    """'Oberbayern (Bayern, Germany)' style label for a file key."""
    cat = catalog()
    item = cat.get(key)
    if not item:
        return key
    chain, parent, guard = [], item.get("parent"), 0
    while parent and guard < 4:
        p = cat.get(file_key(parent))
        if not p or not p.get("parent"):       # stop before the continent
            break
        chain.append(p["name"])
        parent = p.get("parent")
        guard += 1
    return f'{item["name"]} ({", ".join(chain)})' if chain else item["name"]


def continent_of(key):
    """Name of the continent a region belongs to (the region itself for a continent), "" when unknown."""
    cat = catalog()
    item, guard = cat.get(key), 0
    while item and item.get("parent") and guard < 8:
        item, guard = cat.get(file_key(item["parent"])), guard + 1
    return item["name"] if item else ""


def downloaded():
    """Regions present on disk: [{key, name, continent, size, date, age_days}], oldest first."""
    out = []
    if os.path.isdir(MAPS_DIR):
        for n in os.listdir(MAPS_DIR):
            if n.endswith(SUFFIX):
                p = os.path.join(MAPS_DIR, n)
                st = os.stat(p)
                key = n[:-len(SUFFIX)]
                out.append({"key": key, "name": pretty_name(key), "continent": continent_of(key), "size": st.st_size,
                            "date": time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
                            "age_days": int((time.time() - st.st_mtime) // 86400)})
    return sorted(out, key=lambda r: r["age_days"], reverse=True)


def available():
    """All regions Geofabrik offers: [{key, name (short), parent_key, label}], for the nested list."""
    cat = catalog()
    out = []
    for k, it in cat.items():
        pk = file_key(it["parent"]) if it.get("parent") else ""
        out.append({"key": k, "name": it["name"], "parent_key": pk if pk in cat else "",
                    "label": pretty_name(k)})
    return sorted(out, key=lambda r: sort_key(r["name"]))


def delete(key):
    """Remove one downloaded region (and any half-downloaded leftover). Returns freed bytes."""
    if not re.fullmatch(r"[A-Za-z0-9_.\-]+", key or ""):
        raise ValueError("Invalid region name.")
    freed = 0
    for suffix in (SUFFIX, SUFFIX + ".part"):
        p = os.path.join(MAPS_DIR, key + suffix)
        if os.path.isfile(p):
            freed += os.path.getsize(p)
            os.remove(p)
    if not freed:
        raise ValueError("This region is not on disk.")
    return freed


def _is_link(path):
    """A symbolic link, or on Windows a directory junction (what we make there: no admin rights needed)."""
    if os.path.islink(path):
        return True
    if os.name == "nt" and os.path.isdir(path):
        try:
            os.readlink(path)                         # Python 3.8+ reads junctions too
            return True
        except (OSError, ValueError):
            return False
    return False


def _make_link(target, link):
    """link -> target: a symbolic link, or on Windows a directory junction (a symbolic link needs admin rights or
    the developer mode there)."""
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(target, link)
    else:
        os.symlink(target, link)


def _remove_link(link):
    if os.name == "nt" and not os.path.islink(link):
        os.rmdir(link)                                # removes the junction only, never what it points to
    else:
        os.remove(link)


def data_dir_info(base=""):
    """Where WMC's data really is, and whether it is inside `base` (the user's maprBoy folder)."""
    real = os.path.realpath(WMC_DIR)
    want = os.path.join(os.path.abspath(os.path.expanduser(base)), OSM_FOLDER) if base else ""
    size = 0
    if os.path.isdir(MAPS_DIR):
        size = sum(os.path.getsize(os.path.join(MAPS_DIR, n)) for n in os.listdir(MAPS_DIR)
                   if os.path.isfile(os.path.join(MAPS_DIR, n)))
    return {"wmc_dir": WMC_DIR, "real": real, "is_link": _is_link(WMC_DIR),
            "exists": os.path.exists(WMC_DIR), "wanted": want, "size": size,
            "ok": bool(want) and real == os.path.realpath(want)}


def link_data_dir(base, move=False):
    """Keep WMC's data in <base>/OSM_data, and make ~/wahooMapsCreatorData a link to it.

    WMC always reads ~/wahooMapsCreatorData, so the link is what lets the data live in the
    user's own folder. Existing data is only moved when move=True.
    """
    target = os.path.join(os.path.abspath(os.path.expanduser(base)), OSM_FOLDER)
    if os.path.realpath(WMC_DIR) == os.path.realpath(target):
        os.makedirs(target, exist_ok=True)
        return "already"
    if _is_link(WMC_DIR):                             # a link to somewhere else: just repoint it
        os.makedirs(target, exist_ok=True)
        _remove_link(WMC_DIR)
        _make_link(target, WMC_DIR)
        return "repointed"
    if os.path.isdir(WMC_DIR):
        if not move:
            return "needs_move"
        if os.path.exists(target) and os.listdir(target):
            raise ValueError(f"{target} already contains files: empty it or choose another folder.")
        if os.path.isdir(target):
            os.rmdir(target)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.move(WMC_DIR, target)                  # instant on the same disk, a copy otherwise
        _make_link(target, WMC_DIR)
        return "moved"
    os.makedirs(target, exist_ok=True)
    _make_link(target, WMC_DIR)
    return "linked"


class Downloader:
    """Downloads one or several regions in a thread (.part file, renamed when complete)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cancel_flag = False
        self._state = {"running": False, "queue": [], "current": "", "bytes": 0, "total": 0,
                       "done": [], "failed": {}, "error": ""}

    def status(self):
        with self.lock:
            return dict(self._state)

    def start(self, keys, force=False):
        cat = catalog()
        if not cat:
            raise ValueError("The Geofabrik region list is not on disk yet: run 'Check the plan' once first.")
        unknown = [k for k in keys if k not in cat]
        if unknown:
            raise ValueError("Unknown region: " + ", ".join(unknown))
        with self.lock:
            if self._state["running"]:
                raise ValueError("A download is already running.")
            self.cancel_flag = False
            self._state = {"running": True, "queue": list(keys), "current": "", "bytes": 0,
                           "total": 0, "done": [], "failed": {}, "error": ""}
        threading.Thread(target=self._run, args=(list(keys), force), daemon=True).start()

    def cancel(self):
        self.cancel_flag = True

    def _set(self, **kw):
        with self.lock:
            self._state.update(kw)

    def _fetch(self, key, url, target):
        """Download one file. Raises Unavailable (really gone), Retry (worth trying again) or OSError."""
        part = target + ".part"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            r = urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError as exc:
            if exc.code in (404, 410):
                raise Unavailable(f"Geofabrik does not offer this region any more (HTTP {exc.code}).")
            if exc.code in (301, 302, 303, 307, 308, 429, 500, 502, 503, 504):
                # Geofabrik sometimes answers with a redirect loop or a busy signal and is fine a few seconds later
                raise Retry(f"Geofabrik answered HTTP {exc.code} ({exc.reason}).")
            raise OSError(f"HTTP {exc.code} {exc.reason}")
        except urllib.error.URLError as exc:
            raise Retry(f"network problem: {exc.reason}")
        with r, open(part, "wb") as f:
            total = int(r.headers.get("Content-Length") or 0)
            self._set(total=total)
            got = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                if self.cancel_flag:
                    raise InterruptedError("cancelled")
                f.write(chunk)
                got += len(chunk)
                self._set(bytes=got)
        if total and got != total:
            raise Retry(f"incomplete download ({got} of {total} bytes)")
        os.replace(part, target)

    def _wait(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            if self.cancel_flag:
                raise InterruptedError("cancelled")
            time.sleep(0.2)

    def _run(self, keys, force):
        """Queue with retries: a region that fails goes to the END of the queue (the others are
        tried first); when only failing ones are left we wait a little longer before each new try."""
        os.makedirs(MAPS_DIR, exist_ok=True)
        cat = catalog()
        queue = list(keys)
        tries = {}
        try:
            while queue:
                key = queue.pop(0)
                target = os.path.join(MAPS_DIR, key + SUFFIX)
                if os.path.isfile(target) and not force:
                    with self.lock:
                        self._state["done"].append(key)
                    continue
                self._set(current=key, bytes=0, total=0)
                try:
                    self._fetch(key, cat[key]["url"], target)
                    with self.lock:
                        self._state["done"].append(key)
                        self._state["failed"].pop(key, None)
                except InterruptedError:
                    raise
                except Retry as exc:
                    tries[key] = tries.get(key, 0) + 1
                    if tries[key] >= MAX_TRIES:
                        with self.lock:
                            self._state["failed"][key] = f"{exc} Tried {MAX_TRIES} times: try again in a few minutes."
                    else:
                        queue.append(key)
                        self._set(current=f"{key} (try {tries[key] + 1} of {MAX_TRIES} soon)")
                        if all(tries.get(k) for k in queue):      # only failing regions left: pause
                            self._wait(RETRY_DELAYS[min(tries[key], len(RETRY_DELAYS)) - 1])
                except (Unavailable, OSError) as exc:
                    with self.lock:
                        self._state["failed"][key] = str(exc)
                finally:
                    try:
                        os.remove(target + ".part")
                    except OSError:
                        pass
        except InterruptedError:
            self._set(error="cancelled")
        except Exception as exc:
            self._set(error=str(exc))
        finally:
            self._set(running=False, current="")


class Unavailable(Exception):
    """The region is listed in the index but cannot be downloaded any more."""


class Retry(Exception):
    """Temporary problem: trying again later usually works."""


# ------------------------------------------------------------------ for the Garmin maps
GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1.json"


INDEX_MAX_DAYS = 7      # Geofabrik's region list is renewed after this many days


def normalize_index(path=None):
    """Make Geofabrik's region list readable by WMC 4.4.0, which takes a region without "parent" for a continent.
    The list of November 2026 writes "parent": null instead, and WMC stopped on it ("'<' not supported between
    instances of 'NoneType' and 'str'"): those null parents are taken out. Returns how many; the file is only
    written again when there was one."""
    path = path or GEOFABRIK
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for ft in data.get("features", []):
        props = ft.get("properties") or {}
        if "parent" in props and props["parent"] is None:
            del props["parent"]
            n += 1
    if n:
        with open(path + ".part", "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(path + ".part", path)
    return n


def refresh_index(max_days=INDEX_MAX_DAYS, log=print):
    """Geofabrik's region list, which gives the address of each region: downloaded when it is missing or older
    than max_days (in November 2026 Geofabrik moved every region to a folder of its own, and a list of the week
    before only gave HTTP 404), the old one kept when the download fails, then made readable by WMC
    (normalize_index). Raises OSError only when there is no list at all. Used by every run, WMC's or not (WMC
    downloads the list only when it is missing, and never renews it)."""
    old = os.path.isfile(GEOFABRIK)
    if not old or (time.time() - os.path.getmtime(GEOFABRIK)) / 86400 > max_days:
        part = GEOFABRIK + ".part"
        try:
            os.makedirs(DL_DIR, exist_ok=True)
            req = urllib.request.Request(GEOFABRIK_INDEX, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                shutil.copyfileobj(r, f)
            with open(part, encoding="utf-8") as f:
                json.load(f)                       # a whole list, not an error page or a cut download
            os.replace(part, GEOFABRIK)
        except (OSError, ValueError) as exc:
            try:
                os.remove(part)
            except OSError:
                pass
            if not old:
                raise OSError(f"Geofabrik's region list could not be downloaded: {exc}")
            log(f"! Geofabrik's region list could not be renewed ({exc}): the copy on disk is used.")
    try:
        normalize_index()
    except (OSError, ValueError) as exc:
        log(f"! Geofabrik's region list could not be read ({exc}).")


def ensure_catalog():
    """Geofabrik's region list, renewed when it is old (see refresh_index); the Garmin and Karoo runs and the
    names of the maps use it."""
    refresh_index()


SAMPLES = 40        # points per side of the area when choosing its regions (about 2.5 km apart on a z8 tile)


def _area(rings):
    """Rough surface of a region (square degrees, shoelace), to tell a small region from a large one."""
    return sum(abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(r, r[1:] + r[:1]))) / 2 for r in rings)


def regions_for_bbox(bbox):
    """Keys of the OSM extracts a Garmin map of bbox (W, S, E, N) needs: for each point of the area, the
    SMALLEST Geofabrik region containing it (Oberbayern rather than Bayern, Germany or the "Germany, Austria,
    Switzerland" extract). A small region touching the edge of the area between two points is added too."""
    cat = catalog()
    touching = {k: it for k, it in cat.items()
                if any(_ring_touches_box(r, bbox) or _box_in_ring(bbox, r) for r in it.get("rings", []))}
    size = {k: _area(it["rings"]) for k, it in touching.items()}
    parents = {file_key(it["parent"]) for it in cat.values() if it.get("parent")}
    w, s, e, n = bbox
    chosen, sampled = set(), set()
    for i in range(SAMPLES):
        for j in range(SAMPLES):
            x, y = w + (i + 0.5) * (e - w) / SAMPLES, s + (j + 0.5) * (n - s) / SAMPLES
            inside = [k for k, it in touching.items() if any(_in_ring(x, y, r) for r in it["rings"])]
            sampled.update(inside)
            if inside:
                chosen.add(min(inside, key=lambda k: size[k]))
    chosen |= {k for k in touching if k not in sampled and k not in parents}      # slivers at the edge
    return sorted(chosen)


def _box_in_ring(box, ring):
    w, s, e, n = box
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    return min(xs) <= w and e <= max(xs) and min(ys) <= s and n <= max(ys) and _in_ring((w + e) / 2, (s + n) / 2, ring)


def region_path(key):
    return os.path.join(MAPS_DIR, key + SUFFIX)


def to_fetch(keys, max_days):
    """The regions of `keys` that are missing, or older than max_days (0 = never too old)."""
    out = []
    for k in keys:
        p = region_path(k)
        if not os.path.isfile(p):
            out.append(k)
        elif max_days and (time.time() - os.path.getmtime(p)) / 86400 > max_days:
            out.append(k)
    return out


SPACE_FACTOR = 3                          # free space needed for a whole continent: 3 times its size


def remote_size(url, timeout=20):
    """Size in bytes of the file at url (HEAD request), or None when it cannot be known."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.headers.get("Content-Length") or 0) or None
    except (OSError, ValueError):
        return None


def continent_check(keys, size_of=remote_size, free=None):
    """Lines to print before downloading `keys`, and the reason to stop (or None). A continent (a region with no
    parent) is only chosen when part of the area lies outside every smaller region, mostly open sea: it is
    downloaded all the same, with a warning, when the disk has SPACE_FACTOR times its size free."""
    cat = catalog()
    notes = []
    for k in keys:
        it = cat.get(k)
        if not it or it.get("parent"):
            continue
        size = size_of(it["url"])
        if size is None:
            notes.append(f"! {pretty_name(k)}: the whole continent is downloaded, because part of the area lies "
                         "outside every smaller region (open sea?). Press Stop if this is not what you want.")
            continue
        if free is None:
            os.makedirs(MAPS_DIR, exist_ok=True)
            free = shutil.disk_usage(MAPS_DIR).free
        gb = size / 1e9
        if free < SPACE_FACTOR * size:
            return notes, (f"{pretty_name(k)}: the whole continent ({gb:.1f} GB) would be downloaded, because part "
                           f"of the area lies outside every smaller region (open sea?), but only {free / 1e9:.1f} GB "
                           f"are free ({SPACE_FACTOR * gb:.0f} GB needed). Draw the area closer to the coast, or "
                           "free some space.")
        notes.append(f"! {pretty_name(k)}: the whole continent is downloaded ({gb:.1f} GB), because part of the "
                     "area lies outside every smaller region (open sea?). Press Stop if this is not what you want.")
        free -= size
    return notes, None


def continent_question(keys, max_days, size_of=remote_size, free=None):
    """The whole continents a run fetching `keys` would download, asked about in the page before the run starts:
    [{"name", "gb"}] (gb None when unknown). [] when none would be downloaded, or when the run would stop before
    the download for lack of room (it says why)."""
    keys = to_fetch(keys, max_days)
    sizes = {}

    def size(url):
        if url not in sizes:
            sizes[url] = size_of(url)
        return sizes[url]
    if continent_check(keys, size, free)[1]:
        return []
    cat = catalog()
    return [{"name": pretty_name(k), "gb": None if size(cat[k]["url"]) is None else round(size(cat[k]["url"]) / 1e9, 1)}
            for k in keys if cat.get(k) and not cat[k].get("parent")]


def main(argv=None):
    """python3 -m wahoondrer.gui.regions --fetch KEY [KEY ...] [--max-days N]: download the missing regions
    (and those older than N days) now, with progress lines; used by the Garmin build of the page."""
    import argparse
    import sys
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", nargs="+", required=True)
    ap.add_argument("--max-days", type=int, default=0)
    a = ap.parse_args(argv)
    keys = to_fetch(a.fetch, a.max_days)
    if not keys:
        print("OSM data: every region is on disk and recent enough")
        return
    notes, stop = continent_check(keys)
    for line in notes:
        print(line, flush=True)
    if stop:
        sys.exit(stop)
    print("Downloading the OSM data of: " + ", ".join(pretty_name(k) for k in keys), flush=True)
    import signal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit("Stopped: the unfinished download was removed."))
    try:
        _fetch_now(keys)
    finally:                              # stopped from the page: never leave a half file (.part) behind
        for k in keys:
            try:
                os.remove(region_path(k) + ".part")
            except OSError:
                pass


def _fetch_now(keys):
    import sys
    d = Downloader()
    old = [k for k in keys if os.path.isfile(region_path(k))]
    t = threading.Thread(target=d._run, args=(keys, bool(old)), daemon=True)
    d._state["running"] = True
    t.start()
    from ..progress import MARK, gui
    bar, shown = gui(), 0
    while t.is_alive():
        t.join(1 if bar else 10)          # the page shows a bar; a terminal gets a line every 10 seconds
        st = d.status()
        if not st["current"]:
            continue
        name = pretty_name(st["current"].split(" ")[0])
        got, total = st["bytes"] >> 20, st["total"] >> 20
        text = f"{name}: {got} / {total} MB ({100 * got // total} %)" if total else f"{name}: {got} MB"
        if bar:
            print(f"{MARK}P|{text}", flush=True)
        elif time.time() - shown >= 9:
            print("  " + text, flush=True)
            shown = time.time()
    if bar:
        print(f"{MARK}P|", flush=True)             # clears the bar
    st = d.status()
    if st["error"] == "cancelled":
        sys.exit("Cancelled.")
    failed = dict(st["failed"])
    if st["error"]:                       # stopped as a whole: every region not downloaded failed
        failed.update({k: st["error"] for k in keys if k not in st["done"] and k not in failed})
    # a region that could not be renewed but is already on disk is used as it is (seen in November 2026: Geofabrik
    # redirected Oberbayern to a page that did not exist); the run only stops for a region it does not have at all
    kept = [k for k in failed if os.path.isfile(region_path(k))]
    for k in kept:
        day = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(region_path(k))))
        print(f"! {pretty_name(k)}: {failed[k]} The copy already on disk (from {day}) is used.", flush=True)
    missing = [k for k in failed if k not in kept]
    if missing:
        for k in missing:
            print(f"! {pretty_name(k)}: {failed[k]}", flush=True)
        sys.exit("Some OSM regions could not be downloaded: see above")
    print(f"Downloaded {len(st['done'])} region(s)" + (f"; {len(kept)} kept as they were" if kept else ""), flush=True)


if __name__ == "__main__":
    main()
