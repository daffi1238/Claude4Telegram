"""Broker: un único bot de Telegram (grupo con temas) + servidor HTTP local para los hooks.

Cada hook de Claude Code hace POST /hook y la petición queda abierta hasta que respondes
en Telegram, se agota el tiempo o vuelves al ordenador (/aqui). Sin decisión, Claude Code
sigue su flujo normal en el terminal.
"""
import asyncio
import base64
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (BotCommand, BufferedInputFile, CallbackQuery,
                           InlineKeyboardButton, InlineKeyboardMarkup, Message,
                           ReactionTypeEmoji)
from aiohttp import web

from .config import Config
from .render import clip, esc, header, last_assistant_text, pre, provider_of, render_tool
from .inbox import describe_for_claude, git_root, inbox_dir, key_for, session_context, stamped
from .inbox import process as process_incoming
from .media import MAX_UPLOAD, send_media
from .presence import typed_since
from .state import State
from .hub import Hub, HISTORY_MESSAGES
from . import voice
from .summary import ago, md_bold, render_line, render_summary, transcript_of
from .ai_summary import summarize

log = logging.getLogger("c4t")

TOPIC_COLORS = {"Claude": 16766590, "DeepSeek": 7322096}  # amarillo / azul
DEFAULT_TOPIC_COLOR = 13338331                               # violeta
QUIET_NOTIFICATIONS = {"permission_prompt", "idle_prompt"}   # ya cubiertos por PermissionRequest / Stop
TELEGRAM_DOWNLOAD_LIMIT = 20 * 1024 * 1024                    # getFile de la Bot API
TERMINAL_POLL = 1.0                                          # s entre comprobaciones de tmux
WAIT_MARGIN = 15                                             # s antes de que Claude Code mate el hook
REPAIR_SYSTEM = (
    "Eres un corrector de dictado por voz en español. Recibes la transcripción automática de una nota "
    "de voz que un desarrollador le dicta a Claude Code. Devuelve SOLO el texto corregido: arregla "
    "puntuación, mayúsculas y errores evidentes de reconocimiento, respeta términos técnicos y nombres "
    "de ficheros, y no añadas comillas, explicaciones ni saludos. Si no entiendes algo, déjalo como está.")
CHAT_SYSTEM = (
    "Eres un asistente útil y conciso que responde por Telegram al dueño de este equipo. Responde en "
    "español salvo que te escriban en otro idioma, ve al grano y usa texto plano (sin markdown pesado).")

HELP = (
    "<b>claude4Telegram</b>\n"
    "/fuera — las decisiones de Claude Code llegan aquí\n"
    "/aqui — vuelves al ordenador (lo pendiente pasa al terminal)\n"
    "/estado — sesiones activas y peticiones pendientes\n"
    "/resumen — qué está haciendo cada sesión (o escribe cualquier cosa en el tema); "
    "con 🧠 Resumen IA, un modelo te lo resume\n"
    "/chat — conversa con el modelo local (responde a su respuesta para seguir)\n"
    "/id — ids de chat, usuario y tema\n\n"
    "Para texto libre, <b>responde</b> al mensaje del bot (o escribe en el tema si solo hay una pregunta pendiente)."
)


def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


@dataclass
class Pending:
    id: str
    kind: str                 # perm | question | stop
    session_id: str
    project: str
    thread_id: int | None
    future: asyncio.Future
    text: str = ""            # HTML enviado, para poder editarlo al cerrar
    message_id: int | None = None
    data: dict = field(default_factory=dict)
    tmux: dict | None = None  # pane de la sesión, para soltar el hook si tecleas en el terminal
    created: float = field(default_factory=time.time)


class Broker:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.bot = Bot(cfg.token, default=DefaultBotProperties(
            parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
        self.state = State(cfg.state_dir / "state.json")
        self.pending: dict[str, Pending] = {}
        self.msg_to_req: dict[int, str] = {}
        self.topic_lock = asyncio.Lock()
        self.danger = [re.compile(p, re.IGNORECASE) for p in cfg.danger_patterns]
        self.ai_running: set[str] = set()   # sesiones con un 🧠 Resumen IA en marcha
        self.voice_reply: dict[str, int] = {}  # session_id -> thread_id al que narrar la próxima respuesta
        self.hub = Hub(cfg.hub_url, cfg.hub_key, cfg.chat_model, cfg.tts_voice)
        self.chat_history: dict[int, list[dict]] = {}  # thread_id -> conversación con el hub (/chat)
        self.chat_msgs: dict[int, int] = {}            # message_id de una respuesta de /chat -> thread_id

    # ------------------------------------------------------------------ Telegram helpers

    def allowed(self, chat_id: int, user_id: int | None) -> bool:
        return self.cfg.configured and chat_id == self.cfg.chat_id and user_id in self.cfg.owner_ids

    async def topic_for(self, project: str, provider: str) -> int:
        async with self.topic_lock:
            if project not in self.state.topics:
                topic = await self.bot.create_forum_topic(
                    self.cfg.chat_id, name=f"{self.cfg.topic_prefix}{project}"[:128],
                    icon_color=TOPIC_COLORS.get(provider, DEFAULT_TOPIC_COLOR))
                self.state.topics[project] = topic.message_thread_id
                self.state.save()
            return self.state.topics[project]

    async def send(self, project: str, provider: str, text: str, markup=None, silent=False) -> Message:
        for attempt in range(2):
            thread = await self.topic_for(project, provider)
            try:
                return await self.bot.send_message(
                    self.cfg.chat_id, text, message_thread_id=thread,
                    reply_markup=markup, disable_notification=silent)
            except TelegramBadRequest as e:
                # Tema borrado o cerrado a mano: se crea uno nuevo.
                if attempt == 0 and ("thread" in str(e).lower() or "topic" in str(e).lower()):
                    self.state.topics.pop(project, None)
                    continue
                raise

    async def send_file(self, project: str, provider: str, name: str, content: str) -> None:
        thread = await self.topic_for(project, provider)
        await self.bot.send_document(
            self.cfg.chat_id, BufferedInputFile(content.encode(), filename=name),
            message_thread_id=thread, disable_notification=True)

    # ------------------------------------------------------------------ pendientes

    async def ask(self, kind: str, sess: dict, text: str, rows, data: dict | None = None) -> Pending:
        """Envía una pregunta con botones; "{id}" en su callback_data se sustituye por el id de la petición."""
        req = Pending(
            id=secrets.token_hex(4), kind=kind, session_id=sess["id"], project=sess["project"],
            thread_id=None, future=asyncio.get_running_loop().create_future(), text=text, data=data or {},
            tmux=sess.get("tmux"))
        markup = InlineKeyboardMarkup(inline_keyboard=[
            [btn(b.text, b.callback_data.replace("{id}", req.id)) for b in row] for row in rows])
        msg = await self.send(sess["project"], sess["provider"], text, markup)
        req.message_id, req.thread_id = msg.message_id, msg.message_thread_id
        self.pending[req.id] = req
        self.msg_to_req[msg.message_id] = req.id
        return req

    async def wait(self, req: Pending, timeout: float):
        """Espera la respuesta de Telegram; gana el terminal si tecleas en el pane de la sesión."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(timeout, 5)
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    await self.close_message(req, "⌛ Sin respuesta: decide en el ordenador")
                    return None
                try:
                    return await asyncio.wait_for(asyncio.shield(req.future), min(TERMINAL_POLL, remaining))
                except asyncio.TimeoutError:
                    if await typed_since(req.tmux, req.created):
                        await self.close_message(req, "🖥 Respondido en el ordenador")
                        return None
        except asyncio.CancelledError:
            # Claude Code cortó el hook (p. ej. se respondió en el terminal).
            asyncio.create_task(self.close_message(req, "🖥 Resuelto en el ordenador"))
            raise
        finally:
            self.pending.pop(req.id, None)
            self.msg_to_req.pop(req.message_id, None)

    async def resolve(self, req: Pending, value, label: str) -> None:
        if not req.future.done():
            req.future.set_result(value)
            await self.close_message(req, label)

    async def close_message(self, req: Pending, label: str) -> None:
        try:
            await self.bot.edit_message_text(
                req.text + f"\n\n<b>→ {esc(clip(label, 300))}</b>",
                chat_id=self.cfg.chat_id, message_id=req.message_id, reply_markup=None)
        except TelegramBadRequest as e:
            log.debug("no se pudo editar %s: %s", req.message_id, e)

    # ------------------------------------------------------------------ HTTP (envío desde proyectos)

    async def http_send(self, request: web.Request) -> web.Response:
        """Mensajes y archivos que un proyecto manda al tema de Telegram (c4t_send.py). No espera respuesta."""
        if not secrets.compare_digest(request.headers.get("X-C4T-Secret", ""), self.cfg.secret):
            return web.json_response({"error": "forbidden"}, status=403)
        if not self.cfg.configured:
            return web.json_response({"error": "broker sin configurar"}, status=503)
        body = await request.json()
        project = body.get("project") or "sin-proyecto"
        last = max((s for s in self.state.sessions.values() if s["project"] == project),
                   key=lambda s: s.get("last_seen", 0), default={})
        provider = last.get("provider", "Claude")
        caption = f"📎 <b>{esc(project)}</b>" + (f"\n{esc(body['text'])}" if body.get("text") else "")
        try:
            if not body.get("data_b64"):
                await self.send(project, provider, caption)
                return web.json_response({"ok": True})
            data = base64.b64decode(body["data_b64"])
            if len(data) > MAX_UPLOAD:
                return web.json_response({"error": "el archivo supera 50 MB"}, status=413)
            thread = await self.topic_for(project, provider)
            msg = await send_media(self.bot, self.cfg.chat_id, thread, body.get("filename") or "archivo",
                                   data, caption, body.get("kind"))
            return web.json_response({"ok": True, "message_id": msg.message_id})
        except TelegramBadRequest as e:
            return web.json_response({"error": str(e)}, status=502)

    # ------------------------------------------------------------------ HTTP (hooks)

    async def http_hook(self, request: web.Request) -> web.Response:
        if not secrets.compare_digest(request.headers.get("X-C4T-Secret", ""), self.cfg.secret):
            return web.json_response({"error": "forbidden"}, status=403)
        body = await request.json()
        try:
            output = await self.handle(body)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("error procesando hook")
            output = None
        return web.json_response({"output": output})

    def touch_session(self, payload: dict, body: dict) -> dict:
        sid = payload.get("session_id", "unknown")
        env = body.get("env") or {}
        sess = self.state.sessions.get(sid, {"id": sid, "started": time.time()})
        if payload.get("model"):
            sess["model"] = payload["model"]
        sess.update(
            project=body.get("project") or "sin-proyecto",
            cwd=payload.get("cwd", ""),
            root=body.get("project_root") or sess.get("root") or payload.get("cwd", ""),
            transcript_path=payload.get("transcript_path") or sess.get("transcript_path"),
            base_url=env.get("ANTHROPIC_BASE_URL") or sess.get("base_url"),
            last_seen=time.time(),
        )
        sess.setdefault("model", env.get("ANTHROPIC_MODEL"))
        if env.get("TMUX_PANE"):
            sess["tmux"] = {"socket": (env.get("TMUX") or "").split(",")[0], "pane": env["TMUX_PANE"]}
        sess["provider"] = provider_of(sess.get("base_url"), sess.get("model"))
        self.state.sessions[sid] = sess
        return sess

    async def handle(self, body: dict):
        if not self.cfg.configured:
            return None
        payload = body.get("payload") or {}
        event = payload.get("hook_event_name")
        sess = self.touch_session(payload, body)
        timeout = float(body.get("timeout", 600)) - WAIT_MARGIN
        head = header(sess["project"], sess["provider"], sess["id"])

        if event == "SessionStart":
            sess["ended"] = False
            self.state.save()
            model = f" · {esc(sess['model'])}" if sess.get("model") else ""
            await self.send(sess["project"], sess["provider"],
                            f"🟢 {head}\nSesión ({esc(payload.get('source', 'startup'))}){model}", silent=True)
            context = session_context(sess["project"], sess["root"])
            sess["context_sent"] = True
            notices = self.state.inbox.pop(sess["project"], [])
            if notices:
                self.state.save()
                context += "\n\n" + "\n\n".join(notices)
            return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
        if event == "SessionEnd":
            sess["ended"] = True
            self.state.save()
            await self.send(sess["project"], sess["provider"],
                            f"⚪ {head}\nSesión cerrada ({esc(payload.get('reason', ''))})", silent=True)
            return None

        self.state.save()
        # Archivos recibidos por Telegram que Claude aún no conoce: se entregan en cuanto la sesión
        # da señales (al escribirle tú o al terminar una tarea), estés fuera o delante del ordenador.
        if event == "UserPromptSubmit":
            # Sesiones abiertas antes de instalar Claude4Telegram: se enteran en tu siguiente mensaje.
            parts = [] if sess.get("context_sent") else [session_context(sess["project"], sess["root"])]
            sess["context_sent"] = True
            parts += self.state.inbox.pop(sess["project"], [])
            self.state.save()
            if parts:
                return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                               "additionalContext": "\n\n".join(parts)}}
            return None
        if event == "Stop" and sess["id"] in self.voice_reply:
            # La tarea venía de una nota de voz: su respuesta se narra de vuelta al tema.
            thread = self.voice_reply.pop(sess["id"])
            msg = payload.get("last_assistant_message") or last_assistant_text(payload.get("transcript_path"))
            if msg:
                asyncio.create_task(self.narrate_reply(sess, thread, msg))
        if event == "Stop" and self.state.inbox.get(sess["project"]):
            notices = "\n\n".join(self.state.inbox.pop(sess["project"]))
            self.state.save()
            return {"decision": "block", "reason": notices}
        if not self.state.away:
            return None
        if event in ("PermissionRequest", "PreToolUse", "Stop") and \
                await typed_since(sess.get("tmux"), time.time() - self.cfg.presence_window):
            return None  # estás tecleando en esa sesión: decide el terminal
        if event == "PermissionRequest":
            return await self.on_permission(payload, sess, head, timeout)
        if event == "PreToolUse" and payload.get("tool_name") == "AskUserQuestion":
            return await self.on_question(payload, sess, head, timeout)
        if event == "Stop":
            return await self.on_stop(payload, sess, head, timeout)
        if event == "Notification" and payload.get("notification_type") not in QUIET_NOTIFICATIONS:
            await self.send(sess["project"], sess["provider"],
                            f"🔔 {head}\n{esc(clip(payload.get('message', ''), 1500))}")
        return None

    # ------------------------------------------------------------------ eventos

    def is_dangerous(self, tool: str, inp: dict) -> bool:
        return tool == "Bash" and any(p.search(inp.get("command", "")) for p in self.danger)

    async def on_permission(self, payload: dict, sess: dict, head: str, timeout: float):
        tool, inp = payload.get("tool_name", "?"), payload.get("tool_input") or {}
        if tool == "AskUserQuestion":
            return None  # lo gestiona PreToolUse
        danger = self.is_dangerous(tool, inp)
        suggestions = payload.get("permission_suggestions") or []
        text = f"🔐 {head}\n<b>Permiso: {esc(tool)}</b>\n{render_tool(tool, inp)}"
        if danger:
            text += "\n\n⚠️ <b>Comando peligroso</b>: solo se puede aprobar en el ordenador."
        text += "\n\n<i>Responde a este mensaje para denegar indicando qué hacer en su lugar.</i>"
        rid = "{id}"
        rows = [] if danger else [[btn("✅ Permitir", f"{rid}:a")]
                                  + ([btn("🔁 Permitir siempre", f"{rid}:A")] if suggestions else [])]
        rows.append([btn("❌ Denegar", f"{rid}:d"), btn("🖥 En el PC", f"{rid}:t")])
        req = await self.ask("perm", sess, text, rows, {"suggestions": suggestions})
        res = await self.wait(req, timeout)
        if res is None:
            return None
        decision = {"behavior": "allow"}
        if res == "always":
            decision["updatedPermissions"] = suggestions
        elif res == "deny":
            decision = {"behavior": "deny", "message": "El usuario lo ha denegado desde Telegram."}
        elif isinstance(res, dict):
            decision = {"behavior": "deny",
                        "message": f"El usuario lo ha denegado desde Telegram con este comentario: {res['text']}"}
        return {"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}

    async def on_question(self, payload: dict, sess: dict, head: str, timeout: float):
        inp = payload.get("tool_input") or {}
        questions = inp.get("questions") or []
        deadline = time.time() + timeout
        answers: dict[str, str] = {}
        for i, q in enumerate(questions, 1):
            opts = q.get("options") or []
            lines = [f"❓ {head}\n<b>{esc(q.get('header', 'Pregunta'))}</b> ({i}/{len(questions)})",
                     esc(q.get("question", "")), ""]
            lines += [f"<b>{n}. {esc(o.get('label', ''))}</b> — {esc(o.get('description', ''))}"
                      for n, o in enumerate(opts, 1)]
            lines.append("\n<i>Responde a este mensaje para dar otra respuesta.</i>")
            data = {"q": q, "sel": set()}
            req = await self.ask("question", sess, clip("\n".join(lines), 3800),
                                          self.question_rows(q, set()), data)
            res = await self.wait(req, deadline - time.time())
            if res is None:
                return None  # el resto de la pregunta se contesta en el terminal
            answers[q.get("question", "")] = res["text"] if isinstance(res, dict) else res
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "permissionDecisionReason": "Respondido desde Telegram",
            "updatedInput": {**inp, "answers": answers},
        }}

    @staticmethod
    def question_rows(q: dict, sel: set[int], rid: str = "{id}") -> list[list[InlineKeyboardButton]]:
        multi = q.get("multiSelect", False)
        rows = []
        for n, o in enumerate(q.get("options") or []):
            label = o.get("label", str(n + 1))
            if multi:
                label = ("☑️ " if n in sel else "⬜ ") + label
            rows.append([btn(clip(label, 60), f"{rid}:o{n}")])
        tail = [btn("✔️ Confirmar", f"{rid}:c")] if multi else []
        rows.append(tail + [btn("🖥 En el PC", f"{rid}:t")])
        return rows

    async def on_stop(self, payload: dict, sess: dict, head: str, timeout: float):
        msg = payload.get("last_assistant_message") or last_assistant_text(payload.get("transcript_path"))
        text = f"🏁 {head}\n<b>Claude ha terminado</b>\n\n{esc(clip(msg, 3000)) or '<i>(sin texto)</i>'}"
        text += "\n\n<i>Responde a este mensaje con la siguiente instrucción.</i>"
        rows = [[btn("▶️ Sigue trabajando", "{id}:g"), btn("✅ Listo, para aquí", "{id}:s")]]
        req = await self.ask("stop", sess, text, rows)
        if len(msg) > 3000:
            await self.send_file(sess["project"], sess["provider"], "respuesta.md", msg)
        res = await self.wait(req, timeout)
        if res == "go":
            return {"decision": "block",
                    "reason": "El usuario (vía Telegram) pide que continúes con el siguiente paso lógico."}
        if isinstance(res, dict):
            return {"decision": "block", "reason": f"Nueva instrucción del usuario vía Telegram:\n{res['text']}"}
        return None

    # ------------------------------------------------------------------ Telegram handlers

    def router(self) -> Router:
        r = Router()

        @r.message(Command("id"))
        async def cmd_id(m: Message):
            await m.reply(f"chat_id: <code>{m.chat.id}</code>\nuser_id: <code>{m.from_user.id}</code>\n"
                          f"thread_id: <code>{m.message_thread_id}</code>\n"
                          f"forum: {'sí' if m.chat.is_forum else 'no (activa los temas)'}")

        @r.message(Command("start", "help", "ayuda"))
        async def cmd_help(m: Message):
            if self.allowed(m.chat.id, m.from_user.id):
                await m.reply(HELP)

        @r.message(Command("fuera", "away"))
        async def cmd_away(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            self.state.away = True
            self.state.save()
            await m.reply("🚶 <b>Modo fuera</b>: permisos, preguntas y finales de tarea llegarán aquí.")

        @r.message(Command("aqui", "back"))
        async def cmd_back(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            self.state.away = False
            self.state.save()
            pend = list(self.pending.values())
            for req in pend:
                await self.resolve(req, None, "🖥 Pasado al ordenador")
            await m.reply(f"🖥 <b>Modo ordenador</b>. {len(pend)} pendiente(s) pasadas al terminal.")

        @r.message(Command("estado", "status"))
        async def cmd_status(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            lines = [f"Modo: <b>{'🚶 fuera' if self.state.away else '🖥 ordenador'}</b>", ""]
            active = [s for s in self.state.sessions.values()
                      if not s.get("ended") and time.time() - s.get("last_seen", 0) < 86400]
            for s in sorted(active, key=lambda s: -s["last_seen"]):
                waiting = [p.kind for p in self.pending.values() if p.session_id == s["id"]]
                ago = int((time.time() - s["last_seen"]) / 60)
                lines.append(f"• {header(s['project'], s['provider'], s['id'])} — hace {ago} min"
                             + (f" — ⏳ {', '.join(waiting)}" if waiting else ""))
            if not active:
                lines.append("<i>Sin sesiones activas en las últimas 24 h.</i>")
            await m.reply("\n".join(lines))

        @r.message(F.document | F.photo | F.voice | F.audio | F.video | F.video_note | F.animation)
        async def on_file(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            if (m.voice or m.video_note) and self.hub.enabled:
                await self.receive_voice(m)   # nota de voz → instrucción
            else:
                await self.receive_file(m)    # cualquier otro archivo (con OCR si es imagen)

        @r.message(Command("resumen", "summary"))
        async def cmd_summary(m: Message):
            if self.allowed(m.chat.id, m.from_user.id):
                await self.reply_summary(m)

        @r.message(Command("chat"))
        async def cmd_chat(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            if not self.hub.enabled:
                await m.reply("El hub de modelos no está configurado (pon <code>C4T_HUB_URL</code> en el .env).")
                return
            prompt = (m.text or "").partition(" ")[2].strip()
            if not prompt:
                await m.reply("Uso: <code>/chat tu mensaje</code>. Luego <b>responde</b> a mi respuesta para seguir.")
                return
            await self.do_chat(m, prompt)

        @r.message(F.text & ~F.text.startswith("/"))
        async def on_text(m: Message):
            if not self.allowed(m.chat.id, m.from_user.id):
                return
            # Responder a una respuesta de /chat continúa esa conversación con el hub.
            if m.reply_to_message and m.reply_to_message.message_id in self.chat_msgs:
                await self.do_chat(m, m.text)
                return
            req = None
            if m.reply_to_message and m.reply_to_message.message_id in self.msg_to_req:
                req = self.pending.get(self.msg_to_req[m.reply_to_message.message_id])
            if req is None:
                in_thread = [p for p in self.pending.values() if p.thread_id == m.message_thread_id]
                if len(in_thread) == 1:
                    req = in_thread[0]
                elif in_thread:
                    await m.reply("Hay varias peticiones pendientes en este tema: <b>responde</b> al mensaje concreto.")
                    return
            if req is None:
                # Nada pendiente: el texto se toma como "¿cómo vas?". Con varios equipos en el
                # mismo grupo, solo contesta el broker dueño del tema.
                if m.message_thread_id in self.state.topics.values():
                    await self.reply_summary(m)
                return
            labels = {"perm": "❌ Denegado con comentario", "question": "💬 Respuesta", "stop": "▶️ Nueva instrucción"}
            await self.resolve(req, {"text": m.text}, f"{labels[req.kind]}: {m.text}")
            await m.react([ReactionTypeEmoji(emoji="👍")])

        @r.callback_query()
        async def on_button(cq: CallbackQuery):
            if not self.allowed(cq.message.chat.id, cq.from_user.id):
                await self.ack(cq, "No autorizado")
                return
            req_id, _, action = (cq.data or "").partition(":")
            if req_id in ("sum", "ai"):
                sess = next((s for s in self.state.sessions.values() if s["id"].startswith(action)), None)
                if sess is None:
                    await self.ack(cq, "Ya no tengo esa sesión")
                elif req_id == "sum":
                    await cq.message.reply(render_summary(sess, self.waiting_for(sess["id"])),
                                           reply_markup=self.summary_markup(sess))
                    await self.ack(cq)
                else:
                    await self.ai_summary(cq, sess)
                return
            req = self.pending.get(req_id)
            if req is None:
                await self.ack(cq, "Esta petición ya no está activa")
                return
            if req.kind == "perm":
                values = {"a": ("allow", "✅ Permitido"), "A": ("always", "🔁 Permitido siempre"),
                          "d": ("deny", "❌ Denegado"), "t": (None, "🖥 En el ordenador")}
                value, label = values[action]
                await self.resolve(req, value, label)
            elif req.kind == "stop":
                value, label = {"g": ("go", "▶️ Sigue trabajando"), "s": (None, "✅ Listo, parado")}[action]
                await self.resolve(req, value, label)
            elif req.kind == "question":
                await self.on_question_button(cq, req, action)
                return
            await self.ack(cq)

        return r

    @staticmethod
    async def ack(cq: CallbackQuery, text: str | None = None) -> None:
        try:
            await cq.answer(text)
        except TelegramBadRequest:
            pass  # botón pulsado hace demasiado (p. ej. antes de reiniciar el broker)

    # ------------------------------------------------------------------ archivos desde Telegram

    def project_of_thread(self, thread_id: int | None) -> str | None:
        return next((p for p, t in self.state.topics.items() if t == thread_id), None)

    def best_session(self, project: str) -> dict | None:
        """Sesión más reciente del proyecto con carpeta conocida (dedúcela del cwd si hace falta)."""
        sess = max((s for s in self.state.sessions.values()
                    if s["project"] == project and (s.get("root") or s.get("cwd"))),
                   key=lambda s: s.get("last_seen", 0), default=None)
        if sess is not None and not sess.get("root"):
            # Sesión registrada antes de que el hook mandara la raíz: se deduce de su cwd.
            root = git_root(sess["cwd"])
            sess["root"] = str(root if root.name == project else Path(sess["cwd"]))
        return sess

    async def receive_file(self, m: Message) -> None:
        project = self.project_of_thread(m.message_thread_id)
        if project is None:
            if m.message_thread_id is None:
                await m.reply("Envía el archivo en el tema del proyecto al que va dirigido.")
            return  # tema de otro equipo
        sess = self.best_session(project)
        if sess is None or not Path(sess["root"]).is_dir():
            await m.reply("Aún no sé en qué carpeta está este proyecto: abre una sesión de Claude Code en él.")
            return
        media = m.document or m.audio or m.video or m.animation or m.voice or m.video_note or (m.photo or [None])[-1]
        default_names = {"voice": "nota_de_voz.ogg", "video_note": "video_nota.mp4", "photo": "foto.jpg"}
        name = getattr(media, "file_name", None) or default_names.get(m.content_type, f"{m.content_type}.bin")
        if (media.file_size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
            await m.reply("Telegram no deja a los bots descargar archivos de más de 20 MB. "
                          "Pártelo o comprímelo (un .7z cifrado también vale).")
            return
        inbox = inbox_dir(sess["root"])
        dest = stamped(inbox, name)
        await self.bot.download(media, destination=dest)
        item = await process_incoming(dest, key_for(self.cfg.state_dir / "keys", project))
        rel = esc(item.path.relative_to(Path(sess["root"])))
        size = sum(p.stat().st_size for p in item.path.rglob("*") if p.is_file()) if item.path.is_dir() \
            else item.path.stat().st_size
        icon = {"decrypted": " · 🔓 descifrado", "plain": ""}.get(item.status, "")
        if not item.usable:
            key_cmd = f"python3 {Path(__file__).resolve().parent.parent / 'hooks' / 'c4t_key.py'} --proyecto \"{project}\""
            reason = {"no_key": "🔒 Está cifrado y este proyecto no tiene contraseña configurada.",
                      "wrong_key": "🔒 La contraseña configurada no lo descifra.",
                      "error": f"⚠️ No se pudo descifrar: {esc(item.detail)}"}[item.status]
            await m.reply(f"📥 Guardado en <code>{rel}</code>, pero Claude no lo recibirá todavía.\n{reason}\n"
                          f"En el ordenador: <code>{esc(key_cmd)}</code> y reenvíalo.")
            return
        notice = describe_for_claude(item, m.caption)
        ocr_text = ""
        if self.hub.enabled and voice.is_image(m) and item.path.is_file():
            try:
                ocr_text = await self.hub.ocr(item.path.read_bytes(), item.path.name)
            except Exception as e:
                log.warning("OCR en %s: %s", project, e)
            if ocr_text:
                notice += f"\n\nTexto extraído de la imagen por OCR (puede tener erratas):\n{ocr_text}"
        stop = self.pending_stop(m)
        if stop:
            await self.resolve(stop, {"text": notice}, "📥 Archivo enviado a Claude")
            where = "Claude ya lo tiene y sigue trabajando."
        else:
            self.state.inbox.setdefault(project, []).append(notice)
            self.state.save()
            where = "Claude lo recibirá en cuanto termine lo que está haciendo o le escribas."
        extra = f"\n📝 <b>OCR:</b> {esc(clip(ocr_text, 500))}" if ocr_text else ""
        await m.reply(f"📥 <code>{rel}</code> ({size // 1024 or 1} KB){icon}\n{where}{extra}")

    # ------------------------------------------------------------------ notas de voz

    def pending_stop(self, m: Message) -> "Pending | None":
        """La petición de fin de tarea a la que responde este mensaje (si contesta a una, o si es la única)."""
        if m.reply_to_message and m.reply_to_message.message_id in self.msg_to_req:
            req = self.pending.get(self.msg_to_req[m.reply_to_message.message_id])
            if req and req.kind == "stop":
                return req
        stops = [p for p in self.pending.values() if p.kind == "stop" and p.thread_id == m.message_thread_id]
        return stops[0] if len(stops) == 1 else None

    async def download_bytes(self, media) -> bytes:
        buf = await self.bot.download(media)   # destino None → BytesIO en memoria
        return buf.read()

    async def repair(self, text: str) -> str:
        """Repara la transcripción con el chat del hub. Ante cualquier fallo devuelve el texto original."""
        if not self.hub.enabled:
            return text
        try:
            fixed = await self.hub.chat(
                [{"role": "system", "content": REPAIR_SYSTEM}, {"role": "user", "content": text}], max_tokens=400)
            return fixed.strip('"').strip() or text
        except Exception as e:
            log.warning("reparar transcripción: %s", e)
            return text

    async def receive_voice(self, m: Message) -> None:
        """Nota de voz → transcripción (hub STT) → reparada (hub chat) → instrucción en la sesión."""
        project = self.project_of_thread(m.message_thread_id)
        if project is None:
            if m.message_thread_id is None:
                await m.reply("Envía la nota de voz en el tema del proyecto.")
            return
        sess = self.best_session(project)
        if sess is None:
            await m.reply("Aún no sé en qué carpeta está este proyecto: abre una sesión de Claude Code en él.")
            return
        media = m.voice or m.video_note
        if (media.file_size or 0) > TELEGRAM_DOWNLOAD_LIMIT:
            await m.reply("La nota de voz supera los 20 MB que un bot puede descargar.")
            return
        note = await m.reply("🎧 Transcribiendo…")
        try:
            audio = await self.download_bytes(media)
            text = await self.hub.transcribe(audio, "nota.ogg")
            if not text:
                raise RuntimeError("no se entendió nada en el audio")
            instruction = await self.repair(text)
        except Exception as e:
            log.warning("voz en %s: %s", project, e)
            await self.edit_or_reply(note, m, f"⚠️ No pude procesar la nota de voz: {esc(clip(str(e), 300))}")
            return
        for_claude = ("Instrucción dictada por voz desde Telegram (transcrita automáticamente, "
                      f"puede tener erratas):\n{instruction}")
        stop = self.pending_stop(m)
        if stop:
            await self.resolve(stop, {"text": for_claude}, "🎙 Instrucción de voz enviada")
            where = "➡️ Enviada; Claude retoma la tarea."
        elif await voice.inject(sess.get("tmux"), for_claude):
            where = "➡️ Escrita en la sesión."
        else:
            self.state.inbox.setdefault(project, []).append(for_claude)
            self.state.save()
            where = "➡️ Claude la recibirá cuando termine lo que hace o le escribas (sin tmux no puedo teclear en vivo)."
        self.voice_reply[sess["id"]] = m.message_thread_id
        await self.edit_or_reply(note, m, f"🎙 <b>Entendí:</b> {esc(clip(instruction, 1500))}\n{where}")

    @staticmethod
    async def edit_or_reply(note: Message, m: Message, text: str) -> Message:
        try:
            return await note.edit_text(text)
        except TelegramBadRequest:
            return await m.reply(text)

    async def do_chat(self, m: Message, prompt: str) -> None:
        """Conversación con el hub en este tema. Responder a la respuesta la continúa."""
        thread = m.message_thread_id or 0
        history = self.chat_history.setdefault(thread, [])
        history.append({"role": "user", "content": clip(prompt, 4000)})
        note = await m.reply("💬 …")
        try:
            answer = await self.hub.chat([{"role": "system", "content": CHAT_SYSTEM}] + history[-HISTORY_MESSAGES:])
        except Exception as e:
            history.pop()
            await self.edit_or_reply(note, m, f"⚠️ El hub no respondió: {esc(clip(str(e), 300))}")
            return
        history.append({"role": "assistant", "content": answer})
        self.chat_history[thread] = history[-HISTORY_MESSAGES * 2:]   # acota el contexto que guardamos
        sent = await self.edit_or_reply(note, m, f"💬 {md_bold(esc(clip(answer, 3500)))}")
        self.chat_msgs[sent.message_id] = thread   # para continuar al responder a este mensaje

    async def narrate_reply(self, sess: dict, thread_id: int, message: str) -> None:
        """Convierte la respuesta de Claude en una nota de voz (hub TTS) y la manda al tema."""
        spoken = voice.speakable(message)
        if not spoken:
            return
        try:
            audio = await voice.to_opus(await self.hub.speak(spoken))
            if audio:
                await send_media(self.bot, self.cfg.chat_id, thread_id, "respuesta.ogg", audio,
                                 f"🔊 <b>{esc(sess['project'])}</b>", "voice")
        except Exception as e:
            log.warning("TTS en %s: %s", sess["project"], e)

    # ------------------------------------------------------------------ resúmenes

    def waiting_for(self, session_id: str) -> list[str]:
        return [p.kind for p in self.pending.values() if p.session_id == session_id]

    def recent_sessions(self, project: str | None = None) -> list[dict]:
        """Sesiones abiertas en las últimas 24 h; si un proyecto no tiene ninguna, su última sesión."""
        pool = [s for s in self.state.sessions.values() if project is None or s["project"] == project]
        pool.sort(key=lambda s: -s.get("last_seen", 0))
        active = [s for s in pool if not s.get("ended") and time.time() - s.get("last_seen", 0) < 86400]
        return active or (pool[:1] if project else [])

    async def reply_summary(self, m: Message) -> None:
        project = next((p for p, t in self.state.topics.items() if t == m.message_thread_id), None)
        sessions = self.recent_sessions(project)
        if not sessions:
            await m.reply("No hay sesiones registradas" + (" en este proyecto." if project else " en las últimas 24 h."))
        elif project is None:
            await m.reply("\n".join(["<b>Sesiones activas</b> (escribe en el tema de un proyecto para el detalle)", ""]
                                    + [render_line(s, self.waiting_for(s["id"])) for s in sessions]))
        elif len(sessions) == 1:
            await m.reply(render_summary(sessions[0], self.waiting_for(sessions[0]["id"])),
                          reply_markup=self.summary_markup(sessions[0]))
        else:
            rows = [[btn(f"{s['provider']} · {s['id'][:6]} · {ago(s.get('last_seen'))}", f"sum:{s['id'][:8]}")]
                    for s in sessions[:8]]
            await m.reply(f"Hay {len(sessions)} sesiones activas en <b>{esc(project)}</b>. ¿Cuál?",
                          reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @staticmethod
    def summary_markup(sess: dict) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[[btn("🧠 Resumen IA", f"ai:{sess['id'][:8]}")]])

    async def ai_summary(self, cq: CallbackQuery, sess: dict) -> None:
        if sess["id"] in self.ai_running:
            await self.ack(cq, "Ya lo estoy generando…")
            return
        transcript = transcript_of(sess)
        if not transcript or not Path(transcript).exists():
            await self.ack(cq, "No encuentro la transcripción de esta sesión")
            return
        self.ai_running.add(sess["id"])
        await self.ack(cq, "Generando resumen…")
        note = await cq.message.reply(f"🧠 Resumiendo con {esc(sess['provider'])}…")
        try:
            text = await summarize(sess, transcript, self.cfg.summary_model, self.cfg.claude_bin)
            body = f"🧠 {header(sess['project'], sess['provider'], sess['id'])}\n\n{md_bold(esc(clip(text, 3500)))}"
        except Exception as e:  # el error se enseña en Telegram, no debe tumbar el broker
            log.warning("Resumen IA de %s: %s", sess["id"], e)
            body = f"⚠️ No se pudo generar el resumen IA: {esc(clip(str(e), 500))}"
        finally:
            self.ai_running.discard(sess["id"])
        try:
            await note.edit_text(body)
        except TelegramBadRequest:
            await cq.message.reply(body)

    async def on_question_button(self, cq: CallbackQuery, req: Pending, action: str) -> None:
        q, sel = req.data["q"], req.data["sel"]
        labels = [o.get("label", "") for o in q.get("options") or []]
        if action == "t":
            await self.resolve(req, None, "🖥 En el ordenador")
        elif action == "c":
            if not sel:
                await self.ack(cq, "Elige al menos una opción")
                return
            answer = ", ".join(labels[i] for i in sorted(sel))
            await self.resolve(req, answer, f"✔️ {answer}")
        elif action.startswith("o"):
            idx = int(action[1:])
            if not q.get("multiSelect"):
                await self.resolve(req, labels[idx], f"✔️ {labels[idx]}")
            else:
                sel.symmetric_difference_update({idx})
                markup = InlineKeyboardMarkup(inline_keyboard=self.question_rows(q, sel, req.id))
                await cq.message.edit_reply_markup(reply_markup=markup)
        await self.ack(cq)


async def run(cfg: Config) -> None:
    broker = Broker(cfg)
    app = web.Application(client_max_size=MAX_UPLOAD * 4 // 3 + 1024 * 1024)  # base64 + margen
    app.router.add_post("/hook", broker.http_hook)
    app.router.add_post("/send", broker.http_send)
    app.router.add_get("/health", lambda _: web.json_response({"ok": True, "away": broker.state.away}))
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, cfg.host, cfg.port).start()
    except OSError as e:
        raise SystemExit(f"No se puede escuchar en {cfg.host}:{cfg.port} ({e.strerror}). "
                         f"¿Puerto ocupado? Cambia C4T_PORT en .env") from None
    log.info("HTTP en %s:%s", cfg.host, cfg.port)

    if not cfg.configured:
        log.warning("Modo configuración: escribe /id en el grupo y rellena TELEGRAM_CHAT_ID y TELEGRAM_OWNER_IDS")
    else:
        await broker.bot.set_my_commands([
            BotCommand(command="fuera", description="Recibir decisiones aquí"),
            BotCommand(command="aqui", description="Volver al ordenador"),
            BotCommand(command="estado", description="Sesiones y pendientes"),
            BotCommand(command="resumen", description="Qué está haciendo cada sesión"),
            BotCommand(command="chat", description="Conversar con el modelo local"),
            BotCommand(command="id", description="Mostrar ids"),
        ])

    dp = Dispatcher()
    dp.include_router(broker.router())
    try:
        await dp.start_polling(broker.bot, allowed_updates=["message", "callback_query"])
    finally:
        await runner.cleanup()
