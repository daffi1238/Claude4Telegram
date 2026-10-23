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
| 🏁 Claude ha terminado | Sigue trabajando · Listo, para aquí | Siguiente instrucción, y la sesión sigue |
| 🔔 Otras notificaciones | — | — |

Solo llega a Telegram lo que el terminal te habría preguntado. Los comandos de solo lectura (`ls`, `cat`…) y las sesiones en modo *auto*, *accept edits* o *bypass* no piden permiso.

### Terminal y Telegram a la vez (tmux)

Si la sesión de Claude Code corre dentro de **tmux**, gana quien responda primero:

- Mientras una petición espera en Telegram, basta con **pulsar cualquier tecla en el pane de esa sesión** para soltarla: el mensaje de Telegram se cierra con "🖥 Respondido en el ordenador" y sigues en el terminal (en menos de 1 s).
- Si has tecleado en ese pane en los últimos 2 minutos (`C4T_PRESENCE_WINDOW`), no se manda nada a Telegram: estás delante.
- Fuera de tmux, la sesión espera a Telegram (o a `/aqui`) como siempre.

### Consultar el estado

Escribe cualquier cosa ("¿cómo vas?") en el tema de un proyecto cuando no haya nada pendiente, o usa `/resumen`. El bot lee la transcripción de la sesión y responde al instante, sin gastar tokens:

- estado: 🟢 trabajando, ⏳ esperando una decisión, 💤 parada (desde cuándo) o ⚪ cerrada
- título de la sesión, rama git y modo de permisos
- tu última instrucción y la lista de tareas de Claude, si la usa
- ficheros modificados y últimos comandos
- el último mensaje de Claude

Si hay varias sesiones activas en el proyecto, te deja elegir con un botón.

Debajo del resumen hay un botón **🧠 Resumen IA**: manda un extracto de la conversación (tus instrucciones, las respuestas de Claude y las herramientas usadas, unos 40 000 caracteres como máximo) a `claude -p` sin herramientas y te devuelve objetivo, hecho, estado, pendiente y siguiente paso. Este sí gasta tokens:

- Se lanza en la carpeta del proyecto, así que usa su misma configuración: en un proyecto DeepSeek resume DeepSeek con el modelo del proyecto; en uno Claude, `haiku` (`C4T_SUMMARY_MODEL`).
- No se guarda como sesión ni dispara los hooks de claude4Telegram.

### Comandos del bot

| Comando | Efecto |
|---|---|
| `/fuera` | Las decisiones llegan a Telegram y Claude espera tu respuesta |
| `/aqui` | Vuelves al ordenador: lo pendiente pasa al terminal |
| `/estado` | Sesiones activas y qué espera cada una |
| `/resumen` | Qué está haciendo cada sesión (en un tema: detalle del proyecto; en General: una línea por sesión) |
| `/chat` | Conversa con el modelo local del hub (responde a su respuesta para seguir) |
| `/id` | Ids de chat, usuario y tema |

## Enviar archivos desde los proyectos

Cualquier proyecto (o Claude dentro de él) puede mandar mensajes y archivos a su tema de Telegram:

```bash
python3 ~/Claude4Telegram/hooks/c4t_send.py --texto "Tests en verde"
python3 ~/Claude4Telegram/hooks/c4t_send.py --file portada.png --texto "Portada v3"
python3 ~/Claude4Telegram/hooks/c4t_send.py -f a.png -f b.png --proyecto ComicSanlucar
python3 ~/Claude4Telegram/hooks/c4t_send.py --file nota.ogg --como voice
```

- El tema sale de la raíz git del directorio actual, igual que para los permisos; `--proyecto` lo cambia.
- El tipo se deduce de la extensión: foto (JPG/PNG/WebP hasta 10 MB), GIF animado, nota de voz (`.ogg`/`.opus`), audio, vídeo (`.mp4`/`.mov`/`.webm`) o documento para todo lo demás. `--como` lo fuerza, y si Telegram rechaza el formato se reintenta como documento.
- Límite de 50 MB por archivo (el de la Bot API de Telegram).
- Se envía siempre, aunque estés en modo ordenador, y no espera respuesta.
- `install_hooks.py` añade a `permissions.allow` la regla `Bash(python3 <repo>/hooks/c4t_send.py:*)`, así Claude puede enviar sin pedirte permiso. `--uninstall` la quita.
- El comando manda los bytes al broker (`POST /send`, con `C4T_SECRET`); el broker nunca lee archivos del disco por su cuenta.
- Si el broker no está disponible, avisa y sale con código 1.

## Enviar archivos al proyecto desde Telegram

Manda una foto, nota de voz, audio, vídeo o documento **en el tema del proyecto**. El broker lo guarda en `<proyecto>/.c4t/inbox/` (la carpeta `.c4t/` se ignora sola en git) y se lo entrega a Claude con la ruta y tu comentario (el pie del mensaje):

- Si la sesión está esperando en un 🏁 (o respondes a ese mensaje con el archivo), Claude lo recibe al momento y sigue trabajando.
- Si no, queda en cola y lo recibe en cuanto le escribas o termine lo que está haciendo.
- Límite de 20 MB por archivo (el de descarga de la Bot API).

### Archivos cifrados

Para que Telegram solo vea el archivo cifrado, cífralo en el móvil y configura la contraseña **en el equipo** (nunca pasa por Telegram):

```bash
python3 ~/Claude4Telegram/hooks/c4t_key.py                 # contraseña del proyecto actual
python3 ~/Claude4Telegram/hooks/c4t_key.py --global        # la que se usa si el proyecto no tiene la suya
python3 ~/Claude4Telegram/hooks/c4t_key.py --lista | --borrar
```

| Formato | Ejemplos de app | Resultado |
|---|---|---|
| ZIP con AES o `.7z` con contraseña | ZArchiver (Android), 7-Zip | Se extrae a una carpeta |
| `.gpg` / `.pgp` / `.asc` simétrico | OpenKeychain (Android), `gpg -c` | Se descifra el archivo |

- Las contraseñas se guardan en `~/.local/state/claude4telegram/keys/` con permisos `600`, y se pasan a `gpg`/`7z` por stdin (no aparecen en la lista de procesos).
- Si falta la contraseña o no es la correcta, el archivo se guarda cifrado, Claude no lo recibe y el bot te dice qué hacer.
- Los `.zip` sin cifrar se dejan tal cual, sin extraer.
- Ojo: el archivo descifrado queda en el disco del proyecto, al alcance de Claude.

## Voz, OCR y chat (hub de modelos local)

Si configuras un **hub de modelos** en `localhost` (chat, STT, TTS y OCR bajo una API tipo OpenAI, con `C4T_HUB_URL`), se activan tres cosas. Todo el procesado es local; solo el audio y las imágenes pasan por Telegram.

### Instrucciones por voz

Manda una **nota de voz** (o *video note*) al tema de un proyecto y se convierte en una instrucción para esa sesión:

```
nota de voz ─▶ hub STT ─▶ hub chat (repara el dictado) ─▶ tmux send-keys ─▶ sesión de Claude
respuesta de Claude ─▶ hub TTS ─▶ ffmpeg (opus) ─▶ nota de voz de vuelta al tema
```

1. El hub la **transcribe** (STT).
2. El hub **repara** el dictado (puntuación, mayúsculas, erratas), respetando términos técnicos.
3. El texto se **escribe en la sesión por tmux**, con un aviso de que viene de una nota de voz. Sin tmux, entra como los archivos (al terminar o cuando le escribas).
4. Cuando Claude termina, el hub **resume** su respuesta a ≤1500 caracteres (texto plano, sin markdown ni emojis) y ese resumen vuelve al tema como **nota de voz + texto**. Desactívalo con `C4T_VOICE_SUMMARY=0` para que lea la respuesta íntegra.

La nota de voz original no se conserva: se borra tras transcribirla.

### OCR de imágenes

Manda una **imagen** al tema y, además de guardarla, el hub le pasa el **OCR**: el texto extraído se adjunta a la instrucción para Claude y se te muestra un extracto en Telegram.

### Conversar con el modelo

`/chat <mensaje>` habla con el modelo del hub desde cualquier tema; **responde** a su respuesta para seguir la conversación (mantiene algo de contexto). El texto suelto sin nada pendiente sigue devolviendo el resumen de estado.

> El hub atiende **de uno en uno** y cambiar de tipo de modelo (chat→voz→OCR) cuesta unos segundos; el broker serializa las llamadas y usa timeouts holgados. Necesitas `ffmpeg` para la respuesta hablada. Sin hub configurado, estas tres funciones quedan desactivadas y las notas de voz se tratan como archivos normales.

## Cómo se entera cada sesión

Claude Code solo habla con el broker a través de los hooks, así que el broker no puede meter nada en una sesión cuando quiere: aprovecha el siguiente hook.

| Hook | Qué recibe Claude |
|---|---|
| `SessionStart` | Instrucciones para usar `c4t_send.py` con su proyecto y dónde llegarán tus archivos |
| `UserPromptSubmit` | Lo mismo la primera vez en sesiones abiertas antes de instalar, y los archivos en cola |
| `Stop` | Los archivos en cola (la sesión sigue trabajando con ellos) |

## Requisitos

- tmux (opcional, recomendado: permite responder desde el terminal o Telegram, lo que llegue antes; también es lo que permite inyectar instrucciones de voz en vivo)
- `gpg` y `7z` (p7zip) para descifrar archivos (opcional)
- Un hub de modelos local tipo OpenAI (opcional, para voz/OCR/chat) y `ffmpeg` para la respuesta hablada
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
python3 install_hooks.py              # espera hasta 24 h por decisión (--wait 1800 para solo 30 min)
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
| `C4T_PRESENCE_WINDOW` | Segundos tras teclear en el pane tmux durante los que no se manda nada a Telegram (por defecto 120) |
| `C4T_TOPIC_PREFIX` | Prefijo para los nombres de tema, útil con varios equipos |
| `C4T_DANGER_EXTRA` | Regex extra de comandos Bash que nunca se aprueban desde Telegram, separadas por `;;` |
| `C4T_STATE_DIR` | Estado persistente (por defecto `~/.local/state/claude4telegram`) |
| `C4T_SUMMARY_MODEL` | Modelo del 🧠 Resumen IA en proyectos Claude (por defecto `haiku`; vacío = el configurado) |
| `C4T_CLAUDE_BIN` | Ruta al comando `claude` si el servicio no lo encuentra (busca en el PATH y en `~/.local/bin`) |
| `C4T_HUB_URL` | Hub de modelos local (chat/STT/TTS/OCR), p. ej. `http://localhost:8081`; vacío = voz/OCR/chat desactivados |
| `C4T_HUB_KEY` | Bearer del hub (nunca se sube al repo) |
| `C4T_CHAT_MODEL` | Modelo para `/chat` y para reparar transcripciones (por defecto `qwen-coder`) |
| `C4T_TTS_VOICE` | Voz del hub para la respuesta hablada (por defecto `ef_dora`) |
| `C4T_VOICE_SUMMARY` | Resumir con el hub la respuesta hablada a ≤1500 car. (por defecto `1`; `0` para leerla entera) |

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

- Fuera de tmux, mientras un hook espera tu respuesta el terminal de esa sesión también espera. Usa `/aqui` al volver al ordenador.
- Para soltar una petición desde el terminal hay que pulsar una tecla primero; esa pulsación puede no llegar al diálogo, así que respóndelo después.
- Si se agota la espera del hook `Stop`, la sesión queda parada; retómala con `claude --resume`.
- Claude Code corta la tarea tras 8 "Sigue trabajando" seguidos sin usar herramientas.

## Estructura

```
c4t/            broker: bot de Telegram + servidor HTTP local
hooks/          hook de Claude Code, c4t_send.py y c4t_key.py (solo librería estándar)
systemd/        plantilla del servicio de usuario
install_hooks.py  instala/desinstala los hooks en ~/.claude/settings.json
setup.sh        instalación en un equipo nuevo
```

## Licencia

[GPL-3.0](LICENSE): puedes usar, modificar y redistribuir este proyecto, pero las versiones derivadas que distribuyas deben publicarse también bajo GPL-3.0 con su código fuente.
