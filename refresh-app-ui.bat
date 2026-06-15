@echo off
cd /d D:\enlight-lab-platform
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\refresh-app-ui.ps1
pause
