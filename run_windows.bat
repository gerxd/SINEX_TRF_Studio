@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "UV=%CD%\.uv\uv.exe"
if exist "%UV%" goto launch

where uv >nul 2>nul
if not errorlevel 1 (
    set "UV=uv"
    goto launch
)

echo Setting up the installer. This runs once.
set "UV_VERSION=0.12.17"
set "UV_ARCH=x86_64"
if /i "%PROCESSOR_ARCHITECTURE%"=="ARM64" set "UV_ARCH=aarch64"
if /i "%PROCESSOR_ARCHITEW6432%"=="ARM64" set "UV_ARCH=aarch64"
set "UV_FILE=uv-%UV_ARCH%-pc-windows-msvc.zip"
set "UV_MIRROR=https://releases.astral.sh/github/uv/releases/download/%UV_VERSION%"
set "UV_GITHUB=https://github.com/astral-sh/uv/releases/latest/download"
set "UV_ZIP=%TEMP%\%UV_FILE%"
if not exist "%CD%\.uv" mkdir "%CD%\.uv"
where curl >nul 2>nul
if errorlevel 1 goto download_ps
curl -fL --connect-timeout 15 -o "%UV_ZIP%" "%UV_MIRROR%/%UV_FILE%"
if not errorlevel 1 goto unpack
echo The first address did not answer. Trying the second one.
curl -fL --connect-timeout 15 -o "%UV_ZIP%" "%UV_GITHUB%/%UV_FILE%"
goto unpack

:download_ps
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%UV_MIRROR%/%UV_FILE%' -OutFile '%UV_ZIP%' } catch { Invoke-WebRequest -UseBasicParsing -Uri '%UV_GITHUB%/%UV_FILE%' -OutFile '%UV_ZIP%' }"

:unpack
if errorlevel 1 goto failed
where tar >nul 2>nul
if errorlevel 1 goto unpack_ps
tar -xf "%UV_ZIP%" -C "%CD%\.uv"
goto unpacked

:unpack_ps
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%UV_ZIP%' -DestinationPath '%CD%\.uv' -Force"

:unpacked
if errorlevel 1 goto failed
del "%UV_ZIP%" 2>nul
if not exist "%UV%" goto failed

:launch
"%UV%" run --no-project --python 3.12 --python-preference only-managed main.py
if errorlevel 1 goto failed
exit /b 0

:failed
echo.
echo The setup did not finish. Check the internet connection or the proxy settings, then run this file again.
pause
exit /b 1
