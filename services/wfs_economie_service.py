"""Accès aux couches WFS "Bedrijventerreinen" (économie/zones
d'activité) — `geo.api.vlaanderen.be/Bedrijventerreinen/wfs`, trouvée via
un catalogue tiers de services WFS belges (l'URL documentée initialement,
`geoservices.informatievlaanderen.be`, s'est révélée un domaine
INEXISTANT — jamais vérifié en direct avant, corrigé ici).

6 couches confirmées en direct (`GetCapabilities` + `GetFeature` réels),
CRS EPSG:31370 (Lambert 72, comme la position d'adresse du registre —
aucune reprojection nécessaire) :
- `Bedrperc` (parcelle de terrain d'entreprise) — champ `AANGEBODEN`
  ("aangeboden"/"nietAangeboden") → colonne EN.
- `Bedrter`, `Bedrbeh`, `Bedrontw`, `Bedrplan` — existence-only (motif
  `_existe`, comme `wfs_georisques_service.py` côté France) → colonnes
  EO/EP/EQ/ER respectivement, le nom de la couche correspondant
  directement à la catégorie Excel (Bedrter=bedrijventerrein,
  Bedrbeh=beheerde bedrijvenzone, Bedrontw=ontwikkelbare bedrijvenzone,
  Bedrplan=planningszone).

Colonnes ES (Brownfieldconvenanten), ET (Steunzone), EU/EV (Seveso) —
PAS couvertes par ce jeu de données, source différente non encore
identifiée (voir le plan)."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_economie_service")

_WFS_BASE = "https://geo.api.vlaanderen.be/Bedrijventerreinen/wfs"


class WfsEconomieService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def _get_text(self, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"Bedrijventerreinen:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            return self._http.get_text(_WFS_BASE, params, service_key="economie_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", typename, x, y, exc)
            return None

    def _existe(self, typename: str, x: float, y: float) -> Optional[str]:
        """"O"/"N" selon qu'au moins un élément de `typename` intersecte
        un petit tampon autour du point, `None` si la réponse n'est pas
        exploitable — même motif que `wfs_georisques_service.py::_existe`
        côté France."""
        xml = self._get_text(typename, x, y)
        if xml is None:
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            _logger.warning("Couche '%s' (x=%s, y=%s) : réponse sans 'numberReturned' exploitable.", typename, x, y)
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def aangeboden_perceel(self, x: float, y: float) -> Optional[str]:
        """Valeur brute du champ `AANGEBODEN` ("aangeboden"/
        "nietAangeboden"), `None` si aucune parcelle de terrain
        d'entreprise à ce point — colonne EN."""
        xml = self._get_text("Bedrperc", x, y)
        if xml is None:
            return None
        m = re.search(r"<Bedrijventerreinen:AANGEBODEN>([^<]*)</Bedrijventerreinen:AANGEBODEN>", xml)
        return m.group(1).strip() if m else None

    def bedrijventerrein(self, x: float, y: float) -> Optional[str]:
        """Colonne EO."""
        return self._existe("Bedrter", x, y)

    def beheerde_bedrijvenzone(self, x: float, y: float) -> Optional[str]:
        """Colonne EP."""
        return self._existe("Bedrbeh", x, y)

    def ontwikkelbare_bedrijvenzone(self, x: float, y: float) -> Optional[str]:
        """Colonne EQ."""
        return self._existe("Bedrontw", x, y)

    def planningszone(self, x: float, y: float) -> Optional[str]:
        """Colonne ER."""
        return self._existe("Bedrplan", x, y)
