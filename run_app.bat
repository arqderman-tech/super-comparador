@echo off
title SuperComparador - Servidor y Dashboard
echo ========================================================
echo   Iniciando SuperComparador (La Gallega + Indexacion)
echo ========================================================
echo.
start http://localhost:8000
python -X utf8 server.py
pause
