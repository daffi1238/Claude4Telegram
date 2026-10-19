#!/usr/bin/env python3
"""Envía un mensaje o archivos al tema de Telegram del proyecto. Solo librería estándar.

    python3 c4t_send.py --texto "Tests en verde"
    python3 c4t_send.py --file portada.png --texto "Portada v3"
    python3 c4t_send.py --file a.png --file b.png --proyecto ComicSanlucar
    python3 c4t_send.py --file nota.ogg --como voice

El tipo (foto, audio, nota de voz, vídeo, documento) se deduce de la extensión; --como lo fuerza.
Sale con código 1 y un aviso si el broker no está disponible; nunca lanza excepciones.
"""
import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from c4t_hook import load_env, project_name  # noqa: E402

MAX_UPLOAD = 50 * 1024 * 1024


def post(env: dict, body: dict) -> dict:
    request = urllib.request.Request(
        f"http://{env.get('C4T_HOST', '127.0.0.1')}:{env.get('C4T_PORT', '8799')}/send",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "X-C4T-Secret": env.get("C4T_SECRET", "")},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except ValueError:
            return {"error": f"HTTP {e.code}"}
    except (urllib.error.URLError, OSError) as e:
        return {"error": f"broker no disponible ({getattr(e, 'reason', e)})"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Envía mensajes y archivos al tema de Telegram del proyecto")
    ap.add_argument("--file", "-f", action="append", default=[], help="archivo a enviar (repetible)")
    ap.add_argument("--texto", "-t", default="", help="texto o pie de foto")
    ap.add_argument("--proyecto", "-p", help="proyecto/tema (por defecto, la raíz git del directorio actual)")
    ap.add_argument("--como", choices=("photo", "animation", "audio", "voice", "video", "document"),
                    help="fuerza el tipo de envío")
    args = ap.parse_args()
    if not args.file and not args.texto:
        ap.error("indica --file y/o --texto")

    env = load_env()
    project = args.proyecto or project_name(os.getcwd())
    jobs = [None] if not args.file else args.file
    failed = 0
    for i, name in enumerate(jobs):
        body = {"project": project, "text": args.texto if i == 0 else "", "kind": args.como}
        if name:
            path = Path(name).expanduser()
            if not path.is_file():
                print(f"✗ {name}: no existe", file=sys.stderr)
                failed += 1
                continue
            if path.stat().st_size > MAX_UPLOAD:
                print(f"✗ {name}: supera 50 MB (límite de Telegram para bots)", file=sys.stderr)
                failed += 1
                continue
            body.update(filename=path.name, data_b64=base64.b64encode(path.read_bytes()).decode())
        result = post(env, body)
        if result.get("ok"):
            print(f"✓ {name or 'mensaje'} → tema {project}")
        else:
            print(f"✗ {name or 'mensaje'}: {result.get('error', 'error desconocido')}", file=sys.stderr)
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
