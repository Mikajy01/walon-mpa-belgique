"""Accès aux couches WFS "sol" (DOV — Databank Ondergrond Vlaanderen).
Ce host partage la même infrastructure que `mercator.vlaanderen.be`
(même panne, même retour en ligne le 2026-09-16 — confirmé via DNS,
IP identique).

`erosie:so_potbdmerosiepp_2026` (Potentiële bodemerosiekaart per
perceel, `www.dov.vlaanderen.be/geoserver/erosie/wfs`) — champ
`totale_erosie` confirmé en direct (valeurs réelles observées : "zeer
laag", "laag", "medium", "hoog", "verwaarloosbaar", "niet van
toepassing") → colonne EL, existence seule (pas de palier Excel à
distinguer pour cette colonne précise, contrairement à EC→EH qui sont
un jeu de données DIFFÉRENT — "Afstromingskaart" — pas encore trouvé).
ATTENTION non résolue : cette couche a une couverture QUASI TOTALE de la
Flandre (606123 entités vues en direct), y compris pour des points dont
`totale_erosie` vaut littéralement "niet van toepassing" -- `_existe()`
ne regarde QUE `numberReturned` (jamais la valeur du champ), donc EL
risque de sortir "O" même là où `totale_erosie` dit explicitement "non
applicable". Jamais vérifié si le gabarit veut vraiment "une entité
existe dans ce jeu de données" (répond alors quasi toujours "O") ou "le
champ dit autre chose que 'niet van toepassing'" -- à trancher avant de
faire confiance aveuglément à cette colonne, PAS corrigé ici (hors
périmètre de l'échange du 2026-09-19 qui a mené à cette découverte,
concernait uniquement EI).

**Millésimes** -- ces deux couches DOV sont mises à jour ANNUELLEMENT,
avec un changement de convention de nommage en cours de route :
descriptif long jusqu'en 2014 (`erosie_andere_erosiegerelateerde_
gronden_2014`, `erosie_potentiele_bodemerosiekaart_per_perceel_2014`),
puis code court `so_a_grndn_AAAA`/`so_potbdmerosiepp_AAAA` à partir de
2015. La recherche initiale (voir l'ancien commentaire "millésime 2014,
le plus récent trouvé") n'avait manifestement trouvé QUE l'ancienne
convention de nommage et s'était arrêtée là -- écart réel trouvé en
investigation live le 2026-09-19 (l'utilisateur ne retrouvait pas
"Andere erosiegerelateerde gronden (2014)" sur geopunt.be, qui n'expose
que le millésime COURANT) : `GetCapabilities` liste en fait des
millésimes annuels jusqu'à 2023 (EI, `so_a_grndn_2023`, 176 entités
vues en direct sur toute la Flandre) et 2026 (EL, `so_potbdmerosiepp_
2026`, confirmé en direct, champ `totale_erosie` identique). Toujours
utiliser le millésime le PLUS RÉCENT disponible (revérifier `Get
Capabilities` périodiquement -- ces jeux de données changent chaque
année) plutôt que de se fier à un nom figé une fois pour toutes."""

from __future__ import annotations

import re
from typing import Optional

import requests

from services.exceptions import ApiServiceError

from services.http_client import HttpClient
from utils.logger import get_logger

_logger = get_logger("services.wfs_bodem_service")

_EROSIE_WFS_BASE = "https://www.dov.vlaanderen.be/geoserver/erosie/wfs"


class WfsBodemService:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    @staticmethod
    def _bbox(x: float, y: float, marge_m: float = 5.0) -> str:
        return f"{x - marge_m},{y - marge_m},{x + marge_m},{y + marge_m},urn:ogc:def:crs:EPSG::31370"

    def _existe(self, typename: str, x: float, y: float) -> Optional[str]:
        params = {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"erosie:{typename}", "count": 1,
            "BBOX": self._bbox(x, y),
        }
        try:
            xml = self._http.get_text(_EROSIE_WFS_BASE, params, service_key="bodem_be")
        except (requests.exceptions.RequestException, ApiServiceError):
            # Erreur reseau/API (jamais une reponse HTTP valide sans resultat) -- NE JAMAIS
            # avaler ici en None/"N" : doit remonter jusqu'au resolveur pour etre marquee
            # "ERREUR" et retentee au run suivant (voir resolveur_service.py, decision du
            # 2026-09-19 -- une degradation silencieuse en "N" rendait ces cellules fausses
            # de facon PERMANENTE, jamais retentees une fois la ligne ecrite).
            raise
        except Exception as exc:  # noqa: BLE001 — une couche indisponible ne doit jamais faire échouer tout le traitement de la parcelle
            _logger.warning("Couche 'erosie:%s' indisponible (x=%s, y=%s) : %s", typename, x, y, exc)
            return None
        m = re.search(r'numberReturned="(\d+)"', xml)
        if not m:
            return None
        return "O" if int(m.group(1)) > 0 else "N"

    def potentiele_bodemerosie(self, x: float, y: float) -> Optional[str]:
        """"O"/"N" — colonne EL. Millésime 2026 (le plus récent
        disponible au 2026-09-19, voir le docstring du module)."""
        return self._existe("so_potbdmerosiepp_2026", x, y)

    def andere_erosiegerelateerde_gronden(self, x: float, y: float) -> Optional[str]:
        """"O"/"N" — colonne EI. Millésime 2023 (le plus récent
        disponible au 2026-09-19, voir le docstring du module -- corrige
        un ancien choix bloqué sur 2014, la recherche initiale n'ayant
        pas trouvé le changement de convention de nommage)."""
        return self._existe("so_a_grndn_2023", x, y)
