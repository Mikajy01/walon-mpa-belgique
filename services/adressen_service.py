"""Accès au Basisregisters Vlaanderen — Adressenregister
(`api.basisregisters.vlaanderen.be/v2`) : liste des adresses d'une rue et
résolution du lien OFFICIEL adresse->parcelle cadastrale (`caPaKey`).

Remplace tout le mécanisme français `geocodage_service.py` +
`traversal_service.py` + seuils de bordure (`_DISTANCE_MAX_BORDURE_
POLYGONE_M`, `marge_adresse_parcelle`) — voir le plan, "on fait confiance
à la parcelle" : ici, le registre lie DIRECTEMENT une adresse à sa
parcelle (pas une simple proximité géométrique à calibrer), donc plus
besoin de deviner "cette parcelle borde-t-elle vraiment cette adresse ?".

PIÈGE RÉEL confirmé en direct pendant la construction de ce module (voir
le plan) : la position d'une adresse (`adresPositie`) est en **Lambert 72
(EPSG:31370)**, PAS en lat/lon comme la BAN française — toute
réutilisation de cette position pour un autre service doit vérifier son
propre système de coordonnées attendu plutôt que supposer WGS84."""

from __future__ import annotations

import re
from typing import List, Optional

import config
from models.adresse import AdresseBE
from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.adressen_service")

_RE_GML_POS = re.compile(r"<gml:pos>\s*([-\d.]+)\s+([-\d.]+)\s*</gml:pos>")


class AdressenService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def lister_adresses(self, gemeentenaam: str, straatnaam: str) -> List[AdresseBE]:
        """Liste TOUTES les adresses d'une rue (commune+nom de rue),
        paginées via le champ `volgende` (lien "page suivante" explicite
        renvoyé par l'API elle-même) — confirmé en direct plus fiable que
        le motif français (`_start`/`_limit` + `totalFeatures`) : pas
        besoin de deviner la fin, la présence/absence de `volgende` le
        dit sans ambiguïté. Boucle tant que `volgende` est présent, par
        prudence (jamais un seul appel non-paginé qui ferait confiance à
        un `limit` par défaut implicite)."""
        url = f"{config.ADRESSENREGISTER_BASE}/adressen"
        params = {"gemeentenaam": gemeentenaam, "straatnaam": straatnaam, "limit": 100}
        adresses: List[AdresseBE] = []
        url_courante: Optional[str] = url
        params_courants: Optional[dict] = params
        while url_courante is not None:
            data = self._http.get_json(url_courante, params_courants, service_key="adressenregister")
            for entree in data.get("adressen", []):
                object_id = entree["identificator"]["objectId"]
                huisnummer = entree.get("huisnummer", "")
                detail = self._detail_adresse(object_id, gemeentenaam, straatnaam, huisnummer)
                if detail is not None:
                    adresses.append(detail)
            volgende = data.get("volgende")
            if volgende:
                url_courante = volgende
                params_courants = None  # déjà inclus dans l'URL "volgende"
            else:
                url_courante = None
        _logger.info(
            "Adressenregister : %d adresse(s) trouvée(s) pour '%s' (%s).",
            len(adresses), straatnaam, gemeentenaam,
        )
        return adresses

    def _detail_adresse(
        self, object_id: str, gemeentenaam: str, straatnaam: str, huisnummer: str,
    ) -> Optional[AdresseBE]:
        """Récupère la position (Lambert 72) d'une adresse puis ses
        parcelles liées. Renvoie `None` (jamais deviné) si la position ou
        le lien parcelle sont absents/inexploitables — journalisé pour
        investigation plutôt que silencieusement ignoré."""
        url = f"{config.ADRESSENREGISTER_BASE}/adressen/{object_id}"
        data = self._http.get_json(url, service_key="adressenregister")
        gml = data.get("adresPositie", {}).get("geometrie", {}).get("gml")
        if not gml:
            _logger.warning("Adresse %s (%s) : pas de position exploitable.", object_id, huisnummer)
            return None
        m = _RE_GML_POS.search(gml)
        if not m:
            _logger.warning("Adresse %s (%s) : position GML non reconnue : %s", object_id, huisnummer, gml[:200])
            return None
        x, y = float(m.group(1)), float(m.group(2))

        capakeys = self._capakeys_pour_adresse(object_id)
        if not capakeys:
            _logger.warning("Adresse %s (%s) : aucune parcelle liée dans le registre.", object_id, huisnummer)

        # Code postal RÉEL de cette adresse précise -- jamais celui donné en
        # paramètre du run (voir AdresseBE.postcode) : un lot de rues d'une
        # même "commune" (au sens administratif large) peut mélanger
        # plusieurs codes postaux réels (déelgemeenten/communes fusionnées).
        postcode = data.get("postinfo", {}).get("objectId", "")
        if not postcode:
            _logger.warning("Adresse %s (%s) : pas de code postal exploitable dans le registre.", object_id, huisnummer)

        return AdresseBE(
            object_id=object_id, huisnummer=huisnummer, straatnaam=straatnaam,
            gemeentenaam=gemeentenaam, x=x, y=y, capakeys=capakeys, postcode=postcode,
        )

    def _capakeys_pour_adresse(self, object_id: str) -> List[str]:
        """Le lien OFFICIEL adresse->parcelle(s) — voir le docstring du
        module. Une adresse peut être liée à plusieurs parcelles (ex. un
        immeuble à cheval sur 2 parcelles) : toutes sont renvoyées, jamais
        une seule devinée arbitrairement."""
        url = f"{config.ADRESSENREGISTER_BASE}/percelen"
        data = self._http.get_json(url, {"adresobjectid": object_id}, service_key="adressenregister")
        return [p["caPaKey"] for p in data.get("percelen", []) if p.get("caPaKey")]
