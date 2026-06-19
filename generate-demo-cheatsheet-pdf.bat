@echo off
cd /d "%~dp0"
python scripts\generate-demo-cheatsheet-pdf.py
if exist docs\MANAGER-DEMO-CHEATSHEET.pdf (
  echo.
  echo PDF ready: docs\MANAGER-DEMO-CHEATSHEET.pdf
  start "" "docs\MANAGER-DEMO-CHEATSHEET.pdf"
) else (
  echo PDF generation failed.
  exit /b 1
)
