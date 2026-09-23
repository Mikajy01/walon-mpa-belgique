"""Orchestration : résout TOUTES les colonnes confirmées (voir le plan --
177/184 avec le bloc RUP "/" inclus, DY/FA/FD ajoutés le 2026-09-16 ;
seules DE/DV/EB/FE restent sans source confirmée, voir le docstring de
`wfs_watertoets_service.py`/`wfs_natuur_service.py`) pour un point donné
(position d'adresse, Lambert 72) en appelant chaque service thématique
déjà construit et vérifié.

Principe de sortie : `resoudre()` renvoie `(valeurs, erreurs)` --
`valeurs` : `Dict[str, str]` lettre de colonne Excel -> valeur
("O"/"N"/"ERREUR"/texte) ; `erreurs` : `Set[str]` des lettres dont la
valeur dans `valeurs` est "ERREUR" (sous-ensemble de `valeurs.keys()`,
jamais recalculé ailleurs -- pratique pour `main.py`/`erreurs_service.py`
qui doivent tracer CES colonnes précises sans reparcourir tout `valeurs`).

**"N" vs "ERREUR" -- distinction stricte, décision explicite de
l'utilisateur (2026-09-19)** : une réponse WFS/API valide qui ne trouve
simplement aucune correspondance continue de dégrader en "N" (légitime,
voir plus bas). Une VRAIE panne réseau/API (timeout, 5xx, connexion
refusée -- voir `services/exceptions.py` + les `except
(requests.exceptions.RequestException, ApiServiceError): raise` ajoutés
le 2026-09-19 dans CHAQUE service `wfs_*.py`, qui laissent maintenant ces
erreurs remonter jusqu'ici au lieu de les avaler en `None`) écrit
"ERREUR" à la place, JAMAIS "N" -- ancien comportement (jusqu'au
2026-09-19) : une panne réseau ponctuelle produisait un "N" indiscernable
d'une vraie réponse négative, et restait fausse de façon PERMANENTE une
fois la ligne écrite (`lire_capakeys_deja_ecrits` fait sauter toute ligne
déjà présente lors d'un run normal -- jamais retraitée). "ERREUR" est
tracée par l'appelant (voir `services/erreurs_service.py`,
`config.CELLULES_A_REVISITER_PATH`) et retentée automatiquement au
prochain run (`reessayer_cellules_erreur`, appelé en tout début de
`main.py::executer_traitement`) -- même principe que
walon-map-france-cloud (`cellules_a_revisiter.csv` /
`reessayer_cellules_wfs`), adapté à la structure plus simple de ce
pipeline (un seul appel `resoudre()` recalcule TOUTES les colonnes d'un
point, pas ~5 catégories séparées comme côté France).

Catch volontairement RESTREINT (voir `_get`) à
`(requests.exceptions.RequestException, ApiServiceError)` -- jamais une
exception de programmation (ex `TypeError`, `KeyError` d'un vrai bug),
qui doit continuer à faire planter le run : un bug reste un bug, jamais
masqué en "ERREUR" récupérable.

**Groupes "un seul parmi plusieurs" (Gewestplan, paliers watertoets,
bruit, pollution des sols OVAM)** : décision du 2026-09-17 (demande
explicite de l'utilisateur) -- `_un_parmi()` écrit "N" dans TOUTES les
colonnes sœurs dès que le service a répondu SANS exception, pas
seulement "O" dans la gagnante. Ce n'est sûr QUE parce que chaque service
concerné garantit une réponse EXHAUSTIVE une fois qu'il a répondu :
- Gewestplan (`lu:lu_gwp_gv`) et les paliers watertoets pluvial/fluvial/
  zee ont une légende OFFICIELLE confirmée en direct qui couvre TOUT le
  territoire (y compris un palier explicite "A -- Geen ... gemodelleerd"
  pour l'absence de risque) -- l'absence de correspondance ne signifie
  jamais "pas de donnée", toujours "aucun des paliers listés".
- OVAM a un palier de repli explicite "GE -- Geen bodeminfo" DANS le
  service lui-même (`wfs_ovam_service.py`) pour l'absence de feature --
  jamais une simple absence de réponse.
- Bruit (`hh_weg_lnight`/`hh_lucht_lnight`/`hh_spoor_lden`) n'a PAS de
  palier "en dessous du seuil" officiel distinct de "hors zone
  modélisée" dans les données sources (voir `wfs_bruit_service.py`) --
  extension DÉLIBÉRÉE du même principe qu'utilisent déjà ~100 autres
  colonnes de ce pipeline (`None` = "N" pour une réponse SANS exception)
  -- cohérent avec l'existant, pas un risque nouveau. Une exception sur
  CE groupe marque maintenant TOUTES ses colonnes sœurs "ERREUR" (voir
  `_un_parmi_resilient`), jamais "N".

Gewestplan reste le seul groupe où le remplissage "N" est conditionné à
une réponse BRUTE non vide du service (`svnaam_categories`) avant de
marquer "N" partout -- 26 colonnes d'un coup sur une fausse déduction
aurait un impact bien plus large qu'une colonne seule, marge de
prudence supplémentaire justifiée par la taille du groupe."""

from __future__ import annotations

from typing import Callable, Dict, Optional, Sequence, Set, Tuple

import requests

from services.exceptions import ApiServiceError
from services.wfs_advieskaart_service import WfsAdvieskaartService
from services.wfs_afstromingskaart_service import WfsAfstromingskaartService
from services.wfs_gewestplan_service import WfsGewestplanService
from services.wfs_bruit_service import WfsBruitService
from services.wfs_watertoets_service import WfsWatertoetsService
from services.wfs_economie_service import WfsEconomieService
from services.wfs_landinrichting_woningbouw_service import (
    WfsLandinrichtingService, WfsWoningbouwService, WfsNatuurinrichtingService, WfsRuilverkavelingService,
)
from services.wfs_landschap_service import WfsLandschapService
from services.wfs_natuur_service import WfsNatuurService
from services.wfs_bodem_service import WfsBodemService
from services.wfs_grondverschuiving_service import WfsGrondverschuivingService
from services.wfs_grondwaterwinning_service import WfsGrondwaterwinningService
from services.wfs_ovam_service import WfsOvamService
from services.wfs_seveso_service import WfsSevesoService
from services.wfs_steunzone_brownfield_service import WfsSteunzoneBrownfieldService
from models.colonnes_be import COLONNES_BE
from utils.text_normalize import meilleure_correspondance, normaliser
from utils.logger import get_logger

_logger = get_logger("services.resolveur_service")

# Alias CONNUS pour des `svnaam` réels dont le score flou tombe juste
# sous SEUIL_CORRESPONDANCE_FLOUE (0.85) malgré une correspondance
# sémantique sans ambiguïté -- confirmé en direct le 2026-09-17 :
# "landschappelijk waardevolle agrarische gebieden" (zones agricoles à
# valeur paysagère, une sous-catégorie réelle du Gewestplan) ne score
# que 0.843 contre la colonne Z ("Landschappelijke waarevolle gebieden"
# -- le gabarit a lui-même une faute de frappe, "waarevolle" au lieu de
# "waardevolle", ce qui abaisse encore le score), la 2e meilleure
# correspondance restant loin derrière (0.576). Liste courte et
# EXPLICITE plutôt que d'abaisser le seuil global (qui risquerait de
# confondre deux catégories réellement différentes ailleurs) -- clé
# normalisée (voir `normaliser`), vérifiée AVANT le calcul flou.
ALIAS_GEWESTPLAN_CONNUS: Dict[str, str] = {
    normaliser("landschappelijk waardevolle agrarische gebieden"): "Z",
}

# Sentinel interne (jamais exposé hors de ce module) -- distingue "fn()
# a levé une erreur réseau/API" de "fn() a légitimement renvoyé None"
# (aucune correspondance, réponse SANS exception). `is` uniquement,
# jamais `==` (voir `_get`).
_ERREUR = object()


def _un_parmi(valeurs: Dict[str, str], colonnes: Sequence[str], gagnants: Optional[Set[str]] = None) -> None:
    """Marque chaque colonne de `colonnes` "O" si elle est dans
    `gagnants`, "N" sinon -- voir le docstring du module pour la
    justification (sûr uniquement pour les groupes où le service
    garantit une réponse exhaustive). `gagnants=None` ou `set()` marque
    tout le groupe "N" (aucune colonne ne s'applique à ce point)."""
    gagnants = gagnants or set()
    for c in colonnes:
        valeurs[c] = "O" if c in gagnants else "N"


def _get(erreurs: Set[str], colonnes: Sequence[str], nom: str, fn: Callable[[], object]) -> object:
    """Exécute `fn()` ; renvoie son résultat normal (potentiellement
    `None`, une vraie absence de correspondance -- laissée telle quelle
    à l'appelant). Sur une erreur réseau/API connue -- voir le docstring
    du module --, ajoute `colonnes` à `erreurs` et renvoie le sentinel
    `_ERREUR` plutôt que d'avaler silencieusement en `None` (ancien
    comportement, corrigé le 2026-09-19). Catch volontairement RESTREINT :
    toute autre exception (bug de programmation) continue de remonter et
    de faire planter le run, jamais masquée."""
    try:
        return fn()
    except (requests.exceptions.RequestException, ApiServiceError) as exc:
        _logger.warning(
            "%s : erreur réseau/API (%s) -- colonne(s) %s marquée(s) \"ERREUR\" (retentée(s) "
            "automatiquement au prochain run, voir services/erreurs_service.py).",
            nom, exc, ",".join(sorted(colonnes)),
        )
        erreurs.update(colonnes)
        return _ERREUR


def _colonne_resiliente(
    valeurs: Dict[str, str], erreurs: Set[str], lettre: str, nom: str, fn: Callable[[], Optional[str]],
) -> None:
    """Équivalent résilient de `v = fn(); if v is not None: valeurs[lettre] = v`
    -- voir `_get`."""
    v = _get(erreurs, (lettre,), nom, fn)
    if v is _ERREUR:
        valeurs[lettre] = "ERREUR"
    elif v is not None:
        valeurs[lettre] = v


def _un_parmi_resilient(
    valeurs: Dict[str, str], erreurs: Set[str], colonnes: Sequence[str], nom: str,
    fn: Callable[[], Optional[str]],
) -> None:
    """Équivalent résilient de `_un_parmi(valeurs, colonnes, {fn()} if fn() else None)`
    -- sur une erreur réseau/API, TOUTES les colonnes sœurs deviennent
    "ERREUR" (jamais "N", voir `_get`/le docstring du module)."""
    c = _get(erreurs, colonnes, nom, fn)
    if c is _ERREUR:
        for col in colonnes:
            valeurs[col] = "ERREUR"
        return
    _un_parmi(valeurs, colonnes, {c} if c else None)


class ResolveurBE:
    def __init__(
        self, gewestplan: WfsGewestplanService, bruit: WfsBruitService,
        watertoets: WfsWatertoetsService, economie: WfsEconomieService,
        landinrichting: WfsLandinrichtingService, woningbouw: WfsWoningbouwService,
        natuurinrichting: WfsNatuurinrichtingService, landschap: WfsLandschapService,
        natuur: WfsNatuurService, bodem: WfsBodemService, seveso: WfsSevesoService,
        steunzone_brownfield: WfsSteunzoneBrownfieldService, ovam: WfsOvamService,
        grondverschuiving: WfsGrondverschuivingService,
        grondwaterwinning: WfsGrondwaterwinningService,
        afstromingskaart: WfsAfstromingskaartService, advieskaart: WfsAdvieskaartService,
        ruilverkaveling: WfsRuilverkavelingService,
    ) -> None:
        self._gewestplan = gewestplan
        self._bruit = bruit
        self._watertoets = watertoets
        self._economie = economie
        self._landinrichting = landinrichting
        self._woningbouw = woningbouw
        self._natuurinrichting = natuurinrichting
        self._landschap = landschap
        self._natuur = natuur
        self._bodem = bodem
        self._seveso = seveso
        self._steunzone_brownfield = steunzone_brownfield
        self._ovam = ovam
        self._grondverschuiving = grondverschuiving
        self._grondwaterwinning = grondwaterwinning
        self._afstromingskaart = afstromingskaart
        self._advieskaart = advieskaart
        self._ruilverkaveling = ruilverkaveling

    def resoudre(self, x: float, y: float) -> Tuple[Dict[str, str], Set[str]]:
        """`x`/`y` : position (Lambert 72, EPSG:31370) — la position de
        l'adresse liée à la parcelle (voir `AdresseBE`). Renvoie
        `(valeurs, erreurs)` -- voir le docstring du module."""
        valeurs: Dict[str, str] = {}
        erreurs: Set[str] = set()

        # -- Gewestplan (un seul parmi 26, correspondance floue) -------
        # Backfill "N" conditionné à une réponse BRUTE non vide (voir le
        # docstring du module) -- seul groupe traité avec cette prudence
        # supplémentaire vu sa taille (26 colonnes d'un coup).
        gewestplan_colonnes = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "gewestplan")
        candidats_gwp = {l: i["categorie"] for l, i in COLONNES_BE.items() if i["groupe"] == "gewestplan"}
        svnaam_trouves = _get(erreurs, gewestplan_colonnes, "Gewestplan", lambda: self._gewestplan.svnaam_categories(x, y))
        if svnaam_trouves is _ERREUR:
            for col in gewestplan_colonnes:
                valeurs[col] = "ERREUR"
        elif svnaam_trouves:
            gagnants_gwp: Set[str] = set()
            for svnaam in svnaam_trouves:
                colonne = ALIAS_GEWESTPLAN_CONNUS.get(normaliser(svnaam)) or meilleure_correspondance(svnaam, candidats_gwp)
                if colonne:
                    gagnants_gwp.add(colonne)
                else:
                    _logger.warning("Gewestplan : svnaam '%s' sans correspondance >= seuil.", svnaam)
            _un_parmi(valeurs, gewestplan_colonnes, gagnants_gwp)

        # -- Bruit (un seul parmi 5, par thème) -------------------------
        geluid_wegen = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "geluid_wegen")
        geluid_luchthaven = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "geluid_luchthaven")
        geluid_spoorwegen = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "geluid_spoorwegen")
        _un_parmi_resilient(valeurs, erreurs, geluid_wegen, "bruit routes", lambda: self._bruit.colonne_bruit_routes(x, y))
        _un_parmi_resilient(valeurs, erreurs, geluid_luchthaven, "bruit aéroport", lambda: self._bruit.colonne_bruit_aeroport(x, y))
        _un_parmi_resilient(valeurs, erreurs, geluid_spoorwegen, "bruit voies ferrées", lambda: self._bruit.colonne_bruit_voies_ferrees(x, y))

        # -- Watertoets ---------------------------------------------------
        overstr = _get(erreurs, ("DC", "DD"), "watertoets overstromingsgevoelig", lambda: self._watertoets.overstromingsgevoelig(x, y))
        if overstr is _ERREUR:
            valeurs["DC"] = "ERREUR"
            valeurs["DD"] = "ERREUR"
        else:
            gagnant_dcdd = "DC" if overstr == "mogelijk" else "DD" if overstr == "effectief" else None
            _un_parmi(valeurs, ("DC", "DD"), {gagnant_dcdd} if gagnant_dcdd else None)

        signaal = _get(erreurs, ("DT", "DU"), "watertoets signaalgebied_categorie", lambda: self._watertoets.signaalgebied_categorie(x, y))
        if signaal is _ERREUR:
            valeurs["DT"] = "ERREUR"
            valeurs["DU"] = "ERREUR"
        else:
            gagnant_dtdu = "DT" if signaal == "Bouwvrije opgave" else "DU" if signaal == "Verscherpte watertoets" else None
            _un_parmi(valeurs, ("DT", "DU"), {gagnant_dtdu} if gagnant_dtdu else None)

        # DX : colonne seule (pas de sœur) -- risicozone() renvoie le
        # texte du champ si une feature existe, sinon None (jamais
        # deviné entre "hors zone" et "pas de risque", même convention
        # que les ~100 autres colonnes existence-only du pipeline).
        dx = _get(erreurs, ("DX",), "watertoets risicozone", lambda: self._watertoets.risicozone(x, y))
        valeurs["DX"] = "ERREUR" if dx is _ERREUR else ("O" if dx else "N")

        # Même défaut de structure qu'EN, corrigé au même moment (2026-09-24) : `elif
        # lbl:` (vérité) laissait la cellule VIDE quand `lbl` est `None` (aucun polygone
        # NOG:Nog à ce point -- le cas normal hors zone naturellement inondable), pas
        # seulement sur une chaîne vide. Pas encore observé en pratique (cette rue de
        # Sint-Truiden tombait toujours dans une zone classée), mais même risque
        # structurel qu'EN -- corrigé par prudence avant qu'il ne se manifeste ailleurs.
        lbl = _get(erreurs, ("DW",), "watertoets van_nature_overstroombaar", lambda: self._watertoets.van_nature_overstroombaar(x, y))
        if lbl is _ERREUR:
            valeurs["DW"] = "ERREUR"
        elif lbl is None:
            valeurs["DW"] = "N"
        else:
            valeurs["DW"] = "O" if "niet" not in lbl.lower() else "N"

        # DZ : `overstromingsgebied_oeverzone_iwb` renvoie déjà "O"/"N"
        # (voir `_existe_wfs`) -- écrire directement la valeur.
        _colonne_resiliente(valeurs, erreurs, "DZ", "watertoets overstromingsgebied_oeverzone_iwb", lambda: self._watertoets.overstromingsgebied_oeverzone_iwb(x, y))

        _un_parmi_resilient(valeurs, erreurs, ("DR", "DS"), "watertoets colonne_afgebakend_oeverzone_iwb", lambda: self._watertoets.colonne_afgebakend_oeverzone_iwb(x, y))
        _un_parmi_resilient(valeurs, erreurs, ("DF", "DG", "DH", "DI"), "watertoets pluviaal", lambda: self._watertoets.colonne_overstromingsgevoelig_pluviaal(x, y))
        _un_parmi_resilient(valeurs, erreurs, ("DJ", "DK", "DL", "DM"), "watertoets fluviaal", lambda: self._watertoets.colonne_overstromingsgevoelig_fluviaal(x, y))
        _un_parmi_resilient(valeurs, erreurs, ("DN", "DO", "DP", "DQ"), "watertoets zee", lambda: self._watertoets.colonne_overstromingsgevoelig_zee(x, y))

        _colonne_resiliente(valeurs, erreurs, "DY", "watertoets recent_overstroomd", lambda: self._watertoets.recent_overstroomd(x, y))

        # -- Économie (EN valeur brute + EO->ER existence) ---------------
        # Bug réel corrigé le 2026-09-24 (1060/1385 lignes vides sur un run
        # Sint-Truiden) : `aangeboden_perceel` renvoie `None` -- une réponse
        # LÉGITIME, pas une erreur -- pour l'immense majorité des parcelles
        # (aucune parcelle "Bedrperc" à ce point, donc pas dans une zone
        # d'entreprise du tout). L'ancien code ne posait "O"/"N" QUE si une
        # parcelle Bedrperc existait, laissant la cellule VIDE partout
        # ailleurs -- jamais voulu (même règle que ~100 autres colonnes de ce
        # pipeline : "N" par défaut, jamais deviné mais jamais vide non plus).
        aangeboden = _get(erreurs, ("EN",), "economie aangeboden_perceel", lambda: self._economie.aangeboden_perceel(x, y))
        if aangeboden is _ERREUR:
            valeurs["EN"] = "ERREUR"
        elif aangeboden is None:
            valeurs["EN"] = "N"
        else:
            valeurs["EN"] = "O" if aangeboden == "aangeboden" else "N"
        for lettre, methode in (
            ("EO", self._economie.bedrijventerrein), ("EP", self._economie.beheerde_bedrijvenzone),
            ("EQ", self._economie.ontwikkelbare_bedrijvenzone), ("ER", self._economie.planningszone),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"economie {lettre}", lambda methode=methode: methode(x, y))

        # -- Landinrichting / Natuurinrichting / Woningbouw --------------
        for lettre, methode in (
            ("EW", self._landinrichting.landinrichting_in_onderzoek),
            ("EX", self._landinrichting.vastgesteld_landinrichtingsproject),
            ("EY", self._landinrichting.landinrichtingsplan),
            ("EZ", self._natuurinrichting.natuurinrichting),
            ("FA", self._ruilverkaveling.ruilverkaveling_uit_kracht_van_wet),
            ("GF", self._woningbouw.woningbouwgebied),
            ("GG", self._woningbouw.woonvernieuwingsgebied),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"landinrichting {lettre}", lambda methode=methode: methode(x, y))

        # -- Landschap ------------------------------------------------
        # Même défaut de structure qu'EN/DW (corrigés le 2026-09-24) : `elif fb is not
        # None:` laissait la cellule VIDE quand `fb is None` (aucun polygone à ce point)
        # -- pas encore observé en pratique sur ce jeu de rues (couverture complète),
        # mais même risque structurel, corrigé par prudence. Aucun polygone ici est
        # conceptuellement proche de "Onbepaald" (déjà mappé sur N), pas une vraie
        # correspondance -- même repli "N".
        fb = _get(erreurs, ("FB",), "landschap fysische_systeemeenheid", lambda: self._landschap.fysische_systeemeenheid(x, y))
        if fb is _ERREUR:
            valeurs["FB"] = "ERREUR"
        elif fb is None:
            valeurs["FB"] = "N"
        else:
            valeurs["FB"] = "N" if fb == "Onbepaald" else "O"
        _colonne_resiliente(valeurs, erreurs, "FC", "landschap traditioneel_landschap", lambda: self._landschap.traditioneel_landschap(x, y))

        # -- Nature -----------------------------------------------------
        for lettre, methode in (
            ("FG", self._natuur.duinendecreet), ("FH", self._natuur.ven),
            ("FI", self._natuur.habitatrichtlijngebied), ("FJ", self._natuur.vogelrichtlijngebied),
            ("FF", self._natuur.erkend_natuurreservaat),
            ("FD", self._natuur.beheergebied_natura2000_soorten),
            ("FE", self._natuur.bosreservaat),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"natuur {lettre}", lambda methode=methode: methode(x, y))

        # -- Bodem (érosion) ---------------------------------------------
        for lettre, methode in (
            ("EL", self._bodem.potentiele_bodemerosie), ("EI", self._bodem.andere_erosiegerelateerde_gronden),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"bodem {lettre}", lambda methode=methode: methode(x, y))

        # -- Seveso -------------------------------------------------------
        for lettre, methode in (
            ("EU", self._seveso.seveso_inrichting), ("EV", self._seveso.seveso_consultatiezone),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"seveso {lettre}", lambda methode=methode: methode(x, y))

        # -- Steunzone / Brownfield ---------------------------------------
        for lettre, methode in (
            ("ET", self._steunzone_brownfield.steunzone),
            ("ES", self._steunzone_brownfield.brownfieldconvenant),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"steunzone {lettre}", lambda methode=methode: methode(x, y))

        # -- OVAM (pollution des sols, un seul parmi 6) -------------------
        # Sûr à 100% : le service a déjà un palier de repli explicite
        # "GE" pour l'absence de feature (voir wfs_ovam_service.py), donc
        # une réponse SANS exception est toujours exploitable -- seule
        # une vraie exception réseau/API marque le groupe "ERREUR".
        bodem_verontr = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "bodem_verontreiniging")
        _un_parmi_resilient(valeurs, erreurs, bodem_verontr, "OVAM bodemverontreiniging", lambda: self._ovam.colonne_bodemverontreiniging(x, y))

        # -- Grondverschuivingen -----------------------------------------
        for lettre, methode in (
            ("EJ", self._grondverschuiving.gekarteerde_grondverschuiving),
            ("EK", self._grondverschuiving.gevoeligheid_grondverschuiving),
        ):
            _colonne_resiliente(valeurs, erreurs, lettre, f"grondverschuiving {lettre}", lambda methode=methode: methode(x, y))

        _colonne_resiliente(valeurs, erreurs, "EA", "grondwaterwinning", lambda: self._grondwaterwinning.grondwaterwingebied(x, y))

        afstroom_colonnes = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "erosion_afstroming")
        _un_parmi_resilient(valeurs, erreurs, afstroom_colonnes, "afstromingskaart", lambda: self._afstromingskaart.colonne_afstromingskaart(x, y))

        _colonne_resiliente(valeurs, erreurs, "EM", "advieskaart watertoets", lambda: self._advieskaart.watertoets(x, y))

        # -- DE / DV / EB : aucune source live trouvée malgré recherche
        # approfondie (voir wfs_watertoets_service.py) -- "N" partout,
        # SANS couleur distinctive, sur demande explicite du 2026-09-17
        # ("met les tous N sans colorer le fond"). Valeur STRUCTURELLE
        # (pas un appel réseau) -- jamais "ERREUR", rien à retenter ici.
        valeurs["DE"] = "N"
        valeurs["DV"] = "N"
        valeurs["EB"] = "N"

        # -- RUP (AF->DB, 75 colonnes) -----------------------------------
        # Décision explicite de l'utilisateur (2026-09-16) : le bloc RUP
        # détaillé de la feuille principale n'est PAS rempli par le
        # pipeline (classement fiable non construit, voir le plan) --
        # écrit "/" partout, même convention que le gabarit officiel pour
        # une cellule non applicable (voir ligne 4 des en-têtes). Le
        # vrai remplissage RUP se fait à la main sur la feuille "RUP"
        # dédiée (lien + texte libre par niveau), pas ici. Valeur
        # STRUCTURELLE (pas un appel réseau) -- jamais "ERREUR".
        for lettre, info in COLONNES_BE.items():
            if info["groupe"] in ("rup_region", "rup_province", "rup_commune"):
                valeurs[lettre] = "/"

        return valeurs, erreurs
