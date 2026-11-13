@echo off
chcp 65001 > nul
title Subir SuperComparador a GitHub
cd /d "%~dp0"

echo ======================================================================
echo   🚀 Subiendo SuperComparador a GitHub...
echo ======================================================================
echo.

:: Asegurar ruta de Git y GitHub CLI instalados
set "PATH=%LOCALAPPDATA%\Microsoft\WinGet\Packages\Git.MinGit_Microsoft.Winget.Source_8wekyb3d8bbwe\cmd;%LOCALAPPDATA%\Microsoft\WinGet\Packages\GitHub.cli_Microsoft.Winget.Source_8wekyb3d8bbwe\bin;%PATH%"

git push -u origin main

echo.
if %ERRORLEVEL% EQU 0 (
    echo ======================================================================
    echo   ✅ ¡ÉXITO! Todo el proyecto y los datos están en GitHub.
    echo.
    echo   Ahora activa GitHub Pages:
    echo   1. Entra a: https://github.com/arqderman-tech/super-comparador/settings/pages
    echo   2. En "Source" elige: "GitHub Actions"
    echo   3. Tu web estará lista en: https://arqderman-tech.github.io/super-comparador/
    echo ======================================================================
) else (
    echo.
    echo ⚠️ Hubo un detalle al subir. Si te abrió una ventana en el navegador,
    echo autoriza el acceso a GitHub para completar la subida.
)
echo.
pause
