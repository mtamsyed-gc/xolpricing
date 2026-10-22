@echo off
rem One-off check of the Merit route on this computer, with the synthetic Merit outputs in tests\fixtures\merit_v7:
rem copies them and their test Control into data\input, runs 00 -> 01c -> 02 (LIVE workflows, Alteryx engine),
rem then removes the copied inputs again (log: data\output\live\engine_run.log); then runs 00 -> 01c on the synthetic
rem ETE files with the SYNTH workflows (log: data\output\synth\engine_run.log). Rerun 01a afterwards for line mode.
rem Refuses to run when data\input already holds a Control workbook or ETE files (yours).
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "IN=%ROOT%\data\input"
if exist "%IN%\XoL_Control.xlsx" (echo data\input\XoL_Control.xlsx exists: not overwriting your inputs. & goto :end)
if exist "%IN%\ete\dim_life.csv" (echo data\input\ete holds files: not overwriting your inputs. & goto :end)
if not exist "%IN%\ete" mkdir "%IN%\ete"
copy /y "%ROOT%\tests\fixtures\merit_v7\XoL_Control_merit_test.xlsx" "%IN%\XoL_Control.xlsx" >nul
for %%F in (dim_life fact_member_month fact_claims run_manifest_claims) do copy /y "%ROOT%\tests\fixtures\merit_v7\%%F.csv" "%IN%\ete\%%F.csv" >nul
echo === Merit synthetic outputs through the LIVE workflows, stages 00 to 02
call "%~dp0run_pipeline.bat" live 01c 00 02 quiet
set "RC1=%errorlevel%"
del "%IN%\XoL_Control.xlsx"
for %%F in (dim_life fact_member_month fact_claims run_manifest_claims) do del "%IN%\ete\%%F.csv"
echo Test inputs removed from data\input.
echo.
echo === Synthetic ETE files through the SYNTH workflows, stages 00 to 01c
call "%~dp0run_pipeline.bat" synth 01c 00 01c quiet
set "RC2=%errorlevel%"
echo.
if "%RC1%"=="0" if "%RC2%"=="0" echo CHECK PASSED: both runs ended without errors.
if not "%RC1%"=="0" echo Merit route FAILED: see %ROOT%\data\output\live\engine_run.log
if not "%RC2%"=="0" echo Synthetic ETE run FAILED: see %ROOT%\data\output\synth\engine_run.log
:end
echo.
pause
