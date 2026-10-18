# Claude4Telegram

Controla tus sesiones de [Claude Code](https://code.claude.com) desde Telegram: aprueba permisos, responde a las preguntas con opciones y dile qué hacer cuando termina una tarea, sin estar delante del ordenador.

- **Un bot y un grupo con temas**: cada proyecto tiene su propio tema y los mensajes van etiquetados con proyecto, proveedor y sesión.
- **Funciona con cualquier modelo**: usa los hooks de Claude Code, así que da igual que la sesión use Claude o DeepSeek u otro proveedor vía `ANTHROPIC_BASE_URL`.
- **Seguro por defecto**: si no respondes o el broker está caído, no se decide nada y la sesión sigue en el terminal. Los comandos peligrosos solo se aprueban en el ordenador.
- **Sin dependencias en los hooks**: el hook usa solo la librería estándar de Python. El broker usa [aiogram](https://docs.aiogram.dev).

```
sesiones Claude Code ─► hooks/c4t_hook.py ─HTTP 127.0.0.1─► broker (python -m c4t) ◄─► bot ◄─► grupo con temas
```

## Qué llega a Telegram

Solo en **modo fuera** (`/fuera`). En modo ordenador (`/aqui`) los hooks no esperan y solo se registran inicio y fin de sesión, como mensajes silenciosos.

| Situación | Botones | Texto libre (respondiendo al mensaje del bot) |
|---|---|---|
| 🔐 Permiso de herramienta | Permitir · Permitir siempre · Denegar · En el PC | Deniega y le dice a Claude qué hacer en su lugar |
| ❓ Pregunta con opciones (`AskUserQuestion`) | Una por opción (multiselección con ☑️ + Confirmar) · En el PC | Respuesta libre ("Otra") |
| 🏁 Claude ha terminado | Continúa · Dejar parado | Siguiente instrucción, y la sesión sigue |
| 🔔 Otras notificaciones | — | — |

Solo llega a Telegram lo que el terminal te habría preguntado. Los comandos de solo lectura (`ls`, `cat`…) y las sesiones en modo *auto*, *accept edits* o *bypass* no piden permiso.

### Comandos del bot

| Comando | Efecto |
|---|---|
| `/fuera` | Las decisiones llegan a Telegram y Claude espera tu respuesta |
| `/aqui` | Vuelves al ordenador: lo pendiente pasa al terminal |
| `/estado` | Sesiones activas y qué espera cada una |
| `/id` | Ids de chat, usuario y tema |

## Requisitos

- Linux con systemd (para el servicio; el broker en sí funciona en cualquier sistema con Python)
- Python 3.10+
- Claude Code 2.1 o superior (hooks `PermissionRequest` y `Stop` con `last_assistant_message`)
- Una cuenta de Telegram

## Instalación

### 1. Clonar e instalar

```bash
git clone https://github.com/daffi1238/Claude4Telegram.git
cd Claude4Telegram
./setup.sh
```

`setup.sh` crea el entorno virtual, instala las dependencias, genera un `.env` con un `C4T_SECRET` aleatorio e instala el servicio systemd de usuario apuntando a la carpeta del repo. Puedes ejecutarlo de nuevo sin riesgo.

### 2. Crear el bot

En Telegram, abre [@BotFather](https://t.me/BotFather) → `/newbot` y copia el token en `TELEGRAM_BOT_TOKEN` dentro de `.env`.

### 3. Crear el grupo

1. Crea un grupo privado y añade el bot como miembro.
2. Activa los temas: nombre del grupo → **Editar** o **Manage** → **Temas** (*Topics*). Hazlo antes del paso 4, porque al activarlos el grupo cambia de id.
3. Haz al bot **administrador** con el permiso **Gestionar temas** (*Manage Topics*). Al ser admin, también ve los mensajes que no son comandos.

### 4. Obtener los ids

```bash
.venv/bin/python -m c4t
```

Escribe `/id` en el grupo. Copia `chat_id`, que empieza por `-100`, a `TELEGRAM_CHAT_ID`, y tu `user_id` a `TELEGRAM_OWNER_IDS`. Para el broker con Ctrl+C.

### 5. Arrancar el servicio

```bash
systemctl --user enable --now claude4telegram
loginctl enable-linger "$USER"        # que funcione aunque no hayas iniciado sesión
journalctl --user -u claude4telegram -f
```

### 6. Instalar los hooks

```bash
python3 install_hooks.py              # --wait 3600 para esperar hasta 1 h por decisión (por defecto 30 min)
```

Hace una copia de seguridad de `~/.claude/settings.json` y solo añade sus propias entradas. Reinicia las sesiones de Claude Code abiertas.

## Configuración (`.env`)

| Variable | Descripción |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Token de @BotFather |
| `TELEGRAM_CHAT_ID` | Id del grupo (lo da `/id`) |
| `TELEGRAM_OWNER_IDS` | Usuarios autorizados, separados por comas |
| `C4T_SECRET` | Secreto compartido entre hooks y broker (lo genera `setup.sh`) |
| `C4T_HOST` / `C4T_PORT` | Dónde escucha el broker (por defecto `127.0.0.1:8799`) |
| `C4T_TOPIC_PREFIX` | Prefijo para los nombres de tema, útil con varios equipos |
| `C4T_DANGER_EXTRA` | Regex extra de comandos Bash que nunca se aprueban desde Telegram, separadas por `;;` |
| `C4T_STATE_DIR` | Estado persistente (por defecto `~/.local/state/claude4telegram`) |

## Varios equipos

Telegram solo permite que **un proceso** lea las actualizaciones de cada bot, así que **cada equipo necesita su propio bot**. Pueden compartir el mismo grupo:

1. Crea un bot nuevo en @BotFather para cada equipo y hazlo admin del grupo.
2. En el `.env` de cada equipo pon su token, el mismo `TELEGRAM_CHAT_ID` y un `C4T_TOPIC_PREFIX` distinto (por ejemplo `portátil · ` y `servidor · `).
3. `/fuera` y `/aqui` sin mención afectan a todos los equipos. Usa `/fuera@bot_del_portatil` para uno solo.

## Proyectos con DeepSeek u otros proveedores

No hace falta nada especial. El proveedor se detecta por `ANTHROPIC_BASE_URL` o por el nombre del modelo, y el tema del proyecto se colorea según el proveedor. El hook solo envía al broker la URL base y el nombre del modelo, **nunca las claves**.

## Seguridad

- El broker escucha solo en `127.0.0.1` y exige `C4T_SECRET` en cada petición.
- El bot ignora cualquier chat distinto de `TELEGRAM_CHAT_ID` y cualquier usuario fuera de `TELEGRAM_OWNER_IDS`.
- Usa long polling: no hay que abrir puertos ni configurar webhooks.
- Los comandos Bash peligrosos (`rm -rf`, `git push --force`, `git reset --hard`, `sudo`, `curl | sh`, `DROP TABLE`…) no tienen botón *Permitir*.
- `.env` está en `.gitignore`: no lo subas nunca.
- Cuidado con la inyección de prompts: lee lo que apruebas, sobre todo con *Permitir siempre*.

## Desinstalar

```bash
python3 install_hooks.py --uninstall
systemctl --user disable --now claude4telegram
rm ~/.config/systemd/user/claude4telegram.service
```

## Limitaciones

- Mientras un hook espera tu respuesta, el terminal de esa sesión también espera. Usa `/aqui` al volver al ordenador.
- Si se agota la espera del hook `Stop`, la sesión queda parada; retómala con `claude --resume`.
- Claude Code corta la tarea tras 8 "Continúa" seguidos sin usar herramientas.

## Estructura

```
c4t/            broker: bot de Telegram + servidor HTTP local
hooks/          hook que ejecuta Claude Code (solo librería estándar)
systemd/        plantilla del servicio de usuario
install_hooks.py  instala/desinstala los hooks en ~/.claude/settings.json
setup.sh        instalación en un equipo nuevo
```

## Licencia

[GPL-3.0](LICENSE): puedes usar, modificar y redistribuir este proyecto, pero las versiones derivadas que distribuyas deben publicarse también bajo GPL-3.0 con su código fuente.
