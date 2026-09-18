"""Accès aux couches WFS "sol" (DOV — Databank Ondergrond Vlaanderen).
Ce host partage la même infrastructure que `mercator.vlaanderen.be`
(même panne, même retour en ligne le 2026-09-16 — confirmé via DNS,
IP identique).

`erosie:so_potbdmerosiepp_2025` (Potentiële bodemerosiekaart per
perceel, `www.dov.vlaanderen.be/geoserver/erosie/wfs`) — champ
`totale_erosie` confirmé en direct (valeurs réelles observées : "zeer
laag", "laag", "medium", "hoog", "verwaarloosbaar", "niet van
toepassing") → colonne EL, existence seule (pas de palier Excel à
distinguer pour cette colonne précise, contrairement à EC→EH qui sont
un jeu de données DIFFÉRENT — "Afstromingskaart" — pas encore trouvé)."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_bodem_service")

_EROSIE_WFS_BASE = "https://www.dov.vlaanderen.be/geoserver/erosie/wfs"


class WfsBodemService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def _existe(self, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"erosie:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(_EROSIE_WFS_BASE, params, service_key="bodem_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'erosie:%s' indisponible (x=%s, y=%s) : %s", typename, x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def potentiele_bodemerosie(self, x: float, y: float) -> Optional[str]:
        """"O"/"N" — colonne EL."""
        return self._existe("so_potbdmerosiepp_2025", x, y)

    def andere_erosiegerelateerde_gronden(self, x: float, y: float) -> Optional[str]:
        """"O"/"N" — colonne EI. Millésime 2014 (le plus récent trouvé,
        confirmé en direct avec 69 features réelles sur un échantillon
        Flandre)."""
        return self._existe("erosie_andere_erosiegerelateerde_gronden_2014", x, y)
