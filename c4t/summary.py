"""Resumen del estado de una sesión a partir de su transcripción (sin usar ningún modelo)."""
import json
import os
import re
import time
from pathlib import Path

from .render import clip, esc, header

TAIL_BYTES = 2_000_000          # solo se lee el final de la transcripción
WORKING_WINDOW = 90             # s sin escribir en la transcripción → ya no está trabajando
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
PENDING_LABELS = {"perm": "un permiso 🔐", "question": "una respuesta ❓", "stop": "instrucciones 🏁"}
TODO_ICONS = {"completed": "✅", "in_progress": "⏳", "pending": "⬜"}


def one_line_command(cmd: str) -> str:
    """Comando en una línea, sin el `cd <ruta> &&` inicial que no aporta nada."""
    stripped = re.sub(r"^\s*(cd\s+\S+\s*(&&|;|\n)\s*)+", "", cmd)
    return re.sub(r"\s+", " ", stripped or cmd).strip()


def short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def md_bold(html_text: str) -> str:
    """Convierte **negrita** y `código` de markdown (ya escapado) a HTML de Telegram."""
    html_text = re.sub(r"^#{1,6}\s*(.+)$", lambda m: "**" + m.group(1).replace("**", "") + "**",
                       html_text, flags=re.MULTILINE)
    html_text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html_text)
    return re.sub(r"`([^`\n]+)`", r"<code>\1</code>", html_text)


def read_tail(path: Path) -> list[dict]:
    with path.open("rb") as f:
        size = f.seek(0, os.SEEK_END)
        f.seek(max(0, size - TAIL_BYTES))
        raw = f.read().decode(errors="replace").splitlines()
    if size > TAIL_BYTES:
        raw = raw[1:]  # la primera línea puede estar cortada
    entries = []
    for line in raw:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def analyze(transcript_path: str | None) -> dict:
    info = {"files": [], "commands": [], "todos": [], "last_text": "", "title": "", "prompt": "",
            "branch": "", "mode": "", "mtime": None}
    if not transcript_path or not Path(transcript_path).exists():
        return info
    path = Path(transcript_path)
    info["mtime"] = path.stat().st_mtime
    for e in read_tail(path):
        kind = e.get("type")
        if kind == "ai-title":
            info["title"] = e.get("aiTitle", "")
        elif kind == "last-prompt":
            info["prompt"] = e.get("lastPrompt", "")
        elif kind == "permission-mode":
            info["mode"] = e.get("permissionMode", "")
        elif kind == "assistant":
            info["branch"] = e.get("gitBranch") or info["branch"]
            for block in (e.get("message") or {}).get("content") or []:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text" and block.get("text", "").strip():
                    info["last_text"] = block["text"]
                elif block.get("type") == "tool_use":
                    name, inp = block.get("name", ""), block.get("input") or {}
                    if name in EDIT_TOOLS or name.endswith(("__edit", "__write", "__multi_edit")):
                        fp = inp.get("file_path") or inp.get("notebook_path")
                        if fp:
                            info["files"] = [f for f in info["files"] if f != fp] + [fp]
                    elif name == "Bash" and inp.get("command"):
                        info["commands"].append(one_line_command(inp["command"]))
                    elif name == "TodoWrite":
                        info["todos"] = inp.get("todos") or []
    return info


def ago(ts: float | None) -> str:
    if not ts:
        return "?"
    minutes = int((time.time() - ts) / 60)
    if minutes < 1:
        return "ahora mismo"
    if minutes < 60:
        return f"hace {minutes} min"
    return f"hace {minutes // 60} h {minutes % 60} min"


def status_line(sess: dict, info: dict, waiting: list[str]) -> str:
    if waiting:
        return "⏳ Esperando " + " y ".join(PENDING_LABELS[k] for k in waiting)
    if sess.get("ended"):
        return f"⚪ Sesión cerrada ({ago(sess.get('last_seen'))})"
    if info["mtime"] and time.time() - info["mtime"] < WORKING_WINDOW:
        return "🟢 Trabajando"
    return f"💤 Parada, última actividad {ago(info['mtime'] or sess.get('last_seen'))}"


def relative(path: str, cwd: str) -> str:
    try:
        return str(Path(path).relative_to(cwd)) if cwd else path
    except ValueError:
        return path


def transcript_of(sess: dict) -> str | None:
    if sess.get("transcript_path"):
        return sess["transcript_path"]
    found = list((Path.home() / ".claude" / "projects").glob(f"*/{sess['id']}.jsonl"))
    return str(found[0]) if found else None


def render_summary(sess: dict, waiting: list[str]) -> str:
    info = analyze(transcript_of(sess))
    lines = [f"📋 {header(sess['project'], sess['provider'], sess['id'])}"]
    if info["title"]:
        lines.append(f"<b>{esc(info['title'])}</b>")
    branch = info["branch"] if info["branch"] not in ("", "HEAD") else ""
    extra = " · ".join(x for x in (f"🌿 {esc(branch)}" if branch else "",
                                   f"modo {esc(info['mode'])}" if info["mode"] else "") if x)
    lines += [status_line(sess, info, waiting) + (f" · {extra}" if extra else ""), ""]
    if info["prompt"]:
        lines.append(f"🗣 <b>Tu última instrucción:</b> {esc(short(info['prompt'], 300))}")
    if info["todos"]:
        lines.append("\n<b>Tareas</b>")
        lines += [f"{TODO_ICONS.get(t.get('status'), '•')} {esc(short(t.get('content', ''), 120))}"
                  for t in info["todos"][:12]]
    if info["files"]:
        shown = info["files"][-8:]
        more = len(info["files"]) - len(shown)
        lines.append(f"\n<b>Ficheros modificados</b> ({len(info['files'])})")
        lines += [f"• <code>{esc(relative(f, sess.get('cwd', '')))}</code>" for f in shown]
        if more:
            lines.append(f"<i>… y {more} más</i>")
    if info["commands"]:
        lines.append("\n<b>Últimos comandos</b>")
        lines += [f"• <code>{esc(short(c, 90))}</code>" for c in info["commands"][-4:]]
    if info["last_text"]:
        lines.append(f"\n<b>Último mensaje de Claude</b>\n{md_bold(esc(short(info['last_text'], 700)))}")
    return clip("\n".join(lines), 4000)


def render_line(sess: dict, waiting: list[str]) -> str:
    """Versión de una línea para /resumen en el tema General."""
    info = analyze(transcript_of(sess))
    title =f" — {esc(clip(info['title'], 60))}" if info["title"] else ""
    return f"• {header(sess['project'], sess['provider'], sess['id'])}{title}\n   {status_line(sess, info, waiting)}"
