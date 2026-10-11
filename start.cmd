@echo off
rem Double click = start.ps1 (Windows, needs Docker Desktop).
rem Arguments are passed through: start.cmd stop / logs / status / rebuild
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
if errorlevel 1 pause
