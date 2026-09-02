"""
Store transactionnel SQLite (principe cible ②, phase 9).

Ce module est le socle du remplacement des fichiers JSON éparpillés par une base
unique. **Étape A : il n'a encore AUCUN appelant.** Le brancher se fait ensuite
étape par étape (cache, puis état, puis contenu), chacune en double écriture,
pour qu'aucun état intermédiaire ne laisse deux sources de vérité en désaccord.

Pourquoi SQLite plutôt que des fichiers durcis
----------------------------------------------
Les phases 1 à 5 ont rendu chaque fichier atomique pris isolément. Ça ne garantit
pas que DEUX fichiers soient cohérents entre eux — et c'est précisément le défaut
F3 : un chapitre est ajouté au `.md` AVANT que `chapitres_traduits` ne soit
persisté, donc un arrêt dans cet intervalle fait réécrire ce chapitre à la
reprise. Une transaction supprime cette fenêtre par construction.

Choix assumés
-------------
- **Une base centrale** (`backend/toledo.db`) plutôt qu'une base par document.
  Une transaction ne peut pas traverser deux fichiers SQLite ; or c'est
  exactement ce qu'il faut pour écrire un chapitre ET avancer la progression
  d'un coup. Contrepartie honnête : on réintroduit un point unique, ce que
  l'audit reprochait à `bibliotheque.json` (F1). La différence est que SQLite en
  WAL journalise ses écritures et se répare seul, là où notre JSON réécrit par
  troncature ne le pouvait pas.
- **Mode WAL** : plusieurs lecteurs (les requêtes HTTP) pendant qu'un rédacteur
  (le worker) écrit, sans blocage mutuel.
- **Une connexion par thread**. Une connexion SQLite n'est pas sûre à partager
  entre threads, et le produit en a plusieurs : le worker de la file, le thread
  du planificateur, ceux d'uvicorn. `check_same_thread=False` supprimerait le
  garde-fou sans supprimer le problème.
- **`chemin_sortie` comme identifiant**, comme partout ailleurs dans le produit.
  Changer d'identifiant en même temps que de stockage rendrait la migration
  impossible à vérifier.
"""

import os
import sqlite3
import threading
import time

CHEMIN_BASE = os.path.join(os.path.dirname(__file__), "..", "..", "toledo.db")
CHEMIN_BASE = os.path.normpath(CHEMIN_BASE)

_local = threading.local()

# Sérialise la MISE EN PLACE d'une connexion (bascule WAL + création du schéma).
# `PRAGMA journal_mode=WAL` exige un verrou exclusif : si deux threads ouvrent
# leur connexion en même temps sur une base fraîche, l'un des deux reçoit
# « database is locked » — le busy_timeout ne protège pas contre ça, parce que le
# conflit porte sur le changement de mode, pas sur une écriture.
# Trouvé par le test de concurrence à 3 threads, qui échouait environ une fois
# sur cinq. Ce n'est pas un artefact de test : le worker, le planificateur et les
# threads d'uvicorn ouvrent leurs connexions au démarrage, donc en même temps.
_verrou_ouverture = threading.Lock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    chemin_sortie  TEXT PRIMARY KEY,
    chemin_source  TEXT NOT NULL,
    modele         TEXT,
    langue_source  TEXT,
    langue_cible   TEXT,
    maj_a          REAL NOT NULL
);

-- État du job, sérialisé. Le détail reste un blob : le faire éclater en colonnes
-- figerait le schéma d'EtatJob dans la base, alors qu'il évolue encore.
CREATE TABLE IF NOT EXISTS etats (
    chemin_sortie  TEXT PRIMARY KEY,
    donnees        TEXT NOT NULL,
    maj_a          REAL NOT NULL
);

-- Cache de traduction : une ligne par sous-morceau, indexée par CONTENU.
CREATE TABLE IF NOT EXISTS morceaux (
    chemin_sortie  TEXT NOT NULL,
    cle            TEXT NOT NULL,
    texte          TEXT NOT NULL,
    cree_a         REAL NOT NULL,
    PRIMARY KEY (chemin_sortie, cle)
);

-- Contenu traduit, une ligne par chapitre. `ordre` existe séparément d'`index_chapitre`
-- parce qu'une reprise peut réécrire les chapitres dans un ordre différent de
-- celui où ils ont été produits, et l'export doit suivre l'ordre du document.
CREATE TABLE IF NOT EXISTS chapitres (
    chemin_sortie   TEXT NOT NULL,
    index_chapitre  INTEGER NOT NULL,
    titre           TEXT,
    contenu         TEXT NOT NULL,
    ordre           INTEGER NOT NULL,
    maj_a           REAL NOT NULL,
    PRIMARY KEY (chemin_sortie, index_chapitre)
);

CREATE INDEX IF NOT EXISTS idx_chapitres_ordre
    ON chapitres (chemin_sortie, ordre);
"""


def connexion() -> sqlite3.Connection:
    """
    Connexion du thread courant, créée à la demande.

    `PRAGMA foreign_keys` n'est pas activé : le schéma n'a volontairement aucune
    clé étrangère. Un document peut disparaître du registre sans que son travail
    soit détruit — c'est déjà la sémantique de `DELETE /api/bibliotheque`, qui
    retire du registre sans toucher aux fichiers.
    """
    conn = getattr(_local, "conn", None)
    # On mémorise POUR QUELLE base la connexion a été ouverte. Le worker de la
    # file est un thread de longue durée : si la base change sous lui (ce que
    # font les tests, un fichier temporaire par test), il continuerait sinon
    # d'écrire dans l'ancienne — les chapitres partaient dans la base du test
    # précédent, et le test courant lisait une base vide. En production le chemin
    # ne change jamais, mais un garde qui ne tient que par cette hypothèse est
    # un garde qui tombera le jour où elle cesse d'être vraie.
    if conn is not None and getattr(_local, "chemin", None) == CHEMIN_BASE:
        return conn
    if conn is not None:
        conn.close()
        _local.conn = None
    os.makedirs(os.path.dirname(CHEMIN_BASE), exist_ok=True)
    conn = sqlite3.connect(CHEMIN_BASE, timeout=30)
    conn.row_factory = sqlite3.Row
    with _verrou_ouverture:
        # `busy_timeout` explicite AVANT la bascule WAL : le paramètre `timeout`
        # de connect() ne s'applique pas encore de façon fiable à ce PRAGMA.
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
    # NORMAL suffit en WAL : les écritures survivent au crash du PROCESS, seul un
    # arrêt brutal de la MACHINE peut coûter la dernière transaction. C'est le
    # compromis standard, et il évite un fsync par sous-morceau.
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        conn.commit()
    _local.conn = conn
    _local.chemin = CHEMIN_BASE
    return conn


def fermer() -> None:
    """Ferme la connexion du thread courant (tests, arrêt propre)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def reinitialiser_pour_tests(chemin: str) -> None:
    """Redirige la base vers un fichier temporaire et repart d'un schéma neuf."""
    global CHEMIN_BASE
    fermer()
    CHEMIN_BASE = chemin


# ── Cache de traduction ──────────────────────────────────────────────────────

def lire_morceaux(chemin_sortie: str) -> dict[str, str]:
    lignes = connexion().execute(
        "SELECT cle, texte FROM morceaux WHERE chemin_sortie = ?", (chemin_sortie,)
    ).fetchall()
    return {l["cle"]: l["texte"] for l in lignes}


def ecrire_morceau(chemin_sortie: str, cle: str, texte: str) -> None:
    """
    Écrit UN morceau. À comparer au cache JSON, qui réécrivait le fichier ENTIER
    à chaque sous-morceau — coût en O(n²) sur un livre, et autant d'occasions de
    tout perdre.
    """
    conn = connexion()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO morceaux (chemin_sortie, cle, texte, cree_a) "
            "VALUES (?, ?, ?, ?)",
            (chemin_sortie, cle, texte, time.time()),
        )


def ecrire_morceaux(chemin_sortie: str, morceaux: dict[str, str]) -> None:
    """Écrit un lot de morceaux en UNE transaction (migration, double écriture)."""
    if not morceaux:
        return
    conn = connexion()
    maintenant = time.time()
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO morceaux (chemin_sortie, cle, texte, cree_a) "
            "VALUES (?, ?, ?, ?)",
            [(chemin_sortie, c, t, maintenant) for c, t in morceaux.items()],
        )


# ── État du job ──────────────────────────────────────────────────────────────

def lire_etat(chemin_sortie: str) -> str | None:
    ligne = connexion().execute(
        "SELECT donnees FROM etats WHERE chemin_sortie = ?", (chemin_sortie,)
    ).fetchone()
    return ligne["donnees"] if ligne else None


def lire_etat_horodate(chemin_sortie: str) -> tuple[str, float] | None:
    """
    (donnees, maj_a) du store, ou None. Sert à `job_manager.charger_etat` pour
    décider si le store est réellement à jour par rapport au JSON, plutôt que de
    le préférer à l'aveugle — voir la feature 328 (bascule lecture) : le JSON
    est TOUJOURS écrit avec succès avant que le store ne soit tenté, donc le
    store peut être en retard (écriture ratée en silence) mais jamais en avance
    sur une écriture qu'il n'a pas encore vue.
    """
    ligne = connexion().execute(
        "SELECT donnees, maj_a FROM etats WHERE chemin_sortie = ?", (chemin_sortie,)
    ).fetchone()
    return (ligne["donnees"], ligne["maj_a"]) if ligne else None


def ecrire_etat(chemin_sortie: str, donnees: str) -> None:
    conn = connexion()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO etats (chemin_sortie, donnees, maj_a) VALUES (?, ?, ?)",
            (chemin_sortie, donnees, time.time()),
        )


def supprimer_etat(chemin_sortie: str) -> None:
    conn = connexion()
    with conn:
        conn.execute("DELETE FROM etats WHERE chemin_sortie = ?", (chemin_sortie,))


# ── Contenu traduit ──────────────────────────────────────────────────────────

def ecrire_chapitre_et_etat(
    chemin_sortie: str,
    index_chapitre: int,
    titre: str | None,
    contenu: str,
    ordre: int,
    etat_json: str,
) -> None:
    """
    LE point de l'étape D : écrire un chapitre et avancer l'état sont UNE SEULE
    transaction. C'est ce qui supprime F3 — la fenêtre où le chapitre existait
    dans la sortie sans être encore marqué comme fait, donc réécrit à la reprise.

    Soit les deux réussissent, soit aucun.
    """
    conn = connexion()
    maintenant = time.time()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO chapitres "
            "(chemin_sortie, index_chapitre, titre, contenu, ordre, maj_a) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chemin_sortie, index_chapitre, titre, contenu, ordre, maintenant),
        )
        conn.execute(
            "INSERT OR REPLACE INTO etats (chemin_sortie, donnees, maj_a) VALUES (?, ?, ?)",
            (chemin_sortie, etat_json, maintenant),
        )


def lire_chapitres(chemin_sortie: str) -> list[dict]:
    """Chapitres du document, dans l'ORDRE du document (pas celui de production)."""
    lignes = connexion().execute(
        "SELECT index_chapitre, titre, contenu, ordre FROM chapitres "
        "WHERE chemin_sortie = ? ORDER BY ordre",
        (chemin_sortie,),
    ).fetchall()
    return [dict(l) for l in lignes]


def enregistrer_document(
    chemin_sortie: str, chemin_source: str, modele: str,
    langue_source: str, langue_cible: str,
) -> None:
    conn = connexion()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO documents "
            "(chemin_sortie, chemin_source, modele, langue_source, langue_cible, maj_a) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (chemin_sortie, chemin_source, modele, langue_source, langue_cible, time.time()),
        )


def lire_document(chemin_sortie: str) -> dict | None:
    ligne = connexion().execute(
        "SELECT chemin_sortie, chemin_source, modele, langue_source, langue_cible, maj_a "
        "FROM documents WHERE chemin_sortie = ?",
        (chemin_sortie,),
    ).fetchone()
    return dict(ligne) if ligne else None


def lister_documents() -> list[dict]:
    """
    Registre tel que connu du store. N'a PAS les mêmes champs que
    `bibliotheque.json` (pas de `nom`, `cree_a`, `qualite` — voir feature 328) :
    ce n'est PAS un remplacement direct, seulement un filet de récupération si
    le JSON est illisible. Voir `bibliotheque._charger`.
    """
    lignes = connexion().execute(
        "SELECT chemin_sortie, chemin_source, modele, langue_source, langue_cible, maj_a "
        "FROM documents"
    ).fetchall()
    return [dict(l) for l in lignes]
