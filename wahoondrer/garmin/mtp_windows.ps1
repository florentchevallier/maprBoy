# Copy maps to a Garmin watch plugged in by USB on Windows (MTP), through File Explorer's own objects (Shell.Application).
# Called by wahoondrer/garmin/device.py. What was seen on Windows 10 with a Forerunner 955 (November 2026):
# - the watch is an item of "This PC" that is not a file system, with "vid_091e" (Garmin's USB id) in its path;
# - Internal Storage > GARMIN; the names show without .img, System.FileName gives the real name, Size is 0 but
#   System.Size is the real size;
# - CopyHere ignores "yes to all": a map of the same name brings Windows' "Replace" window;
# - deleting asked for a confirmation and then failed or did nothing: nothing is deleted here.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File mtp_windows.ps1 list
#   powershell -NoProfile -ExecutionPolicy Bypass -File mtp_windows.ps1 copy FILE.img [FILE.img ...]
# list: one JSON line {"devices": [names], "maps": [file names in GARMIN of the first watch]}
# copy: one line per map; exit code 1 with "Error: ..." when something fails.
param([string]$Action = "list", [Parameter(ValueFromRemainingArguments = $true)][string[]]$Files = @())
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Get-Watches {
    $sh = New-Object -ComObject Shell.Application
    @($sh.Namespace(17).Items() | Where-Object { -not $_.IsFileSystem -and $_.Path -like "*vid_091e*" })
}

function Get-GarminFolder($dev) {
    foreach ($storage in @($dev.GetFolder.Items())) {
        $g = @($storage.GetFolder.Items() | Where-Object { $_.Name -eq "GARMIN" }) | Select-Object -First 1
        if ($g) { return $g.GetFolder }
    }
    return $null
}

function Find-Map($folder, $name) {
    @($folder.Items() | Where-Object { $_.ExtendedProperty("System.FileName") -eq $name }) | Select-Object -First 1
}

try {
    $watches = @(Get-Watches)        # @(): a function's single item comes back alone, and .Count is empty on it
    if ($Action -eq "list") {
        $maps = @()
        if ($watches.Count) {
            $g = Get-GarminFolder $watches[0]
            if ($g) { $maps = @($g.Items() | ForEach-Object { $_.ExtendedProperty("System.FileName") }) }
        }
        ConvertTo-Json -Compress @{ devices = @($watches | ForEach-Object { $_.Name }); maps = $maps }
        exit 0
    }
    if ($Action -ne "copy") { throw "unknown action $Action" }
    if (-not $watches.Count) { throw "no Garmin watch found: is it plugged in, and Garmin Express closed?" }
    $folder = Get-GarminFolder $watches[0]
    if (-not $folder) { throw "no GARMIN folder on $($watches[0].Name)" }
    $copied = 0
    $i = 0
    foreach ($f in $Files) {
        $i++
        $name = Split-Path -Leaf $f
        $size = (Get-Item -LiteralPath $f).Length
        Write-Output "STEP|$i|$($Files.Count)|$name"
        $there = Find-Map $folder $name
        if ($there -and $there.ExtendedProperty("System.Size") -eq $size) {
            Write-Output "  ${name}: already on the device"
            continue
        }
        if ($there) { Write-Output "  ${name}: an older map of this name is on the watch: Windows asks whether to replace it" }
        $folder.CopyHere($f)
        # the copy runs in the background: wait until the watch holds the whole file (about 1 MB/s is slow for MTP)
        $deadline = (Get-Date).AddSeconds([Math]::Max(120, $size / 1MB * 10))
        do {
            Start-Sleep -Seconds 1
            $item = Find-Map $folder $name
            $done = $item -and $item.ExtendedProperty("System.Size") -eq $size
        } until ($done -or (Get-Date) -gt $deadline)
        if (-not $done) { throw "$name was not copied in time (was Windows' Replace window answered with Skip?)" }
        $copied++
        Write-Output ("  copied {0} ({1:N1} MB)" -f $name, ($size / 1MB))
    }
    Write-Output "$copied map(s) sent to the GARMIN folder of $($watches[0].Name)."
} catch {
    Write-Output "Error: $($_.Exception.Message)"
    exit 1
}
