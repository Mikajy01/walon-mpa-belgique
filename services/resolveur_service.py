"""Orchestration : résout TOUTES les colonnes confirmées (voir le plan --
177/184 avec le bloc RUP "/" inclus, DY/FA/FD ajoutés le 2026-09-16 ;
seules DE/DV/EB/FE restent sans source confirmée, voir le docstring de
`wfs_watertoets_service.py`/`wfs_natuur_service.py`) pour un point donné
(position d'adresse, Lambert 72) en appelant chaque service thématique
déjà construit et vérifié.

Principe de sortie : `Dict[str, str]` lettre de colonne Excel -> valeur
("O"/"N"/texte).

**Groupes "un seul parmi plusieurs" (Gewestplan, paliers watertoets,
bruit, pollution des sols OVAM)** : décision du 2026-09-17 (demande
explicite de l'utilisateur) -- `_un_parmi()` écrit maintenant "N" dans
TOUTES les colonnes sœurs dès que le service a répondu, pas seulement
"O" dans la gagnante. Ce n'est sûr QUE parce que chaque service
concerné garantit une réponse EXHAUSTIVE une fois qu'il a répondu sans
exception :
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
  colonnes de ce pipeline (`None` = "N", que la couche n'ait renvoyé
  aucune entité OU qu'une erreur réseau soit survenue, la distinction
  n'étant exposée nulle part ailleurs dans ce projet non plus) --
  cohérent avec l'existant, pas un risque nouveau.
Chaque service continue de journaliser un avertissement sur une
vraie erreur réseau ; le "N" écrit dans ce cas est donc une hypothèse
DÉJÀ acceptée partout ailleurs dans ce pipeline, pas une nouveauté
propre aux groupes à choix unique.

Gewestplan reste le seul groupe où le remplissage est conditionné à une
réponse BRUTE non vide du service (`svnaam_categories`) avant de
marquer "N" partout -- 26 colonnes d'un coup sur une fausse déduction
aurait un impact bien plus large qu'une colonne seule, marge de
prudence supplémentaire justifiée par la taille du groupe."""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Set

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


def _un_parmi(valeurs: Dict[str, str], colonnes: Sequence[str], gagnants: Optional[Set[str]] = None) -> None:
    """Marque chaque colonne de `colonnes` "O" si elle est dans
    `gagnants`, "N" sinon -- voir le docstring du module pour la
    justification (sûr uniquement pour les groupes où le service
    garantit une réponse exhaustive). `gagnants=None` ou `set()` marque
    tout le groupe "N" (aucune colonne ne s'applique à ce point)."""
    gagnants = gagnants or set()
    for c in colonnes:
        valeurs[c] = "O" if c in gagnants else "N"


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

    def resoudre(self, x: float, y: float) -> Dict[str, str]:
        """`x`/`y` : position (Lambert 72, EPSG:31370) — la position de
        l'adresse liée à la parcelle (voir `AdresseBE`)."""
        valeurs: Dict[str, str] = {}

        # -- Gewestplan (un seul parmi 26, correspondance floue) -------
        # Backfill "N" conditionné à une réponse BRUTE non vide (voir le
        # docstring du module) -- seul groupe traité avec cette prudence
        # supplémentaire vu sa taille (26 colonnes d'un coup).
        gewestplan_colonnes = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "gewestplan")
        candidats_gwp = {l: i["categorie"] for l, i in COLONNES_BE.items() if i["groupe"] == "gewestplan"}
        svnaam_trouves = self._gewestplan.svnaam_categories(x, y)
        if svnaam_trouves:
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
        c = self._bruit.colonne_bruit_routes(x, y)
        _un_parmi(valeurs, geluid_wegen, {c} if c else None)
        c = self._bruit.colonne_bruit_aeroport(x, y)
        _un_parmi(valeurs, geluid_luchthaven, {c} if c else None)
        c = self._bruit.colonne_bruit_voies_ferrees(x, y)
        _un_parmi(valeurs, geluid_spoorwegen, {c} if c else None)

        # -- Watertoets ---------------------------------------------------
        overstr = self._watertoets.overstromingsgevoelig(x, y)
        gagnant_dcdd = "DC" if overstr == "mogelijk" else "DD" if overstr == "effectief" else None
        _un_parmi(valeurs, ("DC", "DD"), {gagnant_dcdd} if gagnant_dcdd else None)

        signaal = self._watertoets.signaalgebied_categorie(x, y)
        gagnant_dtdu = "DT" if signaal == "Bouwvrije opgave" else "DU" if signaal == "Verscherpte watertoets" else None
        _un_parmi(valeurs, ("DT", "DU"), {gagnant_dtdu} if gagnant_dtdu else None)

        # DX : colonne seule (pas de sœur) -- risicozone() renvoie le
        # texte du champ si une feature existe, sinon None (jamais
        # deviné entre "hors zone" et "pas de risque", même convention
        # que les ~100 autres colonnes existence-only du pipeline).
        valeurs["DX"] = "O" if self._watertoets.risicozone(x, y) else "N"

        lbl = self._watertoets.van_nature_overstroombaar(x, y)
        if lbl:
            valeurs["DW"] = "O" if "niet" not in lbl.lower() else "N"

        # DZ : `overstromingsgebied_oeverzone_iwb` renvoie déjà "O"/"N"
        # (voir `_existe_wfs`) -- écrire directement la valeur au lieu
        # de filtrer sur "O" comme avant (le "N" calculé était jeté).
        dz = self._watertoets.overstromingsgebied_oeverzone_iwb(x, y)
        if dz is not None:
            valeurs["DZ"] = dz

        iwb = self._watertoets.colonne_afgebakend_oeverzone_iwb(x, y)
        _un_parmi(valeurs, ("DR", "DS"), {iwb} if iwb else None)

        pluvial = self._watertoets.colonne_overstromingsgevoelig_pluviaal(x, y)
        _un_parmi(valeurs, ("DF", "DG", "DH", "DI"), {pluvial} if pluvial else None)
        fluvial = self._watertoets.colonne_overstromingsgevoelig_fluviaal(x, y)
        _un_parmi(valeurs, ("DJ", "DK", "DL", "DM"), {fluvial} if fluvial else None)
        zee = self._watertoets.colonne_overstromingsgevoelig_zee(x, y)
        _un_parmi(valeurs, ("DN", "DO", "DP", "DQ"), {zee} if zee else None)

        v = self._watertoets.recent_overstroomd(x, y)
        if v is not None:
            valeurs["DY"] = v

        # -- Économie (EN valeur brute + EO->ER existence) ---------------
        aangeboden = self._economie.aangeboden_perceel(x, y)
        if aangeboden is not None:
            valeurs["EN"] = "O" if aangeboden == "aangeboden" else "N"
        for lettre, methode in (
            ("EO", self._economie.bedrijventerrein), ("EP", self._economie.beheerde_bedrijvenzone),
            ("EQ", self._economie.ontwikkelbare_bedrijvenzone), ("ER", self._economie.planningszone),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

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
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        # -- Landschap ------------------------------------------------
        fb = self._landschap.fysische_systeemeenheid(x, y)
        if fb is not None:
            valeurs["FB"] = "N" if fb == "Onbepaald" else "O"
        fc = self._landschap.traditioneel_landschap(x, y)
        if fc is not None:
            valeurs["FC"] = fc

        # -- Nature -----------------------------------------------------
        for lettre, methode in (
            ("FG", self._natuur.duinendecreet), ("FH", self._natuur.ven),
            ("FI", self._natuur.habitatrichtlijngebied), ("FJ", self._natuur.vogelrichtlijngebied),
            ("FF", self._natuur.erkend_natuurreservaat),
            ("FD", self._natuur.beheergebied_natura2000_soorten),
            ("FE", self._natuur.bosreservaat),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        # -- Bodem (érosion) ---------------------------------------------
        for lettre, methode in (
            ("EL", self._bodem.potentiele_bodemerosie), ("EI", self._bodem.andere_erosiegerelateerde_gronden),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        # -- Seveso -------------------------------------------------------
        for lettre, methode in (
            ("EU", self._seveso.seveso_inrichting), ("EV", self._seveso.seveso_consultatiezone),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        # -- Steunzone / Brownfield ---------------------------------------
        for lettre, methode in (
            ("ET", self._steunzone_brownfield.steunzone),
            ("ES", self._steunzone_brownfield.brownfieldconvenant),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        # -- OVAM (pollution des sols, un seul parmi 6) -------------------
        # Sûr à 100% : le service a déjà un palier de repli explicite
        # "GE" pour l'absence de feature (voir wfs_ovam_service.py), donc
        # `colonne_ovam` est None UNIQUEMENT sur une vraie erreur réseau.
        bodem_verontr = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "bodem_verontreiniging")
        colonne_ovam = self._ovam.colonne_bodemverontreiniging(x, y)
        _un_parmi(valeurs, bodem_verontr, {colonne_ovam} if colonne_ovam else None)

        # -- Grondverschuivingen -----------------------------------------
        for lettre, methode in (
            ("EJ", self._grondverschuiving.gekarteerde_grondverschuiving),
            ("EK", self._grondverschuiving.gevoeligheid_grondverschuiving),
        ):
            v = methode(x, y)
            if v is not None:
                valeurs[lettre] = v

        v = self._grondwaterwinning.grondwaterwingebied(x, y)
        if v is not None:
            valeurs["EA"] = v

        afstroom_colonnes = tuple(l for l, i in COLONNES_BE.items() if i["groupe"] == "erosion_afstroming")
        colonne_afstr = self._afstromingskaart.colonne_afstromingskaart(x, y)
        _un_parmi(valeurs, afstroom_colonnes, {colonne_afstr} if colonne_afstr else None)

        v = self._advieskaart.watertoets(x, y)
        if v is not None:
            valeurs["EM"] = v

        # -- DE / DV / EB : aucune source live trouvée malgré recherche
        # approfondie (voir wfs_watertoets_service.py) -- "N" partout,
        # SANS couleur distinctive, sur demande explicite du 2026-09-17
        # ("met les tous N sans colorer le fond"). BUG corrigé le
        # 2026-09-18 : cette règle n'avait été appliquée qu'en patch
        # direct sur un fichier déjà écrit, jamais ici dans le code --
        # toute commune traitée depuis laissait ces 3 colonnes VIDES
        # (confirmé en direct sur Lille, 2275).
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
        # dédiée (lien + texte libre par niveau), pas ici.
        for lettre, info in COLONNES_BE.items():
            if info["groupe"] in ("rup_region", "rup_province", "rup_commune"):
                valeurs[lettre] = "/"

        return valeurs
