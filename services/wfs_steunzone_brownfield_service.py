"""Accès aux couches "Steunzone" et "Brownfieldconvenanten" — host
`geo.api.vlaanderen.be/VLAIO/wms` (VLAIO — Agentschap Innoveren en
Ondernemen). PAS de WFS sur ce host précis (`VLAIO/wfs` redirige vers
une page d'erreur, confirmé en direct) — accès via `GetFeatureInfo`
(même motif que `wfs_landschap_service.py::fysische_systeemeenheid`).

2 couches confirmées en direct avec de vraies données (ex. "Steunzone
Genk", "Stationsomgeving Diest") : `Steunzone` → colonne ET,
`Brownf` → colonne ES. Existence seule (pas de sous-catégorie dans le
gabarit pour ces 2 colonnes)."""

from __future__ import annotations

from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_steunzone_brownfield_service")

_WMS_BASE = "https://geo.api.vlaanderen.be/VLAIO/wms"


class WfsSteunzoneBrownfieldService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def _existe(self, layer: str, x: float, y: float, marge_m: float = 5.0) -> Optional[str]:
        params = {
            "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
            "layers": layer, "query_layers": layer, "crs": "EPSG:31370",
            "bbox": f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m}",
            "width": "101", "height": "101", "i": "50", "j": "50",
            "info_format": "text/xml",
        }
        try:
            xml = self._http.get_text(_WMS_BASE, params, service_key="vlaio_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", layer, x, y, exc)
            return None
        return "O" if "<FIELDS" in xml else "N"

    def steunzone(self, x: float, y: float) -> Optional[str]:
        """Colonne ET."""
        return self._existe("Steunzone", x, y)

    def brownfieldconvenant(self, x: float, y: float) -> Optional[str]:
        """Colonne ES."""
        return self._existe("Brownf", x, y)
