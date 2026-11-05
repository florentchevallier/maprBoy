#!/usr/bin/env python3
"""Fill the settings of a fresh installation (called by the installers, in the maprBoy environment, with MAPRBOY_HOME set).

Only what is still empty is filled, so running the installer again (an update) never changes the user's choices:
- the data folders: the standard sub-folders in --data (as the "Set up my folders" button of the page), and the
  link ~/wahooMapsCreatorData -> <data>/OSM_data that WMC needs, unless that path already exists;
- the paths of the tools the installer downloaded (--mkgmap, --osmosis, --adb).

Usage: python3 scripts/install_setup.py --data DIR [--mkgmap JAR] [--osmosis PATH] [--adb PATH]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.gui import regions, settings  # noqa: E402


def setup(data, tools, log=print):
    s = settings.load()
    folders = s["folders"]
    if not (folders.get("wandrer") or folders.get("out")):
        data = os.path.abspath(os.path.expanduser(data))
        folders.update(settings.make_standard_folders(data))
        folders["data"] = data
        log(f"Data folders created in {data}")
        if os.path.lexists(regions.WMC_DIR):
            log(f"{regions.WMC_DIR} already exists: left as it is (the OSM data stays where it is).")
        else:
            regions.link_data_dir(data, False)
            log(f"OSM data folder: {os.path.join(data, regions.OSM_FOLDER)} (linked from {regions.WMC_DIR})")
    else:
        log("Data folders already set: kept.")
    for key, value in tools.items():
        if value and not s["tools"].get(key):
            s["tools"][key] = os.path.abspath(os.path.expanduser(value))
            log(f"{key}: {s['tools'][key]}")
    settings.save(s)
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True)
    ap.add_argument("--mkgmap", default="")
    ap.add_argument("--osmosis", default="")
    ap.add_argument("--adb", default="")
    args = ap.parse_args()
    setup(args.data, {"mkgmap": args.mkgmap, "osmosis": args.osmosis, "adb": args.adb})


if __name__ == "__main__":
    main()
