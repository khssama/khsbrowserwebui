"""Tiny logging helper with the same look as the rest of the tool."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger("khsbrowserwebui")


def setup(debug: bool = False) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-5s %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    log.handlers[:] = [handler]
    log.setLevel(logging.DEBUG if debug else logging.INFO)
    log.propagate = False
