"""Turn the settings into command lines, and run them one after the other."""
import json
import os
import subprocess
import sys
import threading
import time

from .. import contour, poi_config
from . import settings as settings_mod

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TILE_OK = set("0123456789/")


def format_duration(seconds):
    """1 h 05 min 09 s / 5 min 09 s / 42 s"""
    s = int(round(seconds))
    h, rest = divmod(s, 3600)
    m, sec = divmod(rest, 60)
    if h:
        return f"{h} h {m:02d} min {sec:02d} s"
    return f"{m} min {sec:02d} s" if m else f"{sec} s"


class SettingsError(ValueError):
    """A problem the user can fix (shown in the page)."""


def parse_tiles(tiles):
    out = []
    for t in tiles:
        t = t.strip()
        if not t:
            continue
        parts = t.split("/")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or not set(t) <= TILE_OK:
            raise SettingsError(f"Invalid tile '{t}' (expected x/y, e.g. 136/88)")
        x, y = int(parts[0]), int(parts[1])
        if not (0 <= x < 256 and 0 <= y < 256):
            raise SettingsError(f"Tile {t} is outside the z8 grid (0-255)")
        out.append(f"{x}/{y}")
    return sorted(set(out), key=lambda s: tuple(map(int, s.split("/"))))


def _inside(path, parent):
    path, parent = os.path.realpath(path), os.path.realpath(parent)
    return path == parent or path.startswith(parent + os.sep)


def poi_args(s):
    """['--poi-rules', file] when the user changed POIs (the effective list is written next to the settings),
    else [] (the defaults of config/poi-rules.json are used)."""
    user = s.get("pois") or {}
    if not poi_config.has_user_choices(user):
        return []
    try:
        pois = [p for p in poi_config.effective_pois(poi_config.read_rules(), user) if p.get("enabled", True)]
        poi_config.validate(pois, poi_config.Paths().icon_dir)
    except (ValueError, TypeError, AttributeError) as exc:
        raise SettingsError(f"POI settings: {exc}")
    path = os.path.join(os.path.dirname(settings_mod.SETTINGS_PATH), "poi-rules.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"pois": pois}, f, indent=2)
    return ["--poi-rules", path]


def _wandrer_covers(exports, tiles):
    """True if one export may touch one of the tiles (rough bounding box; unknown = yes)."""
    from . import scan
    wanted = {tuple(int(v) for v in t.split("/")) for t in tiles}
    for path in exports:
        try:
            info = scan.cached_info(path)
        except OSError:
            return True
        if not info or info.get("error") or "tiles" not in info:
            return True
        if wanted & {tuple(t) for t in info["tiles"]}:
            return True
    return False


def build_commands(s, exports, squadrats_file, plan=False):
    """Return [(title, argv), ...].

    s: settings dict; exports: the selected Wandrer export paths;
    squadrats_file: path of the Squadrats KML to use (or None).
    """
    py = sys.executable
    tiles = parse_tiles(s["tiles"])
    if not tiles:
        raise SettingsError("Choose at least one tile.")
    out = os.path.expanduser(s["folders"]["out"] or "")
    if not out:
        raise SettingsError("Choose an output folder.")
    if _inside(out, REPO):
        raise SettingsError("The output folder must be outside the repository.")
    for name in ("wandrer", "squadrats"):
        folder = os.path.expanduser(s["folders"][name] or "")
        if folder and os.path.realpath(out) == os.path.realpath(folder):
            raise SettingsError("The output folder must not be an export folder "
                                "(its wmc_classic and wahoondrer_maps sub-folders are deleted at each run).")

    w, q = s["wandrer"], s["squadrats"]
    if q["enabled"] and not squadrats_file:
        raise SettingsError("No Squadrats KML file found in the Squadrats folder.")
    use_wandrer = bool(w["enabled"] and exports)
    use_squadrats = bool(q["enabled"])          # independent of Wandrer
    cmds = []
    if use_wandrer and not plan and not _wandrer_covers(exports, tiles):
        use_wandrer = False             # nothing to convert: no export touches the chosen tiles
        if not use_squadrats:
            cmds.append(("No Wandrer data on these tiles: conversion skipped",
                         [sys.executable, "-c",
                          "print('None of the selected Wandrer exports covers the chosen tiles: '"
                          "'the exports are not unzipped and only plain (classic) tiles are built.')"]))
        else:
            cmds.append(("No Wandrer data on these tiles: the exports are not unzipped",
                         [sys.executable, "-c", "print('Only the Squadrats layer is added.')"]))
    convert = (use_wandrer or use_squadrats) and not plan
    osm = os.path.join(os.path.expanduser(s["folders"]["work"] or out), "wandrer.osm")
    if convert:
        a = [py, os.path.join(REPO, "scripts", "kmz_to_osm.py"), "-o", osm,
             *(exports if use_wandrer else []),
             "--radius-m", str(w["radius_m"]), "--simplify-m", str(w["simplify_m"])]
        if use_squadrats:
            a += ["--tiles", ",".join(tiles)]       # Squadrats do not need Wandrer: draw them on every chosen tile
        if w["with_traveled"]:
            a.append("--with-traveled")
        if w["unpaved_always"]:
            a.append("--unpaved-always")
        if w["paved_always"]:
            a.append("--paved-always")
        if not w["boundaries"]:
            a.append("--no-boundaries")
        if q["enabled"]:
            levels = [l for l in q["levels"] if l in ("big", "small")]
            if not levels:
                raise SettingsError("Squadrats: choose at least one level.")
            buffer = int(q["buffer"])
            if buffer < 0:
                raise SettingsError("Squadrats: the band width must be 0 or more.")
            a += ["--squadrats", squadrats_file, "--squadrats-levels", ",".join(levels),
                  "--squadrats-buffer", str(buffer),
                  "--squadrats-small-scope", q["small_scope"]]
        cmds.append(("Convert the " + " / ".join(n for n, on in (("Wandrer", use_wandrer), ("Squadrats", use_squadrats)) if on) + " exports", a))

    b = [py, os.path.join(REPO, "scripts", "build_tiles.py"), "--xy", ",".join(tiles),
         "--out", out]
    if convert:
        b += ["--wandrer", osm]
    if s["build"]["skip_classic"]:
        b.append("--skip-classic")
    if s["build"].get("contour"):
        spacing = s["build"].get("contour_spacing") or contour.DEFAULT_PRESET
        try:
            contour.preset(spacing)
        except ValueError as exc:
            raise SettingsError(str(exc))
        b += ["--contour", "--contour-spacing", spacing]
        if s["build"].get("contour_per_tile"):           # per-tile choices (menus on the map, Pro)
            chosen = s["build"].get("contour_tiles") or {}
            for k in tiles:
                v = chosen.get(k)
                if v and v != spacing:
                    try:
                        contour.choice(v)
                    except ValueError as exc:
                        raise SettingsError(f"Tile {k}: {exc}")
                    b += ["--contour-tile", f"{k}={v}"]
    if not plan:
        b += poi_args(s)
    if plan:
        b.append("--plan")
    cmds.append(("Plan: tiles and OSM regions" if plan else "Build the map tiles", b))
    return cmds


class Job:
    """Runs commands sequentially in a thread; the page polls for new log lines."""

    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        self.lines, self.status, self.proc, self.cancelled = [], "idle", None, False
        self.title, self.started, self.finished, self.kind = "", 0.0, 0.0, ""
        self.plan = getattr(self, "plan", None)   # last "Check the plan" result, kept between jobs

    def running(self):
        return self.status == "running"

    def start(self, commands, kind="run"):
        with self.lock:
            if self.running():
                raise SettingsError("A job is already running.")
            self.reset()
            self.status, self.started, self.kind = "running", time.time(), kind
        threading.Thread(target=self._run, args=(commands,), daemon=True).start()

    def _log(self, text):
        if text.startswith("PLAN-JSON "):        # machine-readable line from build_tiles.py --plan
            try:
                plan = json.loads(text[len("PLAN-JSON "):])
                with self.lock:
                    self.plan = plan
            except ValueError:
                pass
            return
        with self.lock:
            self.lines.append(text)

    def _run(self, commands):
        env = dict(os.environ, PYTHONUNBUFFERED="1", WAHOONDRER_GUI="1")
        ok = True
        for i, (title, argv) in enumerate(commands, 1):
            with self.lock:
                self.title = f"{i}/{len(commands)} {title}"
            self._log(f"\n=== {i}/{len(commands)} {title} ===")
            try:
                self.proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                             stderr=subprocess.STDOUT, text=True,
                                             bufsize=1, env=env, cwd=REPO)
                for line in self.proc.stdout:
                    self._log(line.rstrip("\n"))
                code = self.proc.wait()
            except OSError as exc:
                self._log(f"ERROR: cannot start the command: {exc}")
                code = -1
            if self.cancelled:
                break
            if code != 0:
                self._log(f"\nStopped: this step failed (exit code {code}).")
                ok = False
                break
        with self.lock:
            self.finished = time.time()
            self.status = "cancelled" if self.cancelled else ("done" if ok else "failed")
        took = format_duration(self.finished - self.started) if self.started else ""
        self._log({"done": f"\nFinished in {took}.", "failed": f"\nTime spent: {took}.", "cancelled": f"\nCancelled after {took}."}[self.status])

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def snapshot(self, since=0):
        with self.lock:
            return {"status": self.status, "kind": self.kind, "title": self.title, "next": len(self.lines),
                    "lines": self.lines[since:],
                    "seconds": round((self.finished or time.time()) - self.started) if self.started else 0}
