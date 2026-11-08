#!/usr/bin/env bash
# Exposición EFÍMERA de un chromium para iniciar sesión en YouTube, vía noVNC + cloudflared quick tunnel.
#
# Imprime una línea «URL=https://....trycloudflare.com/vnc.html...» cuando el túnel está listo y se
# mantiene abierto C4T_YT_LOGIN_SECONDS (5 min por defecto); al salir (tiempo, señal o error) lo cierra
# TODO. El perfil del navegador es persistente, así que la sesión de YouTube que inicies queda guardada
# y yt-dlp la reutiliza. Pensado para lanzarse desde el broker (/ytlogin) y reenviar la URL por Telegram.
set -u

PROFILE="${C4T_YT_PROFILE:-$HOME/.config/claude4telegram/yt-browser}"
NOVNC="${C4T_NOVNC_DIR:-$HOME/.local/share/novnc}"
DURATION="${C4T_YT_LOGIN_SECONDS:-300}"
CF_LOG="$(mktemp)"

# Display X libre (aquí hay muchos Xvfb; no pisar ninguno).
DISPLAY_NUM=""
for n in $(seq 90 160); do
    [ -e "/tmp/.X11-unix/X$n" ] || { DISPLAY_NUM="$n"; break; }
done
[ -z "$DISPLAY_NUM" ] && { echo "ERROR=sin display X libre"; exit 5; }
VNC_PORT=$((5900 + DISPLAY_NUM))
# Puerto web libre para websockify/noVNC.
PORT="${C4T_YT_VNC_WEB_PORT:-$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')}"
# Puerto de DevTools (solo 127.0.0.1) para volcar las cookies en vivo al cerrar.
DBG="$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')"
HERE="$(cd "$(dirname "$0")" && pwd)"
VENV_PY="$(cd "$HERE/.." && pwd)/.venv/bin/python"
COOKIES_OUT="${C4T_YT_COOKIES_OUT:-$HOME/.local/state/claude4telegram/yt-cookies.txt}"

CF_PID=""; WS_PID=""; X11VNC_PID=""; CHROME_PID=""; XVFB_PID=""
cleanup() {
    trap - EXIT INT TERM
    # 1) Fuera el túnel y el VNC: la exposición acaba ya.
    kill "$CF_PID" "$WS_PID" "$X11VNC_PID" 2>/dev/null
    [ -n "${PORT:-}" ]     && pkill -f "127.0.0.1:$PORT" 2>/dev/null
    [ -n "${VNC_PORT:-}" ] && pkill -f "rfbport $VNC_PORT" 2>/dev/null
    # 2) Volcar las cookies EN VIVO vía DevTools antes de cerrar (no depende del guardado a disco de chromium).
    if [ -n "${DBG:-}" ] && [ -x "$VENV_PY" ] && [ -f "$HERE/yt_cookies.py" ]; then
        mkdir -p "$(dirname "$COOKIES_OUT")"
        "$VENV_PY" "$HERE/yt_cookies.py" "$DBG" "$COOKIES_OUT" >/dev/null 2>&1 || true
        chmod 600 "$COOKIES_OUT" 2>/dev/null
    fi
    # 3) SIGTERM al principal de chromium y margen para cerrar.
    kill "$CHROME_PID" 2>/dev/null
    sleep 2
    # 3) Ahora sí, barrido duro de lo que quede (subprocesos de chromium ya cerrados o colgados) y el display.
    [ -n "${PROFILE:-}" ]     && pkill -f "user-data-dir=$PROFILE" 2>/dev/null
    kill "$XVFB_PID" 2>/dev/null
    [ -n "${DISPLAY_NUM:-}" ] && pkill -f "Xvfb :$DISPLAY_NUM " 2>/dev/null
    pkill -P $$ 2>/dev/null
    rm -f "$CF_LOG"
}
trap 'cleanup; exit 0' EXIT INT TERM

for bin in Xvfb chromium x11vnc websockify cloudflared; do
    command -v "$bin" >/dev/null || { echo "FALTA=$bin"; exit 3; }
done
[ -f "$NOVNC/vnc.html" ] || { echo "FALTA=novnc ($NOVNC)"; exit 3; }

mkdir -p "$PROFILE"
Xvfb ":$DISPLAY_NUM" -screen 0 1280x800x24 >/dev/null 2>&1 & XVFB_PID=$!
for _ in $(seq 1 10); do [ -e "/tmp/.X11-unix/X$DISPLAY_NUM" ] && break; sleep 0.5; done
[ -e "/tmp/.X11-unix/X$DISPLAY_NUM" ] || { echo "ERROR=Xvfb no arrancó en :$DISPLAY_NUM"; exit 5; }
DISPLAY=":$DISPLAY_NUM" chromium --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check \
    --disable-dev-shm-usage --no-sandbox --password-store=basic --start-maximized \
    --remote-debugging-port="$DBG" --remote-allow-origins=* \
    "https://www.youtube.com" >/dev/null 2>&1 & CHROME_PID=$!
# -rfbport fijo: si no, x11vnc escoge 5900 y websockify (que apunta a 5900+display) no lo encuentra.
x11vnc -display ":$DISPLAY_NUM" -rfbport "$VNC_PORT" -localhost -nopw -forever -shared -quiet \
    >/dev/null 2>&1 & X11VNC_PID=$!
sleep 1
# Todo en IPv4 explícito: cloudflared resuelve localhost por IPv6 y no encontraría a websockify.
websockify --web="$NOVNC" --heartbeat 30 "127.0.0.1:$PORT" "127.0.0.1:$VNC_PORT" >/dev/null 2>&1 & WS_PID=$!
# --protocol http2: los WebSockets sobre quick tunnels QUIC fallan a menudo (la página carga pero el VNC no conecta).
cloudflared tunnel --protocol http2 --url "http://127.0.0.1:$PORT" >"$CF_LOG" 2>&1 & CF_PID=$!

URL=""
for _ in $(seq 1 40); do
    URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$CF_LOG" | head -1)"
    [ -n "$URL" ] && break
    sleep 1
done
[ -z "$URL" ] && { echo "ERROR=no se obtuvo URL del túnel"; exit 4; }

# Parámetros explícitos para que noVNC construya bien el WebSocket a través del túnel (host/puerto/cifrado).
DOMAIN="${URL#https://}"
echo "URL=${URL}/vnc.html?autoconnect=1&resize=scale&path=websockify&host=${DOMAIN}&port=443&encrypt=1&reconnect=1"
echo "MARK=$PORT $VNC_PORT $DISPLAY_NUM $PROFILE"   # para que el broker pueda matar este stack sin lugar a dudas
echo "SECONDS=$DURATION"
# `wait` (a diferencia de `sleep` directo) SÍ es interrumpible por SIGTERM: el trap salta al instante.
sleep "$DURATION" & wait $!
