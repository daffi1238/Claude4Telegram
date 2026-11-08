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


async def extract_frames(video: Path, out_dir: Path, fps: float, max_frames: int) -> list[tuple[Path, float]]:
    """Fotogramas distintos entre sí con su segundo. Si salen más de `max_frames`, se reparten uniformes."""
    out_dir.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},mpdecimate=hi=64*12:lo=64*5:frac=0.33,scale={FRAME_WIDTH}:-2,showinfo"
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


def mmss(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60:02d}:{s % 60:02d}"
