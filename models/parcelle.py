"""Modèle représentant une parcelle cadastrale belge (WFS fédéral,
`cp:CadastralParcel`) — équivalent du `Parcelle` français, identifiée par
son `caPaKey` (référence officielle, ex. `"71053H1051/00N000"`, même
convention commune+section+parcelle+subdivision que la référence
nationale renvoyée par le WFS fédéral)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class Parcelle:
    reference: str  # caPaKey / référence nationale
    geometry: Optional[Dict] = None  # GeoJSON, EPSG:4258
    area: Optional[float] = None
