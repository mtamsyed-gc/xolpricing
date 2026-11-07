@echo off
rem XoL pricing tool: browser interface (0.8.0). Starts the local server and opens the page in the default browser.
rem   tools\xol_ui.bat            port 8765
rem   tools\xol_ui.bat 9000       another port
rem Keep this window open while you use the page; close it (or Ctrl+C) to stop the server.
setlocal
set "PYX=py -3.12"
py -3.12 -c "" >nul 2>&1 || set "PYX=py"
set "PORT=%~1"
if "%PORT%"=="" set "PORT=8765"
%PYX% "%~dp0ui\serve.py" --port %PORT%
