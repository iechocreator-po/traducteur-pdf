"""
Tests du registre de la Bibliothèque (documents traduits) et de la capture
d'intérêt pour les fonctionnalités en développement.
"""

from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import bibliotheque, interet
from app.services.job_manager import sauvegarder_etat


def _registre_temporaire(tmp_path, monkeypatch):
    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "bibliotheque.json"))


def test_enregistrer_puis_lister(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("contenu traduit")

    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"),
        chemin_sortie=str(sortie),
        modele="llama3.1",
        langue_source="anglais",
        langue_cible="français",
    )
    docs = bibliotheque.lister_documents()
    assert len(docs) == 1
    assert docs[0]["nom"] == "doc.pdf"
    # Sortie présente sans .state.json → jugé terminé (traduction d'avant le registre)
    assert docs[0]["statut"] == "termine"


def test_upsert_ne_duplique_pas(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("x")

    for modele in ("llama3.1", "mistral"):
        bibliotheque.enregistrer_document(
            chemin_source=str(tmp_path / "doc.pdf"),
            chemin_sortie=str(sortie),
            modele=modele,
            langue_source="anglais",
            langue_cible="français",
        )
    docs = bibliotheque.lister_documents()
    assert len(docs) == 1
    assert docs[0]["modele"] == "mistral"  # mis à jour, pas dupliqué


def test_statut_et_progression_depuis_etat(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("x")

    etat = EtatJob(
        job_id="j1",
        chemin_pdf=str(tmp_path / "doc.pdf"),
        chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=StatutJob.EN_COURS,
        derniere_section_completee=3,
        total_sections=10,
    )
    sauvegarder_etat(etat)

    bibliotheque.enregistrer_document(
        str(tmp_path / "doc.pdf"), str(sortie), "llama3.1", "anglais", "français",
    )
    docs = bibliotheque.lister_documents()
    assert docs[0]["statut"] == "en_cours"
    assert docs[0]["sections_completees"] == 3
    assert docs[0]["total_sections"] == 10


def test_fichiers_disparus_ignores(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    bibliotheque.enregistrer_document(
        str(tmp_path / "doc.pdf"), str(tmp_path / "disparu_traduit.md"),
        "llama3.1", "anglais", "français",
    )
    assert bibliotheque.lister_documents() == []


def test_registre_absent_retourne_vide(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    assert bibliotheque.lister_documents() == []


# ── Capture d'intérêt ─────────────────────────────────────────────────────────

def test_lister_expose_chapitres_traduits_et_job_id(tmp_path, monkeypatch):
    """La liste enrichie expose chapitres_traduits (pour marquer le sélecteur) et
    job_id (pour la pause depuis « Vos traductions »)."""
    _registre_temporaire(tmp_path, monkeypatch)
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("contenu", encoding="utf-8")
    etat = EtatJob(
        job_id="abc", chemin_pdf=str(tmp_path / "doc.pdf"), chemin_sortie=str(sortie),
        langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele_ollama="llama3.1",
        statut=StatutJob.TERMINE, chapitres_traduits=[0, 2, 5],
    )
    sauvegarder_etat(etat)
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"), chemin_sortie=str(sortie),
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )

    doc = bibliotheque.lister_documents()[0]
    assert doc["chapitres_traduits"] == [0, 2, 5]
    assert doc["job_id"] == "abc"
    assert doc["statut"] == "termine"


def test_retirer_document_du_registre_sans_toucher_au_disque(tmp_path, monkeypatch):
    _registre_temporaire(tmp_path, monkeypatch)
    sortie = tmp_path / "doc_traduit_ll.md"
    sortie.write_text("contenu traduit")
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"), chemin_sortie=str(sortie),
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )
    assert len(bibliotheque.lister_documents()) == 1

    retire = bibliotheque.retirer_document(str(sortie))
    assert retire is True
    assert bibliotheque.lister_documents() == []
    # Le fichier de sortie sur le disque est CONSERVÉ (nettoyage de liste seulement).
    assert sortie.exists()
    # Retirer un chemin inconnu ne fait rien.
    assert bibliotheque.retirer_document(str(tmp_path / "inconnu.md")) is False


def test_email_valide():
    assert interet.email_valide("jp@example.com")
    assert interet.email_valide("  jp@example.com  ")  # espaces tolérés
    assert not interet.email_valide("pas-un-email")
    assert not interet.email_valide("a@b")
    assert not interet.email_valide("a b@c.com")


def test_enregistrer_interet_trace_dans_le_log(tmp_path, monkeypatch):
    log = tmp_path / "interet.log"
    monkeypatch.setattr(interet, "_FICHIER_LOG", str(log))

    interet.enregistrer_interet("clonage_voix", "jp@example.com")
    interet.enregistrer_interet("export_pdf", "autre@example.com")

    contenu = log.read_text()
    assert "fonctionnalite=clonage_voix email=jp@example.com" in contenu
    assert "fonctionnalite=export_pdf email=autre@example.com" in contenu
    assert len(contenu.strip().splitlines()) == 2
