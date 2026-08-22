param(
    [switch]$Clean
)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $projectRoot ".venv"
$pythonExe = Join-Path (Join-Path $venvDir "Scripts") "python.exe"

if (-not (Test-Path $pythonExe)) {
    Write-Error "Python executable not found at $pythonExe"
    exit 1
}

$resourcesDir = [System.IO.Path]::Combine($venvDir, "Lib", "site-packages", "PyQt5", "Qt5", "resources")
$translationsDir = [System.IO.Path]::Combine($venvDir, "Lib", "site-packages", "PyQt5", "Qt5", "translations")

if (-not (Test-Path $resourcesDir)) {
    Write-Error "Qt resources directory not found at $resourcesDir"
    exit 1
}

if (-not (Test-Path $translationsDir)) {
    Write-Error "Qt translations directory not found at $translationsDir"
    exit 1
}

$resourcesArg = $resourcesDir -replace '\\','/'
$translationsArg = $translationsDir -replace '\\','/'

if ($Clean) {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "main.build")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "main.dist")
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "main.onefile-build")
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "main.exe")
    Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $projectRoot "SINEX Studio.exe")
}

$initFile = Join-Path $projectRoot "sinex_parser\__init__.py"
$versionMatch = Select-String -Path $initFile -Pattern "__version__\s*=\s*'(\d+\.\d+\.\d+)'" | Select-Object -First 1
if (-not $versionMatch) {
    Write-Error "Could not read __version__ from $initFile"
    exit 1
}
$appVersion = $versionMatch.Matches[0].Groups[1].Value
$fileVersion = "$appVersion.0"

$arguments = @(
    "-m", "nuitka",
    "--standalone",
    "--onefile",
    "--enable-plugin=pyqt5",
    "--mingw64",
    "--assume-yes-for-downloads",
    "--windows-console-mode=disable",
    "--windows-icon-from-ico=sinex_parser/ui/icon.ico",
    "--include-data-file=logo2.jpg=logo2.jpg",
    "--include-data-file=sinex_parser/ui/icon.ico=sinex_parser/ui/icon.ico",
    "--include-data-file=sinex_parser/icon.png=sinex_parser/icon.png",
    "--include-data-file=sinex_parser/ui/icon.png=sinex_parser/ui/icon.png",
    "--include-data-dir=sinex_parser/ui/map_assets=sinex_parser/ui/map_assets",
    "--include-data-dir=$resourcesArg=PyQt5/Qt5/resources",
    "--include-data-dir=$translationsArg=PyQt5/Qt5/translations",
    '--output-filename="SINEX TRF Studio.exe"',
    "--windows-product-name=SINEX TRF Studio",
    "--windows-product-version=$appVersion",
    "--windows-file-version=$fileVersion",
    "--windows-company-name=International Hellenic University",
    "--windows-file-description=SINEX TRF Studio - Gerasimos M. Dossas <gerasimos.dossas@gmail.com>",
    "main.py"
)

Write-Host "Running Nuitka build..." -ForegroundColor Cyan
Write-Host "$pythonExe $($arguments -join ' ')" -ForegroundColor DarkGray

& $pythonExe @arguments

if ($LASTEXITCODE -ne 0) {
    Write-Error "Nuitka build failed with exit code $LASTEXITCODE"
    exit $LASTEXITCODE
}

Write-Host "Nuitka build completed successfully." -ForegroundColor Green
