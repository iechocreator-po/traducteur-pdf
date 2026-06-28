"""
Traducteur PDF via Ollama
==========================
Interface graphique simple pour traduire un document PDF (anglais -> français)
en utilisant un modèle Ollama local, en découpant le texte en morceaux pour
respecter la fenêtre de contexte du modèle.

Prérequis :
    - Ollama installé et lancé (https://ollama.com)
    - Un modèle téléchargé, ex: ollama pull llama3.1
    - pip install pdfplumber requests --break-system-packages

Utilisation :
    python3 traducteur_pdf_ollama.py
"""

import json
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

import pdfplumber
import requests

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "llama3.1"
CHUNK_SIZE_CHARS = 3000  # ~ une page environ ; ajuster selon ta RAM/modèle


# ----------------------------------------------------------------------------
# Logique métier
# ----------------------------------------------------------------------------
def extraire_texte_pdf(chemin_pdf: str) -> str:
    """Extrait tout le texte d'un PDF, page par page."""
    texte_complet = []
    with pdfplumber.open(chemin_pdf) as pdf:
        for page in pdf.pages:
            texte_page = page.extract_text() or ""
            texte_complet.append(texte_page)
    return "\n\n".join(texte_complet)


def decouper_en_chunks(texte: str, taille_max: int = CHUNK_SIZE_CHARS) -> list[str]:
    """
    Découpe le texte en morceaux d'une taille raisonnable, en essayant de
    couper sur des paragraphes plutôt qu'en plein milieu d'une phrase.
    """
    paragraphes = texte.split("\n\n")
    chunks = []
    chunk_actuel = ""

    for paragraphe in paragraphes:
        if len(chunk_actuel) + len(paragraphe) > taille_max and chunk_actuel:
            chunks.append(chunk_actuel.strip())
            chunk_actuel = paragraphe
        else:
            chunk_actuel += "\n\n" + paragraphe if chunk_actuel else paragraphe

    if chunk_actuel.strip():
        chunks.append(chunk_actuel.strip())

    return chunks


def traduire_chunk(texte: str, modele: str, langue_source: str, langue_cible: str) -> str:
    """Envoie un chunk de texte à Ollama et retourne la traduction."""
    prompt = (
        f"Traduis le texte suivant de {langue_source} vers {langue_cible}. "
        f"Traduis INTÉGRALEMENT, mot à mot, sans rien résumer, sans rien omettre, "
        f"sans ajouter de commentaire ni d'introduction. "
        f"Donne uniquement la traduction, rien d'autre.\n\n"
        f"Texte à traduire :\n{texte}"
    )

    reponse = requests.post(
        OLLAMA_URL,
        json={
            "model": modele,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
            },
        },
        timeout=300,
    )
    reponse.raise_for_status()
    data = reponse.json()
    return data.get("response", "").strip()


def lister_modeles_ollama() -> list[str]:
    """Récupère la liste des modèles installés localement via l'API Ollama."""
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        r.raise_for_status()
        data = r.json()
        return [m["name"] for m in data.get("models", [])]
    except Exception:
        return [DEFAULT_MODEL]


# ----------------------------------------------------------------------------
# Interface graphique
# ----------------------------------------------------------------------------
class TraducteurApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Traducteur PDF via Ollama v3")
        self.root.geometry("700x600")

        self.chemin_pdf = tk.StringVar()
        self.chemin_sortie = tk.StringVar()
        self.modele_selectionne = tk.StringVar(value=DEFAULT_MODEL)
        self.langue_source = tk.StringVar(value="anglais")
        self.langue_cible = tk.StringVar(value="français")

        self._construire_interface()
        self._rafraichir_modeles()

    def _construire_interface(self):
        padding = {"padx": 10, "pady": 5}

        # --- Sélection du fichier ---
        frame_fichier = ttk.LabelFrame(self.root, text="1. Document à traduire")
        frame_fichier.pack(fill="x", **padding)

        ttk.Entry(frame_fichier, textvariable=self.chemin_pdf, state="readonly").pack(
            side="left", fill="x", expand=True, padx=5, pady=5
        )
        ttk.Button(frame_fichier, text="Choisir un PDF...", command=self._choisir_fichier).pack(
            side="left", padx=5, pady=5
        )

        # --- Fichier de sortie (auto, modifiable) ---
        frame_sortie = ttk.LabelFrame(self.root, text="2. Fichier de sortie (.txt)")
        frame_sortie.pack(fill="x", padx=10, pady=5)

        ttk.Entry(frame_sortie, textvariable=self.chemin_sortie).pack(
            side="left", fill="x", expand=True, padx=5, pady=5
        )
        ttk.Button(frame_sortie, text="Changer...", command=self._choisir_sortie).pack(
            side="left", padx=5, pady=5
        )

        # --- Options de traduction ---
        frame_options = ttk.LabelFrame(self.root, text="3. Options")
        frame_options.pack(fill="x", **padding)

        ttk.Label(frame_options, text="Modèle Ollama :").grid(row=0, column=0, sticky="w", padx=5, pady=5)
        self.combo_modeles = ttk.Combobox(frame_options, textvariable=self.modele_selectionne, state="readonly")
        self.combo_modeles.grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(frame_options, text="Langue source :").grid(row=1, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(frame_options, textvariable=self.langue_source).grid(row=1, column=1, sticky="ew", padx=5, pady=5)

        ttk.Label(frame_options, text="Langue cible :").grid(row=2, column=0, sticky="w", padx=5, pady=5)
        ttk.Entry(frame_options, textvariable=self.langue_cible).grid(row=2, column=1, sticky="ew", padx=5, pady=5)

        frame_options.columnconfigure(1, weight=1)

        # --- Bouton de lancement ---
        self.bouton_traduire = ttk.Button(
            self.root, text="Traduire le document", command=self._lancer_traduction
        )
        self.bouton_traduire.pack(pady=10)

        # --- Barre de progression ---
        self.progress = ttk.Progressbar(self.root, mode="determinate")
        self.progress.pack(fill="x", padx=10, pady=5)

        self.label_statut = ttk.Label(self.root, text="En attente d'un document...")
        self.label_statut.pack(padx=10, pady=2)

        # --- Zone de résultat ---
        frame_resultat = ttk.LabelFrame(self.root, text="4. Résultat (sauvegardé automatiquement au fil de la traduction)")
        frame_resultat.pack(fill="both", expand=True, padx=10, pady=5)

        self.zone_texte = scrolledtext.ScrolledText(frame_resultat, wrap="word")
        self.zone_texte.pack(fill="both", expand=True, padx=5, pady=5)

        self.bouton_sauvegarder = ttk.Button(
            self.root, text="Sauvegarder une copie ailleurs...", command=self._sauvegarder, state="disabled"
        )
        self.bouton_sauvegarder.pack(pady=5)

    def _rafraichir_modeles(self):
        modeles = lister_modeles_ollama()
        self.combo_modeles["values"] = modeles
        if modeles:
            self.modele_selectionne.set(modeles[0])

    def _choisir_fichier(self):
        chemin = filedialog.askopenfilename(
            title="Choisir un fichier PDF", filetypes=[("Fichiers PDF", "*.pdf")]
        )
        if chemin:
            self.chemin_pdf.set(chemin)
            self.label_statut.config(text=f"Document sélectionné : {os.path.basename(chemin)}")

            # Calcule automatiquement le chemin de sortie : même dossier, même nom, en .txt
            dossier = os.path.dirname(chemin)
            nom_base = os.path.splitext(os.path.basename(chemin))[0]
            chemin_txt_auto = os.path.join(dossier, f"{nom_base}.txt")
            self.chemin_sortie.set(chemin_txt_auto)

    def _choisir_sortie(self):
        """Permet de changer manuellement le fichier de sortie avant de lancer la traduction."""
        chemin = filedialog.asksaveasfilename(
            title="Choisir où sauvegarder la traduction",
            defaultextension=".txt",
            initialfile=os.path.basename(self.chemin_sortie.get()) or "traduction.txt",
            filetypes=[("Fichier texte", "*.txt")],
        )
        if chemin:
            self.chemin_sortie.set(chemin)

    def _lancer_traduction(self):
        if not self.chemin_pdf.get():
            messagebox.showwarning("Attention", "Choisis d'abord un fichier PDF.")
            return

        if not self.chemin_sortie.get():
            messagebox.showwarning("Attention", "Aucun fichier de sortie défini.")
            return

        self.bouton_traduire.config(state="disabled")
        self.bouton_sauvegarder.config(state="disabled")
        self.zone_texte.delete("1.0", tk.END)
        self.progress["value"] = 0

        # On vide/crée le fichier de sortie tout de suite, pour la sauvegarde progressive
        try:
            with open(self.chemin_sortie.get(), "w", encoding="utf-8") as f:
                f.write("")
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible de créer le fichier de sortie :\n{e}")
            self.bouton_traduire.config(state="normal")
            return

        thread = threading.Thread(target=self._executer_traduction, daemon=True)
        thread.start()

    def _executer_traduction(self):
        try:
            self._maj_statut("Extraction du texte du PDF...")
            texte = extraire_texte_pdf(self.chemin_pdf.get())

            if not texte.strip():
                self._maj_statut("Aucun texte trouvé dans ce PDF (peut-être scanné en image ?).")
                self._reactiver_bouton()
                return

            self._maj_statut("Découpage du texte en sections...")
            chunks = decouper_en_chunks(texte)
            total = len(chunks)
            self.root.after(0, lambda: self.progress.config(maximum=total, value=0))

            traductions = []
            for i, chunk in enumerate(chunks, start=1):
                self._maj_statut(f"Traduction de la section {i}/{total}...")
                traduction = traduire_chunk(
                    chunk,
                    self.modele_selectionne.get(),
                    self.langue_source.get(),
                    self.langue_cible.get(),
                )
                traductions.append(traduction)

                # Sauvegarde progressive : on ajoute la section au fichier sur disque
                # tout de suite, pour ne rien perdre si le programme plante ou est fermé.
                with open(self.chemin_sortie.get(), "a", encoding="utf-8") as f:
                    f.write(f"--- Section {i} ---\n{traduction}\n\n")

                # Affichage progressif du résultat
                self.root.after(0, lambda t=traduction, idx=i: self._ajouter_resultat(t, idx))
                self.root.after(0, lambda v=i: self.progress.config(value=v))

            self._maj_statut(f"Traduction terminée ! Sauvegardée dans : {self.chemin_sortie.get()}")
            self.root.after(0, lambda: self.bouton_sauvegarder.config(state="normal"))

        except requests.exceptions.ConnectionError:
            self._maj_statut("Erreur : impossible de contacter Ollama. Est-il bien lancé ?")
            messagebox.showerror(
                "Erreur de connexion",
                "Impossible de contacter Ollama sur http://localhost:11434.\n"
                "Vérifie qu'Ollama est bien lancé.\n\n"
                f"Les sections déjà traduites sont sauvegardées dans :\n{self.chemin_sortie.get()}",
            )
        except Exception as e:
            self._maj_statut(f"Erreur : {e}")
            messagebox.showerror(
                "Erreur",
                f"{e}\n\nLes sections déjà traduites sont sauvegardées dans :\n{self.chemin_sortie.get()}",
            )
        finally:
            self._reactiver_bouton()

    def _ajouter_resultat(self, texte: str, index: int):
        self.zone_texte.insert(tk.END, f"--- Section {index} ---\n{texte}\n\n")
        self.zone_texte.see(tk.END)

    def _maj_statut(self, texte: str):
        self.root.after(0, lambda: self.label_statut.config(text=texte))

    def _reactiver_bouton(self):
        self.root.after(0, lambda: self.bouton_traduire.config(state="normal"))

    def _sauvegarder(self):
        chemin = filedialog.asksaveasfilename(
            title="Sauvegarder la traduction",
            defaultextension=".txt",
            filetypes=[("Fichier texte", "*.txt")],
        )
        if chemin:
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(self.zone_texte.get("1.0", tk.END))
            messagebox.showinfo("Sauvegardé", f"Traduction sauvegardée :\n{chemin}")


# ----------------------------------------------------------------------------
# Point d'entrée
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    app = TraducteurApp(root)
    root.mainloop()