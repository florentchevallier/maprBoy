"""Copy the Karoo maps of the output folder to a Karoo plugged in by USB (adb), or take them off again.

Seen on a borrowed Karoo 2 (October 2026): adb works once USB debugging is on (developer options); the maps are in
/sdcard/offline/maps/, the theme is /sdcard/offline_vNN.xml (v15 there; v7 to v14 were also on the device), the
icons we reference as file:/maprboy-icons/... are read from /sdcard/maprboy-icons/.

copy:   every maprboy-*.map of OUT/karoo/ (one per place: several stay on the device) is pushed unless the same file
        is already there; the icons too; the theme with the highest number is read from the device, our block
        (OUT/karoo/maprboy-theme-block.xml, from the last build) put into it, and pushed back. The first time a
        device's theme is read without our block, it is kept in OUT/karoo/backup/<serial>/.
remove: the theme without our block is pushed back, our maps and icons are deleted.
The new maps show after a restart (button on the page).

Usage: python3 -m wahoondrer.karoo.device --out OUTPUT_FOLDER (copy | remove) [--adb PATH]
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile

from .. import devcopy
from ..progress import MARK, gui
from . import theme

KAROO_DIR = "karoo"              # Output/karoo/
BLOCK_FILE = "maprboy-theme-block.xml"
REMOTE = "/sdcard"
REMOTE_MAPS = REMOTE + "/offline/maps"
REMOTE_ICONS = f"{REMOTE}/{theme.ICON_DIR}"
_THEME_NAME = re.compile(r"^offline_v(\d+)\.xml$")
_MAP_NAME = re.compile(r"^maprboy-[A-Za-z0-9._-]+\.map$")


class KarooError(Exception):
    pass


def latest_theme(names):
    """The theme the Karoo uses among the file names of /sdcard: offline_vNN.xml with the highest NN, or None."""
    found = [(int(m.group(1)), n) for n in names for m in [_THEME_NAME.match(n)] if m]
    return max(found)[1] if found else None


def local_files(out):
    """(maps, icons, block path) of OUT/karoo/."""
    root = os.path.join(os.path.abspath(os.path.expanduser(out)), KAROO_DIR)
    maps = sorted(os.path.join(root, n) for n in (os.listdir(root) if os.path.isdir(root) else []) if _MAP_NAME.match(n))
    icon_dir = os.path.join(root, theme.ICON_DIR)
    icons = sorted(os.path.join(icon_dir, n) for n in (os.listdir(icon_dir) if os.path.isdir(icon_dir) else [])
                   if n.endswith(".svg"))
    return maps, icons, os.path.join(root, BLOCK_FILE)


def _device(adb_path):
    adb = devcopy.find_adb(adb_path)
    if not adb:
        raise KarooError("adb not found: install it (macOS: brew install android-platform-tools).")
    ready = [d for d in devcopy.devices(adb) if d["state"] == "device"]
    if not ready:
        raise KarooError("No authorised device: turn USB debugging on in the Karoo's developer options, plug it in "
                         "and accept the prompt on its screen, then check with `adb devices`.")
    return devcopy.Adb(adb, ready[0]["serial"]), ready[0]["serial"]


def _theme_of(dev):
    """(remote path, text) of the theme the Karoo uses."""
    names = [line.strip() for line in dev.shell(f"ls {REMOTE}", check=False).splitlines()]
    name = latest_theme(names)
    if not name:
        raise KarooError("No offline_vNN.xml theme on the device: is it a Karoo, and has it downloaded maps?")
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, name)
        dev.run("pull", f"{REMOTE}/{name}", local)
        with open(local, encoding="utf-8") as f:
            return f"{REMOTE}/{name}", f.read()


def _push_text(dev, text, remote):
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, os.path.basename(remote))
        with open(local, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        dev.run("push", local, remote + ".part")
    dev.shell(f"mv -f {shlex.quote(remote + '.part')} {shlex.quote(remote)}")


def _push_if_changed(dev, local, folder, on_device):
    name = os.path.basename(local)
    remote = f"{folder}/{name}"
    size = os.path.getsize(local)
    if on_device.get(name) == size:
        remote_md5 = dev.shell(f"md5sum {shlex.quote(remote)}", check=False).split()[:1]
        if remote_md5 == [devcopy.md5(local)]:
            return False
    dev.shell(f"mkdir -p {shlex.quote(folder)}")
    dev.run("push", local, remote + ".part")
    dev.shell(f"mv -f {shlex.quote(remote + '.part')} {shlex.quote(remote)}")
    return True


def copy(out, adb_path="", log=print):
    maps, icons, block_path = local_files(out)
    if not maps:
        raise KarooError("No Karoo map in the output folder (karoo/maprboy-*.map): make the maps first.")
    if not os.path.isfile(block_path):
        raise KarooError(f"{BLOCK_FILE} is missing in the output folder: make the maps again.")
    with open(block_path, encoding="utf-8") as f:
        our_block = f.read()
    dev, serial = _device(adb_path)
    remote_theme, current = _theme_of(dev)
    original = theme.without_block(current)
    backup = os.path.join(os.path.dirname(block_path), "backup", serial)
    kept = os.path.join(backup, os.path.basename(remote_theme))
    if current == original and not os.path.isfile(kept):        # the Karoo's own theme, never saved yet
        os.makedirs(backup, exist_ok=True)
        with open(kept, "w", encoding="utf-8", newline="\n") as f:
            f.write(current)
        log(f"The Karoo's theme is saved in {kept}")
    files = [(p, REMOTE_MAPS) for p in maps] + [(p, REMOTE_ICONS) for p in icons]
    on_device = {REMOTE_MAPS: dev.sizes(REMOTE_MAPS), REMOTE_ICONS: dev.sizes(REMOTE_ICONS)}
    copied = 0
    for i, (local, folder) in enumerate(files, 1):
        if gui():
            print(f"{MARK}S|{i}|{len(files)}|{os.path.basename(local)}", flush=True)
        if _push_if_changed(dev, local, folder, on_device[folder]):
            copied += 1
            log(f"  copied {os.path.basename(local)} ({os.path.getsize(local) / 1e6:.1f} MB)")
    _push_text(dev, theme.with_block(current, our_block), remote_theme)
    log(f"Device {serial}: {copied} file(s) copied, {len(files) - copied} already there; "
        f"our rules added to {remote_theme}. Restart the Karoo to see the maps.")
    return {"copied": copied, "theme": remote_theme}


def remove(out, adb_path="", log=print):
    dev, serial = _device(adb_path)
    remote_theme, current = _theme_of(dev)
    _push_text(dev, theme.without_block(current), remote_theme)
    gone = [n for n in devcopy.parse_ls(dev.shell(f"ls -l {REMOTE_MAPS}", check=False)) if _MAP_NAME.match(n)]
    for n in gone:
        dev.shell(f"rm -f {shlex.quote(REMOTE_MAPS + '/' + n)}")
    dev.shell(f"rm -rf {shlex.quote(REMOTE_ICONS)}", check=False)
    log(f"Device {serial}: our rules taken out of {remote_theme}, {len(gone)} map(s) and the icons deleted. "
        "Restart the Karoo.")
    return {"removed": gone, "theme": remote_theme}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("copy", "remove"))
    ap.add_argument("--out", required=True, help="the output folder (the one holding karoo/)")
    ap.add_argument("--adb", default="", help="path of adb (default: found in the PATH)")
    args = ap.parse_args(argv)
    try:
        (copy if args.action == "copy" else remove)(args.out, args.adb)
    except (KarooError, theme.ThemeError, devcopy.CopyError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
