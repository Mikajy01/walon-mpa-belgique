"""Accès à 2 WFS "geo.api.vlaanderen.be" distincts, tous deux
existence-only (motif `_existe`, comme `wfs_georisques_service.py` côté
France) :

- `Landinrichting/wfs` — 3 couches confirmées en direct correspondant
  CLAIREMENT (titre GetCapabilities identique/quasi-identique au libellé
  Excel) aux colonnes EW/EX/EY. Les 2 couches restantes du service
  (`RvvLandinrpl`, `Hvk`) sont AMBIGUËS par rapport à la colonne FA
  ("Ruilverkaveling") — leurs titres se recoupent partiellement
  ("Herverkaveling"/"recht van voorkoop, landinrichtingsplan") sans
  correspondance exacte ni évidente entre les deux — laissées non
  résolues plutôt que deviner laquelle est la bonne.

- `RVV/wfs` (même registre "Recht Van Voorkoop" que
  `wfs_natuur_service.py`/`wfs_watertoets_service.py`), couche
  `Rvvnir` — SRTTYPELAB confirmé UNIQUE ("Natuurinrichtingsproject" sur
  tout un échantillon large, aucune autre valeur observée) → colonne
  EZ ("Natuurinrichting"), pas trouvée dans le service Landinrichting
  dédié malgré son nom proche.

- `RVV/wfs`, couche `RvvRvkUkw` ("Rvk" = Ruilverkaveling, "Ukw" = Uit
  Kracht van Wet) → colonne FA "Ruilverkaveling (Ruilverkaveling van
  landeigendommen uit kracht van wet)" — correspondance EXACTE et non
  ambiguë avec le libellé complet du gabarit (confirmé en direct :
  champs `CASEKEY`/`BGUNST`="Vlaamse Landmaatschappij"/`NUMAC`, données
  récentes jusqu'à fin 2025), résolvant l'ambiguïté mentionnée plus haut
  entre `RvvLandinrpl`/`Hvk` -- ce n'était ni l'un ni l'autre. La couche
  sœur `RvvRvkGiw` ("Grote Infrastructuurwerken", un autre mécanisme
  légal) existe aussi mais N'EST PAS interrogée ici : le libellé du
  gabarit précise explicitement "uit kracht van wet" seulement.

- `WoningbWoonvernieuwing/wfs` — 2 couches confirmées en direct,
  correspondance directe et sans ambiguïté avec GF/GG.

Trouvés via le catalogue officiel `metadata.vlaanderen.be` (recherche
par mot-clé), pas par essai/erreur de noms de service comme
`wfs_economie_service.py` — beaucoup plus fiable, à privilégier pour les
thèmes restants une fois `mercator.vlaanderen.be` de nouveau joignable."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_landinrichting_woningbouw_service")

_LANDINRICHTING_BASE = "https://geo.api.vlaanderen.be/Landinrichting/wfs"
_WONINGBOUW_BASE = "https://geo.api.vlaanderen.be/WoningbWoonvernieuwing/wfs"
_RVV_BASE = "https://geo.api.vlaanderen.be/RVV/wfs"


def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
    return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"


class _ServiceExistenceWfs:
    """Base commune : bbox Lambert 72 + `numberReturned` — même motif que
    `wfs_georisques_service.py::_existe` côté France."""

    def __init__(self, http: HttpClient, base_url: str, namespace: str, service_key: str) -> None:
        self._http = http
        self._base_url = base_url
        self._namespace = namespace
        self._service_key = service_key

    def _existe(self, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"{self._namespace}:{typename}", "count": 1,
            "BBOX": _bbox(x, y),
        }
        try:
            xml = self._http.get_text(self._base_url, params, service_key=self._service_key)
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s' indisponible (x=%s, y=%s) : %s", typename, x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            _logger.warning("Couche '%s' (x=%s, y=%s) : réponse sans 'numberReturned' exploitable.", typename, x, y)
            return None
        return "O" if int(m.group(1)) > 0 else "N"


class WfsLandinrichtingService(_ServiceExistenceWfs):
    def __init__(self, http: HttpClient) -> None:
        super().__init__(http, _LANDINRICHTING_BASE, "Landinrichting", "landinrichting_be")

    def landinrichting_in_onderzoek(self, x: float, y: float) -> Optional[str]:
        """Colonne EW."""
        return self._existe("LandinrInOnd", x, y)

    def vastgesteld_landinrichtingsproject(self, x: float, y: float) -> Optional[str]:
        """Colonne EX."""
        return self._existe("VastgestLandinrprj", x, y)

    def landinrichtingsplan(self, x: float, y: float) -> Optional[str]:
        """Colonne EY."""
        return self._existe("Landinrpl", x, y)


class WfsNatuurinrichtingService(_ServiceExistenceWfs):
    def __init__(self, http: HttpClient) -> None:
        super().__init__(http, _RVV_BASE, "RVV", "natuurinrichting_be")

    def natuurinrichting(self, x: float, y: float) -> Optional[str]:
        """Colonne EZ."""
        return self._existe("Rvvnir", x, y)


class WfsRuilverkavelingService(_ServiceExistenceWfs):
    def __init__(self, http: HttpClient) -> None:
        super().__init__(http, _RVV_BASE, "RVV", "ruilverkaveling_be")

    def ruilverkaveling_uit_kracht_van_wet(self, x: float, y: float) -> Optional[str]:
        """Colonne FA."""
        return self._existe("RvvRvkUkw", x, y)


class WfsWoningbouwService(_ServiceExistenceWfs):
    def __init__(self, http: HttpClient) -> None:
        super().__init__(http, _WONINGBOUW_BASE, "WoningbWoonvernieuwing", "woningbouw_be")

    def woningbouwgebied(self, x: float, y: float) -> Optional[str]:
        """Colonne GF."""
        return self._existe("Woningwbgb", x, y)

    def woonvernieuwingsgebied(self, x: float, y: float) -> Optional[str]:
        """Colonne GG."""
        return self._existe("Woningwvgb", x, y)
