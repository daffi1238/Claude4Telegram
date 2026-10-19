"""Estado persistente: modo fuera, temas por proyecto y sesiones conocidas."""
import json
import time
from pathlib import Path

SESSION_TTL = 7 * 24 * 3600


class State:
    def __init__(self, path: Path):
        self.path = path
        self.away = False
        self.topics: dict[str, int] = {}      # proyecto -> message_thread_id
        self.sessions: dict[str, dict] = {}   # session_id -> info
        self.inbox: dict[str, list[str]] = {}  # proyecto -> avisos de archivos aún no entregados a Claude
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        data = json.loads(self.path.read_text())
        self.away = data.get("away", False)
        self.topics = data.get("topics", {})
        self.inbox = data.get("inbox", {})
        cutoff = time.time() - SESSION_TTL
        self.sessions = {k: v for k, v in data.get("sessions", {}).items() if v.get("last_seen", 0) > cutoff}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(
            {"away": self.away, "topics": self.topics, "sessions": self.sessions, "inbox": self.inbox},
            indent=2, ensure_ascii=False,
        ))
        tmp.replace(self.path)
