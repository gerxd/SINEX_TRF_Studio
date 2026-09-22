@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if not "%CD:~239,1%"=="" goto long_path

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
set "CURL=%SystemRoot%\System32\curl.exe"
set "TAR=%SystemRoot%\System32\tar.exe"
if not exist ".uv" mkdir ".uv"
pushd ".uv"
if exist "%CURL%" goto download_curl

:download_ps
powershell -NoProfile -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri '%UV_MIRROR%/%UV_FILE%' -OutFile 'uv.zip' } catch { Invoke-WebRequest -UseBasicParsing -Uri '%UV_GITHUB%/%UV_FILE%' -OutFile 'uv.zip' }"
goto unpack

:download_curl
"%CURL%" -fL --connect-timeout 15 -o "uv.zip" "%UV_MIRROR%/%UV_FILE%"
if not errorlevel 1 goto unpack
echo The first address did not answer. Trying the second one.
"%CURL%" -fL --connect-timeout 15 -o "uv.zip" "%UV_GITHUB%/%UV_FILE%"

:unpack
if errorlevel 1 goto failed_pop
if not exist "%TAR%" goto unpack_ps
"%TAR%" -xf "uv.zip"
if exist "uv.exe" goto unpacked

:unpack_ps
powershell -NoProfile -Command "Expand-Archive -LiteralPath 'uv.zip' -DestinationPath '.' -Force"

:unpacked
del "uv.zip" 2>nul
popd
if not exist "%UV%" goto failed

:launch
"%UV%" run --no-project --python 3.12 --python-preference only-managed main.py
if errorlevel 2 goto stop
if errorlevel 1 goto failed
exit /b 0

:stop
pause
exit /b 1

:failed_pop
popd
goto failed

:long_path
echo.
echo The folder path is too long. Move the folder closer to the drive root, then run this file again.
pause
exit /b 1

:failed
echo.
echo The setup did not finish. Run this file again. If it keeps failing, check the internet connection.
pause
exit /b 1
