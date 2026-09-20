@echo off
rem Start the loom orchestrator + a frontend together (see loom-dev.ps1 for the options).
rem   loom-dev                 orchestrator + v1 in the browser
rem   loom-dev -Frontend v2    the new frontend
rem   loom-dev -Mode tauri     the desktop window
where pwsh >nul 2>&1
if %errorlevel%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0loom-dev.ps1" %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0loom-dev.ps1" %*
)
