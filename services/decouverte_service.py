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
généraliser (voir le plan, §Vérification 3)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from models.adresse import AdresseBE
from models.parcelle import Parcelle
from services.adressen_service import AdressenService
from services.cadastre_service import CadastreService, lambert72_vers_4258
from utils.logger import get_logger

_logger = get_logger("services.decouverte_service")


@dataclass
class ParcelleTrouvee:
    parcelle: Parcelle
    adresses: List[AdresseBE]  # toutes les adresses liées à cette parcelle
    cote: str  # "pair" / "impair"


def decouvrir_parcelles(
    gemeentenaam: str, straatnaam: str, adressen: AdressenService, cadastre: CadastreService,
) -> List[ParcelleTrouvee]:
    """Découvre et ordonne les parcelles de `straatnaam` (`gemeentenaam`) :
    un côté d'abord (numéros impairs par convention, voir plus bas), puis
    l'autre, chacun trié par numéro croissant — voir le docstring du
    module."""
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

    trouvees: List[ParcelleTrouvee] = []
    for cle, groupe in par_parcelle.items():
        groupe.sort(key=lambda a: a.numero)
        premiere = groupe[0]
        p = _resoudre_geometrie(cle[0], premiere, cadastre)
        if p is None:
            continue
        trouvees.append(ParcelleTrouvee(parcelle=p, adresses=groupe, cote=premiere.parite))

    # Un côté d'abord (impair, convention standard belge/européenne —
    # numéros impairs généralement d'un côté, pairs de l'autre), puis
    # l'autre, chacun par numéro croissant.
    ordre_cote = {"impair": 0, "pair": 1}
    trouvees.sort(key=lambda pt: (ordre_cote.get(pt.cote, 2), pt.adresses[0].numero))
    return trouvees


def _resoudre_geometrie(reference: str, adresse_reference: AdresseBE, cadastre: CadastreService) -> Optional[Parcelle]:
    lon, lat = lambert72_vers_4258(adresse_reference.x, adresse_reference.y)
    return cadastre.get_parcelle(reference, lat, lon)
