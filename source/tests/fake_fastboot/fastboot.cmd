@echo off
rem Fake fastboot for MetroidFlasher tests. Never talks to a phone. See fake_fastboot.py.
if not defined FAKE_FB_PYTHON set "FAKE_FB_PYTHON=C:\Python315\python.exe"
"%FAKE_FB_PYTHON%" -I "%~dp0fake_fastboot.py" %*
