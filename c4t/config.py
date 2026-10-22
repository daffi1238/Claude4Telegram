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
    # Notas de voz: Whisper transcribe, un LLM local repara el texto y se inyecta en la sesión por tmux.
    whisper_model: str = "small"   # modelo de Whisper para transcribir (tiny/base/small/medium)
    llm_url: str = ""              # endpoint OpenAI-compatible para reparar la transcripción ("" = sin reparar)
    llm_key: str = ""              # Bearer del LLM local (nunca se sube al repo)
    llm_model: str = "qwen-coder"  # modelo del LLM local
    voice_model: str = ""          # ruta al .onnx de Piper para la respuesta hablada ("" = sin voz de vuelta)

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
        whisper_model=env.get("C4T_WHISPER_MODEL", "small"),
        llm_url=env.get("C4T_LLM_URL", ""),
        llm_key=env.get("C4T_LLM_KEY", ""),
        llm_model=env.get("C4T_LLM_MODEL", "qwen-coder"),
        voice_model=_default_voice(env.get("C4T_VOICE_MODEL", "")),
    )


def _default_voice(configured: str) -> str:
    """Ruta al modelo de Piper: la configurada, o la voz española que descarga setup.sh si existe."""
    if configured:
        return configured
    default = Path("~/.local/share/c4t-voices/es_ES-davefx-medium.onnx").expanduser()
    return str(default) if default.exists() else ""
