"""
Historique des traitements et estimation des durées (10/10/2026).

Chaque PASSAGE du moteur de traduction (ou de génération de fiche d'étude) laisse une ligne dans
`stats_runs` (store.py) : fichier, taille du texte, modèle, jetons lus/générés, durée murale et
durée de calcul d'Ollama, morceaux servis par le cache, plafonds atteints… Deux usages :

- consulter l'historique (onglet « Chronique ») ;
- ESTIMER la durée d'un futur traitement à partir de ce qu'a réellement coûté le même modèle.

Principes :
- Le journal est append-only : on n'édite jamais une ligne après coup.
- Il ne doit JAMAIS faire échouer un traitement : toute erreur d'écriture est avalée (et vue sur
  stdout), jamais propagée.
- « Non mesuré » est NULL, jamais 0. Les lignes reconstituées depuis d'anciens .state.json n'ont
  ni jetons ni durée de calcul ; elles ne servent à estimer qu'à défaut de mesures réelles, et
  l'estimation dit laquelle des deux bases elle utilise.
- L'estimation donne une FOURCHETTE (quartiles) et le nombre d'échantillons ; en dessous de
  ECHANTILLONS_MIN elle répond « insuffisant » plutôt qu'un chiffre trompeur.
"""

import os
import statistics
import time

from app.services import store

ECHANTILLONS_MIN = 3

_COLONNES = (
    "type", "job_id", "nom", "chemin_source", "chemin_sortie", "modele", "langue_source",
    "langue_cible", "strategie", "demarre_a", "duree_secondes", "statut", "chapitres",
    "morceaux_total", "morceaux_traduits", "morceaux_cache", "caracteres_traites",
    "caracteres_cache", "caracteres_sortie", "mots", "appels", "jetons_entree", "jetons_sortie",
    "duree_generation_s", "duree_chargement_s", "reponses_tronquees", "nouvelles_tentatives",
    "avertissements", "erreurs", "reconstitue",
)


def inserer_run(ligne: dict) -> int:
    """Insère une ligne (clés = colonnes ci-dessus, les absentes valent NULL)."""
    valeurs = [ligne.get(c) for c in _COLONNES]
    conn = store.connexion()
    with conn:
        cur = conn.execute(
            f"INSERT INTO stats_runs ({', '.join(_COLONNES)}) VALUES ({', '.join('?' for _ in _COLONNES)})",
            valeurs,
        )
    return cur.lastrowid


def _nom(chemin: str | None) -> str:
    return os.path.basename(chemin) if chemin else ""


def _enregistrer_sans_risque(fabrique) -> None:
    try:
        inserer_run(fabrique())
    except Exception as e:  # le journal ne doit jamais faire échouer un traitement
        print(f"[stats] ligne d'historique non enregistrée : {e}", flush=True)


def enregistrer_traduction(state, compteurs, session_debut: float, nb_chapitres: int) -> None:
    """Appelée à la fin de chaque passage de `_executer_traduction`, quelle qu'en soit l'issue."""

    def fabrique() -> dict:
        c = compteurs
        return {
            "type": "traduction",
            "job_id": state.job_id,
            "nom": _nom(state.chemin_pdf),
            "chemin_source": state.chemin_pdf,
            "chemin_sortie": state.chemin_sortie,
            "modele": state.modele_ollama,
            "langue_source": state.langue_source.value,
            "langue_cible": state.langue_cible.value,
            "demarre_a": session_debut,
            "duree_secondes": time.time() - session_debut,
            "statut": state.statut.value,
            "chapitres": nb_chapitres,
            "morceaux_total": state.total_sections,
            "morceaux_traduits": c.morceaux_traduits,
            "morceaux_cache": c.morceaux_cache,
            "caracteres_traites": c.caracteres_traites,
            "caracteres_cache": c.caracteres_cache,
            "caracteres_sortie": c.caracteres_sortie,
            "mots": state.mots_traduits,
            "appels": c.appels,
            "jetons_entree": c.jetons_entree,
            "jetons_sortie": c.jetons_sortie,
            "duree_generation_s": c.duree_generation_ns / 1e9,
            "duree_chargement_s": c.duree_chargement_ns / 1e9,
            "reponses_tronquees": c.reponses_tronquees,
            "nouvelles_tentatives": c.nouvelles_tentatives,
            "avertissements": len(state.avertissements),
            "erreurs": len(state.erreurs),
            "reconstitue": 0,
        }

    _enregistrer_sans_risque(fabrique)


def enregistrer_etude(etat, compteurs, session_debut: float, caracteres_source: int) -> None:
    """Idem pour un passage de génération de fiche d'étude."""

    def fabrique() -> dict:
        c = compteurs
        return {
            "type": "etude",
            "job_id": etat.job_id,
            "nom": _nom(etat.chemin_source),
            "chemin_source": etat.chemin_source,
            "chemin_sortie": etat.chemin_sortie,
            "modele": etat.modele_ollama,
            "langue_cible": etat.langue_fiche,
            "strategie": etat.strategie,
            "demarre_a": session_debut,
            "duree_secondes": time.time() - session_debut,
            "statut": etat.statut.value,
            "chapitres": len(etat.chapitres),
            "morceaux_total": etat.total_etapes,
            "morceaux_traduits": c.appels,
            "caracteres_traites": caracteres_source,
            "caracteres_sortie": c.caracteres_sortie or None,
            "appels": c.appels,
            "jetons_entree": c.jetons_entree,
            "jetons_sortie": c.jetons_sortie,
            "duree_generation_s": c.duree_generation_ns / 1e9,
            "duree_chargement_s": c.duree_chargement_ns / 1e9,
            "reponses_tronquees": c.reponses_tronquees,
            "erreurs": len(etat.erreurs),
            "reconstitue": 0,
        }

    _enregistrer_sans_risque(fabrique)


def lister_runs(type_: str | None = None, limite: int = 300) -> list[dict]:
    """Passages du plus récent au plus ancien."""
    sql = "SELECT * FROM stats_runs"
    params: list = []
    if type_:
        sql += " WHERE type = ?"
        params.append(type_)
    sql += " ORDER BY demarre_a DESC, id DESC LIMIT ?"
    params.append(max(1, min(limite, 5000)))
    return [dict(r) for r in store.connexion().execute(sql, params).fetchall()]


# ── Calculs ──────────────────────────────────────────────────────────────────

def _vitesse_jetons(r: dict) -> float | None:
    """Jetons générés par seconde de calcul, ou None si non mesuré."""
    if r.get("jetons_sortie") and r.get("duree_generation_s"):
        return r["jetons_sortie"] / r["duree_generation_s"]
    return None


def _secondes_par_1000_car(r: dict) -> float | None:
    """Durée murale par millier de caractères SOURCE réellement envoyés au modèle (cache exclu)."""
    if r.get("caracteres_traites") and r.get("duree_secondes"):
        return r["duree_secondes"] / r["caracteres_traites"] * 1000
    return None


def _secondes_par_1000_mots(r: dict) -> float | None:
    if r.get("mots") and r.get("duree_secondes"):
        return r["duree_secondes"] / r["mots"] * 1000
    return None


def _mediane(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def agreger(runs: list[dict]) -> dict:
    """Totaux et vitesse par (type, modèle). Médianes : un passage aberrant (modèle froid, veille de
    la machine) ne doit pas fausser la vitesse « normale »."""
    par_modele: dict[tuple, list[dict]] = {}
    for r in runs:
        par_modele.setdefault((r["type"], r["modele"]), []).append(r)
    lignes = []
    for (type_, modele), rs in sorted(par_modele.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        mesures = [r for r in rs if not r["reconstitue"]]
        lignes.append({
            "type": type_,
            "modele": modele,
            "passages": len(rs),
            "mesures": len(mesures),
            "jetons_par_seconde": _mediane([v for r in mesures if (v := _vitesse_jetons(r))]),
            "secondes_par_1000_car": _mediane([v for r in mesures if (v := _secondes_par_1000_car(r))]),
            "secondes_par_1000_mots": _mediane([v for r in rs if (v := _secondes_par_1000_mots(r))]),
            "chargement_moyen_s": _mediane([r["duree_chargement_s"] for r in mesures if r.get("duree_chargement_s")]),
        })
    return {
        "modeles": lignes,
        "totaux": {
            "passages": len(runs),
            "duree_secondes": sum(r["duree_secondes"] or 0 for r in runs),
            "jetons_sortie": sum(r["jetons_sortie"] or 0 for r in runs),
            "jetons_entree": sum(r["jetons_entree"] or 0 for r in runs),
            "caracteres_traites": sum(r["caracteres_traites"] or 0 for r in runs),
            "reponses_tronquees": sum(r["reponses_tronquees"] or 0 for r in runs),
        },
    }


def estimer(modele: str, caracteres: int | None = None, mots: int | None = None,
            type_: str = "traduction") -> dict:
    """
    Estime la durée d'un traitement de `caracteres` (ou `mots`) avec `modele`, d'après l'historique.

    Retour : {"statut": "ok"|"insuffisant", "secondes", "min", "max", "echantillons", "base",
    "source"}. `min`/`max` = 1ᵉʳ et 3ᵉ quartiles (ou min/max sous 4 échantillons). `base` dit
    sur quoi repose le chiffre (« caracteres » ou « mots »), `source` « mesure » ou
    « reconstitue ». La durée ne contient PAS le chargement à froid du modèle, qui s'y ajoute au
    premier appel (voir `chargement_moyen_s` dans l'agrégat).
    """
    runs = [r for r in lister_runs(type_, limite=5000) if r["modele"] == modele]
    candidats = []
    if caracteres:
        mesures = [v for r in runs if not r["reconstitue"] and (v := _secondes_par_1000_car(r))]
        candidats.append(("caracteres", "mesure", mesures, caracteres))
    if mots:
        candidats.append(("mots", "mesure", [v for r in runs if not r["reconstitue"] and (v := _secondes_par_1000_mots(r))], mots))
        candidats.append(("mots", "reconstitue", [v for r in runs if r["reconstitue"] and (v := _secondes_par_1000_mots(r))], mots))
    for base, source, taux, taille in candidats:
        if len(taux) >= ECHANTILLONS_MIN:
            taux = sorted(taux)
            q = statistics.quantiles(taux, n=4) if len(taux) >= 4 else [taux[0], statistics.median(taux), taux[-1]]
            return {
                "statut": "ok", "base": base, "source": source, "echantillons": len(taux),
                "secondes": statistics.median(taux) * taille / 1000,
                "min": q[0] * taille / 1000, "max": q[-1] * taille / 1000,
            }
    mieux = max((len(t) for _, _, t, _ in candidats), default=0)
    return {"statut": "insuffisant", "echantillons": mieux, "minimum": ECHANTILLONS_MIN}


# ── Reconstitution depuis les traductions déjà faites ────────────────────────

def reconstituer_depuis_documents() -> int:
    """
    Crée une ligne `reconstitue = 1` par document déjà traduit, à partir de son .state.json
    (durée cumulée, mots, morceaux). Idempotent : un document déjà présent (reconstitué OU mesuré)
    est ignoré. Retourne le nombre de lignes ajoutées.

    Honnête sur ce que ça vaut : le .state.json ne garde ni jetons ni durée de calcul ni nombre de
    caractères, donc ces colonnes restent NULL. La durée est cumulée sur tous les passages du
    document, cache inclus.
    """
    from app.services import bibliotheque
    from app.services.job_manager import charger_etat

    deja = {r["chemin_sortie"] for r in store.connexion().execute("SELECT chemin_sortie FROM stats_runs").fetchall()}
    ajoutes = 0
    for doc in bibliotheque.lister_documents():
        sortie = doc.get("chemin_sortie")
        if not sortie or sortie in deja:
            continue
        etat = charger_etat(sortie)
        if etat is None or not etat.temps_ecoule_secondes or not etat.mots_traduits:
            continue
        inserer_run({
            "type": "traduction", "job_id": etat.job_id, "nom": _nom(etat.chemin_pdf),
            "chemin_source": etat.chemin_pdf, "chemin_sortie": sortie, "modele": etat.modele_ollama,
            "langue_source": etat.langue_source.value, "langue_cible": etat.langue_cible.value,
            "demarre_a": etat.temps_debut or time.time(), "duree_secondes": etat.temps_ecoule_secondes,
            "statut": etat.statut.value, "chapitres": len(etat.chapitres_traduits),
            "morceaux_total": etat.total_sections, "morceaux_traduits": etat.derniere_section_completee,
            "mots": etat.mots_traduits, "avertissements": len(etat.avertissements),
            "erreurs": len(etat.erreurs), "reconstitue": 1,
        })
        ajoutes += 1
    return ajoutes
