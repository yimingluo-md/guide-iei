@echo off
setlocal
title GUIDE-IEI

rem GUIDE-IEI one-click launcher (Windows). Double-click to start.
rem Keep the window open while using GUIDE-IEI; closing it stops the
rem workbench.
echo GUIDE-IEI launcher is starting...
echo.

set "IEI_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%IEI_POWERSHELL%" (
    echo ERROR: Windows PowerShell was not found at:
    echo   %IEI_POWERSHELL%
    echo Ask your administrator to enable Windows PowerShell, then try again.
    echo.
    pause
    exit /b 1
)

"%IEI_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0GUIDE-IEI.ps1" %*
set "IEI_EXIT=%ERRORLEVEL%"
if not "%IEI_EXIT%"=="0" (
    echo.
    echo GUIDE-IEI did not start. Read the explanation above.
    echo The diagnostic-log location is also printed near the top.
    echo.
    pause
)
exit /b %IEI_EXIT%
