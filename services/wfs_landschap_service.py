"""Accès aux données "landschap" (paysage) — colonnes FB/FC.

- FB "Fysische systeemeenheden" : PAS de WFS documenté, seulement WMS
  (`geo.api.vlaanderen.be/VLM/wms`, couche `FysSyst`) — accès via
  `GetFeatureInfo` (même motif que
  `wfs_watertoets_service.py::_gridcode_wms`), confirmé en direct :
  champ `FYSCODE`/`OMSCHR` (ex. `OMSCHR="Onbepaald"`).
- FC "Traditionele landschappen" : WFS confirmé,
  `geo.api.vlaanderen.be/TradLa/wfs`, couche `TradLa:Tradla`."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_landschap_service")

_VLM_WMS_BASE = "https://geo.api.vlaanderen.be/VLM/wms"
_TRADLA_WFS_BASE = "https://geo.api.vlaanderen.be/TradLa/wfs"


class WfsLandschapService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m}"

    def fysische_systeemeenheid(self, x: float, y: float) -> Optional[str]:
        """Champ `OMSCHR` du système physique — colonne FB. `None` si
        aucune donnée à ce point."""
        params = {
            "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
            "layers": "FysSyst", "query_layers": "FysSyst", "crs": "EPSG:31370",
            "bbox": self._bbox(x, y), "width": "101", "height": "101", "i": "50", "j": "50",
            "info_format": "text/xml",
        }
        try:
            xml = self._http.get_text(_VLM_WMS_BASE, params, service_key="landschap_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'FysSyst' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        m = re.search(r'OMSCHR="([^"]*)"', xml)
        return m.group(1).strip() if m else None

    def traditioneel_landschap(self, x: float, y: float) -> Optional[str]:
        """Existence seule — colonne FC."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "TradLa:Tradla", "count": 1,
            "BBOX": f"{self._bbox(x, y)},urn:ogc:def:crs:EPSG::31370",
        }
        try:
            xml = self._http.get_text(_TRADLA_WFS_BASE, params, service_key="landschap_be")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Couche 'TradLa:Tradla' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"
