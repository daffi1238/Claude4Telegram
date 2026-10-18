#!/usr/bin/env bash
# Instalación de claude4Telegram en este equipo. Se puede volver a ejecutar sin riesgo.
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"

echo "→ Entorno virtual y dependencias"
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
    echo "→ Creando .env con un C4T_SECRET aleatorio"
    cp .env.example .env
    chmod 600 .env
    secret="$(python3 -c 'import secrets; print(secrets.token_hex(24))')"
    sed -i "s/^C4T_SECRET=$/C4T_SECRET=${secret}/" .env
else
    echo "→ .env ya existe, no se toca"
fi

echo "→ Servicio systemd de usuario"
mkdir -p ~/.config/systemd/user
sed "s#@DIR@#${DIR}#g" systemd/claude4telegram.service > ~/.config/systemd/user/claude4telegram.service
systemctl --user daemon-reload

cat <<EOF

Listo. Siguientes pasos (detalle en README.md):
  1. Pon TELEGRAM_BOT_TOKEN en ${DIR}/.env
  2. .venv/bin/python -m c4t   → escribe /id en el grupo, copia los ids al .env y para con Ctrl+C
  3. systemctl --user enable --now claude4telegram && loginctl enable-linger "\$USER"
  4. python3 install_hooks.py
EOF
