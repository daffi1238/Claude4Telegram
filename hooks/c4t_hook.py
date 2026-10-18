#!/usr/bin/env python3
"""Hook de Claude Code -> broker claude4Telegram. Solo librería estándar (arranque rápido).

Si el broker no está corriendo o falla, sale sin decidir nada: Claude Code sigue
su flujo normal en el terminal. Nunca envía claves API, solo URL base y modelo.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORWARDED_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")


def load_env() -> dict[str, str]:
    values = {}
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and not key.startswith("#"):
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def project_name(cwd: str) -> str:
    path = Path(cwd or ".").resolve()
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            return candidate.name
    return path.name or "sin-proyecto"


def main() -> int:
    timeout = 600
    if "--timeout" in sys.argv:
        timeout = int(sys.argv[sys.argv.index("--timeout") + 1])
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    env = load_env()
    body = {
        "payload": payload,
        "project": project_name(payload.get("cwd", "")),
        "env": {k: os.environ.get(k) for k in FORWARDED_ENV},
        "timeout": timeout,
    }
    request = urllib.request.Request(
        f"http://{env.get('C4T_HOST', '127.0.0.1')}:{env.get('C4T_PORT', '8799')}/hook",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-C4T-Secret": env.get("C4T_SECRET", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            output = json.load(response).get("output")
    except Exception:
        return 0
    if output:
        print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
