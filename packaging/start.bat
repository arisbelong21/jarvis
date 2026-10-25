@echo off
title JARVIS
cd /d "%~dp0"
if not exist ".venv313\Scripts\python.exe" (
  echo Belum siap. Jalankan setup.bat dulu.
  pause
  exit /b 1
)
call run_aris_console.bat
