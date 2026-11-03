@echo off
cd /d "%~dp0"
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
if not exist .env copy .env.ejemplo .env
pause
