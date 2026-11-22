@echo off
rem Runs all MetroidFlasher tests. No phone needed: a fake fastboot is used, the real one is never started.
rem   run_tests.bat          everything (about 2 minutes)
rem   run_tests.bat --fast   only the quick unit tests
cd /d "%~dp0"
set "PY=C:\Python315\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -I run_tests.py %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" pause
exit /b %RC%
