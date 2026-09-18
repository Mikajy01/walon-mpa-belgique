"""Exceptions partagées entre les services HTTP (qui les lèvent) et
`utils/retry.py` (qui inclut les transitoires dans sa politique de
réessai) — isolées dans ce module pour éviter tout import circulaire,
même raison que project/services/exceptions.py."""

from __future__ import annotations


class ApiServiceError(RuntimeError):
    """Erreur applicative renvoyée par une des APIs publiques françaises
    utilisées (apicarto, Géoportail de l'urbanisme, Géorisques,
    géocodage) — considérée comme définitive par défaut (ex: paramètres
    invalides, HTTP 4xx), jamais réessayée automatiquement."""


class ApiServiceTransientError(ApiServiceError):
    """Variante de `ApiServiceError` pour une erreur considérée comme
    temporaire (ex: HTTP 5xx, service momentanément indisponible) —
    incluse dans `utils.retry._est_reessayable`."""
