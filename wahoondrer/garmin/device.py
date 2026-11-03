"""Copy Garmin maps (.img) to a Garmin plugged in by USB.

Two ways in, both seen in November 2026:
- as a disk (Edge 830: the volume GARMIN in the Finder): the maps are copied into <volume>/Garmin/;
- by MTP (Forerunner 955: no disk, an MTP device): with the libmtp command line tools (brew install libmtp).
  mtp-sendfile accepted the number of the GARMIN folder as parent, not its name ("Parent folder could not be
  found" with /GARMIN/...). Sending a name already there listed a second entry of that name; deleting one of the
  two removed both: an older map of the same name is therefore deleted before the new one is sent.

The maps of the last run are recorded in Output/garmin/last_run.json ("mark", the last step of a Garmin build).

Usage: python3 -m wahoondrer.garmin.device copy --target disk:/Volumes/GARMIN | mtp  FILE.img [FILE.img ...]
       python3 -m wahoondrer.garmin.device mark --out Output/garmin NAME.img [NAME.img ...]
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

from ..progress import MARK, gui

LAST_RUN = "last_run.json"
VOLUMES = "/Volumes"
MTP_TOOLS = ("mtp-detect", "mtp-folders", "mtp-files", "mtp-sendfile", "mtp-delfile")


class DeviceError(Exception):
    pass


# ------------------------------------------------------------------ finding the device
def _garmin_dir(volume):
    """The Garmin folder of a mounted volume (any case), or None."""
    try:
        names = os.listdir(volume)
    except OSError:
        return None
    return next((os.path.join(volume, n) for n in names if n.lower() == "garmin"
                 and os.path.isdir(os.path.join(volume, n))), None)


def model_of(garmin_dir):
    """The model written in Garmin/GarminDevice.xml (e.g. "Edge 830"), or ""."""
    try:
        with open(os.path.join(garmin_dir, "GarminDevice.xml"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r"<Model>.*?<Description>\s*([^<]+?)\s*</Description>", text, re.S)
    return m.group(1) if m else ""


def disks(volumes=VOLUMES):
    """[{"kind": "disk", "name", "path"}]: the mounted volumes holding a Garmin folder with GarminDevice.xml."""
    out = []
    try:
        names = sorted(os.listdir(volumes))
    except OSError:
        return out
    for n in names:
        g = _garmin_dir(os.path.join(volumes, n))
        if g and os.path.isfile(os.path.join(g, "GarminDevice.xml")):
            out.append({"kind": "disk", "name": model_of(g) or n, "path": os.path.join(volumes, n)})
    return out


def mtp_ready():
    return all(shutil.which(t) for t in MTP_TOOLS)


def parse_detect(text):
    """Model names from the output of mtp-detect ("Device 0 (VID=091e and PID=4fb8) is a Garmin Forerunner 955 Solar.")."""
    return [m.group(1) for m in re.finditer(r"^Device \d+ \(VID=091e[^)]*\) is a (Garmin [^\n]+?)\.?$", text, re.M)]


def mtp_devices():
    """[{"kind": "mtp", "name"}] for the Garmin seen by MTP (needs libmtp)."""
    if not mtp_ready():
        return []
    try:
        r = subprocess.run(["mtp-detect"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    return [{"kind": "mtp", "name": n.replace("Garmin ", "", 1)} for n in parse_detect(r.stdout + r.stderr)]


def status():
    """What the page shows: the Garmin plugged in (as a disk or by MTP) and whether libmtp is installed."""
    return {"devices": disks() + mtp_devices(), "mtp": mtp_ready()}


# ------------------------------------------------------------------ copy
def _md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_to_disk(files, volume, log=print):
    target = _garmin_dir(volume)
    if not target:
        raise DeviceError(f"No Garmin folder on {volume}: is the device still plugged in?")
    copied = 0
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        if gui():
            print(f"{MARK}S|{i}|{len(files)}|{name}", flush=True)
        dest = os.path.join(target, name)
        if os.path.isfile(dest) and os.path.getsize(dest) == os.path.getsize(f) and _md5(dest) == _md5(f):
            log(f"  {name}: already on the device")
            continue
        shutil.copyfile(f, dest + ".part")                # a stopped copy never leaves half a map
        os.replace(dest + ".part", dest)
        copied += 1
        log(f"  copied {name} ({os.path.getsize(f) / 1e6:.1f} MB)")
    log(f"{copied} map(s) copied into {target}. Eject the device before unplugging it.")
    return copied


def parse_folders(text):
    """{name: id} of the top folders in the output of mtp-folders ("16777216<TAB>GARMIN"; sub-folders are indented)."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^(\d+)\t(\S.*)$", line)
        if m:
            out[m.group(2).strip()] = int(m.group(1))
    return out


def parse_files(text):
    """[{"id", "name", "size", "parent"}] from the output of mtp-files."""
    out, cur = [], None
    for line in text.splitlines():
        line = line.strip()
        m = re.match(r"^File ID: (\d+)", line)
        if m:
            cur = {"id": int(m.group(1)), "name": "", "size": -1, "parent": -1}
            out.append(cur)
        elif cur is not None:
            if line.startswith("Filename: "):
                cur["name"] = line[len("Filename: "):]
            elif line.startswith("File size "):
                cur["size"] = int(line.split()[2])
            elif line.startswith("Parent ID: "):
                cur["parent"] = int(line.split()[2])
    return out


def _mtp(args, what, timeout=3600):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    text = r.stdout + r.stderr
    if r.returncode != 0:
        raise DeviceError(f"{what} failed: " + "\n".join(l for l in text.splitlines() if "association type" not in l)[-400:])
    return text


def copy_mtp(files, log=print):
    if not mtp_ready():
        raise DeviceError("libmtp is not installed (macOS: brew install libmtp).")
    folders = parse_folders(_mtp(["mtp-folders"], "mtp-folders", 120))
    garmin = next((i for n, i in folders.items() if n.lower() == "garmin"), None)
    if garmin is None:
        raise DeviceError("No GARMIN folder found by MTP: is the device plugged in, and no other program (Garmin "
                          "Express, Android File Transfer) using it?")
    log("Reading the list of files of the device ...")
    there = [f for f in parse_files(_mtp(["mtp-files"], "mtp-files", 600)) if f["parent"] == garmin]
    copied = 0
    for i, f in enumerate(files, 1):
        name = os.path.basename(f)
        if gui():
            print(f"{MARK}S|{i}|{len(files)}|{name}", flush=True)
        same = [e for e in there if e["name"] == name]
        if same and all(e["size"] == os.path.getsize(f) for e in same):
            log(f"  {name}: already on the device")
            continue
        for e in same:                                     # the older map of that name first (see above)
            _mtp(["mtp-delfile", "-n", str(e["id"])], "mtp-delfile", 120)
        out = _mtp(["mtp-sendfile", f, f"{garmin}/{name}"], "mtp-sendfile")
        if "New file ID" not in out:
            raise DeviceError(f"{name} was not sent: " + "\n".join(
                l for l in out.splitlines() if "association type" not in l)[-400:])
        copied += 1
        log(f"  copied {name} ({os.path.getsize(f) / 1e6:.1f} MB)")
    log(f"{copied} map(s) sent to the GARMIN folder of the device.")
    return copied


def eject(volume):
    """Unmount the disk of the device (macOS)."""
    r = subprocess.run(["diskutil", "eject", volume], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise DeviceError((r.stderr or r.stdout).strip() or "the device could not be ejected")


# ------------------------------------------------------------------ last run
def mark(out_dir, names):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, LAST_RUN), "w", encoding="utf-8") as f:
        json.dump({"maps": [os.path.basename(n) for n in names]}, f)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("copy", "mark"))
    ap.add_argument("files", nargs="+")
    ap.add_argument("--target", help="copy: disk:<volume> or mtp")
    ap.add_argument("--out", help="mark: the Output/garmin folder")
    args = ap.parse_args(argv)
    try:
        if args.action == "mark":
            mark(args.out, args.files)
            return
        missing = [f for f in args.files if not os.path.isfile(f)]
        if missing:
            raise DeviceError("not found: " + ", ".join(missing))
        if (args.target or "").startswith("disk:"):
            copy_to_disk(args.files, args.target[5:])
        elif args.target == "mtp":
            copy_mtp(args.files)
        else:
            raise DeviceError("--target disk:<volume> or mtp")
    except (DeviceError, OSError, subprocess.SubprocessError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
