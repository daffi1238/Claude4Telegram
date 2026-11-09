"""Vídeos de YouTube en curso, guardados en disco para retomarlos si el bot se reinicia.

Telegram no vuelve a entregar un mensaje ya recibido, así que sin esto un reinicio pierde el enlace.
"""
import json
import os
from pathlib import Path

MAX_ATTEMPTS = 3


def serialize_message(m) -> str:
    """Solo lo necesario para responder: el mensaje entero no siempre se puede pasar a JSON."""
    return json.dumps({
        "message_id": m.message_id,
        "date": int(m.date.timestamp()),
        "chat": {"id": m.chat.id, "type": m.chat.type},
        "message_thread_id": m.message_thread_id,
        "is_topic_message": m.is_topic_message,
        "text": m.text,
    })


class PendingVideos:
    def __init__(self, folder: Path):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)

    def _path(self, chat_id: int, message_id: int) -> Path:
        return self.folder / f"{chat_id}_{message_id}.json"

    def add(self, chat_id: int, message_id: int, url: str, message_json: str) -> Path:
        """Registra el vídeo; cada llamada para el mismo mensaje cuenta como un intento más."""
        path = self._path(chat_id, message_id)
        attempts = self._read(path).get("attempts", 0) + 1
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"url": url, "message": message_json, "attempts": attempts}))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        return path

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def load(self) -> list[tuple[Path, dict]]:
        entries = ((path, self._read(path)) for path in sorted(self.folder.glob("*.json")))
        return [(path, data) for path, data in entries if data.get("url") and data.get("message")]
