#!/usr/bin/env python3
"""
YT-Transcribe: YouTube o archivo local → Transcripción en Markdown
Acepta URLs de YouTube o archivos de audio/video locales.
Soporta múltiples fuentes en un solo comando (modo batch).

Uso:
  python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"
  python yt_transcribe.py "reunion.mp4"
  python yt_transcribe.py "C:\\grabaciones\\meeting.mp3"
  python yt_transcribe.py URL1 URL2 archivo.mp4 archivo2.mp3  (modo batch)
  python yt_transcribe.py URL --force-audio    (saltar subtítulos, ir directo a Groq)
  python yt_transcribe.py URL -o carpeta       (guardar en otra carpeta)
  python yt_transcribe.py URL --lang en        (buscar subtítulos en inglés)
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


def get_local_file_info(filepath):
    """Obtener metadata básica de un archivo local"""
    path = Path(filepath)
    file_size_mb = path.stat().st_size / (1024 * 1024)

    duration = 0
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(filepath)
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        duration = int(float(result.stdout.strip()))
    except Exception:
        pass

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
    """Extraer audio de un archivo de video con ffmpeg"""
    output_path = os.path.join(output_dir, "audio.mp3")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vn",
        "-acodec", "libmp3lame",
        "-q:a", "5",
        output_path, "-y"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return None
    return output_path


def get_video_info(url):
    """Obtener título, duración y metadata del video de YouTube"""
    cmd = ["yt-dlp", "--dump-json", "--no-download", url]
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
    """Convertir archivo VTT en texto limpio sin duplicados"""
    lines = []
    seen = set()

    with open(vtt_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if (not line or line.startswith("WEBVTT") or line.startswith("Kind:")
                    or line.startswith("Language:") or "-->" in line
                    or line.startswith("NOTE") or line[0:1].isdigit() and line.endswith(":")):
                continue
            clean = re.sub(r'<[^>]+>', '', line).strip()
            if clean and clean not in seen:
                seen.add(clean)
                lines.append(clean)

    paragraphs = []
    for i in range(0, len(lines), 5):
        chunk = " ".join(lines[i:i+5])
        paragraphs.append(chunk)

    return "\n\n".join(paragraphs)


def download_audio(url, output_dir):
    """Descargar solo audio del video de YouTube como MP3"""
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "5",
        "-o", output_template,
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"  ❌ Error descargando audio: {result.stderr[:200]}")
        return None

    for f in Path(output_dir).glob("audio.*"):
        if f.suffix in (".mp3", ".m4a", ".opus", ".webm", ".wav"):
            return str(f)
    return None


def split_audio_if_needed(audio_path, max_size_mb=24):
    """Dividir audio en trozos si supera el límite de Groq (25 MB)"""
    file_size_mb = os.path.getsize(audio_path) / (1024 * 1024)

    if file_size_mb <= max_size_mb:
        return [audio_path]

    print(f"  📦 Audio grande ({file_size_mb:.1f} MB), dividiendo en partes...")
    output_dir = os.path.dirname(audio_path)

    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        audio_path
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    try:
        total_duration = float(result.stdout.strip())
    except (ValueError, AttributeError):
        total_duration = 3600

    chunk_duration = int((max_size_mb / file_size_mb) * total_duration * 0.9)
    chunk_duration = max(chunk_duration, 60)

    chunk_pattern = os.path.join(output_dir, "chunk_%03d.mp3")
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-f", "segment",
        "-segment_time", str(chunk_duration),
        "-c:a", "libmp3lame", "-q:a", "5",
        chunk_pattern, "-y"
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    chunks = sorted(str(f) for f in Path(output_dir).glob("chunk_*.mp3"))
    if chunks:
        print(f"  ✅ Dividido en {len(chunks)} partes")
        return chunks

    print(f"  ⚠️  No se pudo dividir, intentando archivo completo...")
    return [audio_path]


def transcribe_with_groq(audio_path, api_key, max_retries=5):
    """Transcribir audio con Groq Whisper API con retry automático en rate limit"""
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
                    response_format="text",
                )
            return transcription

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
                    import time as _time
                    _time.sleep(interval)
                    elapsed += interval
                    print(f"  ⏳ {int(remaining - interval)}s restantes...")
                else:
                    import time as _time
                    _time.sleep(remaining)
                    elapsed = wait_seconds

            print(f"  🔄 Reintentando (intento {attempt + 2}/{max_retries})...")


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
    """Procesar una sola fuente (URL o archivo local). Devuelve (filepath, método) o lanza excepción."""
    local_mode = is_local_file(source)

    if local_mode:
        # ── MODO ARCHIVO LOCAL ───────────────────────────
        filepath = Path(source)
        if not filepath.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {source}")

        print(f"\n📂 Archivo local detectado: {filepath.name}")
        info = get_local_file_info(filepath)
        file_size_mb = info["file_size_mb"]
        print(f"   Nombre:  {info['title']}")
        print(f"   Tamaño:  {file_size_mb:.1f} MB")
        if info["duration"]:
            dur = info["duration"]
            print(f"   Duración: {dur // 3600}h {(dur % 3600) // 60}m {dur % 60}s" if dur >= 3600
                  else f"   Duración: {dur // 60}:{dur % 60:02d}")

        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            raise EnvironmentError("No se encontró GROQ_API_KEY en .env")

        transcript = None
        method = "Groq Whisper (whisper-large-v3)"

        if filepath.suffix.lower() in VIDEO_EXTENSIONS:
            print(f"\n🎬 Extrayendo audio del video (requiere ffmpeg)...")
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = extract_audio_from_video(filepath, tmpdir)
                if not audio_path:
                    raise RuntimeError("Error extrayendo audio. ¿Está ffmpeg instalado? (winget install ffmpeg)")
                audio_size = os.path.getsize(audio_path) / (1024 * 1024)
                print(f"  ✅ Audio extraído ({audio_size:.1f} MB)")

                chunks = split_audio_if_needed(audio_path)
                n = len(chunks)
                print(f"\n🤖 Transcribiendo con Groq Whisper ({n} {'parte' if n == 1 else 'partes'})...")
                parts = []
                for i, chunk in enumerate(chunks):
                    if n > 1:
                        print(f"  ⏳ Parte {i+1}/{n}...")
                    text = transcribe_with_groq(chunk, api_key)
                    parts.append(text)
                    if n > 1:
                        print(f"  ✅ Parte {i+1}/{n} completada ({len(text):,} chars)")
                transcript = "\n\n".join(parts)
        else:
            print(f"\n🤖 Transcribiendo con Groq Whisper...")
            if file_size_mb > 24:
                with tempfile.TemporaryDirectory() as tmpdir:
                    import shutil
                    tmp_audio = os.path.join(tmpdir, filepath.name)
                    shutil.copy2(str(filepath), tmp_audio)
                    chunks = split_audio_if_needed(tmp_audio)
                    n = len(chunks)
                    print(f"  Dividido en {n} partes...")
                    parts = []
                    for i, chunk in enumerate(chunks):
                        if n > 1:
                            print(f"  ⏳ Parte {i+1}/{n}...")
                        text = transcribe_with_groq(chunk, api_key)
                        parts.append(text)
                    transcript = "\n\n".join(parts)
            else:
                transcript = transcribe_with_groq(str(filepath), api_key)

        print(f"  ✅ Transcripción completa ({len(transcript):,} caracteres)")

    else:
        # ── MODO YOUTUBE ─────────────────────────────────
        print(f"\n📹 Obteniendo info del video...")
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

            print(f"\n🎵 Descargando audio...")
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = download_audio(source, tmpdir)
                if not audio_path:
                    raise RuntimeError("Error descargando audio")

                file_size = os.path.getsize(audio_path) / (1024 * 1024)
                print(f"  ✅ Audio descargado ({file_size:.1f} MB)")

                chunks = split_audio_if_needed(audio_path)
                n = len(chunks)
                print(f"\n🤖 Transcribiendo con Groq Whisper ({n} {'parte' if n == 1 else 'partes'})...")

                parts = []
                for i, chunk in enumerate(chunks):
                    if n > 1:
                        print(f"  ⏳ Parte {i+1}/{n}...")
                    text = transcribe_with_groq(chunk, api_key)
                    parts.append(text)
                    if n > 1:
                        print(f"  ✅ Parte {i+1}/{n} completada ({len(text):,} chars)")

                transcript = "\n\n".join(parts)
                method = "Groq Whisper (whisper-large-v3)"
                print(f"  ✅ Transcripción completa ({len(transcript):,} caracteres)")

    filepath_out = save_transcript(transcript, info, output_dir, method)
    return filepath_out, method, len(transcript)


def main():
    parser = argparse.ArgumentParser(
        description="YT-Transcribe: YouTube o archivo local → Transcripción en Markdown",
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
    results = []  # (source, filepath_out, chars) para los OK
    errors  = []  # (source, mensaje) para los fallidos

    print(f"\n{'='*60}")
    print(f"  YT-Transcribe{f'  —  {total} fuentes' if total > 1 else ''}")
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

    # ── Resumen final (solo en modo batch) ───────────────
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
