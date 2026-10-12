@echo off
cd /d "%~dp0"
title Acceso - Finalizar
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0finalizar.ps1"
pause
