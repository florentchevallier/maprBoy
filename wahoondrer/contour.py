"""Contour lines: finds and runs pyhgtmap, which turns elevation data into OSM contour lines.

WMC's own contour step calls the old `phyghtmap` 2.23, which no longer works with current
Python libraries. Wahoondrer therefore runs `pyhgtmap` (the maintained fork) itself and writes the
result where WMC's merge step expects it. pyhgtmap lives in its own small environment so that
its dependencies cannot disturb GDAL or shapely. Standard library only.
"""

import os
import shutil
import subprocess
import sys

VENV = os.path.join(os.path.expanduser("~"), ".wahoondrer", "contour-venv")
SOURCES = "view1,view3"        # Viewfinder Panoramas: free, no account needed
MIN_BYTES = 1000               # a real contour file is much bigger than its XML header


def _bin_dir():
    return os.path.join(VENV, "Scripts" if os.name == "nt" else "bin")


def pyhgtmap_path():
    """The pyhgtmap program of the Wahoondrer environment, else one found on the PATH, else None."""
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


def build_command(exe, tile, out_prefix, hgt_dir, jobs=1):
    """Same settings as WMC (10 m steps, 100 m / 50 m categories, no 0 m line), without any account."""
    return [exe,
            "-a", f"{tile['left']}:{tile['bottom']}:{tile['right']}:{tile['top']}",
            "-o", out_prefix, "-s", "10", "-c", "100,50",
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
