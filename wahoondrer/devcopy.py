"""Copy the maps of the last run to a Wahoo ELEMNT plugged in by USB, with adb.

Tested on an ELEMNT Bolt v2 (October 2026): the device storage is /sdcard/, laid out like
Output/wahoondrer_maps/ (maps/tiles/8/<x>/..., maps/vtm-elemnt/...). adb must be installed and the device
authorised once (USB unplugged, several short presses on the power button, plug in again: `adb devices`
then shows "device" instead of "unauthorized").

What it does, file by file (never a whole folder):
- the theme (vtm-elemnt.xml and the icons), the tiles of the last run (.map.lzma, .map.lzma.17 and the
  small place-name .txt) - or, for the ELEMNT Roam v1, the files of Output/roam_v1/ (.map.gz, the cfg file);
- a file that is already on the device with the same content (size, then md5) is skipped;
- a file that would be replaced is first saved in Output/backup/<date>/ (same layout as the device);
- each file is pushed to a temporary name, then renamed: a stopped copy never leaves half a tile;
- at the end, as WahooMapsCreator advises: empty maps/temp (a cache), then the PURGE and RELOAD_MAP
  broadcasts, so the device reloads its maps without a restart.

Usage: python3 -m wahoondrer.devcopy --out OUTPUT_FOLDER [--model bolt2|roam1] [--adb PATH]
"""
import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import time

from . import roam
from .progress import MARK, gui

REMOTE = "/sdcard"
JUNK = (".DS_Store", "Thumbs.db")
BROADCASTS = ("com.wahoofitness.bolt.service.BMapManager.PURGE",
              "com.wahoofitness.bolt.service.BMapManager.RELOAD_MAP")
COMMON_ADB = ("/opt/homebrew/bin/adb", "/usr/local/bin/adb",
              os.path.expanduser("~/Library/Android/sdk/platform-tools/adb"),
              os.path.expanduser("~/Android/Sdk/platform-tools/adb"))


class CopyError(Exception):
    pass


# ------------------------------------------------------------------ adb
def find_adb(path=""):
    """The adb program: the given path, else the PATH, else the usual install places (a page started from
    a desktop icon may not see the terminal's PATH)."""
    if path:
        return path if os.path.isfile(path) else None
    found = shutil.which("adb")
    if found:
        return found
    return next((p for p in COMMON_ADB if os.path.isfile(p)), None)


def devices(adb):
    """[{"serial", "state"}] from `adb devices` (state: device, unauthorized, offline...)."""
    r = subprocess.run([adb, "devices"], capture_output=True, text=True, timeout=10)
    out = []
    for line in r.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            out.append({"serial": parts[0], "state": parts[1]})
    return out


def status(adb_path=""):
    """What the page shows: is adb there, is a device plugged in and authorised."""
    adb = find_adb(adb_path)
    if not adb:
        return {"adb": None, "devices": [], "ready": False}
    try:
        devs = devices(adb)
    except (OSError, subprocess.SubprocessError) as e:
        return {"adb": adb, "devices": [], "ready": False, "error": str(e)}
    return {"adb": adb, "devices": devs, "ready": any(d["state"] == "device" for d in devs)}


class Adb:
    def __init__(self, adb, serial):
        self.base = [adb, "-s", serial]

    def run(self, *args, check=True, timeout=600):
        r = subprocess.run(self.base + list(args), capture_output=True, text=True, timeout=timeout)
        if check and r.returncode != 0:
            raise CopyError(f"adb {' '.join(args[:2])} failed: {(r.stderr or r.stdout).strip()[-400:]}")
        return r.stdout

    def shell(self, cmd, check=True):
        return self.run("shell", cmd, check=check)

    def sizes(self, remote_dir):
        """{name: size} of the files of a device folder ({} if it does not exist)."""
        out = self.shell(f"ls -l {shlex.quote(remote_dir)} 2>/dev/null", check=False)
        return parse_ls(out)


def parse_ls(text):
    """{name: size} from the `ls -l` of the device (toybox): perms links user group size date time name."""
    found = {}
    for line in text.splitlines():
        parts = line.split(None, 7)
        if len(parts) == 8 and parts[0].startswith("-") and parts[4].isdigit():
            found[parts[7]] = int(parts[4])
    return found


# ------------------------------------------------------------------ what to copy
def plan(out, model="bolt2"):
    """[(local path, remote path)] for the last run: theme (xml + icons), then the tiles of the last run.
    Roam v1: the files of Output/roam_v1/ (its cfg file first)."""
    out = os.path.abspath(os.path.expanduser(out))
    try:
        with open(os.path.join(out, "last_run.json"), encoding="utf-8") as f:
            last = json.load(f)
    except (OSError, ValueError):
        raise CopyError("No maps made yet (no last_run.json in the output folder): make the maps first.")
    tiles = last.get("built") or last.get("selected") or []
    root = os.path.join(out, roam.DIR) if model == "roam1" else os.path.join(out, "wahoondrer_maps")
    names = os.path.join(out, "wahoondrer_maps", "maps", "tiles", "8")
    files = []

    def add(local, rel):
        if os.path.isfile(local):
            files.append((local, REMOTE + "/" + rel))

    if model == "roam1":
        add(os.path.join(root, roam.CFG), roam.CFG)
    theme = os.path.join(root, "maps", "vtm-elemnt")
    add(os.path.join(theme, "vtm-elemnt.xml"), "maps/vtm-elemnt/vtm-elemnt.xml")
    icons = os.path.join(theme, "icons")
    if os.path.isdir(icons):
        for f in sorted(os.listdir(icons)):
            if f.endswith(".svg") and f not in JUNK and not f.startswith("._"):
                add(os.path.join(icons, f), "maps/vtm-elemnt/icons/" + f)
    for t in tiles:
        x, y = str(t).split("/")
        tdir = os.path.join(root, "maps", "tiles", "8", x)
        exts = (".map.gz",) if model == "roam1" else (".map.lzma", ".map.lzma.17")
        for ext in exts:
            add(os.path.join(tdir, y + ext), f"maps/tiles/8/{x}/{y}{ext}")
        if os.path.isdir(os.path.join(names, x)):
            for f in sorted(os.listdir(os.path.join(names, x))):
                if f.startswith(y + "_") and f.endswith(".txt"):           # the place name, for the user
                    add(os.path.join(names, x, f), f"maps/tiles/8/{x}/{f}")
    if not any(r.endswith((".map.lzma", ".map.gz")) for _, r in files):
        raise CopyError("No tile of the last run found" + (" in roam_v1: prepare the Roam v1 files first."
                                                           if model == "roam1" else "."))
    return files


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ------------------------------------------------------------------ copy
def copy(out, model="bolt2", adb_path="", log=print):
    out = os.path.abspath(os.path.expanduser(out))
    adb = find_adb(adb_path)
    if not adb:
        raise CopyError("adb not found: install it (macOS: brew install android-platform-tools).")
    ready = [d for d in devices(adb) if d["state"] == "device"]
    if not ready:
        raise CopyError("No authorised device: plug the ELEMNT in, authorise it, then check with `adb devices`.")
    dev = Adb(adb, ready[0]["serial"])
    files = plan(out, model)
    backup = os.path.join(out, "backup", time.strftime("%Y-%m-%d_%H%M%S"))
    on_device = {}
    total = sum(os.path.getsize(p) for p, _ in files)
    done_bytes, copied, skipped, saved = 0, 0, 0, 0
    log(f"Device {ready[0]['serial']}: {len(files)} file(s) to check, {total / 1e6:.0f} MB")
    for i, (local, remote) in enumerate(files, 1):
        folder, name = remote.rsplit("/", 1)
        if folder not in on_device:
            on_device[folder] = dev.sizes(folder)
        size = os.path.getsize(local)
        if gui():
            print(f"{MARK}S|{i}|{len(files)}|{name}", flush=True)
        same = on_device[folder].get(name) == size
        if same and size:
            remote_md5 = dev.shell(f"md5sum {shlex.quote(remote)}", check=False).split()[:1]
            same = remote_md5 == [md5(local)]
        if same:
            skipped += 1
            done_bytes += size
            continue
        if name in on_device[folder]:                        # keep the old file before replacing it
            dest = os.path.join(backup, *remote[len(REMOTE) + 1:].split("/"))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            dev.run("pull", remote, dest)
            saved += 1
        dev.shell(f"mkdir -p {shlex.quote(folder)}")
        dev.run("push", local, remote + ".part")
        dev.shell(f"mv -f {shlex.quote(remote + '.part')} {shlex.quote(remote)}")
        copied += 1
        done_bytes += size
        log(f"  copied {remote[len(REMOTE) + 1:]} ({size / 1e6:.1f} MB) - {done_bytes * 100 // max(total, 1)} %")
    log(f"{copied} file(s) copied, {skipped} already up to date" +
        (f", {saved} old file(s) saved in {backup}" if saved else ""))
    if copied:
        log("Reloading the maps on the device: maps/temp emptied, then PURGE and RELOAD_MAP")
        dev.shell(f"rm -rf {REMOTE}/maps/temp/*", check=False)
        ok = all(dev.shell(f"am broadcast -a {b}", check=False).strip() for b in BROADCASTS)
        if not ok:
            log("! The device did not answer the reload commands: restart it to see the new maps.")
    return {"copied": copied, "skipped": skipped, "saved": saved, "backup": backup if saved else ""}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="the output folder (the one holding wahoondrer_maps)")
    ap.add_argument("--model", default="bolt2", help="bolt2 (default) or roam1 (the .map.gz files of roam_v1)")
    ap.add_argument("--adb", default="", help="path of adb (default: found in the PATH)")
    args = ap.parse_args(argv)
    try:
        copy(args.out, args.model, args.adb)
    except (CopyError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
