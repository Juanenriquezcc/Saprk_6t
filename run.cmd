@echo off
rem Entry point of PySpark Lab Analyzer.
rem -ExecutionPolicy Bypass applies to THIS process only: the Windows policy is not changed.
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
set "RC=%ERRORLEVEL%"
rem Opened with a double click and failed: keep the window open to read the message.
if not "%RC%"=="0" if "%~1"=="" pause
exit /b %RC%
