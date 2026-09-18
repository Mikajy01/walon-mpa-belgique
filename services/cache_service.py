"""Cache local des requêtes HTTP (SQLite), pour la performance et pour
éviter des appels réseau redondants au sein d'un même run et entre deux
runs — même motif de verrouillage que
project/services/cache_service.py::CacheService.

Ne contient PAS de suivi de progression métier (pas de `ProgressStore`) :
décision utilisateur explicite (voir le plan, §"Stockage de progression —
RÉVISÉ") — l'Excel fourni par l'utilisateur à chaque run EST la seule
source de vérité sur ce qui est déjà traité, jamais une base séparée.
Voir `services/excel_service.py::lire_identifiants_deja_ecrits` pour la
façon dont la déduplication se fait à la place, en relisant l'Excel
lui-même."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

from utils.logger import get_logger

_logger = get_logger("services.cache_service")


class HttpCache:
    """Cache brut des réponses HTTP, clé par (url, params)."""

    def __init__(self, cache_dir: Path) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = cache_dir / "http_cache.sqlite3"
        self._lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, timeout=30)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_cache (
                    cache_key TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    params TEXT NOT NULL,
                    response TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.commit()

    @staticmethod
    def make_key(url: str, params: dict) -> str:
        payload = json.dumps({"url": url, "params": params}, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, url: str, params: dict) -> Optional[Any]:
        key = self.make_key(url, params)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT response FROM http_cache WHERE cache_key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        _logger.debug("Cache HIT (%s) url=%s params=%s", key[:12], url, params)
        return json.loads(row[0])

    def set(self, url: str, params: dict, response: Any) -> None:
        key = self.make_key(url, params)
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO http_cache (cache_key, url, params, response) "
                "VALUES (?, ?, ?, ?)",
                (key, url, json.dumps(params, default=str), json.dumps(response)),
            )
            conn.commit()
        _logger.debug("Cache SET (%s) url=%s params=%s", key[:12], url, params)
