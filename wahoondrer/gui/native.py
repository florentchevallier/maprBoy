"""Native folder dialog, "open in the file manager" and system notifications (macOS, Windows, Linux).

A web page cannot read the real path of a folder, but this server runs on the
user's own computer, so it can ask the operating system to show its own dialog,
or to show a notification when a long run ends while the page is out of sight.
"""
import os
import shutil
import subprocess
import sys


class NoDialog(RuntimeError):
    """No native dialog could be shown (the page then uses its own folder browser)."""


def _run(argv, timeout=900):
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def pick_folder(prompt="Choose a folder", start=""):
    """Return the chosen folder, or None if the user cancelled."""
    start = os.path.expanduser(start or "")
    start = start if os.path.isdir(start) else ""
    # these strings end up inside a script: drop the characters that could break out of it
    prompt = "".join(c for c in prompt if c not in "\"'\\`$\n")
    start = "".join(c for c in start if c not in "\"'\\`$\n")
    try:
        if sys.platform == "darwin":
            loc = f' default location POSIX file "{start}"' if start else ""
            script = ('tell application "System Events"\n activate\n'
                      f' set f to choose folder with prompt "{prompt}"{loc}\n'
                      ' return POSIX path of f\nend tell')
            r = _run(["osascript", "-e", script])
            if r.returncode == 0:
                return r.stdout.strip().rstrip("/") or "/"
            if "-128" in r.stderr or "canceled" in r.stderr.lower():
                return None
            raise NoDialog(r.stderr.strip() or "osascript failed")
        if sys.platform.startswith("win"):
            ps = ("Add-Type -AssemblyName System.Windows.Forms;"
                  "$f=New-Object System.Windows.Forms.FolderBrowserDialog;"
                  f"$f.Description='{prompt}';"
                  + (f"$f.SelectedPath='{start}';" if start else "") +
                  "if($f.ShowDialog() -eq 'OK'){$f.SelectedPath}")
            r = _run(["powershell", "-NoProfile", "-Command", ps])
            if r.returncode == 0:
                return r.stdout.strip() or None
            raise NoDialog(r.stderr.strip() or "powershell failed")
        for tool, args in (("zenity", ["--file-selection", "--directory", "--title", prompt]),
                           ("kdialog", ["--getexistingdirectory", start or os.path.expanduser("~")])):
            if shutil.which(tool):
                r = _run([tool, *args])
                return r.stdout.strip() or None if r.returncode == 0 else None
        raise NoDialog("no dialog tool found (zenity or kdialog)")
    except (OSError, subprocess.SubprocessError) as exc:
        raise NoDialog(str(exc))


def open_folder(path):
    path = os.path.expanduser(path)
    if not os.path.isdir(path):
        raise FileNotFoundError(path)
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])


APP_NAME = "maprBoy"


def _clean(text, limit):
    """One line of printable characters, at most `limit` long (the text ends up in a notification)."""
    text = " ".join("".join(c if c.isprintable() else " " for c in str(text)).split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "\u2026"


def notify(title, text, ok=True):
    """Show a system notification (a banner, with a sound on macOS). True if one was asked for.

    Never raises: a missing notification tool must not break a run. The title and the text are
    passed to the tool as separate arguments (or environment variables), never pasted into a
    script, so no character in them can change what is run.
    """
    title, text = _clean(title, 60), _clean(text, 220)
    try:
        if sys.platform == "darwin":
            # shown under "Script Editor" in System Settings > Notifications
            script = ("on run argv\n"
                      " display notification (item 2 of argv) with title (item 1 of argv) sound name (item 3 of argv)\n"
                      "end run")
            argv = ["osascript", "-e", script, title, text, "Glass" if ok else "Basso"]
            env = None
        elif sys.platform.startswith("win"):
            ps = ("Add-Type -AssemblyName System.Windows.Forms;Add-Type -AssemblyName System.Drawing;"
                  "$n=New-Object System.Windows.Forms.NotifyIcon;"
                  "$n.Icon=[System.Drawing.SystemIcons]::" + ("Information" if ok else "Error") + ";"
                  "$n.Visible=$true;"
                  "$n.ShowBalloonTip(10000,$env:MAPRBOY_TITLE,$env:MAPRBOY_TEXT,"
                  "[System.Windows.Forms.ToolTipIcon]::" + ("Info" if ok else "Error") + ");"
                  "Start-Sleep -Seconds 12;$n.Dispose()")
            argv = ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps]
            env = dict(os.environ, MAPRBOY_TITLE=title, MAPRBOY_TEXT=text)
        else:
            if not shutil.which("notify-send"):
                return False
            argv = ["notify-send", "-a", APP_NAME, "-u", "normal" if ok else "critical", "--", title, text]
            env = None
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, env=env)
        return True
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
