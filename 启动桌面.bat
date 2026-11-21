@echo off
cd /d "%~dp0desktop"
set "PY=%USERPROFILE%\AI\software\anaconda\envs\agent\pythonw.exe"
if not exist "%PY%" set "PY=pythonw"
start "" "%PY%" "%~dp0desktop\launch.py"
