#!/usr/bin/env python3
"""Contraseña para descifrar los archivos cifrados que envías desde Telegram. Se escribe aquí, en el
equipo, y nunca pasa por Telegram. Solo librería estándar.

    python3 c4t_key.py                      # contraseña del proyecto actual (raíz git)
    python3 c4t_key.py --proyecto Android_Audit
    python3 c4t_key.py --global             # la que se usa si un proyecto no tiene la suya
    python3 c4t_key.py --borrar [--proyecto X | --global]
    python3 c4t_key.py --lista
"""
import argparse
import getpass
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c4t_hook import load_env, project_name  # noqa: E402


def safe_name(name: str) -> str:  # igual que c4t/inbox.py
    name = re.sub(r"[^\w.\-]+", "_", Path(name).name).strip("._")
    return name[:120] or "archivo"


def main() -> int:
    ap = argparse.ArgumentParser(description="Gestiona las contraseñas de descifrado de Claude4Telegram")
    target = ap.add_mutually_exclusive_group()
    target.add_argument("--proyecto", "-p")
    target.add_argument("--global", dest="glob", action="store_true")
    ap.add_argument("--borrar", action="store_true")
    ap.add_argument("--lista", action="store_true")
    args = ap.parse_args()

    state = Path(load_env().get("C4T_STATE_DIR", "~/.local/state/claude4telegram")).expanduser()
    keys = state / "keys"
    if args.lista:
        names = sorted(p.stem for p in keys.glob("*.key")) if keys.is_dir() else []
        print("\n".join(("(global)" if n == "default" else n) for n in names) or "No hay contraseñas guardadas.")
        return 0

    name = "default" if args.glob else safe_name(args.proyecto or project_name(os.getcwd()))
    label = "global" if name == "default" else f"del proyecto {name}"
    path = keys / f"{name}.key"
    if args.borrar:
        path.unlink(missing_ok=True)
        print(f"Contraseña {label} borrada.")
        return 0

    first = getpass.getpass(f"Contraseña {label}: ")
    if not first:
        print("Vacía: no se guarda nada.", file=sys.stderr)
        return 1
    if getpass.getpass("Repítela: ") != first:
        print("No coinciden: no se guarda nada.", file=sys.stderr)
        return 1
    keys.mkdir(parents=True, exist_ok=True)
    os.chmod(keys, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(first + "\n")
    print(f"Contraseña {label} guardada en {path} (solo legible por tu usuario).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
