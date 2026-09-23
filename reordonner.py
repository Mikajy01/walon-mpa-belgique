"""Orchestrateur CLI — réordonnance d'un fichier d'état DÉJÀ traité (voir
`services/reordonnancement_service.py` pour le principe complet et pourquoi
c'est une passe SÉPARÉE de `main.py`).

Beaucoup plus léger que `main.py` : ne recalcule AUCUNE colonne thématique
(pas de `ResolveurBE`, pas des 18 services WFS) -- seulement les positions,
via `adressen`/`cadastre`/`geometrique`, pour retrouver l'ordre le long de
chaque rue."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional

import config
from main import chemin_etat_commune
from services.adressen_service import AdressenService
from services.cache_service import HttpCache
from services.cadastre_service import CadastreService
from services.decouverte_geometrique_service import DecouverteGeometrique
from services.http_client import HttpClient
from services.reordonnancement_service import reordonner_fichier
from services.wegenregister_service import WegenregisterService
from utils.logger import get_logger, setup_logging
from utils.rate_limiter import RateLimiter

_logger = get_logger("reordonner")


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Réordonne un fichier d'état déjà traité (voir le module pour le principe).")
    parser.add_argument("--commune", required=True)
    parser.add_argument("--code-postal", required=True)
    parser.add_argument("--state-dir", default=str(config.STATE_DIR))
    parser.add_argument("--cache-dir", default=str(config.CACHE_DIR))
    parser.add_argument("--logs-dir", default=str(config.BASE_DIR / "logs"))
    parser.add_argument("--debug", action="store_true")
    parser.add_argument(
        "--rayon-geometrique-m", type=float, default=10.0,
        help="Même rôle que dans main.py -- doit correspondre au rayon utilisé lors du/des run(s) réel(s) "
             "sur cette commune (10 par défaut, 50 pour une commune rurale déjà traitée avec ce réglage).",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(Path(args.logs_dir), debug=args.debug)

    excel_path = chemin_etat_commune(Path(args.state_dir), args.commune, args.code_postal)
    if not excel_path.exists():
        _logger.error("Aucun fichier d'état pour '%s' (%s) -- rien à réordonner.", args.commune, excel_path)
        return 1

    cache = HttpCache(Path(args.cache_dir))
    http = HttpClient(cache, RateLimiter(config.MAX_REQUESTS_PER_SECOND), timeout=config.HTTP_TIMEOUT_SECONDS)
    adressen = AdressenService(http)
    cadastre = CadastreService(http)
    geometrique = DecouverteGeometrique(WegenregisterService(http), cadastre, rayon_m=args.rayon_geometrique_m)

    reordonner_fichier(excel_path, args.commune, adressen, cadastre, geometrique)
    return 0


if __name__ == "__main__":
    sys.exit(main())
