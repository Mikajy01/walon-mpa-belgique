"""Accès à la couche OVAM (pollution des sols) — trouvée sur le host
**DOV** (`www.dov.vlaanderen.be/geoserver/ovam/wfs`), PAS sur
`geoserver-extern.ovam.be` (cherché en vain pendant toute la session
précédente — cette dernière URL n'expose que des données "zwerfvuil",
sans rapport). Confirmé en direct : le champ `ovam:kadaster_id`
utilise le même format de référence cadastrale (`caPaKey`) que le
reste du projet.

`ovam:uitspraak_bodemonderzoeken`, champ `uitspraak` — 5 valeurs
réelles observées sur un large échantillon, correspondance directe et
sans ambiguïté avec les 6 colonnes FZ→GE (la 6e, GE "Geen bodeminfo",
correspond à l'ABSENCE de feature à ce point, jamais une valeur du
champ lui-même) :

- "Laatste bodemonderzoek ontdekte geen bodemverontreiniging" -> GA
- "Aanwezige bodemverontreiniging vraagt geen verder onderzoek/
  sanering" -> FZ
- "Aanwezige bodemverontreiniging vraagt verder onderzoek/sanering"
  -> GB
- "Resultaten van bodemonderzoek nog niet verwerkt" -> GC
- "Er is een oriënterend bodemonderzoek nodig" -> GD
- (aucune feature trouvée) -> GE"""

from __future__ import annotations

import re
from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_ovam_service")

_WFS_BASE = "https://www.dov.vlaanderen.be/geoserver/ovam/wfs"

_UITSPRAAK_VERS_COLONNE = {
    "Laatste bodemonderzoek ontdekte geen bodemverontreiniging": "GA",
    "Aanwezige bodemverontreiniging vraagt geen verder onderzoek/sanering": "FZ",
    "Aanwezige bodemverontreiniging vraagt verder onderzoek/sanering": "GB",
    "Resultaten van bodemonderzoek nog niet verwerkt": "GC",
    "Er is een oriënterend bodemonderzoek nodig": "GD",
}


class WfsOvamService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def colonne_bodemverontreiniging(self, x: float, y: float) -> Optional[str]:
        """Renvoie la lettre de colonne Excel (FZ/GA/GB/GC/GD/GE) à
        cocher — "GE" si aucune donnée OVAM trouvée à ce point (jamais
        une simple absence de réponse, voir le docstring du module)."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "ovam:uitspraak_bodemonderzoeken", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="ovam_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'ovam:uitspraak_bodemonderzoeken' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        m = re.search(r"<ovam:uitspraak>([^<]*)</ovam:uitspraak>", xml)
        if not m:
            return "GE"
        uitspraak = m.group(1).strip()
        colonne = _UITSPRAAK_VERS_COLONNE.get(uitspraak)
        if colonne is None:
            _logger.warning("Uitspraak OVAM '%s' inconnue du mapping -- aucune colonne cochée.", uitspraak)
        return colonne
