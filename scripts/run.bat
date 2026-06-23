@echo off
REM ========================================
REM Presensi Udinus Bot - Auto-start launcher
REM ========================================
title Presensi Udinus Bot
cd /d "%~dp0\.."

echo [%date% %time%] Starting bot...

REM Loop: restart otomatis kalau crash, dengan cooldown
:loop
echo [%date% %time%] Bot instance starting...

REM Hapus lock lama supaya gak false positive
if exist data\runtime\bot.lock del data\runtime\bot.lock

python bot.py
set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% == 0 (
    echo [%date% %time%] Bot exit normal.
    exit /b 0
)

if %EXIT_CODE% == 1 (
    echo [%date% %time%] Bot exit (instance conflict).
    exit /b 1
)

echo.
echo [%date% %time%] Bot CRASHED! Exit code: %EXIT_CODE% Restart in 10 seconds...
timeout /t 10 /nobreak >nul
goto loop
