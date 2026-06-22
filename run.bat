@echo off
REM ========================================
REM Presensi Udinus Bot - Auto-start launcher
REM ========================================
title Presensi Udinus Bot
cd /d "%~dp0"

echo [%date% %time%] Starting bot...
echo.

REM Loop: restart otomatis kalau crash
:loop
echo [%date% %time%] Bot instance starting...
python bot.py
echo.
echo [%date% %time%] Bot CRASHED! Restart in 10 seconds...
timeout /t 10 /nobreak >nul
goto loop
