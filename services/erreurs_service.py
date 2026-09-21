"""Suivi et nouvel essai des cellules marquées "ERREUR" (échec réseau/API
lors de la résolution d'un point -- voir `resolveur_service.py` pour la
distinction stricte "N" (légitime) vs "ERREUR" (retentable), décision
explicite de l'utilisateur du 2026-09-19) -- même principe que
walon-map-france-cloud (`cellules_a_revisiter.csv` /
`reessayer_cellules_wfs`), adapté à la structure plus simple de ce
pipeline : `ResolveurBE.resoudre()` calcule TOUTES les colonnes d'un
point en un seul appel (contrairement aux ~5 catégories séparées côté
France), donc un SEUL CSV et une SEULE fonction de réessai suffisent --
un nouvel essai relance `resoudre()` une fois par parcelle trackée
(recalcule tout, pas seulement les colonnes en erreur -- redondant mais
trivial et correct, jamais de risque de désynchronisation entre
catégories comme côté France).

`chemin_revisite` est un fichier CSV UNIQUE, PARTAGÉ entre TOUTES les
communes/fichiers Excel jamais traités (`config.CELLULES_A_REVISITER_PATH`)
-- chaque ligne connaît son `excel_path` d'origine, donc `reessayer_
cellules_erreur` peut filtrer exactement les lignes qui le concernent
sans ambiguïté (plus simple que le filtre section/numéro de
walon-map-france-cloud, rendu nécessaire là-bas par l'absence de ce
champ)."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set

from openpyxl.utils import column_index_from_string

from services.excel_service import (
    COL_NUMERO_CADASTRAL, FIRST_DATA_ROW, charger_classeur, feuille_principale, sauvegarder,
    trouver_premiere_ligne_vide,
)
from services.resolveur_service import ResolveurBE
from utils.logger import get_logger

_logger = get_logger("services.erreurs_service")

_CHAMPS = ["date", "excel_path", "commune", "code_postal", "rue", "numero", "capakey", "x", "y", "colonne"]


def tracer_cellules_erreur(
    chemin_revisite: Path, *, excel_path: Path, commune: str, code_postal: str, rue: str,
    numero: str, capakey: str, x: float, y: float, colonnes: Set[str],
) -> None:
    """Ajoute une ligne CSV par colonne en erreur (append-only) -- jamais
    silencieusement, même principe que `_forcer_valeurs_manquantes_en_n`
    côté France."""
    if not colonnes:
        return
    chemin_revisite.parent.mkdir(parents=True, exist_ok=True)
    nouveau_fichier = not chemin_revisite.exists()
    with chemin_revisite.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if nouveau_fichier:
            writer.writerow(_CHAMPS)
        date = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for colonne in sorted(colonnes):
            writer.writerow([date, str(excel_path), commune, code_postal, rue, numero, capakey, x, y, colonne])


def reessayer_cellules_erreur(excel_path: Path, chemin_revisite: Path, resolveur: ResolveurBE) -> int:
    """Relit `chemin_revisite`, ne retente QUE les lignes appartenant à
    `excel_path` (comparaison de chemin exacte). Pour chaque parcelle
    unique encore trackée (regroupée par caPaKey, position `x`/`y`
    reprise TELLE QUELLE du CSV -- capturée au moment du premier échec,
    évite un aller-retour supplémentaire vers le registre d'adresses) :
    relance `resolveur.resoudre(x, y)` UNE fois ; pour chaque colonne
    trackée dont la nouvelle réponse n'est plus "ERREUR", écrit la
    valeur dans l'Excel et retire la ligne du suivi. Renvoie le nombre
    de cellules réparées.

    Excel sauvegardé AVANT le CSV réécrit -- même précaution que côté
    France (`reessayer_cellules_wfs`, incident réel Challex 2026-09-08) :
    un échec de sauvegarde Excel (ex: fichier ouvert dans un tableur) ne
    doit jamais faire perdre le suivi d'une cellule pas réellement
    corrigée."""
    if not chemin_revisite.exists():
        return 0

    excel_path_str = str(excel_path)
    with chemin_revisite.open(newline="", encoding="utf-8") as f:
        toutes_lignes = list(csv.DictReader(f))
    a_retenter = [l for l in toutes_lignes if l["excel_path"] == excel_path_str]
    autres_fichiers = [l for l in toutes_lignes if l["excel_path"] != excel_path_str]
    if not a_retenter:
        return 0

    wb = charger_classeur(excel_path)
    ws = feuille_principale(wb)
    derniere = trouver_premiere_ligne_vide(ws) - 1
    ligne_par_capakey: Dict[str, int] = {}
    for r in range(FIRST_DATA_ROW, derniere + 1):
        v = ws.cell(row=r, column=COL_NUMERO_CADASTRAL).value
        if v not in (None, ""):
            ligne_par_capakey[str(v).strip()] = r

    par_capakey: Dict[str, List[dict]] = {}
    for l in a_retenter:
        par_capakey.setdefault(l["capakey"], []).append(l)

    n_repare = 0
    lignes_restantes: List[dict] = list(autres_fichiers)
    for capakey, groupe in par_capakey.items():
        row = ligne_par_capakey.get(capakey)
        if row is None:
            # Ligne disparue du fichier (jamais censé arriver hors modification manuelle) --
            # gardée trackée plutôt que perdue silencieusement.
            lignes_restantes.extend(groupe)
            continue
        x, y = float(groupe[0]["x"]), float(groupe[0]["y"])
        try:
            valeurs, erreurs_nouvelles = resolveur.resoudre(x, y)
        except Exception as exc:  # noqa: BLE001 -- un nouvel essai ne doit jamais faire planter le run
            _logger.warning("Nouvel essai de la parcelle '%s' a échoué (%s) -- reste trackée.", capakey, exc)
            lignes_restantes.extend(groupe)
            continue
        for l in groupe:
            colonne = l["colonne"]
            if colonne in erreurs_nouvelles:
                lignes_restantes.append(l)
                continue
            valeur = valeurs.get(colonne)
            if valeur is None:
                # Toujours pas de réponse exploitable, mais plus une erreur réseau cette
                # fois (une vraie absence de donnée) -- laisse la cellule "ERREUR" jusqu'à
                # investigation manuelle, jamais deviné "N" à sa place.
                lignes_restantes.append(l)
                continue
            ws.cell(row=row, column=column_index_from_string(colonne), value=valeur)
            n_repare += 1

    if n_repare:
        sauvegarder(wb, excel_path)

    with chemin_revisite.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CHAMPS)
        writer.writeheader()
        writer.writerows(lignes_restantes)

    _logger.info(
        "Nouvel essai des cellules \"ERREUR\" (%s) : %d cellule(s) réparée(s), %d reste(nt) trackée(s) au total.",
        excel_path.name, n_repare, len(lignes_restantes),
    )
    return n_repare


def purger_cellules_fichier(chemin_revisite: Path, excel_path: Path) -> int:
    """Retire du suivi toutes les cellules "ERREUR" trackées pour `excel_path` --
    à appeler quand ce fichier est archivé puis recréé de zéro (option
    `--repartir-de-zero`) : ses anciennes lignes n'existent plus, les retenter
    échouerait à jamais ("ligne disparue"). Renvoie le nombre de lignes retirées."""
    if not chemin_revisite.exists():
        return 0
    with chemin_revisite.open(newline="", encoding="utf-8") as f:
        lignes = list(csv.DictReader(f))
    gardees = [l for l in lignes if l["excel_path"] != str(excel_path)]
    if len(gardees) == len(lignes):
        return 0
    with chemin_revisite.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CHAMPS)
        writer.writeheader()
        writer.writerows(gardees)
    return len(lignes) - len(gardees)
