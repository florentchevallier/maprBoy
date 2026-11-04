"""The land polygons (coastlines) of the Wahoo maps, kept by maprBoy instead of WMC.

WMC renews land-polygons-split-4326 (about 1 GB) with the OSM regions, every few days, and deletes the old copy
before it downloads the new one: when osmdata.openstreetmap.de does not answer, nothing is left and the build
stops. Here they are renewed every 6 months only, the new copy is unpacked next to the old one and replaces it once
complete, and the old copy is kept when the download fails. The date of the copy is kept in a small file
(.maprboy-downloaded), since the date of land_polygons.shp is set to now before WMC looks at it (WMC would
otherwise delete a copy older than its own limit).
"""
import os
import shutil
import time
import zipfile

URL = "https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip"
FOLDER = "land-polygons-split-4326"
SHP = "land_polygons.shp"
STAMP = ".maprboy-downloaded"
MAX_DAYS = 182                       # about 6 months


class LandError(Exception):
    pass


def downloaded_at(folder):
    """When the copy in folder was downloaded (seconds), or None when there is no copy. A copy made by WMC (no
    stamp yet) gets a stamp with the date of its land_polygons.shp."""
    shp, stamp = os.path.join(folder, SHP), os.path.join(folder, STAMP)
    if not os.path.isfile(shp):
        return None
    if not os.path.isfile(stamp):
        with open(stamp, "w", encoding="utf-8"):
            pass
        t = os.path.getmtime(shp)
        os.utime(stamp, (t, t))
    return os.path.getmtime(stamp)


def _for_wmc(folder):
    now = time.time()
    os.utime(os.path.join(folder, SHP), (now, now))


def refresh(dl_dir, download, max_days=MAX_DAYS, log=print, now=None):
    """Make sure dl_dir/land-polygons-split-4326 holds land polygons WMC will use as they are.
    download(url, path) fetches a file. Returns "fresh", "renewed" (a new copy: the land tiles must be made again)
    or "kept" (too old, but the download failed); raises LandError when there is no copy and none can be had."""
    now = time.time() if now is None else now
    folder = os.path.join(dl_dir, FOLDER)
    since = downloaded_at(folder)
    if since is not None and now - since < max_days * 86400:
        _for_wmc(folder)
        return "fresh"
    log("  Land polygons: " + ("none on disk yet, downloading them (about 900 MB, once)" if since is None else
                               f"older than {max_days} days, downloading a new copy (the old one is kept meanwhile)"))
    zpath = os.path.join(dl_dir, FOLDER + ".zip")
    tmp = os.path.join(dl_dir, FOLDER + ".new")
    try:
        os.makedirs(dl_dir, exist_ok=True)
        download(URL, zpath)
        shutil.rmtree(tmp, ignore_errors=True)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(tmp)
        new = os.path.join(tmp, FOLDER)
        if not os.path.isfile(os.path.join(new, SHP)):
            raise LandError(f"{SHP} is not in the downloaded file")
    except (Exception, SystemExit) as e:          # progress.download stops with sys.exit on an HTTP error
        shutil.rmtree(tmp, ignore_errors=True)
        if os.path.exists(zpath):
            os.remove(zpath)
        why = str(e).splitlines()[0][:200] if str(e) else type(e).__name__
        if since is None:
            raise LandError("the land polygons (coastlines) could not be downloaded from osmdata.openstreetmap.de "
                            f"({why}) and there is no copy on disk yet: check the internet connection, or try again "
                            "later. They are needed once, for the Wahoo maps.") from None
        log(f"! Land polygons: the new copy could not be downloaded ({why}); the old copy is used.")
        _for_wmc(folder)
        return "kept"
    old = folder + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(folder):
        os.rename(folder, old)
    os.rename(new, folder)
    shutil.rmtree(old, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)
    os.remove(zpath)
    with open(os.path.join(folder, STAMP), "w", encoding="utf-8"):
        pass                                       # its date is now: the date of this copy
    _for_wmc(folder)
    log("  Land polygons: new copy installed.")
    return "renewed"
