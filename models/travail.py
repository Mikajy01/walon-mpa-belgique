"""Une rue précise d'une commune flamande précise à traiter entièrement —
équivalent du `models/travail.py::ElementTravail` français, sans
`departement` (pas de système de département en Belgique/Flandre) ni
`pays` (toujours la Flandre pour ce pipeline)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ElementTravail:
    commune: str  # gemeentenaam
    code_postal: str
    rue: str  # straatnaam
    nis_code: Optional[str] = None  # code commune Vlaanderen, résolu via l'API adressenregister

    @property
    def cle(self) -> tuple[str, str]:
        """Identifiant stable de cet élément de travail, utilisé pour la
        progression au grain (commune, rue) — même principe que côté
        France."""
        return (self.commune, self.rue)
