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
from utils.geometrie import centroide_geometrie
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
) -> List[ParcelleTrouvee]:
    """Découvre et ordonne les parcelles de `straatnaam` (`gemeentenaam`) :
    un côté d'abord (numéros impairs par convention, voir plus bas), puis
    l'autre, chacun trié par numéro croissant, chaque parcelle adressée
    immédiatement suivie de ses éventuelles sœurs sans adresse propre --
    voir le docstring du module."""
    adresses = adressen.lister_adresses(gemeentenaam, straatnaam)
    if not adresses:
        _logger.warning("Aucune adresse trouvée pour '%s' (%s).", straatnaam, gemeentenaam)
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

    # Un côté d'abord (impair, convention standard belge/européenne —
    # numéros impairs généralement d'un côté, pairs de l'autre), puis
    # l'autre, chacun par numéro croissant. Les sœurs sans adresse n'ont
    # pas leur place dans ce tri (pas de vrai numéro) -- aplaties juste
    # après leur ancrage ensuite, jamais mélangées au tri lui-même.
    ordre_cote = {"impair": 0, "pair": 1}
    trouvees.sort(key=lambda pt: (ordre_cote.get(pt.cote, 2), pt.adresses[0].numero))

    resultat: List[ParcelleTrouvee] = []
    for entree in trouvees:
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
    lon, lat = centroide_geometrie(voisine.geometry)
    x, y = vers_lambert72(lon, lat)
    return AdresseBE(
        object_id="", huisnummer="/", straatnaam=straatnaam, gemeentenaam=gemeentenaam,
        x=x, y=y, capakeys=[voisine.reference],
    )
