"""Archivos que llegan desde Telegram: se guardan en <proyecto>/.c4t/inbox/ y, si vienen cifrados,
se descifran con la contraseña configurada en el equipo (c4t_key.py). La contraseña nunca pasa por Telegram
y se entrega a gpg/7z por stdin, así que tampoco aparece en la lista de procesos."""
import asyncio
import os
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

GPG_EXT = (".gpg", ".pgp", ".asc")
ARCHIVE_EXT = (".7z", ".zip")
TOOL_TIMEOUT = 120


@dataclass
class Incoming:
    path: Path                # lo que debe usar Claude (archivo descifrado o carpeta extraída)
    status: str               # plain | decrypted | no_key | wrong_key | error
    original: str = ""        # nombre del archivo cifrado recibido
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status in ("plain", "decrypted")


def safe_name(name: str) -> str:
    name = re.sub(r"[^\w.\-]+", "_", Path(name).name).strip("._")
    return name[:120] or "archivo"


def inbox_dir(root: str) -> Path:
    base = Path(root) / ".c4t"
    inbox = base / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    ignore = base / ".gitignore"
    if not ignore.exists():
        ignore.write_text("# Creado por Claude4Telegram: nada de aquí debe ir al repo\n*\n")
    return inbox


def stamped(inbox: Path, name: str) -> Path:
    return inbox / f"{time.strftime('%Y%m%d-%H%M%S')}_{safe_name(name)}"


def key_for(keys_dir: Path, project: str) -> str | None:
    for candidate in (keys_dir / f"{safe_name(project)}.key", keys_dir / "default.key"):
        if candidate.is_file():
            return candidate.read_text().rstrip("\n")
    return None


async def run(cmd: list[str], stdin: bytes) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "LC_ALL": "C"})  # mensajes en inglés, para reconocer "contraseña incorrecta"
    try:
        out, _ = await asyncio.wait_for(proc.communicate(stdin), TOOL_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "tiempo agotado"
    return proc.returncode, out.decode(errors="replace")


async def archive_encrypted(path: Path) -> bool:
    _, out = await run(["7z", "l", "-slt", str(path)], b"\n")
    return "Encrypted = +" in out or "Cannot open encrypted" in out


async def process(path: Path, password: str | None) -> Incoming:
    """Descifra si hace falta. Los archivos sin cifrar se dejan tal cual (los .zip normales no se extraen)."""
    suffix = path.suffix.lower()
    if suffix in GPG_EXT:
        if password is None:
            return Incoming(path, "no_key", path.name)
        out = path.with_name(path.stem if path.stem else "descifrado")
        code, msg = await run(["gpg", "--batch", "--yes", "--quiet", "--pinentry-mode", "loopback",
                               "--passphrase-fd", "0", "--decrypt", "-o", str(out), str(path)],
                              password.encode() + b"\n")
        if code != 0:
            out.unlink(missing_ok=True)
            wrong = any(s in msg.lower() for s in ("bad passphrase", "bad session key", "decryption failed"))
            return Incoming(path, "wrong_key" if wrong else "error", path.name, msg.strip()[-300:])
        path.unlink()
        return Incoming(out, "decrypted", path.name)
    if suffix in ARCHIVE_EXT and await archive_encrypted(path):
        if password is None:
            return Incoming(path, "no_key", path.name)
        outdir = path.with_name(path.name[: -len(suffix)])
        code, msg = await run(["7z", "x", "-y", f"-o{outdir}", str(path)], password.encode() + b"\n")
        if code != 0:
            shutil.rmtree(outdir, ignore_errors=True)
            wrong = "wrong password" in msg.lower()
            return Incoming(path, "wrong_key" if wrong else "error", path.name, msg.strip()[-300:])
        path.unlink()
        return Incoming(outdir, "decrypted", path.name)
    return Incoming(path, "plain")


def session_context(project: str, root: str) -> str:
    """Lo que se le cuenta a cada sesión al arrancar, para que sepa usar su tema de Telegram."""
    send = Path(__file__).resolve().parent.parent / "hooks" / "c4t_send.py"
    return (
        f"Claude4Telegram: este proyecto tiene su propio tema («{project}») en el grupo de Telegram del usuario.\n"
        f"- Para mandarle mensajes o archivos (imágenes, audio, vídeo, PDF…) usa:\n"
        f"  python3 {send} --proyecto \"{project}\" --file <ruta> [--file <ruta>…] --texto \"<pie>\"\n"
        f"  (solo --texto para un mensaje). Hazlo cuando el usuario te pida que le envíes algo por Telegram, "
        f"o cuando esté fuera y un resultado visual le ahorre tener que ir al ordenador. No envíes secretos ni datos "
        f"sensibles sin que te lo pida.\n"
        f"- Lo que el usuario te mande desde Telegram se guarda en {Path(root) / '.c4t' / 'inbox'} y se te avisará "
        f"con la ruta; si venía cifrado ya estará descifrado. Nunca le pidas la contraseña de descifrado por Telegram: "
        f"se configura en el equipo con python3 {send.with_name('c4t_key.py')}."
    )


def describe_for_claude(item: Incoming, caption: str | None) -> str:
    if item.path.is_dir():
        files = [p for p in item.path.rglob("*") if p.is_file()]
        what = f"una carpeta con {len(files)} archivo(s) extraída de «{item.original}» (venía cifrado): {item.path}"
    elif item.status == "decrypted":
        what = f"un archivo (venía cifrado como «{item.original}» y ya está descifrado): {item.path}"
    else:
        what = f"un archivo: {item.path}"
    text = f"El usuario te ha enviado por Telegram {what}"
    if caption:
        text += f"\nMensaje que lo acompaña: {caption}"
    return text + "\nRevísalo y actúa según lo que pida; si no está claro qué hacer con él, pregúntale."
