"""New versions: which maprBoy is installed, whether GitHub has a newer release, and running the installer again.

The installers put the release they installed in the VERSION file of the code folder ("v1.2"; "main" when GitHub had
no release yet). A copy run from a clone has no VERSION file: it is a development copy and is never offered updates.
The check asks the GitHub API for the latest release, at most once a day (the answer is kept in state/), unless the
user presses "Check now". Nothing about the user is sent. Standard library only.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import urllib.request

from . import home

REPO = "florentchevallier/maprboy"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = "update_check.json"
DAY = 24 * 3600
PORT = 8765


def current(code_dir=CODE_DIR):
    """The installed release ("v1.2"), "main", "copy" (installed from a folder, version unknown), or None for a
    development copy (no VERSION file)."""
    try:
        with open(os.path.join(code_dir, "VERSION"), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def parse(tag):
    """(1, 2) for "v1.2" or "1.2"; None when the text is not a version number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (tag or "").strip())
    return tuple(int(n) for n in m.group(1).split(".")) if m else None


def is_newer(latest, installed):
    """True when the release `latest` should replace what is installed. A copy installed from the main branch
    (before the first release) takes any release; a development copy is never offered one."""
    new = parse(latest)
    if not new or not installed:
        return False
    old = parse(installed)
    if old is None:
        return True
    width = max(len(new), len(old))                  # v1.2 and v1.2.0 are the same version
    return new + (0,) * (width - len(new)) > old + (0,) * (width - len(old))


def _fetch_latest(timeout=8):
    req = urllib.request.Request(API_LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "maprBoy"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:                             # no release published yet
            return {"tag": None, "url": None}
        raise
    return {"tag": data.get("tag_name"), "url": data.get("html_url")}


def _read_cache():
    try:
        with open(home.path(CACHE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _write_cache(data):
    try:
        os.makedirs(home.app_home(), exist_ok=True)
        with open(home.path(CACHE), "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass


def latest(force=False, now=None, fetch=_fetch_latest):
    """{"tag", "url", "checked", "error"?}: the latest release, asked from GitHub when the kept answer is a day old
    (or when forced). A failed check (no internet) is kept too, so that it is not retried at every page load."""
    now = time.time() if now is None else now
    kept = _read_cache()
    if not force and kept and 0 <= now - kept.get("checked", 0) < DAY:
        return kept
    try:
        info = {**fetch(), "checked": now}
    except Exception as e:                            # offline, GitHub down, rate limit: tried again tomorrow
        info = {"tag": kept.get("tag"), "url": kept.get("url"), "checked": now, "error": str(e) or e.__class__.__name__}
    _write_cache(info)
    return info


def install_dir(state_dir=None):
    """The folder the installer installed into (it holds maprBoy-app/), or None when the page was not started by
    an installed copy."""
    app = os.path.dirname(state_dir or home.app_home())
    if os.path.basename(app) == "maprBoy-app" and os.path.isfile(os.path.join(app, "code", "scripts", "gui.py")):
        return os.path.dirname(app)
    return None


def status(enabled=True, force=False, **kw):
    """What the page shows: the installed version, the latest release, whether to offer it, whether the update
    button can work here."""
    installed = current(kw.pop("code_dir", CODE_DIR))
    out = {"current": installed, "enabled": enabled, "can_update": install_dir(kw.pop("state_dir", None)) is not None}
    if installed is None or not (enabled or force):
        return out
    info = latest(force=force, **kw)
    out.update(latest=info.get("tag"), url=info.get("url"), checked=info.get("checked"), error=info.get("error"),
               newer=is_newer(info.get("tag"), installed))
    return out


# ------------------------------------------------------------------ running the installer again

TERMINALS = (["x-terminal-emulator", "-e"], ["gnome-terminal", "--"], ["konsole", "-e"], ["xfce4-terminal", "-x"],
             ["mate-terminal", "-x"], ["lxterminal", "-e"], ["kitty"], ["alacritty", "-e"], ["xterm", "-e"])


def script_text(target_dir, system=None):
    """The small script run in a terminal window: it downloads the installer of the main branch (which installs the
    latest release), runs it for the same folder (the installer first waits for this page to stop), then starts
    maprBoy again. When something fails, the window stays open with the installer's message."""
    system = system or sys.platform
    if system == "win32":
        quoted = "'" + target_dir.replace("'", "''") + "'"
        return ("# maprBoy update (made by the maprBoy page): installs the latest version into the same folder.\n"
                "$ProgressPreference = 'SilentlyContinue'\n"
                "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor "
                "[Net.SecurityProtocolType]::Tls12\n"
                f"$dir = {quoted}\n"
                "$installer = Join-Path $env:TEMP 'maprboy-install.ps1'\n"
                "try {\n"
                f"    Invoke-WebRequest -UseBasicParsing -Uri '{RAW}/install_windows.ps1' -OutFile $installer\n"
                "    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer -Dir $dir -Update\n"
                "    if ($LASTEXITCODE -eq 0) {\n"
                "        Start-Process (Join-Path $dir 'maprBoy-app\\maprBoy.bat')\n"
                "        Write-Host 'maprBoy starts again: its page opens in your browser.'\n"
                "        Start-Sleep -Seconds 5\n"
                "        exit\n"
                "    }\n"
                "} catch { Write-Host \"Error: $($_.Exception.Message)\" -ForegroundColor Red }\n"
                "Read-Host 'maprBoy was not updated (see above). Press Enter to close this window'\n")
    launcher = os.path.join(target_dir, "maprBoy-app", "maprBoy.command" if system == "darwin" else "maprBoy.sh")
    url = f"{RAW}/install_macos_linux.sh"
    get = f"curl -fsSL {url}" if system == "darwin" else f"{{ curl -fsSL {url} 2>/dev/null || wget -qO- {url}; }}"
    return ("#!/bin/bash\n"
            "# maprBoy update (made by the maprBoy page): installs the latest version into the same folder, then starts it.\n"
            f'if installer="$({get})" && [ -n "$installer" ]; then\n'
            f'    bash -c "$installer" maprboy-install --dir {shlex.quote(target_dir)} --update && exec bash {shlex.quote(launcher)}\n'
            "else\n"
            '    echo "The installer could not be downloaded (internet connection?)."\n'
            "fi\n"
            'echo; read -r -p "maprBoy was not updated (see above). Press Enter to close this window. "\n')


def start_update(system=None, which=shutil.which, popen=subprocess.Popen):
    """Write the update script into state/ and open it in a new terminal window. Returns None, or a message when
    it could not be started (the page then stays up)."""
    system = system or sys.platform
    target = install_dir()
    if not target:
        return "This maprBoy was not installed with the installer: update it with git, or run the installer."
    name = {"win32": "update.ps1", "darwin": "update.command"}.get(system, "update.sh")
    path = home.path(name)
    os.makedirs(home.app_home(), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\r\n" if system == "win32" else "\n") as f:
        f.write(script_text(target, system))
    if system != "win32":
        os.chmod(path, 0o755)
    try:
        if system == "win32":
            popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path],
                  creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0x10))
        elif system == "darwin":
            popen(["open", "-a", "Terminal", path])
        else:
            for term in TERMINALS:
                if which(term[0]):
                    popen(term + ["bash", path])
                    break
            else:
                return f"No terminal program was found: run it yourself in a terminal: bash {shlex.quote(path)}"
    except OSError as e:
        return f"The update could not be started ({e})."
    return None
