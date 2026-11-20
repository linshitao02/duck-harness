@echo off
cd /d "%~dp0backend"
set "PY=%USERPROFILE%\AI\software\anaconda\envs\agent\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" run_web.py
