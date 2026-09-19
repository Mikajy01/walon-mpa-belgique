"""Accès au WFS Gewestplan (plan de secteur) — Mercator/Departement
Omgeving. Confirmé en direct (2026-09-16, après la panne serveur des
jours précédents) : `https://www.mercator.vlaanderen.be/
raadpleegdienstenmercatorpubliek/ows`, couche vecteur **`lu:lu_gwp_gv`**
(PAS `lu_gwp_rv_raster`, une image).

PIÈGE RÉEL confirmé en direct : ce service utilise **EPSG:3812**
(Lambert Belge 2008), PAS EPSG:31370 (Lambert 72) comme le reste des
services flamands déjà câblés (watertoets, économie, landinrichting,
woningbouw, landschap) — vérifié via `srsName` dans la géométrie
renvoyée. Nécessite une reprojection depuis le point d'adresse (Lambert
72) via `pyproj`.

Champ de catégorie confirmé : `lu:svnaam` (texte, ex. "woongebieden met
landelijk karakter") — correspond au libellé de la colonne Excel
concernée, à comparer via `utils.text_normalize.normaliser` (le gabarit
contient au moins une faute de frappe connue, "culturelke" au lieu de
"cultureel", confirmée en direct sur un vrai retour d'API).

CORRECTION du 2026-09-17 (5 lignes à "double zone" trouvées dans le
fichier réel, ex. Gippershovenstraat 11) : le simple filtre BBOX (petit
buffer de 5m autour du point) peut renvoyer une zone VOISINE quand le
point est proche d'une vraie frontière Gewestplan, alors que le plan de
secteur est une PARTITION STRICTE du territoire (jamais 2 catégories
réellement superposées à un même point). Confirmé en direct sur
Gippershovenstraat 11 : un simple test point-dans-polygone (anneaux
extérieurs moins trous intérieurs) sur la géométrie complète déjà
renvoyée par le WFS élimine correctement la zone voisine ("woongebieden
met landelijk karakter") et ne garde que la vraie ("agrarische
gebieden"). Filet de sécurité : si le test précis n'élimine RIEN de
concluant (aucune des features candidates ne contient précisément le
point -- cas limite non encore rencontré), on retombe sur l'ancien
comportement (tout ce qui est dans le buffer) plutôt que de renvoyer
une liste vide à tort."""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

import requests

from services.exceptions import ApiServiceError

import pyproj

import config
from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_gewestplan_service")

_WFS_BASE = "https://www.mercator.vlaanderen.be/raadpleegdienstenmercatorpubliek/ows"
_EPSG_MERCATOR = "EPSG:3812"

_transformer_l72_vers_3812 = pyproj.Transformer.from_crs("EPSG:31370", _EPSG_MERCATOR, always_xy=True)


def lambert72_vers_3812(x: float, y: float) -> tuple[float, float]:
    return _transformer_l72_vers_3812.transform(x, y)


def _point_dans_anneau(px: float, py: float, anneau: List[Tuple[float, float]]) -> bool:
    """Ray casting standard -- vrai si (px, py) est à l'intérieur du
    polygone simple défini par `anneau` (liste de sommets, fermé ou non)."""
    n = len(anneau)
    dedans = False
    j = n - 1
    for i in range(n):
        xi, yi = anneau[i]
        xj, yj = anneau[j]
        if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi:
            dedans = not dedans
        j = i
    return dedans


def _parse_poslist(texte: str) -> List[Tuple[float, float]]:
    vals = texte.split()
    return [(float(vals[i]), float(vals[i + 1])) for i in range(0, len(vals), 2)]


def _point_dans_geometrie(px: float, py: float, bloc_xml: str) -> bool:
    """Vrai si (px, py) est réellement à l'intérieur de la géométrie du
    membre WFS `bloc_xml` -- teste chaque `gml:Polygon` (un `MultiSurface`
    peut en avoir plusieurs), anneau extérieur MOINS les anneaux
    intérieurs (trous) -- voir le docstring du module."""
    for polygone in re.findall(r"<gml:Polygon\b.*?</gml:Polygon>", bloc_xml, re.DOTALL):
        m_ext = re.search(r"<gml:exterior>.*?<gml:posList>([^<]*)</gml:posList>.*?</gml:exterior>", polygone, re.DOTALL)
        if not m_ext:
            continue
        if not _point_dans_anneau(px, py, _parse_poslist(m_ext.group(1))):
            continue
        trous = re.findall(r"<gml:interior>.*?<gml:posList>([^<]*)</gml:posList>.*?</gml:interior>", polygone, re.DOTALL)
        if any(_point_dans_anneau(px, py, _parse_poslist(t)) for t in trous):
            continue  # dans un trou de CE polygone -- pas réellement dedans
        return True
    return False


class WfsGewestplanService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::3812"

    def svnaam_categories(self, x_l72: float, y_l72: float, marge_m: float = 5.0) -> List[str]:
        """Renvoie la/les valeur(s) `svnaam` trouvée(s) au point donné
        (coordonnées Lambert 72, reprojetées en interne) — normalement 1
        seule. Filtre par test géométrique précis (point-dans-polygone,
        voir `_point_dans_geometrie`) parmi les features candidates du
        buffer, pour éliminer une zone VOISINE attrapée à tort près
        d'une frontière réelle (voir le docstring du module) — ne
        renvoie plusieurs valeurs que si le point est VRAIMENT dans
        plusieurs géométries à la fois (jamais rencontré en pratique,
        le Gewestplan étant une partition stricte, mais pas exclu par
        construction)."""
        x, y = lambert72_vers_3812(x_l72, y_l72)
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "lu:lu_gwp_gv", "count": 10,
            "BBOX": self._bbox(x, y, marge_m),
        }
        try:
            xml = self._http.get_text(_WFS_BASE, params, service_key="gewestplan_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Gewestplan indisponible (x=%s, y=%s) : %s", x_l72, y_l72, exc)
            return []
        blocs = xml.split("<wfs:member>")[1:]
        candidats: List[Tuple[str, bool]] = []
        for bloc in blocs:
            m = re.search(r"<lu:svnaam>([^<]*)</lu:svnaam>", bloc)
            if not m:
                continue
            candidats.append((m.group(1).strip(), _point_dans_geometrie(x, y, bloc)))
        precis = [nom for nom, dedans in candidats if dedans]
        if precis:
            if len(candidats) > 1 and len(precis) < len(candidats):
                _logger.info(
                    "Gewestplan (x=%s, y=%s) : %d candidat(s) dans le buffer, %d retenu(s) après test "
                    "point-dans-polygone (zone(s) voisine(s) écartée(s)).",
                    x_l72, y_l72, len(candidats), len(precis),
                )
            return precis
        if candidats:
            # Filet de sécurité : le test précis n'a RIEN retenu (cas
            # limite jamais rencontré) -- ancien comportement plutôt
            # qu'une liste vide à tort (voir le docstring du module).
            _logger.warning(
                "Gewestplan (x=%s, y=%s) : test point-dans-polygone n'a retenu aucun des %d candidat(s) du "
                "buffer -- repli sur tous les candidats (jamais une liste vide à tort).",
                x_l72, y_l72, len(candidats),
            )
            return [nom for nom, _ in candidats]
        return []
