#!/usr/bin/env python3
"""
Migration des documents existants vers le store SQLite — étape F de la phase 9.

Importe dans la base ce qui vit aujourd'hui dans des fichiers :
  <base>_traduit_<modele>.md          → table `chapitres`
  <base>_traduit_<modele>.state.json  → table `etats`
  <base>_traduit_<modele>.cache.json  → table `morceaux`

⚠️ N'EFFACE RIEN. Les fichiers restent en place, et la double écriture reste
active : c'est volontairement la moitié SÛRE de l'étape F. Le retrait du chemin
JSON viendra ensuite, une fois la migration constatée.

⚠️ MODE SIMULATION PAR DÉFAUT. Il faut `--appliquer` pour écrire quoi que ce
soit. Une migration qui s'exécute au premier lancement par mégarde sur les
vraies données de quelqu'un est exactement le genre d'accident que ce projet a
déjà connu deux fois.

Usage :
    python3 scripts/migrer_vers_store.py             # simulation
    python3 scripts/migrer_vers_store.py --appliquer
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services import store                                    # noqa: E402
from app.services.bibliotheque import lister_documents            # noqa: E402
from app.services.cache_traduction import chemin_fichier_cache, charger_cache  # noqa: E402
from app.services.job_manager import charger_etat                 # noqa: E402
from app.services.translation_runner import TITRE_ANNEXE_LIENS    # noqa: E402

_RE_MARQUEUR = re.compile(r"<!-- === chapitre (\d+) : (.*?) === -->")


def decouper_chapitres(chemin_md: str) -> list[dict]:
    """
    Reconstruit les chapitres depuis le `.md`, en s'appuyant sur les marqueurs
    que le moteur y écrit.

    Sans marqueur, le document a été traduit en « chapitre implicite » : tout le
    corps est le chapitre 0. L'annexe des liens est retirée — elle n'appartient à
    aucun chapitre, et l'inclure la ferait réapparaître en double à la première
    régénération.
    """
    try:
        with open(chemin_md, encoding="utf-8") as f:
            contenu = f.read()
    except OSError:
        return []

    corps = contenu.split("\n", 1)[1] if "\n" in contenu else ""

    # ⚠️ On découpe par marqueurs D'ABORD, et on retire l'annexe chapitre par
    # chapitre ENSUITE. Tronquer le corps à la position de l'annexe serait faux :
    # elle n'est PAS forcément en fin de fichier. Un document traduit en
    # plusieurs passes la voit ajoutée après la première, puis d'autres
    # chapitres s'ajoutent APRÈS elle. Constaté sur un livre réel de 716 Ko :
    # l'annexe était ligne 393 sur 3327, et la troncature perdait 15 chapitres
    # sur 22 — 90 % du document, en silence.
    marqueurs = list(_RE_MARQUEUR.finditer(corps))
    if not marqueurs:
        return [{"index": 0, "titre": "Document entier", "contenu": t}] if (t := _sans_annexe(corps)) else []

    chapitres = []
    for i, m in enumerate(marqueurs):
        fin = marqueurs[i + 1].start() if i + 1 < len(marqueurs) else len(corps)
        contenu_chap = _sans_annexe(corps[m.end():fin])
        chapitres.append({
            "index": int(m.group(1)),
            "titre": m.group(2),
            "contenu": contenu_chap,
        })
    return chapitres


def _sans_annexe(texte: str) -> str:
    """Retire le bloc d'annexe des liens d'un fragment, où qu'il se trouve."""
    position = texte.find(TITRE_ANNEXE_LIENS)
    if position == -1:
        return texte.strip()
    separateur = texte.rfind("\n\n---\n\n", 0, position)
    return texte[: separateur if separateur != -1 else position].strip()


def migrer(appliquer: bool) -> int:
    documents = lister_documents()
    print(f"{len(documents)} document(s) dans le registre\n")
    migres = 0

    for doc in documents:
        sortie = doc.get("chemin_sortie")
        if not sortie:
            continue
        nom = os.path.basename(sortie)

        deja = store.lire_chapitres(sortie)
        if deja:
            print(f"  ─ {nom}\n      déjà dans le store ({len(deja)} chapitre(s)) — ignoré")
            continue

        chapitres = decouper_chapitres(sortie)
        etat = charger_etat(sortie)
        cache = charger_cache(sortie) if os.path.exists(chemin_fichier_cache(sortie)) else {}

        print(f"  ─ {nom}")
        print(f"      chapitres : {len(chapitres)}   cache : {len(cache)} morceau(x)"
              f"   état : {'oui' if etat else 'absent'}")
        if not chapitres:
            print("      aucun contenu exploitable — ignoré")
            continue

        if not appliquer:
            migres += 1
            continue

        store.enregistrer_document(
            chemin_sortie=sortie,
            chemin_source=doc.get("chemin_source", ""),
            modele=doc.get("modele", ""),
            langue_source=doc.get("langue_source", ""),
            langue_cible=doc.get("langue_cible", ""),
        )
        if cache:
            store.ecrire_morceaux(sortie, cache)
        etat_json = etat.model_dump_json() if etat else "{}"
        for c in chapitres:
            store.ecrire_chapitre_et_etat(
                chemin_sortie=sortie,
                index_chapitre=c["index"],
                titre=c["titre"],
                contenu=c["contenu"],
                ordre=c["index"],
                etat_json=etat_json,
            )
        print("      migré")
        migres += 1

    return migres


def main() -> int:
    parseur = argparse.ArgumentParser(description=__doc__)
    parseur.add_argument("--appliquer", action="store_true",
                         help="écrit réellement dans le store (sinon : simulation)")
    args = parseur.parse_args()

    if not args.appliquer:
        print("MODE SIMULATION — rien ne sera écrit. Ajoutez --appliquer pour migrer.\n")

    n = migrer(args.appliquer)

    print()
    if args.appliquer:
        print(f"✅ {n} document(s) migré(s). Les fichiers n'ont pas été touchés.")
    else:
        print(f"{n} document(s) seraient migrés. Relancez avec --appliquer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
