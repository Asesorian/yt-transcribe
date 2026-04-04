# YT-Transcribe

YouTube → Transcripción en Markdown. Un solo comando.

## Instalación

```
cd B:\Aplicaciones\yt-transcribe
install.bat
```

## Uso

```bash
# Básico — intenta subtítulos YouTube, si no hay usa Groq Whisper
python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"

# Forzar audio + Groq (mejor calidad, gasta minutos Groq)
python yt_transcribe.py "URL" --force-audio

# Guardar en otra carpeta
python yt_transcribe.py "URL" -o "B:\Downloads\mis_transcripciones"

# Buscar subtítulos en inglés
python yt_transcribe.py "URL" --lang en
```

## Cómo funciona

1. **Primero** busca subtítulos en YouTube (gratis, instantáneo)
2. **Si no hay**, descarga el audio y transcribe con Groq Whisper (~300 min/día gratis)
3. **Si el audio es >25 MB**, lo divide automáticamente en partes (requiere ffmpeg)
4. **Guarda** la transcripción como `.md` en `transcripciones/`

## Salida

Cada transcripción se guarda como Markdown con metadata:

```markdown
# Título del Video

> **Canal:** NombreCanal
> **Duración:** 45:33
> **Transcrito:** 2026-03-25 15:30
> **Método:** Subtítulos YouTube (es)  /  Groq Whisper
> **URL:** https://youtube.com/watch?v=...

[texto de la transcripción]
```

## Requisitos

- Python 3.10+
- yt-dlp (`pip install yt-dlp`)
- groq (`pip install groq`)
- ffmpeg (opcional, solo para videos largos >25 min)
- API key de Groq en `.env`

## Estructura

```
yt-transcribe/
  yt_transcribe.py     Script principal
  install.bat          Instalador
  .env                 API key de Groq (copiada de DictaFlow)
  transcripciones/     Aquí se guardan los .md
  README.md            Este archivo
```
