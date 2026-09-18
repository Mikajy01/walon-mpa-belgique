"""Logique partagée entre `build_onefile.py` et `build_onedir.py` — porté
tel quel depuis walon-map-france/build_common.py (même choix : script
Python plutôt qu'un `.bat` complet, un `.bat` combinant `pip install` +
un appel PyInstaller déclenchait de façon fiable une erreur cmd.exe non
identifiée côté projet wallon).

Les `.bat` (`build-onefile.bat`/`build-onedir.bat`) ne servent qu'à
lancer ce module d'un double-clic, sans aucune autre logique."""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

RACINE = pathlib.Path(__file__).resolve().parent
VENV_DIR = RACINE / ".venv_build"
VENV_PYTHON = VENV_DIR / "Scripts" / "python.exe"


def _executer(commande: list, **kwargs) -> None:
    print("+ " + " ".join(str(c) for c in commande))
    resultat = subprocess.run(commande, cwd=RACINE, **kwargs)
    if resultat.returncode != 0:
        print(f"[ERREUR] Commande échouée (code {resultat.returncode}).")
        sys.exit(1)


def preparer_environnement() -> None:
    """Crée `.venv_build` si absent, installe les dépendances execution +
    build dedans — jamais dans l'environnement Python courant (même
    raison que côté France)."""
    if not VENV_PYTHON.exists():
        print(f"Création d'un environnement virtuel dédié au build — dossier {VENV_DIR.name}...")
        _executer([sys.executable, "-m", "venv", str(VENV_DIR)])

    print("Installation des dépendances (exécution + build) dans l'environnement dédié...")
    _executer([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
    _executer([
        str(VENV_PYTHON), "-m", "pip", "install",
        "-r", "requirements.txt", "-r", "requirements-build.txt",
    ])


def _version_actuelle() -> str:
    config_path = RACINE / "config.py"
    texte = config_path.read_text(encoding="utf-8")
    m = re.search(r'APP_VERSION = "(.*?)"', texte)
    if not m:
        print("[ERREUR] Impossible de lire config.APP_VERSION dans config.py.")
        sys.exit(1)
    return m.group(1)


def resoudre_version(argument: "str | None") -> str:
    """Détermine la version à construire — reprise de config.APP_VERSION,
    en argument (build non interactif) ou saisie interactive (Entrée pour
    garder l'actuelle) — puis met à jour config.py si elle change."""
    version_actuelle = _version_actuelle()

    if argument:
        nouvelle_version = argument
        print(f"Version indiquée en argument : {nouvelle_version}")
    else:
        saisie = input(
            f"Version actuelle : {version_actuelle} — nouvelle version à construire "
            f"(Entrée pour garder) : "
        ).strip()
        nouvelle_version = saisie or version_actuelle

    if nouvelle_version != version_actuelle:
        print(f'Mise à jour de config.py : APP_VERSION = "{nouvelle_version}"...')
        config_path = RACINE / "config.py"
        texte = config_path.read_text(encoding="utf-8")
        texte2, n = re.subn(
            r'APP_VERSION = ".*?"', f'APP_VERSION = "{nouvelle_version}"', texte, count=1,
        )
        assert n == 1
        config_path.write_text(texte2, encoding="utf-8")

    print(f"Version : {nouvelle_version}")
    return nouvelle_version


def construire(mode: str, version: str) -> pathlib.Path:
    """Lance PyInstaller (`mode` = "onefile" ou "onedir") pour cette
    version, nettoie le build précédent du même mode. Renvoie le chemin
    de l'exécutable produit.

    PAS de `--add-data` pour le gabarit officiel -- même convention que
    walon-map-france (config.py : `sys.executable` comme BASE_DIR sous
    PyInstaller, gabarit attendu À CÔTÉ de l'exe, jamais embarqué dedans).
    `templates/gabarit_officiel.xlsx` est donc copié explicitement dans
    `dist_dir` après la construction, comme un vrai fichier livré, PAS
    de l'état régénérable (contrairement à cache/logs/state, créés vides
    au premier lancement par `config.py`)."""
    assert mode in ("onefile", "onedir")

    dist_dir = RACINE / "dist" / mode
    build_dir = RACINE / "build" / mode
    nom = f"WalonMapBelgique-{version}"

    print(f"\nNettoyage du build {mode} précédent...")
    for dossier in (dist_dir, build_dir):
        if dossier.exists():
            shutil.rmtree(dossier)

    print(f"\nConstruction {mode.upper()} (PyInstaller --{mode} --windowed)...")
    _executer([
        str(VENV_DIR / "Scripts" / "pyinstaller.exe"),
        f"--{mode}", "--windowed",
        "--name", nom,
        "--collect-all", "customtkinter",
        "--distpath", str(dist_dir),
        "--workpath", str(build_dir),
        "--specpath", str(build_dir),
        "gui.py",
    ])

    if mode == "onefile":
        exe_dir = dist_dir
        exe_path = dist_dir / f"{nom}.exe"
    else:
        exe_dir = dist_dir / nom
        exe_path = exe_dir / f"{nom}.exe"

    print(f"Copie du gabarit officiel à côté de l'exe ({exe_dir / 'templates'})...")
    dest_templates = exe_dir / "templates"
    dest_templates.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        RACINE / "templates" / "gabarit_officiel.xlsx", dest_templates / "gabarit_officiel.xlsx",
    )

    return exe_path
