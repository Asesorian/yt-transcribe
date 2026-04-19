#!/usr/bin/env python3
"""
YT-Transcribe v3.0: YouTube o archivo local → Transcripción en Markdown con timestamps

Cambios v3.0 respecto v2 (breaking):
  - Chunking con OVERLAP (5s) en lugar de corte en seco. Evita pérdida de
    palabras en los bordes cuando Groq procesa audios largos.
  - Groq ahora usa response_format="verbose_json" → segments con timestamps
    locales por chunk.
  - Recomposición de timestamps: offset acumulado por chunk + deduplicación
    en zona de overlap (regla: skip segments cuyo global_start < max_end - 0.5).
  - Output Markdown con [HH:MM:SS] por párrafo (~45s por párrafo).
  - Agrupación en párrafos por umbral temporal, no por número de líneas.

No tocado (sigue igual que v2):
  - Retry rate limit Groq (hasta 5 intentos, espera parseada del error)
  - Tolerancia a fallos por chunk (continúa con siguientes si uno falla)
  - Detección truncamiento "and so on"
  - Validación densidad chars/min (400 chars/min mínimo)
  - Subtítulos YouTube vía yt-dlp (ruta preferente, no chunkeamos ahí)
  - Modo batch multi-fuente
  - Extracción audio de video local vía ffmpeg

Uso:
  python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"
  python yt_transcribe.py "reunion.mp4"
  python yt_transcribe.py URL1 URL2 archivo.mp4          (modo batch)
  python yt_transcribe.py URL --force-audio              (saltar subs YouTube)
  python yt_transcribe.py URL -o carpeta
  python yt_transcribe.py URL --lang en
"""

import sys
import os
import json
import re
import argparse
import tempfile
import subprocess
from pathlib import Path
from datetime import datetime


# Extensiones de audio/video que se procesan como archivos locales
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".ogg", ".flac", ".opus", ".weba"}
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".ts", ".mts"}
LOCAL_EXTENSIONS = AUDIO_EXTENSIONS | VIDEO_EXTENSIONS

# Marcadores conocidos de truncamiento de Groq Whisper (bug reportado en su comunidad)
TRUNCATION_MARKERS = [
    "and so on",
    "y así sucesivamente",
    "etcétera, etcétera",
    "and so forth",
]

# Heurística de densidad mínima esperada en español hablado (chars/min)
# Habla normal ronda 900-1100 chars/min. 400 = umbral muy conservador.
MIN_CHARS_PER_MINUTE = 400

# Configuración de chunking v3
OVERLAP_SECONDS = 5          # Solapamiento entre chunks consecutivos
MAX_CHUNK_MB = 24            # Límite seguro bajo el tope de 25 MB de Groq
MIN_CHUNK_DURATION = 60      # Chunk mínimo (si audio es muy corto lo dejamos entero)
PARAGRAPH_GAP_SECONDS = 45   # Cada ~45s de contenido → nuevo párrafo con timestamp
DEDUP_TOLERANCE = 0.5        # Segundos de tolerancia al deduplicar overlap

# Ruta del cookies.txt (misma carpeta que el script)
COOKIES_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt")


def get_yt_args():
    """Construir argumentos base para yt-dlp.
    - Siempre incluye cookies.txt si existe (necesario desde cambios YouTube 2026)
    - Sin forzar cliente concreto (yt-dlp elige el mejor disponible)
    """
    args = []
    if os.path.exists(COOKIES_FILE):
        args += ["--cookies", COOKIES_FILE]
    return args


def load_env():
    """Cargar GROQ_API_KEY desde .env si existe"""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("GROQ_API_KEY=") and not line.startswith("#"):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    os.environ.setdefault("GROQ_API_KEY", key)


def is_local_file(source):
    """Detectar si el argumento es un archivo local (no una URL)"""
    path = Path(source)
    if path.exists() and path.is_file():
        return True
    if path.suffix.lower() in LOCAL_EXTENSIONS:
        return True
    return False


def fmt_time(seconds):
    """Formatear segundos a [HH:MM:SS] o [MM:SS] según duración."""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_audio_duration(audio_path):
    """Obtener duración en segundos de un audio vía ffprobe. Devuelve 0 si falla."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(audio_path)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True)
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def get_local_file_info(filepath):
    """Obtener metadata básica de un archivo local"""
    path = Path(filepath)
    file_size_mb = path.stat().st_size / (1024 * 1024)
    duration = int(get_audio_duration(filepath))

    return {
        "title": path.stem,
        "duration": duration,
        "uploader": "Archivo local",
        "upload_date": "",
        "id": "",
        "source_path": str(filepath),
        "file_size_mb": file_size_mb,
    }


def extract_audio_from_video(video_path, output_dir):
    """Extraer audio de un archivo de video con ffmpeg."""
    output_path = os.path.join(output_dir, "audio.mp3")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "5",
        output_path, "-y"
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return None
    return output_path


def get_video_info(url):
    """Obtener título, duración y metadata del video de YouTube"""
    cmd = ["yt-dlp", "--dump-json", "--no-download"] + get_yt_args() + [url]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"  ❌ Error obteniendo info: {result.stderr[:200]}")
        return None
    info = json.loads(result.stdout)
    return {
        "title": info.get("title", "Sin título"),
        "duration": info.get("duration", 0),
        "uploader": info.get("uploader", "Desconocido"),
        "upload_date": info.get("upload_date", ""),
        "id": info.get("id", ""),
        "description": info.get("description", "")[:500],
    }


def try_youtube_subtitles(url, lang="es"):
    """Intentar obtener subtítulos de YouTube (manuales primero, luego auto)"""
    with tempfile.TemporaryDirectory() as tmpdir:
        output = os.path.join(tmpdir, "subs")

        for sub_flag in ["--write-sub", "--write-auto-sub"]:
            cmd = [
                "yt-dlp", sub_flag,
                "--sub-lang", lang,
                "--sub-format", "vtt",
                "--skip-download",
            ] + get_yt_args() + [
                "-o", output,
                url
            ]
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")

            for f in Path(tmpdir).glob("*.vtt"):
                text = parse_vtt(f)
                if text and len(text) > 100:
                    return text

        if lang != "en":
            print(f"  ⚠️  No hay subtítulos en '{lang}', probando inglés...")
            return try_youtube_subtitles(url, "en")

    return None


def parse_vtt(vtt_path):
    """Convertir archivo VTT en texto limpio."""
    lines = []
    last_clean = None

    with open(vtt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if (not line or line.startswith("WEBVTT") or line.startswith("Kind:")
                    or line.startswith("Language:") or "-->" in line
                    or line.startswith("NOTE")
                    or (line[0:1].isdigit() and line.endswith(":"))):
                continue
            clean = re.sub(r'<[^>]+>', '', line).strip()
            if clean and clean != last_clean:
                lines.append(clean)
                last_clean = clean

    paragraphs = []
    for i in range(0, len(lines), 5):
        chunk = " ".join(lines[i:i+5])
        paragraphs.append(chunk)

    return "\n\n".join(paragraphs)


def download_audio(url, output_dir):
    """Descargar solo audio del video de YouTube como MP3."""
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "5",
    ] + get_yt_args() + [
        "-o", output_template,
        url
    ]
    result = subprocess.run(cmd, encoding="utf-8")
    if result.returncode != 0:
        print(f"  ❌ Error descargando audio (yt-dlp returncode {result.returncode})")
        return None

    for f in Path(output_dir).glob("audio.*"):
        if f.suffix in (".mp3", ".m4a", ".opus", ".webm", ".wav"):
            return str(f)
    return None


def _extract_chunk(audio_path, start, duration, output_path):
    """Extraer un chunk concreto con ffmpeg (-ss start -t duration)."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-t", str(duration),
        "-i", audio_path,
        "-c:a", "libmp3lame", "-q:a", "5",
        output_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ ffmpeg falló extrayendo chunk desde {start}s:")
        print(f"     {result.stderr[:300]}")
        return False
    if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
        return False
    return True


def split_audio_with_overlap(audio_path, max_size_mb=MAX_CHUNK_MB,
                             overlap_seconds=OVERLAP_SECONDS):
    """Dividir audio en chunks solapados para Groq (v3)."""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)
    total_duration = get_audio_duration(audio_path)

    if file_size_mb <= max_size_mb:
        return [(audio_path, 0.0)]

    if total_duration <= 0:
        total_duration = 3600

    print(f"  📦 Audio grande ({file_size_mb:.1f} MB, {total_duration/60:.1f} min), "
          f"dividiendo con overlap de {overlap_seconds}s...")

    output_dir = os.path.dirname(audio_path)
    chunk_duration = int((max_size_mb / file_size_mb) * total_duration * 0.8)
    chunk_duration = max(chunk_duration, MIN_CHUNK_DURATION)

    def build_chunks(chunk_dur):
        for f in Path(output_dir).glob("chunk_*.mp3"):
            try:
                os.remove(f)
            except OSError:
                pass

        chunks = []
        start = 0.0
        idx = 0
        step = chunk_dur - overlap_seconds
        if step <= 0:
            raise ValueError(f"overlap ({overlap_seconds}s) >= chunk_duration ({chunk_dur}s)")

        while start < total_duration:
            chunk_path = os.path.join(output_dir, f"chunk_{idx:03d}.mp3")
            effective_duration = min(chunk_dur, total_duration - start + 1)
            print(f"  \u2702\ufe0f  Extrayendo parte {idx+1} [desde {fmt_time(start)}]...",
                  end="", flush=True)
            if not _extract_chunk(audio_path, start, effective_duration, chunk_path):
                raise RuntimeError(f"No se pudo extraer chunk {idx} desde {start}s")
            chunk_size = os.path.getsize(chunk_path) / (1024 * 1024)
            print(f" ok ({chunk_size:.1f} MB)")
            chunks.append((chunk_path, start))
            start += step
            idx += 1

        return chunks

    chunks = build_chunks(chunk_duration)

    def check_oversized(chunk_list):
        oversized = []
        for path, _ in chunk_list:
            size_mb = os.path.getsize(path) / (1024 * 1024)
            if size_mb > max_size_mb:
                oversized.append((path, size_mb))
        return oversized

    oversized = check_oversized(chunks)
    retry_count = 0
    while oversized and retry_count < 3:
        retry_count += 1
        chunk_duration = max(chunk_duration // 2, 30)
        print(f"  🔄 Re-segmentando con chunks de {chunk_duration}s (+{overlap_seconds}s overlap)...")
        chunks = build_chunks(chunk_duration)
        oversized = check_oversized(chunks)

    if oversized:
        raise RuntimeError(
            f"Tras {retry_count} reintentos, {len(oversized)} chunks siguen "
            f"superando {max_size_mb} MB. Audio fuente posiblemente corrupto."
        )

    print(f"  ✅ Dividido en {len(chunks)} partes con overlap de {overlap_seconds}s:")
    for path, offset in chunks:
        size_mb = os.path.getsize(path) / (1024 * 1024)
        print(f"     {Path(path).name}: {size_mb:.1f} MB  [offset {fmt_time(offset)}]")

    return chunks


def transcribe_with_groq(audio_path, api_key, max_retries=5):
    """Transcribir audio con Groq Whisper API (verbose_json con timestamps)."""
    import time
    from groq import Groq, RateLimitError

    client = Groq(api_key=api_key)

    for attempt in range(max_retries):
        try:
            with open(audio_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=audio_file,
                    model="whisper-large-v3",
                    language="es",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"],
                )

            if hasattr(transcription, "segments"):
                raw_segments = transcription.segments or []
            elif isinstance(transcription, dict):
                raw_segments = transcription.get("segments", [])
            else:
                raw_segments = []

            segments = []
            for seg in raw_segments:
                if isinstance(seg, dict):
                    start = float(seg.get("start", 0))
                    end = float(seg.get("end", start))
                    text = (seg.get("text") or "").strip()
                else:
                    start = float(getattr(seg, "start", 0))
                    end = float(getattr(seg, "end", start))
                    text = (getattr(seg, "text", "") or "").strip()
                if text:
                    segments.append({"start": start, "end": end, "text": text})

            return segments

        except RateLimitError as e:
            if attempt >= max_retries - 1:
                raise

            wait_seconds = 750
            match = re.search(r'try again in (\d+)m([\d.]+)s', str(e))
            if match:
                wait_seconds = int(match.group(1)) * 60 + float(match.group(2)) + 10
            else:
                match_s = re.search(r'try again in ([\d.]+)s', str(e))
                if match_s:
                    wait_seconds = float(match_s.group(1)) + 10

            mins = int(wait_seconds // 60)
            secs = int(wait_seconds % 60)
            print(f"  ⏸️  Rate limit alcanzado. Esperando {mins}m {secs}s antes de reintentar...")

            elapsed = 0
            interval = 30
            while elapsed < wait_seconds:
                remaining = wait_seconds - elapsed
                if remaining > interval:
                    time.sleep(interval)
                    elapsed += interval
                    print(f"  ⏳ {int(remaining - interval)}s restantes...")
                else:
                    time.sleep(remaining)
                    elapsed = wait_seconds

            print(f"  🔄 Reintentando (intento {attempt + 2}/{max_retries})...")


def format_transcript_with_timestamps(global_segments, gap_seconds=PARAGRAPH_GAP_SECONDS):
    """Agrupar segments globales en párrafos con [HH:MM:SS] de cabecera."""
    if not global_segments:
        return ""

    paragraphs = []
    current_text = []
    current_start = global_segments[0]["start"]
    current_accum_start = current_start
    last_end = global_segments[0]["start"]

    for seg in global_segments:
        gap_from_last = seg["start"] - last_end
        elapsed_in_paragraph = seg["end"] - current_accum_start

        if current_text and (elapsed_in_paragraph > gap_seconds or gap_from_last > 3.0):
            paragraphs.append(
                f"[{fmt_time(current_accum_start)}] " + " ".join(current_text).strip()
            )
            current_text = []
            current_accum_start = seg["start"]

        current_text.append(seg["text"])
        last_end = seg["end"]

    if current_text:
        paragraphs.append(
            f"[{fmt_time(current_accum_start)}] " + " ".join(current_text).strip()
        )

    return "\n\n".join(paragraphs)


def transcribe_chunks(chunks_with_offset, api_key, total_duration_seconds=0):
    """Transcribir chunks con offset y recomponer timestamps globales."""
    n = len(chunks_with_offset)
    all_global_segments = []
    failures = []
    max_end_so_far = -1.0
    dedup_skipped = 0
    error_markers = []

    for i, (chunk_path, offset) in enumerate(chunks_with_offset):
        if n > 1:
            print(f"  ⏳ Parte {i+1}/{n} (offset {fmt_time(offset)})...")
        try:
            segments = transcribe_with_groq(chunk_path, api_key)
            if not segments:
                raise RuntimeError("Groq devolvió 0 segments")

            full_text = " ".join(s["text"] for s in segments).lower().rstrip(". \n")
            if any(full_text.endswith(marker) for marker in TRUNCATION_MARKERS):
                print(f"  ⚠️  Parte {i+1} parece truncada (termina en marcador Groq)")

            kept_in_this_chunk = 0
            for seg in segments:
                global_start = offset + seg["start"]
                global_end = offset + seg["end"]

                if global_start < max_end_so_far - DEDUP_TOLERANCE:
                    dedup_skipped += 1
                    continue

                all_global_segments.append({
                    "start": global_start,
                    "end": global_end,
                    "text": seg["text"],
                })
                kept_in_this_chunk += 1
                if global_end > max_end_so_far:
                    max_end_so_far = global_end

            if n > 1:
                chars_kept = sum(
                    len(s["text"]) for s in all_global_segments[-kept_in_this_chunk:]
                ) if kept_in_this_chunk else 0
                print(f"  ✅ Parte {i+1}/{n}: {len(segments)} segments "
                      f"({chars_kept:,} chars tras dedup)")

        except Exception as e:
            failures.append((i + 1, str(e)))
            error_markers.append((offset, i + 1, str(e)))
            print(f"  ❌ Parte {i+1}/{n} FALLÓ: {e}")
            print(f"     Continuando con las siguientes partes...")

    all_global_segments.sort(key=lambda s: s["start"])
    transcript = format_transcript_with_timestamps(all_global_segments)

    if error_markers:
        lines = []
        inserted = set()
        for block in transcript.split("\n\n"):
            m = re.match(r'\[(\d{2}):(\d{2})(?::(\d{2}))?\]', block)
            if m:
                if m.group(3):
                    block_seconds = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
                else:
                    block_seconds = int(m.group(1)) * 60 + int(m.group(2))
            else:
                block_seconds = 0

            for offset, chunk_idx, err in error_markers:
                if chunk_idx not in inserted and offset <= block_seconds:
                    lines.append(f"[{fmt_time(offset)}] [PARTE {chunk_idx}/{n} FALLÓ: {err}]")
                    inserted.add(chunk_idx)
            lines.append(block)

        for offset, chunk_idx, err in error_markers:
            if chunk_idx not in inserted:
                lines.append(f"[{fmt_time(offset)}] [PARTE {chunk_idx}/{n} FALLÓ: {err}]")
                inserted.add(chunk_idx)

        transcript = "\n\n".join(lines)

    real_chars = sum(len(s["text"]) for s in all_global_segments)

    stats = {
        "total_chunks": n,
        "failed_chunks": len(failures),
        "failures": failures,
        "real_chars": real_chars,
        "total_segments": len(all_global_segments),
        "dedup_skipped_segments": dedup_skipped,
        "completeness_warning": False,
    }

    if dedup_skipped > 0 and n > 1:
        print(f"\n  🔀 Deduplicación overlap: {dedup_skipped} segments descartados")

    if total_duration_seconds > 0:
        expected_min_chars = (total_duration_seconds / 60) * MIN_CHARS_PER_MINUTE
        if real_chars < expected_min_chars:
            ratio_pct = (real_chars / expected_min_chars) * 100
            duration_min = total_duration_seconds / 60
            print(f"\n  ⚠️  ALERTA: transcripción posiblemente incompleta")
            print(f"     {real_chars:,} chars para {duration_min:.0f} min de audio")
            print(f"     Mínimo esperado: {expected_min_chars:,.0f} chars")
            print(f"     Densidad obtenida: {ratio_pct:.0f}% del mínimo")
            stats["completeness_warning"] = True

    if failures:
        print(f"\n  ⚠️  Resumen: {len(failures)}/{n} partes fallaron")

    return transcript, stats


def save_transcript(text, info, output_dir, method):
    """Guardar transcripción como archivo Markdown"""
    safe_title = re.sub(r'[<>:"/\\|?*]', '', info["title"])
    safe_title = safe_title.strip()[:80]
    filename = f"{safe_title}.md"
    filepath = os.path.join(output_dir, filename)

    upload_date = info.get("upload_date", "")
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    duration = int(info.get("duration", 0))
    dur_min, dur_sec = divmod(duration, 60)
    dur_hour, dur_min = divmod(dur_min, 60)
    if dur_hour:
        dur_str = f"{dur_hour}h {dur_min:02d}m {dur_sec:02d}s"
    elif dur_min:
        dur_str = f"{dur_min}:{dur_sec:02d}"
    else:
        dur_str = "—"

    video_id = info.get("id", "")
    source_path = info.get("source_path", "")
    if video_id:
        origen = f"> **URL:** https://www.youtube.com/watch?v={video_id}"
    elif source_path:
        origen = f"> **Archivo:** {Path(source_path).name}"
    else:
        origen = ""

    content = f"""# {info['title']}

> **Fuente:** {info['uploader']}
> **Fecha:** {upload_date or datetime.now().strftime('%Y-%m-%d')}
> **Duración:** {dur_str}
> **Transcrito:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
> **Método:** {method}
{origen}

---

{text}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def process_source(source, args, output_dir):
    """Procesar una sola fuente (URL o archivo local)."""
    local_mode = is_local_file(source)

    if local_mode:
        filepath = Path(source)
        if not filepath.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {source}")

        print(f"\n📂 Archivo local detectado: {filepath.name}")
        info = get_local_file_info(filepath)
        file_size_mb = info["file_size_mb"]
        duration = info["duration"]
        print(f"   Nombre:  {info['title']}")
        print(f"   Tamaño:  {file_size_mb:.1f} MB")
        if duration:
            if duration >= 3600:
                print(f"   Duración: {duration // 3600}h {(duration % 3600) // 60}m {duration % 60}s")
            else:
                print(f"   Duración: {duration // 60}:{duration % 60:02d}")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("No se encontró GROQ_API_KEY en .env")

        transcript = None
        method = "Groq Whisper (whisper-large-v3, verbose_json + overlap 5s)"

        if filepath.suffix.lower() in VIDEO_EXTENSIONS:
            print(f"\n🎬 Extrayendo audio del video (requiere ffmpeg)...")
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = extract_audio_from_video(filepath, tmpdir)
                if not audio_path:
                    raise RuntimeError("Error extrayendo audio. ¿Está ffmpeg instalado? (winget install ffmpeg)")
                audio_size = os.path.getsize(audio_path) / (1024 * 1024)
                print(f"  ✅ Audio extraído ({audio_size:.1f} MB)")
                chunks = split_audio_with_overlap(audio_path)
                n = len(chunks)
                print(f"\n🤖 Transcribiendo con Groq Whisper ({n} {'parte' if n == 1 else 'partes'})...")
                transcript, stats = transcribe_chunks(chunks, api_key, total_duration_seconds=duration)
        else:
            print(f"\n🤖 Transcribiendo con Groq Whisper...")
            if file_size_mb > MAX_CHUNK_MB:
                with tempfile.TemporaryDirectory() as tmpdir:
                    import shutil
                    tmp_audio = os.path.join(tmpdir, filepath.name)
                    shutil.copy2(str(filepath), tmp_audio)
                    chunks = split_audio_with_overlap(tmp_audio)
                    n = len(chunks)
                    print(f"  Dividido en {n} partes...")
                    transcript, stats = transcribe_chunks(chunks, api_key, total_duration_seconds=duration)
            else:
                transcript, stats = transcribe_chunks(
                    [(str(filepath), 0.0)], api_key, total_duration_seconds=duration
                )

        print(f"  ✅ Transcripción completa ({len(transcript):,} caracteres, "
              f"{stats['total_segments']} segments)")

    else:
        print(f"\n📹 Obteniendo info del video...")
        if os.path.exists(COOKIES_FILE):
            print(f"  🍪 Usando cookies.txt para autenticación")
        info = get_video_info(source)
        if not info:
            raise RuntimeError(f"No se pudo obtener info del video: {source}")
        dur = int(info["duration"])
        print(f"   Título:   {info['title']}")
        print(f"   Canal:    {info['uploader']}")
        print(f"   Duración: {dur // 60}:{dur % 60:02d}")

        transcript = None
        method = ""

        if not args.force_audio:
            print(f"\n📝 Buscando subtítulos en YouTube ({args.lang})...")
            transcript = try_youtube_subtitles(source, args.lang)
            if transcript:
                method = f"Subtítulos YouTube ({args.lang})"
                print(f"  ✅ Subtítulos encontrados ({len(transcript):,} caracteres)")
            else:
                print(f"  ❌ No hay subtítulos disponibles")

        if not transcript:
            api_key = os.environ.get("GROQ_API_KEY")
            if not api_key:
                raise EnvironmentError("No se encontró GROQ_API_KEY en .env")

            print(f"\n🎵 Descargando audio (verás el progreso de yt-dlp abajo)...")
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = download_audio(source, tmpdir)
                if not audio_path:
                    raise RuntimeError("Error descargando audio")

                file_size = os.path.getsize(audio_path) / (1024 * 1024)
                print(f"  ✅ Audio descargado ({file_size:.1f} MB)")

                chunks = split_audio_with_overlap(audio_path)
                n = len(chunks)
                print(f"\n🤖 Transcribiendo con Groq Whisper ({n} {'parte' if n == 1 else 'partes'})...")

                transcript, stats = transcribe_chunks(chunks, api_key, total_duration_seconds=dur)
                method = "Groq Whisper (whisper-large-v3, verbose_json + overlap 5s)"
                print(f"  ✅ Transcripción completa ({len(transcript):,} caracteres, "
                      f"{stats['total_segments']} segments)")

    filepath_out = save_transcript(transcript, info, output_dir, method)
    return filepath_out, method, len(transcript)


def main():
    parser = argparse.ArgumentParser(
        description="YT-Transcribe v3.0: YouTube o archivo local → Markdown con timestamps",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"
  python yt_transcribe.py "reunion.mp4"
  python yt_transcribe.py "C:\\grabaciones\\meeting.mp3"
  python yt_transcribe.py URL1 URL2 archivo.mp4           (modo batch)
  python yt_transcribe.py URL --force-audio
  python yt_transcribe.py URL --lang en
        """
    )
    parser.add_argument("sources", nargs="+",
                        help="URLs de YouTube y/o rutas a archivos de audio/video (uno o varios)")
    parser.add_argument("-o", "--output", default=None,
                        help="Carpeta de salida (default: ./transcripciones)")
    parser.add_argument("--force-audio", action="store_true",
                        help="Saltar subtítulos YouTube, ir directo a Groq Whisper")
    parser.add_argument("--lang", default="es",
                        help="Idioma preferido para subtítulos YouTube (default: es)")

    args = parser.parse_args()

    load_env()

    output_dir = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "transcripciones"
    )
    os.makedirs(output_dir, exist_ok=True)

    total = len(args.sources)
    results = []
    errors = []

    print(f"\n{'='*60}")
    print(f"  YT-Transcribe v3.0{f'  —  {total} fuentes' if total > 1 else ''}")
    print(f"{'='*60}")

    for idx, source in enumerate(args.sources):
        if total > 1:
            print(f"\n{'─'*60}")
            print(f"  [{idx+1}/{total}] {source}")
            print(f"{'─'*60}")
        try:
            filepath_out, method, chars = process_source(source, args, output_dir)
            results.append((source, filepath_out, method, chars))
            print(f"\n  💾 Guardado en: {filepath_out}")
            print(f"  📊 Método: {method}  |  📝 Caracteres: {chars:,}")
        except Exception as e:
            errors.append((source, str(e)))
            print(f"\n  ❌ Error: {e}")
            if total == 1:
                sys.exit(1)
            else:
                print(f"  ⏭️  Continuando con la siguiente fuente...")

    if total > 1:
        print(f"\n{'='*60}")
        print(f"  RESUMEN BATCH — {total} fuentes procesadas")
        print(f"{'='*60}")
        print(f"  ✅ Completadas: {len(results)}/{total}")
        for source, filepath_out, method, chars in results:
            name = Path(source).name if is_local_file(source) else source[:60]
            print(f"     • {name}")
            print(f"       → {Path(filepath_out).name}  ({chars:,} chars, {method})")
        if errors:
            print(f"\n  ❌ Con errores: {len(errors)}/{total}")
            for source, msg in errors:
                name = Path(source).name if is_local_file(source) else source[:60]
                print(f"     • {name}: {msg}")
        print(f"{'='*60}\n")
    else:
        print(f"\n{'='*60}\n")


if __name__ == "__main__":
    main()
