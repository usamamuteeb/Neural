@echo off
rem Starts (or restarts) Neural Desktop, the floating companion. First run installs it.
cd /d "%~dp0"
where node >nul 2>nul
if errorlevel 1 (
  echo Node.js is needed to run Neural Desktop. Install the LTS version from https://nodejs.org and run this again.
  pause
  exit /b 1
)
if not exist "node_modules\electron\dist\electron.exe" (
  echo Installing Neural Desktop - one time, takes a minute...
  call npm install
  if errorlevel 1 (
    echo.
    echo npm install failed - see the messages above.
    pause
    exit /b 1
  )
)
rem A stuck or invisible earlier copy would block this one from opening
rem (only one Neural Desktop runs at a time), so close any copy of THIS
rem install first. Other Electron apps are left alone.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$exe = (Resolve-Path 'node_modules\electron\dist\electron.exe').Path; Get-Process electron -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $exe } | Stop-Process -Force -ErrorAction SilentlyContinue"
timeout /t 1 /nobreak >nul
start "" "node_modules\electron\dist\electron.exe" . %*
