"""Look into the user's export folders (Wandrer KMZ/KML, Squadrats KML).

Reading a big Wandrer export takes seconds, so results are cached on disk
(~/.wahoondrer/cache.json, keyed by path + modification time + size) and the
reading runs in a background thread that reports its progress.
"""
import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request

from wahoondrer.kmz import read_export
from wahoondrer.tiles import tiles_for_bbox

CACHE_PATH = os.path.join(os.path.expanduser("~"), ".wahoondrer", "cache.json")
USER_AGENT = "maprBoy/0.1 (local tool for personal use)"
HUB_CELL = 0.05          # degrees: grid used to find where the rides are concentrated

_lock = threading.Lock()
_cache = None            # {"exports": {key: info}, "places": {"lat,lon": name}}


# ----------------------------------------------------------------- cache
def _data():
    global _cache
    if _cache is None:
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
        _cache.setdefault("exports", {})
        _cache.setdefault("places", {})
    return _cache


def _save():
    try:
        os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
        tmp = CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_data(), f)
        os.replace(tmp, CACHE_PATH)
    except OSError:
        pass


def _key(path):
    st = os.stat(path)
    return f"{path}|{int(st.st_mtime)}|{st.st_size}"


def _files(folder, exts):
    folder = os.path.expanduser(folder or "")
    if not os.path.isdir(folder):
        return []
    return sorted(os.path.join(folder, n) for n in os.listdir(folder)
                  if n.lower().endswith(exts) and not n.startswith(".")
                  and os.path.isfile(os.path.join(folder, n)))


# --------------------------------------------------------------- reading
def _hub(exp):
    """Centre of the densest 0.05-degree cell of the ridden roads: where the user rides most."""
    segs = [s for s in exp.segments.values() if s.state == "visited"] or list(exp.segments.values())
    cells = {}
    for s in segs:
        x, y = s.coords[0]
        k = (math.floor(x / HUB_CELL), math.floor(y / HUB_CELL))
        cells[k] = cells.get(k, 0) + 1
    if not cells:
        return None
    (kx, ky), _ = max(cells.items(), key=lambda kv: kv[1])
    return [round((kx + .5) * HUB_CELL, 4), round((ky + .5) * HUB_CELL, 4)]


def describe_export(path):
    """Read one Wandrer export and summarise it (cached until the file changes)."""
    with _lock:
        hit = _data()["exports"].get(_key(path))
    if hit:
        return hit
    st = os.stat(path)
    info = {"name": os.path.basename(path), "path": path, "size": st.st_size}
    try:
        exp = read_export(path)
        segs = list(exp.segments.values())
        ts = exp.timestamp
        info.update(
            activity=exp.activity, timestamp=ts,
            date=f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}" if ts else time.strftime("%Y-%m-%d", time.localtime(st.st_mtime)),
            traveled=sum(1 for s in segs if s.state == "visited"),
            untraveled=sum(1 for s in segs if s.state == "unvisited" and not s.unpaved),
            unpaved=sum(1 for s in segs if s.state == "unvisited" and s.unpaved),
            boundaries=len(exp.boundaries),
            bbox=list(exp.bbox) if segs else None,
            hub=_hub(exp),
            tiles=[list(t) for t in tiles_for_bbox(*exp.bbox)] if segs else [])
    except Exception as exc:  # unreadable file: report it, do not crash the page
        info["error"] = str(exc)
    with _lock:
        _data()["exports"][_key(path)] = info
        _save()
    return info


def cached_info(path):
    with _lock:
        return _data()["exports"].get(_key(path))


def scan_wandrer(folder):
    """Infos of the exports found in the folder. Files not read yet are flagged 'pending'."""
    out = []
    for p in _files(folder, (".kmz", ".kml")):
        info = cached_info(p)
        out.append(info or {"name": os.path.basename(p), "path": p, "pending": True,
                            "size": os.path.getsize(p)})
    return sorted(out, key=lambda i: (i.get("timestamp", ""), i["name"]), reverse=True)


# ------------------------------------------------------- place names
_last_request = [0.0]
_offline_until = [0.0]     # after a failed lookup, do not try again for a while


def lookup_place(lon, lat):
    """Nearest town name from OpenStreetMap's Nominatim (politeness: 1 request/second, cached)."""
    key = f"{lat:.2f},{lon:.2f}"
    with _lock:
        if key in _data()["places"]:
            return _data()["places"][key]
    if time.time() < _offline_until[0]:
        return None
    wait = 1.1 - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    _last_request[0] = time.time()
    url = "https://nominatim.openstreetmap.org/reverse?" + urllib.parse.urlencode(
        {"format": "jsonv2", "zoom": 10, "accept-language": "en", "lat": lat, "lon": lon})
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as r:
            addr = json.load(r).get("address", {})
    except Exception:
        _offline_until[0] = time.time() + 600     # offline or refused: retry in 10 minutes
        return None
    name = next((addr[k] for k in ("city", "town", "village", "municipality", "county", "state")
                 if addr.get(k)), None)
    with _lock:
        _data()["places"][key] = name or ""
        _save()
    return name or ""


# ------------------------------------------------------ background scan
class Scanner:
    """Reads the pending exports in a thread; the page polls `status()`."""

    def __init__(self):
        self.lock = threading.Lock()
        self._state = {"running": False, "done": 0, "total": 0, "current": "", "phase": ""}

    def status(self):
        with self.lock:
            return dict(self._state)

    def start(self, folder, geocode=True):
        with self.lock:
            if self._state["running"]:
                return False
            self._state = {"running": True, "done": 0, "total": 0, "current": "", "phase": "reading"}
        threading.Thread(target=self._run, args=(folder, geocode), daemon=True).start()
        return True

    def _set(self, **kw):
        with self.lock:
            self._state.update(kw)

    def _run(self, folder, geocode):
        try:
            files = _files(folder, (".kmz", ".kml"))
            pending = [p for p in files if cached_info(p) is None]
            self._set(total=len(pending))
            for i, p in enumerate(pending, 1):
                self._set(current=f"{os.path.basename(p)} ({os.path.getsize(p) // 1024 // 1024 or 1} MB)", done=i - 1)
                describe_export(p)
            self._set(done=len(pending), current="")
            if geocode:
                todo = [cached_info(p) for p in files]
                todo = [i for i in todo if i and i.get("hub") and i.get("place") is None]
                if todo:
                    self._set(phase="places", total=len(todo), done=0)
                for n, info in enumerate(todo, 1):
                    self._set(current=info["name"], done=n - 1)
                    place = lookup_place(*info["hub"])
                    if place is None and time.time() < _offline_until[0]:
                        break
                    if place is not None:
                        with _lock:
                            info["place"] = place
                            _save()
                self._set(done=len(todo), current="")
        finally:
            self._set(running=False, phase="")


# --------------------------------------------------------------- others
def squadrats_files(folder):
    """Squadrats KML files, most recent first (date in the name, else file date)."""
    out = []
    for p in _files(folder, (".kml",)):
        m = re.search(r"(\d{4})-?(\d{2})-?(\d{2})", os.path.basename(p))
        date = "-".join(m.groups()) if m else time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(p)))
        out.append({"name": os.path.basename(p), "path": p, "date": date,
                    "size": os.path.getsize(p), "_sort": (date, os.path.getmtime(p))})
    out.sort(key=lambda i: i["_sort"], reverse=True)
    for i in out:
        del i["_sort"]
    return out


def list_dir(path):
    """Sub-folders of a folder (for the page's own folder browser, used if the native dialog fails)."""
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        path = os.path.expanduser("~")
    try:
        names = sorted(n for n in os.listdir(path)
                       if not n.startswith(".") and os.path.isdir(os.path.join(path, n)))
    except OSError:
        names = []
    parent = os.path.dirname(path)
    return {"path": path, "parent": parent if parent != path else None, "dirs": names}
