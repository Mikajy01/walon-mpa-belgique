# Walon-Map Flandre — GUI (desktop)

Interface graphique locale (CustomTkinter), même architecture que
`walon-map-france/gui.py` : le pipeline complet (`services/`, `models/`,
`utils/`, `main.py`) tourne **en local** sur la machine de
l'utilisateur — pas de dépendance à GitHub Actions pour cette version.
Voir la conversation du 2026-09-18 pour la justification du choix
"tout en local" plutôt qu'un simple déclencheur de workflow distant.

C'est une **copie locale** du code de `cloud/` (services WFS, résolveur,
écriture Excel), pas un lien vers l'autre dépôt -- toute correction
faite côté `cloud/` doit être reportée ici manuellement pour rester
synchronisée (limite connue, pas de mécanisme de partage automatique
entre les deux dépôts pour l'instant).

## Lancer

```
pip install -r requirements.txt
python gui.py
```

## Onglets

- **Traitement** : commune, code postal, liste de rues (une par ligne,
  toujours obligatoire), budget de temps interne, logs détaillés.
- **Vérifier des rues** : vérifie une liste de rues contre le vrai
  registre officiel (Basisregisters Vlaanderen) avant de lancer un
  traitement -- construit après un écart réel trouvé en session
  ("Nachtegalens" demandé, "Nachtegalenstraat" le vrai nom, zéro
  adresse trouvée silencieusement).
- **Journal** : suivi en direct du traitement (barres de progression
  textuelles incluses).

Pas d'onglet "Registre de colonnes" (contrairement à la France) : le
gabarit belge a un mapping FIXE colonne→rôle
(`models/colonnes_be.py`), aucune icône ancrée à classifier à la main.

## État

MVP fonctionnel (testé en direct, lancement + import sans erreur).
Reste à faire : empaquetage PyInstaller (voir `walon-map-france/
build_onefile.py`/`build_onedir.py` comme référence), pas encore fait.
