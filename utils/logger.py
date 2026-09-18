"""Configuration centralisée du logging (console + fichier + mode DEBUG)
— porté tel quel depuis project/utils/logger.py."""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False


def setup_logging(logs_dir: Path, debug: bool = False) -> None:
    """Configure le logger racine une seule fois par exécution du programme."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    logs_dir.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if debug else logging.INFO

    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # sys.stdout est en cp1252 par défaut sur console Windows — tout
    # caractère hors de cette page (ex. "→") fait planter l'emit du
    # handler (UnicodeEncodeError, vu en direct le 2026-08-20 sur un
    # message contenant "H→HV"). errors="backslashreplace" pour ne
    # plus jamais perdre un run entier à cause d'un seul caractère de
    # log — reconfigure() est un no-op inoffensif sur un flux déjà en
    # UTF-8 (ex. Ubuntu/GitHub Actions).
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):
        pass
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(level)
    root.addHandler(console)

    # Repartir d'un fichier vide à chaque exécution — même raisonnement
    # que project/utils/logger.py (déboguer la dernière exécution, sans
    # accumuler un historique illimité). RotatingFileHandler ignore
    # silencieusement mode="w" dès que maxBytes > 0, on tronque donc le
    # fichier explicitement avant de construire le handler.
    log_path = logs_dir / "walon_map_belgique.log"
    log_path.write_text("", encoding="utf-8")
    file_handler = RotatingFileHandler(
        log_path, mode="a", maxBytes=10_000_000, backupCount=1, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(logging.DEBUG)
    root.addHandler(file_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
