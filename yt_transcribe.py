#!/usr/bin/env python3
"""
YT-Transcribe: YouTube → Transcripción en Markdown
Primero intenta subtítulos de YouTube (gratis, instantáneo).
Si no hay o son de baja calidad, descarga audio y transcribe con Groq Whisper.

Uso:
  python yt_transcribe.py URL
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


def get_video_info(url):
    """Obtener título, duración y metadata del video"""
    cmd = ["yt-dlp", "--dump-json", "--no-download", url]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"  ❌ Error obteniendo info: {result.stderr[:200]}")
        sys.exit(1)
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

        # Intentar manuales primero, luego auto-generados
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

        # Si no hay en el idioma pedido, probar inglés
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
            # Saltar cabeceras, timestamps, líneas vacías
            if (not line or line.startswith("WEBVTT") or line.startswith("Kind:")
                    or line.startswith("Language:") or "-->" in line
                    or line.startswith("NOTE") or line[0:1].isdigit() and line.endswith(":")):
                continue
            # Limpiar tags HTML
            clean = re.sub(r'<[^>]+>', '', line).strip()
            if clean and clean not in seen:
                seen.add(clean)
                lines.append(clean)

    # Unir en párrafos (cada ~5 líneas un salto)
    paragraphs = []
    for i in range(0, len(lines), 5):
        chunk = " ".join(lines[i:i+5])
        paragraphs.append(chunk)

    return "\n\n".join(paragraphs)


def download_audio(url, output_dir):
    """Descargar solo audio del video como MP3"""
    output_template = os.path.join(output_dir, "audio.%(ext)s")
    cmd = [
        "yt-dlp",
        "-x",
        "--audio-format", "mp3",
        "--audio-quality", "5",  # Calidad media → archivos más pequeños
        "-o", output_template,
        url
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        print(f"  ❌ Error descargando audio: {result.stderr[:200]}")
        return None

    # Buscar archivo descargado
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

    # Calcular duración de cada chunk para que quepa en 24 MB
    # Estimación: si X MB = duración total, chunk_duration = (24/X) * duración
    # Obtener duración con ffprobe
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
        total_duration = 3600  # fallback 1 hora

    chunk_duration = int((max_size_mb / file_size_mb) * total_duration * 0.9)  # 10% margen
    chunk_duration = max(chunk_duration, 60)  # mínimo 1 minuto

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

    # Si ffmpeg falla, intentar enviar el archivo entero
    print(f"  ⚠️  No se pudo dividir, intentando archivo completo...")
    return [audio_path]


def transcribe_with_groq(audio_path, api_key, max_retries=5):
    """Transcribir audio con Groq Whisper API.
    Reintenta automáticamente si se alcanza el rate limit (429),
    esperando el tiempo exacto que indica Groq en el error.
    """
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
                raise  # Agotados los reintentos

            # Extraer tiempo de espera del mensaje de Groq
            # Formato: "try again in 12m22.5s"
            wait_seconds = 750  # fallback conservador (12.5 min)
            match = re.search(r'try again in (\d+)m([\d.]+)s', str(e))
            if match:
                wait_seconds = int(match.group(1)) * 60 + float(match.group(2)) + 10  # +10s margen
            else:
                # Si no hay minutos, buscar solo segundos
                match_s = re.search(r'try again in ([\d.]+)s', str(e))
                if match_s:
                    wait_seconds = float(match_s.group(1)) + 10

            mins = int(wait_seconds // 60)
            secs = int(wait_seconds % 60)
            print(f"  ⏸️  Rate limit alcanzado. Esperando {mins}m {secs}s antes de reintentar...")

            # Cuenta regresiva visible cada 30 segundos
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


def save_transcript(text, info, output_dir, method):
    """Guardar transcripción como archivo Markdown"""
    # Nombre de archivo seguro
    safe_title = re.sub(r'[<>:"/\\|?*]', '', info["title"])
    safe_title = safe_title.strip()[:80]
    filename = f"{safe_title}.md"
    filepath = os.path.join(output_dir, filename)

    # Formatear metadata
    upload_date = info.get("upload_date", "")
    if upload_date and len(upload_date) == 8:
        upload_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:]}"

    duration = int(info.get("duration", 0))
    dur_min, dur_sec = divmod(duration, 60)
    dur_hour, dur_min = divmod(dur_min, 60)
    if dur_hour:
        dur_str = f"{dur_hour}h {dur_min:02d}m {dur_sec:02d}s"
    else:
        dur_str = f"{dur_min}:{dur_sec:02d}"

    content = f"""# {info['title']}

> **Canal:** {info['uploader']}
> **Fecha video:** {upload_date}
> **Duración:** {dur_str}
> **Transcrito:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
> **Método:** {method}
> **URL:** https://www.youtube.com/watch?v={info['id']}

---

{text}
"""

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def main():
    parser = argparse.ArgumentParser(
        description="YT-Transcribe: YouTube → Transcripción en Markdown",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python yt_transcribe.py "https://youtube.com/watch?v=xxxxx"
  python yt_transcribe.py URL --force-audio
  python yt_transcribe.py URL -o "B:\\Downloads\\transcripciones"
  python yt_transcribe.py URL --lang en
        """
    )
    parser.add_argument("url", help="URL del video de YouTube")
    parser.add_argument("-o", "--output", default=None,
                        help="Carpeta de salida (default: ./transcripciones)")
    parser.add_argument("--force-audio", action="store_true",
                        help="Saltar subtítulos, ir directo a descarga audio + Groq")
    parser.add_argument("--lang", default="es",
                        help="Idioma preferido para subtítulos (default: es)")

    args = parser.parse_args()

    # Cargar API key
    load_env()

    # Carpeta de salida
    output_dir = args.output or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "transcripciones"
    )
    os.makedirs(output_dir, exist_ok=True)

    # ── Info del video ──────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  YT-Transcribe")
    print(f"{'='*60}")
    print(f"\n📹 Obteniendo info del video...")
    info = get_video_info(args.url)
    dur = int(info["duration"])
    print(f"   Título:   {info['title']}")
    print(f"   Canal:    {info['uploader']}")
    print(f"   Duración: {dur // 60}:{dur % 60:02d}")

    transcript = None
    method = ""

    # ── Paso 1: Subtítulos YouTube ──────────────────────
    if not args.force_audio:
        print(f"\n📝 Buscando subtítulos en YouTube ({args.lang})...")
        transcript = try_youtube_subtitles(args.url, args.lang)
        if transcript:
            method = f"Subtítulos YouTube ({args.lang})"
            print(f"  ✅ Subtítulos encontrados ({len(transcript):,} caracteres)")
        else:
            print(f"  ❌ No hay subtítulos disponibles")

    # ── Paso 2: Audio + Groq Whisper ────────────────────
    if not transcript:
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            print(f"\n❌ No se encontró GROQ_API_KEY")
            print(f"   Crea un archivo .env en {os.path.dirname(os.path.abspath(__file__))}")
            print(f"   con: GROQ_API_KEY=tu_clave_de_groq")
            sys.exit(1)

        print(f"\n🎵 Descargando audio...")
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = download_audio(args.url, tmpdir)
            if not audio_path:
                print("  ❌ Error descargando audio")
                sys.exit(1)

            file_size = os.path.getsize(audio_path) / (1024 * 1024)
            print(f"  ✅ Audio descargado ({file_size:.1f} MB)")

            # Dividir si es necesario
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

    # ── Guardar ─────────────────────────────────────────
    filepath = save_transcript(transcript, info, output_dir, method)

    print(f"\n{'='*60}")
    print(f"  💾 Guardado en: {filepath}")
    print(f"  📊 Método: {method}")
    print(f"  📝 Caracteres: {len(transcript):,}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
