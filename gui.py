"""Interface graphique (CustomTkinter) — même motif que
walon-map-france/gui.py : thread d'arrière-plan pour le traitement
réseau, pont par `queue.Queue` + `self.after(100, poll)` vers le thread
Tk principal, aucune logique métier dupliquée ici (tout passe par
`main.py`/`services/`, copie locale de `cloud/` -- voir la conversation
du 2026-09-18 pour la justification de cette architecture "tout en
local", comme côté France, plutôt qu'un simple déclencheur de GitHub
Actions).

Onglets : Traitement (saisie manuelle commune/rues), Vérifier des rues
(contre le vrai registre Basisregisters Vlaanderen, avant de lancer un
traitement -- construit après l'écart réel "Nachtegalens" vs
"Nachtegalenstraat"), Journal.

PAS d'onglet "Registre de colonnes" (contrairement à la France) : le
gabarit belge a un mapping FIXE colonne->rôle (`models/colonnes_be.py`),
aucune icône ancrée à classifier à la main.

Lancement : `python gui.py`
"""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import List, Optional

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

import customtkinter as ctk

import config
from main import executer_traitement, verifier_rues
from services.cache_service import HttpCache
from services.http_client import HttpClient
from utils.logger import get_logger, setup_logging
from utils.rate_limiter import RateLimiter

APP_TITLE = f"Walon-Map Belgique — Remplissage automatique (v{config.APP_VERSION})"
_logger = get_logger("gui")


class _QueueLogHandler(logging.Handler):
    """Même motif que walon-map-france/gui.py::_QueueLogHandler --
    pousse chaque ligne de log (y compris les barres de progression
    textuelles de `main.py::_log_progres`) dans la queue, lue par le
    thread Tk principal via `_poll_queue`."""

    def __init__(self, message_queue: "queue.Queue") -> None:
        super().__init__()
        self._queue = message_queue
        self.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put(("log", self.format(record)))


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("820x680")
        self.minsize(720, 560)

        self._message_queue: "queue.Queue" = queue.Queue()
        self._en_cours = False

        self._construire_interface()
        self._brancher_logging()
        self.after(100, self._poll_queue)

    # -- Construction de l'interface ------------------------------------

    def _construire_interface(self) -> None:
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self.onglet_traitement = self.tabview.add("Traitement")
        self.onglet_verifier_rues = self.tabview.add("Vérifier des rues")
        self.onglet_journal = self.tabview.add("Journal")

        self._construire_onglet_traitement()
        self._construire_onglet_verifier_rues()
        self._construire_onglet_journal()

    def _construire_onglet_traitement(self) -> None:
        onglet = self.onglet_traitement

        cadre_lieu = ctk.CTkFrame(onglet)
        cadre_lieu.pack(fill="x", padx=10, pady=(10, 5))
        ctk.CTkLabel(cadre_lieu, text="Commune :").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_commune = ctk.CTkEntry(cadre_lieu, width=200)
        self.entry_commune.grid(row=0, column=1, padx=5, pady=5)
        ctk.CTkLabel(cadre_lieu, text="Code postal :").grid(row=0, column=2, sticky="w", padx=5, pady=5)
        self.entry_code_postal = ctk.CTkEntry(cadre_lieu, width=100)
        self.entry_code_postal.grid(row=0, column=3, padx=5, pady=5)
        ctk.CTkLabel(cadre_lieu, text="Budget (h) :").grid(row=0, column=4, sticky="w", padx=5, pady=5)
        self.entry_budget_heures = ctk.CTkEntry(cadre_lieu, width=60)
        self.entry_budget_heures.insert(0, "5.5")
        self.entry_budget_heures.grid(row=0, column=5, padx=5, pady=5)
        self.checkbox_debug = ctk.CTkCheckBox(cadre_lieu, text="Logs détaillés (debug)")
        self.checkbox_debug.grid(row=0, column=6, padx=(15, 5), pady=5)

        cadre_sortie = ctk.CTkFrame(onglet)
        cadre_sortie.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(cadre_sortie, text="Dossier de sortie :").pack(side="left", padx=5, pady=5)
        self.entry_dossier_sortie = ctk.CTkEntry(cadre_sortie)
        self.entry_dossier_sortie.insert(0, str(config.STATE_DIR))
        self.entry_dossier_sortie.pack(side="left", fill="x", expand=True, padx=5, pady=5)
        ctk.CTkButton(
            cadre_sortie, text="Parcourir…", width=100, command=self._choisir_dossier_sortie,
        ).pack(side="left", padx=5, pady=5)
        self.checkbox_geometrie = ctk.CTkCheckBox(
            onglet,
            text="Découverte géométrique : ajouter aussi les parcelles qui bordent la rue SANS adresse "
            "(champs, digues, rues sans numéros)",
        )
        self.checkbox_geometrie.select()
        self.checkbox_geometrie.pack(anchor="w", padx=10, pady=(0, 2))
        cadre_rayon = ctk.CTkFrame(onglet, fg_color="transparent")
        cadre_rayon.pack(anchor="w", padx=10, pady=(0, 5))
        ctk.CTkLabel(cadre_rayon, text="Rayon autour de la route (m) :").pack(side="left", padx=(20, 5))
        self.entry_rayon_geometrique = ctk.CTkEntry(cadre_rayon, width=60)
        self.entry_rayon_geometrique.insert(0, "10")
        self.entry_rayon_geometrique.pack(side="left")
        ctk.CTkLabel(
            cadre_rayon, text="10 = parcelles riveraines ; 50 = jusqu'à 50 m (champs derrière la route)",
            text_color=("gray30", "gray70"),
        ).pack(side="left", padx=10)
        ctk.CTkLabel(
            onglet,
            text="Le nom du fichier Excel est calculé automatiquement (code postal + commune) et créé dans ce "
            "dossier -- s'il existe déjà, le traitement continue dedans (les lignes déjà écrites sont sautées) "
            "plutôt que de l'écraser.",
            text_color=("gray30", "gray70"), wraplength=700, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 5))

        ctk.CTkLabel(
            onglet, text="Rues à traiter (une par ligne, TOUJOURS obligatoire -- pas de découverte "
            "automatique de toute la commune) :",
        ).pack(anchor="w", padx=10, pady=(10, 0))
        self.zone_rues = ctk.CTkTextbox(onglet, height=140)
        self.zone_rues.pack(fill="x", padx=10, pady=5)

        ctk.CTkLabel(
            onglet, text="Astuce : vérifie d'abord l'orthographe exacte des rues dans l'onglet "
            "\"Vérifier des rues\" -- une rue mal orthographiée renvoie silencieusement 0 adresse.",
            text_color=("gray30", "gray70"), wraplength=700, justify="left",
        ).pack(anchor="w", padx=10, pady=(0, 5))

        cadre_actions = ctk.CTkFrame(onglet)
        cadre_actions.pack(fill="x", padx=10, pady=10)
        self.bouton_lancer = ctk.CTkButton(
            cadre_actions, text="Démarrer le traitement", font=ctk.CTkFont(size=14, weight="bold"),
            command=self._lancer_traitement,
        )
        self.bouton_lancer.pack(side="left", padx=5)
        self.barre_progression = ctk.CTkProgressBar(cadre_actions, mode="indeterminate")
        self.barre_progression.pack(side="left", fill="x", expand=True, padx=10)

        self.label_statut = ctk.CTkLabel(onglet, text="Prêt. Le détail de la progression s'affiche dans l'onglet Journal.")
        self.label_statut.pack(anchor="w", padx=10, pady=(0, 10))

    def _construire_onglet_verifier_rues(self) -> None:
        onglet = self.onglet_verifier_rues

        ctk.CTkLabel(
            onglet,
            text="Vérifie une liste de rues contre le vrai registre officiel (Basisregisters Vlaanderen) "
                 "d'une commune, avant de lancer un traitement -- plus besoin de demander une par une si "
                 "une rue existe, ni de deviner l'orthographe exacte.",
            wraplength=700, justify="left",
        ).pack(anchor="w", padx=10, pady=(10, 5))

        cadre_lieu = ctk.CTkFrame(onglet)
        cadre_lieu.pack(fill="x", padx=10, pady=5)
        ctk.CTkLabel(cadre_lieu, text="Commune :").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.entry_verif_commune = ctk.CTkEntry(cadre_lieu, width=200)
        self.entry_verif_commune.grid(row=0, column=1, padx=5, pady=5)

        ctk.CTkLabel(onglet, text="Rues à vérifier (une par ligne) :").pack(anchor="w", padx=10, pady=(10, 0))
        self.zone_verif_rues = ctk.CTkTextbox(onglet, height=100)
        self.zone_verif_rues.pack(fill="x", padx=10, pady=5)

        self.bouton_verifier = ctk.CTkButton(onglet, text="Vérifier", command=self._lancer_verification_rues)
        self.bouton_verifier.pack(anchor="w", padx=10, pady=5)

        ctk.CTkLabel(onglet, text="Résultat :", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=10, pady=(10, 0))
        self.cadre_resultats_verif = ctk.CTkScrollableFrame(onglet, height=280)
        self.cadre_resultats_verif.pack(fill="both", expand=True, padx=10, pady=(0, 10))

    def _construire_onglet_journal(self) -> None:
        onglet = self.onglet_journal
        self.zone_log = ctk.CTkTextbox(onglet, state="disabled", font=ctk.CTkFont(family="Consolas", size=11))
        self.zone_log.pack(fill="both", expand=True, padx=10, pady=10)

    # -- Logging / pont thread -> Tk -------------------------------------

    def _brancher_logging(self) -> None:
        setup_logging(config.BASE_DIR / "logs", debug=False)
        handler = _QueueLogHandler(self._message_queue)
        logging.getLogger().addHandler(handler)

    def _poll_queue(self) -> None:
        try:
            while True:
                message = self._message_queue.get_nowait()
                self._traiter_message(message)
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _traiter_message(self, message: tuple) -> None:
        kind = message[0]
        if kind == "log":
            self._ajouter_log(message[1])
        elif kind == "done":
            self._fin_traitement(succes=True, code_retour=message[1])
        elif kind == "error":
            self._fin_traitement(succes=False, erreur=message[1])
        elif kind == "verif_rues_done":
            self._afficher_resultats_verif_rues(message[1])
        elif kind == "verif_rues_error":
            self._afficher_erreur_verif_rues(message[1])

    def _ajouter_log(self, ligne: str) -> None:
        self.zone_log.configure(state="normal")
        self.zone_log.insert("end", ligne + "\n")
        self.zone_log.see("end")
        self.zone_log.configure(state="disabled")

    # -- Onglet Traitement ------------------------------------------------

    def _choisir_dossier_sortie(self) -> None:
        dossier = filedialog.askdirectory(
            initialdir=self.entry_dossier_sortie.get().strip() or str(config.STATE_DIR),
            title="Choisir le dossier de sortie",
        )
        if dossier:
            self.entry_dossier_sortie.delete(0, "end")
            self.entry_dossier_sortie.insert(0, dossier)

    def _valider_formulaire(self) -> Optional[dict]:
        commune = self.entry_commune.get().strip()
        code_postal = self.entry_code_postal.get().strip()
        rues_brut = self.zone_rues.get("1.0", "end").strip()
        budget_str = self.entry_budget_heures.get().strip()
        dossier_sortie = self.entry_dossier_sortie.get().strip() or str(config.STATE_DIR)

        if not commune or not code_postal:
            messagebox.showerror(APP_TITLE, "Commune et code postal sont obligatoires.")
            return None
        if not rues_brut:
            messagebox.showerror(APP_TITLE, "Indiquez au moins une rue à traiter (une par ligne).")
            return None
        try:
            Path(dossier_sortie).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror(APP_TITLE, f"Dossier de sortie invalide : {exc}")
            return None
        try:
            rayon_geometrique_m = float(self.entry_rayon_geometrique.get().strip() or "10")
            if rayon_geometrique_m <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror(APP_TITLE, "Le rayon autour de la route doit être un nombre de mètres > 0.")
            return None
        try:
            budget_heures = float(budget_str) if budget_str else 5.5
        except ValueError:
            messagebox.showerror(APP_TITLE, "Le budget de temps doit être un nombre (heures).")
            return None
        if budget_heures > 5.5:
            # Même garde-fou que traiter_commune.yml (timeout-minutes 6h,
            # marge de 30 min) -- pas de GitHub Actions ici, mais la
            # même limite reste une valeur sûre par défaut pour éviter
            # un run qui tourne indéfiniment sans jamais se sauvegarder
            # proprement à temps.
            if not messagebox.askyesno(
                APP_TITLE,
                f"Budget de {budget_heures}h supérieur à la valeur recommandée (5.5h). Continuer quand même ?",
            ):
                return None

        return {
            "commune": commune, "code_postal": code_postal,
            "rues": ",".join(l.strip() for l in rues_brut.splitlines() if l.strip()),
            "debug": bool(self.checkbox_debug.get()),
            "budget_heures": budget_heures,
            "state_dir": dossier_sortie,
            "decouverte_geometrique": bool(self.checkbox_geometrie.get()),
            "rayon_geometrique_m": rayon_geometrique_m,
        }

    def _lancer_traitement(self) -> None:
        if self._en_cours:
            return
        parametres = self._valider_formulaire()
        if parametres is None:
            return

        self._en_cours = True
        self._dernier_dossier_sortie = parametres["state_dir"]
        self.bouton_lancer.configure(state="disabled", text="Traitement en cours…")
        self.bouton_verifier.configure(state="disabled")
        self.barre_progression.start()
        self.label_statut.configure(text="Démarrage… voir l'onglet Journal pour le détail.")
        self.tabview.set("Journal")

        thread = threading.Thread(target=self._executer_traitement, args=(parametres,), daemon=True)
        thread.start()

    def _executer_traitement(self, parametres: dict) -> None:
        """Tourne dans un thread d'arrière-plan -- aucun accès direct aux
        widgets ici, uniquement via `self._message_queue` (voir
        `_poll_queue`), même motif que walon-map-france/gui.py::_executer."""
        try:
            code_retour = executer_traitement(
                commune=parametres["commune"], code_postal=parametres["code_postal"],
                rues=parametres["rues"], template=str(config.TEMPLATE_PATH),
                state_dir=parametres["state_dir"], cache_dir=str(config.CACHE_DIR),
                logs_dir=str(config.BASE_DIR / "logs"), debug=parametres["debug"],
                budget_heures=parametres["budget_heures"],
                decouverte_geometrique=parametres["decouverte_geometrique"],
                rayon_geometrique_m=parametres["rayon_geometrique_m"],
            )
            self._message_queue.put(("done", code_retour))
        except Exception as exc:  # noqa: BLE001 -- remonté proprement au thread Tk, jamais un crash silencieux
            _logger.exception("Erreur pendant le traitement.")
            self._message_queue.put(("error", str(exc)))

    def _fin_traitement(self, *, succes: bool, code_retour: int = 0, erreur: str = "") -> None:
        self._en_cours = False
        self.bouton_lancer.configure(state="normal", text="Démarrer le traitement")
        self.bouton_verifier.configure(state="normal")
        self.barre_progression.stop()
        self.barre_progression.set(0)
        if not succes:
            self.label_statut.configure(text=f"Erreur : {erreur}")
            messagebox.showerror(APP_TITLE, f"Le traitement a échoué :\n{erreur}")
        elif code_retour == 75:
            self.label_statut.configure(text="Arrêté proprement (budget de temps atteint) -- relance pour reprendre.")
            messagebox.showinfo(
                APP_TITLE,
                "Le budget de temps a été atteint avant la fin -- rien n'est perdu (chaque parcelle est "
                "sauvegardée dès qu'elle est résolue). Relance le traitement avec les mêmes paramètres pour "
                "reprendre là où ça s'est arrêté.",
            )
        else:
            self.label_statut.configure(text="Traitement terminé.")
            messagebox.showinfo(
                APP_TITLE, f"Traitement terminé -- voir le fichier dans :\n{self._dernier_dossier_sortie}",
            )

    # -- Onglet Vérifier des rues -----------------------------------------

    def _lancer_verification_rues(self) -> None:
        if self._en_cours:
            return
        commune = self.entry_verif_commune.get().strip()
        rues_brut = self.zone_verif_rues.get("1.0", "end").strip()
        if not commune:
            messagebox.showerror(APP_TITLE, "Indiquez une commune.")
            return
        rues = [l.strip() for l in rues_brut.splitlines() if l.strip()]
        if not rues:
            messagebox.showerror(APP_TITLE, "Indiquez au moins une rue à vérifier (une par ligne).")
            return

        for widget in self.cadre_resultats_verif.winfo_children():
            widget.destroy()
        self.bouton_verifier.configure(state="disabled", text="Vérification…")

        thread = threading.Thread(target=self._executer_verification_rues, args=(commune, rues), daemon=True)
        thread.start()

    def _executer_verification_rues(self, commune: str, rues: List[str]) -> None:
        try:
            cache = HttpCache(config.CACHE_DIR)
            rate_limiter = RateLimiter(config.MAX_REQUESTS_PER_SECOND)
            http = HttpClient(cache, rate_limiter, timeout=config.HTTP_TIMEOUT_SECONDS)
            resultats = verifier_rues(commune, rues, http)
            self._message_queue.put(("verif_rues_done", resultats))
        except Exception as exc:  # noqa: BLE001
            _logger.exception("Erreur pendant la vérification des rues.")
            self._message_queue.put(("verif_rues_error", str(exc)))

    def _afficher_resultats_verif_rues(self, resultats: List[tuple]) -> None:
        self.bouton_verifier.configure(state="normal", text="Vérifier")
        for demandee, exact, suggestions in resultats:
            ligne = ctk.CTkFrame(self.cadre_resultats_verif)
            ligne.pack(fill="x", pady=2)
            if exact:
                couleur = ("green4", "green3")
                texte = f"✓ \"{demandee}\" -- trouvée dans le registre" + (
                    f" sous \"{exact}\"" if exact != demandee else ""
                )
            else:
                couleur = ("red3", "red2")
                suggestions_txt = ", ".join(f'"{s}"' for s in suggestions) if suggestions else "aucune suggestion proche"
                texte = f"✗ \"{demandee}\" -- INTROUVABLE. Suggestions : {suggestions_txt}"
            ctk.CTkLabel(ligne, text=texte, text_color=couleur, wraplength=650, justify="left", anchor="w").pack(
                fill="x", padx=5, pady=3,
            )

    def _afficher_erreur_verif_rues(self, erreur: str) -> None:
        self.bouton_verifier.configure(state="normal", text="Vérifier")
        messagebox.showerror(APP_TITLE, f"Erreur pendant la vérification :\n{erreur}")


if __name__ == "__main__":
    ctk.set_appearance_mode("system")
    ctk.set_default_color_theme("blue")
    app = App()
    app.mainloop()
