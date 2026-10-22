"""Pegamento de voz para el broker: escribir en la sesión por tmux y convertir el WAV del hub
a ogg/opus para mandarlo como nota de voz. La transcripción y la síntesis las hace el hub (hub.py)."""
import asyncio
import shutil
import tempfile
from pathlib import Path


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
