"""
Tests du store transactionnel (phase 9, étape A).

À ce stade le store n'a AUCUN appelant : ces tests le valident isolément, avant
de le brancher étape par étape. L'essentiel porte sur la propriété qui justifie
tout le chantier — écrire le contenu et avancer l'état est atomique (F3).
"""

import json
import os
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


# ── Étape C : l'état en double écriture ──────────────────────────────────────

def _etat(chemin_sortie: str, statut="en_pause", faites=3):
    from app.models.schemas import EtatJob, Langue, StatutJob
    return EtatJob(
        job_id="job-1",
        chemin_pdf="/fake/doc.pdf",
        chemin_sortie=chemin_sortie,
        langue_source=Langue.ANGLAIS,
        langue_cible=Langue.FRANCAIS,
        modele_ollama="llama3.1",
        statut=StatutJob(statut),
        derniere_section_completee=faites,
    )


def test_l_etat_est_ecrit_dans_le_store_ET_le_json(tmp_path):
    from app.services import job_manager

    sortie = str(tmp_path / "doc_traduit.md")
    job_manager.sauvegarder_etat(_etat(sortie))

    assert os.path.exists(job_manager.chemin_fichier_etat(sortie)), "le JSON reste la source"
    assert store.lire_etat(sortie) is not None, "le store doit être alimenté en parallèle"


def test_un_etat_json_corrompu_est_recupere_depuis_le_store(tmp_path):
    """
    Même gain que pour le cache : la corruption d'un `.state.json` ne fait plus
    perdre la progression. Avant, le fichier partait en quarantaine et le job
    devenait un document sans état — donc réputé terminé.
    """
    from app.services import job_manager

    sortie = str(tmp_path / "doc_traduit.md")
    job_manager.sauvegarder_etat(_etat(sortie, faites=7))

    chemin = job_manager.chemin_fichier_etat(sortie)
    contenu = open(chemin, encoding="utf-8").read()
    with open(chemin, "w", encoding="utf-8") as f:
        f.write(contenu[: len(contenu) // 2])          # troncature réelle

    recharge = job_manager.charger_etat(sortie)
    assert recharge is not None, "l'état a été perdu alors que le store l'avait"
    assert recharge.derniere_section_completee == 7


def test_supprimer_un_etat_le_retire_des_DEUX_cotes(tmp_path):
    """
    Régression : `supprimer_etat` n'effaçait que le fichier. Le repli sur le
    store ressuscitait alors l'état « supprimé » au premier `charger_etat`.
    """
    from app.services import job_manager

    sortie = str(tmp_path / "doc_traduit.md")
    job_manager.sauvegarder_etat(_etat(sortie))
    job_manager.supprimer_etat(sortie)

    assert store.lire_etat(sortie) is None
    assert job_manager.charger_etat(sortie) is None


def test_un_etat_d_avant_la_bascule_reste_lisible(tmp_path):
    """Compat : un document d'avant la phase 9 n'a que son JSON — il doit servir."""
    from app.services import job_manager
    from app.services.persistance import ecrire_texte_atomique

    sortie = str(tmp_path / "ancien_traduit.md")
    ecrire_texte_atomique(
        job_manager.chemin_fichier_etat(sortie), _etat(sortie, faites=42).model_dump_json()
    )
    assert store.lire_etat(sortie) is None                    # rien dans le store
    assert job_manager.charger_etat(sortie).derniere_section_completee == 42


# ── Feature 328 : bascule de lecture store-primaire ───────────────────────────

def test_le_store_a_jour_est_prefere_au_json(tmp_path):
    """
    Une fois la double écriture réussie, c'est le store qui est lu — pas
    seulement un repli. Le prouver en modifiant le JSON sur disque APRÈS coup,
    pour un contenu que seul le store peut avoir renvoyé.
    """
    from app.services import job_manager

    sortie = str(tmp_path / "doc_traduit.md")
    job_manager.sauvegarder_etat(_etat(sortie, faites=5))

    # Le JSON est modifié furtivement APRÈS l'écriture double, sans passer par
    # sauvegarder_etat — si charger_etat lisait encore le JSON en priorité, il
    # verrait cette valeur trafiquée plutôt que celle du store. Le mtime est
    # remis dans le passé : ce test isole "le store est préféré à contenu
    # équivalent ou plus récent", pas la règle de fraîcheur elle-même (couverte
    # par le test suivant) — sans ça, la réécriture avancerait le mtime du JSON
    # et invaliderait le test qu'on cherche justement à écrire.
    chemin = job_manager.chemin_fichier_etat(sortie)
    donnees = json.loads(open(chemin, encoding="utf-8").read())
    donnees["derniere_section_completee"] = 999
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(donnees, f)
    os.utime(chemin, (0, 0))

    recharge = job_manager.charger_etat(sortie)
    assert recharge.derniere_section_completee == 5, (
        "le store aurait dû faire foi (il est au moins aussi récent) — "
        "999 signifierait que le JSON trafiqué a été lu à sa place"
    )


def test_un_store_perime_ne_ressuscite_pas_une_progression_obsolete(tmp_path, monkeypatch):
    """
    Le risque central de la bascule : si l'écriture store échoue en silence une
    fois (comme le prévoit déjà `sauvegarder_etat`), le store garde l'ANCIENNE
    progression. charger_etat ne doit alors PAS la préférer au JSON, plus récent
    et toujours écrit avec succès en premier.
    """
    from app.services import job_manager

    sortie = str(tmp_path / "doc_traduit.md")
    job_manager.sauvegarder_etat(_etat(sortie, faites=3))   # store ET json à 3

    # Le prochain appel réussit le JSON (toujours en premier) mais rate le store
    # — exactement le scénario que le try/except de sauvegarder_etat masque déjà.
    def _store_echoue(*a, **k):
        raise sqlite3.OperationalError("simulation d'échec du store")
    monkeypatch.setattr(store, "ecrire_etat", _store_echoue)
    job_manager.sauvegarder_etat(_etat(sortie, faites=8))    # json=8, store reste à 3

    recharge = job_manager.charger_etat(sortie)
    assert recharge.derniere_section_completee == 8, (
        "le store périmé (resté à 3) n'aurait jamais dû être préféré au JSON, "
        "plus récent — préférer le store ici ressusciterait une progression obsolète"
    )
