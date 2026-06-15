@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start-demo-control.ps1
start "" "http://localhost:30900"
