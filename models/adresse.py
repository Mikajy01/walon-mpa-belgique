"""Modèle représentant une adresse du Basisregisters Vlaanderen
(Adressenregister) — équivalent du `AdressePoint` français (BAN), mais
avec un lien OFFICIEL vers sa parcelle cadastrale (`capakeys`, résolu via
`/percelen?adresobjectid=`) au lieu d'un simple point à rapprocher d'une
parcelle par distance (voir le plan, "on fait confiance à la parcelle")."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class AdresseBE:
    """Une adresse réelle renvoyée par `api.basisregisters.vlaanderen.be/
    v2/adressen` (liste) puis complétée via `/v2/adressen/{object_id}`
    (détail, position + liens).

    `x`/`y` sont en Lambert 72 (EPSG:31370, confirmé en direct sur
    `adresPositie.geometrie.gml`), PAS lat/lon — contrairement aux
    adresses BAN françaises.

    `postcode` : le VRAI code postal officiel de CETTE adresse précise
    (`postinfo.objectId` de la réponse détail), PAS le code postal donné
    en paramètre du run -- écart réel confirmé le 2026-09-23 (Sint-Truiden) :
    un même lot de rues à traiter peut mélanger plusieurs codes postaux
    réels (3800/3803/3806 selon la rue, communes fusionnées/déelgemeenten),
    jamais un seul code valable pour toute la commune. Vide ("") pour une
    adresse SYNTHÉTIQUE (parcelle sans adresse propre, voir
    `decouverte_service.py`) dont le code n'a pas pu être déterminé --
    l'appelant (main.py) retombe alors sur le code postal du run."""

    object_id: str
    huisnummer: str
    straatnaam: str
    gemeentenaam: str
    x: float
    y: float
    capakeys: List[str] = field(default_factory=list)
    postcode: str = ""

    @property
    def parite(self) -> str:
        """"pair"/"impair" — convention à vérifier sur un cas réel connu
        avant de lui faire confiance comme signal de côté de rue (voir le
        plan, §Vérification 3) ; même principe que côté France
        (`AdressePoint.numero_parite`), mais ici directement fiable
        puisque `huisnummer` est un numéro officiel du registre, pas
        dérivé d'un identifiant composite."""
        chiffres = "".join(c for c in self.huisnummer if c.isdigit())
        if not chiffres:
            return "impair"
        return "pair" if int(chiffres) % 2 == 0 else "impair"

    @property
    def numero(self) -> int:
        """Valeur numérique de `huisnummer` (ignore un éventuel suffixe
        de boîte/lettre), utilisée pour trier dans l'ordre croissant au
        sein d'un même côté — 0 si aucun chiffre n'est trouvé (jamais
        deviné, ce cas doit rester visible plutôt que planter le tri)."""
        chiffres = "".join(c for c in self.huisnummer if c.isdigit())
        return int(chiffres) if chiffres else 0
