@echo off
rem XoL pricing tool: run the pipeline with the Alteryx engine (no Designer window needed).
rem   run_pipeline.bat                       synthetic data, line adapter (01a)
rem   run_pipeline.bat live 01b              live data, claimant adapter
rem   run_pipeline.bat synth 01a 04          start at stage 04 (00-03 already run)
rem   run_pipeline.bat live 01c 00 02        run stages 00 to 02 only (fourth argument: last stage)
rem   run_pipeline.bat live 01a 00 07 quiet  fifth argument quiet: no "press any key" at the end
rem Stops at the first workflow that ends with an error (a STOP check is an error).
rem Log: data\output\<variant>\engine_run.log
setlocal EnableDelayedExpansion
set "ENGINE=%LOCALAPPDATA%\Alteryx\bin\AlteryxEngineCmd.exe"
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VAR=%~1"
if "%VAR%"=="" set "VAR=synth"
set "AD=%~2"
if "%AD%"=="" set "AD=01a"
set "FROM=%~3"
if "%FROM%"=="" set "FROM=00"
set "TO=%~4"
if "%TO%"=="" set "TO=07"
set "QUIET=%~5"
if /i "%VAR%"=="live" (set "WF=%ROOT%\alteryx\workflows") else (set "WF=%ROOT%\alteryx\workflows\synth")
set "ADW="
if /i "%AD%"=="01a" set "ADW=01a_adapter_line"
if /i "%AD%"=="01b" set "ADW=01b_adapter_claimant"
if /i "%AD%"=="01c" set "ADW=01c_adapter_ete"
if not defined ADW (echo Unknown adapter %AD%: use 01a, 01b or 01c & goto :end)
if not exist "%ENGINE%" (echo Alteryx engine not found: %ENGINE% & goto :end)
set "OUT=%ROOT%\data\output\%VAR%"
for %%D in ("%OUT%" "%OUT%\stage" "%OUT%\checks" "%OUT%\results") do if not exist "%%~D" mkdir "%%~D"
if not exist "%OUT%\XoL_Results.xlsx" copy "%ROOT%\excel\templates\XoL_Results.xlsx" "%OUT%\XoL_Results.xlsx" >nul
set "LOG=%OUT%\engine_run.log"
echo XoL pipeline: variant %VAR%, adapter %ADW%, stages %FROM% to %TO% > "%LOG%"
echo Started %date% %time% >> "%LOG%"
echo XoL pipeline: %VAR%, %ADW%, stages %FROM% to %TO%. Log: %LOG%
rem stage codes in run order; the adapter's code is its own argument (01a / 01b / 01c)
set "GO=0"
set "RESULT=ok"
for %%S in (00 %AD% 02 03 04a 04 05 06 07) do (
    set "CODE=%%S"
    set "W=!CODE!"
    if "!CODE!"=="00" set "W=00_read_control"
    if "!CODE!"=="%AD%" set "W=%ADW%"
    if "!CODE!"=="02" set "W=02_experience"
    if "!CODE!"=="03" set "W=03_burning_cost"
    if "!CODE!"=="04a" set "W=04a_fit_severity"
    if "!CODE!"=="04" set "W=04_simulate"
    if "!CODE!"=="05" set "W=05_per_event"
    if "!CODE!"=="06" set "W=06_stop_loss"
    if "!CODE!"=="07" set "W=07_results"
    if "!RESULT!"=="ok" if /i "!CODE!"=="%FROM%" set "GO=1"
    if "!RESULT!"=="ok" if "!GO!"=="1" (
        call :run "!W!"
        if errorlevel 1 set "RESULT=failed"
        if /i "!CODE!"=="%TO%" set "GO=0"
    )
)
if "%RESULT%"=="ok" (
    echo ALL DONE %date% %time% >> "%LOG%"
    echo.
    if /i "%TO%"=="07" (echo All workflows ran. Results workbook: %OUT%\XoL_Results.xlsx) else (echo Stages %FROM% to %TO% ran.)
    set "RC=0"
) else (
    echo STOPPED %date% %time% >> "%LOG%"
    echo.
    echo Stopped: the last workflow ended with an error. Details in %LOG%
    set "RC=2"
)
goto :end

:run
set "WN=%~1"
echo [%time%] %WN% ...
echo. >> "%LOG%"
echo ===== %WN%  start %date% %time% >> "%LOG%"
"%ENGINE%" "%WF%\%WN%.yxmd" >> "%LOG%" 2>&1
set "ERC=%errorlevel%"
echo ===== %WN%  end %time%  exit code %ERC% >> "%LOG%"
if %ERC% GEQ 2 (echo    FAILED, exit code %ERC%: see %LOG% & exit /b 1)
if %ERC%==1 (echo    finished with warnings: see %LOG%) else (echo    ok)
exit /b 0

:end
echo.
if /i not "%QUIET%"=="quiet" pause
exit /b %RC%
