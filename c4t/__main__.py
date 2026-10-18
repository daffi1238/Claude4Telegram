import asyncio
import logging

from .broker import run
from .config import load_config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
asyncio.run(run(load_config()))
