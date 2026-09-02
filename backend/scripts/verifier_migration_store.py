#!/usr/bin/env python3
"""
Vérification aller-retour de la migration vers le store — LECTURE SEULE.

N'écrit JAMAIS sur le disque, n'appelle JAMAIS `_regenerer_sortie` (qui écrit
le fichier réel). Reconstruit en mémoire le texte attendu à partir des
chapitres migrés dans le store, avec exactement la même recette que
`translation_runner._regenerer_sortie`, puis le compare OCTET PAR OCTET au
contenu réel du fichier `.md` sur disque.

Raison d'être : la migration précédente (script `migrer_vers_store.py`) a déjà
une fois perdu 90 % d'un livre tout en affichant « migration réussie » — un
compte de documents migrés ou un log vert n'est pas une preuve. Seul cet
aller-retour l'est.

Usage :
    python3 scripts/verifier_migration_store.py
"""

import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import store                                          # noqa: E402
from app.services.bibliotheque import lister_documents                  # noqa: E402
from app.services.job_manager import charger_etat                       # noqa: E402
from app.services.translation_runner import (                           # noqa: E402
    _extraire_annexe_liens,
    _marqueur_chapitre,
    _RE_MARQUEUR_CHAPITRE,
)


def _est_implicite(contenu_reel: str) -> bool:
    """Même détection que la migration : pas de marqueur => chapitre implicite."""
    return _RE_MARQUEUR_CHAPITRE.search(contenu_reel) is None


def _reconstruire(chemin_sortie: str) -> str | None:
    """Texte attendu, reconstruit UNIQUEMENT depuis le store + le fichier réel
    (lu, jamais écrit). Retourne None si le store ne connaît pas ce document."""
    chapitres = store.lire_chapitres(chemin_sortie)
    if not chapitres:
        return None

    with open(chemin_sortie, encoding="utf-8") as f:
        contenu_reel = f.read()
    implicite = _est_implicite(contenu_reel)

    etat = charger_etat(chemin_sortie)
    if etat is None:
        print(f"      ⚠️  pas d'état lisible pour {os.path.basename(chemin_sortie)} — ignoré")
        return None

    annexe = _extraire_annexe_liens(chemin_sortie)
    indices_str = ", ".join(str(i) for i in sorted(etat.chapitres_traduits))
    morceaux = [
        f"<!-- modèle : {etat.modele_ollama} | source : {etat.langue_source.value}"
        f" → {etat.langue_cible.value} | chapitres traduits : {indices_str} -->\n"
    ]
    for c in chapitres:
        if not implicite:
            morceaux.append(f"\n{_marqueur_chapitre(c['index_chapitre'], c['titre'] or '')}\n\n")
        morceaux.append(c["contenu"] + "\n")
    if annexe:
        morceaux.append(annexe)
    return "".join(morceaux)


def verifier() -> int:
    documents = lister_documents()
    print(f"{len(documents)} document(s) dans le registre\n")

    ok, echecs, ignores = 0, 0, 0
    for doc in documents:
        sortie = doc.get("chemin_sortie")
        nom = os.path.basename(sortie) if sortie else "?"
        if not sortie or not os.path.exists(sortie):
            print(f"  ─ {nom} : fichier absent — ignoré")
            ignores += 1
            continue

        attendu = _reconstruire(sortie)
        if attendu is None:
            print(f"  ─ {nom} : pas dans le store — ignoré")
            ignores += 1
            continue

        with open(sortie, encoding="utf-8") as f:
            reel = f.read()

        if attendu == reel:
            print(f"  ✅ {nom} : identique octet pour octet ({len(reel)} car.)")
            ok += 1
        else:
            print(f"  ❌ {nom} : DIVERGENCE — attendu {len(attendu)} car., "
                  f"réel {len(reel)} car.")
            # Premier point de divergence, pour localiser vite.
            for i, (a, b) in enumerate(zip(attendu, reel)):
                if a != b:
                    debut = max(0, i - 40)
                    print(f"      premier écart au caractère {i} :")
                    print(f"      attendu : ...{attendu[debut:i+40]!r}")
                    print(f"      réel    : ...{reel[debut:i+40]!r}")
                    break
            else:
                print("      (l'un est un préfixe strict de l'autre)")
            # Signal séparé, plus important que le compte d'octets : le CONTENU
            # de chaque chapitre est-il retrouvé tel quel dans le fichier réel ?
            # Une différence de mise en forme (un saut de ligne au raccord d'une
            # ancienne annexe mi-fichier, ex. multi-passes) n'est PAS le bug du
            # 90 % — seul un chapitre dont le texte est introuvable l'est.
            chapitres = store.lire_chapitres(sortie)
            manquants = [c["index_chapitre"] for c in chapitres if c["contenu"] not in reel]
            if manquants:
                print(f"      ⚠️  CONTENU MANQUANT — chapitre(s) introuvables tel "
                      f"quel dans le fichier réel : {manquants}")
            else:
                print("      contenu de TOUS les chapitres retrouvé tel quel dans "
                      "le fichier réel — écart de mise en forme uniquement (espaces/"
                      "sauts de ligne), aucune perte de contenu.")
            echecs += 1

    print(f"\n{ok} identique(s), {echecs} divergence(s), {ignores} ignoré(s)")
    return 1 if echecs else 0


if __name__ == "__main__":
    raise SystemExit(verifier())
