param(
    [string]$PythonUrl = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip",
    [string]$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    [string]$Output = "dist\StremDesk"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot ".")).Path
$outputPath = Join-Path $root $Output
$tempPath = Join-Path $root ".portable-build"
$pythonZip = Join-Path $tempPath "python.zip"
$ffmpegZip = Join-Path $tempPath "ffmpeg.zip"

if (-not (Test-Path -LiteralPath $root -PathType Container)) {
    throw "Project root not found: $root"
}
New-Item -ItemType Directory -Path $tempPath -Force | Out-Null
New-Item -ItemType Directory -Path $outputPath -Force | Out-Null

Invoke-WebRequest -Uri $PythonUrl -OutFile $pythonZip
Invoke-WebRequest -Uri $FfmpegUrl -OutFile $ffmpegZip

$pythonExtract = Join-Path $tempPath "python"
$ffmpegExtract = Join-Path $tempPath "ffmpeg"
Remove-Item -LiteralPath $pythonExtract, $ffmpegExtract -Recurse -Force -ErrorAction SilentlyContinue
Expand-Archive -LiteralPath $pythonZip -DestinationPath $pythonExtract -Force
Expand-Archive -LiteralPath $ffmpegZip -DestinationPath $ffmpegExtract -Force

$pythonSource = Get-ChildItem -LiteralPath $pythonExtract -Filter python.exe -Recurse | Select-Object -First 1
$ffmpegSource = Get-ChildItem -LiteralPath $ffmpegExtract -Filter ffmpeg.exe -Recurse | Select-Object -First 1
$licenseSource = Get-ChildItem -LiteralPath $ffmpegExtract -Filter LICENSE* -Recurse | Select-Object -First 1
if ($null -eq $pythonSource -or $null -eq $ffmpegSource) {
    throw "Python or FFmpeg executable was not found in the downloaded archives"
}

$runtimePython = Join-Path $outputPath "runtime\python"
$runtimeFfmpeg = Join-Path $outputPath "runtime\ffmpeg"
Remove-Item -LiteralPath $runtimePython, $runtimeFfmpeg -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path $runtimePython -Force | Out-Null
New-Item -ItemType Directory -Path $runtimeFfmpeg -Force | Out-Null
Get-ChildItem -LiteralPath $pythonSource.Directory.FullName -Force | Copy-Item -Destination $runtimePython -Recurse -Force
Copy-Item -LiteralPath $ffmpegSource.FullName -Destination $runtimeFfmpeg
if ($null -ne $licenseSource) {
    Copy-Item -LiteralPath $licenseSource.FullName -Destination (Join-Path $runtimeFfmpeg "LICENSE.txt")
}

$pthFile = Get-ChildItem -LiteralPath $runtimePython -Filter "*._pth" | Select-Object -First 1
if ($null -ne $pthFile) {
    $pthLines = @(Get-Content -LiteralPath $pthFile.FullName)
    $pthLines = @($pthLines | Where-Object { $_ -ne "..\.." -and $_ -ne "app" })
    $pthLines += "..\.."
    Set-Content -LiteralPath $pthFile.FullName -Value $pthLines -Encoding ASCII
}

Copy-Item -LiteralPath (Join-Path $root "stremdesk.py") -Destination $outputPath -Force
Copy-Item -LiteralPath (Join-Path $root "config.json") -Destination $outputPath -Force
Copy-Item -LiteralPath (Join-Path $root "desktop_streamer") -Destination $outputPath -Recurse -Force

Copy-Item -LiteralPath (Join-Path $root "Start-StremDesk.bat") -Destination $outputPath -Force

$archive = Join-Path $root "dist\StremDesk-portable.zip"
Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Compress-Archive -Path (Join-Path $outputPath "*") -DestinationPath $archive -Force
Remove-Item -LiteralPath $tempPath -Recurse -Force
Write-Host "Portable package: $outputPath"
Write-Host "Archive: $archive"
