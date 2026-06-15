@echo off

cd /d "%~dp0"

pip install fpdf2 --quiet 2>nul

python scripts\generate-full-demo-script-pdf.py

if exist docs\FULL-DEMO-SCRIPT.pdf (

  echo.

  echo PDF ready: docs\FULL-DEMO-SCRIPT.pdf

  start "" "docs\FULL-DEMO-SCRIPT.pdf"

) else (

  echo PDF generation failed.

  exit /b 1

)

