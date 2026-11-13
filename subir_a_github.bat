@echo off
title Subir SuperComparador a GitHub
cd /d "%~dp0"

echo ======================================================================
echo   Subiendo SuperComparador a GitHub...
echo ======================================================================
echo.

set "MINGIT=C:\Users\marti\AppData\Local\Microsoft\WinGet\Packages\Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe"
set "PATH=%MINGIT%\cmd;%MINGIT%\ucrt64\bin;C:\Users\marti\AppData\Local\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin;%PATH%"

"%MINGIT%\cmd\git.exe" push -u origin main

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ======================================================================
    echo   [EXITO] Todo el proyecto y los datos estan en GitHub.
    echo.
    echo   Ahora activa GitHub Pages:
    echo   1. Entra a: https://github.com/arqderman-tech/super-comparador/settings/pages
    echo   2. En "Source" elige: "GitHub Actions"
    echo   3. Tu web estara lista en: https://arqderman-tech.github.io/super-comparador/
    echo ======================================================================
) else (
    echo.
    echo ======================================================================
    echo   [AVISO] Si se abrio el navegador para iniciar sesion, completa el login.
    echo ======================================================================
)

echo.
pause
