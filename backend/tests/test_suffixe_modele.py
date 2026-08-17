"""
Non-régression sur l'identification du modèle dans les noms de fichiers.

Défaut trouvé le 1/8/2026 en préparant l'ajout de Qwen (feature 338) : le
suffixe était `modele[:2]`, donc deux modèles d'une même famille écrivaient dans
le MÊME fichier de sortie, le même `.state.json` et le même cache. Or comparer
deux modèles sur un document est la raison même d'en installer un second.
"""

import os

from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import job_manager
from app.services.translation_runner import (
    build_output_path,
    _trouver_etat_existant,
    suffixe_modele,
)


def test_deux_modeles_d_une_meme_famille_ne_collisionnent_plus(tmp_path):
    source = str(tmp_path / "livre.pdf")
    paires = [
        ("llama3.1", "llama3.2"),
        ("qwen2.5", "qwen3"),
        ("gemma2", "gemma3"),
    ]
    for a, b in paires:
        assert build_output_path(source, a) != build_output_path(source, b), (
            f"{a} et {b} écrivent dans le même fichier"
        )


def test_la_balise_ollama_ne_change_pas_le_fichier(tmp_path):
    """`qwen2.5` et `qwen2.5:latest` sont le même modèle — même sortie."""
    source = str(tmp_path / "livre.pdf")
    assert build_output_path(source, "qwen2.5") == build_output_path(source, "qwen2.5:latest")


def test_le_suffixe_reste_lisible():
    """Les fichiers vivent à côté des documents : on doit lire le modèle."""
    assert suffixe_modele("qwen2.5:latest") == "qwen2-5"
    assert suffixe_modele("llama3.1") == "llama3-1"
    assert suffixe_modele("") == ""


def test_un_document_traduit_avant_garde_son_ancien_nom(tmp_path):
    """
    Compat : sans ça, reprendre une traduction existante repartirait de zéro
    dans un fichier neuf et l'ancienne deviendrait orpheline.
    """
    source = str(tmp_path / "livre.pdf")
    ancien = tmp_path / "livre_traduit_ll.md"
    ancien.write_text("# déjà traduit\n", encoding="utf-8")

    assert build_output_path(source, "llama3.1") == str(ancien)


def test_un_document_neuf_prend_le_nom_distinctif(tmp_path):
    source = str(tmp_path / "livre.pdf")
    assert build_output_path(source, "llama3.1").endswith("_traduit_llama3-1.md")


def _etat(chemin_sortie: str, job_id: str) -> None:
    job_manager.sauvegarder_etat(EtatJob(
        job_id=job_id,
        chemin_pdf="/fake/livre.pdf",
        chemin_sortie=chemin_sortie,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="peu importe",
        statut=StatutJob.EN_PAUSE,
    ))


def test_reprendre_ne_repart_jamais_sur_l_etat_d_un_autre_modele(tmp_path):
    """
    Le défaut le plus coûteux : `_trouver_etat_existant` renvoyait le PREMIER
    état trouvé par le glob, donc un état arbitraire. Avec deux modèles,
    « Reprendre » pouvait poursuivre le travail de l'autre.
    """
    source = str(tmp_path / "livre.pdf")
    sortie_a = build_output_path(source, "llama3.1")
    sortie_b = build_output_path(source, "qwen2.5")
    _etat(sortie_a, "job-llama")
    _etat(sortie_b, "job-qwen")

    assert _trouver_etat_existant(source, "llama3.1").job_id == "job-llama"
    assert _trouver_etat_existant(source, "qwen2.5").job_id == "job-qwen"


def test_un_modele_sans_traduction_ne_recupere_pas_celle_du_voisin(tmp_path):
    """
    Traduire avec un NOUVEAU modèle doit repartir de zéro, pas hériter de l'état
    d'un autre — sinon on croirait le document déjà traduit par Qwen.
    """
    source = str(tmp_path / "livre.pdf")
    _etat(build_output_path(source, "llama3.1"), "job-llama")

    assert _trouver_etat_existant(source, "qwen2.5") is None
