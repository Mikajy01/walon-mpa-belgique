"""Accès à la géométrie des parcelles cadastrales belges — WFS fédéral
INSPIRE (FOD Financiën / FPS Finance), `cp:CadastralParcel`.

PIÈGE RÉEL confirmé en direct en construisant ce module : `CQL_FILTER`
sur `label` OU sur `nationalCadastralReference` ne filtre PAS réellement
côté serveur — une requête `CQL_FILTER=nationalCadastralReference='X'`
renvoie des parcelles QUELCONQUES, sans rapport avec `X` (vérifié deux
fois, avec deux valeurs de filtre différentes, deux résultats
différents mais TOUJOURS faux). Ce WFS ArcGIS-hébergé ignore
silencieusement ce paramètre plutôt que de renvoyer une erreur ou un
résultat vide — donc **ne jamais faire confiance à un résultat CQL_FILTER
sans vérifier la référence réellement renvoyée**. Seule méthode fiable
trouvée : interroger par BBOX autour d'un point connu (comme
`services/wfs_georisques_service.py` côté France) puis chercher la
référence exacte attendue (`caPaKey`) parmi les résultats, en élargissant
le bbox si besoin — jamais accepter un résultat sans cette vérification
explicite.

Système de coordonnées : EPSG:4258 (~WGS84), ordre (latitude, longitude)
pour le BBOX — même convention que le WFS Géorisques français. Le point
de départ (position d'une adresse du registre) est lui en Lambert 72
(EPSG:31370) : reprojeté via `pyproj` avant toute requête ici."""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional

import pyproj

import config
from models.parcelle import Parcelle
from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.cadastre_service")

_RE_MEMBER = re.compile(
    r"<cp:nationalCadastralReference>([^<]*)</cp:nationalCadastralReference>.*?"
    r"<cp:areaValue[^>]*>([^<]*)</cp:areaValue>",
    re.S,
)
_RE_POSLIST = re.compile(r"<gml:posList>([^<]*)</gml:posList>")

_TRANSFORMER_L72_VERS_4258 = pyproj.Transformer.from_crs("EPSG:31370", "EPSG:4258", always_xy=True)

# Marges (m) d'élargissement progressif du bbox de recherche — la
# parcelle attendue est censée être TRÈS proche du point de départ (une
# adresse liée à elle par le registre), donc un bbox large n'est qu'un
# filet de sécurité, jamais le cas normal.
_MARGES_RECHERCHE_M = (100.0, 300.0, 1000.0)


def lambert72_vers_4258(x: float, y: float) -> tuple[float, float]:
    """Reprojette un point Lambert 72 (EPSG:31370, x/y) vers EPSG:4258
    (lon/lat) — utilisé pour interroger ce service à partir d'une
    position d'adresse du registre flamand (voir `adressen_service.py`)."""
    lon, lat = _TRANSFORMER_L72_VERS_4258.transform(x, y)
    return lon, lat


class CadastreService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(lat: float, lon: float, marge_m: float) -> str:
        dlat = marge_m / 111320
        dlon = marge_m / (111320 * math.cos(math.radians(lat)))
        return f"{lat - dlat},{lon - dlon},{lat + dlat},{lon + dlon},urn:ogc:def:crs:EPSG::4258"

    def get_parcelle(self, reference: str, lat: float, lon: float) -> Optional[Parcelle]:
        """Récupère la géométrie de la parcelle `reference` (caPaKey,
        ex. `"71053H1051/00N000"`), connue pour être proche de
        `(lat, lon)` (EPSG:4258) — jamais interrogée par CQL_FILTER (voir
        le docstring du module). Élargit le bbox progressivement
        (`_MARGES_RECHERCHE_M`) si la référence n'apparaît pas dans les
        premiers résultats ; renvoie `None` (jamais deviné) si elle reste
        introuvable après la marge la plus large."""
        for marge in _MARGES_RECHERCHE_M:
            parcelles = self._chercher_par_bbox(lat, lon, marge)
            for p in parcelles:
                if p.reference == reference:
                    return p
            _logger.debug(
                "Parcelle '%s' non trouvée dans un bbox de %.0fm autour de (%.6f, %.6f) "
                "(%d parcelle(s) vue(s)) — élargissement.", reference, marge, lat, lon, len(parcelles),
            )
        _logger.warning(
            "Parcelle '%s' introuvable près de (%.6f, %.6f) même après élargissement à %.0fm.",
            reference, lat, lon, _MARGES_RECHERCHE_M[-1],
        )
        return None

    def _chercher_par_bbox(self, lat: float, lon: float, marge_m: float) -> List[Parcelle]:
        url = config.CADASTRE_WFS_BASE
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "cp:CadastralParcel", "count": 200,
            "BBOX": self._bbox(lat, lon, marge_m),
        }
        xml = self._http.get_text(url, params, service_key="cadastre_be")
        return self._parser_membres(xml)

    @staticmethod
    def _parser_membres(xml: str) -> List[Parcelle]:
        """Découpe la réponse GML en membres `cp:CadastralParcel` et
        extrait référence/aire/géométrie de chacun. Analyse texte simple
        (pas un vrai parseur XML) — même choix pragmatique que
        `wfs_georisques_service.py` côté France, la structure de ce WFS
        étant stable et connue."""
        parcelles: List[Parcelle] = []
        membres = xml.split("<wfs:member>")[1:]
        for bloc in membres:
            m_ref = re.search(r"<cp:nationalCadastralReference>([^<]*)</cp:nationalCadastralReference>", bloc)
            if not m_ref:
                continue
            reference = m_ref.group(1).strip()
            m_area = re.search(r"<cp:areaValue[^>]*>([^<]*)</cp:areaValue>", bloc)
            area = float(m_area.group(1)) if m_area else None
            geometry = CadastreService._parser_geometrie(bloc)
            parcelles.append(Parcelle(reference=reference, geometry=geometry, area=area))
        return parcelles

    @staticmethod
    def _parser_geometrie(bloc: str) -> Optional[Dict]:
        """Convertit le(s) `gml:posList` (ordre lat,lon, EPSG:4258) en
        géométrie GeoJSON (ordre lon,lat) — `utils/geometrie.py` attend
        cet ordre (voir `centroide_geometrie`/`point_dans_geometrie`).
        Ne gère qu'un seul anneau extérieur par polygone : aucun trou
        constaté sur les parcelles vues en investigation live (même
        hypothèse que côté France)."""
        anneaux = _RE_POSLIST.findall(bloc)
        if not anneaux:
            return None
        polygones = []
        for anneau_texte in anneaux:
            valeurs = [float(v) for v in anneau_texte.split()]
            paires = list(zip(valeurs[0::2], valeurs[1::2]))  # (lat, lon)
            anneau = [[lon, lat] for lat, lon in paires]
            polygones.append([anneau])
        if len(polygones) == 1:
            return {"type": "Polygon", "coordinates": polygones[0]}
        return {"type": "MultiPolygon", "coordinates": polygones}
