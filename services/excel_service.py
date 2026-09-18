"""Lecture/écriture du classeur Excel flamand — BEAUCOUP plus simple que
côté France : pas de registre de colonnes dynamique par icône (le
gabarit n'a aucune image ancrée, `models/colonnes_be.py` donne un
mapping FIXE lettre->rôle), donc pas de `scan_layout` à refaire à
chaque fichier.

Structure confirmée en direct sur le gabarit officiel réel ("Formation
Geopunt 1 1.xlsx", reçu le 2026-09-16, DEUX feuilles) :
- Feuille principale (nom arbitraire, ex. "Feuille"/"Mesen" selon le
  fichier -- jamais fixe, toujours prise par POSITION, `wb.worksheets[0]`)
  : ligne 2 = en-têtes identité (A->E), ligne 3 = catégorie (dynamique),
  ligne 4 = palier, ligne 5+ = données.
- Feuille **"RUP"** (nom stable, cherché par nom) : même structure
  d'identité (A->E), mais seulement 6 colonnes utiles (F/G, I/J, L/M --
  lien + texte libre par niveau région/province/commune), à compléter
  MANUELLEMENT par l'utilisateur (voir le plan : le bloc RUP détaillé
  de la feuille principale reçoit "/" partout, jamais de classement
  automatique construit)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, Optional, Set

import openpyxl
from openpyxl.styles import PatternFill
from openpyxl.styles.colors import Color
from openpyxl.utils import column_index_from_string
from openpyxl.workbook.workbook import Workbook
from openpyxl.worksheet.worksheet import Worksheet

from models.colonnes_be import COLONNES_BE
from utils.logger import get_logger

_logger = get_logger("services.excel_service")

# Format court du numéro cadastral affiché sur la carte geopunt.be (ex.
# "233D", "9") -- demande explicite de l'utilisateur du 2026-09-17,
# confirmée par capture d'écran de la carte, PLUS FIABLE que le format
# CaPaKey complet renvoyé par le WFS fédéral (71053H1081/00_000)
# uniquement pour la LECTURE humaine du fichier final -- le format
# complet reste la seule référence fiable en interne (une vraie
# collision existe déjà entre deux communes différentes : "149L" =
# Gippershovenstraat 1 (Sint-Truiden) ET Gorsem-Dorp 89 (rattachée à un
# code cadastral historique différent, 71018), voir la conversation).
_RE_CAPAKEY_COMPLET = re.compile(r"^(\d{5})([A-Z])(\d{4})/(\d{2})([A-Z_])(\d{3})$")


def vers_capakey_court(reference: str) -> str:
    """Convertit une référence cadastrale COMPLÈTE (71053H1081/00_000,
    telle que renvoyée par le WFS fédéral -- voir cadastre_service.py)
    vers le format court affiché sur geopunt.be (1081, 1049B, 59B...).
    Renvoie `reference` INCHANGÉE si le motif attendu n'est pas
    reconnu -- jamais d'exception qui ferait échouer tout le traitement
    de la parcelle pour un simple souci de présentation."""
    m = _RE_CAPAKEY_COMPLET.match(reference)
    if not m:
        _logger.warning("Référence cadastrale '%s' : motif inattendu, conversion au format court ignorée.", reference)
        return reference
    _nis, _section, parcel, _bisnum, bisletter, power = m.groups()
    court = str(int(parcel))
    if bisletter != "_":
        court += bisletter
    if power != "000":
        court += str(int(power))
    return court

HEADER_ROW = 2
FIRST_DATA_ROW = 5

COL_COMMUNE = 1  # A
COL_CODE_POSTAL = 2  # B
COL_RUE = 3  # C
COL_NUMERO = 4  # D
COL_NUMERO_CADASTRAL = 5  # E

NOM_FEUILLE_RUP = "RUP"


def charger_classeur(excel_path: Path) -> Workbook:
    return openpyxl.load_workbook(excel_path)


def feuille_principale(wb: Workbook) -> Worksheet:
    """Toujours la PREMIÈRE feuille par position -- son nom varie d'un
    fichier à l'autre (vu "Mesen" puis "Feuille" sur deux exemplaires du
    même gabarit), jamais fiable à chercher par nom."""
    return wb.worksheets[0]


def feuille_rup(wb: Workbook) -> Optional[Worksheet]:
    """La feuille "RUP" (nom stable) si le classeur en a une -- `None`
    sur un ancien gabarit à une seule feuille, jamais deviné."""
    return wb[NOM_FEUILLE_RUP] if NOM_FEUILLE_RUP in wb.sheetnames else None


def charger_feuille(excel_path: Path) -> Worksheet:
    """Compatibilité : renvoie directement la feuille principale."""
    return feuille_principale(charger_classeur(excel_path))


def trouver_premiere_ligne_vide(ws: Worksheet) -> int:
    """Première ligne (>= FIRST_DATA_ROW) où la colonne A (Commune) est
    vide — même convention que côté France."""
    r = FIRST_DATA_ROW
    while ws.cell(row=r, column=COL_COMMUNE).value not in (None, ""):
        r += 1
    return r


def lire_capakeys_deja_ecrits(ws: Worksheet) -> Set[str]:
    """Référence cadastrale (colonne E) de chaque ligne déjà écrite —
    utilisé pour ne jamais réécrire une parcelle déjà traitée (motif
    "continuer" de reprise, même principe que côté France)."""
    capakeys: Set[str] = set()
    derniere = trouver_premiere_ligne_vide(ws) - 1
    for r in range(FIRST_DATA_ROW, derniere + 1):
        v = ws.cell(row=r, column=COL_NUMERO_CADASTRAL).value
        if v not in (None, ""):
            capakeys.add(str(v).strip())
    return capakeys


def lire_capakeys_vers_lignes(ws: Worksheet) -> Dict[str, int]:
    """Comme `lire_capakeys_deja_ecrits`, mais renvoie le NUMÉRO DE
    LIGNE de chaque référence -- nécessaire pour la réconciliation des
    colonnes RUP dynamiques (main.py), qui doit retrouver la ligne
    d'une parcelle déjà écrite (pas seulement savoir qu'elle existe)."""
    lignes: Dict[str, int] = {}
    derniere = trouver_premiere_ligne_vide(ws) - 1
    for r in range(FIRST_DATA_ROW, derniere + 1):
        v = ws.cell(row=r, column=COL_NUMERO_CADASTRAL).value
        if v not in (None, ""):
            lignes[str(v).strip()] = r
    return lignes


def ecrire_identite(ws: Worksheet, row: int, *, commune: str, code_postal: str, rue: str, numero: str, capakey: str) -> None:
    ws.cell(row=row, column=COL_COMMUNE, value=commune)
    ws.cell(row=row, column=COL_CODE_POSTAL, value=code_postal)
    ws.cell(row=row, column=COL_RUE, value=rue)
    ws.cell(row=row, column=COL_NUMERO, value=numero)
    ws.cell(row=row, column=COL_NUMERO_CADASTRAL, value=capakey)


# Colonnes (lien, texte) par niveau sur la feuille "RUP" — voir
# wfs_rup_service.py, confirmé en direct sur la structure réelle de
# cette feuille (F/G région, I/J province, L/M commune).
_RUP_COLONNES = {"region": (6, 7), "province": (9, 10), "commune": (12, 13)}


def ecrire_rup(ws_rup: Worksheet, row: int, niveau: str, *, lien: str, texte: str) -> None:
    """Écrit le lien + texte libre RUP pour `niveau`
    ("region"/"province"/"commune") à la ligne `row` de la feuille RUP —
    jamais de classement, juste ce que l'API a réellement renvoyé pour
    ce point (voir `services/wfs_rup_service.py::InfoRup`)."""
    col_lien, col_texte = _RUP_COLONNES[niveau]
    ws_rup.cell(row=row, column=col_lien, value=lien)
    ws_rup.cell(row=row, column=col_texte, value=texte)


def ecrire_ligne(
    ws: Worksheet, row: int, *, commune: str, code_postal: str, rue: str,
    numero: str, capakey: str, valeurs: Dict[str, str],
) -> None:
    """Écrit l'identité (A->E) puis les valeurs dynamiques (`valeurs` :
    lettre de colonne Excel -> "O"/"N"/"/"/texte) à la ligne `row` de la
    feuille PRINCIPALE. Ne valide PAS que chaque lettre de `valeurs`
    existe dans `COLONNES_BE` (les appelants passent déjà des lettres
    résolues via les services WFS) -- journalise un avertissement
    plutôt que planter si une lettre inattendue apparaît, jamais deviné
    où l'écrire."""
    ecrire_identite(ws, row, commune=commune, code_postal=code_postal, rue=rue, numero=numero, capakey=capakey)

    for lettre, valeur in valeurs.items():
        if lettre not in COLONNES_BE:
            _logger.warning(
                "Colonne '%s' absente de COLONNES_BE (ligne %d, parcelle %s) -- valeur '%s' NON écrite.",
                lettre, row, capakey, valeur,
            )
            continue
        idx = column_index_from_string(lettre)
        ws.cell(row=row, column=idx, value=valeur)


# ---------------------------------------------------------------------------
# Colonnes RUP DYNAMIQUES (une par type de zone rencontré, ex. "Zone voor
# lokaal bedrijventerrein") -- demande du 2026-09-17, voir
# services/wfs_rup_service.py pour la justification complète : `svnaam`
# sert LITTÉRALEMENT de nom de colonne (aucune correspondance floue),
# créée à la volée dès qu'un point tombe dans une zone jamais vue.
#
# Décision du 2026-09-17 : la colonne est nommée par `svnaam` SEUL, SANS
# préfixe de niveau (région/province/commune) -- choix délibéré de
# l'utilisateur pour matcher le processus manuel habituel, qui ne
# distingue pas non plus par niveau. Risque accepté en connaissance de
# cause : si le MÊME nom de zone existe à deux niveaux différents (rare
# mais réellement possible), les deux se retrouvent fusionnés dans une
# seule colonne. `noms_zones_par_niveau` (voir main.py) détecte ce cas
# EXACT (un même `svnaam` matché par plus d'un niveau au cours d'un même
# run) pour le signaler en fin de run -- jamais silencieux.
#
# Sûr UNIQUEMENT parce que la reconciliation (voir main.py) traite un lot
# ENTIER de rues en une fois : toutes les colonnes nécessaires sont créées
# AVANT que "O"/"N" ne soit écrit pour la moindre ligne de ce lot -- donc
# "N" dans une colonne dynamique signifie toujours "requête faite, cette
# zone ne s'applique pas ici", jamais une case simplement pas encore vue.
# Limite CONNUE et acceptée : une colonne découverte dans un run NE
# rebackfille PAS les lignes de rues traitées lors d'un run PRÉCÉDENT --
# un script de reconciliation complet séparé serait nécessaire pour ça.
# ---------------------------------------------------------------------------

DYNAMIC_RUP_FIRST_COL = 15  # O -- au-delà des 13 colonnes fixes (N=14 jamais utilisée par le gabarit officiel)

# Demande du 2026-09-17 : l'en-tête d'une colonne dynamique va en ligne
# 3 (comme "Lien d'execution commune"/"RUP" pour les colonnes fixes),
# PAS en ligne 2 (réservée au libellé du THÈME, ex. "Plans d'exécution
# spatiaux (Commune)..." -- une seule fois par bloc, pas par colonne
# individuelle). Ligne 4 reprend le même "/" à fond gris que les
# colonnes fixes (voir _FOND_GRIS_SLASH, couleur de thème EXACTE copiée
# du gabarit officiel -- `theme=1, tint=0.249977111117893`, PAS une
# couleur RGB approximative).
DYNAMIC_RUP_HEADER_ROW = 3
DYNAMIC_RUP_SLASH_ROW = 4
_FOND_GRIS_SLASH = PatternFill(
    patternType="solid",
    fgColor=Color(theme=1, tint=0.249977111117893, type="theme"),
    bgColor=Color(indexed=64),
)


def index_colonnes_dynamiques_rup(ws_rup: Worksheet) -> Dict[str, int]:
    """État actuel des colonnes dynamiques RUP déjà créées (clé
    `svnaam` -> index de colonne) -- à relire après toute création pour
    rester synchronisé."""
    index: Dict[str, int] = {}
    for col in range(DYNAMIC_RUP_FIRST_COL, ws_rup.max_column + 1):
        v = ws_rup.cell(row=DYNAMIC_RUP_HEADER_ROW, column=col).value
        if v:
            index[str(v)] = col
    return index


def trouver_ou_creer_colonne_dynamique_rup(
    ws_rup: Worksheet, index: Dict[str, int], svnaam: str, legende: str,
) -> int:
    """Renvoie l'index de colonne pour `svnaam`, la créant (en fin de
    feuille, avec l'en-tête en ligne 3 + couleur de fond best-effort
    tirée de `legende`, et le "/" à fond gris en ligne 4 comme les
    colonnes fixes) si elle n'existe pas encore. Met `index` à jour en
    place."""
    if svnaam in index:
        return index[svnaam]
    nouvelle_col = max([DYNAMIC_RUP_FIRST_COL - 1, *index.values()]) + 1
    cell = ws_rup.cell(row=DYNAMIC_RUP_HEADER_ROW, column=nouvelle_col, value=svnaam)
    couleur = couleur_depuis_legende(legende)
    if couleur:
        cell.fill = PatternFill(start_color=couleur, end_color=couleur, fill_type="solid")
    slash_cell = ws_rup.cell(row=DYNAMIC_RUP_SLASH_ROW, column=nouvelle_col, value="/")
    slash_cell.fill = _FOND_GRIS_SLASH
    index[svnaam] = nouvelle_col
    return nouvelle_col


def ecrire_zones_dynamiques_rup(
    ws_rup: Worksheet, row: int, index: Dict[str, int], colonnes_gagnantes: Set[int],
) -> None:
    """Marque "O"/"N" pour TOUTES les colonnes dynamiques déjà connues, à
    cette ligne -- sûr uniquement si `index` contient déjà toutes les
    colonnes nécessaires pour le LOT traité (voir le docstring de
    section ci-dessus)."""
    for col in index.values():
        ws_rup.cell(row=row, column=col, value=("O" if col in colonnes_gagnantes else "N"))


# -- Couleur best-effort à partir du champ `legende` (texte libre WFS) ------
#
# Variété RÉELLE observée en direct sur ~320 valeurs distinctes (voir la
# conversation du 2026-09-17) : couleurs simples ("rood", "licht groen"),
# codes hex ("#4c4c00"), triplets RGB ("RGB 165/210/241"), mais aussi des
# dizaines de motifs composés ("geel met zwarte arcering", "pyjama RGB.../
# RGB..."), et des codes internes SANS lien évident avec une couleur
# ("VLAK60920"). Volontairement PRUDENT : ne devine JAMAIS sur un motif
# composé ou non reconnu -- pas de couleur plutôt qu'une couleur fausse.

_COULEURS_DE_BASE: Dict[str, str] = {
    "rood": "FF0000", "groen": "00A651", "geel": "FFFF00", "blauw": "0070C0",
    "blauwgroen": "00A693", "geelgroen": "9ACD32",
    "paars": "7030A0", "auberginepaars": "5B2C6F",
    "bruin": "8B4513", "okerbruin": "A67B2A", "oranjebruin": "B5651D",
    "grijs": "808084", "grijsblauw": "6E7F8C",
    "wit": "FFFFFF", "oranje": "FFA500", "roze": "FFC0CB", "roos": "FFC0CB",
    "magenta": "FF00FF", "zwart": "000000",
    "kaki": "8B8B5A", "kakigroen": "8B8B5A", "legergroen": "4B5320",
    "okergeel": "CBA135",
}
_RE_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_RE_RGB = re.compile(r"rgb\s*(\d+)\s*/\s*(\d+)\s*/\s*(\d+)")
_MOTS_AMBIGUS = (
    "met", " en ", "arcering", "rand", "stippen", "gearceerd", "lijn",
    "kruisjes", "bollen", "raster", "pyjama", "ster",
)


def _eclaircir(hex6: str, ratio: float) -> str:
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    r, g, b = (int(c + (255 - c) * ratio) for c in (r, g, b))
    return f"{r:02X}{g:02X}{b:02X}"


def _foncer(hex6: str, ratio: float) -> str:
    r, g, b = int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16)
    r, g, b = (int(c * (1 - ratio)) for c in (r, g, b))
    return f"{r:02X}{g:02X}{b:02X}"


def couleur_depuis_legende(legende: str) -> Optional[str]:
    """Best-effort : hex 6 caractères à partir du texte libre `legende`
    du WFS RUP (voir services/wfs_rup_service.py), ou `None` si non
    reconnu avec confiance (voir le docstring de section ci-dessus)."""
    if not legende:
        return None
    texte = legende.strip().lower()
    m = _RE_HEX.match(texte)
    if m:
        return m.group(1).upper()
    # Motifs ambigus (dont "pyjama", deux couleurs en rayures) AVANT le
    # test RGB -- sinon "pyjama RGB x/y/z & RGB a/b/c" matcherait le
    # PREMIER triplet seul, une couleur non représentative du motif réel.
    if any(mot in texte for mot in _MOTS_AMBIGUS):
        return None
    m = _RE_RGB.search(texte)
    if m:
        r, g, b = (int(v) for v in m.groups())
        if all(0 <= v <= 255 for v in (r, g, b)):
            return f"{r:02X}{g:02X}{b:02X}"
    intensite = None
    if texte.startswith("licht"):
        intensite, texte = "licht", texte[len("licht"):].strip()
    elif texte.startswith("donker"):
        intensite, texte = "donker", texte[len("donker"):].strip()
    elif texte.startswith("fel"):
        texte = texte[len("fel"):].strip()
    base = _COULEURS_DE_BASE.get(texte.replace(" ", ""))
    if base is None:
        return None
    if intensite == "licht":
        return _eclaircir(base, 0.5)
    if intensite == "donker":
        return _foncer(base, 0.4)
    return base


def sauvegarder(wb: Workbook, excel_path: Path) -> None:
    wb.save(excel_path)
