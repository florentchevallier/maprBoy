#!/usr/bin/env python3
"""Start the Wahoondrer web interface (opens your browser).

Usage:  python3 scripts/gui.py [--port 8765] [--no-browser]
Run it in the environment where WahooMapsCreator is installed (wahoondrer-env).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from wahoondrer.gui.server import serve  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    serve(args.port, not args.no_browser)


if __name__ == "__main__":
    main()
