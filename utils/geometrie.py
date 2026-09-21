"""Petits calculs géométriques réutilisés par plusieurs services
(centroïde d'une géométrie GeoJSON cadastrale) — porté de la logique
ad-hoc validée en investigation live sur des géométries réelles
(Polygon/MultiPolygon à un seul anneau extérieur, jamais de trou
constaté sur une parcelle cadastrale française)."""

from __future__ import annotations

from typing import Dict, Tuple


def centroide_geometrie(geometry: Dict) -> Tuple[float, float]:
    """Centroïde approximatif (moyenne des sommets de l'anneau
    extérieur) — suffisant pour positionner une parcelle par rapport à
    une rue (pas un vrai centroïde pondéré par aire, inutile ici vu la
    taille des parcelles concernées).

    ATTENTION : pour une parcelle allongée/en lanière (fréquent en zone
    rurale, ex Argis 01017), ce centroïde peut être TRÈS éloigné d'un
    point réellement situé À L'INTÉRIEUR de la parcelle mais proche
    d'une extrémité — écart réel trouvé en investigation live (adresse
    "41 Chemin de la Morandière" : à 16m du centroïde de la parcelle
    VOISINE 0071, mais géométriquement DANS la parcelle 0070, dont le
    centroïde est à 26m). Ne jamais départager des candidates par seule
    distance à ce centroïde sans avoir d'abord tenté `point_dans_geometrie`
    (voir `main.py::decouvrir_parcelles`)."""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        anneau = geometry["coordinates"][0]
    elif gtype == "MultiPolygon":
        anneau = geometry["coordinates"][0][0]
    elif gtype == "Point":
        # Un lieu-dit NON habité (BDTOPO `lieu_dit_non_habite`, voir
        # VoirieService.get_lieu_dit) est un Point brut, pas une zone —
        # écart réel trouvé en investigation live (Ambléon, 01006,
        # "Corbanay") : contrairement aux lieux-dits habités
        # (`zone_d_habitation`, toujours Polygon/MultiPolygon), ce
        # deuxième calque BDTOPO n'a pas de contour, juste ses propres
        # coordonnées comme "centroïde".
        x, y = geometry["coordinates"]
        return x, y
    else:
        raise ValueError(f"Type de géométrie non supporté : {gtype!r}")
    lons = [c[0] for c in anneau]
    lats = [c[1] for c in anneau]
    return sum(lons) / len(lons), sum(lats) / len(lats)


def _point_dans_anneau(x: float, y: float, anneau: list) -> bool:
    """Ray casting standard sur un anneau de polygone (liste de [lon, lat])."""
    dedans = False
    n = len(anneau)
    x1, y1 = anneau[0]
    for i in range(1, n + 1):
        x2, y2 = anneau[i % n]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / (y2 - y1) + x1):
            dedans = not dedans
        x1, y1 = x2, y2
    return dedans


def point_dans_geometrie(lon: float, lat: float, geometry: Dict) -> bool:
    """True si `(lon, lat)` tombe à l'intérieur de `geometry` (Polygon ou
    MultiPolygon GeoJSON, un seul anneau extérieur — jamais de trou
    constaté sur une géométrie cadastrale française). Test géométrique
    EXACT, à préférer à toute heuristique par distance au centroïde
    (voir `centroide_geometrie`) quand la question est "ce point est-il
    dans CETTE parcelle", pas "quelle parcelle est la plus proche"."""
    gtype = geometry.get("type")
    if gtype == "Polygon":
        polygones = [geometry["coordinates"]]
    elif gtype == "MultiPolygon":
        polygones = geometry["coordinates"]
    else:
        return False
    return any(_point_dans_anneau(lon, lat, poly[0]) for poly in polygones)


def point_interieur(geometry: Dict) -> Tuple[float, float]:
    """Un point GARANTI (autant que possible) à l'intérieur de `geometry`.
    Le centroïde moyen des sommets (`centroide_geometrie`) peut tomber HORS
    d'une parcelle allongée ou concave (champ en lanière, parcelle en L) --
    or les couches WFS thématiques sont interrogées en ce point, un point
    hors de la parcelle donnerait la réponse du voisin. Essaie le centroïde,
    puis une grille 15x15 sur l'emprise (le point intérieur le plus proche du
    centroïde) ; retombe sur le centroïde si rien ne convient (jamais
    d'exception : cas dégénéré)."""
    cx, cy = centroide_geometrie(geometry)
    if point_dans_geometrie(cx, cy, geometry):
        return cx, cy
    gtype = geometry.get("type")
    anneau = geometry["coordinates"][0] if gtype == "Polygon" else geometry["coordinates"][0][0]
    xs = [c[0] for c in anneau]
    ys = [c[1] for c in anneau]
    n = 15
    meilleur = None
    for i in range(1, n):
        for j in range(1, n):
            x = min(xs) + (max(xs) - min(xs)) * i / n
            y = min(ys) + (max(ys) - min(ys)) * j / n
            if point_dans_geometrie(x, y, geometry):
                d = (x - cx) ** 2 + (y - cy) ** 2
                if meilleur is None or d < meilleur[0]:
                    meilleur = (d, x, y)
    return (meilleur[1], meilleur[2]) if meilleur else (cx, cy)
