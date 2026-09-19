"""Accès à la couche "Grondwaterwingebieden en beschermingszones" — host
DOV, `geoserver-inspire/drinkingwaterprotectionarea/wfs`. Confirmé en
direct : géométrie en **EPSG:4258** (comme le cadastre fédéral belge,
PAS EPSG:31370 comme la plupart des autres services flamands déjà
câblés) — réutilise `lambert72_vers_4258` de `cadastre_service.py`.
Existence seule → colonne EA."""

from __future__ import annotations

import math
import re
from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.cadastre_service import lambert72_vers_4258
from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_grondwaterwinning_service")

_WFS_BASE = "https://www.dov.vlaanderen.be/geoserver-inspire/drinkingwaterprotectionarea/wfs"


class WfsGrondwaterwinningService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(lat: float, lon: float, marge_m: float = 5.0) -> str:
        dlat = marge_m / 111320
        dlon = marge_m / (111320 * math.cos(math.radians(lat)))
        return f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon},urn:ogc:def:crs:EPSG::4258"

    def grondwaterwingebied(self, x_l72: float, y_l72: float) -> Optional[str]:
        """Colonne EA."""
        lon, lat = lambert72_vers_4258(x_l72, y_l72)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "drinkingwaterprotectionarea:drinkingWaterProtectionArea", "count": 1,
            "BBOX": self._bbox(lat, lon),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="grondwaterwinning_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche indisponible (x=%s, y=%s) : %s", x_l72, y_l72, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"
