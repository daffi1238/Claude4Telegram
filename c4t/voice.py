"""Notas de voz de Telegram ↔ sesión de Claude Code, todo en local.

Entrada:  audio → Whisper (transcribe) → LLM local (repara dictado) → se escribe en el pane tmux
          de la sesión como una instrucción más.
Salida:   la respuesta de Claude → Piper (TTS) → ffmpeg (ogg/opus) → nota de voz en el tema.

Nada de esto sale del equipo: ni el audio, ni la contraseña, ni el texto pasan por servidores ajenos
(salvo Telegram, que es por donde llega y vuelve el propio audio)."""
import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

WHISPER_TIMEOUT = 300
LLM_TIMEOUT = 60
TTS_TIMEOUT = 120
MAX_TTS_CHARS = 1500          # no vale la pena narrar respuestas larguísimas
REPAIR_SYSTEM = (
    "Eres un corrector de dictado por voz en español. Recibes la transcripción automática de una nota "
    "de voz que un desarrollador le dicta a Claude Code. Devuelve SOLO el texto corregido: arregla "
    "puntuación, mayúsculas y errores evidentes de reconocimiento, respeta términos técnicos y nombres "
    "de ficheros, y no añadas comillas, explicaciones ni saludos. Si no entiendes algo, déjalo como está."
)


async def _run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, b"", b"timeout"
    return proc.returncode, out, err


def whisper_bin() -> str | None:
    return shutil.which("whisper") or (lambda p: str(p) if p.exists() else None)(
        Path.home() / ".local" / "bin" / "whisper")


async def transcribe(path: Path, model: str) -> str:
    """Transcribe el audio con Whisper (local). Devuelve texto o lanza RuntimeError."""
    binary = whisper_bin()
    if not binary:
        raise RuntimeError("whisper no está instalado")
    with tempfile.TemporaryDirectory() as tmp:
        code, _, err = await _run(
            [binary, str(path), "--language", "es", "--model", model, "--output_format", "txt",
             "--output_dir", tmp, "--fp16", "False", "--verbose", "False"], WHISPER_TIMEOUT)
        if code != 0:
            raise RuntimeError(f"whisper falló: {err.decode(errors='replace').strip()[-200:]}")
        txt = next(Path(tmp).glob("*.txt"), None)
        text = txt.read_text().strip() if txt else ""
    if not text:
        raise RuntimeError("no se entendió nada en el audio")
    return text


async def repair(text: str, url: str, key: str, model: str) -> str:
    """Repara la transcripción con el LLM local. Ante cualquier problema devuelve el texto original."""
    if not url:
        return text
    import aiohttp
    body = {"model": model, "temperature": 0.1, "max_tokens": 400, "stream": False,
            "messages": [{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": text}]}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        timeout = aiohttp.ClientTimeout(total=LLM_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(url, json=body, headers=headers) as r:
                data = await r.json()
        fixed = (data["choices"][0]["message"]["content"] or "").strip()
        return fixed.strip('"').strip() or text
    except Exception:
        return text  # el LLM es un lujo: si no está, se usa la transcripción tal cual


async def inject(tmux: dict | None, text: str) -> bool:
    """Escribe `text` en el pane tmux de la sesión y pulsa Enter, como si lo teclearas tú."""
    if not tmux or not tmux.get("pane"):
        return False
    base = ["tmux"]
    if tmux.get("socket"):
        base += ["-S", tmux["socket"]]
    pane = tmux["pane"]
    code, _, _ = await _run(base + ["send-keys", "-t", pane, "-l", text], 10)
    if code != 0:
        return False
    await asyncio.sleep(0.3)  # deja que Claude Code procese el texto antes del Enter
    code, _, _ = await _run(base + ["send-keys", "-t", pane, "Enter"], 10)
    return code == 0


async def synthesize(text: str, voice_model: str) -> bytes | None:
    """Convierte `text` en una nota de voz ogg/opus con Piper + ffmpeg. None si no se puede."""
    if not voice_model or not Path(voice_model).exists() or not shutil.which("ffmpeg"):
        return None
    local = Path(sys.executable).with_name("piper")   # piper del venv (el servicio no tiene su bin en PATH)
    piper = str(local) if local.exists() else shutil.which("piper")
    piper_cmd = [piper] if piper else [sys.executable, "-m", "piper"]
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "out.wav"
        code, _, err = await _run(piper_cmd + ["-m", voice_model, "-f", str(wav)],
                                  TTS_TIMEOUT, stdin=text[:MAX_TTS_CHARS].encode())
        if code != 0 or not wav.exists():
            return None
        ogg = Path(tmp) / "out.ogg"
        code, _, _ = await _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
                                 "-c:a", "libopus", "-b:a", "48k", str(ogg)], TTS_TIMEOUT)
        if code != 0 or not ogg.exists():
            return None
        return ogg.read_bytes()


def is_voice(content_type: str) -> bool:
    return content_type in ("voice", "audio", "video_note")
