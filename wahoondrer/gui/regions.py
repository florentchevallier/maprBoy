"""OpenStreetMap regions (Geofabrik extracts) that WahooMapsCreator keeps on disk.

WMC stores them in ~/wahooMapsCreatorData/_download/maps as <id>-latest.osm.pbf and
deletes/re-downloads a file by itself when it is older than 14 days. This module lists
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
                                            "parent": p.get("parent"), "url": url}
        _catalog.update(mtime=mtime, items=items)
    return _catalog["items"]


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


def downloaded():
    """Regions present on disk: [{key, name, size, date, age_days}], oldest first."""
    out = []
    if os.path.isdir(MAPS_DIR):
        for n in os.listdir(MAPS_DIR):
            if n.endswith(SUFFIX):
                p = os.path.join(MAPS_DIR, n)
                st = os.stat(p)
                key = n[:-len(SUFFIX)]
                out.append({"key": key, "name": pretty_name(key), "size": st.st_size,
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


def data_dir_info(base=""):
    """Where WMC's data really is, and whether it is inside `base` (the user's maprBoy folder)."""
    real = os.path.realpath(WMC_DIR)
    want = os.path.join(os.path.abspath(os.path.expanduser(base)), OSM_FOLDER) if base else ""
    size = 0
    if os.path.isdir(MAPS_DIR):
        size = sum(os.path.getsize(os.path.join(MAPS_DIR, n)) for n in os.listdir(MAPS_DIR)
                   if os.path.isfile(os.path.join(MAPS_DIR, n)))
    return {"wmc_dir": WMC_DIR, "real": real, "is_link": os.path.islink(WMC_DIR),
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
    if os.path.islink(WMC_DIR):                       # a link to somewhere else: just repoint it
        os.makedirs(target, exist_ok=True)
        os.remove(WMC_DIR)
        os.symlink(target, WMC_DIR)
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
        os.symlink(target, WMC_DIR)
        return "moved"
    os.makedirs(target, exist_ok=True)
    os.symlink(target, WMC_DIR)
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
