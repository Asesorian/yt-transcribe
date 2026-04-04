@echo off
chcp 65001 >nul
title YT-Transcribe
echo.
echo  ======================================
echo           YT-Transcribe v1.0
echo     YouTube - Transcripcion Markdown
echo  ======================================
echo.

:ask
set "URL="
set /p URL="  Pega la URL del video: "

if "%URL%"=="" (
    echo.
    echo   No has pegado ninguna URL
    echo.
    goto ask
)

echo.
echo  Procesando...
echo.

cd /d "%~dp0"
python yt_transcribe.py "%URL%"

echo.
echo  ----------------------------------------
echo.
set /p OTRO="  Otro video? (s/n): "
if /i "%OTRO%"=="s" (
    echo.
    goto ask
)

echo.
echo  Hasta luego!
pause
