@echo off
cd /d D:\enlight-lab-platform
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\fix-dashboards.ps1
pause
