"""
Tests du store transactionnel (phase 9, étape A).

À ce stade le store n'a AUCUN appelant : ces tests le valident isolément, avant
de le brancher étape par étape. L'essentiel porte sur la propriété qui justifie
tout le chantier — écrire le contenu et avancer l'état est atomique (F3).
"""

import json
import sqlite3
import threading

import pytest

from app.services import store


@pytest.fixture(autouse=True)
def base_temporaire(tmp_path):
    store.reinitialiser_pour_tests(str(tmp_path / "test.db"))
    yield
    store.fermer()


def test_le_schema_se_cree_et_le_mode_wal_est_actif():
    mode = store.connexion().execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal", "sans WAL, un lecteur bloque le rédacteur"
    tables = {
        l["name"] for l in store.connexion().execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert {"documents", "etats", "morceaux", "chapitres"} <= tables


# ── Cache ────────────────────────────────────────────────────────────────────

def test_un_morceau_s_ecrit_sans_reecrire_les_autres():
    """
    Le cache JSON réécrivait le fichier ENTIER à chaque sous-morceau — O(n²) sur
    un livre, et autant d'occasions de tout perdre.
    """
    store.ecrire_morceau("/doc.md", "cle-a", "traduction A")
    store.ecrire_morceau("/doc.md", "cle-b", "traduction B")
    assert store.lire_morceaux("/doc.md") == {"cle-a": "traduction A", "cle-b": "traduction B"}


def test_les_caches_de_deux_documents_ne_se_melangent_pas():
    store.ecrire_morceau("/a.md", "meme-cle", "pour A")
    store.ecrire_morceau("/b.md", "meme-cle", "pour B")
    assert store.lire_morceaux("/a.md")["meme-cle"] == "pour A"
    assert store.lire_morceaux("/b.md")["meme-cle"] == "pour B"


def test_reecrire_une_cle_remplace_sans_dupliquer():
    store.ecrire_morceau("/doc.md", "cle", "v1")
    store.ecrire_morceau("/doc.md", "cle", "v2")
    assert store.lire_morceaux("/doc.md") == {"cle": "v2"}


# ── État ─────────────────────────────────────────────────────────────────────

def test_etat_ecrit_relu_supprime():
    assert store.lire_etat("/doc.md") is None
    store.ecrire_etat("/doc.md", json.dumps({"statut": "en_cours"}))
    assert json.loads(store.lire_etat("/doc.md"))["statut"] == "en_cours"
    store.supprimer_etat("/doc.md")
    assert store.lire_etat("/doc.md") is None


# ── LA propriété qui justifie la phase 9 ─────────────────────────────────────

def test_chapitre_et_etat_sont_ecrits_dans_UNE_transaction():
    """
    F3 : le chapitre était ajouté au .md AVANT que `chapitres_traduits` ne soit
    persisté. Un arrêt dans cet intervalle faisait réécrire le chapitre à la
    reprise — duplication silencieuse. Ici les deux tombent ensemble.
    """
    store.ecrire_chapitre_et_etat(
        "/doc.md", 0, "Chapitre 1", "contenu traduit", 0,
        json.dumps({"chapitres_traduits": [0]}),
    )
    assert len(store.lire_chapitres("/doc.md")) == 1
    assert json.loads(store.lire_etat("/doc.md"))["chapitres_traduits"] == [0]


def test_un_echec_en_cours_de_transaction_n_ecrit_RIEN():
    """
    Preuve de l'atomicité : on fait échouer la seconde écriture de la transaction
    et on vérifie que la PREMIÈRE n'a pas été conservée.
    """
    conn = store.connexion()
    with pytest.raises(sqlite3.Error):
        with conn:
            conn.execute(
                "INSERT INTO chapitres (chemin_sortie, index_chapitre, titre, contenu, ordre, maj_a) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("/doc.md", 0, "Chapitre 1", "contenu", 0, 0.0),
            )
            # Colonne inexistante → la transaction entière doit être annulée.
            conn.execute("INSERT INTO etats (colonne_absente) VALUES (1)")

    assert store.lire_chapitres("/doc.md") == [], "le chapitre a survécu à une transaction annulée"


def test_les_chapitres_ressortent_dans_l_ordre_du_document():
    """
    Une reprise réécrit les chapitres dans l'ordre où elle les traite, pas dans
    celui du document. `ordre` existe pour ça — sinon un chapitre recousu se
    retrouverait à la fin, comme le faisait le rejeu avant l'unification.
    """
    store.ecrire_chapitre_et_etat("/d.md", 5, "Cinq", "c5", 5, "{}")
    store.ecrire_chapitre_et_etat("/d.md", 1, "Un", "c1", 1, "{}")
    store.ecrire_chapitre_et_etat("/d.md", 3, "Trois", "c3", 3, "{}")
    assert [c["index_chapitre"] for c in store.lire_chapitres("/d.md")] == [1, 3, 5]


# ── Concurrence ──────────────────────────────────────────────────────────────

def test_deux_threads_ecrivent_sans_se_marcher_dessus():
    """
    Le produit a plusieurs threads : le worker, le planificateur, ceux d'uvicorn.
    Une connexion SQLite n'étant pas sûre à partager, le store en ouvre une PAR
    thread — ce test échouerait si on partageait la même.
    """
    erreurs = []

    def ecrire(prefixe):
        try:
            for i in range(20):
                store.ecrire_morceau("/doc.md", f"{prefixe}-{i}", f"texte {i}")
        except Exception as e:      # noqa: BLE001
            erreurs.append(e)
        finally:
            store.fermer()

    fils = [threading.Thread(target=ecrire, args=(p,)) for p in ("a", "b", "c")]
    for f in fils: f.start()
    for f in fils: f.join()

    assert erreurs == [], f"écritures concurrentes en échec : {erreurs}"
    assert len(store.lire_morceaux("/doc.md")) == 60
