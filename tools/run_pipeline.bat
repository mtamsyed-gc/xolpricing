@echo off
rem XoL pricing tool: run the whole pipeline with the Alteryx engine (no Designer window needed).
rem   run_pipeline.bat                 synthetic data, line adapter (01a)
rem   run_pipeline.bat live 01b        live data, claimant adapter
rem   run_pipeline.bat synth 01a 04    start at stage 04 (00-03 already run)
rem Stops at the first workflow that ends with an error (a STOP check is an error).
rem Log: data\output\<variant>\engine_run.log
setlocal
set "ENGINE=%LOCALAPPDATA%\Alteryx\bin\AlteryxEngineCmd.exe"
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VAR=%~1"
if "%VAR%"=="" set "VAR=synth"
set "AD=%~2"
if "%AD%"=="" set "AD=01a"
set "FROM=%~3"
if "%FROM%"=="" set "FROM=00"
if /i "%VAR%"=="live" (set "WF=%ROOT%\alteryx\workflows") else (set "WF=%ROOT%\alteryx\workflows\synth")
if /i "%AD%"=="01a" set "ADW=01a_adapter_line"
if /i "%AD%"=="01b" set "ADW=01b_adapter_claimant"
if /i "%AD%"=="01c" set "ADW=01c_adapter_ete"
if not defined ADW (echo Unknown adapter %AD%: use 01a, 01b or 01c & goto :end)
if not exist "%ENGINE%" (echo Alteryx engine not found: %ENGINE% & goto :end)
set "OUT=%ROOT%\data\output\%VAR%"
if not exist "%OUT%" mkdir "%OUT%"
if not exist "%OUT%\XoL_Results.xlsx" copy "%ROOT%\excel\templates\XoL_Results.xlsx" "%OUT%\XoL_Results.xlsx" >nul
set "LOG=%OUT%\engine_run.log"
echo XoL pipeline: variant %VAR%, adapter %ADW%, from stage %FROM% > "%LOG%"
echo Started %date% %time% >> "%LOG%"
echo XoL pipeline: %VAR%, %ADW%. Log: %LOG%
set "GO=0"
for %%W in (00_read_control %ADW% 02_experience 03_burning_cost 04a_fit_severity 04_simulate 05_per_event 06_stop_loss 07_results) do (
    call :run %%W || goto :fail
)
echo ALL DONE %date% %time% >> "%LOG%"
echo.
echo All workflows ran. Results workbook: %OUT%\XoL_Results.xlsx
goto :end

:run
set "W=%~1"
if "%GO%"=="0" if /i not "%W:~0,2%"=="%FROM:~0,2%" (echo   skip %W & exit /b 0)
if "%GO%"=="0" if /i "%FROM%"=="04a" if /i not "%W:~0,3%"=="04a" (echo   skip %W & exit /b 0)
set "GO=1"
echo [%time%] %W ...
echo. >> "%LOG%"
echo ===== %W  start %date% %time% >> "%LOG%"
"%ENGINE%" "%WF%\%W%.yxmd" >> "%LOG%" 2>&1
set "RC=%errorlevel%"
echo ===== %W  end %time%  exit code %RC% >> "%LOG%"
if %RC% GEQ 2 (echo    FAILED, exit code %RC%: see %LOG% & exit /b 1)
if %RC%==1 (echo    finished with warnings: see %LOG%) else (echo    ok)
exit /b 0

:fail
echo STOPPED %date% %time% >> "%LOG%"
echo.
echo Stopped: the last workflow ended with an error. Details in %LOG%

:end
echo.
pause
