"""Pruebas de la lógica pura de claude4Telegram (sin hub, sin Telegram, sin red).

    python3 -m unittest discover -s tests        # o:  .venv/bin/python -m unittest discover -s tests
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from c4t import voice
from c4t.config import load_config
from c4t.media import guess_kind
from c4t.inbox import safe_name, git_root
from c4t.render import clip, esc, provider_of
from c4t.summary import one_line_command, md_bold, short


class Speakable(unittest.TestCase):
    def test_quita_markdown_y_emojis(self):
        out = voice.speakable("## Título 🏁\n**negrita** y `código`\n- viñeta ✅\n[enlace](http://x)")
        self.assertNotIn("**", out)
        self.assertNotIn("#", out)
        self.assertNotIn("`", out)
        self.assertNotIn("🏁", out)
        self.assertNotIn("✅", out)
        self.assertIn("negrita", out)
        self.assertIn("código", out)
        self.assertIn("enlace", out)        # el texto del enlace se conserva
        self.assertNotIn("http", out)       # la URL no

    def test_bloques_de_codigo(self):
        out = voice.speakable("Hecho:\n```bash\ngit push\n```\nfin")
        self.assertIn("(bloque de código)", out)
        self.assertNotIn("git push", out)

    def test_cap_longitud(self):
        self.assertLessEqual(len(voice.speakable("hola " * 1000)), voice.SPEAK_MAX)

    def test_vacio(self):
        self.assertEqual(voice.speakable("   \n  "), "")


class GuessKind(unittest.TestCase):
    def test_tipos(self):
        self.assertEqual(guess_kind("foto.png", 1000), "photo")
        self.assertEqual(guess_kind("clip.gif", 1000), "animation")
        self.assertEqual(guess_kind("nota.ogg", 1000), "voice")
        self.assertEqual(guess_kind("peli.mp4", 1000), "video")
        self.assertEqual(guess_kind("informe.pdf", 1000), "document")

    def test_imagen_grande_es_documento(self):
        self.assertEqual(guess_kind("enorme.png", 50 * 1024 * 1024), "document")


class Providers(unittest.TestCase):
    def test_deteccion(self):
        self.assertEqual(provider_of("https://api.deepseek.com", "x"), "DeepSeek")
        self.assertEqual(provider_of(None, "deepseek-chat"), "DeepSeek")
        self.assertEqual(provider_of(None, None), "Claude")
        self.assertEqual(provider_of("https://api.anthropic.com", "opus"), "Claude")
        self.assertEqual(provider_of("http://localhost:8081", "x"), "localhost:8081")


class SummaryHelpers(unittest.TestCase):
    def test_one_line_command(self):
        self.assertEqual(one_line_command("cd /x/y && git status"), "git status")
        self.assertEqual(one_line_command("ls  -la\n\n"), "ls -la")

    def test_md_bold(self):
        self.assertIn("<b>x</b>", md_bold("**x**"))
        self.assertIn("<b>T</b>", md_bold("# T"))
        self.assertIn("<code>c</code>", md_bold("`c`"))

    def test_short(self):
        self.assertEqual(short("abcdef", 4), "abc…")
        self.assertEqual(short("ab", 4), "ab")


class Inbox(unittest.TestCase):
    def test_safe_name(self):
        self.assertEqual(safe_name("../../etc/passwd"), "passwd")
        self.assertEqual(safe_name("informe final.docx"), "informe_final.docx")
        self.assertTrue(safe_name(""))

    def test_git_root_sin_git(self):
        self.assertEqual(git_root("/"), Path("/"))   # sin .git devuelve el propio directorio


class Render(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(esc("<a> & 'b'"), "&lt;a&gt; &amp; 'b'")

    def test_clip(self):
        self.assertEqual(clip("hola", 10), "hola")
        self.assertTrue(len(clip("x" * 100, 20)) <= 20 + len("\n… (recortado)"))


class ConfigParsing(unittest.TestCase):
    def _load(self, **env):
        base = {"TELEGRAM_BOT_TOKEN": "1:tok", "C4T_SECRET": "s", "C4T_DANGER_EXTRA": "", **env}
        saved = dict(os.environ)
        os.environ.update(base)
        try:
            # evita que un .env real del equipo pise estos valores de prueba
            import c4t.config as cfgmod
            orig = cfgmod.load_env_file
            cfgmod.load_env_file = lambda *a, **k: {}
            try:
                return load_config()
            finally:
                cfgmod.load_env_file = orig
        finally:
            os.environ.clear()
            os.environ.update(saved)

    def test_defaults(self):
        c = self._load()
        self.assertEqual(c.chat_model, "qwen-coder")
        self.assertEqual(c.tts_voice, "ef_dora")
        self.assertTrue(c.voice_summary)
        self.assertFalse(c.hub_url)        # sin hub por defecto

    def test_voice_summary_off(self):
        self.assertFalse(self._load(C4T_VOICE_SUMMARY="0").voice_summary)
        self.assertFalse(self._load(C4T_VOICE_SUMMARY="no").voice_summary)
        self.assertTrue(self._load(C4T_VOICE_SUMMARY="1").voice_summary)

    def test_owner_ids(self):
        self.assertEqual(self._load(TELEGRAM_OWNER_IDS="1, 2 ,3").owner_ids, {1, 2, 3})


if __name__ == "__main__":
    unittest.main()
