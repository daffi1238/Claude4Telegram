"""Pegamento de voz para el broker: escribir en la sesión por tmux, limpiar el texto para que
se lea bien en voz alta, y convertir el WAV del hub a ogg/opus para mandarlo como nota de voz.
La transcripción y la síntesis las hace el hub (hub.py)."""
import asyncio
import re
import shutil
import tempfile
from pathlib import Path

SPEAK_MAX = 1500   # caracteres máximos que se mandan a TTS (una respuesta larga aburre en audio)

# Emojis y pictogramas: se quitan para que el TTS no los deletree.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF\U00002B00-\U00002BFF\U00002300-\U000023FF️‍⃣]+")


def speakable(text: str) -> str:
    """Convierte la respuesta de Claude (markdown + emojis) en texto plano apto para leer en voz alta."""
    text = re.sub(r"```.*?```", " (bloque de código) ", text, flags=re.DOTALL)   # bloques de código
    text = re.sub(r"~~~.*?~~~", " (bloque de código) ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)                                      # `código` en línea
    text = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", text)                        # enlaces/imágenes → texto
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)             # encabezados
    text = re.sub(r"(\*\*|__)(.+?)\1", r"\2", text)                              # **negrita** / __negrita__
    text = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", text)               # *cursiva* / _cursiva_
    text = re.sub(r"^\s{0,3}>\s?", "", text, flags=re.MULTILINE)                  # citas
    text = re.sub(r"^\s*[-*•+]\s+", "", text, flags=re.MULTILINE)                 # viñetas
    text = re.sub(r"^\s*\d+[.)]\s+", "", text, flags=re.MULTILINE)                # listas numeradas
    text = re.sub(r"^\s*([-*_]\s?){3,}\s*$", "", text, flags=re.MULTILINE)        # líneas ---
    text = _EMOJI.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text).strip()
    return text[:SPEAK_MAX].strip()


async def _run(cmd: list[str], timeout: float, stdin: bytes | None = None) -> tuple[int, bytes]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, b"timeout"
    return proc.returncode, out


async def inject(tmux: dict | None, text: str) -> bool:
    """Escribe `text` en el pane tmux de la sesión y pulsa Enter, como si lo teclearas tú."""
    if not tmux or not tmux.get("pane"):
        return False
    base = ["tmux"]
    if tmux.get("socket"):
        base += ["-S", tmux["socket"]]
    pane = tmux["pane"]
    code, _ = await _run(base + ["send-keys", "-t", pane, "-l", text], 10)
    if code != 0:
        return False
    await asyncio.sleep(0.3)  # deja que Claude Code procese el texto antes del Enter
    code, _ = await _run(base + ["send-keys", "-t", pane, "Enter"], 10)
    return code == 0


async def to_opus(wav: bytes) -> bytes | None:
    """Convierte el WAV del hub a ogg/opus (lo que Telegram acepta como nota de voz). None si no hay ffmpeg."""
    if not shutil.which("ffmpeg") or not wav:
        return None
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.wav", Path(tmp) / "out.ogg"
        src.write_bytes(wav)
        code, _ = await _run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                              "-c:a", "libopus", "-b:a", "48k", str(dst)], 120)
        return dst.read_bytes() if code == 0 and dst.exists() else None


def is_image(m) -> bool:
    if m.photo:
        return True
    doc = m.document
    return bool(doc and (doc.mime_type or "").startswith("image/"))
