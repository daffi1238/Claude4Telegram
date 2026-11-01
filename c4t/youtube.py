"""Vídeos de YouTube enviados por Telegram: se descarga el audio con yt-dlp, lo transcribe el hub
(Whisper) y Claude (suscripción) hace el resumen. La descarga necesita cookies de una sesión iniciada
(YouTube exige "Sign in to confirm you're not a bot"); se configuran en el equipo, nunca por Telegram."""
import asyncio
import re
import shutil
from pathlib import Path

URL_RE = re.compile(r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?[^\s]*v=|shorts/|live/)|youtu\.be/)[\w\-]+[^\s]*")
SIGN_IN = ("sign in to confirm", "confirm you’re not a bot", "confirm you're not a bot", "cookies")
DOWNLOAD_TIMEOUT = 600


def find_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    return m.group(0) if m else None


def yt_dlp_bin() -> str | None:
    return shutil.which("yt-dlp") or (lambda p: str(p) if p.exists() else None)(
        Path.home() / ".local" / "bin" / "yt-dlp")


class SignInRequired(RuntimeError):
    """yt-dlp pide cookies de una sesión iniciada."""


async def download_audio(url: str, dest_dir: Path, cookies: str = "", cookies_browser: str = "",
                         max_minutes: int = 0) -> tuple[Path, str]:
    """Descarga el audio del vídeo a dest_dir y devuelve (ruta, título). Lanza SignInRequired/RuntimeError."""
    binary = yt_dlp_bin()
    if not binary:
        raise RuntimeError("yt-dlp no está instalado")
    out = str(dest_dir / "%(title).150B [%(id)s].%(ext)s")
    cmd = [binary, "-x", "--audio-format", "mp3", "--audio-quality", "5", "--no-playlist",
           "--no-progress", "-o", out]
    if max_minutes:
        cmd += ["--match-filter", f"duration<={max_minutes * 60}"]
    if cookies:
        cmd += ["--cookies", cookies]
    elif cookies_browser:
        cmd += ["--cookies-from-browser", cookies_browser]
    if shutil.which("node"):
        cmd += ["--js-runtimes", "node"]   # YouTube necesita un runtime JS; deno por defecto, node si lo hay
    cmd.append(url)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out_bytes, _ = await asyncio.wait_for(proc.communicate(), DOWNLOAD_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError("la descarga tardó demasiado") from None
    log = out_bytes.decode(errors="replace")
    files = sorted(p for p in dest_dir.iterdir() if p.is_file())
    if proc.returncode != 0 or not files:
        if any(s in log.lower() for s in SIGN_IN):
            raise SignInRequired(log.strip()[-300:])
        if max_minutes and "does not pass filter" in log.lower():
            raise RuntimeError(f"el vídeo dura más de {max_minutes} min")
        raise RuntimeError(log.strip()[-300:] or "no se pudo descargar el audio")
    audio = files[0]
    title = re.sub(r"\s*\[[\w\-]+\]$", "", audio.stem).strip() or audio.stem
    return audio, title
