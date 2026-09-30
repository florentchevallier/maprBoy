"""The local web server. It only listens on 127.0.0.1 and runs the project's own scripts."""
import json
import mimetypes
import os
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import jobs, native, regions, scan, settings

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
JOB = jobs.Job()
SCANNER = scan.Scanner()
DOWNLOADER = regions.Downloader()
MAX_UPLOAD = 1 << 30      # 1 GB


def effective_activity(s, infos):
    """bike / foot / combined to use. Simple mode: always one type. Pro mode: also
    'any' (all types mixed) and 'latest' (type of the newest export)."""
    act = s["activity"]
    if s["mode"] != "pro" and act not in ("bike", "foot", "combined"):
        act = "bike"
    if act == "latest":
        for i in infos:                      # infos are sorted newest first
            if i.get("activity"):
                return i["activity"]
        return ""
    return "" if act == "any" else act


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
            "scan": SCANNER.status(), "job": JOB.snapshot(0)}


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
            "max_days": 14}


class Handler(BaseHTTPRequestHandler):
    server_version = "Wahoondrer"

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
            if url.path == "/api/osm_data":
                return self._send(200, regions.data_dir_info(settings.load()["folders"]["data"]))
            if url.path == "/api/regions_available":
                return self._send(200, {"available": regions.available()})
            return self._static(url.path)
        except Exception as exc:
            return self._send(500, {"error": str(exc)})

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
                freed = regions.delete(body.get("key", ""))
                return self._send(200, {"freed": freed})
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
        JOB.start(cmds, "plan" if plan else "run")
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
        target, part, left = os.path.join(folder, name), os.path.join(folder, name + ".part"), size
        with open(part, "wb") as f:
            while left > 0:
                chunk = self.rfile.read(min(1 << 20, left))
                if not chunk:
                    break
                f.write(chunk)
                left -= len(chunk)
        if left:
            os.remove(part)
            raise ValueError("Upload interrupted.")
        os.replace(part, target)
        return self._send(200, {"saved": name})


def serve(port=8765, open_browser=True):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Wahoondrer is running at {url}  (Ctrl+C to stop)")
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
