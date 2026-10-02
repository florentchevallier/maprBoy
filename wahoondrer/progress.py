"""Progress display for build_tiles.py: a download bar and a "step n of N" counter.

Two ways to show them:
- in a terminal: the bar is redrawn on its own line (carriage return);
- in the web interface (environment variable WAHOONDRER_GUI=1): special lines starting with
  the character \\x01 that the page draws as a bar. Anything else is an ordinary log line.
"""
import os
import sys
import time

MARK = "\x01"


def gui():
    return os.environ.get("WAHOONDRER_GUI") == "1"


def bar(fraction, width=24):
    n = int(round(max(0.0, min(1.0, fraction)) * width))
    return "█" * n + "░" * (width - n)


def mb(n):
    return f"{n / 1e6:.0f}" if n >= 1e7 else f"{n / 1e6:.1f}"


def download_text(name, done, total, speed):
    if total:
        return f"{name}  {bar(done / total)} {done * 100 // total:3d} %  {mb(done)}/{mb(total)} MB  {speed / 1e6:.1f} MB/s"
    return f"{name}  {mb(done)} MB  {speed / 1e6:.1f} MB/s"


def download(url, path):
    """Download url to path with a progress bar (replaces wahoomc.downloader.download_url_to_file)."""
    import requests
    name = os.path.basename(path)
    if gui():
        print(f"{MARK}P|{name}  connecting...", flush=True)
    else:
        print(f"  {name}: connecting...", flush=True)
    r = requests.get(url, allow_redirects=True, stream=True, timeout=1800)
    if r.status_code != 200:
        print(f"! failed download URL: {url}")
        sys.exit(1)
    total = int(r.headers.get("Content-Length") or 0)
    part = path + ".part"            # the final name only exists once the file is complete
    done, t0, last, last_pct = 0, time.time(), 0.0, -1
    tty = sys.stdout.isatty()
    with open(part, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 100):
            f.write(chunk)
            done += len(chunk)
            now = time.time()
            if now - last < (0.2 if tty else 1.0):
                continue
            last = now
            text = download_text(name, done, total, done / max(now - t0, 1e-6))
            if gui():
                print(f"{MARK}P|{text}", flush=True)
            elif tty:
                print("\r" + text, end="", flush=True)
            else:
                pct = done * 10 // total if total else -1       # a line every 10 %
                if pct != last_pct:
                    last_pct = pct
                    print("  " + text, flush=True)
    if total and done != total:
        os.remove(part)
        print(f"! download of {name} incomplete ({done} of {total} bytes): run again")
        sys.exit(1)
    os.replace(part, path)
    text = download_text(name, done, total or done, done / max(time.time() - t0, 1e-6))
    if gui():
        print(f"{MARK}P|", flush=True)                         # clears the bar
    elif tty:
        print("\r" + text)
    print(f"  downloaded {name}: {mb(done)} MB in {int(time.time() - t0)} s", flush=True)


class Steps:
    """"step n of N" with the time each step took. total can be changed once it is known."""

    def __init__(self, total):
        self.total, self.n, self.t0 = total, 0, time.time()

    def _show(self, label):
        print(f"[step {self.n}/{self.total}] {label}", flush=True)
        if gui():
            print(f"{MARK}S|{self.n}|{self.total}|{label}", flush=True)

    def run(self, label, func, *args):
        self.n += 1
        self._show(label)
        t = time.time()
        res = func(*args)
        print(f"  ({label}: {int(time.time() - t)} s)", flush=True)
        return res

    def done(self):
        if gui():
            print(f"{MARK}S|{self.total}|{self.total}|", flush=True)
