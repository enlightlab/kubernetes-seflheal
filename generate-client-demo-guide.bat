@echo off



cd /d "%~dp0"



pip install fpdf2 --quiet 2>nul



python scripts\generate-client-demo-guide-pdf.py



if exist docs\CLIENT-DEMO-EXPLAINED.pdf (



  echo.



  echo PDF ready: docs\CLIENT-DEMO-EXPLAINED.pdf



  start "" "docs\CLIENT-DEMO-EXPLAINED.pdf"



) else (



  echo PDF generation failed.



  exit /b 1



)


