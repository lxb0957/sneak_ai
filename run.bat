@echo off
cd /d "%~dp0"

rem ---- Pick a Python that can actually import pygame ----
rem NOTE: this file is intentionally ASCII-only with CRLF line endings.
rem UTF-8 Chinese text + LF endings makes cmd misparse the script and
rem execute random fragments as commands (seen as mojibake errors).

rem 1) explicit override:  set SNEAK_PY=C:\Python311\python.exe
set "PY=%SNEAK_PY%"
set "OK=0"
if not "%PY%"=="" call :check "%PY%"
if "%OK%"=="1" goto run

rem 2) project-local virtualenv (the layout most people expect)
set "PY=%~dp0.venv\Scripts\python.exe"
call :check "%PY%"
if "%OK%"=="1" goto run

rem 3) whatever python resolves to on PATH
set "PY=python"
call :check "%PY%"
if "%OK%"=="1" goto run

rem 4) author dev environment. Harmless for everyone else: the pygame
rem    check simply fails and we fall through to the error message.
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\sneak\Scripts\python.exe"
call :check "%PY%"
if "%OK%"=="1" goto run

echo [ERROR] No Python with pygame was found.
echo.
echo Tried, in order:
echo   1. %%SNEAK_PY%%  -- environment override
echo   2. .venv\Scripts\python.exe in this folder
echo   3. python on PATH
echo.
echo Fix: create a virtualenv and install dependencies:
echo   python -m venv .venv
echo   .venv\Scripts\pip install -r requirements.txt
echo   run.bat
echo.
pause
exit /b 1

:check
set "OK=0"
"%~1" -c "import pygame" >nul 2>&1
if not errorlevel 1 set "OK=1"
goto :eof

:run
echo Starting Stealth Protocol ...
"%PY%" -m sneak_ai
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" goto finish
echo ---------- Exited with code %RC% ----------
if exist "crash.log" (
  echo Crash stack written to crash.log in the project folder:
  echo ----------------------------------------
  type "crash.log"
  echo ----------------------------------------
) else (
  echo No crash.log - the process died before entering the game loop.
  echo Common causes: missing display, bad video driver, broken Python env.
)
:finish
pause
exit /b %RC%
