@echo off
title MarketHub
cd /d "%~dp0"

REM Start the MarketHub server (UI at http://127.0.0.1:7070/ui/)
REM
REM Bootstrap note: this machine's shared Python ships a python314._pth
REM (isolated mode) which never adds the current directory to sys.path,
REM so plain "python -m app.server" cannot resolve the app package here.
REM The -c bootstrap below inserts the working directory explicitly and
REM works on every interpreter, including standard CI Pythons.

python -c "import sys; sys.path.insert(0, ''); from app.server import main; main()"

echo.
echo Server exited.
pause
