@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Assistant IA - garde cette fenetre ouverte
if not exist ".venv\Scripts\python.exe" (
  echo L'assistant n'est pas encore installe : lance d'abord installer_windows.bat
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m app.main
pause
