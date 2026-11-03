@echo off
rem Start Organoid Sizer on Windows. Edit ENV_NAME if your Cellpose conda environment has another name.
set ENV_NAME=cellpose
call conda activate %ENV_NAME% 2>nul
cd /d "%~dp0.."
python -m organoid_sizer %*
pause
