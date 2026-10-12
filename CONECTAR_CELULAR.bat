@echo off
cd /d "%~dp0"
title Acceso - Conectar celular
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0conectar_celular.ps1"
if errorlevel 1 pause
