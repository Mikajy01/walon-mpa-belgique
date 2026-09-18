"""Client HTTP générique, point de passage unique pour tous les appels
réseau du projet — même esprit que
project/services/geoportail_service.py::ArcGISRestClient, mais générique
(pas spécifique à ArcGIS) puisque ce projet interroge 4 familles d'APIs
différentes (apicarto, Géoportail de l'urbanisme, Géorisques,
géocodage/BAN), chacune avec son propre service HTTP mais un seul
mécanisme de cache/débit/réessai partagé.

Les services métier (cadastre_service, urbanisme_service,
georisques_service, geocodage_service, commune_service) ne construisent
jamais de `requests.get(...)` eux-mêmes : ils passent systématiquement
par `HttpClient.get_json`.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import requests

import config
from services.cache_service import HttpCache
from services.exceptions import ApiServiceError, ApiServiceTransientError
from utils.logger import get_logger
from utils.rate_limiter import RateLimiter
from utils.retry import http_retry

_logger = get_logger("services.http_client")


class HttpClient:
    def __init__(
        self,
        cache: HttpCache,
        rate_limiter: RateLimiter,
        timeout: int = 30,
        use_cache: bool = True,
    ) -> None:
        self._cache = cache
        self._rate_limiter = rate_limiter
        self._timeout = timeout
        self._use_cache = use_cache
        self._session = requests.Session()
        # PIÈGE RÉEL confirmé en direct (2026-09-16) : au moins un serveur
        # WFS belge (waterinfo.be) bloque silencieusement (RST TCP, aucune
        # erreur HTTP) le User-Agent par défaut de `requests`/`curl` — un
        # navigateur classique passe sans problème. Défini une fois ici,
        # pour tout le client, plutôt que service par service : un autre
        # host gouvernemental belge pourrait avoir le même filtre.
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        })

    @http_retry()
    def _get_json(self, url: str, params: Dict[str, Any], timeout: Optional[int]) -> Any:
        return self._get_json_impl(url, params, timeout)

    # Tolérance réduite pour un service listé dans
    # config.SERVICE_MAX_ATTEMPTS_OVERRIDES — même motif que côté wallon
    # (voir services/geoportail_service.py::_get_json_tolerance_reduite).
    @http_retry(max_attempts=2)
    def _get_json_tolerance_reduite(self, url: str, params: Dict[str, Any], timeout: Optional[int]) -> Any:
        return self._get_json_impl(url, params, timeout)

    def _get_json_impl(self, url: str, params: Dict[str, Any], timeout: Optional[int]) -> Any:
        if self._use_cache:
            cached = self._cache.get(url, params)
            if cached is not None:
                return cached

        self._rate_limiter.wait()
        delai = timeout if timeout is not None else self._timeout
        _logger.debug("GET %s params=%s", url, params)
        try:
            response = self._session.get(url, params=params, timeout=delai)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            raise
        if response.status_code >= 500:
            raise ApiServiceTransientError(f"{url} -> HTTP {response.status_code}")
        response.raise_for_status()
        if not response.text:
            # Écart réel trouvé en investigation live (Géorisques
            # `/api/v1/rga`, 2026-08-22) : certains endpoints renvoient
            # HTTP 200 avec un corps LITTÉRALEMENT VIDE plutôt qu'un JSON
            # structuré du genre `{}`/`{"data": []}` — leur façon (non
            # standard) de dire "aucune donnée à ce point précis", pas une
            # erreur. `response.json()` plante sur un texte vide
            # (`JSONDecodeError`), ce qui remontait jusqu'à faire échouer
            # TOUT un lot de règles regroupées (voir `resoudre_georisques`)
            # au lieu de laisser la règle concernée traiter normalement
            # une absence de donnée. Traité comme "aucune donnée" (`None`),
            # jamais mis en cache comme un JSON `{}` normal (une future
            # réponse non-vide au même point doit toujours écraser le
            # cache, jamais rester bloquée sur ce repli).
            return None
        data = response.json()

        if self._use_cache:
            self._cache.set(url, params, data)
        return data

    def get_json(
        self, url: str, params: Optional[Dict[str, Any]] = None, *, service_key: Optional[str] = None,
    ) -> Any:
        """Requête GET avec cache/débit/réessai. `service_key` sélectionne
        une tolérance réduite (timeout plus court, moins de tentatives)
        via `config.SERVICE_TIMEOUT_SECONDS_OVERRIDES`/
        `SERVICE_MAX_ATTEMPTS_OVERRIDES` si le service concerné se montre
        peu fiable — même mécanisme que côté wallon (incident réel PASH),
        vide par défaut pour ce projet tant qu'aucun service français
        utilisé ne s'est montré instable de la même façon."""
        params = params or {}
        timeout_reduit = config.SERVICE_TIMEOUT_SECONDS_OVERRIDES.get(service_key) if service_key else None
        get_fn = (
            self._get_json_tolerance_reduite
            if service_key in config.SERVICE_MAX_ATTEMPTS_OVERRIDES
            else self._get_json
        )
        return get_fn(url, params, timeout_reduit)

    @http_retry()
    def _get_text(self, url: str, params: Dict[str, Any], timeout: Optional[int]) -> str:
        return self._get_text_impl(url, params, timeout)

    def _get_text_impl(self, url: str, params: Dict[str, Any], timeout: Optional[int]) -> str:
        """Comme `_get_json_impl` mais renvoie le corps BRUT (`response.
        text`) sans tenter `response.json()` — nécessaire pour les
        services WFS (XML/GML), voir `services/wfs_georisques_service.py`.
        Le cache accepte une chaîne directement (`json.dumps` d'une
        chaîne reste une chaîne après `json.loads`), aucun changement
        nécessaire côté `HttpCache`."""
        if self._use_cache:
            cached = self._cache.get(url, params)
            if cached is not None:
                return cached

        self._rate_limiter.wait()
        delai = timeout if timeout is not None else self._timeout
        _logger.debug("GET (texte brut) %s params=%s", url, params)
        response = self._session.get(url, params=params, timeout=delai)
        if response.status_code >= 500:
            raise ApiServiceTransientError(f"{url} -> HTTP {response.status_code}")
        response.raise_for_status()
        texte = response.text

        if self._use_cache:
            self._cache.set(url, params, texte)
        return texte

    def get_text(
        self, url: str, params: Optional[Dict[str, Any]] = None, *, service_key: Optional[str] = None,
    ) -> str:
        """Variante texte brut de `get_json` — mêmes garanties
        (cache/débit/réessai), pas de tolérance réduite spécifique pour
        l'instant (aucun service WFS testé ne s'est montré peu fiable)."""
        params = params or {}
        return self._get_text(url, params, None)
