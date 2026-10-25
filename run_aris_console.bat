@echo off
title ARIS - Console Log (JANGAN TUTUP JENDELA INI)
cd /d "%~dp0"
echo [ARIS] Starting... semua log muncul di jendela hitam ini.
echo.

REM Python 3.13 lewat venv .venv313 — versi inilah yang stabil untuk aplikasi
REM ini. Python 3.14 (global) pernah menghasilkan crash native (access
REM violation) di thread provider. Kalau venv belum ada, jatuh ke python
REM global supaya Jarvis tetap bisa jalan.
set "PY=python"
if exist ".venv313\Scripts\python.exe" (
    set "PY=.venv313\Scripts\python.exe"
) else (
    echo [ARIS] PERINGATAN: .venv313 tidak ada - memakai python global.
    echo [ARIS] Untuk membuatnya: python -m venv .venv313 ^&^& .venv313\Scripts\pip install -r requirements.txt
)
echo [ARIS] Python: %PY%
echo.

%PY% main.py
echo.
echo [ARIS berhenti - exit code %errorlevel%]
echo Jendela sengaja dibiarkan terbuka supaya log/error bisa dibaca.
pause
