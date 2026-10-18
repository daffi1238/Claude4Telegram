#!/usr/bin/env python3
"""Instala (o desinstala) los hooks de claude4Telegram en ~/.claude/settings.json.

    python3 install_hooks.py               # instalar (espera máx. 30 min por decisión)
    python3 install_hooks.py --wait 3600   # esperar hasta 1 h
    python3 install_hooks.py --uninstall

Hace copia de seguridad del settings.json y es idempotente: solo toca sus propias entradas.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

HOOK = Path(__file__).resolve().parent / "hooks" / "c4t_hook.py"
MARKER = "c4t_hook.py"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--settings", default="~/.claude/settings.json")
    ap.add_argument("--wait", type=int, default=1800, help="segundos máximos esperando respuesta en Telegram")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    path = Path(args.settings).expanduser()
    settings = json.loads(path.read_text()) if path.exists() else {}
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-c4t-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"Copia de seguridad: {backup}")

    python = shutil.which("python3") or sys.executable
    hooks = settings.setdefault("hooks", {})

    # Quitar entradas previas nuestras
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            group["hooks"] = [h for h in group.get("hooks", []) if MARKER not in h.get("command", "")]
            if group["hooks"]:
                groups.append(group)
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]

    if not args.uninstall:
        events = {
            # evento: (matcher, timeout en s)
            "SessionStart": (None, 10),
            "SessionEnd": (None, 10),
            "PermissionRequest": ("*", args.wait),
            "PreToolUse": ("AskUserQuestion", args.wait),
            "Stop": (None, args.wait),
            "Notification": (None, 10),
        }
        for event, (matcher, timeout) in events.items():
            group = {"hooks": [{
                "type": "command",
                "command": f'"{python}" "{HOOK}" --timeout {timeout}',
                "timeout": timeout,
            }]}
            if matcher:
                group["matcher"] = matcher
            hooks.setdefault(event, []).append(group)

    if not hooks:
        settings.pop("hooks", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n")
    print("Hooks desinstalados." if args.uninstall else f"Hooks instalados en {path}.")
    print("Reinicia las sesiones de Claude Code abiertas para que los carguen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
