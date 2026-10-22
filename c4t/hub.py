"""Cliente del hub de modelos local (http://localhost:8081): chat, transcripción (STT),
voz (TTS) y OCR, todo detrás del mismo token.

El hub atiende **de uno en uno** y cambiar de modelo cuesta unos segundos, así que todas
las llamadas pasan por un único lock (nunca en paralelo) y con timeouts holgados. Nada de
esto sale del equipo: el hub corre en localhost."""
import asyncio
from dataclasses import dataclass

import aiohttp

CHAT_TIMEOUT = 180
MEDIA_TIMEOUT = 300    # STT / TTS pueden incluir carga de modelo
OCR_TIMEOUT = 600
CHAT_MAX_TOKENS = 512
HISTORY_MESSAGES = 8   # mensajes recientes que se mandan en una conversación (contexto ~4-5k tokens)


@dataclass
class Hub:
    url: str = ""
    key: str = ""
    chat_model: str = "qwen-coder"
    tts_voice: str = "ef_dora"
    lang: str = "es"

    def __post_init__(self):
        self.url = self.url.rstrip("/")
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    async def _post(self, path: str, timeout: int, **kwargs):
        async with self._lock:  # el hub es serie; serializamos para no encolar ni provocar swaps en paralelo
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                             headers=self._headers()) as s:
                async with s.post(f"{self.url}{path}", **kwargs) as r:
                    r.raise_for_status()
                    if r.content_type and r.content_type.startswith("audio/"):
                        return await r.read()
                    return await r.json()

    async def chat(self, messages: list[dict], model: str | None = None,
                   max_tokens: int = CHAT_MAX_TOKENS) -> str:
        data = await self._post("/v1/chat/completions", CHAT_TIMEOUT, json={
            "model": model or self.chat_model, "messages": messages, "max_tokens": max_tokens})
        return (data["choices"][0]["message"]["content"] or "").strip()

    async def transcribe(self, audio: bytes, filename: str, lang: str | None = None) -> str:
        form = aiohttp.FormData()
        form.add_field("file", audio, filename=filename, content_type="application/octet-stream")
        form.add_field("language", lang or self.lang)
        data = await self._post("/v1/audio/transcriptions", MEDIA_TIMEOUT, data=form)
        return (data.get("text") or "").strip()

    async def speak(self, text: str, voice: str | None = None, lang: str | None = None) -> bytes:
        return await self._post("/v1/audio/speech", MEDIA_TIMEOUT, json={
            "input": text, "voice": voice or self.tts_voice, "language": lang or self.lang})

    async def ocr(self, image: bytes, filename: str) -> str:
        form = aiohttp.FormData()
        form.add_field("file", image, filename=filename, content_type="application/octet-stream")
        data = await self._post("/ocr", OCR_TIMEOUT, data=form)
        return (data.get("text") or "").strip()

    async def health(self) -> dict | None:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as s:
                async with s.get(f"{self.url}/health") as r:
                    return await r.json()
        except Exception:
            return None
