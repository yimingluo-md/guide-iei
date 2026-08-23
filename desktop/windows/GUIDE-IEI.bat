@echo off
rem GUIDE-IEI one-click launcher (Windows). Double-click to start.
rem Keep the window open while using GUIDE-IEI; closing it stops the
rem workbench.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0GUIDE-IEI.ps1" %*
if errorlevel 1 pause
