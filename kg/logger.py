"""Tiny logging config for the knowledge-graph core.

Uvicorn already wires its own access/error handlers; here we just make
sure our own kg.* loggers print at INFO with a readable format and the
extractor name visible.

Call `setup()` once from the app's lifespan.
"""

from __future__ import annotations

import logging
import os


def setup() -> None:
    level_name = os.getenv("KG_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
    datefmt = "%H:%M:%S"

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))

    root = logging.getLogger("kg")
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
