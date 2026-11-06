# maprBoy installer for Windows (10 and 11, 64-bit).
#
# In PowerShell (Start menu > type PowerShell), paste:
#   irm https://raw.githubusercontent.com/florentchevallier/maprboy/main/install_windows.ps1 -OutFile "$env:TEMP\install_windows.ps1"; powershell -ExecutionPolicy Bypass -File "$env:TEMP\install_windows.ps1"
#   (downloaded, then run: Microsoft Defender blocked "irm ... | iex" in November 2026, Trojan:Win32/Commando.C!ml)
# or, from a copy of the repository:
#   powershell -ExecutionPolicy Bypass -File install_windows.ps1 [-Dir FOLDER] [-From COPY] [-Update]
#   (-Update: what the page's Update button runs: waits for the page to stop)
#
# Same layout as install_macos_linux.sh (macOS): one folder chosen by the user (default %USERPROFILE%\maprBoy) with
#   maprBoy-app\   the code, micromamba and its environment (Python, GDAL, osmium, Java...), Osmosis, mkgmap, adb,
#                  pyhgtmap, the settings and caches (state\), maprBoy.bat and uninstall.bat
#   maprBoy-data\  Wandrer and Squadrats exports, OSM data, the maps made (only on a first installation)
# A maprBoy shortcut (to maprBoy.bat) is put on the Desktop. Running the installer again updates the code; the environment is made again
# only when environment.yml changed. The code comes from GitHub (the latest release; the main branch while there is none),
# or from the copy of maprBoy holding install_windows.ps1.

function Install-MaprBoy {
    param([string]$Dir = "", [string]$From = "", [switch]$Update)
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"                 # Invoke-WebRequest is very slow with its progress bar
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo = "florentchevallier/maprboy"
    $MicromambaUrl = "https://github.com/mamba-org/micromamba-releases/releases/latest/download/micromamba-win-64"
    $OsmosisVersion = "0.49.2"
    $OsmosisUrl = "https://github.com/openstreetmap/osmosis/releases/download/$OsmosisVersion/osmosis-$OsmosisVersion.zip"
    $MapWriterUrl = "https://repo1.maven.org/maven2/org/mapsforge/mapsforge-map-writer/0.21.0/mapsforge-map-writer-0.21.0-jar-with-dependencies.jar"
    $MkgmapVersion = "r4924"
    $MkgmapUrl = "https://www.mkgmap.org.uk/download/mkgmap-$MkgmapVersion.zip"
    $AdbUrl = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
    $VcRedistUrl = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    $Port = 8765

    function Say($text) { Write-Host ""; Write-Host $text -ForegroundColor Cyan }
    function Fetch($url, $path) {
        Write-Host "  downloading $url"
        try { Invoke-WebRequest -Uri $url -OutFile $path -UseBasicParsing }
        catch { throw "could not download $url ($($_.Exception.Message))" }
    }

    if (-not [Environment]::Is64BitOperatingSystem) { throw "maprBoy needs a 64-bit Windows." }
    if (-not $From -and $PSCommandPath) {                   # run from a copy of maprBoy: install that copy
        $here = Split-Path -Parent $PSCommandPath
        if (Test-Path (Join-Path $here "scripts\gui.py")) { $From = $here }
    }
    Say "maprBoy installer (Windows $([Environment]::OSVersion.Version))"

    # ------------------------------------------------------------------ the folder
    if (-not $Dir) {
        $default = Join-Path $env:USERPROFILE "maprBoy"
        do {                         # the folder is confirmed: a word typed by mistake would become its name
            $Dir = Read-Host "Folder for maprBoy: press Enter for the default, $default, or type another folder"
            if (-not $Dir) { $Dir = $default }
            $Dir = [IO.Path]::GetFullPath([IO.Path]::Combine((Get-Location).Path, $Dir))   # Combine keeps a full path as it is
            $ok = (Read-Host "maprBoy will be installed into $Dir. OK? [Y/n]") -notmatch "^[nN]"
        } until ($ok)
    }
    function Test-Page { try { Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3 | Out-Null; $true } catch { $false } }
    if ($Update) {                                           # started by the page, which is stopping now
        for ($i = 0; $i -lt 60 -and (Test-Page); $i++) { Start-Sleep -Seconds 1 }
    }
    if (Test-Page) {                                         # the page running keeps the code folder in use
        throw "maprBoy is running: stop it with 'Quit maprBoy' in the settings of its page, then run the installer again."
    }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $Dir = (Resolve-Path $Dir).Path
    $App = Join-Path $Dir "maprBoy-app"
    $Data = Join-Path $Dir "maprBoy-data"
    # a program of maprBoy-app still running keeps its folders in use: the adb server, which the page starts and
    # which outlives it (seen in November 2026), so it is stopped (adb starts it again when it is needed)
    $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($App + "\", [StringComparison]::OrdinalIgnoreCase) })
    if ($left.Count) {
        Write-Host ("Stopping " + (($left | ForEach-Object { $_.Name }) -join ", ") + " (started by maprBoy)")
        $left | Stop-Process -Force
        Start-Sleep -Seconds 1
    }
    foreach ($d in "bin", "tools", "state") { New-Item -ItemType Directory -Force -Path (Join-Path $App $d) | Out-Null }
    Write-Host "Installing into $Dir"
    $Tmp = Join-Path ([IO.Path]::GetTempPath()) ("maprboy-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Tmp | Out-Null
    try {
        # -------------------------------------------------------------- the code
        Say "1/5  maprBoy code"
        $code = Join-Path $App "code"
        if ($From) {
            if (-not (Test-Path (Join-Path $From "scripts\gui.py"))) { throw "$From is not a copy of maprBoy" }
            if (Test-Path $code) { Remove-Item -Recurse -Force $code }
            New-Item -ItemType Directory -Path $code | Out-Null
            Get-ChildItem -Force $From | Where-Object { $_.Name -notin ".git", "__pycache__", "Art (do not commit)", "CLAUDE.md", ".DS_Store" } |
                Copy-Item -Destination $code -Recurse -Force
            Set-Content -Path (Join-Path $code "VERSION") -Value "copy" -Encoding Ascii   # version unknown: the page offers the latest release
            Write-Host "from $From"
        } else {
            # the latest release; the main branch when there is none (GitHub answers 404) or no answer
            $tag = ""
            try { $tag = (Invoke-RestMethod -Uri "https://api.github.com/repos/$Repo/releases/latest" -UseBasicParsing -TimeoutSec 20).tag_name } catch { }
            $zip = if ($tag) { "https://github.com/$Repo/archive/refs/tags/$tag.zip" } else { "https://github.com/$Repo/archive/refs/heads/main.zip" }
            Fetch $zip (Join-Path $Tmp "code.zip")
            Expand-Archive (Join-Path $Tmp "code.zip") (Join-Path $Tmp "unzip")
            if (Test-Path $code) { Remove-Item -Recurse -Force $code }
            Move-Item (Get-ChildItem (Join-Path $Tmp "unzip") -Directory | Select-Object -First 1).FullName $code
            $version = if ($tag) { $tag } else { "main" }
            Set-Content -Path (Join-Path $code "VERSION") -Value $version -Encoding Ascii   # compared with the latest release by the page
            Write-Host ("from GitHub (" + $(if ($tag) { $tag } else { "main branch: no release yet" }) + ")")
        }

        # -------------------------------------------------------------- micromamba and the environment
        Say "2/5  Python environment (the first time: several minutes, about 2 GB)"
        $mm = Join-Path $App "bin\micromamba.exe"
        if (-not (Test-Path $mm)) { Fetch $MicromambaUrl $mm }
        & $mm --version | Out-Null
        if ($LASTEXITCODE -eq -1073741515) {               # 0xC0000135: a DLL is missing, the Visual C++ runtime
            Write-Host "micromamba needs the Microsoft Visual C++ Redistributable: installing it (Windows asks to confirm)."
            $vc = Join-Path $Tmp "vc_redist.x64.exe"
            Fetch $VcRedistUrl $vc
            $p = Start-Process -FilePath $vc -ArgumentList "/install", "/quiet", "/norestart" -Verb RunAs -Wait -PassThru
            if ($p.ExitCode -notin 0, 1638, 3010) { throw "the Visual C++ Redistributable could not be installed (code $($p.ExitCode))." }
            & $mm --version | Out-Null
        }
        if ($LASTEXITCODE -ne 0) { throw "micromamba does not start (code $LASTEXITCODE)." }
        $env:MAMBA_ROOT_PREFIX = Join-Path $App "mamba"
        $env:PIP_NO_CACHE_DIR = "1"     # pip (WMC, pyhgtmap) would leave about 130 MB in the user's cache, outside maprBoy
        $envDir = Join-Path $App "env"
        $shaFile = Join-Path $App "state\environment.sha256"
        $want = (Get-FileHash (Join-Path $code "environment.yml") -Algorithm SHA256).Hash
        $have = if (Test-Path $shaFile) { (Get-Content $shaFile -Raw).Trim() } else { "" }
        if (-not (Test-Path $envDir) -or $want -ne $have) {
            if (Test-Path $envDir) { Remove-Item -Recurse -Force $envDir }
            & $mm create -y -p $envDir -f (Join-Path $code "environment.yml")
            if ($LASTEXITCODE -ne 0) { throw "the environment could not be made (see above)." }
            & $mm install -y -p $envDir -c conda-forge "openjdk>=17"
            if ($LASTEXITCODE -ne 0) { throw "Java could not be installed (see above)." }
            Set-Content -Path $shaFile -Value $want
            & $mm clean -y -a -q | Out-Null
        } else { Write-Host "already up to date" }

        # -------------------------------------------------------------- the tools
        Say "3/5  Tools: Osmosis, mkgmap, adb"
        $osmosisDir = Join-Path $App "tools\osmosis-$OsmosisVersion"
        $osmosis = Join-Path $osmosisDir "bin\osmosis.bat"
        if (-not (Test-Path $osmosis)) {
            Fetch $OsmosisUrl (Join-Path $Tmp "osmosis.zip")
            Expand-Archive (Join-Path $Tmp "osmosis.zip") (Join-Path $Tmp "osm")
            $bat = Get-ChildItem (Join-Path $Tmp "osm") -Recurse -Filter "osmosis.bat" | Select-Object -First 1
            if (-not $bat) { throw "osmosis.bat was not found in its zip file" }
            if (Test-Path $osmosisDir) { Remove-Item -Recurse -Force $osmosisDir }
            Move-Item (Split-Path -Parent $bat.Directory.FullName) $osmosisDir
        }
        $plugins = Join-Path $env:APPDATA "Openstreetmap\Osmosis\Plugins"   # where Osmosis 0.49 (and WMC) look
        New-Item -ItemType Directory -Force -Path $plugins | Out-Null
        if (-not (Get-ChildItem $plugins -Filter "*map-writer*.jar" -ErrorAction SilentlyContinue)) {
            Fetch $MapWriterUrl (Join-Path $plugins (Split-Path -Leaf $MapWriterUrl))
        }
        $mkgmap = Join-Path $App "tools\mkgmap-$MkgmapVersion\mkgmap.jar"
        if (-not (Test-Path $mkgmap)) {
            Fetch $MkgmapUrl (Join-Path $Tmp "mkgmap.zip")
            Expand-Archive -Force (Join-Path $Tmp "mkgmap.zip") (Join-Path $App "tools")
            if (-not (Test-Path $mkgmap)) { throw "mkgmap.jar was not found in its zip file" }
        }
        $adb = Join-Path $App "tools\platform-tools\adb.exe"
        if (-not (Test-Path $adb)) {
            Fetch $AdbUrl (Join-Path $Tmp "adb.zip")
            Expand-Archive -Force (Join-Path $Tmp "adb.zip") (Join-Path $App "tools")
            if (-not (Test-Path $adb)) { throw "adb.exe was not found in its zip file" }
        }

        # -------------------------------------------------------------- settings and the contour lines tool
        Say "4/5  Settings and the contour lines tool"
        $env:MAPRBOY_HOME = Join-Path $App "state"
        & $mm run -p $envDir python (Join-Path $code "scripts\install_setup.py") --data $Data --mkgmap $mkgmap --osmosis $osmosis --adb $adb
        if ($LASTEXITCODE -ne 0) { throw "the settings could not be written." }
        Write-Host "Contour lines tool (pyhgtmap):"
        Push-Location $code
        & $mm run -p $envDir python -c "from wahoondrer import contour; contour.setup()"
        $ok = $LASTEXITCODE -eq 0
        Pop-Location
        if (-not $ok) { Write-Host "pyhgtmap could not be installed: maps without contour lines still work (run the installer again later)." }

        # -------------------------------------------------------------- launcher and uninstaller
        Say "5/5  Launcher"
        # micromamba run hides the console window on Windows (seen in November 2026), so maprBoy runs without a
        # window: launch.ps1 starts it in the background, its messages go to state\server.log, and the page's
        # "Quit maprBoy" button stops it.
        $q = { param($s) "'" + ($s -replace "'", "''") + "'" }
        $launch = @"
# maprBoy launcher (made by the maprBoy installer), run hidden by maprBoy.bat: starts maprBoy in the background and opens its page.
`$App = $(& $q $App)
`$url = "http://127.0.0.1:$Port/"
function Test-Up { try { Invoke-WebRequest -Uri `$url -UseBasicParsing -TimeoutSec 2 | Out-Null; `$true } catch { `$false } }
if (Test-Up) { Start-Process `$url; exit }                  # already running: only the page
`$env:MAMBA_ROOT_PREFIX = Join-Path `$App "mamba"
`$env:MAPRBOY_HOME = Join-Path `$App "state"
`$env:PYTHONUTF8 = "1"                                       # the messages go to a file: keep them in UTF-8
`$env:PATH = (Join-Path `$App "tools\osmosis-$OsmosisVersion\bin") + ";" + (Join-Path `$App "tools\platform-tools") + ";" + `$env:PATH
`$log = Join-Path `$App "state\server.log"
`$err = Join-Path `$App "state\server-errors.log"
`$runArgs = @("run", "-p", ('"' + (Join-Path `$App "env") + '"'), "python", ('"' + (Join-Path `$App "code\scripts\gui.py") + '"'), "--port", "$Port")
`$p = Start-Process -FilePath (Join-Path `$App "bin\micromamba.exe") -ArgumentList `$runArgs -WorkingDirectory `$App -WindowStyle Hidden -RedirectStandardOutput `$log -RedirectStandardError `$err -PassThru
for (`$i = 0; `$i -lt 90; `$i++) {                          # the page opens by itself once maprBoy answers
    Start-Sleep -Seconds 1
    if (Test-Up) { exit }
    if (`$p.HasExited) { break }
}
Start-Process notepad.exe ('"' + `$err + '"')              # it did not start: show why
"@
        Set-Content -Path (Join-Path $App "launch.ps1") -Value $launch -Encoding UTF8
        $launcher = @"
@echo off
rem maprBoy launcher (made by the maprBoy installer): starts maprBoy in the background and opens its page; stop it with
rem "Quit maprBoy" in the settings of the page.
start "" powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "$App\launch.ps1"
"@
        Set-Content -Path (Join-Path $App "maprBoy.bat") -Value $launcher -Encoding Ascii
        $uninstall = @"
# maprBoy uninstaller (made by the maprBoy installer), run by uninstall.bat from a copy in the temporary folder.
`$App = '$($App -replace "'", "''")'
`$Data = '$($Data -replace "'", "''")'
`$Dir = '$($Dir -replace "'", "''")'
Write-Host "This removes maprBoy from `$Dir."
`$reply = Read-Host "Delete your data too (maps, OSM data, Wandrer and Squadrats exports in `$Data)? [y/N]"
`$wmc = Join-Path `$env:USERPROFILE "wahooMapsCreatorData"
`$item = Get-Item -Force `$wmc -ErrorAction SilentlyContinue
if (`$item -and `$item.LinkType -and ((`$item.Target | Select-Object -First 1) -eq (Join-Path `$Data "OSM_data"))) {
    cmd /c rmdir "`$wmc"                     # removes the link only, never what it points to
}
Get-Process -ErrorAction SilentlyContinue | Where-Object { `$_.Path -and `$_.Path.StartsWith(`$App, [StringComparison]::OrdinalIgnoreCase) } | Stop-Process -Force
Start-Sleep -Seconds 1                   # maprBoy running in the background: stopped first, its files are deleted below
`$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "maprBoy.bat"
if ((Test-Path `$desk) -and (Select-String -Quiet -Path `$desk -Pattern "maprBoy launcher \(made by")) { Remove-Item -Force `$desk }
`$lnk = Join-Path ([Environment]::GetFolderPath("Desktop")) "maprBoy.lnk"
if ((Test-Path `$lnk) -and (New-Object -ComObject WScript.Shell).CreateShortcut(`$lnk).TargetPath.StartsWith(`$App, [StringComparison]::OrdinalIgnoreCase)) { Remove-Item -Force `$lnk }
Remove-Item -Recurse -Force `$App
if (`$reply -eq "y") { Remove-Item -Recurse -Force `$Data; Write-Host "Data deleted." } else { Write-Host "Data kept in `$Data" }
# files Windows leaves in a folder that was opened (thumbnails, folder settings) are not a reason to keep it
if ((Test-Path `$Dir) -and -not (Get-ChildItem -Force `$Dir | Where-Object { `$_.Name -notin "Thumbs.db", "desktop.ini" })) { Remove-Item -Recurse -Force `$Dir }
`$plugins = Get-ChildItem (Join-Path `$env:APPDATA "Openstreetmap\Osmosis\Plugins") -Filter "*map-writer*.jar" -ErrorAction SilentlyContinue
if (`$plugins) {
    `$reply = Read-Host "Also delete the Osmosis MapWriter plugin in %APPDATA% (another program may use it)? [y/N]"
    if (`$reply -eq "y") { `$plugins | Remove-Item -Force }
}
Write-Host "maprBoy is uninstalled."
"@
        Set-Content -Path (Join-Path $App "uninstall.ps1") -Value $uninstall -Encoding UTF8
        $uninstallBat = @"
@echo off
rem maprBoy uninstaller: runs a copy of uninstall.ps1 from the temporary folder (this folder is deleted).
copy /y "%~dp0uninstall.ps1" "%TEMP%\maprboy-uninstall.ps1" >NUL
powershell -NoProfile -ExecutionPolicy Bypass -File "%TEMP%\maprboy-uninstall.ps1"
pause
"@
        Set-Content -Path (Join-Path $App "uninstall.bat") -Value $uninstallBat -Encoding Ascii
        # on the Desktop: a shortcut to maprBoy.bat with the maprBoy icon (a .bat file cannot have its own icon)
        $desktop = [Environment]::GetFolderPath("Desktop")
        $old = Join-Path $desktop "maprBoy.bat"              # what earlier versions put there
        if ((Test-Path $old) -and (Select-String -Quiet -Path $old -Pattern "maprBoy launcher \(made by")) { Remove-Item -Force $old }
        $sc = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $desktop "maprBoy.lnk"))
        $sc.TargetPath = Join-Path $App "maprBoy.bat"
        $sc.WorkingDirectory = $App
        $sc.IconLocation = (Join-Path $App "code\wahoondrer\gui\static\maprboy.ico") + ",0"
        $sc.WindowStyle = 7                                  # minimized: the short-lived console window does not flash
        $sc.Description = "Start maprBoy (the page opens in your browser)"
        $sc.Save()

        Say "Done."
        if ($Update) { Write-Host "maprBoy is up to date."; return }
        Write-Host "Start maprBoy: double-click maprBoy on the Desktop (it runs without a window; the page opens in your"
        Write-Host "browser). Stop it with 'Quit maprBoy' in the settings of the page. Uninstall: double-click $App\uninstall.bat"
    } finally {
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    }
}

try { Install-MaprBoy @args }
catch {
    Write-Host ""; Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    if ($PSCommandPath) { exit 1 }           # run as a file (the page's update): tell it; never close a window pasted into
}
