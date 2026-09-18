"""Politique de réessai commune pour tous les appels réseau du projet —
porté tel quel depuis project/utils/retry.py (même logique, adaptée aux
exceptions génériques de ce projet plutôt qu'aux erreurs ArcGIS)."""

from __future__ import annotations

import logging

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from services.exceptions import ApiServiceTransientError
from utils.logger import get_logger

_logger = get_logger("utils.retry")


def _est_reessayable(exc: BaseException) -> bool:
    """Erreurs considérées comme temporaires : timeouts, coupures de
    connexion, réponses tronquées en cours de transfert, et erreurs
    serveur 5xx (via `response.raise_for_status()`). Un HTTPError 4xx
    (paramètres invalides, ressource introuvable) est définitif : la
    même requête échouerait de façon identique à chaque tentative,
    réessayer ne fait que perdre du temps sans jamais pouvoir réussir —
    même raisonnement que project/utils/retry.py, confirmé par
    l'incident réel PASH côté wallon."""
    if isinstance(exc, (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
        ApiServiceTransientError,
    )):
        return True
    if isinstance(exc, requests.exceptions.HTTPError):
        status = exc.response.status_code if exc.response is not None else None
        return status is not None and status >= 500
    return False


def http_retry(max_attempts: int = 5):
    """Décorateur de réessai avec backoff exponentiel pour les appels HTTP.

    Ne réessaie que sur des erreurs transitoires ; une erreur applicative
    définitive (JSON invalide, paramètres invalides, HTTPError 4xx)
    n'est jamais masquée par un réessai silencieux."""
    return retry(
        reraise=True,
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        retry=retry_if_exception(_est_reessayable),
        before_sleep=before_sleep_log(_logger, logging.WARNING),
    )
