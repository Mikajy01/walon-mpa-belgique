"""Accès aux couches WFS "grondverschuivingen" (glissements de terrain)
— host DOV, workspace dédié `grondverschuivingen`, 2 couches confirmées
en direct :

- `grndversch_gekarteerd` (glissements de terrain cartographiés,
  existence seule) → colonne EJ.
- `grndversch_gevoeligh` (sensibilité, champ `gevoelighd` — ex. "lage
  gevoeligheid", existence seule pour cette colonne, pas de palier
  dans le gabarit) → colonne EK."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_grondverschuiving_service")

_WFS_BASE = "https://www.dov.vlaanderen.be/geoserver/grondverschuivingen/wfs"


class WfsGrondverschuivingService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def _existe(self, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"grondverschuivingen:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="grondverschuiving_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", typename, x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def gekarteerde_grondverschuiving(self, x: float, y: float) -> Optional[str]:
        """Colonne EJ."""
        return self._existe("grndversch_gekarteerd", x, y)

    def gevoeligheid_grondverschuiving(self, x: float, y: float) -> Optional[str]:
        """Colonne EK."""
        return self._existe("grndversch_gevoeligh", x, y)
