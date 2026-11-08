#!/usr/bin/env python3
"""Vuelca las cookies del chromium de /ytlogin (vía DevTools/CDP) a un cookies.txt (formato Netscape).

Se llama ANTES de cerrar el navegador, así lee las cookies de la sesión en memoria y no depende de que
chromium las haya guardado a disco (que resultó poco fiable bajo Xvfb). Uso:
    yt_cookies.py <puerto_debug> <ruta_salida>
Requiere el paquete `websockets` (el del venv del proyecto)."""
import asyncio
import json
import sys
import urllib.request

import websockets


async def _call(ws_url: str, method: str) -> list:
    async with websockets.connect(ws_url, max_size=None, open_timeout=15) as ws:
        await ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        await ws.send(json.dumps({"id": 2, "method": method}))
        while True:
            msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
            if msg.get("id") == 2:
                return (msg.get("result") or {}).get("cookies") or []


async def dump(port: str, out: str) -> int:
    targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=10))
    pages = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
    cookies = []
    if pages:  # el target de la página ve todas las cookies del contexto
        cookies = await _call(pages[0]["webSocketDebuggerUrl"], "Network.getAllCookies")
    if not cookies:  # respaldo: endpoint de navegador
        info = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=10))
        cookies = await _call(info["webSocketDebuggerUrl"], "Network.getAllCookies")
    lines = ["# Netscape HTTP Cookie File", "# Generado por claude4Telegram (/ytlogin)"]
    for c in cookies:
        domain = c.get("domain", "")
        if not domain or not c.get("name"):
            continue
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        secure = "TRUE" if c.get("secure") else "FALSE"
        expires = c.get("expires", 0)
        expiry = str(int(expires)) if expires and expires > 0 else "0"
        lines.append("\t".join([domain, flag, c.get("path", "/"), secure, expiry, c["name"], c.get("value", "")]))
    with open(out, "w") as f:
        f.write("\n".join(lines) + "\n")
    return len(cookies)


if __name__ == "__main__":
    try:
        n = asyncio.run(dump(sys.argv[1], sys.argv[2]))
        print(f"WROTE {n}")
    except Exception as e:
        print(f"ERROR {e}", file=sys.stderr)
        sys.exit(1)
