# maprBoy installer for Windows (10 and 11, 64-bit).
#
# In PowerShell (Start menu > type PowerShell), paste:
#   powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/florentchevallier/maprboy/main/install.ps1 | iex"
# or, from a copy of the repository:
#   powershell -ExecutionPolicy Bypass -File install.ps1
#
# Same layout as install.sh (macOS): one folder chosen by the user (default %USERPROFILE%\maprBoy) with
#   maprBoy-app\   the code, micromamba and its environment (Python, GDAL, osmium, Java...), Osmosis, mkgmap, adb,
#                  pyhgtmap, the settings and caches (state\), maprBoy.bat and uninstall.bat
#   maprBoy-data\  Wandrer and Squadrats exports, OSM data, the maps made (only on a first installation)
# maprBoy.bat is copied to the Desktop. Running the installer again updates the code; the environment is made again
# only when environment.yml changed. The code comes from GitHub, or from the copy of maprBoy holding install.ps1.

function Install-MaprBoy {
    param([string]$Dir = "", [string]$From = "")
    $ErrorActionPreference = "Stop"
    $ProgressPreference = "SilentlyContinue"                 # Invoke-WebRequest is very slow with its progress bar
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $RepoZip = "https://github.com/florentchevallier/maprboy/archive/refs/heads/main.zip"
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
        $Dir = Read-Host "Where should maprBoy go? [$default]"
        if (-not $Dir) { $Dir = $default }
    }
    New-Item -ItemType Directory -Force -Path $Dir | Out-Null
    $Dir = (Resolve-Path $Dir).Path
    $App = Join-Path $Dir "maprBoy-app"
    $Data = Join-Path $Dir "maprBoy-data"
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
            Write-Host "from $From"
        } else {
            Fetch $RepoZip (Join-Path $Tmp "code.zip")
            Expand-Archive (Join-Path $Tmp "code.zip") (Join-Path $Tmp "unzip")
            if (Test-Path $code) { Remove-Item -Recurse -Force $code }
            Move-Item (Get-ChildItem (Join-Path $Tmp "unzip") -Directory | Select-Object -First 1).FullName $code
            Write-Host "from GitHub (main)"
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
        $launcher = @"
@echo off
rem maprBoy launcher (made by install.ps1): starts the page; close this window to stop it.
set "APP=$App"
set "MAMBA_ROOT_PREFIX=%APP%\mamba"
set "MAPRBOY_HOME=%APP%\state"
set "PATH=%APP%\tools\osmosis-$OsmosisVersion\bin;%APP%\tools\platform-tools;%PATH%"
curl -s -o NUL http://127.0.0.1:$Port/ && (echo maprBoy is already running: opening the page. & start "" http://127.0.0.1:$Port/ & exit /b 0)
cd /d "%APP%\code"
"%APP%\bin\micromamba.exe" run -p "%APP%\env" python scripts\gui.py --port $Port
pause
"@
        Set-Content -Path (Join-Path $App "maprBoy.bat") -Value $launcher -Encoding Ascii
        $uninstall = @"
# maprBoy uninstaller (made by install.ps1), run by uninstall.bat from a copy in the temporary folder.
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
`$desk = Join-Path ([Environment]::GetFolderPath("Desktop")) "maprBoy.bat"
if ((Test-Path `$desk) -and (Select-String -Quiet -Path `$desk -Pattern "maprBoy launcher \(made by install.ps1\)")) { Remove-Item -Force `$desk }
Remove-Item -Recurse -Force `$App
if (`$reply -eq "y") { Remove-Item -Recurse -Force `$Data; Write-Host "Data deleted." } else { Write-Host "Data kept in `$Data" }
if ((Test-Path `$Dir) -and -not (Get-ChildItem -Force `$Dir)) { Remove-Item -Force `$Dir }
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
        $desktop = [Environment]::GetFolderPath("Desktop")
        Copy-Item (Join-Path $App "maprBoy.bat") (Join-Path $desktop "maprBoy.bat") -Force

        Say "Done."
        Write-Host "Start maprBoy: double-click maprBoy.bat on the Desktop (the page opens in your browser; keep its window"
        Write-Host "open while you use it). Uninstall: double-click $App\uninstall.bat"
    } finally {
        Remove-Item -Recurse -Force $Tmp -ErrorAction SilentlyContinue
    }
}

try { Install-MaprBoy @args }
catch { Write-Host ""; Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red }
