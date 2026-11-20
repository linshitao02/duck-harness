@echo off
cd /d "%~dp0desktop"
set "PY=%USERPROFILE%\AI\software\anaconda\envs\agent\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" launch.py
