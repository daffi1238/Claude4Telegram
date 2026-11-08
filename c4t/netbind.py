"""Proxy SOCKS5 local que saca las conexiones por una interfaz concreta (p. ej. la red doméstica en vez de
la VPN). Solo lo usa yt-dlp: YouTube bloquea las IPs de VPN compartidas, y así el resto del equipo sigue
por la VPN. Escucha únicamente en 127.0.0.1 y no necesita root (SO_BINDTODEVICE sin privilegios)."""
import asyncio
import socket
import struct

_FAIL = b"\x05\x05\x00\x01" + b"\0" * 6
_OK = b"\x05\x00\x00\x01" + b"\0" * 6


async def _open(host: str, port: int, iface: str):
    loop = asyncio.get_running_loop()
    last: OSError | None = None
    for fam, typ, proto, _, addr in await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sock = socket.socket(fam, typ, proto)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, iface.encode())
            sock.setblocking(False)
            await loop.sock_connect(sock, addr)
            return await asyncio.open_connection(sock=sock)
        except OSError as e:   # p. ej. IPv6 sin ruta por esa interfaz: se prueba la siguiente dirección
            sock.close()
            last = e
    raise last or OSError(f"sin dirección para {host}")


async def _pipe(reader, writer) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def _handle(reader, writer, iface: str) -> None:
    try:
        ver, nmethods = await reader.readexactly(2)
        await reader.readexactly(nmethods)
        if ver != 5:
            writer.close()
            return
        writer.write(b"\x05\x00")   # sin autenticación: solo escucha en 127.0.0.1
        await writer.drain()
        _, cmd, _, atyp = await reader.readexactly(4)
        if atyp == 1:
            host = socket.inet_ntoa(await reader.readexactly(4))
        elif atyp == 3:
            host = (await reader.readexactly((await reader.readexactly(1))[0])).decode()
        elif atyp == 4:
            host = socket.inet_ntop(socket.AF_INET6, await reader.readexactly(16))
        else:
            writer.close()
            return
        port = struct.unpack("!H", await reader.readexactly(2))[0]
        if cmd != 1:   # solo CONNECT
            writer.write(b"\x05\x07\x00\x01" + b"\0" * 6)
            await writer.drain()
            writer.close()
            return
        try:
            up_reader, up_writer = await _open(host, port, iface)
        except OSError:
            writer.write(_FAIL)
            await writer.drain()
            writer.close()
            return
        writer.write(_OK)
        await writer.drain()
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))
    except (ConnectionError, asyncio.IncompleteReadError, ValueError):
        writer.close()


async def start(iface: str) -> tuple[asyncio.AbstractServer, str]:
    """Arranca el proxy en un puerto libre de 127.0.0.1 y devuelve (servidor, URL para --proxy)."""
    server = await asyncio.start_server(lambda r, w: _handle(r, w, iface), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"socks5://127.0.0.1:{port}"
