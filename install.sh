#!/bin/bash
# maprBoy installer for macOS (Intel and Apple Silicon).
#
# In the Terminal:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/florentchevallier/maprboy/main/install.sh)"
# or, from a copy of the repository:
#   bash install.sh [--dir FOLDER] [--from COPY]
#
# Everything goes into one folder chosen by the user (default ~/maprBoy):
#   maprBoy-app/   the code, micromamba and its environment (Python, GDAL, osmium, Java...), Osmosis, mkgmap, adb,
#                  the settings and caches (state/), the launcher and uninstall.command
#   maprBoy-data/  Wandrer and Squadrats exports, OSM data, the maps made (only on a first installation)
# A launcher is put on the Desktop. Running the installer again updates the code; the environment is made again
# only when environment.yml changed. The code comes from GitHub, or from the copy of maprBoy that holds this
# install.sh (when it is run from one), or from --from COPY.
set -euo pipefail

REPO_ZIP="https://github.com/florentchevallier/maprboy/archive/refs/heads/main.zip"
MICROMAMBA_URL="https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba"
OSMOSIS_VERSION="0.49.2"
OSMOSIS_URL="https://github.com/openstreetmap/osmosis/releases/download/$OSMOSIS_VERSION/osmosis-$OSMOSIS_VERSION.zip"
MAPWRITER_URL="https://repo1.maven.org/maven2/org/mapsforge/mapsforge-map-writer/0.21.0/mapsforge-map-writer-0.21.0-jar-with-dependencies.jar"
MKGMAP_VERSION="r4924"
MKGMAP_URL="https://www.mkgmap.org.uk/download/mkgmap-$MKGMAP_VERSION.zip"
ADB_URL="https://dl.google.com/android/repository/platform-tools-latest-darwin.zip"
PORT=8765

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
fail() { printf '\n\033[31mError: %s\033[0m\n' "$*" >&2; exit 1; }
ask()  { local reply; read -r -p "$1" reply </dev/tty || reply=""; printf '%s' "$reply"; }
fetch() { curl -fL --retry 3 --progress-bar -o "$2" "$1" || fail "could not download $1"; }

DIR=""
FROM=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dir) DIR="${2:-}"; shift 2 ;;
        --from) FROM="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        *) fail "unknown option $1 (see --help)" ;;
    esac
done

if [ -z "$FROM" ] && [ -f "${BASH_SOURCE[0]:-}" ]; then          # run from a copy of maprBoy: install that copy
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    [ -f "$here/scripts/gui.py" ] && FROM="$here"
fi
[ "$(uname -s)" = "Darwin" ] || fail "this installer is for macOS."
if [ "$(sysctl -n hw.optional.arm64 2>/dev/null || echo 0)" = "1" ]; then PLATFORM="osx-arm64"; else PLATFORM="osx-64"; fi
command -v curl >/dev/null && command -v unzip >/dev/null || fail "curl and unzip are needed (they come with macOS)."

say "maprBoy installer ($PLATFORM, macOS $(sw_vers -productVersion))"

if curl -s -m 3 -o /dev/null "http://127.0.0.1:$PORT/"; then           # the code must not change under a running page
    fail "maprBoy is running: close its Terminal window (the one maprBoy.command opened), then run the installer again."
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
    fetch "$REPO_ZIP" "$TMP/code.zip"
    unzip -q "$TMP/code.zip" -d "$TMP/unzip"
    mv "$TMP/unzip"/*/ "$TMP/code"
    echo "from GitHub (main)"
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
ENV="$APP/env"
WANT="$(shasum -a 256 "$APP/code/environment.yml" | cut -d' ' -f1)"
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
if [ ! -x "$ADB" ]; then
    fetch "$ADB_URL" "$TMP/adb.zip"
    unzip -q -o "$TMP/adb.zip" -d "$APP/tools"
    [ -x "$ADB" ] || fail "adb was not found in its zip file"
fi
if command -v mtp-detect >/dev/null; then
    echo "libmtp (Garmin watches): installed"
elif command -v brew >/dev/null && brew --version >/dev/null 2>&1 && ! brew list libmtp 2>&1 | grep -qi "too old"; then
    # (on macOS 10.14, brew --version works, but brew list says the system is "too old to run Homebrew", and installing
    # libmtp crashed)
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
cat > "$APP/maprBoy.command" <<EOF
#!/bin/bash
# maprBoy launcher (made by install.sh): starts the page; close this window or press Ctrl+C to stop it.
APP=$(q "$APP")
export MAMBA_ROOT_PREFIX="\$APP/mamba" MAPRBOY_HOME="\$APP/state"
export PATH="\$APP/tools/osmosis-$OSMOSIS_VERSION/bin:\$APP/tools/platform-tools:\$PATH"
if curl -s -o /dev/null "http://127.0.0.1:$PORT/"; then
    echo "maprBoy is already running: opening the page."
    open "http://127.0.0.1:$PORT/"
    exit 0
fi
cd "\$APP/code" && exec "\$APP/bin/micromamba" run -p "\$APP/env" python scripts/gui.py --port $PORT
EOF
cat > "$APP/uninstall.command" <<EOF
#!/bin/bash
# maprBoy uninstaller (made by install.sh). The whole script is read before anything is deleted.
main() {
    local APP=$(q "$APP") DATA=$(q "$DATA") DIR=$(q "$DIR") reply
    echo "This removes maprBoy from \$DIR."
    read -r -p "Delete your data too (maps, OSM data, Wandrer and Squadrats exports in \$DATA)? [y/N] " reply
    local wmc="\$HOME/wahooMapsCreatorData"
    if [ -L "\$wmc" ] && [ "\$(readlink "\$wmc")" = "\$DATA/OSM_data" ]; then rm -f "\$wmc"; fi
    if grep -q "maprBoy launcher (made by install.sh)" "\$HOME/Desktop/maprBoy.command" 2>/dev/null; then
        rm -f "\$HOME/Desktop/maprBoy.command"
    fi
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
chmod +x "$APP/maprBoy.command" "$APP/uninstall.command"
# the maprBoy icon on the launchers (Finder custom icon, set with AppKit through osascript; nothing to install)
set_icon() {
    osascript -l JavaScript -e 'function run(a) { ObjC.import("AppKit");
        return $.NSWorkspace.sharedWorkspace.setIconForFileOptions($.NSImage.alloc.initWithContentsOfFile(a[0]), a[1], 0) }' \
        "$APP/code/wahoondrer/gui/static/maprboy-icon.png" "$1" >/dev/null 2>&1 || true
}
set_icon "$APP/maprBoy.command"
if [ -d "$HOME/Desktop" ]; then
    cp "$APP/maprBoy.command" "$HOME/Desktop/maprBoy.command"
    set_icon "$HOME/Desktop/maprBoy.command"
    WHERE="the Desktop"
else
    WHERE="$APP"
fi

say "Done."
echo "Start maprBoy: double-click maprBoy.command on $WHERE (the page opens in your browser; keep the Terminal"
echo "window open while you use it). Uninstall: double-click $APP/uninstall.command"
