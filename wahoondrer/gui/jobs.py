"""Turn the settings into command lines, and run them one after the other."""
import importlib.metadata
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time

from .. import contour, poi_config, theme_style
from . import native
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


# What the build step imports (WahooMapsCreator and the libraries it needs, see environment.yml).
# Checked in the Python that runs this page, which is also the one that runs the build.
REQUIRED_MODULES = ("wahoomc", "osgeo", "shapely", "geojson", "requests")
ENV_NAME = "wahoondrer-env"
WMC_VERSION = "4.4.0"       # the WahooMapsCreator version maprBoy was tested with (same pin as requirements.txt and environment.yml)
MIN_PYTHON = (3, 10)        # WMC 4.4.0 declares Python 3.10 or newer


def environment_message(missing):
    if not missing:
        return ""
    return (f"Python module{'s' if len(missing) > 1 else ''} not found: {', '.join(missing)}. "
            f"The maps cannot be built with this Python ({sys.executable}). "
            f"Stop the page (Ctrl+C in the terminal), activate the {ENV_NAME} environment "
            f"(see \"Installation\" in the README), then start the page again.")


def version_tuple(text):
    """'4.10.1' -> (4, 10, 1); stops at the first part that is not a number ('4.5.0rc1' -> (4, 5, 0))."""
    parts = []
    for piece in re.split(r"[.\-+]", str(text)):
        m = re.match(r"\d+", piece)
        if not m:
            break
        parts.append(int(m.group()))
        if m.end() != len(piece):
            break
    return tuple(parts)


def installed_version(name):
    """Version of an installed package, None if it cannot be read."""
    try:
        return importlib.metadata.version(name)
    except Exception:           # PackageNotFoundError, or damaged metadata: not worth stopping for
        return None


def check_environment():
    """Is everything the build needs installed in the Python running this page?
    -> {"python", "version", "wmc_version", "missing": [module names],
        "message": what stops a build ("" if all is well), "notice": a remark that does not stop anything}"""
    missing = []
    for name in REQUIRED_MODULES:
        try:
            found = importlib.util.find_spec(name) is not None
        except (ImportError, ValueError, AttributeError):
            found = False
        if not found:
            missing.append(name)
    wmc = installed_version("wahoomc") if "wahoomc" not in missing else None
    problems, notice = [], ""
    if missing:
        problems.append(environment_message(missing))
    if sys.version_info[:2] < MIN_PYTHON:
        problems.append(f"This Python is {platform.python_version()} ({sys.executable}), maprBoy needs "
                        f"{'.'.join(map(str, MIN_PYTHON))} or newer: use the {ENV_NAME} environment "
                        f"(see \"Installation\" in the README).")
    if wmc and version_tuple(wmc) < version_tuple(WMC_VERSION):
        problems.append(f"WahooMapsCreator {wmc} is installed in this Python ({sys.executable}), but maprBoy needs "
                        f"{WMC_VERSION}: in the {ENV_NAME} environment run  pip install wahoomc=={WMC_VERSION}  "
                        f"(see \"Installation\" in the README).")
    elif wmc and version_tuple(wmc) > version_tuple(WMC_VERSION):
        notice = (f"WahooMapsCreator {wmc} is installed; maprBoy was tested with {WMC_VERSION}. "
                  f"It may work, but this is not guaranteed.")
    return {"python": sys.executable, "version": platform.python_version(), "wmc_version": wmc,
            "missing": missing, "message": " ".join(problems), "notice": notice}


def require_environment():
    """Refuse to start a build that would stop at its first import."""
    message = check_environment()["message"]
    if message:
        raise SettingsError(message)


# Failures a newcomer cannot read in a traceback: (pattern, short text for the notification, longer hint for the log).
HINTS = (
    (re.compile(r"No module named '?([\w.]+)"),
     "Python module '{0}' not found: the page was probably started outside the " + ENV_NAME + " environment.",
     "Hint: the Python module '{0}' is not installed in " + sys.executable + ". The page was probably started outside the "
     + ENV_NAME + " environment: stop it (Ctrl+C), activate that environment (see \"Installation\" in the README), "
     "start the page again."),
    (re.compile(r"cannot import name '\w+' from 'wahoomc"),
     "WahooMapsCreator is too old: maprBoy needs " + WMC_VERSION + ".",
     "Hint: the installed WahooMapsCreator is older than the " + WMC_VERSION + " that maprBoy needs. In the " + ENV_NAME
     + " environment (Python 3.10 or newer) run:  pip install wahoomc==" + WMC_VERSION + "  then start the page again."),
    (re.compile(r"XY coordinate '(\d+/\d+)' consists of no countries"),
     "Tile {0} has no land: no OpenStreetMap region covers it.",
     "Hint: tile {0} is in the open sea, no OpenStreetMap region covers it, so there is nothing to build. "
     "Take it out of the tiles (on the map) or choose a tile that touches land."),
    (re.compile(r"No space left on device|not enough space on the disk", re.I),
     "The disk is full.",
     "Hint: the disk is full. Delete OSM regions (Map data section) or old tiles, then run again: the finished steps are not redone."),
)


def hint_for(lines):
    """(short, long) hint for a failed run, from the end of its log; None if the cause is not a known one."""
    for line in reversed(list(lines)[-60:]):
        for pattern, short, long in HINTS:
            m = pattern.search(line)
            if m:
                return short.format(*m.groups()), long.format(*m.groups())
    return None


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


def look_args(w):
    """['--theme-look', json] when the look of the Wandrer roads is not the default one, else []."""
    try:
        look = theme_style.clean(w.get("look"))
    except ValueError as exc:
        raise SettingsError(f"Look of the Wandrer roads: {exc}")
    if theme_style.is_default(look):
        return []
    return ["--theme-look", json.dumps(look)]


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
    use_wandrer = bool(w["enabled"] and exports)
    use_squadrats = bool(q["enabled"])          # independent of Wandrer
    cmds = []
    if use_squadrats and not squadrats_file:
        use_squadrats = False           # no KML: build without the layer (a note says so) instead of stopping
        cmds.append(("No Squadrats file: the layer is left out",
                     [sys.executable, "-c",
                      "print('! Squadrats: no KML file found in the Squadrats folder, so this layer is left out. '"
                      "'Put your Squadrats export there (Squadrats section) or untick the layer.')"]))
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
        if use_squadrats:
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
        b += look_args(w)
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
        self.notify, self.thread = False, None
        self.plan = getattr(self, "plan", None)   # last "Check the plan" result, kept between jobs

    def running(self):
        return self.status == "running"

    def start(self, commands, kind="run", notify=False):
        """notify: show a system notification when a "run" ends (done or failed; not when cancelled)."""
        with self.lock:
            if self.running():
                raise SettingsError("A job is already running.")
            self.reset()
            self.status, self.started, self.kind, self.notify = "running", time.time(), kind, notify
            self.thread = threading.Thread(target=self._run, args=(commands,), daemon=True)
        self.thread.start()

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
        hint = None
        if not ok and not self.cancelled:
            with self.lock:
                hint = hint_for(self.lines)
            if hint:
                self._log(f"\n{hint[1]}")
        with self.lock:
            self.finished = time.time()
            self.status = "cancelled" if self.cancelled else ("done" if ok else "failed")
        took = format_duration(self.finished - self.started) if self.started else ""
        self._log({"done": f"\nFinished in {took}.", "failed": f"\nTime spent: {took}.", "cancelled": f"\nCancelled after {took}."}[self.status])
        self._notify(took, hint)

    def _notify(self, took, hint):
        """The page may be hidden behind other windows during a long build: tell the user how it ended."""
        if not self.notify or self.kind != "run" or self.status not in ("done", "failed"):
            return
        if self.status == "done":
            with self.lock:
                notes = sum(1 for l in self.lines if l.startswith("! "))
            more = (f"{notes} note{'s' if notes > 1 else ''} to read: open the page."
                    if notes else "Open the page to copy the files to the Bolt.")
            native.notify("maprBoy: maps ready", f"Finished in {took}. {more}")
        else:
            step = re.sub(r"^\d+/\d+ ", "", self.title)
            native.notify("maprBoy: stopped", f"\u201c{step}\u201d failed. " + (hint[0] if hint else "Open the page for details."), ok=False)

    def cancel(self):
        self.cancelled = True
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()

    def snapshot(self, since=0):
        with self.lock:
            return {"status": self.status, "kind": self.kind, "title": self.title, "next": len(self.lines),
                    "lines": self.lines[since:],
                    "seconds": round((self.finished or time.time()) - self.started) if self.started else 0}
