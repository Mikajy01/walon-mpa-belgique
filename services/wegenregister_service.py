"""Tracé d'une rue flamande via le Wegenregister (couche
`Wegenregister:Wegsegment`, `geo.api.vlaanderen.be/Wegenregister/wfs`,
EPSG:31370) -- utilisé UNIQUEMENT par la découverte géométrique
(`decouverte_geometrique_service.py`) pour les parcelles qu'AUCUNE adresse
du registre ne pointe (rues rurales/digues sans numéro de maison).

Confirmé en direct (2026-09-21, Sint-Gillis-Waas) : "Rode Moerdijk" existe
comme nom de rue (id 147514) mais n'a AUCUNE adresse, alors que le
Wegenregister a bien 6 segments de route pour elle ; "Rode Moerstraat" (id
77472) n'a qu'UNE adresse mais 3 segments sur ~1,3 km.

Le filtre se fait sur `linkerstraatnaamObjectId`/`rechterstraatnaamObjectId`
(PAS sur le nom) : le même nom de rue existe dans plusieurs communes, l'id de
`/v2/straatnamen?gemeentenaam=...` lève l'ambiguïté (vérifié : ce filtre par
id fonctionne, contrairement au paramètre `straatnaamId` de `/v2/adressen`
qui est ignoré silencieusement par l'API).

`segments_autour` renvoie TOUS les segments d'une emprise (toutes rues + les
segments sans nom de rue, ex. chemins agricoles) : sert à ne pas attribuer à
la rue traitée une parcelle qui donne en réalité sur une rue voisine (vérifié
autour de Rode Moerstraat : Klingedijkstraat, Cappaertstraat, Veldstraat...
dans un rayon de 100 m)."""

from __future__ import annotations

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

    def segments(self, straatnaam_object_id: str) -> List[Segment]:
        """Tous les segments de route dont la rue (côté gauche OU droit) est
        `straatnaam_object_id`. Les erreurs réseau remontent telles quelles
        (l'appelant décide, voir `decouvrir_parcelles`)."""
        id_ = escape(str(straatnaam_object_id))
        filtre = (
            "<Filter xmlns='http://www.opengis.net/fes/2.0'><Or>"
            f"<PropertyIsEqualTo><ValueReference>linkerstraatnaamObjectId</ValueReference><Literal>{id_}</Literal></PropertyIsEqualTo>"
            f"<PropertyIsEqualTo><ValueReference>rechterstraatnaamObjectId</ValueReference><Literal>{id_}</Literal></PropertyIsEqualTo>"
            "</Or></Filter>"
        )
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": "Wegenregister:Wegsegment", "filter": filtre, "count": 500,
        }
        xml = self._http.get_text(_WFS_BASE, params, service_key="wegenregister_be")
        return self._parser(xml)

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
            segments.append(Segment(id_, beg, end, points, ids))
        return segments
