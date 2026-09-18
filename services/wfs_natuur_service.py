"""Accès aux couches WFS "nature" — 2 hosts distincts, existence seule
(motif `_existe`) :

**Host Mercator (EPSG:3812)** :
- `ps:ps_ven` (VEN — Vlaams Ecologisch Netwerk) → colonne FH.
- `ps:ps_duin` (gebieden duinendecreet) → colonne FG.

**Host `geo.api.vlaanderen.be/RVV/wfs`** (EPSG:31370, trouvé via le
catalogue officiel en cherchant "speciale beschermingszone" — RVV =
"Recht Van Voorkoop", un registre de zones à droit de préemption qui
recoupe plusieurs types de zones protégées) :
- `RVV:Rvvsbz` (speciale beschermingszones) — 2 champs INDÉPENDANTS
  confirmés en direct sur 94 features réelles (38 avec seulement
  `HRLCODE` rempli, 23 avec seulement `VRLCODE`, 33 avec les deux, 0
  avec aucun) : `HRLCODE` (Habitatrichtlijn) → colonne FI,
  `VRLCODE` (Vogelrichtlijn) → colonne FJ. PAS un simple test
  d'existence de la couche : il faut vérifier CE champ précis.
- `RVV:Rvvnr` (réserves naturelles), champ `TYPE`="ENR" (Erkend
  NatuurReservaat) → colonne FF "Erkende Natuurreservaten".

**Host Mercator, namespace `am` (PAS EPSG:3812 comme `ps:ps_ven`/`ps:ps_duin`
-- confirmé en direct via le `srsName` réel de la géométrie renvoyée,
EPSG:31370, donc bbox construite directement à partir de x/y Lambert 72
SANS reprojection)** :
- `am:am_behgebsoortenbescherming` ("Beheergebieden soortenbescherming",
  608 features réelles sur toute la Flandre, champs `naam`/`doelsoort`/
  `numac` confirmés en direct) → colonne FD "Beheergebieden Natura
  2000-soorten".

**Host `geo.api.vlaanderen.be/Bosref/wfs` (EPSG:31370)** :
- `Bosref:Bos` ("Bosreferentielaag 2000") → colonne FE "Bosreservaten".
  CONSIGNE EXPLICITE DE L'EMPLOYEUR (2026-09-17), malgré un écart
  conceptuel CONNU et documenté en direct : cette couche mesure "cette
  parcelle était-elle boisée (n'importe quel bois) vers 1995-2000,
  d'après interprétation d'orthophotos" (référence légale pour les
  compensations de déboisement, Bosdecreet) -- PAS "ce bois est-il une
  réserve forestière protégée" (Bosreservaten = statut de protection
  spécifique, une désignation plus étroite). Aucune mention de
  "reservaat" dans tout l'historique de traitement des métadonnées
  officielles. Utilisée quand même sur consigne explicite : "même si on
  sait que c'est faux, il vaut mieux respecter ce que dit l'employeur"."""

from __future__ import annotations

import re
from typing import Optional

from services.http_client import HttpClient
from services.wfs_gewestplan_service import lambert72_vers_3812
from utils.logger import get_logger

_logger = get_logger("services.wfs_natuur_service")

_MERCATOR_WFS_BASE = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/ows"
_RVV_WFS_BASE = "https://geo.api.vlaanderen.be/RVV/wfs"
_BOSREF_WFS_BASE = "https://geo.api.vlaanderen.be/Bosref/wfs"


class WfsNatuurService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox_3812(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::3812"

    @staticmethod
    def _bbox_31370(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def _existe_mercator(self, namespace: str, layer: str, x_l72: float, y_l72: float, marge_m: float = 5.0) -> Optional[str]:
        x, y = lambert72_vers_3812(x_l72, y_l72)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"{namespace}:{layer}", "count": 1,
            "BBOX": self._bbox_3812(x, y, marge_m),
        }
        try:
            xml = self._http.get_text(_MERCATOR_WFS_BASE, params, service_key="natuur_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s:%s' indisponible (x=%s, y=%s) : %s", namespace, layer, x_l72, y_l72, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def ven(self, x: float, y: float) -> Optional[str]:
        """Colonne FH."""
        return self._existe_mercator("ps", "ps_ven", x, y)

    def duinendecreet(self, x: float, y: float) -> Optional[str]:
        """Colonne FG."""
        return self._existe_mercator("ps", "ps_duin", x, y)

    def _rvvsbz_champ_rempli(self, champ: str, x: float, y: float, marge_m: float = 5.0) -> Optional[str]:
        """"O" si au moins une feature `Rvvsbz` à ce point a le champ
        `champ` (`HRLCODE` ou `VRLCODE`) non vide -- PAS un simple test
        d'existence de la couche (voir le docstring du module)."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "RVV:Rvvsbz", "count": 10,
            "BBOX": self._bbox_31370(x, y, marge_m),
        }
        try:
            xml = self._http.get_text(_RVV_WFS_BASE, params, service_key="natuur_be")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Couche 'RVV:Rvvsbz' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        valeurs = re.findall(rf"<RVV:{champ}>([^<]*)</RVV:{champ}>", xml)
        return "O" if any(v.strip() for v in valeurs) else "N"

    def habitatrichtlijngebied(self, x: float, y: float) -> Optional[str]:
        """Colonne FI."""
        return self._rvvsbz_champ_rempli("HRLCODE", x, y)

    def vogelrichtlijngebied(self, x: float, y: float) -> Optional[str]:
        """Colonne FJ."""
        return self._rvvsbz_champ_rempli("VRLCODE", x, y)

    def erkend_natuurreservaat(self, x: float, y: float) -> Optional[str]:
        """Colonne FF -- existence dans `RVV:Rvvnr` avec `TYPE`="ENR"."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "RVV:Rvvnr", "count": 10,
            "BBOX": self._bbox_31370(x, y),
        }
        try:
            xml = self._http.get_text(_RVV_WFS_BASE, params, service_key="natuur_be")
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Couche 'RVV:Rvvnr' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        types = re.findall(r"<RVV:TYPE>([^<]*)</RVV:TYPE>", xml)
        return "O" if any(t.strip() == "ENR" for t in types) else "N"

    def bosreservaat(self, x: float, y: float) -> Optional[str]:
        """Colonne FE -- existence dans `Bosref:Bos` ("Bosreferentielaag
        2000"), sur consigne explicite de l'employeur malgré l'écart
        conceptuel connu (voir le docstring du module : cette couche
        mesure "est boisé", pas "est une réserve forestière protégée")."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "Bosref:Bos", "count": 1,
            "BBOX": self._bbox_31370(x, y),
        }
        try:
            xml = self._http.get_text(_BOSREF_WFS_BASE, params, service_key="natuur_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'Bosref:Bos' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def beheergebied_natura2000_soorten(self, x: float, y: float) -> Optional[str]:
        """Colonne FD -- existence dans `am:am_behgebsoortenbescherming`
        (host Mercator, EPSG:31370 -- PAS de reprojection 3812, voir le
        docstring du module)."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "am:am_behgebsoortenbescherming", "count": 1,
            "BBOX": self._bbox_31370(x, y),
        }
        try:
            xml = self._http.get_text(_MERCATOR_WFS_BASE, params, service_key="natuur_be")
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'am:am_behgebsoortenbescherming' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"
