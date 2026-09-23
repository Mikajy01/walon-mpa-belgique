"""Tracé d'une rue flamande via le Wegenregister (couche
`Wegenregister:Wegsegment`, `geo.api.vlaanderen.be/Wegenregister/wfs`,
EPSG:31370) -- utilisé UNIQUEMENT par la découverte géométrique
(`decouverte_geometrique_service.py`) pour les parcelles qu'AUCUNE adresse
du registre ne pointe (rues rurales/digues sans numéro de maison).

Confirmé en direct (2026-09-21, Sint-Gillis-Waas) : "Rode Moerdijk" existe
comme nom de rue (id 147514) mais n'a AUCUNE adresse, alors que le
Wegenregister a bien 6 segments de route pour elle ; "Rode Moerstraat" (id
77472) n'a qu'UNE adresse mais 3 segments sur ~1,3 km.

**Écart réel confirmé le 2026-09-22 (Knesselare)** : l'id de rue du registre
d'adresses (`/v2/straatnamen`, ex. "Maldegemseweg" -> `72198`) ne correspond
PAS TOUJOURS à celui utilisé par le Wegenregister pour la même route réelle
(`linkerstraatnaamObjectId`/`rechterstraatnaamObjectId` -> `203510` pour cette
même "Maldegemseweg") -- confirmé pour TOUTE la commune (même écart sur
plusieurs rues testées), pas un cas isolé. `segments()` filtre donc D'ABORD
par id (rapide, marche pour la majorité des communes vérifiées jusqu'ici,
ex. Sint-Gillis-Waas), et REPLIE sur un filtre par NOM (champ texte
`linkerstraatnaam`/`rechterstraatnaam`, confirmé fonctionner directement en
direct) si l'id ne renvoie rien. Le nom seul pouvant exister dans PLUSIEURS
communes (risque de collision, ex. "Kerkstraat"), un point de référence
(position d'une adresse connue de la rue visée) limite le repli par nom à
`_RAYON_REPLI_NOM_M` -- écarte une route homonyme ailleurs en Flandre plutôt
que de mélanger deux rues sans rapport.

`segments_autour` renvoie TOUS les segments d'une emprise (toutes rues + les
segments sans nom de rue, ex. chemins agricoles) : sert à ne pas attribuer à
la rue traitée une parcelle qui donne en réalité sur une rue voisine (vérifié
autour de Rode Moerstraat : Klingedijkstraat, Cappaertstraat, Veldstraat...
dans un rayon de 100 m). Chaque segment porte aussi le NOM (pas seulement
l'id) de ses côtés -- l'appelant (`decouverte_geometrique_service.py`)
compare par NOM, plus fiable que l'id vu l'écart ci-dessus."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple
from xml.sax.saxutils import escape

import config
from services.http_client import HttpClient
from utils.logger import get_logger
from utils.text_normalize import normaliser

_logger = get_logger("services.wegenregister_service")

_WFS_BASE = "https://geo.api.vlaanderen.be/Wegenregister/wfs"
_PLAFOND = 1000

# Distance (m) au-delà de laquelle un segment trouvé par repli NOM (voir le
# docstring module) est écarté -- large marge par rapport à l'étendue d'une
# commune flamande (quelques km), suffisant pour écarter une route homonyme
# dans une AUTRE commune sans risquer de couper la vraie rue en deux.
_RAYON_REPLI_NOM_M = 10000.0

_RE_POSLIST = re.compile(r"<gml:posList[^>]*>([^<]*)</gml:posList>")


@dataclass
class Segment:
    object_id: str
    begin: str  # id du noeud de départ (pour chaîner les segments dans l'ordre)
    eind: str
    points: List[Tuple[float, float]]  # Lambert 72 (x, y)
    # ids de rue des côtés gauche/droit ; vide = segment SANS nom de rue
    # (chemin agricole...). Sert à savoir à quelle rue appartient un segment.
    straat_ids: Tuple[str, ...] = ()
    # NOMS (texte brut du Wegenregister) des côtés gauche/droit -- comparaison
    # PAR NOM privilégiée par l'appelant (voir le docstring module, écart
    # d'id confirmé sur Knesselare). Vide = segment sans nom de rue.
    straat_noms: Tuple[str, ...] = ()


def _champ(bloc: str, nom: str) -> Optional[str]:
    m = re.search(rf"<Wegenregister:{nom}>([^<]*)</Wegenregister:{nom}>", bloc)
    return m.group(1) if m else None


class WegenregisterService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def trouver_id_rue(self, gemeentenaam: str, straatnaam: str) -> Optional[str]:
        """Id officiel de la rue dans la commune (correspondance exacte,
        accents/casse ignorés) ou `None` -- jamais deviné."""
        cle = normaliser(straatnaam)
        url: Optional[str] = f"{config.ADRESSENREGISTER_BASE}/straatnamen"
        params: Optional[dict] = {"gemeentenaam": gemeentenaam, "limit": 100}
        while url is not None:
            data = self._http.get_json(url, params, service_key="adressenregister")
            for entree in data.get("straatnamen", []):
                nom = entree["straatnaam"]["geografischeNaam"]["spelling"]
                if normaliser(nom) == cle:
                    return str(entree["identificator"]["objectId"])
            volgende = data.get("volgende")
            url, params = (volgende, None) if volgende else (None, None)
        return None

    def segments(
        self, straatnaam_object_id: Optional[str], straatnaam: str, ref_xy: Optional[Tuple[float, float]] = None,
    ) -> List[Segment]:
        """Segments de route de `straatnaam` : filtre D'ABORD par id (rapide),
        REPLIE sur un filtre par nom si l'id ne renvoie rien ou est `None`
        (voir le docstring module) -- `ref_xy` (Lambert 72, ex. la position
        d'une adresse connue de cette rue) limite alors le repli aux segments
        à `_RAYON_REPLI_NOM_M` de ce point, pour écarter une rue homonyme
        dans une autre commune. Les erreurs réseau remontent telles quelles
        (l'appelant décide, voir `decouvrir_parcelles`)."""
        segments: List[Segment] = []
        if straatnaam_object_id is not None:
            segments = self._requete(self._filtre_id(straatnaam_object_id))
        if not segments:
            if straatnaam_object_id is not None:
                _logger.info(
                    "Wegenregister : id de rue %s (%s) sans segment -- repli sur une recherche par nom "
                    "(écart id registre d'adresses / Wegenregister, voir le docstring du module).",
                    straatnaam_object_id, straatnaam,
                )
            segments = self._requete(self._filtre_nom(straatnaam))
            if ref_xy is not None:
                rx, ry = ref_xy
                avant = len(segments)
                segments = [
                    s for s in segments
                    if any(math.hypot(px - rx, py - ry) <= _RAYON_REPLI_NOM_M for px, py in s.points)
                ]
                if len(segments) != avant:
                    _logger.info(
                        "Wegenregister : repli par nom '%s' -- %d segment(s) écarté(s) à plus de %.0f m "
                        "de la position de référence (probable rue homonyme dans une autre commune).",
                        straatnaam, avant - len(segments), _RAYON_REPLI_NOM_M,
                    )
        return segments

    def segments_autour(self, xmin: float, ymin: float, xmax: float, ymax: float) -> List[Segment]:
        """TOUS les segments (toutes rues + sans nom) de l'emprise Lambert 72.
        Plafonné à `_PLAFOND` : au-delà, un avertissement signale une
        troncature possible (jamais silencieuse)."""
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "Wegenregister:Wegsegment", "count": _PLAFOND,
            "bbox": f"{xmin},{ymin},{xmax},{ymax},urn:ogc:def:crs:EPSG::31370",
        }
        xml = self._http.get_text(_WFS_BASE, params, service_key="wegenregister_be")
        segments = self._parser(xml)
        if len(segments) >= _PLAFOND:
            _logger.warning(
                "Wegenregister : %d segments dans l'emprise (plafond) -- des rues voisines ont pu être TRONQUÉES.",
                len(segments),
            )
        return segments

    @staticmethod
    def _filtre_id(straatnaam_object_id: str) -> str:
        id_ = escape(str(straatnaam_object_id))
        return (
            "<Filter xmlns='http://www.opengis.net/fes/2.0'><Or>"
            f"<PropertyIsEqualTo><ValueReference>linkerstraatnaamObjectId</ValueReference><Literal>{id_}</Literal></PropertyIsEqualTo>"
            f"<PropertyIsEqualTo><ValueReference>rechterstraatnaamObjectId</ValueReference><Literal>{id_}</Literal></PropertyIsEqualTo>"
            "</Or></Filter>"
        )

    @staticmethod
    def _filtre_nom(straatnaam: str) -> str:
        nom = escape(straatnaam)
        return (
            "<Filter xmlns='http://www.opengis.net/fes/2.0'><Or>"
            f"<PropertyIsEqualTo><ValueReference>linkerstraatnaam</ValueReference><Literal>{nom}</Literal></PropertyIsEqualTo>"
            f"<PropertyIsEqualTo><ValueReference>rechterstraatnaam</ValueReference><Literal>{nom}</Literal></PropertyIsEqualTo>"
            "</Or></Filter>"
        )

    def _requete(self, filtre: str) -> List[Segment]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "Wegenregister:Wegsegment", "filter": filtre, "count": 500,
        }
        xml = self._http.get_text(_WFS_BASE, params, service_key="wegenregister_be")
        return self._parser(xml)

    @staticmethod
    def _parser(xml: str) -> List[Segment]:
        segments: List[Segment] = []
        for bloc in xml.split("<wfs:member>")[1:]:
            id_ = _champ(bloc, "objectId")
            beg = _champ(bloc, "beginknoopObjectId")
            end = _champ(bloc, "eindknoopObjectId")
            m_pos = _RE_POSLIST.search(bloc)
            if not (id_ and beg and end and m_pos):
                _logger.warning("Segment de route inexploitable ignoré (champ manquant).")
                continue
            valeurs = [float(v) for v in m_pos.group(1).split()]
            points = list(zip(valeurs[0::2], valeurs[1::2]))
            if len(points) < 2:
                continue
            # "-9" (et autres valeurs négatives) = placeholder "pas de nom de rue" (vérifié en direct :
            # segments sans nom autour de Rode Moerstraat) -- jamais un vrai id de rue.
            bruts = (_champ(bloc, "linkerstraatnaamObjectId"), _champ(bloc, "rechterstraatnaamObjectId"))
            ids = tuple(i for i in bruts if i and i.isdigit() and int(i) > 0)
            noms_bruts = (_champ(bloc, "linkerstraatnaam"), _champ(bloc, "rechterstraatnaam"))
            noms = tuple(n for n in noms_bruts if n)
            segments.append(Segment(id_, beg, end, points, ids, noms))
        return segments
