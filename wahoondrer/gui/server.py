"""The local web server. It only listens on 127.0.0.1 and runs the project's own scripts."""
import json
import mimetypes
import os
import re
import subprocess
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .. import contour, devcopy, poi_config, roam
from . import jobs, native, regions, scan, settings

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JOB = jobs.Job()
SCANNER = scan.Scanner()
DOWNLOADER = regions.Downloader()
MAX_UPLOAD = 1 << 30      # 1 GB


def effective_activity(s, infos):
    """bike / foot / combined to use: the type chosen for this device, else the type of the newest export.
    'any' (all types mixed) is Pro only; it returns ''."""
    act = s["activity"]
    if act == "any" and s["mode"] == "pro":
        return ""
    if act in settings.ACTIVITIES:
        return act
    for i in infos:                          # infos are sorted newest first
        if i.get("activity") in settings.ACTIVITIES:
            return i["activity"]
    return "bike"


def annotate(s, infos):
    """Add 'active' (will be used in the run) and 'why' to each info; return the activity used."""
    usable = [i for i in infos if "error" not in i and not i.get("pending")]
    act = effective_activity(s, usable)
    for i in infos:
        if i.get("pending"):
            i["active"], i["why"] = False, "not read yet"
        elif "error" in i:
            i["active"], i["why"] = False, "unreadable"
        elif i["name"] in s["excluded_exports"]:
            i["active"], i["why"] = False, "unticked"
        elif act and i.get("activity") and i["activity"] != act:
            i["active"], i["why"] = False, f"other type ({i['activity']})"
        else:
            i["active"], i["why"] = True, ""
    return act


def pick_squadrats(s, files):
    if s["squadrats"]["file"]:
        for f in files:
            if f["name"] == s["squadrats"]["file"]:
                return f
    return files[0] if files else None


def state(s):
    """Everything the page needs to draw itself (only reads caches: never slow)."""
    infos = scan.scan_wandrer(s["folders"]["wandrer"])
    act = annotate(s, infos)
    sq = scan.squadrats_files(s["folders"]["squadrats"])
    chosen = pick_squadrats(s, sq)
    return {"settings": s, "exports": infos, "activity_used": act,
            "activities": sorted({i["activity"] for i in infos if i.get("activity")}),
            "pending": sum(1 for i in infos if i.get("pending")),
            "squadrats": sq, "squadrats_used": chosen["name"] if chosen else "",
            "scan": SCANNER.status(), "job": JOB.snapshot(0), "env": jobs.check_environment()}


def poi_catalog():
    """The POIs the page offers (config/poi-rules.json) and the icons that can be chosen."""
    paths = poi_config.Paths()
    pois = []
    for p in poi_config.read_rules():
        pois.append({"id": poi_config.poi_id(p), "key": p["key"], "value": p["value"],
                     "label": p.get("label") or poi_config.poi_id(p), "zoom": p["zoom"],
                     "size": p.get("size"), "icon": p["icon"], "enabled": p.get("enabled", True),
                     "variants": [{"tag": v["tag"], "value": v["value"], "icon": v["icon"],
                                   "label": v.get("label") or f'{v["tag"]} = {v["value"]}'} for v in p.get("variants", [])],
                     "hide_if": p.get("hide_if", {}), "only_if": p.get("only_if", {}),
                     "group": p.get("group", ""), "group_label": p.get("group_label", "")})
    return {"pois": pois, "icons": poi_config.library_icons(paths.icon_dir),
            "shapes": list(poi_config.SHAPES), "colors": list(poi_config.COLORS),
            "zoom_min": poi_config.ZOOM_MIN, "zoom_max": poi_config.ZOOM_MAX}


def is_junk(name):
    """Files the operating system adds on its own (.DS_Store, ._x, Thumbs.db, desktop.ini...): never offered."""
    return name.startswith(".") or name.lower() in ("thumbs.db", "ehthumbs.db", "desktop.ini")


def results(out, model="bolt2"):
    """What a build left in the output folder: the files to copy to the device (theme, then tiles).
    model "roam1": the files of Output/roam_v1/ (.map.gz, cfg file), laid out like an ELEMNT Roam v1."""
    out = os.path.abspath(os.path.expanduser(out or ""))
    is_roam = model == "roam1"
    maps = os.path.join(out, roam.DIR if is_roam else "wahoondrer_maps", "maps")
    names_dir = os.path.join(out, "wahoondrer_maps", "maps", "tiles", "8")      # the place names (.txt) stay there
    tile_ext = (".map.gz",) if is_roam else (".map.lzma", ".map.lzma.17")

    def entry(path):
        return {"name": os.path.basename(path), "size": os.path.getsize(path),
                "rel": os.path.relpath(path, out).replace(os.sep, "/")}

    res = {"out": out, "theme": None, "tiles": [], "last_run": None, "model": "roam1" if is_roam else "bolt2"}
    if is_roam:
        cfg = os.path.join(out, roam.DIR, roam.CFG)
        res["roam"] = {"dir": os.path.join(out, roam.DIR), "cfg": entry(cfg) if os.path.isfile(cfg) else None,
                       "missing": roam.missing(out), "made": bool(roam.tiles_in(out))}
    try:                                    # written by build_tiles.py at the end of each run
        with open(os.path.join(out, "last_run.json"), encoding="utf-8") as f:
            res["last_run"] = json.load(f)
    except (OSError, ValueError):
        pass
    try:
        with open(os.path.join(out, "tiles_info.json"), encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        info = {}
    lr = res["last_run"] or {}
    last_x = {str(k).split("/")[0] for k in lr.get("selected", [])}
    built = set(lr.get("built", []))
    theme = os.path.join(maps, "vtm-elemnt")
    if os.path.isdir(theme):
        icons = os.path.join(theme, "icons")
        res["theme"] = {"dir": theme, "icons_dir": icons, "new": bool(lr.get("theme_new")),
                        "files": [entry(os.path.join(theme, f)) for f in sorted(os.listdir(theme))
                                  if not is_junk(f) and os.path.isfile(os.path.join(theme, f))],
                        "icons": [entry(os.path.join(icons, f)) for f in sorted(os.listdir(icons))
                                  if f.endswith(".svg") and not is_junk(f)] if os.path.isdir(icons) else []}
    tiles = os.path.join(maps, "tiles", "8")
    if os.path.isdir(tiles):
        for x in sorted((d for d in os.listdir(tiles) if d.isdigit()), key=int):
            xdir = os.path.join(tiles, x)
            labels = {}
            files = []
            ndir = os.path.join(names_dir, x)
            for f in sorted(os.listdir(ndir)) if os.path.isdir(ndir) else []:
                m = re.match(r"^(\d+)(?: - |_)(.*)\.txt$", f)
                if m and not is_junk(f):
                    labels[m.group(1)] = m.group(2)
            for f in sorted(os.listdir(xdir)):
                if not is_junk(f) and f.endswith(tile_ext):
                    files.append(entry(os.path.join(xdir, f)))
            items = {}
            for fe in files:
                y = fe["name"].split(".")[0]
                it = items.setdefault(y, {"y": int(y), "label": labels.get(y, ""), "files": [], "new": f"{x}/{y}" in built,
                                          "mtime": 0, "size": 0,
                                          "contour": (info.get(f"{x}/{y}") or {}).get("contour", ""),
                                          "plain": (info.get(f"{x}/{y}") or {}).get("wandrer") is False})
                it["files"].append(fe)
                if fe["name"].endswith((".map.lzma", ".map.gz")):
                    it["size"] = fe["size"]
                    it["mtime"] = int(os.path.getmtime(os.path.join(xdir, fe["name"])))
            res["tiles"].append({"x": int(x), "dir": xdir, "labels": labels, "files": files, "last": x in last_x,
                                 "items": sorted(items.values(), key=lambda i: i["y"])})
    return res


def result_file(out, rel):
    """Absolute path of a built file, only inside wahoondrer_maps / wmc_classic / roam_v1 of the output folder."""
    out = os.path.realpath(os.path.expanduser(out or ""))
    full = os.path.realpath(os.path.join(out, rel))
    allowed = [os.path.join(out, d) + os.sep for d in ("wahoondrer_maps", "wmc_classic", roam.DIR)]
    if not out or not any(full.startswith(a) for a in allowed) or not os.path.isfile(full):
        return None
    return full


def contour_status():
    """Contour lines need pyhgtmap (installed once with build_tiles.py --setup-contour)."""
    return {"pyhgtmap": contour.pyhgtmap_path() is not None}


def regions_state():
    plan = JOB.plan or {}
    needed = set(plan.get("regions", []))
    down = regions.downloaded()
    have = {r["key"] for r in down}
    for r in down:
        r["needed"] = r["key"] in needed
    missing = [{"key": k, "name": regions.pretty_name(k)} for k in sorted(needed - have)]
    return {"downloaded": down, "missing": missing, "plan_done": bool(plan),
            "catalog_ready": bool(regions.catalog()), "download": DOWNLOADER.status(),
            "max_days": settings.osm_max_days(settings.load())}   # 0 = never renewed


class Handler(BaseHTTPRequestHandler):
    server_version = "maprBoy"

    def log_message(self, fmt, *args):   # keep the terminal quiet
        pass

    # ---- helpers
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    # ---- GET
    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        try:
            if url.path == "/api/state":
                return self._send(200, state(settings.load()))
            if url.path == "/api/scan_status":
                return self._send(200, SCANNER.status())
            if url.path == "/api/ls":
                return self._send(200, scan.list_dir(q.get("path", [""])[0]))
            if url.path == "/api/job":
                return self._send(200, JOB.snapshot(int(q.get("since", ["0"])[0])))
            if url.path == "/api/regions":
                return self._send(200, regions_state())
            if url.path == "/api/contour_status":
                return self._send(200, contour_status())
            if url.path == "/api/results":
                s = settings.load()
                model = settings.wahoo_model(s) if s.get("device", "wahoo") == "wahoo" else "bolt2"
                return self._send(200, results(s["folders"]["out"], model))
            if url.path == "/api/file":
                return self._file(q.get("rel", [""])[0])
            if url.path == "/api/poi_catalog":
                return self._send(200, poi_catalog())
            if url.path.startswith("/poi_icons/"):
                return self._poi_icon(url.path.rsplit("/", 1)[-1])
            if url.path == "/api/osm_data":
                return self._send(200, regions.data_dir_info(settings.load()["folders"]["data"]))
            if url.path in ("/api/region_shapes", "/api/region_tiles"):
                keys = [k for k in q.get("keys", [""])[0].split(",") if k]
                return self._send(200, regions.shapes(keys) if url.path == "/api/region_shapes"
                                  else {"tiles": regions.tiles_of(keys)})
            if url.path == "/api/device":           # adb and the ELEMNT plugged in (automatic copy)
                return self._send(200, devcopy.status((settings.load().get("tools") or {}).get("adb") or ""))
            if url.path == "/api/estimate":
                g = lambda k: q.get(k, [""])[0]
                return self._send(200, {"estimate": jobs.per_tile_estimate(
                    jobs.load_runs(), g("device") or "wahoo", g("skip") == "1", g("contour") == "1")})
            if url.path == "/api/regions_available":
                return self._send(200, {"available": regions.available()})
            return self._static(url.path)
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def _file(self, rel):
        full = result_file(settings.load()["folders"]["out"], rel)
        if not full:
            return self._send(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(os.path.getsize(full)))
        self.send_header("Content-Disposition", 'attachment; filename="%s"' % os.path.basename(full).replace('"', ""))
        self.end_headers()
        with open(full, "rb") as f:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                self.wfile.write(chunk)

    def _poi_icon(self, name):
        m = re.match(r"^shape-([a-z]+)-([a-z]+)\.svg$", name)
        try:
            if m:
                data = poi_config.shape_svg(*poi_config.shape_parts(f"shape:{m.group(1)}:{m.group(2)}")).encode("utf-8")
            elif name in poi_config.library_icons(poi_config.Paths().icon_dir):
                data = (poi_config.Paths().icon_dir / name).read_bytes()
            else:
                return self._send(404, {"error": "not found"})
        except (ValueError, OSError):
            return self._send(404, {"error": "not found"})
        self._send(200, data, "image/svg+xml")

    def _static(self, path):
        name = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.realpath(os.path.join(STATIC, name))
        if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "not found"})
        with open(full, "rb") as f:
            self._send(200, f.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

    # ---- POST (the custom header blocks requests coming from other web sites)
    def do_POST(self):
        if self.headers.get("X-Wahoondrer") != "1":
            return self._send(403, {"error": "forbidden"})
        try:
            url = urlparse(self.path)
            if url.path == "/api/upload":
                return self._upload(parse_qs(url.query))
            body = self._body()
            path = url.path
            if path == "/api/settings":
                return self._send(200, {"settings": settings.save(body)})
            if path in ("/api/run", "/api/plan"):
                return self._run(body, plan=(path == "/api/plan"))
            if path == "/api/cancel":
                JOB.cancel()
                return self._send(200, {"ok": True})
            if path == "/api/scan":
                s = settings.load()
                started = SCANNER.start(s["folders"]["wandrer"], s["geocode"])
                return self._send(200, {"started": started})
            if path == "/api/pick_folder":
                try:
                    chosen = native.pick_folder(body.get("prompt", "Choose a folder"), body.get("start", ""))
                    return self._send(200, {"path": chosen})
                except native.NoDialog as exc:
                    return self._send(200, {"unavailable": str(exc)})
            if path == "/api/open_folder":
                native.open_folder(body.get("path", ""))
                return self._send(200, {"ok": True})
            if path == "/api/make_folders":
                paths = settings.make_standard_folders(body.get("base", ""))
                s = settings.load()
                s["folders"].update(paths)
                s["folders"]["data"] = os.path.abspath(os.path.expanduser(body["base"]))
                osm = "skipped"
                try:     # OSM data folder + link for WMC (only when WMC has no data yet)
                    if not os.path.lexists(regions.WMC_DIR):
                        osm = regions.link_data_dir(s["folders"]["data"], False)
                except (OSError, ValueError):
                    pass
                return self._send(200, {"settings": settings.save(s), "osm": osm})
            if path == "/api/regions_download":
                if JOB.running():
                    raise jobs.SettingsError("A job is running: wait for it to finish.")
                DOWNLOADER.start(body.get("keys", []), bool(body.get("force")))
                return self._send(200, {"started": True})
            if path == "/api/regions_delete":
                if JOB.running() or DOWNLOADER.status()["running"]:
                    raise jobs.SettingsError("A job or a download is running: wait for it to finish.")
                keys = body.get("keys") or [body.get("key", "")]
                if not isinstance(keys, list) or not all(isinstance(k, str) for k in keys):
                    raise ValueError("Invalid list of regions.")
                freed, n = 0, 0
                for k in keys:
                    try:
                        freed += regions.delete(k)
                        n += 1
                    except ValueError:
                        if len(keys) == 1:
                            raise                 # a single region that is not there: tell the user
                return self._send(200, {"freed": freed, "deleted": n})
            if path == "/api/osm_link":
                if JOB.running() or DOWNLOADER.status()["running"]:
                    raise jobs.SettingsError("A job or a download is running: wait for it to finish.")
                base = settings.load()["folders"]["data"]
                if not base:
                    raise ValueError("Choose or create the data folder first.")
                try:
                    res = regions.link_data_dir(base, bool(body.get("move")))
                except OSError as exc:
                    raise ValueError(f"Cannot create the link: {exc}")
                return self._send(200, {"result": res, "info": regions.data_dir_info(base)})
            if path == "/api/device_copy":
                s = settings.load()
                if s.get("device", "wahoo") != "wahoo":
                    raise jobs.SettingsError("The automatic copy is for the Wahoo ELEMNT only.")
                JOB.start([jobs.copy_command(s)], "copy")
                return self._send(200, {"started": True})
            if path == "/api/device_reboot":        # the new maps show after a restart of the ELEMNT
                if JOB.running() and JOB.kind == "copy":
                    raise jobs.SettingsError("Wait for the end of the copy.")
                try:
                    serial = devcopy.reboot((settings.load().get("tools") or {}).get("adb") or "")
                except (devcopy.CopyError, OSError, subprocess.SubprocessError) as exc:
                    raise jobs.SettingsError(str(exc))
                return self._send(200, {"rebooting": serial})
            if path == "/api/roam_prepare":         # make the Roam v1 files of the tiles already built
                s = settings.load()
                JOB.start([jobs.roam_command(s)], "roam")
                return self._send(200, {"started": True})
            if path == "/api/regions_cancel":
                DOWNLOADER.cancel()
                return self._send(200, {"ok": True})
            return self._send(404, {"error": "not found"})
        except (jobs.SettingsError, ValueError, FileNotFoundError) as exc:
            return self._send(400, {"error": str(exc)})
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

    def _run(self, body, plan):
        s = settings.save(body)
        jobs.require_environment()          # the build would stop at its first import: say why now
        if SCANNER.status()["running"]:
            raise jobs.SettingsError("The exports are still being read: wait a moment.")
        if DOWNLOADER.status()["running"]:
            raise jobs.SettingsError("A region download is running: wait for it to finish.")
        infos = scan.scan_wandrer(s["folders"]["wandrer"])
        annotate(s, infos)
        chosen = [i["path"] for i in infos if i["active"]]
        sq = pick_squadrats(s, scan.squadrats_files(s["folders"]["squadrats"]))
        cmds = jobs.build_commands(s, chosen, sq["path"] if sq else None, plan=plan)
        if s["folders"]["work"]:
            os.makedirs(os.path.expanduser(s["folders"]["work"]), exist_ok=True)
        os.makedirs(os.path.expanduser(s["folders"]["out"]), exist_ok=True)
        JOB.start(cmds, "plan" if plan else "run", notify=bool(s["build"].get("notify", True)),
                  profile=jobs.run_profile(s))
        return self._send(200, {"started": True, "commands": [c[1] for c in cmds]})

    def _upload(self, q):
        """Drag-and-drop: the page sends each dropped file as the raw request body."""
        s = settings.load()
        kind = q.get("kind", [""])[0]
        if kind not in ("wandrer", "squadrats"):
            raise ValueError("Unknown upload type.")
        folder = os.path.expanduser(s["folders"][kind] or "")
        if not folder:
            raise ValueError("Choose or create the folder first.")
        name = os.path.basename(q.get("name", [""])[0].replace("\\", "/"))
        ok_ext = (".kmz", ".kml") if kind == "wandrer" else (".kml",)
        if not name or name.startswith(".") or not name.lower().endswith(ok_ext):
            raise ValueError(f"Only {' / '.join(ok_ext)} files are accepted (got '{name}').")
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0 or size > MAX_UPLOAD:
            raise ValueError("Empty or too large file.")
        os.makedirs(folder, exist_ok=True)
        # received under a hidden name (never listed), then named and moved: an existing file is never replaced
        ext = os.path.splitext(name)[1].lower()
        part, left = os.path.join(folder, f".upload-{os.getpid()}-{threading.get_ident()}{ext}"), size
        try:
            with open(part, "wb") as f:
                while left > 0:
                    chunk = self.rfile.read(min(1 << 20, left))
                    if not chunk:
                        break
                    f.write(chunk)
                    left -= len(chunk)
            if left:
                raise ValueError("Upload interrupted.")
            same = scan.same_file_in(folder, part, ok_ext)
            if same:
                return self._send(200, {"saved": same, "already": True})
            if kind == "wandrer":
                name = scan.name_dropped_export(part, s["geocode"]) or name
            name = scan.free_name(folder, name)
            os.replace(part, os.path.join(folder, name))
        finally:
            if os.path.exists(part):
                os.remove(part)
        return self._send(200, {"saved": name, "already": False})


def serve(port=8765, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"maprBoy is running at {url}  (Ctrl+C to stop)")
    env = jobs.check_environment()
    if env["message"]:
        print(f"\nWARNING: {env['message']}\n")
    if env["notice"]:
        print(f"\nNOTE: {env['notice']}\n")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        JOB.cancel()
        DOWNLOADER.cancel()
        srv.server_close()
