@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Installation de l'assistant IA

echo.
echo  ==============================================
echo    Installation de ton assistant IA
echo  ==============================================
echo.

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo  [!] Python est introuvable.
  echo      Installe Python 3.11 ou plus recent depuis https://www.python.org/downloads/
  echo      en cochant "Add python.exe to PATH", puis relance ce fichier.
  pause
  exit /b 1
)

%PY% -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 (
  echo  [!] Python 3.10 minimum est requis. Installe une version plus recente.
  pause
  exit /b 1
)

echo  [1/3] Creation de l'environnement Python...
if not exist ".venv\Scripts\python.exe" (
  %PY% -m venv .venv || (echo Echec de creation du venv & pause & exit /b 1)
)

echo  [2/3] Installation des dependances (quelques minutes)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
  echo  [!] L'installation des dependances a echoue. Verifie ta connexion internet.
  pause
  exit /b 1
)

echo  [3/3] Configuration...
if not exist ".env" copy ".env.example" ".env" >nul

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo  [i] Node.js n'est pas installe. Il est conseille pour connecter des applications
  echo      ^(la plupart des connecteurs MCP utilisent "npx"^) : https://nodejs.org
)

echo.
echo  Installation terminee !
echo  Lance l'assistant avec : demarrer_windows.bat
echo  (Ta cle API Anthropic se saisit dans les Reglages de l'interface.)
echo.
pause
