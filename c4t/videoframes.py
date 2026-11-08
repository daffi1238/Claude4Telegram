"""Fotogramas y tramos de audio de un vídeo (ffmpeg), para que Claude vea las gráficas que comenta el vídeo.

- Fotogramas: se muestrea a `fps` y se descartan los casi idénticos al anterior (mpdecimate), así quedan
  los cambios de escena (una gráfica nueva, una diapositiva) y no cientos de veces la cara del presentador.
- Audio: se trocea en tramos fijos para tener marcas de tiempo, porque el hub solo devuelve el texto."""
import asyncio
import re
from pathlib import Path

FFMPEG_TIMEOUT = 900
FRAME_WIDTH = 768   # suficiente para leer ejes y etiquetas sin disparar el tamaño


async def _ffmpeg(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-hide_banner", "-nostdin", *args,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    try:
        _, err = await asyncio.wait_for(proc.communicate(), FFMPEG_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timeout"
    return proc.returncode, err.decode(errors="replace")


async def extract_audio(video: Path, out: Path) -> Path:
    code, err = await _ffmpeg("-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out))
    if code != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg no pudo sacar el audio: {err.strip()[-200:]}")
    return out


async def split_audio(audio: Path, out_dir: Path, seconds: int) -> list[tuple[Path, int]]:
    """Trocea el audio en tramos de `seconds`. Devuelve [(tramo, segundo_inicial)] en orden."""
    out_dir.mkdir(parents=True, exist_ok=True)
    code, err = await _ffmpeg("-y", "-i", str(audio), "-f", "segment", "-segment_time", str(seconds),
                              "-c", "copy", str(out_dir / "%04d.mp3"))
    if code != 0:
        raise RuntimeError(f"ffmpeg no pudo trocear el audio: {err.strip()[-200:]}")
    parts = sorted(out_dir.glob("*.mp3"))
    return [(p, i * seconds) for i, p in enumerate(parts)]


async def extract_frames(video: Path, out_dir: Path, fps: float, max_frames: int = 0,
                         dedup: bool = True) -> list[tuple[Path, float]]:
    """Fotogramas con su segundo. Con `dedup` se quitan los casi idénticos; si salen más de `max_frames`,
    se reparten uniformes. Sin `dedup` es el almacén completo (uno cada 1/fps s) para elegir después."""
    out_dir.mkdir(parents=True, exist_ok=True)
    decimate = "mpdecimate=hi=64*12:lo=64*5:frac=0.33," if dedup else ""
    vf = f"fps={fps},{decimate}scale={FRAME_WIDTH}:-2,showinfo"
    code, err = await _ffmpeg("-y", "-i", str(video), "-vf", vf, "-fps_mode", "vfr", "-q:v", "4",
                              str(out_dir / "%05d.jpg"))
    if code != 0:
        raise RuntimeError(f"ffmpeg no pudo sacar fotogramas: {err.strip()[-200:]}")
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", err)]
    files = sorted(out_dir.glob("*.jpg"))
    frames = list(zip(files, times))   # showinfo informa una vez por fotograma escrito, en orden
    if max_frames and len(frames) > max_frames:
        step = len(frames) / max_frames
        frames = [frames[int(i * step)] for i in range(max_frames)]
    return frames


def parse_times(text: str) -> list[int]:
    """Segundos de todas las marcas «mm:ss» / «h:mm:ss» de un texto, en orden y sin repetir."""
    out = []
    for m in re.finditer(r"\b(?:(\d{1,2}):)?(\d{1,2}):(\d{2})\b", text):
        h, mi, s = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
        if s < 60:
            t = h * 3600 + mi * 60 + s
            if t not in out:
                out.append(t)
    return out


def frames_near(frames: list[tuple[Path, float]], moments: list[int], before: float = 2,
                after: float = 6, cap: int = 24, per_moment: int = 4) -> list[tuple[Path, float]]:
    """Fotogramas del almacén en [t-before, t+after] de cada momento (la gráfica suele salir justo tras
    mencionarla), sin repetir, en orden temporal y con tope `cap` repartido entre momentos."""
    picked: list[tuple[Path, float]] = []
    per = max(1, min(per_moment, cap // max(1, len(moments))))
    for t in moments:
        near = [f for f in frames if t - before <= f[1] <= t + after and f not in picked]
        if len(near) > per:   # repartidos por la ventana, no solo los primeros
            step = len(near) / per
            near = [near[int(i * step)] for i in range(per)]
        picked += near
    return sorted(picked, key=lambda f: f[1])[:cap]


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"
