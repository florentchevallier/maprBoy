"""Contour lines: finds and runs pyhgtmap, which turns elevation data into OSM contour lines.

WMC's own contour step calls the old `phyghtmap` 2.23, which no longer works with current
Python libraries. maprBoy therefore runs `pyhgtmap` (the maintained fork) itself and writes the
result where WMC's merge step expects it. pyhgtmap lives in its own small environment so that
its dependencies cannot disturb GDAL or shapely. Standard library only.
"""

import glob
import os
import shutil
import subprocess
import sys

from . import home

VENV = home.path("contour-venv")
SOURCES = "view1,view3"        # Viewfinder Panoramas: free, no account needed
MIN_BYTES = 1000               # a real contour file is much bigger than its XML header

# Spacing presets "minor-medium-major" (metres). Medium and major lines are multiples of the minor step,
# so they always fall on an existing line. The theme draws major lines from zoom 12, medium from 13, minor from 14.
PRESETS = {"10-50-100": (10, 100, 50), "25-75-150": (25, 150, 75), "30-90-180": (30, 180, 90)}
DEFAULT_PRESET = "25-75-150"
LEGACY_PRESET = "10-50-100"    # files made before the spacing was a setting
MARKER = "contour_spacing.txt"  # in each tile folder: the preset its elevation file was made with


def preset(name):
    """(step, major, medium) of a preset name; ValueError when unknown."""
    if name not in PRESETS:
        raise ValueError(f"unknown contour spacing '{name}' (choose one of: {', '.join(PRESETS)})")
    return PRESETS[name]


def choice(value):
    """A per-tile choice: 'none' or a preset name. Returns it, or raises ValueError."""
    if value == "none":
        return value
    preset(value)
    return value


def park(tile_dir):
    """Tile without contour lines: hide its elevation file from WMC (renamed *.osm.off), keep it for later."""
    for f in glob.glob(os.path.join(tile_dir, "elevation*.osm")):
        os.replace(f, f + ".off")


def unpark(tile_dir):
    """Give a hidden elevation file back to WMC (a file that is already there is left alone)."""
    for f in glob.glob(os.path.join(tile_dir, "elevation*.osm.off")):
        os.replace(f, f[:-4])


def read_marker(tile_dir):
    try:
        with open(os.path.join(tile_dir, MARKER), encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return LEGACY_PRESET


def write_marker(tile_dir, name):
    with open(os.path.join(tile_dir, MARKER), "w", encoding="utf-8") as f:
        f.write(name + "\n")


def _bin_dir():
    return os.path.join(VENV, "Scripts" if os.name == "nt" else "bin")


def pyhgtmap_path():
    """The pyhgtmap program of the maprBoy environment, else one found on the PATH, else None."""
    for name in ("pyhgtmap.exe", "pyhgtmap"):
        p = os.path.join(_bin_dir(), name)
        if os.path.isfile(p):
            return p
    return shutil.which("pyhgtmap")


def setup(say=print):
    """Create the separate environment and install pyhgtmap in it. Returns the program path."""
    if os.path.isfile(os.path.join(_bin_dir(), "pyhgtmap")) or os.path.isfile(os.path.join(_bin_dir(), "pyhgtmap.exe")):
        say(f"pyhgtmap is already installed ({VENV}).")
        return pyhgtmap_path()
    say(f"Creating a separate environment for pyhgtmap: {VENV}")
    os.makedirs(os.path.dirname(VENV), exist_ok=True)
    subprocess.run([sys.executable, "-m", "venv", VENV], check=True)
    py = os.path.join(_bin_dir(), "python.exe" if os.name == "nt" else "python")
    say("Installing pyhgtmap (a few minutes, needs an internet connection)...")
    subprocess.run([py, "-m", "pip", "install", "--quiet", "pyhgtmap"], check=True)
    exe = pyhgtmap_path()
    if not exe:
        raise RuntimeError("pyhgtmap was installed but its program was not found.")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True)
    say("Installed: " + (out.stdout or out.stderr).strip())
    return exe


def build_command(exe, tile, out_prefix, hgt_dir, jobs=1, spacing=DEFAULT_PRESET):
    """WMC-like settings (no 0 m line), the line spacing of a preset, without any account."""
    step, major, medium = preset(spacing)
    # "--area=..." as ONE argument: west of Greenwich the area starts with a minus sign, and
    # "-a -5.6:47.5:..." would be read as another option ("expected one argument").
    return [exe,
            f"--area={tile['left']}:{tile['bottom']}:{tile['right']}:{tile['top']}",
            "-o", out_prefix, "-s", str(step), "-c", f"{major},{medium}",
            f"--sources={SOURCES}", f"--jobs={jobs}",
            "--start-node-id=20000000000", "--start-way-id=2000000000",
            "--max-nodes-per-tile=0", "--write-timestamp", "--no-zero-contour",
            f"--hgtdir={hgt_dir}"]


def file_ok(path):
    """True when the file is a finished contour file (a killed run leaves a truncated one)."""
    try:
        size = os.path.getsize(path)
        if size < MIN_BYTES:
            return False
        with open(path, "rb") as f:
            f.seek(max(0, size - 64))
            return f.read().strip().endswith(b"</osm>")
    except OSError:
        return False
