"""Formateo HTML de los mensajes de Telegram."""
import difflib
import html
import json
from pathlib import Path


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 20] + "\n… (recortado)"


def pre(text: str, limit: int = 2500, lang: str | None = None) -> str:
    body = esc(clip(text, limit))
    if lang:
        return f'<pre><code class="language-{lang}">{body}</code></pre>'
    return f"<pre>{body}</pre>"


def provider_of(base_url: str | None, model: str | None) -> str:
    base, model = (base_url or "").lower(), (model or "").lower()
    if "deepseek" in base or "deepseek" in model:
        return "DeepSeek"
    if base and "anthropic.com" not in base:
        host = base.split("//")[-1].split("/")[0]
        return host or "Otro"
    return "Claude"


def header(project: str, provider: str, session_id: str) -> str:
    return f"<b>{esc(project)}</b> · {esc(provider)} · <code>{esc(session_id[:6])}</code>"


def render_tool(tool: str, inp: dict) -> str:
    if tool == "Bash":
        out = pre(inp.get("command", ""), lang="bash")
        if inp.get("description"):
            out += f"\n<i>{esc(clip(inp['description'], 300))}</i>"
        return out
    if tool == "Edit":
        diff = difflib.unified_diff(
            str(inp.get("old_string", "")).splitlines(),
            str(inp.get("new_string", "")).splitlines(),
            lineterm="", n=1,
        )
        body = "\n".join(list(diff)[2:]) or "(sin cambios visibles)"
        return f"📄 <code>{esc(inp.get('file_path', '?'))}</code>\n" + pre(body, 2200, "diff")
    if tool == "Write":
        lines = str(inp.get("content", "")).splitlines()
        preview = "\n".join(lines[:40]) + (f"\n… (+{len(lines) - 40} líneas)" if len(lines) > 40 else "")
        return f"📝 <code>{esc(inp.get('file_path', '?'))}</code> ({len(lines)} líneas)\n" + pre(preview, 2200)
    if tool == "WebFetch":
        return f"🌐 {esc(inp.get('url', '?'))}"
    if tool == "WebSearch":
        return f"🔎 {esc(inp.get('query', '?'))}"
    return pre(json.dumps(inp, indent=2, ensure_ascii=False), 2500, "json")


def last_assistant_text(transcript_path: str | None) -> str:
    """Respaldo por si el hook Stop no trae last_assistant_message."""
    if not transcript_path or not Path(transcript_path).exists():
        return ""
    lines = Path(transcript_path).read_text(errors="replace").splitlines()[-300:]
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = entry.get("message", {}).get("content", [])
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if any(texts):
            return "\n".join(texts)
    return ""
