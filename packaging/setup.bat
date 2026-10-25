@echo off
setlocal EnableDelayedExpansion
title JARVIS - Persiapan (sekali saja)
cd /d "%~dp0"
echo ============================================
echo   JARVIS - persiapan pertama
echo   (hanya sekali; butuh internet)
echo ============================================
echo.

if exist ".venv313\Scripts\python.exe" (
  echo [i] Lingkungan sudah ada - melewati pemasangan Python.
  goto :deps
)

set "PYEXE="
echo [1/4] Mencari Python 3.13...

py -3.13 -c "import sys" >nul 2>&1
if %errorlevel%==0 (
  set "PYEXE=py -3.13"
  echo     ditemukan lewat launcher: py -3.13
  goto :havepy
)

if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
  echo     ditemukan di folder pengguna.
  goto :havepy
)

set "PV="
for /f "delims=" %%v in ('python -c "import sys;print(str(sys.version_info[0])+chr(46)+str(sys.version_info[1]))" 2^>nul') do set "PV=%%v"
if "!PV!"=="3.13" (
  set "PYEXE=python"
  echo     ditemukan di PATH: python !PV!
  goto :havepy
)

echo     Python 3.13 belum ada - dipasang otomatis.
echo.
echo [2/4] Memasang Python 3.13...

where winget >nul 2>&1
if %errorlevel%==0 (
  echo     mencoba winget...
  winget install --id Python.Python.3.13 -e --silent --accept-package-agreements --accept-source-agreements
  if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
    set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
    echo     winget selesai.
    goto :havepy
  )
  echo     winget tidak berhasil - beralih ke unduhan resmi python.org.
)

set "PYVER="
for %%V in (3.13.9 3.13.8 3.13.7 3.13.6 3.13.5 3.13.4 3.13.3) do (
  if not defined PYVER (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/%%V/python-%%V-amd64.exe' -OutFile '%TEMP%\jarvis-python.exe' -UseBasicParsing -ErrorAction Stop; exit 0 } catch { exit 1 }" >nul 2>&1
    if !errorlevel!==0 set "PYVER=%%V"
  )
)

if not defined PYVER (
  echo.
  echo   GAGAL: Python tidak bisa dipasang otomatis.
  echo   Pasang manual dari https://www.python.org/downloads/
  echo   Saat memasang, centang "Add python.exe to PATH",
  echo   lalu jalankan setup.bat lagi.
  pause
  exit /b 1
)

echo     terunduh Python !PYVER!. Memasang (1-2 menit)...
start /wait "" "%TEMP%\jarvis-python.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
del "%TEMP%\jarvis-python.exe" >nul 2>&1

if exist "%LocalAppData%\Programs\Python\Python313\python.exe" (
  set "PYEXE=%LocalAppData%\Programs\Python\Python313\python.exe"
) else (
  echo   GAGAL memasang Python. Pasang manual lalu jalankan lagi.
  pause
  exit /b 1
)

:havepy
echo.
echo [3/4] Menyiapkan lingkungan JARVIS...
%PYEXE% -m venv .venv313
if not exist ".venv313\Scripts\python.exe" (
  echo   GAGAL membuat lingkungan Python.
  pause
  exit /b 1
)

:deps
echo [4/4] Memasang pustaka (5-15 menit, butuh internet)...
".venv313\Scripts\python.exe" -m pip install --upgrade pip --disable-pip-version-check
".venv313\Scripts\python.exe" -m pip install -r requirements.txt --disable-pip-version-check
if errorlevel 1 (
  echo.
  echo   Sebagian pustaka gagal dipasang. Periksa koneksi internet,
  echo   lalu jalankan setup.bat sekali lagi.
  pause
  exit /b 1
)

echo.
echo Memasang browser untuk kontrol web (sekali saja)...
".venv313\Scripts\python.exe" -m playwright install chromium

echo.
echo ============================================
echo   Selesai. Jalankan start.bat untuk membuka JARVIS.
echo ============================================
pause
