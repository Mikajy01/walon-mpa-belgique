"""Découverte GÉOMÉTRIQUE des parcelles bordant une rue -- complète la
découverte par adresses (`decouverte_service.py`) pour les parcelles
qu'AUCUNE adresse du registre ne pointe.

Écart réel trouvé en investigation live (Sint-Gillis-Waas, 2026-09-21) :
"Rode Moerdijk" existe comme rue mais n'a AUCUNE adresse (0 parcelle
trouvée) ; "Rode Moerstraat" n'a qu'UNE adresse alors que ~1,3 km de route
sont bordés de dizaines de parcelles (champs). Le registre ne lie une
parcelle qu'à une adresse : sans numéro de maison, la parcelle est
invisible pour la découverte par adresses.

Méthode : tracé de la rue (Wegenregister, `wegenregister_service.py`, ligne
centrale en Lambert 72) -> parcelles cadastrales dans un bbox autour de
points échantillonnés tous les `_PAS_ECHANTILLON_M` mètres -> on garde
celles dont la LIMITE passe à moins de `rayon_m` de la ligne
centrale (parcelle qui borde la route). Chaque parcelle reçoit son côté
(gauche/droite du sens de parcours) et son abscisse le long de la rue, pour
un ordre "un côté d'abord, puis l'autre" (même principe que le tri
pair/impair des rues avec adresses).

Choix documentés :
- `rayon_m` : distance ligne centrale -> LIMITE de la parcelle. Défaut
  `RAYON_RIVERAIN_M` (10 m) = parcelles qui BORDENT la route (la demi-largeur
  d'une route rurale + accotements/fossé fait quelques mètres). Élargissable
  par run (demande du 2026-09-21 : "pas seulement ce qui borde, jusqu'à 50 m"
  pour Rode Moerdijk/Rode Moerstraat, rues rurales dont les champs ne touchent
  pas tous la route) -- le bbox de recherche s'élargit en conséquence.
- **Attribution à la rue la plus proche** (demande du 2026-09-21 : "ne pas prendre
  les parcelles des autres rues") : avec un rayon large, on capterait les
  parcelles qui donnent sur une rue voisine (vérifié autour de Rode Moerstraat :
  Klingedijkstraat, Cappaertstraat, Veldstraat... à moins de 100 m). Chaque
  parcelle candidate est comparée aux segments des AUTRES rues NOMMÉES du
  Wegenregister : si elle est plus proche (de plus de `_TOLERANCE_ATTRIBUTION_M`)
  d'une autre rue que de celle traitée, elle est écartée. Les segments SANS nom
  (chemins agricoles) ne comptent pas comme "autre rue". Un parcelle d'angle
  (à égale distance de deux rues) reste attribuée aux deux : le premier run
  qui l'écrit la garde (dédoublonnage par numéro cadastral dans le fichier).
- Une parcelle que la ligne centrale TRAVERSE (route bâtie sur une parcelle
  cadastrale, chemin agricole privé) n'est pas exclue automatiquement : voir
  le champ `traverse`, l'appelant décide.
- Le cadastre plafonne à 200 entités par requête (`count`) : si un bbox
  atteint 200, un avertissement le signale (troncature possible, jamais
  silencieuse)."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from models.parcelle import Parcelle
from services.cadastre_service import CadastreService, lambert72_vers_4258, vers_lambert72
from services.wegenregister_service import Segment, WegenregisterService
from utils.geometrie import point_dans_geometrie, point_interieur
from utils.logger import get_logger

_logger = get_logger("services.decouverte_geometrique_service")

RAYON_RIVERAIN_M = 10.0
_PAS_ECHANTILLON_M = 50.0
_PAS_DENSIFICATION_M = 5.0
_PLAFOND_CADASTRE = 200
_TOLERANCE_ATTRIBUTION_M = 1.0


@dataclass
class ParcelleLeLong:
    parcelle: Parcelle
    cote: str  # "gauche" / "droite" (sens de parcours du tracé chaîné)
    abscisse: float  # mètres depuis le début du tracé chaîné
    distance: float  # mètres, ligne centrale -> limite la plus proche
    traverse: bool  # la ligne centrale passe DANS la parcelle
    x: float  # point intérieur, Lambert 72
    y: float


def _chainer(segments: List[Segment]) -> List[List[Tuple[float, float]]]:
    """Ordonne les segments en parcours continu (via les ids de noeuds) et
    orient chacun dans le sens du parcours. Départ : un noeud d'extrémité
    (degré 1) s'il existe ; les branches sont parcourues à la suite."""
    par_noeud: Dict[str, List[Segment]] = defaultdict(list)
    for s in segments:
        par_noeud[s.begin].append(s)
        if s.eind != s.begin:
            par_noeud[s.eind].append(s)
    visites = set()
    ordre: List[List[Tuple[float, float]]] = []

    def parcourir(depart: str) -> None:
        pile = [depart]
        while pile:
            n = pile.pop()
            for s in par_noeud[n]:
                if s.object_id in visites:
                    continue
                visites.add(s.object_id)
                sens_direct = s.begin == n
                ordre.append(s.points if sens_direct else list(reversed(s.points)))
                pile.append(s.eind if sens_direct else s.begin)

    extremites = [n for n, l in par_noeud.items() if len(l) == 1]
    for n in extremites or list(par_noeud):
        parcourir(n)
    for s in segments:  # composantes restantes (boucles)
        if s.object_id not in visites:
            parcourir(s.begin)
    return ordre


def _echantillonner(points: List[Tuple[float, float]], pas: float) -> List[Tuple[float, float]]:
    """Points régulièrement espacés (au plus `pas` m) le long d'une polyligne,
    extrémités incluses."""
    res = [points[0]]
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        longueur = math.hypot(bx - ax, by - ay)
        n = max(1, math.ceil(longueur / pas))
        for i in range(1, n + 1):
            res.append((ax + (bx - ax) * i / n, ay + (by - ay) * i / n))
    return res


def _geometrie_lambert72(geometry: Dict) -> Dict:
    """Copie de la géométrie cadastrale (EPSG:4258, [lon, lat]) reprojetée en
    Lambert 72 ([x, y]) -- pour mesurer des distances en mètres."""
    def conv(anneau):
        return [list(vers_lambert72(lon, lat)) for lon, lat in anneau]
    if geometry["type"] == "Polygon":
        return {"type": "Polygon", "coordinates": [conv(a) for a in geometry["coordinates"]]}
    return {"type": "MultiPolygon", "coordinates": [[conv(a) for a in poly] for poly in geometry["coordinates"]]}


def _anneaux_exterieurs(geom_l72: Dict) -> List[List[List[float]]]:
    if geom_l72["type"] == "Polygon":
        return [geom_l72["coordinates"][0]]
    return [poly[0] for poly in geom_l72["coordinates"]]


def _distance_min_aux_aretes(geom_l72: Dict, aretes: List[Tuple[float, float, float, float]]) -> float:
    """Distance minimale (m) entre la LIMITE d'une parcelle (Lambert 72) et un
    ensemble d'arêtes de routes -- même mesure que pour la rue traitée, pour que
    la comparaison "quelle rue est la plus proche" soit cohérente."""
    meilleur = math.inf
    for anneau in _anneaux_exterieurs(geom_l72):
        for px, py in _echantillonner([(c[0], c[1]) for c in anneau], _PAS_DENSIFICATION_M):
            for ax, ay, bx, by in aretes:
                dx, dy = bx - ax, by - ay
                l2 = dx * dx + dy * dy
                t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / l2))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
                if d < meilleur:
                    meilleur = d
    return meilleur


class DecouverteGeometrique:
    def __init__(
        self, wegenregister: WegenregisterService, cadastre: CadastreService, rayon_m: float = RAYON_RIVERAIN_M,
    ) -> None:
        self._weg = wegenregister
        self._cadastre = cadastre
        self._rayon_m = rayon_m
        # Un point du tracé est à au plus PAS/2 de l'échantillon le plus proche :
        # le bbox doit couvrir rayon + PAS/2 pour ne rater aucune parcelle.
        self._marge_bbox_m = rayon_m + _PAS_ECHANTILLON_M / 2

    def parcelles_le_long(self, gemeentenaam: str, straatnaam: str) -> List[ParcelleLeLong]:
        """Parcelles bordant `straatnaam`, ordonnées (côté gauche puis droit,
        abscisse croissante). Liste vide si la rue est introuvable dans le
        registre ou n'a aucun segment. Les erreurs réseau remontent."""
        id_rue = self._weg.trouver_id_rue(gemeentenaam, straatnaam)
        if id_rue is None:
            _logger.warning("Découverte géométrique : rue '%s' (%s) introuvable dans le registre.", straatnaam, gemeentenaam)
            return []
        segments = self._weg.segments(id_rue)
        if not segments:
            _logger.warning("Découverte géométrique : aucun segment de route pour '%s' (id %s).", straatnaam, id_rue)
            return []
        chemin = _chainer(segments)

        # Arêtes de la ligne centrale avec abscisse cumulée (sens du parcours)
        aretes: List[Tuple[float, float, float, float, float]] = []
        cumul = 0.0
        for pts in chemin:
            for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                aretes.append((ax, ay, bx, by, cumul))
                cumul += math.hypot(bx - ax, by - ay)
        longueur_totale = cumul

        # Parcelles candidates : bbox autour de points échantillonnés
        candidates: Dict[str, Parcelle] = {}
        n_requetes = 0
        for pts in chemin:
            for (px, py) in _echantillonner(pts, _PAS_ECHANTILLON_M):
                lon, lat = lambert72_vers_4258(px, py)
                trouvees = self._cadastre.parcelles_autour(lat, lon, self._marge_bbox_m)
                n_requetes += 1
                if len(trouvees) >= _PLAFOND_CADASTRE:
                    _logger.warning(
                        "Découverte géométrique '%s' : un bbox a atteint le plafond de %d parcelles du cadastre "
                        "-- des parcelles ont pu être TRONQUÉES autour de (%.0f, %.0f).",
                        straatnaam, _PLAFOND_CADASTRE, px, py,
                    )
                for p in trouvees:
                    candidates.setdefault(p.reference, p)
        _logger.info(
            "Découverte géométrique '%s' : %d segment(s), %.0f m, %d requête(s) cadastre, %d parcelle(s) candidate(s).",
            straatnaam, len(segments), longueur_totale, n_requetes, len(candidates),
        )

        aretes_autres = self._aretes_autres_rues(id_rue, chemin)
        n_autres_rues = 0

        centre = [pt for pts in chemin for pt in _echantillonner(pts, _PAS_DENSIFICATION_M)]
        resultats: List[ParcelleLeLong] = []
        for p in candidates.values():
            if p.geometry is None:
                continue
            g = _geometrie_lambert72(p.geometry)
            meilleur: Optional[Tuple[float, float, float]] = None  # (distance, abscisse, croisement)
            for anneau in _anneaux_exterieurs(g):
                pts_anneau = [(c[0], c[1]) for c in anneau]
                for pt in _echantillonner(pts_anneau, _PAS_DENSIFICATION_M):
                    for (ax, ay, bx, by, off) in aretes:
                        dx, dy = bx - ax, by - ay
                        l2 = dx * dx + dy * dy
                        t = 0.0 if l2 == 0 else max(0.0, min(1.0, ((pt[0] - ax) * dx + (pt[1] - ay) * dy) / l2))
                        d = math.hypot(pt[0] - (ax + t * dx), pt[1] - (ay + t * dy))
                        if meilleur is None or d < meilleur[0]:
                            meilleur = (d, off + t * math.sqrt(l2), dx * (pt[1] - ay) - dy * (pt[0] - ax))
            if meilleur is None or meilleur[0] > self._rayon_m:
                continue
            if aretes_autres:
                d_autre = _distance_min_aux_aretes(g, aretes_autres)
                if d_autre + _TOLERANCE_ATTRIBUTION_M < meilleur[0]:
                    n_autres_rues += 1
                    continue  # plus proche d'une AUTRE rue nommée : appartient à celle-ci
            traverse = any(point_dans_geometrie(cx, cy, g) for cx, cy in centre)
            x, y = point_interieur(g)
            resultats.append(ParcelleLeLong(
                parcelle=p, cote="gauche" if meilleur[2] >= 0 else "droite",
                abscisse=meilleur[1], distance=meilleur[0], traverse=traverse, x=x, y=y,
            ))
        if n_autres_rues:
            _logger.info(
                "Découverte géométrique '%s' : %d parcelle(s) écartée(s) car plus proches d'une AUTRE rue.",
                straatnaam, n_autres_rues,
            )
        resultats.sort(key=lambda r: (0 if r.cote == "gauche" else 1, r.abscisse))
        return resultats

    def _aretes_autres_rues(
        self, id_rue: str, chemin: List[List[Tuple[float, float]]],
    ) -> List[Tuple[float, float, float, float]]:
        """Arêtes (Lambert 72) des segments des AUTRES rues NOMMÉES autour de la rue
        traitée (emprise du tracé + rayon + marge). Un segment dont l'un des côtés est
        la rue traitée lui appartient ; un segment SANS nom de rue est ignoré."""
        xs = [x for pts in chemin for x, _ in pts]
        ys = [y for pts in chemin for _, y in pts]
        marge = self._rayon_m + _PAS_ECHANTILLON_M
        voisins = self._weg.segments_autour(min(xs) - marge, min(ys) - marge, max(xs) + marge, max(ys) + marge)
        aretes: List[Tuple[float, float, float, float]] = []
        for seg in voisins:
            if not seg.straat_ids or id_rue in seg.straat_ids:
                continue
            for (ax, ay), (bx, by) in zip(seg.points, seg.points[1:]):
                aretes.append((ax, ay, bx, by))
        return aretes
