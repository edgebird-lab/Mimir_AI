@echo off
REM ==========================================================================
REM  Mimir installer launcher (Windows).  Double-click this file.
REM  Copyright 2026 Olbricht Digital - Apache-2.0
REM
REM  This is an UNSIGNED open-source installer. Windows SmartScreen / your
REM  antivirus may warn you ("Windows protected your PC"). That is expected -
REM  click "More info" -> "Run anyway". The source is next to this file.
REM ==========================================================================
echo Starting the Mimir installer...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
echo.
pause
