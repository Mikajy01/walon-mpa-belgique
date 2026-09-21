"""Découverte des parcelles bordant une rue flamande — orchestration de
`AdressenService` + `CadastreService`, remplace le
`decouvrir_parcelles`/`TraversalService.trier`/seuils de bordure français
(voir le plan, "on fait confiance à la parcelle").

Principe : le registre flamand lie chaque adresse à sa parcelle
OFFICIELLEMENT (pas de calcul de distance/bordure à faire), donc la
"découverte" se réduit à : lister les adresses de la rue, suivre leur
lien vers leur(s) parcelle(s), dédupliquer par parcelle (plusieurs
adresses/unités peuvent partager une même parcelle — écart réel trouvé
en investigation live, Sint-Truiden "Heilig-Hartplein" : 5 adresses "7A"
distinctes toutes liées à `71053H1303/00A000`), puis ordonner : parité du
numéro (pair/impair, un côté puis l'autre) et numéro croissant au sein
d'un côté — convention à confirmer sur un cas réel connu avant de
généraliser (voir le plan, §Vérification 3).

**Parcelles "sœurs" SANS adresse propre** — écart réel trouvé en
investigation live (Mespelhoek 7, Lokeren, 2026-09-19) : le registre
Basisregisters Vlaanderen ne lie une adresse QU'à certaines parcelles
("247G" pour Mespelhoek 7) ; des parcelles cadastrales visibles sur
geopunt.be juste à côté ("247S"/"247T", même référence de base,
bis-lettre différente) n'ont AUCUNE adresse propre -- très probablement
des subdivisions du même terrain d'origine (jardin/annexe). Décision
explicite de l'utilisateur (2026-09-19) : ces parcelles doivent quand
même être traitées (la checklist est par PARCELLE, pas par adresse), pas
seulement celles qu'une adresse pointe directement. Trouvées via
`CadastreService.get_parcelle_et_voisines` (même bbox que la parcelle
adressée, jamais une recherche séparée) et rattachées juste après leur
"ancrage" dans l'ordre final, avec une adresse SYNTHÉTIQUE
(`huisnummer="/"`, même convention "non applicable" que le reste du
projet -- voir `_adresse_synthetique_sans_adresse`) plutôt que de leur
inventer un faux numéro de maison."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from models.adresse import AdresseBE
from models.parcelle import Parcelle
from services.adressen_service import AdressenService
from services.cadastre_service import CadastreService, lambert72_vers_4258, vers_lambert72
from services.decouverte_geometrique_service import DecouverteGeometrique, Trace
from utils.geometrie import point_interieur
from utils.logger import get_logger

_logger = get_logger("services.decouverte_service")


@dataclass
class ParcelleTrouvee:
    parcelle: Parcelle
    adresses: List[AdresseBE]  # toutes les adresses liées à cette parcelle (1 synthétique si sans adresse propre)
    cote: str  # "pair" / "impair" / "sans_adresse"
    # Rempli UNIQUEMENT pendant la construction (voir decouvrir_parcelles) --
    # parcelles sœurs sans adresse propre, insérées juste après celle-ci dans
    # le résultat final aplati. Toujours vide sur une entrée déjà aplatie.
    voisines_sans_adresse: List["ParcelleTrouvee"] = field(default_factory=list)


def decouvrir_parcelles(
    gemeentenaam: str, straatnaam: str, adressen: AdressenService, cadastre: CadastreService,
    geometrique: Optional[DecouverteGeometrique] = None,
) -> List[ParcelleTrouvee]:
    """Découvre et ordonne les parcelles de `straatnaam` (`gemeentenaam`).
    Sans `geometrique` : un côté d'abord (numéros impairs), puis l'autre,
    chacun par numéro croissant, chaque parcelle adressée suivie de ses sœurs
    sans adresse propre. Avec `geometrique` : les parcelles qui bordent la
    rue SANS aucune adresse (champs, digues) sont ajoutées, et TOUTES les
    parcelles (avec ou sans numéro) sont ordonnées le long de la rue, un côté
    d'abord puis l'autre, dans l'ordre d'apparition (`_ordonner_le_long`,
    demande du 2026-09-21) ; repli sur l'ordre pair/impair si le tracé de la
    rue est indisponible."""
    adresses = adressen.lister_adresses(gemeentenaam, straatnaam)
    if not adresses:
        _logger.warning("Aucune adresse trouvée pour '%s' (%s).", straatnaam, gemeentenaam)
        if geometrique is None:
            return []

    par_parcelle: Dict[Tuple[str, ...], List[AdresseBE]] = {}
    for a in adresses:
        if not a.capakeys:
            continue  # déjà journalisé dans AdressenService, jamais deviné ici
        cle = tuple(sorted(a.capakeys))
        par_parcelle.setdefault(cle, []).append(a)

    # Toutes les références capaKey pointées par une adresse RÉELLE
    # (potentiellement plusieurs par adresse) -- jamais traiter l'une
    # d'elles comme une "sœur sans adresse", même si elle apparaît dans
    # le bbox d'une autre adresse AVANT d'être traitée comme sa propre
    # entrée par la boucle principale (l'ordre d'itération de
    # `par_parcelle` ne suit pas forcément l'ordre géographique).
    references_adressees: set = set()
    for a in adresses:
        references_adressees.update(a.capakeys)

    trouvees: List[ParcelleTrouvee] = []
    references_emises: set = set()
    for cle, groupe in par_parcelle.items():
        groupe.sort(key=lambda a: a.numero)
        premiere = groupe[0]
        try:
            p, voisines = _resoudre_geometrie_et_voisines(cle[0], premiere, cadastre)
        except Exception as exc:  # noqa: BLE001 -- voir cadastre_service.py : ce host (fédéral
            # belge) est connu pour être capricieux (timeouts/resets même après les 5 tentatives
            # de tenacity déjà faites dans http_client.py). Un incident réel a montré qu'une
            # seule adresse en échec ici faisait planter TOUTE la rue (et le run entier, aucun
            # try/except ici avant) -- corrigé le 2026-09-19 : on saute cette seule parcelle
            # (journalisée, jamais silencieuse) plutôt que de perdre tout le reste du run.
            _logger.warning(
                "Résolution de la géométrie de '%s' (adresse n°%s) a échoué (%s: %s) -- "
                "parcelle SAUTÉE pour ce run (le cadastre fédéral belge est un service connu "
                "pour être capricieux). Relance le même traitement plus tard pour retenter "
                "cette adresse -- elle sera simplement absente de l'Excel tant que ça échoue.",
                cle[0], premiere.numero, type(exc).__name__, exc,
            )
            continue
        if p is None:
            continue
        if p.reference in references_emises:
            continue
        references_emises.add(p.reference)
        entree = ParcelleTrouvee(parcelle=p, adresses=groupe, cote=premiere.parite)
        trouvees.append(entree)

        for voisine in voisines:
            if voisine.reference in references_adressees:
                continue  # a sa PROPRE adresse ailleurs -- jamais dupliquée en "sans adresse"
            if voisine.reference in references_emises:
                continue  # déjà ajoutée comme sœur d'un autre ancrage
            adresse_synth = _adresse_synthetique_sans_adresse(gemeentenaam, straatnaam, voisine)
            if adresse_synth is None:
                # Géométrie de la sœur elle-même inexploitable (jamais vu en
                # pratique -- le bbox qui l'a rapportée avait forcément une
                # géométrie valide, voir _parser_membres) -- sautée plutôt
                # que deviner une position.
                _logger.warning(
                    "Parcelle sœur '%s' (sans adresse propre, base commune avec '%s') a une "
                    "géométrie inexploitable -- sautée.", voisine.reference, p.reference,
                )
                continue
            references_emises.add(voisine.reference)
            entree.voisines_sans_adresse.append(
                ParcelleTrouvee(parcelle=voisine, adresses=[adresse_synth], cote="sans_adresse"),
            )

    # -- Tracé de la rue + parcelles SANS adresse le long de la route -------
    trace: Optional[Trace] = None
    le_long: list = []
    if geometrique is not None:
        try:
            trace = geometrique.tracer(gemeentenaam, straatnaam)
        except Exception as exc:  # noqa: BLE001 -- jamais perdre les parcelles déjà trouvées par adresse
            _logger.warning(
                "Tracé de '%s' (%s) indisponible (%s: %s) -- ordre par numéros pair/impair, sans parcelles "
                "géométriques pour ce run ; relance plus tard pour retenter.",
                straatnaam, gemeentenaam, type(exc).__name__, exc,
            )
        if trace is not None:
            try:
                le_long = geometrique.parcelles_le_long(gemeentenaam, straatnaam, trace)
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Découverte géométrique de '%s' (%s) a échoué (%s: %s) -- seules les parcelles liées à une "
                    "adresse sont retournées pour ce run ; relance plus tard pour retenter.",
                    straatnaam, gemeentenaam, type(exc).__name__, exc,
                )
                le_long = []

    entrees_geo: List[Tuple[ParcelleTrouvee, str, float]] = []
    for pl in le_long:
        ref = pl.parcelle.reference
        if ref in references_adressees or ref in references_emises:
            continue  # déjà trouvée par adresse (ou comme sœur) -- jamais dupliquée
        references_emises.add(ref)
        adresse_synth = AdresseBE(
            object_id="", huisnummer="/", straatnaam=straatnaam, gemeentenaam=gemeentenaam,
            x=pl.x, y=pl.y, capakeys=[ref],
        )
        entrees_geo.append((ParcelleTrouvee(parcelle=pl.parcelle, adresses=[adresse_synth], cote="geometrie"), pl.cote, pl.abscisse))
    if geometrique is not None:
        _logger.info("'%s' : %d parcelle(s) supplémentaire(s) trouvée(s) par la géométrie de la rue.", straatnaam, len(entrees_geo))

    if trace is not None:
        return _ordonner_le_long(trouvees, entrees_geo, trace, straatnaam)

    # Repli SANS tracé : un côté d'abord (impair, convention standard belge/
    # européenne -- numéros impairs généralement d'un côté, pairs de l'autre),
    # puis l'autre, chacun par numéro croissant. Les sœurs sans adresse sont
    # aplaties juste après leur ancrage.
    ordre_cote = {"impair": 0, "pair": 1}
    trouvees.sort(key=lambda pt: (ordre_cote.get(pt.cote, 2), pt.adresses[0].numero))
    resultat: List[ParcelleTrouvee] = []
    for entree in trouvees:
        resultat.append(entree)
        resultat.extend(entree.voisines_sans_adresse)
    return resultat


def _ordonner_le_long(
    trouvees: List[ParcelleTrouvee], entrees_geo: List[Tuple[ParcelleTrouvee, str, float]],
    trace: Trace, straatnaam: str,
) -> List[ParcelleTrouvee]:
    """Ordre "un côté d'abord, puis l'autre, dans l'ordre d'apparition" pour
    TOUTES les parcelles, avec ou sans numéro de maison (demande du
    2026-09-21 : "toujours dans l'ordre, même si le numéro n'est ni pair ni
    impair"). Chaque parcelle -- adressée (position de son adresse) ou non
    (point intérieur de la parcelle) -- reçoit un côté et une abscisse le long
    de la ligne centrale de la rue ; les sœurs sans adresse suivent leur
    ancrage. Deux choix pour rester cohérent avec l'ancienne convention :
    - SENS : le tracé est retourné si les numéros de maison DÉCROISSENT le
      long du tracé (corrélation numéro/abscisse négative), pour que l'ordre
      aille dans le sens des numéros croissants ;
    - PREMIER CÔTÉ : celui qui porte la majorité des numéros impairs (comme
      avant) ; sans adresse du tout (Rode Moerdijk), le côté "gauche" du
      tracé, arbitraire mais déterministe."""
    items: List[list] = []  # [entrée, côté, abscisse]
    for e in trouvees:
        a = e.adresses[0]
        cote, abscisse, _ = trace.position(a.x, a.y)
        items.append([e, cote, abscisse])
    n_adressees = len(items)
    for e, cote, abscisse in entrees_geo:
        items.append([e, cote, abscisse])

    numeros = [(it[0].adresses[0].numero, it[2]) for it in items[:n_adressees] if it[0].adresses[0].numero > 0]
    retourne = False
    if len(numeros) >= 2:
        m_n = sum(n for n, _ in numeros) / len(numeros)
        m_a = sum(a for _, a in numeros) / len(numeros)
        retourne = sum((n - m_n) * (a - m_a) for n, a in numeros) < 0
    if retourne:
        for it in items:
            it[2] = trace.longueur - it[2]
            it[1] = "droite" if it[1] == "gauche" else "gauche"

    impairs = {"gauche": 0, "droite": 0}
    for it in items[:n_adressees]:
        a = it[0].adresses[0]
        if a.numero > 0 and a.parite == "impair":
            impairs[it[1]] += 1
    premier = "droite" if impairs["droite"] > impairs["gauche"] else "gauche"
    _logger.info(
        "'%s' : ordre le long de la rue (%.0f m) -- sens %s, premier côté '%s' (impairs : %d à gauche, %d à droite).",
        straatnaam, trace.longueur, "inversé" if retourne else "du tracé", premier, impairs["gauche"], impairs["droite"],
    )

    items.sort(key=lambda it: (0 if it[1] == premier else 1, it[2]))
    resultat: List[ParcelleTrouvee] = []
    for entree, _cote, _abscisse in items:
        resultat.append(entree)
        resultat.extend(entree.voisines_sans_adresse)
    return resultat


def _resoudre_geometrie_et_voisines(
    reference: str, adresse_reference: AdresseBE, cadastre: CadastreService,
) -> Tuple[Optional[Parcelle], List[Parcelle]]:
    lon, lat = lambert72_vers_4258(adresse_reference.x, adresse_reference.y)
    return cadastre.get_parcelle_et_voisines(reference, lat, lon)


def _adresse_synthetique_sans_adresse(
    gemeentenaam: str, straatnaam: str, voisine: Parcelle,
) -> Optional[AdresseBE]:
    """Construit une "adresse" SYNTHÉTIQUE (`huisnummer="/"`, même
    convention "non applicable" que le reste du projet -- RUP, DE/DV/EB,
    etc. -- plutôt que d'inventer un faux numéro de maison) pour une
    parcelle SANS adresse propre au registre (voir le docstring du
    module). Garde `main.py` totalement inchangé : il continue de lire
    `p.adresses[0].x/.y/.huisnummer` exactement comme pour une parcelle
    normalement adressée. Position = centroïde de SA PROPRE géométrie
    (jamais celle de l'adresse d'ancrage, potentiellement éloignée si la
    parcelle sœur est grande -- ex. un jardin) -- reprojeté vers Lambert
    72 (`vers_lambert72`, l'inverse de `lambert72_vers_4258`) puisque
    TOUT le reste du pipeline (résolveur WFS, `AdresseBE.x/y`) travaille
    dans ce système, alors que la géométrie cadastrale est en EPSG:4258
    (voir `cadastre_service.py::_parser_geometrie`)."""
    if voisine.geometry is None:
        return None
    lon, lat = point_interieur(voisine.geometry)
    x, y = vers_lambert72(lon, lat)
    return AdresseBE(
        object_id="", huisnummer="/", straatnaam=straatnaam, gemeentenaam=gemeentenaam,
        x=x, y=y, capakeys=[voisine.reference],
    )
