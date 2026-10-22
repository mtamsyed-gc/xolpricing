@echo off
rem Copy Merit's final outputs, as Merit writes them, into the XoL tool's ETE / Merit input folder (data\input\ete).
rem   import_merit.bat "C:\Merit_Client\04_Outputs"
rem Takes <folder>\csv\ (Merit v7 layout) or <folder>\ itself. Nothing in Merit's folder is changed.
rem Then: Control Params source_mode = E (and ete_payer_filter / ete_tpa_filter), and run_pipeline.bat live 01c
setlocal
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "SRC=%~1"
if "%SRC%"=="" (echo Usage: import_merit.bat "Merit output folder" & goto :end)
if exist "%SRC%\csv\dim_life.csv" set "SRC=%SRC%\csv"
set "BAD="
for %%F in (dim_life fact_member_month fact_claims run_manifest_claims) do if not exist "%SRC%\%%F.csv" (echo Missing: %SRC%\%%F.csv & set "BAD=1")
if defined BAD (echo Nothing copied. & goto :end)
set "DST=%ROOT%\data\input\ete"
if not exist "%DST%" mkdir "%DST%"
for %%F in (dim_life fact_member_month fact_claims run_manifest_claims) do copy /y "%SRC%\%%F.csv" "%DST%\%%F.csv" >nul
echo Copied Merit outputs from %SRC% to %DST%:
for %%F in (dim_life fact_member_month fact_claims run_manifest_claims) do echo    %%F.csv
echo Next: set source_mode = E in data\input\XoL_Control.xlsx, then run: run_pipeline.bat live 01c
:end
echo.
pause
