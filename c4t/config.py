"""Configuración del broker, leída de <repo>/.env y del entorno."""
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# Patrones de Bash que nunca se aprueban desde Telegram (solo Denegar / En el PC).
DEFAULT_DANGER = [
    r"\brm\s+-[a-zA-Z]*(rf|fr)",
    r"\brm\s+(-[a-zA-Z]+\s+)*--recursive",
    r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[a-zA-Z]*f",
    r"\bsudo\b",
    r"\bmkfs\b|\bdd\s+if=",
    r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b",
    r"\bdrop\s+(table|database|schema)\b",
    r"\bchmod\s+-R\s+777\b",
]


def load_env_file(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass
class Config:
    token: str
    chat_id: int | None
    owner_ids: set[int]
    host: str
    port: int
    secret: str
    state_dir: Path
    topic_prefix: str = ""
    presence_window: int = 120
    danger_patterns: list[str] = field(default_factory=list)
    summary_model: str = "haiku"   # modelo del 🧠 Resumen IA en proyectos Claude ("" = el del proyecto)
    claude_bin: str = ""           # ruta a claude si no está en el PATH del servicio

    @property
    def configured(self) -> bool:
        return self.chat_id is not None and bool(self.owner_ids)


def load_config() -> Config:
    env = {**load_env_file(), **os.environ}
    token = env.get("TELEGRAM_BOT_TOKEN", "")
    if not token:
        raise SystemExit(f"Falta TELEGRAM_BOT_TOKEN en {ENV_FILE}")
    secret = env.get("C4T_SECRET", "")
    if not secret:
        raise SystemExit(f"Falta C4T_SECRET en {ENV_FILE}")
    chat = env.get("TELEGRAM_CHAT_ID", "").strip()
    owners = {int(x) for x in env.get("TELEGRAM_OWNER_IDS", "").replace(" ", "").split(",") if x}
    extra = [p for p in env.get("C4T_DANGER_EXTRA", "").split(";;") if p.strip()]
    return Config(
        token=token,
        chat_id=int(chat) if chat else None,
        owner_ids=owners,
        host=env.get("C4T_HOST", "127.0.0.1"),
        port=int(env.get("C4T_PORT", "8799")),
        secret=secret,
        state_dir=Path(env.get("C4T_STATE_DIR", "~/.local/state/claude4telegram")).expanduser(),
        topic_prefix=env.get("C4T_TOPIC_PREFIX", ""),
        presence_window=int(env.get("C4T_PRESENCE_WINDOW", "120")),
        danger_patterns=DEFAULT_DANGER + extra,
        summary_model=env.get("C4T_SUMMARY_MODEL", "haiku"),
        claude_bin=env.get("C4T_CLAUDE_BIN", ""),
    )
