"""Accès aux couches WFS Seveso (établissements à risque industriel
majeur) — host Mercator (EPSG:3812), namespace `pf`.

3 couches trouvées, 2 confirmées pertinentes en direct (données réelles,
ex. "Acros Organics", Geel) :
- `pf:pf_seveso_ter` (terrein — le site de l'établissement lui-même)
  → colonne EU "Seveso inrichingen".
- `pf:pf_seveso_con` (consultatiezone) → colonne EV "Consultatie zone
  van Seveso-inrichtingen".
- `pf:pf_seveso_irc` (individueel risico contour) — couche technique
  distincte (champ `irc`, valeur de risque), ne correspond à aucune
  colonne du gabarit, non utilisée."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from services.wfs_gewestplan_service import lambert72_vers_3812
from utils.logger import get_logger

_logger = get_logger("services.wfs_seveso_service")

_WFS_BASE = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/ows"


class WfsSevesoService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::3812"

    def _existe(self, layer: str, x_l72: float, y_l72: float, marge_m: float = 5.0) -> Optional[str]:
        x, y = lambert72_vers_3812(x_l72, y_l72)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"pf:{layer}", "count": 1,
            "BBOX": self._bbox(x, y, marge_m),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="seveso_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", layer, x_l72, y_l72, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def seveso_inrichting(self, x: float, y: float) -> Optional[str]:
        """Colonne EU."""
        return self._existe("pf_seveso_ter", x, y)

    def seveso_consultatiezone(self, x: float, y: float) -> Optional[str]:
        """Colonne EV."""
        return self._existe("pf_seveso_con", x, y)
