"""Where maprBoy keeps its own files (settings, caches, run history, the contour lines environment).

The installed app sets MAPRBOY_HOME (install.sh: <install folder>/maprBoy-app/state) so that everything stays in
the folder the user chose. Without it, as when the page is started from a clone, ~/.wahoondrer is used.
"""
import os


def app_home():
    return os.path.abspath(os.path.expanduser(os.environ.get("MAPRBOY_HOME") or os.path.join("~", ".wahoondrer")))


def path(*parts):
    return os.path.join(app_home(), *parts)
