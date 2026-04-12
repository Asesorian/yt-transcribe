# YT-Transcribe

YouTube o archivos locales → Transcripción en Markdown. Una URL, varios archivos, o una mezcla — todo en un solo comando.

Busca subtítulos en YouTube primero (gratis, instantáneo). Si no hay, descarga el audio y transcribe con Groq Whisper (~300 min/día gratis). Acepta archivos de audio y video locales directamente. **Modo batch incluido:** pasa varias fuentes a la vez y las procesa todas en secuencia.

```bash
# Un archivo
python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"

# Varios a la vez (batch)
python yt_transcribe.py "URL1" "URL2" sesion.mp4 grabacion.mp3
```

---

## Compatibilidad

| Sistema | Estado |
|---|---|
| Windows | ✅ Instalación automática (`install.bat`) |
| Mac / Linux | ✅ Funciona — instalación manual (ver abajo) |

---

## Instalación en Windows

```bash
# 1. Runtime JavaScript (requerido por yt-dlp para extraer info de YouTube)
winget install DenoLand.Deno

# 2. Clonar e instalar
git clone https://github.com/Asesorian/yt-transcribe.git
cd yt-transcribe
install.bat
```

El instalador (`install.bat`) hace cuatro cosas:
- **Verifica que Deno esté instalado** (si falta, aborta con instrucción clara)
- Instala `yt-dlp` y `groq` vía pip
- Detecta si tienes ffmpeg (necesario para videos >25 min y para archivos de video locales)
- Te pide tu API key de Groq y la guarda en `.env`

> ⚠️ **Importante:** `install.bat` verifica Deno pero no lo instala. Si te falta, ejecuta primero `winget install DenoLand.Deno`, **cierra y vuelve a abrir la terminal** para que coja el PATH actualizado, y relanza `install.bat`.

---

## Instalación en Mac / Linux

```bash
# 1. Runtime JavaScript (requerido por yt-dlp)
# Mac:
brew install deno
# Linux:
curl -fsSL https://deno.land/install.sh | sh

# 2. Clonar e instalar
git clone https://github.com/Asesorian/yt-transcribe.git
cd yt-transcribe
pip install yt-dlp groq

# ffmpeg (necesario para videos locales y audios >25 min)
# Mac:
brew install ffmpeg
# Ubuntu/Debian:
sudo apt install ffmpeg

cp .env.example .env
# Edita .env y pon tu clave: GROQ_API_KEY=tu_clave_aqui
```

---

## Obtener API key de Groq (gratis)

1. Ve a [console.groq.com](https://console.groq.com)
2. Crea una cuenta gratuita
3. API Keys → Create API Key
4. Copia la clave y pégala en tu `.env`

El plan gratuito incluye ~300 minutos de audio por día con Whisper Large v3.

---

## Uso

```bash
# URL de YouTube — subtítulos si hay, si no: Groq Whisper
python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"

# Archivo de video local (mp4, mkv, avi, mov...)
python yt_transcribe.py "reunion.mp4"
python yt_transcribe.py "C:\grabaciones\meeting.mp4"

# Archivo de audio local (mp3, m4a, wav, ogg...)
python yt_transcribe.py "audio.mp3"
python yt_transcribe.py "C:\grabaciones\entrevista.m4a"

# Modo batch — varias fuentes a la vez (URLs y/o archivos mezclados)
python yt_transcribe.py "URL1" "URL2" "URL3"
python yt_transcribe.py sesion1.mp4 sesion2.mp4 sesion3.mp3
python yt_transcribe.py "https://youtube.com/watch?v=xxx" reunion.mp4 entrevista.mp3

# Forzar Groq Whisper (saltar subtítulos YouTube)
python yt_transcribe.py "URL" --force-audio

# Guardar en otra carpeta
python yt_transcribe.py "URL" -o "/ruta/a/mis_transcripciones"

# Buscar subtítulos en inglés
python yt_transcribe.py "URL" --lang en
```

---

## Modo batch

Pasa cualquier combinación de URLs de YouTube y archivos locales en un solo comando:

```bash
python yt_transcribe.py "URL1" "URL2" sesion.mp4 grabacion.mp3
```

- Se procesan en orden, uno a uno
- Si una fuente falla, el error se muestra y continúa con la siguiente
- Al final se imprime un resumen con todas las transcripciones completadas y los errores

---

## Cómo funciona

1. **Detecta automáticamente** si es una URL de YouTube o un archivo local
2. **Para YouTube:** busca subtítulos primero (gratis), si no los hay descarga el audio
3. **Para archivos de video** (mp4, mkv...): extrae el audio con ffmpeg y transcribe con Groq
4. **Para archivos de audio** (mp3, m4a...): envía directamente a Groq Whisper
5. **Si el audio supera 25 MB:** lo divide en partes automáticamente, **valida que cada parte esté bajo el límite real** y re-segmenta si hace falta
6. **Si hay rate limit (429):** espera el tiempo exacto que indica Groq y reintenta solo
7. **Tolerancia a fallos por chunk:** si una parte concreta falla, se marca `[PARTE X FALLÓ]` y continúa con las siguientes (no aborta todo)
8. **Validación de completitud:** al final, comprueba caracteres / minuto y avisa si la densidad parece anormalmente baja
9. **Guarda** la transcripción como `.md` en la carpeta `transcripciones/`

---

## Formatos soportados

| Tipo | Extensiones |
|---|---|
| Video | `.mp4` `.mkv` `.avi` `.mov` `.webm` `.wmv` `.ts` `.mts` |
| Audio | `.mp3` `.m4a` `.wav` `.ogg` `.flac` `.opus` `.weba` |

---

## Formato de salida

```markdown
# Título del Video o Nombre del Archivo

> **Fuente:** NombreCanal / Archivo local
> **Fecha:** 2026-04-03
> **Duración:** 2h 25m 03s
> **Transcrito:** 2026-04-03 22:15
> **Método:** Groq Whisper (whisper-large-v3)
> **URL / Archivo:** ...

[transcripción completa]
```

---

## Requisitos

- Python 3.10+
- **Deno** (runtime JavaScript requerido por yt-dlp moderno para extraer info de YouTube)
- yt-dlp (mantener actualizado — ver Troubleshooting)
- groq
- ffmpeg (necesario para archivos de video locales y audios >25 min)
- API key de Groq (gratuita)

---

## Troubleshooting

### Error: *"No supported JavaScript runtime could be found"*

yt-dlp moderno requiere un runtime de JavaScript para extraer información de vídeos de YouTube (YouTube ofusca las URLs con JS). Por defecto busca Deno.

**Solución:**
```bash
# Windows
winget install DenoLand.Deno

# Mac
brew install deno

# Linux
curl -fsSL https://deno.land/install.sh | sh
```

Después **cierra y vuelve a abrir la terminal** para que coja el PATH actualizado. `install.bat` ahora verifica Deno automáticamente y te avisa si falta.

### Error: *"Private video"* / *"Sign in if you've been granted access"*

El vídeo es privado o no listado. Esto ocurre con frecuencia en streams en directo que el organizador oculta tras terminar la retransmisión. Opciones:

- Esperar a que se republique como vídeo normal (suele pasar en eventos con charlas editadas posteriormente)
- Buscar una versión alternativa en otro canal
- Actualmente el script no soporta `--cookies-from-browser` de yt-dlp, pero se puede añadir modificando `yt_transcribe.py`

### yt-dlp falla de forma rara con vídeos de YouTube

YouTube cambia su ofuscador cada pocas semanas y las versiones viejas de yt-dlp dejan de funcionar sin previo aviso. Si notas errores extraños con vídeos que antes funcionaban, **actualiza yt-dlp primero**:

```bash
python -m pip install -U yt-dlp
```

Es buena práctica hacerlo cada pocas semanas o antes de transcribir vídeos importantes.

### Aviso: *"transcripción posiblemente incompleta"*

Si al final de la ejecución ves un warning de densidad chars/minuto baja, la transcripción puede estar parcial (algún chunk falló silenciosamente o Groq devolvió respuesta truncada). Mira el output: cualquier marca `[PARTE X FALLÓ]` te indica exactamente qué chunk hay que reprocesar. Volver a lanzar el comando suele resolverlo (suelen ser rate limits o problemas puntuales de red).

---

## Changelog

### v2 (12 abril 2026)

**Resuelto:** Bug serio en vídeos largos.
- `parse_vtt` perdía 80-95% del contenido en vídeos largos por dedup global de frases comunes. Ahora solo deduplica consecutivos idénticos (que es lo único que produce de verdad el sliding window de los auto-subs de YouTube).
- `split_audio` validaba el chunking solo por estimación; ahora verifica el **tamaño real** de cada chunk post-split y re-segmenta si alguno excede el límite de Groq.
- Si un chunk concreto falla durante la transcripción, ya no aborta todo: marca `[PARTE X FALLÓ]` y continúa.
- Validación de completitud al final (chars/min) avisa si el resultado parece sospechosamente corto.
- `download_audio` y `extract_audio_from_video` ahora muestran el progreso en tiempo real (yt-dlp y ffmpeg sin `capture_output`) — fundamental en vídeos de varias horas.
- `install.bat` verifica Deno automáticamente con mensaje claro si falta.

**Validado con:** vídeo de 173 min → 138.136 caracteres en 4 chunks, manejó 26 minutos de rate limits sin abortar.

Si tenías una versión anterior y trabajabas con vídeos de más de ~30 minutos, **actualizar es muy recomendable**: la versión vieja podía devolver transcripciones parciales sin avisar.

```bash
cd yt-transcribe
git pull
```

### v1 (4 abril 2026)

Primera versión pública: soporte URLs YouTube + archivos locales (mp4, mp3, m4a...), modo batch, retry automático en rate limit, instalador Windows.

---

## Estructura

```
yt-transcribe/
  yt_transcribe.py     Script principal (multiplataforma)
  install.bat          Instalador automático (Windows)
  launcher.pyw         Lanzador con doble clic (Windows)
  YT-Transcribe.bat    Acceso directo (Windows)
  .env.example         Plantilla para tu API key
  .env                 Tu API key (no se sube a Git)
  transcripciones/     Aquí se guardan los .md (no se sube a Git)
  README.md            Este archivo
```
