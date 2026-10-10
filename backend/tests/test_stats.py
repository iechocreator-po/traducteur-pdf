"""
Historique des traitements (services/metriques.py + services/stats.py) : collecte des compteurs
Ollama, journal append-only, agrégats, estimation de durée honnête sur ses échantillons.
"""

import threading
import time

import pytest

from app.models.schemas import EtatJob, Langue, StatutJob
from app.services import metriques, stats, store


@pytest.fixture(autouse=True)
def base_isolee(tmp_path):
    store.reinitialiser_pour_tests(str(tmp_path / "stats.db"))
    yield
    metriques.terminer()


def reponse_ollama(sortie=100, entree=50, gen_s=2.0, charge_s=0.5, raison="stop"):
    return {"prompt_eval_count": entree, "eval_count": sortie, "eval_duration": int(gen_s * 1e9),
            "load_duration": int(charge_s * 1e9), "total_duration": int((gen_s + charge_s) * 1e9),
            "done_reason": raison}


# ── Collecte ─────────────────────────────────────────────────────────────────

def test_la_collecte_accumule_les_compteurs():
    c = metriques.demarrer()
    metriques.enregistrer_reponse(reponse_ollama(sortie=100, entree=40))
    metriques.enregistrer_reponse(reponse_ollama(sortie=60, entree=30, raison="length"))
    metriques.enregistrer_morceau(1000, 1100)
    metriques.enregistrer_cache(500)
    metriques.enregistrer_nouvelle_tentative()
    assert (c.appels, c.jetons_sortie, c.jetons_entree) == (2, 160, 70)
    assert c.reponses_tronquees == 1
    assert (c.morceaux_traduits, c.caracteres_traites, c.caracteres_sortie) == (1, 1000, 1100)
    assert (c.morceaux_cache, c.caracteres_cache, c.nouvelles_tentatives) == (1, 500, 1)


def test_sans_collecte_active_rien_ne_plante():
    metriques.terminer()
    metriques.enregistrer_reponse(reponse_ollama())
    metriques.enregistrer_morceau(10, 10)
    metriques.enregistrer_cache(10)
    assert metriques.courant() is None


def test_champs_absents_de_la_reponse_comptent_zero():
    c = metriques.demarrer()
    metriques.enregistrer_reponse({})
    metriques.enregistrer_reponse({"eval_count": None})
    assert (c.appels, c.jetons_sortie) == (2, 0)


def test_une_collecte_ne_fuit_pas_vers_un_autre_fil():
    """Le worker exécute un traitement par fil : la collecte d'un fil ne doit pas voir l'autre."""
    metriques.demarrer()
    vu = {}

    def autre():
        vu["courant"] = metriques.courant()

    t = threading.Thread(target=autre)
    t.start(); t.join()
    assert vu["courant"] is None


# ── Journal ──────────────────────────────────────────────────────────────────

def etat(modele="qwen2.5:latest"):
    return EtatJob(job_id="j1", chemin_pdf="/tmp/Mon livre.pdf", chemin_sortie="/tmp/Mon livre_traduit_qw.md",
                   langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele_ollama=modele,
                   statut=StatutJob.TERMINE, total_sections=12, mots_traduits=800)


def test_une_traduction_laisse_une_ligne_complete():
    c = metriques.demarrer()
    metriques.enregistrer_reponse(reponse_ollama(sortie=500, entree=300, gen_s=10, charge_s=1.5))
    metriques.enregistrer_morceau(2000, 2200)
    stats.enregistrer_traduction(etat(), c, time.time() - 30, nb_chapitres=2)
    [r] = stats.lister_runs()
    assert (r["type"], r["nom"], r["modele"]) == ("traduction", "Mon livre.pdf", "qwen2.5:latest")
    assert (r["jetons_sortie"], r["jetons_entree"], r["caracteres_traites"]) == (500, 300, 2000)
    assert r["duree_generation_s"] == pytest.approx(10) and r["duree_chargement_s"] == pytest.approx(1.5)
    assert r["duree_secondes"] == pytest.approx(30, abs=2)
    assert (r["chapitres"], r["morceaux_total"], r["reconstitue"]) == (2, 12, 0)


def test_le_journal_ne_fait_jamais_echouer_un_traitement(monkeypatch):
    def casse(*a, **k):
        raise RuntimeError("base verrouillée")

    monkeypatch.setattr(stats, "inserer_run", casse)
    stats.enregistrer_traduction(etat(), metriques.demarrer(), time.time(), 1)   # ne lève pas


def ligne(modele="m", sec=60, car=10000, jetons=1000, gen=40, mots=1700, reconstitue=0, type_="traduction"):
    return {"type": type_, "modele": modele, "demarre_a": time.time(), "duree_secondes": sec,
            "caracteres_traites": car, "jetons_sortie": jetons, "duree_generation_s": gen, "mots": mots,
            "reconstitue": reconstitue, "chemin_sortie": f"/x/{time.time_ns()}.md"}


# ── Agrégats et estimation ───────────────────────────────────────────────────

def test_agregat_vitesse_mediane_par_modele():
    for sec in (50, 60, 400):      # un passage aberrant (modèle froid, veille) ne fausse pas la médiane
        stats.inserer_run(ligne(sec=sec, car=10000, jetons=1000, gen=sec - 10))
    [m] = stats.agreger(stats.lister_runs())["modeles"]
    assert m["passages"] == 3 and m["secondes_par_1000_car"] == pytest.approx(6.0)


def test_non_mesure_reste_null_et_jamais_zero():
    stats.inserer_run(ligne(reconstitue=1, car=None, jetons=None, gen=None))
    [r] = stats.lister_runs()
    assert r["jetons_sortie"] is None and r["caracteres_traites"] is None
    [m] = stats.agreger([r])["modeles"]
    assert m["jetons_par_seconde"] is None and m["secondes_par_1000_car"] is None


def test_estimation_insuffisante_sous_trois_echantillons():
    stats.inserer_run(ligne())
    stats.inserer_run(ligne())
    e = stats.estimer("m", caracteres=50000)
    assert e["statut"] == "insuffisant" and e["echantillons"] == 2


def test_estimation_avec_fourchette():
    for sec in (50, 60, 70, 80):   # 5, 6, 7, 8 s pour 1000 car.
        stats.inserer_run(ligne(sec=sec, car=10000))
    e = stats.estimer("m", caracteres=50000)
    assert e["statut"] == "ok" and (e["base"], e["source"], e["echantillons"]) == ("caracteres", "mesure", 4)
    assert e["secondes"] == pytest.approx(6.5 * 50)
    assert e["min"] < e["secondes"] < e["max"]


def test_estimation_ignore_les_autres_modeles():
    for _ in range(3):
        stats.inserer_run(ligne(modele="rapide", sec=10))
    assert stats.estimer("lent", caracteres=1000)["statut"] == "insuffisant"


def test_les_lignes_reconstituees_ne_servent_qua_defaut_de_mesures():
    for sec in (100, 100, 100):
        stats.inserer_run(ligne(reconstitue=1, sec=sec, car=None, jetons=None, gen=None, mots=1000))
    e = stats.estimer("m", mots=10000)
    assert e["statut"] == "ok" and e["source"] == "reconstitue"
    # dès qu'il y a assez de mesures réelles, elles priment
    for _ in range(3):
        stats.inserer_run(ligne(sec=10, car=None, mots=1000))
    assert stats.estimer("m", mots=10000)["source"] == "mesure"


def test_reconstitution_idempotente(monkeypatch):
    e = etat()
    e.temps_ecoule_secondes = 120.0
    e.temps_debut = 1_700_000_000.0
    monkeypatch.setattr("app.services.bibliotheque.lister_documents", lambda: [{"chemin_sortie": e.chemin_sortie}])
    monkeypatch.setattr("app.services.job_manager.charger_etat", lambda chemin: e)
    assert stats.reconstituer_depuis_documents() == 1
    assert stats.reconstituer_depuis_documents() == 0
    [r] = stats.lister_runs()
    assert r["reconstitue"] == 1 and r["jetons_sortie"] is None and r["mots"] == 800


# ── Routes ───────────────────────────────────────────────────────────────────

def test_routes():
    from fastapi.testclient import TestClient
    from app.main import app

    client = TestClient(app)
    for sec in (50, 60, 70):
        stats.inserer_run(ligne(sec=sec))
    d = client.get("/api/stats/traitements").json()
    assert len(d["runs"]) == 3 and d["agregats"]["totaux"]["passages"] == 3
    assert client.get("/api/stats/traitements", params={"type": "tts"}).status_code == 422
    assert client.get("/api/stats/estimation", params={"modele": "m"}).status_code == 422
    e = client.get("/api/stats/estimation", params={"modele": "m", "caracteres": 10000}).json()
    assert e["statut"] == "ok"


# ── De bout en bout : le moteur de traduction écrit bien sa ligne ────────────

def test_une_traduction_reelle_laisse_ses_jetons_dans_l_historique(tmp_path, monkeypatch):
    """Vrai moteur, vrai runner, seul Ollama (requests.post) est simulé : les compteurs de la réponse
    doivent se retrouver dans `stats_runs`, avec la taille du texte et le modèle."""
    from app.models.schemas import Langue
    from app.services import translation_runner, translator
    from app.services.job_manager import charger_etat

    corps = "mot " * 200
    source = tmp_path / "livre.md"
    source.write_text("".join(f"# Section {i}\n\n{corps}\n\n" for i in range(3)), encoding="utf-8")

    class FauxOllama:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            # traduction de longueur voisine de la source : passe le contrôle qualité (ratio)
            return {"response": corps, "prompt_eval_count": 300, "eval_count": 250,
                    "eval_duration": int(5e9), "load_duration": int(1e9), "total_duration": int(6e9),
                    "done_reason": "stop"}

    monkeypatch.setattr(translator.requests, "post", lambda *a, **k: FauxOllama())
    job_id = translation_runner.demarrer_traduction(
        source_path=str(source), langue_source=Langue.ANGLAIS, langue_cible=Langue.FRANCAIS, modele="llama3.1")
    sortie = translation_runner.build_output_path(str(source), "llama3.1")

    fin = time.time() + 15
    runs = []
    while time.time() < fin and not runs:
        etat = charger_etat(sortie)
        if etat and etat.statut == StatutJob.TERMINE:
            runs = stats.lister_runs()          # la ligne s'écrit juste APRÈS l'état final
        time.sleep(0.05)
    assert len(runs) == 1, "aucune ligne d'historique après une traduction terminée"
    r = runs[0]
    assert (r["type"], r["nom"], r["modele"], r["statut"]) == ("traduction", "livre.md", "llama3.1", "termine")
    assert r["appels"] == 3 and r["morceaux_traduits"] == 3 and r["morceaux_cache"] == 0
    assert (r["jetons_sortie"], r["jetons_entree"]) == (750, 900)
    assert r["duree_generation_s"] == pytest.approx(15) and r["duree_chargement_s"] == pytest.approx(3)
    assert 3 * len(corps) <= r["caracteres_traites"] <= 3 * (len(corps) + 40)   # trois sections de ~800 caractères
    assert r["reconstitue"] == 0 and r["job_id"] == job_id
