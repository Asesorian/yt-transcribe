@echo off
echo ============================================
echo   YT-Transcribe - Instalador
echo ============================================
echo.

echo [1/3] Instalando dependencias Python...
pip install yt-dlp groq --break-system-packages -q
echo   OK

echo.
echo [2/3] Verificando ffmpeg...
where ffmpeg >nul 2>&1
if %errorlevel% neq 0 (
    echo   ffmpeg NO encontrado.
    echo   Es necesario para dividir audios largos.
    echo   Instalar con: winget install ffmpeg
    echo   O descargar de: https://ffmpeg.org/download.html
    echo.
    echo   NOTA: Sin ffmpeg, videos cortos (menos de 25 min) funcionan igualmente.
) else (
    echo   OK - ffmpeg encontrado
)

echo.
echo [3/3] Configurando API key de Groq...
if exist .env (
    echo   OK - .env ya existe
) else (
    echo   Creando .env...
    set /p GROQ_KEY="  Introduce tu GROQ_API_KEY (Enter para saltar): "
    if defined GROQ_KEY (
        echo GROQ_API_KEY=%GROQ_KEY%> .env
        echo   OK - API key guardada en .env
    ) else (
        echo GROQ_API_KEY=tu_clave_aqui> .env
        echo   PENDIENTE - Edita .env con tu clave de Groq
    )
)

echo.
echo ============================================
echo   Instalacion completada!
echo.
echo   Uso:
echo     python yt_transcribe.py "URL_DEL_VIDEO"
echo.
echo   Las transcripciones se guardan en:
echo     %~dp0transcripciones\
echo ============================================
echo.
pause
