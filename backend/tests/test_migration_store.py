"""
Tests de la migration vers le store (phase 9, étape F).

La migration touche les données RÉELLES de l'utilisateur : c'est la seule étape
de la phase 9 qui ne fait pas qu'ajouter. Ces tests portent donc autant sur ce
qu'elle écrit que sur ce qu'elle refuse de faire.
"""

import importlib.util
import os
import sys

import pytest

_CHEMIN = os.path.join(os.path.dirname(__file__), "..", "scripts", "migrer_vers_store.py")
_spec = importlib.util.spec_from_file_location("migrer_vers_store", _CHEMIN)
migration = importlib.util.module_from_spec(_spec)
sys.modules["migrer_vers_store"] = migration
_spec.loader.exec_module(migration)


def _ecrire(tmp_path, nom, contenu):
    chemin = tmp_path / nom
    chemin.write_text(contenu, encoding="utf-8")
    return str(chemin)


def test_decoupe_un_document_a_chapitres(tmp_path):
    md = _ecrire(tmp_path, "doc.md",
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Ponts === -->\n\nLe texte des ponts.\n"
        "\n<!-- === chapitre 1 : Hubs === -->\n\nLe texte des hubs.\n"
    )
    chapitres = migration.decouper_chapitres(md)
    assert [c["index"] for c in chapitres] == [0, 1]
    assert [c["titre"] for c in chapitres] == ["Ponts", "Hubs"]
    assert chapitres[0]["contenu"] == "Le texte des ponts."
    assert chapitres[1]["contenu"] == "Le texte des hubs."


def test_un_document_sans_marqueur_devient_un_chapitre_implicite(tmp_path):
    """Traduction en « chapitre implicite » : tout le corps est le chapitre 0."""
    md = _ecrire(tmp_path, "doc.md", "<!-- en-tête -->\n\nTout le document ici.\n")
    chapitres = migration.decouper_chapitres(md)
    assert len(chapitres) == 1
    assert chapitres[0]["index"] == 0
    assert chapitres[0]["contenu"] == "Tout le document ici."


def test_l_annexe_des_liens_n_est_PAS_avalee_par_le_dernier_chapitre(tmp_path):
    """
    L'annexe n'appartient à aucun chapitre. L'inclure la ferait réapparaître en
    DOUBLE à la première régénération : une fois dans le contenu du chapitre,
    une fois réajoutée en fin de fichier.
    """
    from app.services.translation_runner import TITRE_ANNEXE_LIENS

    md = _ecrire(tmp_path, "doc.md",
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\nContenu du chapitre.\n"
        f"\n\n---\n\n{TITRE_ANNEXE_LIENS}\n\n- <https://exemple.org>\n"
    )
    chapitres = migration.decouper_chapitres(md)
    assert len(chapitres) == 1
    assert chapitres[0]["contenu"] == "Contenu du chapitre."
    assert "exemple.org" not in chapitres[0]["contenu"]
    assert TITRE_ANNEXE_LIENS not in chapitres[0]["contenu"]


def test_un_fichier_absent_ne_leve_pas(tmp_path):
    assert migration.decouper_chapitres(str(tmp_path / "inexistant.md")) == []


def test_la_simulation_n_ecrit_RIEN(tmp_path, monkeypatch):
    """
    Le mode par défaut. Une migration qui s'applique par mégarde sur les vraies
    données est exactement l'accident que ce projet a déjà connu deux fois.
    """
    from app.services import bibliotheque, store

    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))
    sortie = _ecrire(tmp_path, "doc_traduit_ll.md",
        "<!-- en-tête -->\n\n<!-- === chapitre 0 : Un === -->\n\nContenu.\n")
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"), chemin_sortie=sortie,
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )

    assert migration.migrer(appliquer=False) == 1
    assert store.lire_chapitres(sortie) == [], "la simulation a écrit dans le store"


def test_la_migration_importe_chapitres_et_cache(tmp_path, monkeypatch):
    from app.services import bibliotheque, cache_traduction, store

    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))
    sortie = _ecrire(tmp_path, "doc_traduit_ll.md",
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\nContenu zéro.\n"
        "\n<!-- === chapitre 1 : Deux === -->\n\nContenu un.\n")
    cache_traduction.sauvegarder_cache(sortie, {"cle-a": "traduit A"})
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"), chemin_sortie=sortie,
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )

    assert migration.migrer(appliquer=True) == 1

    chapitres = store.lire_chapitres(sortie)
    assert [c["index_chapitre"] for c in chapitres] == [0, 1]
    assert chapitres[0]["contenu"] == "Contenu zéro."
    assert store.lire_morceaux(sortie)["cle-a"] == "traduit A"
    # Le fichier n'a pas été touché.
    assert "chapitre 1 : Deux" in open(sortie, encoding="utf-8").read()


def test_relancer_la_migration_ne_duplique_rien(tmp_path, monkeypatch):
    """Idempotence : un document déjà dans le store est ignoré."""
    from app.services import bibliotheque, store

    monkeypatch.setattr(bibliotheque, "_FICHIER_BIBLIO", str(tmp_path / "biblio.json"))
    sortie = _ecrire(tmp_path, "doc_traduit_ll.md",
        "<!-- en-tête -->\n\n<!-- === chapitre 0 : Un === -->\n\nContenu.\n")
    bibliotheque.enregistrer_document(
        chemin_source=str(tmp_path / "doc.pdf"), chemin_sortie=sortie,
        modele="llama3.1", langue_source="anglais", langue_cible="français",
    )

    assert migration.migrer(appliquer=True) == 1
    assert migration.migrer(appliquer=True) == 0, "le second passage a re-migré"
    assert len(store.lire_chapitres(sortie)) == 1


def test_une_annexe_AU_MILIEU_ne_tronque_pas_le_document(tmp_path):
    """
    RÉGRESSION — perte silencieuse de 90 %, trouvée par contrôle aller-retour
    sur un livre réel de 716 Ko.

    L'annexe des liens n'est PAS forcément en fin de fichier : un document
    traduit en plusieurs passes la voit ajoutée après la première, puis d'autres
    chapitres s'ajoutent APRÈS elle. Sur ce livre elle était ligne 393 sur 3327,
    et tronquer le corps à sa position perdait 15 chapitres sur 22.
    """
    from app.services.translation_runner import TITRE_ANNEXE_LIENS

    md = _ecrire(tmp_path, "doc.md",
        "<!-- en-tête -->\n"
        "\n<!-- === chapitre 0 : Un === -->\n\nContenu zéro.\n"
        f"\n\n---\n\n{TITRE_ANNEXE_LIENS}\n\n- <https://exemple.org>\n"
        "\n<!-- === chapitre 1 : Deux === -->\n\nContenu un.\n"
        "\n<!-- === chapitre 2 : Trois === -->\n\nContenu deux.\n"
    )
    chapitres = migration.decouper_chapitres(md)

    assert [c["index"] for c in chapitres] == [0, 1, 2], "des chapitres ont été perdus"
    assert chapitres[0]["contenu"] == "Contenu zéro."
    assert "exemple.org" not in chapitres[0]["contenu"]
    assert chapitres[1]["contenu"] == "Contenu un."
    assert chapitres[2]["contenu"] == "Contenu deux."
