@echo off
rem Start the loom orchestrator + a frontend together (see loom-dev.ps1 for the options).
rem   loom-dev                           the desktop window on v2 (default)
rem   loom-dev -Mode browser             orchestrator + v2 in the browser
rem   loom-dev -Frontend v1 -Mode browser  the frozen reference UI
where pwsh >nul 2>&1
if %errorlevel%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0loom-dev.ps1" %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0loom-dev.ps1" %*
)
