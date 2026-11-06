@echo off
rem XoL pricing tool: run the pipeline in Python, without Alteryx (0.7.0). Same arguments as run_pipeline.bat:
rem   run_pipeline_python.bat                       synthetic data, line adapter (01a)
rem   run_pipeline_python.bat live 01c              live data, Merit adapter
rem   run_pipeline_python.bat live 01c 00 04        stages 00 to 04 only
rem   run_pipeline_python.bat live 01a 00 07 quiet  fifth argument quiet: no "press any key" at the end
rem Log: data\output\<variant>\python_run.log
setlocal
set "PYX=py -3.12"
py -3.12 -c "" >nul 2>&1 || set "PYX=py"
%PYX% "%~dp0run_python.py" %1 %2 %3 %4
set "RC=%errorlevel%"
if /i not "%~5"=="quiet" pause
exit /b %RC%
