"""Envío de archivos a Telegram eligiendo el método según el tipo (foto, audio, nota de voz, vídeo…)."""
import logging
import mimetypes

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, Message

log = logging.getLogger("c4t")

MAX_UPLOAD = 50 * 1024 * 1024   # límite de subida de la Bot API
MAX_PHOTO = 10 * 1024 * 1024    # por encima, Telegram no lo acepta como foto
CAPTION_LIMIT = 1024
KINDS = ("photo", "animation", "audio", "voice", "video", "document")


def guess_kind(filename: str, size: int) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    mime = mimetypes.guess_type(filename)[0] or ""
    if ext == "gif":
        return "animation"
    if mime in ("image/jpeg", "image/png", "image/webp") and size <= MAX_PHOTO:
        return "photo"
    if ext in ("ogg", "oga", "opus"):
        return "voice"
    if mime.startswith("audio/"):
        return "audio"
    if ext in ("mp4", "mov", "m4v", "webm"):
        return "video"
    return "document"


async def send_media(bot: Bot, chat_id: int, thread: int, filename: str, data: bytes,
                     caption_html: str = "", kind: str | None = None) -> Message:
    kind = kind if kind in KINDS else guess_kind(filename, len(data))
    caption, extra = caption_html, ""
    if len(caption) > CAPTION_LIMIT:  # el pie de foto tiene límite: el resto va en un mensaje aparte
        caption, extra = "", caption_html
    if extra:
        await bot.send_message(chat_id, extra, message_thread_id=thread)
    method = getattr(bot, f"send_{kind}")
    upload = BufferedInputFile(data, filename=filename)
    try:
        return await method(chat_id, upload, caption=caption or None, message_thread_id=thread)
    except TelegramBadRequest as e:
        if kind == "document":
            raise
        # p. ej. foto con dimensiones no admitidas o audio con formato raro: como documento siempre entra
        log.info("send_%s falló (%s); se envía como documento", kind, e)
        return await bot.send_document(chat_id, BufferedInputFile(data, filename=filename),
                                       caption=caption or None, message_thread_id=thread)
