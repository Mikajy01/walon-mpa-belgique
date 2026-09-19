"""Accès aux couches WFS/WMS "waterinfo.be" (risque d'inondation
flamand) — voir le plan pour le détail des colonnes DC→EB couvertes.

**Couches WFS** (`inspirepub.waterinfo.be/.../waterinfo_WFS`) :
`Overstromingsgevoelige_gebieden_2017` (champ `overstrgev` :
"mogelijk"/"effectief" → DC/DD), `Goedgekeurde_signaalgebieden` (champ
`Categorie` : "Bouwvrije opgave"/"Verscherpte watertoets" → DT/DU),
`Risicozones_2017` (champ `risicozone` → DX).

**Couches WMS, accès via `GetFeatureInfo`** (`inspirepub.waterinfo.be/
.../informatieplicht/<nom>/MapServer/WMSServer`) — PAS de WFS activé sur
ces MapServer précis (`WFSServer` renvoie une erreur ArcGIS générique
HTTP 400, confirmé en direct), mais `GetFeatureInfo` fonctionne et
renvoie un XML `<FIELDS>` exploitable par une simple regex, même esprit
que le parsing GML du reste du projet :
`overstromingsgevoelige_gebieden_pluviaal`/`_fluviaal`/`_vanuit_de_zee`
— champ `gridcode` → colonnes DF→DI (Pluvial), DJ→DM (Fluvial), DN→DQ
(vanuit zee). Légende OFFICIELLE confirmée en direct (endpoint natif
ArcGIS `.../MapServer/0?f=json`, `drawingInfo.renderer`, identique sur
les 3 jeux de données) : `0`="A - Geen overstroming gemodelleerd",
`1`="B - Kleine kans op overstromingen onder klimaatverandering",
`2`="C - Kleine kans op overstromingen", `3`="D - Middelgrote kans op
overstromingen" — l'ABSENCE de feature à un point (réponse XML sans
`<FIELDS>`) signifie littéralement le palier 0/A, pas une erreur
(confirmé via la légende, jamais deviné).

**Couches WFS supplémentaires** (`geo.api.vlaanderen.be`, trouvées via
le catalogue officiel `metadata.vlaanderen.be`, PAS `mercator.vlaanderen.be`) :
`NOG/wfs` (`NOG:Nog`, champ `LBLNATOORZ` → colonne DW),
`OGOZ/wfs` (`OGOZ:Ogoz`, existence seule → colonne DZ),
`RVV/wfs` (`RVV:Rvviwb`, champ `SRTTYPELAB` : "IWB Oeverzone"/"IWB
Overstromingsgebied" → colonnes DR/DS — correspondance floue avec le
libellé du gabarit ("IWB OeverzoneI"/"WB Overstromingsgebied", scores
0.96/0.98, voir `utils/text_normalize.py`) : le même registre RVV que
`wfs_natuur_service.py`, EPSG:31370).

**Couche ArcGIS REST (pas WFS/WMS -- `query` natif JSON)**
(`inspirepub.waterinfo.be/arcgis/rest/services/waterinfo/Watertoetskaarten/
MapServer/6`, trouvée en explorant l'arborescence COMPLÈTE des services de
cet host via `/arcgis/rest/services/<dossier>?f=json`, dossier par
dossier -- la couche `VMMWatertoets/wms` initialement supposée pour DV
s'est révélée injoignable/mal enregistrée derrière le proxy
`geo.api.vlaanderen.be`, HTTP 400 générique ArcGIS quel que soit le
paramètre envoyé, y compris sans AUCUN paramètre -- signe que le WMS
n'est simplement pas activé sur ce service précis, pas un problème de
requête) : couche "ROG 2017" (id 6, "Recent Overstroomde Gebieden",
14552 features réelles sur toute la Flandre, champs `ROGID`/`TITEL`/
`GEBIED`/`DATUMSTART` confirmés en direct, ex. "Inventarisatie van de
overstromingen in de Demervallei van september 1998" trouvé à
Sint-Truiden) → colonne DY, existence seule (`query?geometry=...&
geometryType=esriGeometryPoint&inSR=31370&spatialRel=
esriSpatialRelIntersects&distance=<marge>&units=esriSRUnit_Meter`).

Colonnes DE, DV, EB — recherche approfondie mais TOUJOURS pas de source
confirmée : DE ("Beheergebieden beheerovereenkomst waterkwaliteit") a
une fiche officielle `metadata.vlaanderen.be` (uuid
`0b5002ba-b14c-436b-afb3-9db5bff8fb77`, récupérée en direct via
`/srv/api/records/<uuid>/formatters/xml`, PAS via une réponse
synthétisée par recherche web) qui ne liste AUCUN protocole WFS/WMS --
seulement un produit téléchargeable (`download.vlaanderen.be`, id 4180).
DV ("Grondwaterstromingsgevoelige gebieden") et EB ("Infiltratiegevoelige
bodems") apparaissent encore dans les DESCRIPTIONS de 2 services
`inspirepub.waterinfo.be` (`afstroomgebieden_watertoets`,
`waterinfo/Watertoetskaarten`) mais AUCUNE couche correspondante n'existe
réellement dans leur liste de couches (vérifié couche par couche, id par
id -- 404 "Layer not found" sur tous les ids non listés) : cohérent avec
l'annonce officielle de la "vernieuwde watertoets" du 1/1/2023
(integraalwaterbeleid.be) qui a consolidé les anciens critères
individuels dans une "Advieskaart watertoets" unique (déjà colonne EM,
`wfs_advieskaart_service.py`) -- dont le schéma de champs réel
(`CAPAKEY`/`Adviesinst`/`VMM`/`DVW`/`haven`/`MDK`/`penw`) confirme
l'absence de toute décomposition par critère individuel. Probable
retrait définitif de ces 2 critères comme couches publiques
individuelles, pas une simple couche non trouvée."""

from __future__ import annotations

import re
from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_watertoets_service")

_WFS_BASE = "http://inspirepub.waterinfo.be/arcgis/services/waterinfo_WFS/MapServer/WFSServer"
_WMS_INFORMATIEPLICHT_BASE = "https://inspirepub.waterinfo.be/arcgis/services/informatieplicht/{dataset}/MapServer/WMSServer"
_NOG_WFS_BASE = "https://geo.api.vlaanderen.be/NOG/wfs"
_OGOZ_WFS_BASE = "https://geo.api.vlaanderen.be/OGOZ/wfs"
_RVV_WFS_BASE = "https://geo.api.vlaanderen.be/RVV/wfs"
_ROG_ARCGIS_QUERY = (
    "https://inspirepub.waterinfo.be/arcgis/rest/services/waterinfo/"
    "Watertoetskaarten/MapServer/6/query"
)


class WfsWatertoetsService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    # -- bbox Lambert 72 (mètres, pas de conversion degrés) -------------

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    # -- couches WFS classiques ------------------------------------------

    def _valeur_champ_wfs(self, base_url: str, namespace: str, typename: str, champ: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"{namespace}:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(base_url, params, service_key="watertoets")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche '%s:%s' indisponible (x=%s, y=%s) : %s", namespace, typename, x, y, exc)
            return None
        m = re.search(rf"<{namespace}:{champ}>([^<]*)</{namespace}:{champ}>", xml)
        return m.group(1).strip() if m else None

    def _existe_wfs(self, base_url: str, namespace: str, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"{namespace}:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(base_url, params, service_key="watertoets")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Couche '%s:%s' indisponible (x=%s, y=%s) : %s", namespace, typename, x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def overstromingsgevoelig(self, x: float, y: float) -> Optional[str]:
        """"mogelijk"/"effectief"/`None` — colonnes DC/DD."""
        return self._valeur_champ_wfs(_WFS_BASE, "waterinfo_WFS", "Overstromingsgevoelige_gebieden_2017", "overstrgev", x, y)

    def signaalgebied_categorie(self, x: float, y: float) -> Optional[str]:
        """"Bouwvrije opgave"/"Verscherpte watertoets"/`None` — colonnes DT/DU."""
        return self._valeur_champ_wfs(_WFS_BASE, "waterinfo_WFS", "Goedgekeurde_signaalgebieden", "Categorie", x, y)

    def risicozone(self, x: float, y: float) -> Optional[str]:
        """Colonne DX."""
        return self._valeur_champ_wfs(_WFS_BASE, "waterinfo_WFS", "Risicozones_2017", "risicozone", x, y)

    def van_nature_overstroombaar(self, x: float, y: float) -> Optional[str]:
        """Champ `LBLNATOORZ` — colonne DW."""
        return self._valeur_champ_wfs(_NOG_WFS_BASE, "NOG", "Nog", "LBLNATOORZ", x, y)

    def overstromingsgebied_oeverzone_iwb(self, x: float, y: float) -> Optional[str]:
        """Existence seule — colonne DZ."""
        return self._existe_wfs(_OGOZ_WFS_BASE, "OGOZ", "Ogoz", x, y)

    def colonne_afgebakend_oeverzone_iwb(self, x: float, y: float, marge_m: float = 5.0) -> Optional[str]:
        """Renvoie "DR" ("IWB Oeverzone"), "DS" ("IWB Overstromingsgebied",
        correspond au libellé du gabarit "WB Overstromingsgebied" via
        correspondance floue, score 0.98) ou `None` si aucune des deux
        `SRTTYPELAB` de `RVV:Rvviwb` à ce point."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "RVV:Rvviwb", "count": 10,
            "BBOX": f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370",
        }
        try:
            xml = self._http.get_text(_RVV_WFS_BASE, params, service_key="watertoets")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'RVV:Rvviwb' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        labels = set(re.findall(r"<RVV:SRTTYPELAB>([^<]*)</RVV:SRTTYPELAB>", xml))
        if "IWB Oeverzone" in labels:
            return "DR"
        if "IWB Overstromingsgebied" in labels:
            return "DS"
        return None

    # -- couches WMS (GetFeatureInfo, pas de WFS sur ces MapServer) -----

    def _gridcode_wms(self, dataset: str, x: float, y: float, marge_m: float = 5.0) -> Optional[str]:
        """`GetFeatureInfo` sur le MapServer "informatieplicht/<dataset>",
        renvoie le `gridcode` BRUT (chaîne) trouvé, ou `"0"` si la
        réponse est vide/exploitable mais sans `<FIELDS>` — confirmé en
        direct via la légende officielle du serveur (`.../MapServer/0?
        f=json`, `drawingInfo.renderer`) : l'ABSENCE de feature à ce
        point signifie littéralement le palier "A - Geen overstroming
        gemodelleerd" (gridcode 0), pas une erreur. Renvoie `None`
        SEULEMENT en cas de vraie erreur réseau/service (jamais deviné
        comme "0")."""
        url = _WMS_INFORMATIEPLICHT_BASE.format(dataset=dataset)
        params = {
            "service": "WMS", "version": "1.3.0", "request": "GetFeatureInfo",
            "layers": "0", "query_layers": "0", "crs": "EPSG:31370",
            "bbox": f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m}",
            "width": "101", "height": "101", "i": "50", "j": "50",
            "info_format": "text/xml",
        }
        try:
            xml = self._http.get_text(url, params, service_key="watertoets_wms")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.warning("Couche WMS '%s' indisponible (x=%s, y=%s) : %s", dataset, x, y, exc)
            return None
        m = re.search(r'gridcode="([^"]*)"', xml)
        return m.group(1) if m else "0"

    # Légende officielle confirmée en direct (identique sur les 3 jeux
    # de données pluviaal/fluviaal/vanuit_de_zee, vérifié séparément
    # pour chacun) : 0=A-Geen, 1=B-Kleine kans (climat), 2=C-Kleine kans,
    # 3=D-Middelgrote kans -- ordre croissant = probabilité croissante.
    _GRIDCODE_VERS_COLONNE = {"0": 0, "1": 1, "2": 2, "3": 3}

    def _colonne_palier(self, gridcode: Optional[str], colonnes: tuple[str, str, str, str]) -> Optional[str]:
        if gridcode is None:
            return None
        idx = self._GRIDCODE_VERS_COLONNE.get(gridcode)
        if idx is None:
            _logger.warning("gridcode '%s' inconnu de la légende confirmée -- aucune colonne cochée.", gridcode)
            return None
        return colonnes[idx]

    def colonne_overstromingsgevoelig_pluviaal(self, x: float, y: float) -> Optional[str]:
        """Colonnes DF→DI (palier de probabilité pluvial)."""
        gridcode = self._gridcode_wms("overstromingsgevoelige_gebieden_pluviaal", x, y)
        return self._colonne_palier(gridcode, ("DF", "DG", "DH", "DI"))

    def colonne_overstromingsgevoelig_fluviaal(self, x: float, y: float) -> Optional[str]:
        """Colonnes DJ→DM (palier de probabilité fluvial)."""
        gridcode = self._gridcode_wms("overstromingsgevoelige_gebieden_fluviaal", x, y)
        return self._colonne_palier(gridcode, ("DJ", "DK", "DL", "DM"))

    def colonne_overstromingsgevoelig_zee(self, x: float, y: float) -> Optional[str]:
        """Colonnes DN→DQ (palier de probabilité depuis la mer)."""
        gridcode = self._gridcode_wms("overstromingsgevoelige_gebieden_vanuit_de_zee", x, y)
        return self._colonne_palier(gridcode, ("DN", "DO", "DP", "DQ"))

    def recent_overstroomd(self, x: float, y: float, marge_m: float = 5.0) -> Optional[str]:
        """"O"/"N" -- colonne DY. Requête ArcGIS REST native (`query`,
        JSON) sur la couche "ROG 2017", PAS un WFS/WMS classique -- voir
        le docstring du module. `distance`/`units` remplacent le motif
        BBOX habituel (plus simple pour un point + tolérance sur cette
        API précise)."""
        params = {
            "geometry": f"{x},{y}", "geometryType": "esriGeometryPoint", "inSR": "31370",
            "spatialRel": "esriSpatialRelIntersects", "distance": marge_m, "units": "esriSRUnit_Meter",
            "returnGeometry": "false", "returnCountOnly": "true", "f": "json",
        }
        try:
            data = self._http.get_json(_ROG_ARCGIS_QUERY, params, service_key="watertoets_arcgis")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'ROG 2017' indisponible (x=%s, y=%s) : %s", x, y, exc)
            return None
        if not isinstance(data, dict) or "count" not in data:
            _logger.warning("Couche 'ROG 2017' (x=%s, y=%s) : réponse sans 'count' exploitable : %s", x, y, data)
            return None
        return "O" if data["count"] > 0 else "N"
