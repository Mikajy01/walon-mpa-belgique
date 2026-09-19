"""Accès à la couche "Afstroomlijnen" (carte de ruissellement, surface
du bassin versant amont en hectares par pixel) — host `waterinfo.be`,
un `ImageServer` ArcGIS (raster continu), PAS un WFS/WMS classique.
Trouvé en explorant l'arborescence complète des services de ce host
(`/arcgis/rest/services?f=json`) — `Oeverzones/Afstroomlijnen`.

Interrogé via l'opération native ArcGIS `identify` (pas `GetFeatureInfo`
WMS) : renvoie directement la valeur du pixel au point donné. Confirmé
en direct : valeur réelle `0.00934113` sur l'adresse test (une place de
centre-ville, cohérent avec un bassin versant quasi nul à cet endroit
précis). Mapping vers les 6 colonnes Excel (paliers de surface, EC→EH)
PAS encore calibré sur une vraie valeur > 0.5 ha — seuils appliqués tels
que décrits dans le gabarit, à confirmer avec un point réel dans chaque
tranche avant mise en production complète."""

from __future__ import annotations

import json
from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_afstromingskaart_service")

_IMAGE_SERVER_BASE = "https://inspirepub.waterinfo.be/arcgis/rest/services/Oeverzones/Afstroomlijnen/ImageServer/identify"

# Paliers du gabarit (ha) -> colonne Excel, voir le docstring module.
_PALIERS = (
    (0.5, 5, "EC"), (5, 10, "ED"), (10, 20, "EE"),
    (20, 50, "EF"), (50, 100, "EG"), (100, float("inf"), "EH"),
)


class WfsAfstromingskaartService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def colonne_afstromingskaart(self, x: float, y: float) -> Optional[str]:
        """Lettre de colonne Excel (EC->EH) selon la surface du bassin
        versant amont (ha) à ce point (Lambert 72) — `None` si la
        valeur est en dessous du premier palier (0,5 ha) ou si la
        couche est indisponible."""
        params = {
            "geometry": json.dumps({"x": x, "y": y, "spatialReference": {"wkid": 31370}}),
            "geometryType": "esriGeometryPoint", "f": "json",
        }
        try:
            texte = self._http.get_text(_IMAGE_SERVER_BASE, params, service_key="afstromingskaart_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'Afstroomlijnen' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        try:
            data = json.loads(texte)
            valeur_ha = float(data["value"])
        except (KeyError, ValueError, TypeError) as exc:
            _logger.warning("Réponse 'Afstroomlijnen' inexploitable (x=%s, y=%s) : %s", x, y, exc)
            return None
        for borne_min, borne_max, colonne in _PALIERS:
            if borne_min <= valeur_ha < borne_max:
                return colonne
        return None
