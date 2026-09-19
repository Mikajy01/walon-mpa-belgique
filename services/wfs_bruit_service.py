"""Accès aux couches WFS de bruit (geluidsbelasting) — même host Mercator
(EPSG:3812) que Gewestplan/RUP. 3 couches confirmées en direct, noms
EXACTS vérifiés via le `<Title>` du `GetCapabilities` (jamais déduits du
nom de fichier seul — piège réel : `hh_weg_*` / `hh_weg_aan_*` /
`hh_agglo_weg_*` sont 3 couches DIFFÉRENTES pour "wegen", seul le titre
lève l'ambiguïté) :

- `hh:hh_weg_lnight_2021_c` = "belangrijke wegen Lnight" → colonnes FK→FO
- `hh:hh_lucht_lnight_2021_c` = "belangrijke luchthaven(s) Lnight" → FP→FT
- `hh:hh_spoor_lden_2021_c` = "belangrijke spoorwegen Lden" (PAS Lnight
  — vérifié sur le libellé réel de la colonne FU du gabarit) → FU→FY

PIÈGES RÉELS confirmés en direct en échantillonnant les 3 couches sur
toute la Flandre (bbox large, jusqu'à 2000 entités par couche) :

1. Le nom du champ palier N'EST PAS le même partout : `category`
   (anglais) pour les routes, **`categorie`** (néerlandais) pour
   l'aéroport ET les voies ferrées — une même variable de nom de champ
   pour les 3 aurait échoué silencieusement sur 2 couches sur 3.
2. Les paliers réels observés NE correspondent pas tous exactement aux
   5 colonnes Excel (50-55/55-60/60-65/65-70/>70) :
   - Routes : seulement 4 valeurs vues (`Lnight5054/5559/6064/6569`),
     jamais de palier ≥70 dans les données réelles — colonne FO
     (>70 dB) restera "N" pour les routes tant qu'aucun point réel ne
     prouve le contraire.
   - Aéroport : 5 valeurs propres, `LnightVanaf70` ("à partir de 70")
     correspond proprement à la colonne >70 dB.
   - Voies ferrées (Lden) : paliers DÉCALÉS par rapport aux 4 premières
     colonnes Excel — `Lden5559/6064/6569` seulement (jamais vu
     `Lden5054`), PUIS **2 valeurs séparées** `Lden7074` ET
     `LdenVanaf75` pour ce qui n'est qu'UNE SEULE colonne Excel (FY,
     ">70 dB") — les deux sont donc mappées vers FY.

Mapping code->colonne Excel : voir `_PALIERS_ROUTES`/`_PALIERS_AEROPORT`/
`_PALIERS_RAIL` ci-dessous. Toute valeur reçue mais absente du mapping
est journalisée en warning (jamais silencieusement ignorée) plutôt que
de deviner à quelle colonne elle correspond."""

from __future__ import annotations

import re
from typing import Dict, Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from services.wfs_gewestplan_service import lambert72_vers_3812
from utils.logger import get_logger

_logger = get_logger("services.wfs_bruit_service")

_WFS_BASE = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/ows"

# code palier API -> lettre de colonne Excel (voir le docstring module)
_PALIERS_ROUTES: Dict[str, str] = {
    "Lnight5054": "FK", "Lnight5559": "FL", "Lnight6064": "FM",
    "Lnight6569": "FN", "Lnight7074": "FO", "LnightVanaf70": "FO",
}
_PALIERS_AEROPORT: Dict[str, str] = {
    "Lnight5054": "FP", "Lnight5559": "FQ", "Lnight6064": "FR",
    "Lnight6569": "FS", "LnightVanaf70": "FT",
}
_PALIERS_RAIL: Dict[str, str] = {
    "Lden5054": "FU", "Lden5559": "FV", "Lden6064": "FW",
    "Lden6569": "FX", "Lden7074": "FY", "LdenVanaf75": "FY",
}


class WfsBruitService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::3812"

    def _palier_brut(self, layer: str, champ: str, x_l72: float, y_l72: float, marge_m: float = 5.0) -> Optional[str]:
        x, y = lambert72_vers_3812(x_l72, y_l72)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"hh:{layer}", "count": 1,
            "BBOX": self._bbox(x, y, marge_m),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="bruit_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", layer, x_l72, y_l72, exc)
            return None
        m = re.search(rf"<hh:{champ}>([^<]*)</hh:{champ}>", xml)
        return m.group(1).strip() if m else None

    def colonne_bruit_routes(self, x: float, y: float) -> Optional[str]:
        """Renvoie la lettre de colonne Excel (FK→FO) à cocher, ou
        `None` si aucun palier de bruit routier à ce point."""
        palier = self._palier_brut("hh_weg_lnight_2021_c", "category", x, y)
        return self._resoudre_colonne(palier, _PALIERS_ROUTES, "routes")

    def colonne_bruit_aeroport(self, x: float, y: float) -> Optional[str]:
        """Colonnes FP→FT."""
        palier = self._palier_brut("hh_lucht_lnight_2021_c", "categorie", x, y)
        return self._resoudre_colonne(palier, _PALIERS_AEROPORT, "aéroport")

    def colonne_bruit_voies_ferrees(self, x: float, y: float) -> Optional[str]:
        """Colonnes FU→FY."""
        palier = self._palier_brut("hh_spoor_lden_2021_c", "categorie", x, y)
        return self._resoudre_colonne(palier, _PALIERS_RAIL, "voies ferrées")

    @staticmethod
    def _resoudre_colonne(palier: Optional[str], mapping: Dict[str, str], theme: str) -> Optional[str]:
        if palier is None:
            return None
        colonne = mapping.get(palier)
        if colonne is None:
            _logger.warning(
                "Palier de bruit '%s' (thème %s) inconnu du mapping -- aucune colonne cochée, à investiguer.",
                palier, theme,
            )
        return colonne
