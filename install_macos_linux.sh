#!/bin/bash
# maprBoy installer for macOS (Intel and Apple Silicon) and Linux (64-bit).
#
# In the Terminal:
#   macOS:  bash -c "$(curl -fsSL https://raw.githubusercontent.com/florentchevallier/maprboy/main/install_macos_linux.sh)"
#   Linux:  bash -c "$(wget -qO- https://raw.githubusercontent.com/florentchevallier/maprboy/main/install_macos_linux.sh)"
#           (wget: curl is not always there on Linux)
# or, from a copy of the repository:
#   bash install_macos_linux.sh [--dir FOLDER] [--from COPY] [--update]
#   (--update: what the page's Update button runs: waits for the page to stop, asks nothing more than needed)
#
# Everything goes into one folder chosen by the user (default ~/maprBoy):
#   maprBoy-app/   the code, micromamba and its environment (Python, GDAL, osmium, Java...), Osmosis, mkgmap, adb,
#                  the settings and caches (state/), the launcher and the uninstaller
#   maprBoy-data/  Wandrer and Squadrats exports, OSM data, the maps made (only on a first installation)
# A launcher is put on the Desktop (Linux: also in the applications menu). Running the installer again updates the code; the environment is made again
# only when environment.yml changed. The code comes from GitHub (the latest release; the main branch while there is
# none), or from the copy of maprBoy that holds this install_macos_linux.sh (when it is run from one), or from --from COPY.
set -euo pipefail

REPO="florentchevallier/maprboy"
LATEST_API="https://api.github.com/repos/$REPO/releases/latest"
MICROMAMBA_URL="https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba"
OSMOSIS_VERSION="0.49.2"
OSMOSIS_URL="https://github.com/openstreetmap/osmosis/releases/download/$OSMOSIS_VERSION/osmosis-$OSMOSIS_VERSION.zip"
MAPWRITER_URL="https://repo1.maven.org/maven2/org/mapsforge/mapsforge-map-writer/0.21.0/mapsforge-map-writer-0.21.0-jar-with-dependencies.jar"
MKGMAP_VERSION="r4924"
MKGMAP_URL="https://www.mkgmap.org.uk/download/mkgmap-$MKGMAP_VERSION.zip"
ADB_URL="https://dl.google.com/android/repository/platform-tools-latest"    # + -darwin.zip or -linux.zip
PORT=8765

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
ask()  { local reply; read -r -p "$1" reply </dev/tty || reply=""; printf '%s' "$reply"; }
fetch() {
    if command -v curl >/dev/null; then curl -fL --retry 3 --progress-bar -o "$2" "$1" || fail "could not download $1"
    else wget -q --show-progress --tries=3 -O "$2" "$1" || fail "could not download $1"; fi
}
fetch_text() { if command -v curl >/dev/null; then curl -fsSL "$1"; else wget -qO- "$1"; fi; }
page_up() { (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; }   # a maprBoy page answering? (bash itself, no curl)

DIR=""
FROM=""
UPDATE=""
TAG=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:-}"; shift 2 ;;
        --from) FROM="${2:-}"; shift 2 ;;
        --update) UPDATE=1; shift ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) fail "unknown option $1 (see --help)" ;;
    esac
done

if [ -z "$FROM" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then          # run from a copy of maprBoy: install that copy
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -f "$here/scripts/gui.py" ] && FROM="$here"
fi
case "$(uname -s)" in
    Darwin)
        OS="macos"
        if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then PLATFORM="osx-arm64"; else PLATFORM="osx-64"; fi
        SYSTEM="macOS $(sw_vers -productVersion)"
        command -v curl >/dev/null && command -v unzip >/dev/null || fail "curl and unzip are needed (they come with macOS)." ;;
    Linux)
        OS="linux"
        case "$(uname -m)" in
            x86_64) PLATFORM="linux-64" ;;
            aarch64|arm64) PLATFORM="linux-aarch64" ;;
            *) fail "this processor ($(uname -m)) is not supported: 64-bit Intel/AMD or ARM only." ;;
        esac
        SYSTEM="$( (. /etc/os-release && echo "$PRETTY_NAME") 2>/dev/null || echo Linux)"
        { command -v curl >/dev/null || command -v wget >/dev/null; } && command -v unzip >/dev/null \
            || fail "unzip and curl (or wget) are needed. On Ubuntu or Debian: sudo apt install unzip curl" ;;
    *) fail "this installer is for macOS and Linux (Windows: install_windows.ps1)." ;;
esac
if [ "$OS" = "macos" ]; then LAUNCHER="maprBoy.command"; UNINSTALLER="uninstall.command"
else LAUNCHER="maprBoy.sh"; UNINSTALLER="uninstall.sh"; fi

say "maprBoy installer ($PLATFORM, $SYSTEM)"

if [ -n "$UPDATE" ]; then                                            # started by the page, which is stopping now
    for _ in $(seq 60); do page_up || break; sleep 1; done
fi
if page_up; then                                                     # the code must not change under a running page
    fail "maprBoy is running: stop it with 'Quit maprBoy' in the settings of its page, then run the installer again."
fi

# ------------------------------------------------------------------ the folder
if [ -z "$DIR" ]; then
    while :; do                      # the folder is confirmed: a word typed by mistake would become its name
        DIR="$(ask "Folder for maprBoy: press Enter for the default, ~/maprBoy, or type another folder: ")"
        DIR="${DIR:-$HOME/maprBoy}"
        DIR="${DIR/#\~/$HOME}"
        case "$DIR" in /*) ;; *) DIR="$PWD/$DIR" ;; esac
        case "$(ask "maprBoy will be installed into $DIR. OK? [Y/n] ")" in [nN]*) ;; *) break ;; esac
    done
fi
DIR="${DIR/#\~/$HOME}"
mkdir -p "$DIR" || fail "cannot create $DIR"
DIR="$(cd "$DIR" && pwd)"
APP="$DIR/maprBoy-app"
DATA="$DIR/maprBoy-data"
mkdir -p "$APP/bin" "$APP/tools" "$APP/state"
echo "Installing into $DIR"

# ------------------------------------------------------------------ the code
say "1/5  maprBoy code"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
if [ -n "$FROM" ]; then
    FROM="${FROM/#\~/$HOME}"
    [ -f "$FROM/scripts/gui.py" ] || fail "$FROM is not a copy of maprBoy"
    mkdir -p "$TMP/code"
    (cd "$FROM" && tar cf - --exclude .git --exclude __pycache__ --exclude ".DS_Store" --exclude "Art (do not commit)" \
        --exclude CLAUDE.md .) | (cd "$TMP/code" && tar xf -)
    echo "from $FROM"
else
    # the latest release ("tag_name" in GitHub's answer); the main branch when there is none (or no answer)
    TAG="$(fetch_text "$LATEST_API" 2>/dev/null | grep -o '"tag_name": *"[^"]*"' | head -n 1 | sed 's/.*"\([^"]*\)"$/\1/' || true)"
    if [ -n "$TAG" ]; then ZIP="https://github.com/$REPO/archive/refs/tags/$TAG.zip"
    else ZIP="https://github.com/$REPO/archive/refs/heads/main.zip"; fi
    fetch "$ZIP" "$TMP/code.zip"
    unzip -q "$TMP/code.zip" -d "$TMP/unzip"
    mv "$TMP/unzip"/*/ "$TMP/code"
    echo "${TAG:-main}" > "$TMP/code/VERSION"           # what the page compares with the latest release
    echo "from GitHub (${TAG:-main branch: no release yet})"
fi
rm -rf "$APP/code"
mv "$TMP/code" "$APP/code"

# ------------------------------------------------------------------ micromamba and the environment
say "2/5  Python environment (the first time: several minutes, about 2 GB)"
MM="$APP/bin/micromamba"
if [ ! -x "$MM" ]; then
    fetch "$MICROMAMBA_URL-$PLATFORM" "$MM"
    chmod +x "$MM"
fi
export MAMBA_ROOT_PREFIX="$APP/mamba"
export PIP_NO_CACHE_DIR=1          # pip (WMC, pyhgtmap) would leave about 130 MB in the user's cache, outside maprBoy
ENV="$APP/env"
if command -v sha256sum >/dev/null; then SHA="sha256sum"; else SHA="shasum -a 256"; fi
WANT="$($SHA "$APP/code/environment.yml" | cut -d' ' -f1)"
HAVE="$(cat "$APP/state/environment.sha256" 2>/dev/null || true)"
if [ ! -d "$ENV" ] || [ "$WANT" != "$HAVE" ]; then
    rm -rf "$ENV"
    "$MM" create -y -p "$ENV" -f "$APP/code/environment.yml" || fail "the environment could not be made (see above)."
    "$MM" install -y -p "$ENV" -c conda-forge "openjdk>=17" || fail "Java could not be installed (see above)."
    echo "$WANT" > "$APP/state/environment.sha256"
    "$MM" clean -y -a -q >/dev/null 2>&1 || true            # the downloaded packages are not needed any more
else
    echo "already up to date"
fi

# ------------------------------------------------------------------ the tools
say "3/5  Tools: Osmosis, mkgmap, adb"
OSMOSIS_DIR="$APP/tools/osmosis-$OSMOSIS_VERSION"
if [ ! -x "$OSMOSIS_DIR/bin/osmosis" ]; then
    fetch "$OSMOSIS_URL" "$TMP/osmosis.zip"
    rm -rf "$TMP/osm" && unzip -q "$TMP/osmosis.zip" -d "$TMP/osm"
    root="$(dirname "$(dirname "$(find "$TMP/osm" -path '*/bin/osmosis' -type f | head -1)")")"
    [ -n "$root" ] && [ "$root" != "." ] || fail "osmosis was not found in its zip file"
    rm -rf "$OSMOSIS_DIR" && mv "$root" "$OSMOSIS_DIR"
    chmod +x "$OSMOSIS_DIR/bin/osmosis"
fi
PLUGINS="$HOME/.openstreetmap/osmosis/plugins"          # where Osmosis 0.49 looks for plugins (and WMC puts this one)
mkdir -p "$PLUGINS"
if ! ls "$PLUGINS/"*map-writer*.jar >/dev/null 2>&1; then
    fetch "$MAPWRITER_URL" "$PLUGINS/$(basename "$MAPWRITER_URL")"
fi
MKGMAP_JAR="$APP/tools/mkgmap-$MKGMAP_VERSION/mkgmap.jar"
if [ ! -f "$MKGMAP_JAR" ]; then
    fetch "$MKGMAP_URL" "$TMP/mkgmap.zip"
    unzip -q -o "$TMP/mkgmap.zip" -d "$APP/tools"
    [ -f "$MKGMAP_JAR" ] || fail "mkgmap.jar was not found in its zip file"
fi
ADB="$APP/tools/platform-tools/adb"
if [ "$PLATFORM" = "linux-aarch64" ]; then                    # Google publishes adb for 64-bit Intel/AMD Linux only
    ADB=""
    if command -v adb >/dev/null; then echo "adb: $(command -v adb)"
    else echo "adb is not installed (copy to a Wahoo or a Karoo): sudo apt install adb"; fi
elif [ ! -x "$ADB" ]; then
    if [ "$OS" = "macos" ]; then fetch "$ADB_URL-darwin.zip" "$TMP/adb.zip"; else fetch "$ADB_URL-linux.zip" "$TMP/adb.zip"; fi
    unzip -q -o "$TMP/adb.zip" -d "$APP/tools"
    [ -x "$ADB" ] || fail "adb was not found in its zip file"
fi
if command -v mtp-detect >/dev/null; then
    echo "libmtp (Garmin watches): installed"
elif [ "$OS" = "linux" ]; then
    echo "libmtp is not installed: only needed to copy maps to a Garmin watch. On Ubuntu or Debian: sudo apt install mtp-tools"
elif [ -n "$UPDATE" ]; then                     # an update asks no question: the installer run by hand does
    echo "libmtp is not installed: only needed to copy maps to a Garmin watch (run the installer to install it)."
elif command -v brew >/dev/null && brew --version >/dev/null 2>&1 \
        && [[ "$(brew list libmtp 2>&1 || true)" != *"too old"* ]]; then
    # (on macOS 10.14, brew --version works, but brew list says the system is "too old to run Homebrew", and installing
    # libmtp crashed. The answer is read first: with pipefail, "brew list | grep" would take brew's failure as the result)
    if [ "$(ask "Garmin watch (Forerunner, Fenix...)? Install libmtp with Homebrew to copy maps to it [y/N] ")" = "y" ]; then
        brew install libmtp || echo "libmtp could not be installed: the copy to a watch will not work (the rest does)."
    fi
else
    echo "libmtp is not installed: only needed to copy maps to a Garmin watch (needs a working Homebrew:"
    echo "brew install libmtp). Without it, copy the maps to the watch by hand."
fi

# ------------------------------------------------------------------ settings and data folders
say "4/5  Settings and the contour lines tool"
export MAPRBOY_HOME="$APP/state"
"$MM" run -p "$ENV" python "$APP/code/scripts/install_setup.py" --data "$DATA" \
    --mkgmap "$MKGMAP_JAR" --osmosis "$OSMOSIS_DIR/bin/osmosis" --adb "$ADB" || fail "the settings could not be written."
echo "Contour lines tool (pyhgtmap):"
(cd "$APP/code" && "$MM" run -p "$ENV" python -c "from wahoondrer import contour; contour.setup()") \
    || echo "pyhgtmap could not be installed: maps without contour lines still work (run the installer again later)."

# ------------------------------------------------------------------ launcher and uninstaller
say "5/5  Launcher"
q() { printf '%q' "$1"; }
MENU_FILE=""; DESKTOP_FILE=""                     # Linux: the .desktop launchers (applications menu, Desktop)
if [ "$OS" = "linux" ]; then
    MENU_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/maprboy.desktop"
    desk="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"          # its name depends on the language
    if [ -d "$desk" ] && [ "$desk" != "$HOME" ]; then DESKTOP_FILE="$desk/maprBoy.desktop"; fi
    OPEN="xdg-open"
else
    OPEN="open"
fi
cat > "$APP/$LAUNCHER" <<EOF
#!/bin/bash
# maprBoy launcher (made by the maprBoy installer): starts the page; close this window or press Ctrl+C to stop it.
APP=$(q "$APP")
export MAMBA_ROOT_PREFIX="\$APP/mamba" MAPRBOY_HOME="\$APP/state"
export PATH="\$APP/tools/osmosis-$OSMOSIS_VERSION/bin:\$APP/tools/platform-tools:\$PATH"
if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    echo "maprBoy is already running: opening the page."
    $OPEN "http://127.0.0.1:$PORT/"
    exit 0
fi
cd "\$APP/code" && exec "\$APP/bin/micromamba" run -p "\$APP/env" python scripts/gui.py --port $PORT
EOF
cat > "$APP/$UNINSTALLER" <<EOF
#!/bin/bash
# maprBoy uninstaller (made by the maprBoy installer). The whole script is read before anything is deleted.
main() {
    local APP=$(q "$APP") DATA=$(q "$DATA") DIR=$(q "$DIR") reply
    echo "This removes maprBoy from \$DIR."
    read -r -p "Delete your data too (maps, OSM data, Wandrer and Squadrats exports in \$DATA)? [y/N] " reply
    local wmc="\$HOME/wahooMapsCreatorData"
    if [ -L "\$wmc" ] && [ "\$(readlink "\$wmc")" = "\$DATA/OSM_data" ]; then rm -f "\$wmc"; fi
    local f
    for f in "\$HOME/Desktop/maprBoy.command" $(q "$DESKTOP_FILE") $(q "$MENU_FILE"); do     # the launchers it made
        if [ -n "\$f" ] && grep -q "maprBoy launcher (made by" "\$f" 2>/dev/null; then rm -f "\$f"; fi
    done
    rm -rf "\$APP"
    if [ "\$reply" = "y" ]; then rm -rf "\$DATA"; echo "Data deleted."; else echo "Data kept in \$DATA"; fi
    rm -f "\$DIR/.DS_Store"                   # left by the Finder when the folder was opened: not a reason to keep it
    rmdir "\$DIR" 2>/dev/null || true
    local plugins=(\$HOME/.openstreetmap/osmosis/plugins/*map-writer*.jar)
    if [ -e "\${plugins[0]}" ]; then
        read -r -p "Also delete the Osmosis MapWriter plugin in ~/.openstreetmap (another program may use it)? [y/N] " reply
        [ "\$reply" = "y" ] && rm -f "\${plugins[@]}"
    fi
    echo "maprBoy is uninstalled."
}
main
EOF
chmod +x "$APP/$LAUNCHER" "$APP/$UNINSTALLER"
# the maprBoy icon on the launchers (Finder custom icon, set with AppKit through osascript; nothing to install)
set_icon() {
    osascript -l JavaScript -e 'function run(a) { ObjC.import("AppKit");
        return $.NSWorkspace.sharedWorkspace.setIconForFileOptions($.NSImage.alloc.initWithContentsOfFile(a[0]), a[1], 0) }' \
        "$APP/code/wahoondrer/gui/static/maprboy-icon.png" "$1" >/dev/null 2>&1 || true
}
if [ "$OS" = "macos" ]; then
    set_icon "$APP/maprBoy.command"
    if [ -d "$HOME/Desktop" ]; then
        cp "$APP/maprBoy.command" "$HOME/Desktop/maprBoy.command"
        set_icon "$HOME/Desktop/maprBoy.command"
        WHERE="double-click maprBoy.command on the Desktop"
    else
        WHERE="double-click $APP/maprBoy.command"
    fi
    UNDO="double-click $APP/uninstall.command"
else
    # a .desktop launcher with the maprBoy icon, in the applications menu and on the Desktop; it opens a terminal
    write_entry() {
        printf '%s\n' "[Desktop Entry]" "# maprBoy launcher (made by the maprBoy installer)" "Type=Application" "Name=maprBoy" \
            "Comment=Custom OSM maps with POIs, Wandrer and Squadrats (the page opens in your browser)" \
            "Exec=bash \"$APP/$LAUNCHER\"" "Icon=$APP/code/wahoondrer/gui/static/maprboy-icon.png" \
            "Terminal=true" "Categories=Utility;" > "$1"
        chmod +x "$1"
        gio set "$1" metadata::trusted true >/dev/null 2>&1 || true    # GNOME: may spare the "Allow Launching" step
    }
    mkdir -p "$(dirname "$MENU_FILE")"
    write_entry "$MENU_FILE"
    WHERE="maprBoy in the applications menu"
    if [ -n "$DESKTOP_FILE" ]; then
        write_entry "$DESKTOP_FILE"
        WHERE="$WHERE, or maprBoy on the Desktop (the first time, right-click it and choose Allow Launching if asked)"
    fi
    UNDO="bash $APP/uninstall.sh"
fi

say "Done."
if [ -n "$UPDATE" ]; then echo "maprBoy is up to date (${TAG:-main}): it starts again."; exit 0; fi
echo "Start maprBoy: $WHERE. The page opens in your browser; keep the terminal window open while you use it."
echo "Uninstall: $UNDO"
