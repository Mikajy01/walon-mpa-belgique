"""Normalisation de texte pour comparer un libellé renvoyé par une API
(ex. `svnaam` du WFS Gewestplan) au libellé du gabarit Excel — les deux
diffèrent parfois par la casse, les accents, ou une faute de frappe du
gabarit (ex. "culturelke" au lieu de "cultureel", confirmé en direct sur
la colonne J) : minuscules + accents retirés + ponctuation/espaces
réduits à un espace simple, jamais de correspondance floue au-delà de
ça (pas de tolérance aux fautes, seulement à la forme)."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher
from typing import Dict, Optional


def normaliser(texte: str) -> str:
    if texte is None:
        return ""
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    texte = texte.lower().strip()
    texte = re.sub(r"[^a-z0-9]+", " ", texte)
    return re.sub(r"\s+", " ", texte).strip()


# Seuil calibré en direct (2026-09-16) sur le WFS Gewestplan réel : la
# vraie catégorie attendue ("woongebieden met cultureel...", divergence
# connue du gabarit qui écrit "culturelke" au lieu de "culturele/
# cultureel") obtient 0.976, la 2e meilleure correspondance dans le même
# groupe seulement 0.59 — marge large, 0.85 reste un seuil sûr sans
# confondre deux catégories réellement différentes.
SEUIL_CORRESPONDANCE_FLOUE = 0.85


def meilleure_correspondance(valeur: str, candidats: Dict[str, str]) -> Optional[str]:
    """Trouve la clé de `candidats` (dict clé->libellé) dont le libellé
    normalisé ressemble le plus à `valeur` (normalisée), au-dessus de
    `SEUIL_CORRESPONDANCE_FLOUE` — `None` si aucun candidat n'atteint ce
    seuil (jamais deviné en dessous)."""
    cible = normaliser(valeur)
    meilleur_score = 0.0
    meilleure_cle: Optional[str] = None
    for cle, libelle in candidats.items():
        score = SequenceMatcher(None, cible, normaliser(libelle)).ratio()
        if score > meilleur_score:
            meilleur_score = score
            meilleure_cle = cle
    if meilleur_score >= SEUIL_CORRESPONDANCE_FLOUE:
        return meilleure_cle
    return None
