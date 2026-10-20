"""Resumen IA de una sesión: se pasa un extracto de la transcripción a `claude -p` sin herramientas.

Se lanza en la carpeta del proyecto, así usa la misma configuración (y el mismo proveedor, p. ej. DeepSeek)
que las sesiones de ese proyecto. C4T_DISABLE=1 evita que nuestros propios hooks se disparen.
"""
import asyncio
import os
import shutil
from pathlib import Path

from .summary import read_tail, short

DIGEST_CHARS = 40_000           # extracto máximo que se manda al modelo (lo más reciente)
TIMEOUT = 240

PROMPT = """Resume esta sesión de Claude Code para que el usuario la siga desde el móvil (Telegram).
Responde en español, en 15 líneas como máximo, con estos apartados en **negrita**:
**Objetivo** (qué pidió el usuario), **Hecho** (lo conseguido, concreto), **Estado** (en qué punto está ahora),
**Pendiente** (lo que falta, bloqueos o decisiones que esperan al usuario) y **Siguiente paso** (tu recomendación).
Sé concreto (ficheros, comandos, errores) y no inventes nada que no esté en el extracto.

Extracto de la sesión (lo más reciente al final):
"""


def claude_bin(configured: str = "") -> str | None:
    if configured:
        return configured
    found = shutil.which("claude")
    if found:
        return found
    fallback = Path.home() / ".local" / "bin" / "claude"  # el PATH de systemd no suele incluirlo
    return str(fallback) if fallback.exists() else None


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    return [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]


def digest(transcript_path: str) -> str:
    """Conversación en texto plano: instrucciones del usuario, respuestas y herramientas usadas."""
    lines = []
    for e in read_tail(Path(transcript_path)):
        message = e.get("message") or {}
        if e.get("type") == "user" and not e.get("isMeta"):
            for text in _texts(message.get("content")):
                text = text.strip()
                if not text or text.startswith(("<local-command", "<command-")):
                    continue
                label = "Resumen de la parte anterior" if e.get("isCompactSummary") else "Usuario"
                lines.append(f"{label}: {short(text, 4000 if e.get('isCompactSummary') else 1500)}")
        elif e.get("type") == "assistant":
            for block in message.get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    lines.append(f"Claude: {short(block['text'].strip(), 1500)}")
                elif block.get("type") == "tool_use":
                    inp = block.get("input") or {}
                    arg = inp.get("command") or inp.get("file_path") or inp.get("pattern") or inp.get("url") or ""
                    lines.append(f"[{block.get('name')}: {short(' '.join(str(arg).split()), 200)}]")
    text = "\n".join(lines)
    return text[-DIGEST_CHARS:]


async def summarize(sess: dict, transcript_path: str, model: str, binary: str = "") -> str:
    """Devuelve el resumen en markdown. Lanza RuntimeError con un mensaje legible si falla."""
    binary = claude_bin(binary)
    if not binary:
        raise RuntimeError("no encuentro el comando claude (define C4T_CLAUDE_BIN en .env)")
    extract = digest(transcript_path)
    if not extract.strip():
        raise RuntimeError("la transcripción aún no tiene contenido")
    cwd = sess.get("root") or sess.get("cwd")
    if not cwd or not Path(cwd).is_dir():
        cwd = str(Path.home())
    cmd = [binary, "-p", "--tools", "", "--no-session-persistence", "--output-format", "text"]
    if model and sess.get("provider") == "Claude":
        cmd += ["--model", model]  # con otros proveedores se usa el modelo que tenga configurado el proyecto
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=cwd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, env={**os.environ, "C4T_DISABLE": "1"})
    try:
        out, err = await asyncio.wait_for(proc.communicate((PROMPT + extract).encode()), TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        raise RuntimeError(f"el modelo no respondió en {TIMEOUT} s") from None
    if proc.returncode != 0 or not out.strip():
        detail = (err or out).decode(errors="replace").strip()[-300:] or f"código {proc.returncode}"
        raise RuntimeError(detail)
    return out.decode(errors="replace").strip()
